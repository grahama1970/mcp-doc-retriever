"""
Module: web_downloader.py

Description:
Contains the core asynchronous logic for recursively downloading web content
(`start_recursive_download`). It handles URL queuing, checks robots.txt, manages
concurrency using asyncio.Semaphore, fetches content using either httpx or
Playwright (delegated to fetchers module), extracts links, and writes detailed
index records (`_write_index_record`) for each processed URL to a JSONL file.
Uses tqdm for progress reporting.
"""

import asyncio
import logging
import traceback
import shutil
from typing import Optional, Set, Dict, Any
from urllib.parse import urlparse, urljoin
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

import httpx
import aiofiles
from tqdm.asyncio import tqdm  # Use async version

# Internal module imports
from mcp_doc_retriever.utils import (
    TIMEOUT_REQUESTS,
    TIMEOUT_PLAYWRIGHT,
    canonicalize_url,
    url_to_local_path,  # Assume returns string path
    is_url_private_or_internal,
)
from mcp_doc_retriever.models import IndexRecord
from .robots import _is_allowed_by_robots
from .fetchers import (
    fetch_single_url_requests,
    fetch_single_url_playwright,
)

logger = logging.getLogger(__name__)

# --- Helper Function ---


async def _write_index_record(index_path: Path, record: IndexRecord) -> None:
    """
    Appends a single IndexRecord to the specified JSONL index file.
    Handles potential file writing errors.

    Args:
        index_path: The Path object pointing to the JSONL index file.
        record: The IndexRecord object to write.
    """
    try:
        # Use Pydantic's recommended method for JSON serialization
        record_json = record.model_dump_json(exclude_none=True)
        logger.debug(f"Attempting to write index record to {index_path}: {record_json}")
        # Use aiofiles for asynchronous file writing
        async with aiofiles.open(index_path, "a", encoding="utf-8") as f:
            await f.write(record_json + "\n")
        logger.debug(f"Successfully wrote index record for {record.canonical_url}")
    except Exception as write_e:
        # Log critical errors during index writing, as this indicates data loss
        logger.critical(
            f"CRITICAL: Failed to write index record for {record.canonical_url} to {index_path}: {write_e}",
            exc_info=True,
        )
        # Log key data from the failed record to aid debugging
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

    Args:
        start_url: The initial URL to begin downloading from.
        depth: Maximum recursion depth (0 means only the start_url).
        force: Whether to overwrite existing files.
        download_id: Sanitized unique identifier for this download batch.
        base_dir: Root directory Path for downloads (contains index/content).
        use_playwright: Whether to use Playwright (True) or httpx (False).
        timeout_requests: Timeout in seconds for httpx requests.
        timeout_playwright: Timeout in seconds for Playwright operations.
        max_file_size: Maximum size in bytes for individual downloaded files.
        progress_bar: An optional tqdm instance to update progress.
        max_concurrent_requests: Max number of concurrent download tasks.
        executor: Optional ThreadPoolExecutor for sync tasks (if any needed).
    """
    logger.info(
        f"Starting recursive download for ID: {download_id}, URL: {start_url}, Depth: {depth}"
    )

    # Define directories using Path objects
    index_dir = base_dir / "index"
    content_base_dir = base_dir / "content" / download_id  # Specific to this download
    index_path = index_dir / f"{download_id}.jsonl"

    # Ensure directories exist (workflow should handle this, but check again)
    try:
        content_base_dir.mkdir(parents=True, exist_ok=True)
        index_dir.mkdir(parents=True, exist_ok=True)
        logger.debug(f"Content directory: {content_base_dir}")
        logger.debug(f"Index file path: {index_path}")
    except OSError as e:
        logger.critical(f"Cannot ensure required directories exist: {e}. Aborting.")
        return  # Cannot proceed without directories

    # Initialize queue, visited set, and semaphore for concurrency control
    queue: asyncio.Queue = asyncio.Queue()
    visited: Set[str] = (
        set()
    )  # Store canonical URLs that have been added to the queue/processed
    semaphore = asyncio.Semaphore(max_concurrent_requests)
    logger.info(f"Web download concurrency limit set to {max_concurrent_requests}")

    # Initialize starting state
    try:
        start_canonical = canonicalize_url(start_url)
        start_domain = urlparse(start_canonical).netloc
        if not start_domain:
            raise ValueError("Could not extract domain from start URL")

        # Add start URL to the queue and visited set immediately
        await queue.put((start_canonical, 0))  # tuple (url, depth)
        visited.add(start_canonical)
        logger.info(
            f"Start URL canonicalized: {start_canonical}, Domain: {start_domain}"
        )

    except Exception as e:
        logger.critical(f"Invalid start URL '{start_url}': {e}. Aborting download.")
        # Write a failure record for the start URL itself? Optional.
        fail_record = IndexRecord(
            original_url=start_url,
            canonical_url=start_url,
            fetch_status="failed_setup",
            error_message=f"Invalid start URL: {e}",
        )
        await _write_index_record(index_path, fail_record)
        return

    # Cache for robots.txt results
    robots_cache: Dict[str, bool] = {}

    # Shared httpx client configuration (if not using Playwright for everything)
    client_timeout = httpx.Timeout(timeout_requests, read=timeout_requests, connect=15)
    headers = {
        "User-Agent": f"MCPBot/1.0 ({download_id}; +https://example.com/botinfo)"
    }  # More specific UA

    # --- Worker Task Definition ---
    async def worker(worker_id: int, shared_client: httpx.AsyncClient):
        """Processes URLs from the queue using the shared httpx client."""
        logger.debug(f"Web worker {worker_id} started.")
        while True:
            queue_item = None  # Ensure queue_item is defined for finally block
            try:
                # Get an item from the queue
                queue_item = await queue.get()
                if queue_item is None:  # Sentinel value to signal worker exit
                    logger.debug(f"Web worker {worker_id} received None, exiting.")
                    break  # Exit the worker loop

                current_canonical_url, current_depth = queue_item
                logger.debug(
                    f"Worker {worker_id}: Processing {current_canonical_url} at depth {current_depth}"
                )

                # Acquire semaphore to limit concurrency before extensive processing
                async with semaphore:
                    logger.debug(
                        f"Worker {worker_id}: Acquired semaphore for {current_canonical_url}"
                    )

                    # --- Pre-download Checks ---
                    local_path_str: Optional[str] = None
                    should_skip = False
                    skip_reason = ""
                    skip_status = "failed_generic"

                    try:
                        if is_url_private_or_internal(current_canonical_url):
                            should_skip, skip_reason, skip_status = (
                                True,
                                "Blocked by SSRF protection",
                                "failed_ssrf",
                            )
                            logger.warning(f"{skip_reason}: {current_canonical_url}")
                        elif urlparse(current_canonical_url).netloc != start_domain:
                            should_skip, skip_reason, skip_status = (
                                True,
                                f"Skipping URL outside start domain {start_domain}",
                                "skipped_domain",
                            )
                            logger.debug(f"{skip_reason}: {current_canonical_url}")
                        elif not await _is_allowed_by_robots(
                            current_canonical_url, shared_client, robots_cache
                        ):
                            should_skip, skip_reason, skip_status = (
                                True,
                                "Blocked by robots.txt",
                                "failed_robotstxt",
                            )
                            logger.info(f"{skip_reason}: {current_canonical_url}")
                        else:
                            # Only calculate path if checks pass. url_to_local_path needs string base dir.
                            local_path_str = url_to_local_path(
                                str(content_base_dir), current_canonical_url
                            )
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

                    # Handle skips found during pre-checks
                    if should_skip:
                        record = IndexRecord(
                            original_url=current_canonical_url,
                            canonical_url=current_canonical_url,
                            local_path="",
                            fetch_status=skip_status,
                            error_message=skip_reason[:2000],
                        )
                        await _write_index_record(index_path, record)
                        logger.debug(
                            f"Worker {worker_id}: Released semaphore for {current_canonical_url} (skipped pre-check)"
                        )
                        # No download attempt, proceed to next item
                        continue  # Goes to finally block below

                    # --- Main Download Attempt ---
                    if local_path_str is None:  # Should not happen if !should_skip
                        logger.error(
                            f"Internal error: local_path_str is None after pre-checks for {current_canonical_url}"
                        )
                        record = IndexRecord(
                            original_url=current_canonical_url,
                            canonical_url=current_canonical_url,
                            fetch_status="failed_internal",
                            error_message="Internal error: Path not calculated",
                        )
                        await _write_index_record(index_path, record)
                        continue  # Goes to finally block

                    local_path = Path(
                        local_path_str
                    )  # Convert to Path for internal use if needed
                    result: Optional[Dict[str, Any]] = None
                    fetch_status = "failed_request"
                    error_message = "Download did not complete successfully."
                    content_md5 = None
                    http_status = None
                    detected_links = []
                    final_local_path_str = ""

                    try:
                        logger.info(
                            f"Worker {worker_id}: Attempting download: {current_canonical_url} -> {local_path}"
                        )
                        fetcher_kwargs = {
                            "url": current_canonical_url,
                            "target_local_path": str(local_path),  # Pass string path
                            "force": force,
                            "allowed_base_dir": str(
                                content_base_dir
                            ),  # Pass string path
                        }

                        if use_playwright:
                            # Playwright might need its own context management per task or globally
                            # For simplicity, assuming fetch_single_url_playwright handles context internally
                            logger.debug(
                                f"Worker {worker_id}: Using Playwright fetcher"
                            )
                            result = await fetch_single_url_playwright(
                                **fetcher_kwargs, timeout=timeout_playwright
                            )
                        else:
                            logger.debug(
                                f"Worker {worker_id}: Using Requests (httpx) fetcher"
                            )
                            result = await fetch_single_url_requests(
                                **fetcher_kwargs,
                                timeout=timeout_requests,
                                client=shared_client,
                                max_size=max_file_size,
                            )

                        logger.debug(
                            f"Worker {worker_id}: Fetcher raw result for {current_canonical_url}: {result!r}"
                        )

                        # Process fetcher result
                        if result:
                            status_from_result = result.get("status")
                            error_message_from_result = result.get("error_message")
                            content_md5 = result.get("content_md5")
                            http_status = result.get("http_status")
                            detected_links = result.get("detected_links", [])

                            # Map fetcher status to IndexRecord status
                            if status_from_result == "success":
                                fetch_status = "success"
                                final_local_path_str = str(
                                    local_path
                                )  # Store string path on success
                                error_message = None
                            elif status_from_result == "skipped":
                                fetch_status = "skipped"
                                error_message = (
                                    error_message_from_result
                                    or "Skipped (exists or TOCTOU)"
                                )
                            elif (
                                status_from_result == "failed_paywall"
                            ):  # Handle specific failures
                                fetch_status = "failed_paywall"
                                error_message = (
                                    error_message_from_result
                                    or "Failed due to potential paywall"
                                )
                            else:  # Consolidate other failures
                                fetch_status = "failed_request"
                                error_message = (
                                    error_message_from_result
                                    or f"Fetcher failed with status '{status_from_result}'."
                                )

                            if error_message:
                                error_message = str(error_message)[:2000]  # Truncate
                        else:
                            fetch_status = "failed_request"
                            error_message = "Fetcher returned None result."  # Should ideally not happen

                    except Exception as fetch_exception:
                        tb = traceback.format_exc()
                        error_msg_str = f"Exception during fetcher execution: {str(fetch_exception)} | Traceback: {tb}"
                        logger.error(
                            f"Worker {worker_id}: Exception caught processing {current_canonical_url}: {error_msg_str}"
                        )
                        fetch_status = "failed_request"
                        error_message = error_msg_str[:2000]  # Truncate

                    # --- Create and Write Index Record ---
                    record = IndexRecord(
                        original_url=current_canonical_url,  # Log original URL processed
                        canonical_url=current_canonical_url,
                        local_path=final_local_path_str,  # Store path only if successful
                        content_md5=content_md5,
                        fetch_status=fetch_status,
                        http_status=http_status,
                        error_message=error_message,
                        code_snippets=result.get("code_snippets") if result else None,
                    )
                    await _write_index_record(index_path, record)

                    # --- Recurse if successful and within depth limit ---
                    if fetch_status == "success" and current_depth < depth:
                        logger.debug(
                            f"Worker {worker_id}: Download successful for {current_canonical_url}. Found {len(detected_links)} links to potentially queue."
                        )
                        links_added_count = 0
                        for link in detected_links:
                            abs_link = None
                            try:
                                abs_link = urljoin(current_canonical_url, link.strip())
                                parsed_abs_link = urlparse(abs_link)
                                # Basic filter: only http/https, and within same domain
                                if parsed_abs_link.scheme not in ["http", "https"]:
                                    continue
                                if parsed_abs_link.netloc != start_domain:
                                    continue

                                canon_link = canonicalize_url(abs_link)

                                # Check visited status BEFORE putting in queue
                                if canon_link not in visited:
                                    visited.add(
                                        canon_link
                                    )  # Add to visited *before* queueing
                                    await queue.put((canon_link, current_depth + 1))
                                    links_added_count += 1
                                # else: logger.debug(f"Worker {worker_id}: Link already visited/queued: {canon_link}")

                            except Exception as link_e:
                                logger.warning(
                                    f"Worker {worker_id}: Failed to process/queue link '{link}' (Abs: {abs_link}) from {current_canonical_url}: {link_e}",
                                    exc_info=False,
                                )  # Less noisy logging for link errors
                        logger.debug(
                            f"Worker {worker_id}: Added {links_added_count} new links to queue from {current_canonical_url}."
                        )
                    elif fetch_status == "success" and current_depth >= depth:
                        logger.debug(
                            f"Worker {worker_id}: Reached max depth {depth}, not recursing from {current_canonical_url}"
                        )

                    # Release semaphore happens automatically at end of 'async with' block
                    logger.debug(
                        f"Worker {worker_id}: Released semaphore for {current_canonical_url}"
                    )

            except asyncio.CancelledError:
                logger.info(f"Web worker {worker_id} received cancellation.")
                break  # Exit loop on cancellation
            except Exception as e:
                # Log unexpected errors in the worker loop itself
                logger.error(
                    f"Web worker {worker_id}: Unhandled exception processing item {queue_item}: {e}",
                    exc_info=True,
                )
                # Continue processing queue if possible
            finally:
                # Ensure task_done is called for the item from the queue
                if queue_item is not None:  # Don't call task_done for the None sentinel
                    queue.task_done()
                # Update progress bar after processing attempt (success, fail, or skip)
                if progress_bar:
                    progress_bar.update(1)

        logger.debug(f"Web worker {worker_id} finished.")

    # --- Start Workers and Manage Download ---
    async with httpx.AsyncClient(
        follow_redirects=True, timeout=client_timeout, headers=headers
    ) as client:
        # Create worker tasks
        num_workers = max_concurrent_requests
        worker_tasks = [
            asyncio.create_task(worker(i, client)) for i in range(num_workers)
        ]
        logger.info(f"Started {num_workers} web download workers.")

        # Wait for the initial queue items to be processed.
        # New items are added by workers themselves.
        await queue.join()  # Wait until the queue is fully processed
        logger.info("Download queue empty and all items processed.")

        # Signal workers to exit by sending None sentinel values
        logger.debug("Sending exit signals to workers...")
        for _ in range(num_workers):
            await queue.put(None)

        # Wait for all worker tasks to complete
        results = await asyncio.gather(*worker_tasks, return_exceptions=True)
        logger.info(f"All {num_workers} web workers finished.")
        # Log any exceptions returned by workers
        for i, res in enumerate(results):
            if isinstance(res, Exception):
                logger.error(f"Web worker {i} raised an exception: {res}", exc_info=res)

    logger.info(f"Recursive download process completed for ID: {download_id}")


# --- Usage Example (if run directly) ---
async def _web_example():
    """Example of using the web download function directly."""
    print("Running direct web downloader example...")
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(levelname)s - [%(name)s:%(funcName)s:%(lineno)d] - %(message)s",
    )
    test_base_dir = Path("./web_downloader_test")
    download_id = "web_example_httpbin"
    test_content_dir = test_base_dir / "content" / download_id
    test_index_dir = test_base_dir / "index"

    # Clean up previous runs
    if test_base_dir.exists():
        shutil.rmtree(test_base_dir, ignore_errors=True)
    test_content_dir.mkdir(parents=True, exist_ok=True)
    test_index_dir.mkdir(parents=True, exist_ok=True)

    print(f"Test directories created under: {test_base_dir.resolve()}")

    try:
        # Use tqdm instance for direct testing
        with tqdm(desc=f"Downloading ({download_id})", unit="page") as pbar:
            await start_recursive_download(
                start_url="https://httpbin.org/links/10/0",  # Test URL with links
                depth=1,  # Fetch start URL and direct links
                force=True,
                download_id=download_id,
                base_dir=test_base_dir,
                use_playwright=False,  # Use httpx for this test
                max_concurrent_requests=5,
                progress_bar=pbar,
                executor=None,  # No sync tasks expected here
            )
    except Exception as e:
        print(f"Web downloader example failed: {e}")
        logger.error("Example failed", exc_info=True)
    finally:
        print("Direct web downloader example finished.")
        # Check results
        index_file = test_index_dir / f"{download_id}.jsonl"
        if index_file.exists():
            print(f"Index file created: {index_file}")
            # Count lines
            line_count = 0
            with open(index_file, "r") as f:
                line_count = sum(1 for _ in f)
            print(f"Index file contains {line_count} records.")
        else:
            print(f"Index file NOT created: {index_file}")

        # Optionally remove test dir:
        # shutil.rmtree(test_base_dir, ignore_errors=True)


if __name__ == "__main__":
    # Example of how to run the web download function directly (mainly for testing)
    asyncio.run(_web_example())
