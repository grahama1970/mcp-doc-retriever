"""
Description:
  This module orchestrates the recursive crawling and downloading of web pages
  starting from a given URL. It manages a queue of URLs to visit, respects
  robots.txt rules, limits concurrent downloads using asyncio.Semaphore, and
  delegates the actual fetching of content (via HTTPX or Playwright) to the
  `fetchers` module. For each successfully downloaded page, it extracts links
  for further crawling (up to a specified depth) and generates a safe local
  file path using `helpers.url_to_local_path`. It records the outcome of
  each URL processing attempt (success, skip, failure) in a JSONL index file.
  Progress is reported using the `tqdm` library.

Third-Party Documentation:
  - httpx (Used for robots.txt checks & passed to fetchers): https://www.python-httpx.org/
  - aiofiles (Used for async writing of index file): https://github.com/Tinche/aiofiles
  - tqdm (Used for progress bars): https://tqdm.github.io/

Internal Module Dependencies:
  - .helpers (url_to_local_path)
  - .fetchers (fetch_single_url_requests, fetch_single_url_playwright)
  - .robots (_is_allowed_by_robots)
  - mcp_doc_retriever.utils (canonicalize_url, is_url_private_or_internal, timeouts)
  - mcp_doc_retriever.models (IndexRecord)

Sample Input (Conceptual - assumes setup within a running asyncio loop):
  base_dir = Path("./test_web_download")
  download_id = "my_web_crawl"
  start_url = "https://httpbin.org/links/10/0"
  depth = 1
  executor = ThreadPoolExecutor() # If needed by fetchers/helpers
  progress_bar = tqdm(desc="Web Crawl", unit="page")

  await start_recursive_download(
      start_url=start_url,
      depth=depth,
      force=True,
      download_id=download_id,
      base_dir=base_dir,
      use_playwright=False,
      progress_bar=progress_bar,
      executor=executor,
  )

Sample Expected Output:
  - Creates directories: `./test_web_download/index/` and `./test_web_download/content/my_web_crawl/`
  - Creates hostname subdirectory: `./test_web_download/content/my_web_crawl/httpbin.org/`
  - Downloads HTML files into the hostname subdirectory using the flat, hashed filename
    structure (e.g., `.../httpbin.org/http_httpbin.org_links_10_0-HASH.html`, `.../http_httpbin.org_links_10_1-HASH.html`, etc.).
  - Creates and populates `./test_web_download/index/my_web_crawl.jsonl` with IndexRecord entries
    for each processed URL (the start URL and linked URLs up to depth 1).
  - Prints logs to the console.
  - Displays and updates a tqdm progress bar.
"""

# File: src/mcp_doc_retriever/downloader/web_downloader.py
# File: src/mcp_doc_retriever/downloader/web_downloader.py

# File: src/mcp_doc_retriever/downloader/web_downloader.py
import asyncio
import logging
import traceback
import shutil
from typing import Optional, Set, Dict, Any, List
from urllib.parse import urlparse, urljoin
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

import httpx
import aiofiles
from tqdm.asyncio import tqdm  # Use async version

# Global asynchronous lock to serialize writes to the index file.
INDEX_LOCK = asyncio.Lock()

# Internal module imports
from mcp_doc_retriever.utils import (
    TIMEOUT_REQUESTS,
    TIMEOUT_PLAYWRIGHT,
    canonicalize_url,
    is_url_private_or_internal,
)
from mcp_doc_retriever.downloader.helpers import url_to_local_path
from mcp_doc_retriever.models import IndexRecord

try:
    from .robots import _is_allowed_by_robots
    from .fetchers import fetch_single_url_requests, fetch_single_url_playwright
except ImportError:
    from mcp_doc_retriever.downloader.robots import _is_allowed_by_robots
    from mcp_doc_retriever.downloader.fetchers import (
        fetch_single_url_requests,
        fetch_single_url_playwright,
    )

logger = logging.getLogger(__name__)

# --- Helper Function ---


