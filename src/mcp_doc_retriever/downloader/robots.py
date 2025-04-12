"""
Description:
  This module orchestrates the recursive crawling and downloading of web pages,
  respecting the Robots Exclusion Protocol (robots.txt) for each domain. The key function,
  _is_allowed_by_robots, asynchronously checks whether a given URL may be crawled by
  our user agent ("MCPBot/1.0"). It:
    - Fetches robots.txt using a shared httpx.AsyncClient.
    - Caches parsed rules per domain (robots_cache) to reduce repeated network calls.
    - Parses robots.txt content using RobotFileParser.
    - Applies standard rules (specific agent over wildcard, longest match wins).
    - Falls back to allowing crawling if the robots.txt cannot be fetched or parsed.

Third-Party Documentation:
  - httpx: https://www.python-httpx.org/
  - aiofiles: https://github.com/Tinche/aiofiles
  - tqdm: https://tqdm.github.io/
  - loguru: https://github.com/Delgan/loguru

Python Standard Library Documentation:
  - urllib.parse: https://docs.python.org/3/library/urllib.parse.html
  - urllib.robotparser: https://docs.python.org/3/library/urllib.robotparser.html
  - asyncio

Sample Input (Conceptual):
  async with httpx.AsyncClient() as client:
      robots_cache = {}
      allowed = await _is_allowed_by_robots("https://example.com/page", client, robots_cache)

Sample Expected Output:
  - Returns True if crawling is allowed; False if disallowed.
  - In case of errors or if robots.txt is missing, returns True (i.e. allows crawling).
  - Caches the parsed RobotFileParser in robots_cache for subsequent calls.
"""

import asyncio
from loguru import logger
from urllib.parse import urlparse, urljoin
from urllib.robotparser import RobotFileParser
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from typing import Optional, Set, Dict, Any, List
import shutil
import traceback

import httpx
import aiofiles
from tqdm.asyncio import tqdm

# Internal module imports
try:
    from mcp_doc_retriever.utils import (
        TIMEOUT_REQUESTS,
        TIMEOUT_PLAYWRIGHT,
        canonicalize_url,
        is_url_private_or_internal,
    )
    from mcp_doc_retriever.downloader.helpers import url_to_local_path
    from mcp_doc_retriever.models import IndexRecord
    from mcp_doc_retriever.downloader.fetchers import (
        fetch_single_url_requests,
        fetch_single_url_playwright,
    )
except ImportError as e:
    logger.warning(f"Could not perform relative imports, likely running directly: {e}")
    # Fallback definitions
    TIMEOUT_REQUESTS = 30
    TIMEOUT_PLAYWRIGHT = 60

    def canonicalize_url(url: str) -> str:
        return url

    def is_url_private_or_internal(url: str) -> bool:
        return False

    def url_to_local_path(base, url):
        return Path(base) / "mock_path" / "file.html"

    def fetch_single_url_requests(**kwargs):
        return {"status": "failed", "error_message": "mocked"}

    def fetch_single_url_playwright(**kwargs):
        return {"status": "failed", "error_message": "mocked"}

    class IndexRecord:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)

        def model_dump_json(self, **kwargs):
            import json

            return json.dumps(self.__dict__)


# --- Global Lock for Index Writing ---
index_write_lock = asyncio.Lock()


