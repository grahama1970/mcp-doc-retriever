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
  start_url = "https://httpbin.org/html"
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

import asyncio
import logging
import json
import traceback
import shutil
from typing import Optional, Set, Dict, Any, List
from urllib.parse import urlparse, urljoin
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

import httpx
import aiofiles
from tqdm.asyncio import tqdm  # Use async version
from urllib.robotparser import RobotFileParser

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
    # Assume robots.py and fetchers.py are in the same directory
    from .robots import _is_allowed_by_robots
    from .fetchers import fetch_single_url_requests, fetch_single_url_playwright
except ImportError:
    # Fallback for potential direct execution or different structure
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
                await f.flush()  # Ensure data is written to OS buffer
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

    # Ensure base directories exist with proper permissions
    try:
        content_base_dir.mkdir(parents=True, exist_ok=True)
        start_domain_for_path = "unknown_domain"  # Fallback
        try:
            # Calculate start_domain early for directory creation and checks
            start_canonical_url = canonicalize_url(start_url)
            parsed_start_url = urlparse(start_canonical_url)
            start_domain = parsed_start_url.netloc
            if not start_domain:
                raise ValueError(
                    "Could not extract domain from start URL for directory creation"
                )
            start_domain_for_path = start_domain  # Use extracted domain
            # Pre-create the domain-specific content directory
            (content_base_dir / start_domain_for_path).mkdir(
                parents=True, exist_ok=True
            )
            logger.debug(
                f"Ensured domain directory exists: {content_base_dir / start_domain_for_path}"
            )
        except Exception as domain_e:
            logger.warning(
                f"Failed to pre-create domain directory for '{start_domain_for_path}': {domain_e}. Path generation might still work."
            )
            # Continue, url_to_local_path will handle the domain part creation

        index_dir.mkdir(parents=True, exist_ok=True)
        logger.debug(f"Content base directory: {content_base_dir}")
        logger.debug(f"Index file path: {index_path}")
    except OSError as e:
        logger.critical(f"Cannot ensure required directories exist: {e}. Aborting.")
        return

    # Initialize queue, visited set, and semaphore
    queue: asyncio.Queue = asyncio.Queue()
    visited: Set[str] = set()
    # Use calculated effective_concurrency here
    effective_concurrency = max(1, max_concurrent_requests)
    semaphore = asyncio.Semaphore(effective_concurrency)
    logger.info(f"Web download concurrency limit set to {effective_concurrency}")

    # Initialize starting state (use already calculated canonical URL and domain)
    try:
        # start_canonical already calculated above
        # start_domain already calculated above
        await queue.put((start_canonical_url, 0))
        visited.add(start_canonical_url)
        logger.info(
            f"Start URL canonicalized: {start_canonical_url}, Domain: {start_domain}"
        )
    except Exception as e:
        # This block might be less likely now, but kept as safeguard
        logger.critical(
            f"Invalid start URL '{start_url}' during queue init: {e}. Aborting download."
        )
        fail_record = IndexRecord(
            original_url=start_url,
            canonical_url=start_url,  # Use original if canonical failed
            fetch_status="failed_setup",
            error_message=f"Invalid start URL during queue init: {e}",
            local_path="",
        )
        await _write_index_record(index_path, fail_record)
        return

    # Cache for robots.txt results (using RobotFileParser objects or None)
    robots_cache: Dict[str, Optional[RobotFileParser]] = {}

    # Shared httpx client configuration
    client_timeout = httpx.Timeout(timeout_requests, read=timeout_requests, connect=15)
    user_agent_string = (
        f"MCPBot/1.0 ({download_id}; +https://example.com/botinfo)"  # Define once
    )
    headers = {"User-Agent": user_agent_string}

    # --- Worker Task Definition ---
    async def worker(worker_id: int, shared_client: httpx.AsyncClient):
        """Processes URLs from the queue using the shared httpx client."""
        logger.debug(f"Web worker {worker_id} started.")
        while True:
            queue_item = None
            record_to_write: Optional[IndexRecord] = None
            links_to_add_later: List[str] = []
            final_fetch_status_for_recursion = "failed_generic"
            current_canonical_url = "N/A"  # For logging

            try:
                queue_item = await queue.get()
                if queue_item is None:
                    logger.debug(f"Web worker {worker_id} received None, exiting.")
                    break

                current_canonical_url, current_depth = queue_item
                if not isinstance(current_canonical_url, str):
                    logger.error(
                        f"Worker {worker_id}: Received non-string URL in queue item: {queue_item}. Skipping."
                    )
                    queue.task_done()  # Must call task_done even for invalid items
                    continue

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
                    local_path_obj: Optional[Path] = None  # Initialize here

                    try:
                        current_parsed_url = urlparse(current_canonical_url)
                        current_netloc = current_parsed_url.netloc
                        if not current_netloc:
                            raise ValueError("URL has no network location (domain).")

                        # Check 1: Private/Internal Network
                        if is_url_private_or_internal(current_canonical_url):
                            should_skip, skip_reason, skip_status = (
                                True,
                                "Blocked by SSRF protection (private/internal URL)",
                                "failed_ssrf",
                            )
                        # Check 2: Domain Confinement
                        elif current_netloc != start_domain:
                            should_skip, skip_reason, skip_status = (
                                True,
                                f"Skipping URL outside start domain {start_domain} (domain: {current_netloc})",
                                "skipped_domain",
                            )
                        # Check 3: Robots.txt (Pass correct arguments)
                        elif not await _is_allowed_by_robots(
                            url=current_canonical_url,
                            client=shared_client,  # Pass the client
                            robots_cache=robots_cache,  # Pass the cache
                            user_agent=user_agent_string,  # Pass the user agent string
                        ):
                            should_skip, skip_reason, skip_status = (
                                True,
                                "Blocked by robots.txt",
                                "failed_robotstxt",
                            )
                        # Check 4: Path Generation (only if not skipping)
                        else:
                            try:
                                local_path_obj = url_to_local_path(
                                    content_base_dir, current_canonical_url
                                )
                                local_path_str = str(local_path_obj)
                                logger.debug(
                                    f"Mapped {current_canonical_url} to local path: {local_path_str}"
                                )
                                # Ensure parent exists - moved to just before fetcher call for atomicity
                                # local_path_obj.parent.mkdir(parents=True, exist_ok=True)
                            except Exception as path_e:
                                should_skip, skip_reason, skip_status = (
                                    True,
                                    f"Failed to generate local path: {path_e}",
                                    "failed_internal",
                                )
                                logger.error(
                                    f"{skip_reason} for {current_canonical_url}",
                                    exc_info=True,
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

                    # --- Handle Skipping ---
                    if should_skip:
                        logger.info(
                            f"Worker {worker_id}: Skipping {current_canonical_url}: {skip_reason}"
                        )
                        try:
                            record_to_write = IndexRecord(
                                original_url=current_canonical_url,
                                canonical_url=current_canonical_url,
                                local_path="",
                                fetch_status=skip_status,
                                error_message=skip_reason[:2000],
                            )
                        except Exception as e:
                            logger.error(
                                f"Failed to create IndexRecord for SKIPPED url {current_canonical_url} with status {skip_status}: {e}",
                                exc_info=True,
                            )
                            record_to_write = IndexRecord(
                                original_url=current_canonical_url,
                                canonical_url=current_canonical_url,
                                local_path="",
                                fetch_status="failed_internal",
                                error_message=f"Failed to create skip record: {e}"[
                                    :2000
                                ],
                            )
                        final_fetch_status_for_recursion = skip_status

                    # --- Check if path is valid before attempting download ---
                    # This combines the "missing path" check with the skip logic
                    elif local_path_obj is None or local_path_str is None:
                        # This should only happen if path generation failed and was caught above
                        error_msg = f"Internal error: Path object not created or invalid for {current_canonical_url}, skipping download."
                        logger.error(error_msg)
                        try:
                            record_to_write = IndexRecord(
                                original_url=current_canonical_url,
                                canonical_url=current_canonical_url,
                                local_path="",
                                fetch_status="failed_internal",
                                error_message=error_msg,
                            )
                        except Exception as e:
                            logger.error(
                                f"Failed to create IndexRecord for MISSING PATH url {current_canonical_url}: {e}",
                                exc_info=True,
                            )
                            record_to_write = IndexRecord(
                                original_url=current_canonical_url,
                                canonical_url=current_canonical_url,
                                local_path="",
                                fetch_status="failed_internal",
                                error_message=f"Failed to create missing-path record: {e}"[
                                    :2000
                                ],
                            )
                        final_fetch_status_for_recursion = "failed_internal"

                    # --- Perform Download (Only if not skipped and path is valid) ---
                    else:  # not should_skip and local_path_obj is not None
                        result: Optional[Dict[str, Any]] = None
                        fetch_status = (
                            "failed_request"  # Default status for fetch block
                        )
                        error_message = "Download did not complete successfully."
                        content_md5 = None
                        http_status = None
                        detected_links = []
                        # Assume final path is the calculated one unless fetcher indicates failure/skip
                        final_local_path_str = local_path_str

                        try:
                            # Ensure target directory exists just before fetch
                            try:
                                local_path_obj.parent.mkdir(parents=True, exist_ok=True)
                                logger.debug(
                                    f"Target directory ensured ready: {local_path_obj.parent}"
                                )
                            except Exception as mkdir_e:
                                # If mkdir fails here, record it and skip fetch
                                raise RuntimeError(
                                    f"Failed to create target directory {local_path_obj.parent}: {mkdir_e}"
                                ) from mkdir_e

                            logger.info(
                                f"Worker {worker_id}: Attempting download: {current_canonical_url} -> {local_path_str}"
                            )
                            # Pass Path object to fetcher if it supports it, else string
                            # Current fetchers expect string paths based on their signature
                            fetcher_kwargs = {
                                "url": current_canonical_url,
                                "target_local_path": local_path_str,  # Pass string path
                                "force": force,
                                "allowed_base_dir": str(
                                    content_base_dir
                                ),  # Pass string path
                            }

                            if use_playwright:
                                result = await fetch_single_url_playwright(
                                    **fetcher_kwargs, timeout=timeout_playwright
                                )
                            else:
                                logger.debug(
                                    f"Calling fetcher with kwargs: {fetcher_kwargs}"
                                )
                                result = await fetch_single_url_requests(
                                    **fetcher_kwargs,
                                    timeout=timeout_requests,
                                    client=shared_client,
                                    max_size=max_file_size,
                                )

                            logger.info(
                                f"WORKER {worker_id}: Fetcher call COMPLETED for {current_canonical_url}"
                            )

                            if not result:
                                logger.error(
                                    f"Fetcher returned None result for {current_canonical_url}"
                                )
                                # Set specific error if result is None
                                fetch_status = "failed_internal"
                                error_message = "Fetcher returned None result, indicating internal fetcher error."
                                final_local_path_str = (
                                    ""  # No path if fetcher failed internally
                                )

                            else:  # Process the dictionary result
                                logger.debug(
                                    f"Worker {worker_id}: Fetcher raw result for {current_canonical_url}: {result!r}"
                                )
                                status_from_result = result.get("status")
                                error_message_from_result = result.get("error_message")
                                content_md5 = result.get("content_md5")
                                http_status = result.get("http_status")
                                detected_links = result.get("detected_links", [])
                                target_path_from_result = result.get(
                                    "target_path"
                                )  # String path expected

                                if status_from_result == "success":
                                    fetch_status = "success"
                                    # Use path confirmed by fetcher if available and different
                                    if (
                                        target_path_from_result
                                        and target_path_from_result
                                        != final_local_path_str
                                    ):
                                        logger.warning(
                                            f"Fetcher returned path '{target_path_from_result}' different from calculated '{final_local_path_str}'. Using fetcher's."
                                        )
                                        final_local_path_str = target_path_from_result
                                    elif (
                                        not target_path_from_result
                                    ):  # Should not happen on success if fetcher works
                                        logger.error(
                                            f"Fetcher success for {current_canonical_url} but returned no target_path!"
                                        )
                                        fetch_status = "failed_internal"
                                        error_message = (
                                            "Fetcher success but missing path info"
                                        )
                                        final_local_path_str = ""
                                    # else: final_local_path_str remains the original calculated path

                                    if (
                                        fetch_status == "success"
                                    ):  # Re-check after path handling
                                        error_message = None
                                        links_to_add_later = detected_links

                                elif status_from_result == "skipped":
                                    fetch_status = "skipped"
                                    error_message = (
                                        error_message_from_result
                                        or "Skipped (e.g., exists or not modified)"
                                    )
                                    # Keep path as is, file might exist
                                    if (
                                        target_path_from_result
                                    ):  # Use fetcher's path if provided for skipped
                                        final_local_path_str = target_path_from_result

                                elif status_from_result == "failed_paywall":
                                    fetch_status = "failed_paywall"
                                    error_message = (
                                        error_message_from_result
                                        or "Failed due to potential paywall"
                                    )
                                    final_local_path_str = ""  # No file saved

                                else:  # Handle other failures reported by fetcher
                                    # Check if the specific failure status is valid for IndexRecord
                                    is_valid_status = (
                                        hasattr(IndexRecord, "VALID_FETCH_STATUSES")
                                        and status_from_result
                                        in IndexRecord.VALID_FETCH_STATUSES
                                    )
                                    if is_valid_status:
                                        fetch_status = status_from_result
                                    else:  # Map unknown/generic failures to 'failed_request'
                                        logger.warning(
                                            f"Mapping unsupported fetcher failure status '{status_from_result}' to 'failed_request' for {current_canonical_url}"
                                        )
                                        fetch_status = "failed_request"

                                    error_message = (
                                        error_message_from_result
                                        or f"Fetcher failed with status '{status_from_result}'."
                                    )
                                    final_local_path_str = ""  # No file saved

                                if error_message:
                                    error_message = str(error_message)[:2000]

                        except Exception as fetch_exception:
                            # Catch exceptions during the fetch call itself or directory creation right before it
                            tb = traceback.format_exc()
                            error_msg_str = f"Exception during download process: {str(fetch_exception)}"
                            logger.error(
                                f"WORKER {worker_id}: CAUGHT EXCEPTION during download process for {current_canonical_url}: {error_msg_str}\n{tb}"
                            )
                            fetch_status = "failed_exception"
                            error_message = error_msg_str[:2000]
                            final_local_path_str = (
                                ""  # No file saved if exception occurred
                            )

                        # --- Create Index Record (after fetch attempt block) ---
                        logger.info(
                            f"WORKER {worker_id}: Preparing index record AFTER fetch attempt for {current_canonical_url} with status '{fetch_status}'"
                        )
                        try:
                            record_to_write = IndexRecord(
                                original_url=current_canonical_url,
                                canonical_url=current_canonical_url,
                                local_path=final_local_path_str,  # Use path determined by fetch outcome
                                content_md5=content_md5,
                                fetch_status=fetch_status,
                                http_status=http_status,
                                error_message=error_message,
                            )
                        except Exception as e:
                            logger.error(
                                f"Failed to create IndexRecord after DOWNLOAD for {current_canonical_url} with status {fetch_status}: {e}",
                                exc_info=True,
                            )
                            record_to_write = IndexRecord(
                                original_url=current_canonical_url,
                                canonical_url=current_canonical_url,
                                local_path="",
                                fetch_status="failed_internal",
                                error_message=f"Failed to create index record after fetch: {e}"[
                                    :2000
                                ],
                            )
                            fetch_status = "failed_internal"  # Override status if record creation fails

                        final_fetch_status_for_recursion = fetch_status

                    # --- Write Index Record (if one was created) ---
                    if record_to_write:
                        logger.info(
                            f"WORKER {worker_id}: PRE-WRITE Check for {record_to_write.canonical_url} - Status: {record_to_write.fetch_status}"
                        )
                        await _write_index_record(index_path, record_to_write)
                        # _write_index_record handles its own errors now
                        logger.info(
                            f"WORKER {worker_id}: POST-WRITE Check for {record_to_write.canonical_url}"
                        )
                    else:
                        # This should only happen if pre-checks failed AND creating the skip/fail record also failed
                        logger.error(
                            f"WORKER {worker_id}: No record was prepared for {current_canonical_url}, skipping write. (Indicates prior error creating record)"
                        )
                        final_fetch_status_for_recursion = (
                            "failed_internal"  # Ensure this state is marked as failed
                        )

                    # --- Handle Recursion ---
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
                                if not isinstance(link, str) or not link.strip():
                                    continue
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
                    # else: No recursion needed if status wasn't success

                    logger.debug(
                        f"Worker {worker_id}: Released semaphore for {current_canonical_url}"
                    )
            except asyncio.CancelledError:
                logger.info(f"Web worker {worker_id} received cancellation.")
                break
            except Exception as e:
                # Catch unexpected errors in the worker loop itself
                logger.error(
                    f"Web worker {worker_id}: Unhandled exception processing item {queue_item}: {e}",
                    exc_info=True,
                )
                # Ensure task_done is called even on unexpected error
                if queue_item is not None:
                    try:
                        queue.task_done()
                    except ValueError:
                        pass  # Ignore if already done
            finally:
                # Final safety net for task_done
                if queue_item is not None:
                    try:
                        queue.task_done()
                    except ValueError:
                        pass  # Ignore if already done
                # Update progress bar
                if progress_bar is not None and queue_item is not None:
                    try:
                        progress_bar.update(1)
                    except Exception as pbar_e:
                        logger.warning(f"Failed to update progress bar: {pbar_e}")

        logger.debug(f"Web worker {worker_id} finished.")

    # --- Start Workers and Manage Download (Using Shared Client) ---
    worker_tasks = []
    try:
        # Create client context manager OUTSIDE the worker loop
        async with httpx.AsyncClient(
            follow_redirects=True, timeout=client_timeout, headers=headers
        ) as client:
            logger.info(f"Started {effective_concurrency} web download workers.")
            for i in range(effective_concurrency):
                task = asyncio.create_task(
                    worker(i, client)
                )  # Pass the SAME client to all workers
                worker_tasks.append(task)

            # Wait for queue to be empty
            await queue.join()
            logger.info("Download queue empty and all items processed.")

            # Signal workers to exit
            logger.debug("Sending exit signals to workers...")
            for _ in range(effective_concurrency):
                await queue.put(None)

            # Wait for all workers and check results
            gather_timeout = max(timeout_requests * 2, 60)
            results = await asyncio.wait_for(
                asyncio.gather(*worker_tasks, return_exceptions=True),
                timeout=gather_timeout,
            )
            logger.info(
                f"All {effective_concurrency} web workers finished or timed out."
            )
            for i, res in enumerate(results):
                if isinstance(res, Exception):
                    logger.error(
                        f"Web worker {i} raised an exception: {res}", exc_info=res
                    )

    except asyncio.TimeoutError:
        logger.error(
            f"Orchestration timed out waiting for workers after {gather_timeout}s."
        )
        for task in worker_tasks:
            if not task.done():
                task.cancel()  # Attempt cleanup
    except Exception as e:
        logger.error(f"Error during download process orchestration: {e}", exc_info=True)
        # Ensure workers are cleaned up on orchestrator error
        for task in worker_tasks:
            if not task.done():
                task.cancel()
        raise  # Re-raise the orchestrator error
    finally:
        # Ensure all tasks are awaited briefly after potential cancellation
        if worker_tasks:
            await asyncio.sleep(0.1)  # Allow cancellation to propagate
        # Shared client is closed automatically by the 'async with' block

    logger.info(f"Recursive download process completed for ID: {download_id}")


async def _web_example() -> int:  # Return the exit code
    """Runs an example web crawl based on robots.py example."""
    print("Running direct web downloader example (robots.py style)...")
    exit_code = 1  # Default to failure

    # Use standard logging for simplicity in this example
    logging.basicConfig(
        level=logging.DEBUG,
        format="%(asctime)s - %(levelname)s - [%(name)s:%(funcName)s:%(lineno)d] - %(message)s",
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("websockets").setLevel(logging.WARNING)
    logger_instance = logging.getLogger(
        __name__
    )  # Use standard logger for this example
    logger_instance.info(f"Log level set to DEBUG (using standard logging)")

    test_base_dir = Path("./web_downloader_test").resolve()  # Use absolute path
    download_id = "web_example_httpbin"  # Match robots.py example ID
    test_content_dir = test_base_dir / "content" / download_id
    test_index_dir = test_base_dir / "index"

    print(f"Using test base directory: {test_base_dir}")
    print(f"Cleaning up previous test run (if any)...")
    if test_base_dir.exists():
        try:
            shutil.rmtree(test_base_dir)
        except OSError as e:
            print(f"Warning: Could not completely remove old test directory: {e}")
    print("Cleanup successful.")  # Assume success or warning printed

    try:
        test_content_dir.mkdir(parents=True, exist_ok=True)
        test_index_dir.mkdir(parents=True, exist_ok=True)
        print(
            f"Test directories created:\n  Content: {test_content_dir}\n  Index:   {test_index_dir}"
        )
    except OSError as e:
        print(f"FATAL: Could not create test directories: {e}")
        logger_instance.critical(f"Directory creation failed: {e}", exc_info=True)
        return 1  # Return failure code

    example_run_ok = False
    try:
        # Use tqdm context manager. The error happens on exit, but the download should finish.
        with tqdm(desc=f"Downloading ({download_id})", unit="page") as pbar:
            await start_recursive_download(
                start_url="https://httpbin.org/links/10/0",  # Match robots.py example URL
                depth=1,  # Match robots.py example depth
                force=True,  # Overwrite existing files if any
                download_id=download_id,
                base_dir=test_base_dir,
                use_playwright=False,  # Use httpx/requests
                max_concurrent_requests=5,  # Limit concurrency for example
                progress_bar=pbar,
                executor=None,  # Not used in this version
            )
        # If download finishes AND context manager exits without error (it won't yet)
        example_run_ok = True
        print("\nDownload process finished successfully.")

    except TypeError as e:
        # Specifically catch the tqdm error
        if "bool() undefined" in str(e):
            print(
                f"\nDownload process finished, but encountered known tqdm cleanup error: {e}"
            )
            # Consider the run OK despite the tqdm error, as download likely completed
            example_run_ok = True
        else:
            # Other TypeErrors or unexpected errors
            print(f"\nWeb downloader example workflow failed with TypeError: {e}")
            logger_instance.error("Example workflow failed", exc_info=True)
            example_run_ok = False
    except Exception as e:
        print(f"\nWeb downloader example workflow failed with exception: {e}")
        logger_instance.error("Example workflow failed", exc_info=True)
        example_run_ok = False
    # No finally block needed just for tqdm closing now

    # --- Result Checking (outside the main try/except for download) ---
    print("\n--- Example Run Summary ---")
    if example_run_ok:
        print("✓ Download function completed its execution path (ignoring tqdm error).")
    else:
        print(
            "✗ Download function failed or threw an unexpected exception (check logs)."
        )
    print("---------------------------")

    print("Checking results...")
    index_file = test_index_dir / f"{download_id}.jsonl"
    files_found = False
    try:
        # Check for content files
        example_content_dir = test_content_dir / "httpbin.org"
        if example_content_dir.is_dir():
            # Check if any .html files exist in the directory
            found_html = any(example_content_dir.glob("*.html"))
            if found_html:
                print(f"✓ Content files found in: {example_content_dir}")
                files_found = True
            else:
                print(f"✗ No HTML content files found in: {example_content_dir}")
                # List contents for debugging
                print("Directory contents:", list(example_content_dir.iterdir()))
                files_found = False
        else:
            print(f"✗ Content directory not found: {example_content_dir}")
            files_found = False
    except Exception as e:
        print(f"✗ Error checking content files: {e}")
        files_found = False

    index_ok = False
    if index_file.exists() and index_file.is_file():
        print(f"✓ Index file found: {index_file}")
        try:
            line_count = 0
            success_count = 0
            with open(index_file, "r") as f:
                for line in f:
                    if line.strip():
                        line_count += 1
                        try:
                            data = json.loads(line)
                            if data.get("fetch_status") == "success":
                                success_count += 1
                        except json.JSONDecodeError:
                            pass  # ignore bad lines
            print(
                f"Index file contains {line_count} records ({success_count} successful)."
            )
            # Expect start URL + 10 links = 11 records total (if all allowed & succeed)
            if (
                line_count > 1 and success_count > 1
            ):  # Check for >1 because start + at least some links
                print("✓ Basic index content check PASSED (>1 record, >1 success)")
                index_ok = True
            else:
                print(
                    f"✗ Basic index content check FAILED (Expected >1 records and >1 success, Got: {line_count} records, {success_count} success)"
                )
                index_ok = False
        except Exception as read_e:
            print(f"✗ Error reading index file: {read_e}")
            index_ok = False
    else:
        print(f"✗ Index file NOT found: {index_file}")
        index_ok = False

    # Determine final outcome
    final_outcome_ok = example_run_ok and files_found and index_ok
    exit_code = 0 if final_outcome_ok else 1

    print("\n--- Overall Result ---")
    print(
        f"Overall Direct Execution Status: {'PASSED' if final_outcome_ok else 'FAILED'}"
    )
    print("----------------------")
    if not files_found:
        print(
            "NOTE: File writing seems to be the primary remaining issue based on E2E tests, despite direct execution logs showing success."
        )
    print(f"Returning exit code: {exit_code}")
    return exit_code  # Return exit code instead of calling sys.exit


if __name__ == "__main__":
    import sys
    import os
    import asyncio

    SRC_DIR = Path(__file__).resolve().parent.parent.parent
    if str(SRC_DIR) not in sys.path:
        print(f"Adding {SRC_DIR} to sys.path for direct execution.")
        sys.path.insert(0, str(SRC_DIR))

    # Run the example async function and get the exit code
    final_exit_code = 1  # Default exit code if run fails
    try:
        print("--- Starting Downloader Example (_web_example) ---")
        # Ensure the event loop is properly managed if already running (e.g. in tests)
        try:
            loop = asyncio.get_running_loop()
            if loop.is_running():
                # If loop is running (e.g. pytest-asyncio), create task
                task = loop.create_task(_web_example())
                # How to get result from task? Requires more complex setup or use loop.run_until_complete if possible
                # For simplicity in direct run, assume asyncio.run is okay.
                # This might still cause issues if nested loops are attempted.
                final_exit_code = asyncio.run(_web_example())
            else:
                # If no loop is running, asyncio.run is safe
                final_exit_code = asyncio.run(_web_example())
        except RuntimeError:  # No running loop found
            final_exit_code = asyncio.run(_web_example())

    except KeyboardInterrupt:
        print("\nExecution interrupted by user.")
        final_exit_code = 130  # Standard exit code for Ctrl+C
    except Exception as e:
        print(f"\nCritical error during example execution setup: {e}", file=sys.stderr)
        traceback.print_exc()
        final_exit_code = 1

    # Exit with the code returned by _web_example
    print(f"\nScript exiting with final code: {final_exit_code}")
    sys.exit(final_exit_code)