async def _write_index_record(index_path: Path, record: IndexRecord) -> None:
    """
    Appends a single IndexRecord to the specified JSONL index file.
    Handles potential file writing errors with an asynchronous lock to avoid
    race conditions when multiple workers write concurrently.

    Args:
        index_path: The Path object pointing to the JSONL index file.
        record: The IndexRecord object to write.
    """
    try:
        record_json = record.model_dump_json(exclude_none=True)
        logger.debug(f"Attempting to write index record to {index_path}: {record_json}")
        async with INDEX_LOCK:
            async with aiofiles.open(index_path, "a", encoding="utf-8") as f:
                await f.write(record_json + "\n")
                await f.flush()
        logger.debug(f"Successfully wrote index record for {record.canonical_url}")
    except Exception as write_e:
        logger.critical(
            f"CRITICAL: Failed to write index record for {record.canonical_url} to {index_path}: {write_e}",
            exc_info=True,
        )
        logger.error(
            f"Failed Record Data Hint: URL={record.canonical_url}, Status={record.fetch_status}, Error='{record.error_message}'"
        )


# --- Main Recursive Download Function ---


async def start_recursive_download(
    start_url: str,
    depth: int,
    force: bool,
    download_id: str,  # Expect sanitized ID
    base_dir: Path,  # Expect Path object
    use_playwright: bool = False,
    timeout_requests: int = TIMEOUT_REQUESTS,
    timeout_playwright: int = TIMEOUT_PLAYWRIGHT,
    max_file_size: int | None = None,
    progress_bar: Optional[tqdm] = None,  # Accept tqdm instance
    max_concurrent_requests: int = 50,
    executor: Optional[
        ThreadPoolExecutor
    ] = None,  # Accept executor for potential sync tasks
) -> None:
    """
    Starts the asynchronous recursive download process with concurrency limiting
    and progress bar updates. Uses Path objects internally where appropriate.
    """
    logger.info(
        f"Starting recursive download for ID: {download_id}, URL: {start_url}, Depth: {depth}"
    )

    # Define directories using Path objects
    index_dir = base_dir / "index"
    content_base_dir = base_dir / "content" / download_id  # Specific to this download
    index_path = index_dir / f"{download_id}.jsonl"

    # Ensure directories exist
    try:
        content_base_dir.mkdir(parents=True, exist_ok=True)
        index_dir.mkdir(parents=True, exist_ok=True)
        logger.debug(f"Content directory: {content_base_dir}")
        logger.debug(f"Index file path: {index_path}")
    except OSError as e:
        logger.critical(f"Cannot ensure required directories exist: {e}. Aborting.")
        return

    # Initialize queue, visited set, and semaphore
    queue: asyncio.Queue = asyncio.Queue()
    visited: Set[str] = set()
    semaphore = asyncio.Semaphore(max_concurrent_requests)
    logger.info(f"Web download concurrency limit set to {max_concurrent_requests}")

    # Initialize starting state
    try:
        start_canonical = canonicalize_url(start_url)
        start_domain = urlparse(start_canonical).netloc
        if not start_domain:
            raise ValueError("Could not extract domain from start URL")
        await queue.put((start_canonical, 0))
        visited.add(start_canonical)
        logger.info(
            f"Start URL canonicalized: {start_canonical}, Domain: {start_domain}"
        )
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

    # Cache for robots.txt results
    robots_cache: Dict[str, bool] = {}

    # Shared httpx client configuration
    client_timeout = httpx.Timeout(timeout_requests, read=timeout_requests, connect=15)
    headers = {
        "User-Agent": f"MCPBot/1.0 ({download_id}; +https://example.com/botinfo)"
    }

    # --- Worker Task Definition ---
    async def worker(worker_id: int, shared_client: httpx.AsyncClient):
        """Processes URLs from the queue using the shared httpx client."""
        logger.debug(f"Web worker {worker_id} started.")
        while True:
            queue_item = None
            record_to_write: Optional[IndexRecord] = None  # Hold the record to write
            links_to_add_later: List[str] = []  # Hold links if fetch succeeds
            final_fetch_status_for_recursion = "failed_generic"

            try:
                queue_item = await queue.get()
                if queue_item is None:
                    logger.debug(f"Web worker {worker_id} received None, exiting.")
                    break

                current_canonical_url, current_depth = queue_item
                logger.debug(
                    f"Worker {worker_id}: Processing {current_canonical_url} at depth {current_depth}"
                )

                async with semaphore:
                    logger.debug(
                        f"Worker {worker_id}: Acquired semaphore for {current_canonical_url}"
                    )

                    # --- Pre-download Checks ---
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
                                f"Skipping URL outside start domain {start_domain}",
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
                                f"Mapped {current_canonical_url} to local path: {local_path_str}"
                            )
                    except Exception as pre_check_e:
                        should_skip, skip_reason, skip_status = (
                            True,
                            f"Error during pre-download checks: {pre_check_e}",
                            "failed_precheck",
                        )
                        logger.error(
                            f"{skip_reason} for {current_canonical_url}", exc_info=True
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

                    if not should_skip:
                        if local_path_str is None:
                            logger.error(
                                f"Internal error: local_path_str is None after pre-checks for {current_canonical_url}"
                            )
                            record_to_write = IndexRecord(
                                original_url=current_canonical_url,
                                canonical_url=current_canonical_url,
                                local_path="",
                                fetch_status="failed_internal",
                                error_message="Internal error: Path not calculated",
                            )
                            final_fetch_status_for_recursion = "failed_internal"
                        else:
                            result: Optional[Dict[str, Any]] = None
                            fetch_status = "failed_request"
                            error_message = "Download did not complete successfully."
                            content_md5 = None
                            http_status = None
                            detected_links = []
                            final_local_path_str = ""

                            try:
                                logger.info(
                                    f"Worker {worker_id}: Attempting download: {current_canonical_url} -> {local_path_str}"
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
                                    f"WORKER {worker_id}: Fetcher call COMPLETED for {current_canonical_url}"
                                )
                                logger.debug(
                                    f"Worker {worker_id}: Fetcher raw result for {current_canonical_url}: {result!r}"
                                )

                                if result:
                                    logger.info(
                                        f"WORKER {worker_id}: Processing non-None result for {current_canonical_url}"
                                    )
                                    status_from_result = result.get("status")
                                    error_message_from_result = result.get(
                                        "error_message"
                                    )
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
                                            or "Failed due to potential paywall"
                                        )
                                    else:
                                        fetch_status = "failed_request"
                                        error_message = (
                                            error_message_from_result
                                            or f"Fetcher failed with status '{status_from_result}'."
                                        )
                                    if error_message:
                                        error_message = str(error_message)[:2000]
                                else:
                                    logger.warning(
                                        f"WORKER {worker_id}: Fetcher returned None result for {current_canonical_url}"
                                    )
                                    fetch_status = "failed_request"
                                    error_message = "Fetcher returned None result."
                            except Exception as fetch_exception:
                                tb = traceback.format_exc()
                                error_msg_str = f"Exception during fetcher execution: {str(fetch_exception)} | Traceback: {tb}"
                                logger.error(
                                    f"WORKER {worker_id}: CAUGHT EXCEPTION during fetcher execution for {current_canonical_url}: {error_msg_str}"
                                )
                                fetch_status = "failed_request"
                                error_message = error_msg_str[:2000]

                            logger.info(
                                f"WORKER {worker_id}: Preparing index record AFTER TRY for {current_canonical_url} with status '{fetch_status}'"
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
                        logger.info(
                            f"WORKER {worker_id}: PRE-WRITE Check for {record_to_write.canonical_url} - Status: {record_to_write.fetch_status}"
                        )
                        try:
                            await _write_index_record(index_path, record_to_write)
                        except Exception as index_write_err:
                            logger.error(
                                f"WORKER {worker_id}: FAILED during _write_index_record for {record_to_write.canonical_url}: {index_write_err}",
                                exc_info=True,
                            )
                            final_fetch_status_for_recursion = "failed_internal"
                        logger.info(
                            f"WORKER {worker_id}: POST-WRITE Check for {record_to_write.canonical_url}"
                        )
                    else:
                        logger.error(
                            f"WORKER {worker_id}: No record was prepared for {current_canonical_url}, skipping write."
                        )
                        final_fetch_status_for_recursion = "failed_internal"

                    logger.info(
                        f"WORKER {worker_id}: Checking recursion for {current_canonical_url} (Final Status: {final_fetch_status_for_recursion}, Depth: {current_depth}/{depth})"
                    )
                    if (
                        final_fetch_status_for_recursion == "success"
                        and current_depth < depth
                    ):
                        logger.debug(
                            f"Worker {worker_id}: Processing {len(links_to_add_later)} potential links for queueing from {current_canonical_url}."
                        )
                        links_added_count = 0
                        for link in links_to_add_later:
                            abs_link = None
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
                                    f"Worker {worker_id}: Failed to process/queue link '{link}' (Abs: {abs_link}) from {current_canonical_url}: {link_e}",
                                    exc_info=False,
                                )
                        logger.debug(
                            f"Worker {worker_id}: Added {links_added_count} new links to queue from {current_canonical_url}."
                        )
                    elif (
                        final_fetch_status_for_recursion == "success"
                        and current_depth >= depth
                    ):
                        logger.debug(
                            f"Worker {worker_id}: Reached max depth {depth}, not recursing from {current_canonical_url}"
                        )
                    logger.debug(
                        f"Worker {worker_id}: Released semaphore for {current_canonical_url}"
                    )
            except asyncio.CancelledError:
                logger.info(f"Web worker {worker_id} received cancellation.")
                break
            except Exception as e:
                logger.error(
                    f"Web worker {worker_id}: Unhandled exception processing item {queue_item}: {e}",
                    exc_info=True,
                )
                if queue_item is not None:
                    try:
                        queue.task_done()
                    except ValueError:
                        pass
            finally:
                if queue_item is not None:
                    try:
                        queue.task_done()
                    except ValueError:
                        pass
                if progress_bar is not None:
                    try:
                        progress_bar.update(1)
                    except Exception as pbar_e:
                        logger.warning(f"Failed to update progress bar: {pbar_e}")

        logger.debug(f"Web worker {worker_id} finished.")

    # --- Start Workers and Manage Download ---
    async with httpx.AsyncClient(
        follow_redirects=True, timeout=client_timeout, headers=headers
    ) as client:
        num_workers = max_concurrent_requests
        worker_tasks = [
            asyncio.create_task(worker(i, client)) for i in range(num_workers)
        ]
        logger.info(f"Started {num_workers} web download workers.")

        await queue.join()
        logger.info("Download queue empty and all items processed.")

        logger.debug("Sending exit signals to workers...")
        for _ in range(num_workers):
            await queue.put(None)

        results = await asyncio.gather(*worker_tasks, return_exceptions=True)
        logger.info(f"All {num_workers} web workers finished.")
        for i, res in enumerate(results):
            if isinstance(res, Exception):
                logger.error(f"Web worker {i} raised an exception: {res}", exc_info=res)

    logger.info(f"Recursive download process completed for ID: {download_id}")


# --- Usage Example (if run directly) ---
async def _web_example():
    print("Running direct web downloader example...")
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(levelname)s - [%(name)s:%(funcName)s:%(lineno)d] - %(message)s",
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("websockets").setLevel(logging.WARNING)

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
            line_count = 0
            try:
                with open(index_file, "r") as f:
                    line_count = sum(1 for _ in f)
                print(f"Index file contains {line_count} records.")
                if line_count >= 11:
                    print("✓ Line count check PASSED (>= 11)")
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
            f"Overall Direct Execution Status: {'OK' if final_outcome_ok else 'ISSUES FOUND'}"
        )
        print("------------------------------------")
        import sys

        sys.exit(0 if example_run_ok else 1)


if __name__ == "__main__":
    import sys
    import os

    SRC_DIR = Path(__file__).resolve().parent.parent.parent
    if str(SRC_DIR) not in sys.path:
        print(f"Adding {SRC_DIR} to sys.path for direct execution.")
        sys.path.insert(0, str(SRC_DIR))
    asyncio.run(_web_example())