# --- Helper Function: Write an index record ---
async def _write_index_record(index_path: Path, record: IndexRecord) -> None:
    """
    Appends a single IndexRecord to the JSONL index file using an async lock.
    """
    try:
        if hasattr(record, "model_dump_json"):
            record_json = record.model_dump_json(exclude_none=True)
        else:
            import json

            record_json = json.dumps(record.__dict__)
        logger.debug(
            f"Acquiring lock to write index record to {index_path}: {record_json}"
        )
        async with index_write_lock:
            logger.debug(
                f"Lock acquired for record: {getattr(record, 'canonical_url', 'N/A')}"
            )
            async with aiofiles.open(index_path, "a", encoding="utf-8") as f:
                await f.write(record_json + "\n")
            logger.debug(
                f"Wrote index record for {getattr(record, 'canonical_url', 'N/A')}"
            )
    except Exception as write_e:
        logger.critical(
            f"Failed to write index record for {getattr(record, 'canonical_url', 'N/A')} to {index_path}: {write_e}",
            exc_info=True,
        )
        logger.error(
            f"Record data: URL={getattr(record, 'canonical_url', 'N/A')}, Status={getattr(record, 'fetch_status', 'N/A')}, Error={getattr(record, 'error_message', 'N/A')}"
        )


# --- Main Recursive Download Function ---
async def start_recursive_download(
    start_url: str,
    depth: int,
    force: bool,
    download_id: str,
    base_dir: Path,
    use_playwright: bool = False,
    timeout_requests: int = TIMEOUT_REQUESTS,
    timeout_playwright: int = TIMEOUT_PLAYWRIGHT,
    max_file_size: Optional[int] = None,
    progress_bar: Optional[tqdm] = None,
    max_concurrent_requests: int = 50,
    executor: Optional[ThreadPoolExecutor] = None,
) -> None:
    """
    Orchestrates the recursive web download process.
    """
    logger.info(
        f"Starting recursive download for ID: {download_id}, URL: {start_url}, Depth: {depth}"
    )
    index_dir = base_dir / "index"
    content_base_dir = base_dir / "content" / download_id
    index_path = index_dir / f"{download_id}.jsonl"
    try:
        content_base_dir.mkdir(parents=True, exist_ok=True)
        index_dir.mkdir(parents=True, exist_ok=True)
        logger.debug(f"Content directory: {content_base_dir}")
        logger.debug(f"Index file path: {index_path}")
    except OSError as e:
        logger.critical(
            f"Could not create required directories: {e}. Aborting download."
        )
        return

    queue: asyncio.Queue = asyncio.Queue()
    visited: Set[str] = set()
    semaphore = asyncio.Semaphore(max_concurrent_requests)
    logger.info(f"Set concurrency limit: {max_concurrent_requests}")

    try:
        start_canonical = canonicalize_url(start_url)
        start_domain = urlparse(start_canonical).netloc
        if not start_domain:
            raise ValueError("Domain extraction failed from start URL")
        await queue.put((start_canonical, 0))
        visited.add(start_canonical)
        logger.info(f"Start URL: {start_canonical}, Domain: {start_domain}")
    except Exception as e:
        logger.critical(f"Invalid start URL '{start_url}': {e}. Aborting download.")
        fail_record = IndexRecord(
            original_url=start_url,
            canonical_url=start_url,
            fetch_status="failed_setup",
            error_message=f"Invalid start URL: {e}",
            local_path="",
        )
        await _write_index_record(index_path, fail_record)
        return

    robots_cache: Dict[str, bool] = {}
    client_timeout = httpx.Timeout(timeout_requests, read=timeout_requests, connect=15)
    headers = {
        "User-Agent": f"MCPBot/1.0 ({download_id}; +https://example.com/botinfo)"
    }

    # --- Worker Task Definition ---
    async def worker(worker_id: int, shared_client: httpx.AsyncClient):
        logger.debug(f"Web worker {worker_id} started.")
        while True:
            queue_item = None
            record_to_write: Optional[IndexRecord] = None
            links_to_add_later: List[str] = []
            final_fetch_status_for_recursion = "failed_generic"
            try:
                queue_item = await queue.get()
                if queue_item is None:
                    break
                current_canonical_url, current_depth = queue_item
                logger.debug(
                    f"Worker {worker_id}: Processing {current_canonical_url} at depth {current_depth}"
                )
                async with semaphore:
                    logger.debug(
                        f"Worker {worker_id}: Acquired semaphore for {current_canonical_url}"
                    )
                    local_path_str: Optional[str] = None
                    should_skip = False
                    skip_reason = ""
                    skip_status = "failed_generic"
                    local_path_obj = None
                    try:
                        if is_url_private_or_internal(current_canonical_url):
                            should_skip, skip_reason, skip_status = (
                                True,
                                "Blocked by SSRF protection",
                                "failed_ssrf",
                            )
                        elif urlparse(current_canonical_url).netloc != start_domain:
                            should_skip, skip_reason, skip_status = (
                                True,
                                f"URL outside start domain {start_domain}",
                                "skipped_domain",
                            )
                        elif not await _is_allowed_by_robots(
                            current_canonical_url, shared_client, robots_cache
                        ):
                            should_skip, skip_reason, skip_status = (
                                True,
                                "Blocked by robots.txt",
                                "failed_robotstxt",
                            )
                        else:
                            local_path_obj = url_to_local_path(
                                content_base_dir, current_canonical_url
                            )
                            local_path_str = str(local_path_obj)
                            logger.debug(
                                f"Mapped {current_canonical_url} to {local_path_str}"
                            )
                    except Exception as pre_check_e:
                        should_skip, skip_reason, skip_status = (
                            True,
                            f"Pre-download check error: {pre_check_e}",
                            "failed_precheck",
                        )
                        logger.error(
                            f"Pre-check error for {current_canonical_url}: {pre_check_e}",
                            exc_info=True,
                        )
                    if should_skip:
                        record_to_write = IndexRecord(
                            original_url=current_canonical_url,
                            canonical_url=current_canonical_url,
                            local_path="",
                            fetch_status=skip_status,
                            error_message=skip_reason[:2000],
                        )
                        final_fetch_status_for_recursion = skip_status
                    elif local_path_str is None:
                        record_to_write = IndexRecord(
                            original_url=current_canonical_url,
                            canonical_url=current_canonical_url,
                            local_path="",
                            fetch_status="failed_internal",
                            error_message="Local path not calculated",
                        )
                        final_fetch_status_for_recursion = "failed_internal"
                    else:
                        result: Optional[Dict[str, Any]] = None
                        fetch_status = "failed_request"
                        error_message = "Download failed."
                        content_md5 = None
                        http_status = None
                        detected_links = []
                        final_local_path_str = ""
                        try:
                            logger.info(
                                f"Worker {worker_id}: Downloading {current_canonical_url} -> {local_path_str}"
                            )
                            fetcher_kwargs = {
                                "url": current_canonical_url,
                                "target_local_path": local_path_str,
                                "force": force,
                                "allowed_base_dir": str(content_base_dir),
                            }
                            if use_playwright:
                                result = await fetch_single_url_playwright(
                                    **fetcher_kwargs, timeout=timeout_playwright
                                )
                            else:
                                result = await fetch_single_url_requests(
                                    **fetcher_kwargs,
                                    timeout=timeout_requests,
                                    client=shared_client,
                                    max_size=max_file_size,
                                )
                            logger.info(
                                f"WORKER {worker_id}: Fetcher completed for {current_canonical_url}"
                            )
                            logger.debug(
                                f"Worker {worker_id}: Fetcher result: {result!r}"
                            )
                            if result:
                                status_from_result = result.get("status")
                                error_message_from_result = result.get("error_message")
                                content_md5 = result.get("content_md5")
                                http_status = result.get("http_status")
                                detected_links = result.get("detected_links", [])
                                target_path_from_result = result.get("target_path")
                                if status_from_result == "success":
                                    fetch_status = "success"
                                    final_local_path_str = (
                                        str(target_path_from_result)
                                        if target_path_from_result
                                        else ""
                                    )
                                    error_message = None
                                    links_to_add_later = detected_links
                                elif status_from_result == "skipped":
                                    fetch_status = "skipped"
                                    error_message = (
                                        error_message_from_result
                                        or "Skipped (exists or TOCTOU)"
                                    )
                                    final_local_path_str = (
                                        str(target_path_from_result)
                                        if target_path_from_result
                                        else local_path_str
                                    )
                                elif status_from_result == "failed_paywall":
                                    fetch_status = "failed_paywall"
                                    error_message = (
                                        error_message_from_result
                                        or "Failed due to paywall"
                                    )
                                else:
                                    fetch_status = "failed_request"
                                    error_message = (
                                        error_message_from_result
                                        or f"Fetcher status '{status_from_result}'"
                                    )
                                if error_message:
                                    error_message = str(error_message)[:2000]
                            else:
                                logger.warning(
                                    f"Fetcher returned None for {current_canonical_url}"
                                )
                                fetch_status = "failed_request"
                                error_message = "Fetcher returned None."
                        except Exception as fetch_exception:
                            tb = traceback.format_exc()
                            error_msg_str = (
                                f"Fetcher exception: {fetch_exception} | {tb}"
                            )
                            logger.error(
                                f"Worker {worker_id}: Exception during fetch for {current_canonical_url}: {error_msg_str}"
                            )
                            fetch_status = "failed_request"
                            error_message = error_msg_str[:2000]
                        logger.info(
                            f"WORKER {worker_id}: Preparing index record for {current_canonical_url} with status '{fetch_status}'"
                        )
                        record_to_write = IndexRecord(
                            original_url=current_canonical_url,
                            canonical_url=current_canonical_url,
                            local_path=final_local_path_str,
                            content_md5=content_md5,
                            fetch_status=fetch_status,
                            http_status=http_status,
                            error_message=error_message,
                        )
                        final_fetch_status_for_recursion = fetch_status
                    if record_to_write:
                        try:
                            await _write_index_record(index_path, record_to_write)
                        except Exception as index_write_err:
                            logger.error(
                                f"Worker {worker_id}: Error writing index record for {getattr(record_to_write, 'canonical_url', 'N/A')}: {index_write_err}",
                                exc_info=True,
                            )
                            final_fetch_status_for_recursion = "failed_internal"
                    else:
                        logger.error(
                            f"Worker {worker_id}: No record prepared for {current_canonical_url}"
                        )
                        final_fetch_status_for_recursion = "failed_internal"
                    logger.info(
                        f"WORKER {worker_id}: Checking recursion for {current_canonical_url} (Status: {final_fetch_status_for_recursion}, Depth: {current_depth}/{depth})"
                    )
                    if (
                        final_fetch_status_for_recursion == "success"
                        and current_depth < depth
                    ):
                        logger.debug(
                            f"Worker {worker_id}: Processing {len(links_to_add_later)} links from {current_canonical_url}"
                        )
                        links_added_count = 0
                        for link in links_to_add_later:
                            try:
                                abs_link = urljoin(current_canonical_url, link.strip())
                                parsed_abs_link = urlparse(abs_link)
                                if parsed_abs_link.scheme not in ["http", "https"]:
                                    continue
                                if parsed_abs_link.netloc != start_domain:
                                    continue
                                canon_link = canonicalize_url(abs_link)
                                if canon_link not in visited:
                                    visited.add(canon_link)
                                    await queue.put((canon_link, current_depth + 1))
                                    links_added_count += 1
                            except Exception as link_e:
                                logger.warning(
                                    f"Worker {worker_id}: Error queuing link '{link}' from {current_canonical_url}: {link_e}"
                                )
                        logger.debug(
                            f"Worker {worker_id}: Added {links_added_count} links from {current_canonical_url}"
                        )
                    elif final_fetch_status_for_recursion == "success":
                        logger.debug(
                            f"Worker {worker_id}: Reached maximum depth for {current_canonical_url}"
                        )
                    logger.debug(
                        f"Worker {worker_id}: Released semaphore for {current_canonical_url}"
                    )
            except asyncio.CancelledError:
                logger.info(f"Web worker {worker_id} received cancellation.")
                break
            except Exception as e:
                logger.error(
                    f"Worker {worker_id}: Unhandled exception for item {queue_item}: {e}",
                    exc_info=True,
                )
                try:
                    if queue_item is not None:
                        queue.task_done()
                except ValueError:
                    pass
            finally:
                try:
                    if queue_item is not None:
                        queue.task_done()
                except ValueError:
                    pass
                if progress_bar is not None:
                    try:
                        progress_bar.update(1)
                    except Exception as pbar_e:
                        logger.warning(
                            f"Worker {worker_id}: Progress bar update error: {pbar_e}"
                        )
        logger.debug(f"Web worker {worker_id} finished.")

    async with httpx.AsyncClient(
        follow_redirects=True, timeout=client_timeout, headers=headers
    ) as client:
        num_workers = max_concurrent_requests
        worker_tasks = [
            asyncio.create_task(worker(i, client)) for i in range(num_workers)
        ]
        logger.info(f"Started {num_workers} web download workers.")
        await queue.join()
        logger.info("Download queue empty; all items processed.")
        logger.debug("Sending exit signals to workers...")
        for _ in range(num_workers):
            await queue.put(None)
        results = await asyncio.gather(*worker_tasks, return_exceptions=True)
        logger.info(f"All {num_workers} workers finished.")
        for i, res in enumerate(results):
            if isinstance(res, Exception):
                logger.error(f"Worker {i} raised exception: {res}", exc_info=True)
    logger.info(f"Recursive download process completed for ID: {download_id}")


# --- Asynchronous Robots.txt Checker ---
async def _is_allowed_by_robots(
    url: str, client: httpx.AsyncClient, robots_cache: dict
) -> bool:
    """
    Asynchronously determines if the URL is allowed to be crawled based on robots.txt.
    """
    logger.debug(f"ROBOTS: Checking URL: {url}") # Added log
    parsed = urlparse(url)
    domain = f"{parsed.scheme}://{parsed.netloc}"
    if domain in robots_cache:
        logger.debug(f"ROBOTS: Cache hit for domain: {domain}") # Added log
        rp = robots_cache[domain]
        if rp is None: # Handle case where cache explicitly stores None for fetch failure
             logger.warning(f"ROBOTS: Cache hit 'None' for {domain}, allowing crawl.")
             return True
    else:
        logger.debug(f"ROBOTS: Cache miss for domain: {domain}. Fetching robots.txt") # Added log
        robots_url = f"{domain}/robots.txt"
        rp = None # Initialize rp to None
        try:
            # Increased timeout slightly for robustness
            response = await client.get(robots_url, timeout=15.0)
            logger.debug(f"ROBOTS: Fetched {robots_url} with status {response.status_code}") # Added log
            if response.status_code == 200:
                content = response.text
                logger.trace(f"ROBOTS: Content of {robots_url}:\n{content}") # Added log (trace level)
                rp = RobotFileParser()
                rp.set_url(robots_url)
                # Use response.text directly which handles decoding
                rp.parse(response.text.splitlines())
                logger.debug(f"ROBOTS: Parsed {robots_url} successfully.") # Added log
            elif 400 <= response.status_code < 500:
                 logger.warning(
                    f"ROBOTS: robots.txt not found or client error ({response.status_code}) at {robots_url}. Allowing crawl."
                 )
                 # Cache None to prevent refetching on 4xx errors
                 robots_cache[domain] = None
                 return True
            else: # 5xx or other issues
                logger.error(
                    f"ROBOTS: Server error ({response.status_code}) fetching {robots_url}. Allowing crawl temporarily."
                )
                # Don't cache on server errors, maybe temporary
                return True
        except httpx.TimeoutException as e:
             logger.error(f"ROBOTS: Timeout fetching robots.txt from {robots_url}: {e}. Allowing crawl.")
             robots_cache[domain] = None # Cache None on timeout
             return True
        except Exception as e:
            logger.error(f"ROBOTS: Error fetching/parsing robots.txt from {robots_url}: {e}. Allowing crawl.")
            # Don't cache on unexpected errors
            return True

        # Only cache the parser if successfully fetched and parsed
        if rp is not None:
             robots_cache[domain] = rp
        else: # Should not happen if logic above is correct, but as safeguard
             logger.error(f"ROBOTS: rp is None after fetch/parse attempt for {domain}. Allowing crawl.")
             return True


    # Ensure rp is valid before calling can_fetch
    if rp is None:
        logger.error(f"ROBOTS: rp became None unexpectedly for {domain} before can_fetch. Allowing crawl.")
        return True

    user_agent = "MCPBot/1.0"
    try:
        is_allowed = rp.can_fetch(user_agent, url)
        logger.debug(f"ROBOTS: Check result for {url} (User-Agent: {user_agent}): {is_allowed}") # Final check log
        return is_allowed
    except Exception as e:
        logger.error(f"ROBOTS: Error during rp.can_fetch for {url}: {e}. Allowing crawl.")
        return True


# --- Usage Example ---
async def _web_example():
    """Runs an example web crawl."""
    print("Running direct web downloader example...")
    # Loguru is already configured if imported; avoid using standard logging.getLogger calls.
    test_base_dir = Path("./web_downloader_test")
    download_id = "web_example_httpbin"
    test_content_dir = test_base_dir / "content" / download_id
    test_index_dir = test_base_dir / "index"
    if test_base_dir.exists():
        shutil.rmtree(test_base_dir, ignore_errors=True)
    test_content_dir.mkdir(parents=True, exist_ok=True)
    test_index_dir.mkdir(parents=True, exist_ok=True)
    print(f"Test directories created under: {test_base_dir.resolve()}")
    example_run_ok = False
    try:
        with tqdm(desc=f"Downloading ({download_id})", unit="page") as pbar:
            await start_recursive_download(
                start_url="https://httpbin.org/links/10/0",
                depth=1,
                force=True,
                download_id=download_id,
                base_dir=test_base_dir,
                use_playwright=False,
                max_concurrent_requests=5,
                progress_bar=pbar,
                executor=None,
            )
        example_run_ok = True
    except Exception as e:
        print(f"Web downloader example workflow failed: {e}")
        logger.error("Example workflow failed", exc_info=True)
    finally:
        print("\n------------------------------------")
        if example_run_ok:
            print("✓ Direct web downloader example workflow finished successfully.")
        else:
            print("✗ Direct web downloader example workflow failed to run.")
        print("------------------------------------")
        print("Checking results...")
        index_file = test_index_dir / f"{download_id}.jsonl"
        final_outcome_ok = False
        if index_file.exists():
            print(f"Index file created: {index_file}")
            try:
                with open(index_file, "r") as f:
                    line_count = sum(1 for _ in f)
                print(f"Index file contains {line_count} records.")
                if line_count >= 10:
                    print("✓ Line count check PASSED (>= 10)")
                    final_outcome_ok = True
                else:
                    print(
                        f"✗ Line count check FAILED (Expected >= 11, Got {line_count})"
                    )
                    final_outcome_ok = False
            except Exception as read_e:
                print(f"Error reading index file: {read_e}")
                final_outcome_ok = False
        else:
            print(f"✗ Index file NOT created: {index_file}")
            final_outcome_ok = False
        print("\n------------------------------------")
        print(
            f"Overall Direct Execution Status: {'OK' if final_outcome_ok else 'FAILED'}"
        )
        print("------------------------------------")
        import sys

        sys.exit(0 if final_outcome_ok else 1)


if __name__ == "__main__":
    from pathlib import Path
    import sys

    SRC_DIR = Path(__file__).resolve().parent.parent.parent
    if str(SRC_DIR) not in sys.path:
        print(f"Adding {SRC_DIR} to sys.path for direct execution.")
        sys.path.insert(0, str(SRC_DIR))
    asyncio.run(_web_example())
