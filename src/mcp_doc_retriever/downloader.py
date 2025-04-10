"""
Module: downloader.py

Description:
Orchestrates the asynchronous recursive download process. Manages the download queue,
handles URL canonicalization, domain/depth limits, robots.txt checks (via robots.py),
invokes fetchers (via fetchers.py), processes results, writes index records, and queues new links.

Third-party packages:
- httpx: https://www.python-httpx.org/
- aiofiles: https://github.com/Tinche/aiofiles

Internal Modules:
- mcp_doc_retriever.utils: Helper functions (timeouts, semaphores, path/URL manipulation).
- mcp_doc_retriever.models: Pydantic models (IndexRecord).
- mcp_doc_retriever.robots: Robots.txt checking logic.
- mcp_doc_retriever.fetchers: URL fetching implementations (requests, playwright).

Sample input (programmatic call):
await start_recursive_download(
    start_url="https://docs.python.org/3/",
    depth=1,
    force=False,
    download_id="my_python_docs_download",
    base_dir="/app/downloads",
    use_playwright=False,
    max_file_size=10485760 # 10MB limit
)

Expected output:
- Coordinates the download of content starting from the URL.
- Creates an index file at /app/downloads/index/<download_id>.jsonl detailing fetch attempts.
"""

import os
import json
import asyncio
import re
import httpx
import aiofiles
from urllib.parse import urlparse, urljoin
import logging
import traceback

# Configure logging (obtained from the application's central config)
logger = logging.getLogger(__name__)

# Import constants and utilities
from mcp_doc_retriever.utils import (
    TIMEOUT_REQUESTS,
    TIMEOUT_PLAYWRIGHT,
    canonicalize_url,
    url_to_local_path,
)

# Import data models
from mcp_doc_retriever.models import IndexRecord

# Import specialized functions
from mcp_doc_retriever.robots import _is_allowed_by_robots
from mcp_doc_retriever.fetchers import (
    fetch_single_url_requests,
    fetch_single_url_playwright,
)


async def start_recursive_download(
    start_url: str,
    depth: int,
    force: bool,
    download_id: str,
    base_dir: str = "/app/downloads",  # Sensible default for container environment
    use_playwright: bool = False,
    timeout_requests: int = TIMEOUT_REQUESTS,
    timeout_playwright: int = TIMEOUT_PLAYWRIGHT,
    max_file_size: int | None = None,
) -> None:
    """
    Starts the asynchronous recursive download process.

    Args:
        start_url: The initial URL to begin downloading from.
        depth: Maximum recursion depth (0 means only the start_url).
        force: Whether to overwrite existing files.
        download_id: A unique identifier for this download batch.
        base_dir: The root directory for all downloads (content and index).
        use_playwright: Whether to use Playwright (True) or httpx (False) by default.
        timeout_requests: Timeout in seconds for httpx requests.
        timeout_playwright: Timeout in seconds for Playwright operations.
        max_file_size: Maximum size in bytes for individual downloaded files.
    """
    logger.debug(f"!!! Entering start_recursive_download for ID: {download_id}, URL: {start_url}") # ADDED FOR DEBUG
    # Ensure base_dir is absolute for reliable path comparisons later
    abs_base_dir = os.path.abspath(base_dir)
    logger.info(f"Download starting. ID: {download_id}, Base Dir: {abs_base_dir}")

    # Prepare directories and index file path safely
    index_dir = os.path.join(abs_base_dir, "index")
    content_base_dir = os.path.join(abs_base_dir, "content")
    try:
        os.makedirs(index_dir, exist_ok=True)
        os.makedirs(content_base_dir, exist_ok=True)
        logger.debug(f"Ensured directories exist: {index_dir}, {content_base_dir}")
    except OSError as e:
        logger.critical(
            f"Cannot create base directories ({index_dir}, {content_base_dir}): {e}. Aborting download."
        )
        # Consider raising an exception here or returning a status
        return

    # Sanitize download_id to prevent path traversal in index filename
    # Allow letters, numbers, underscore, hyphen, dot
    safe_download_id = re.sub(r"[^a-zA-Z0-9_\-\.]", "_", download_id)
    if not safe_download_id:
        safe_download_id = "default_download"
        logger.warning(
            f"Original download_id '{download_id}' was invalid or empty, using '{safe_download_id}'"
        )
    index_path = os.path.join(index_dir, f"{safe_download_id}.jsonl")
    logger.info(f"Using index file: {index_path}")

    # Initialize queue and visited set
    queue = asyncio.Queue()
    visited = set()  # Store canonical URLs

    # Canonicalize start URL and domain
    try:
        start_canonical = canonicalize_url(start_url)
        await queue.put((start_canonical, 0))  # Add canonical URL tuple (url, depth)
        visited.add(start_canonical)  # Add start URL to visited immediately
        start_domain = urlparse(start_canonical).netloc
        if not start_domain:
            raise ValueError("Could not extract domain from start URL")
        logger.info(
            f"Starting domain: {start_domain}, Canonical start URL: {start_canonical}, Depth: {depth}"
        )
    except Exception as e:
        logger.critical(f"Invalid start URL '{start_url}': {e}. Aborting download.")
        # Optionally write a failure record to index?
        return

    robots_cache = {}

    # Use a single shared httpx client for the entire download process
    # Configure reasonable timeouts
    client_timeout = httpx.Timeout(timeout_requests, read=timeout_requests, connect=15)
    # Define standard headers
    headers = {
        "User-Agent": "MCPBot/1.0 (compatible; httpx; +https://example.com/botinfo)"
    }  # Example informative UA

    async with httpx.AsyncClient(
        follow_redirects=True, timeout=client_timeout, headers=headers
    ) as client:
        while not queue.empty():
            # Dequeue the next URL to process
            current_canonical_url, current_depth = await queue.get()
            logger.debug(
                f"Processing URL: {current_canonical_url} at depth {current_depth}"
            )

            # --- Pre-download Checks ---
            try:
                # Domain restriction check (using canonical URL)
                parsed_url = urlparse(current_canonical_url)
                # Allow subdomains? Example: if not (parsed_url.netloc == start_domain or parsed_url.netloc.endswith(f".{start_domain}")):
                if parsed_url.netloc != start_domain:
                    logger.debug(
                        f"Skipping URL outside start domain {start_domain}: {current_canonical_url}"
                    )
                    queue.task_done()  # Mark task as done even if skipped
                    continue

                # Robots.txt check - Use the shared client
                allowed = await _is_allowed_by_robots(
                    current_canonical_url, client, robots_cache
                )
                if not allowed:
                    logger.info(f"Blocked by robots.txt: {current_canonical_url}")
                    record = IndexRecord(
                        original_url=current_canonical_url,  # In this context, original_url IS the canonical one being checked
                        canonical_url=current_canonical_url,
                        local_path="",
                        content_md5=None,
                        fetch_status="failed_robotstxt",
                        http_status=None,
                        error_message="Blocked by robots.txt",
                    )
                    # Write index record for skipped due to robots.txt
                    try:
                        async with aiofiles.open(
                            index_path, "a", encoding="utf-8"
                        ) as f:
                            await f.write(
                                record.model_dump_json(exclude_none=True) + "\n"
                            )
                    except Exception as write_e:
                        logger.error(
                            f"Failed to write robots.txt index record for {current_canonical_url}: {write_e}"
                        )
                    queue.task_done()
                    continue

                # Map URL to local path *after* robots check and domain check pass
                local_path = url_to_local_path(content_base_dir, current_canonical_url)
                logger.debug(
                    f"Mapped {current_canonical_url} to local path: {local_path}"
                )

            except Exception as pre_check_e:
                logger.error(
                    f"Error during pre-download checks for {current_canonical_url}: {pre_check_e}",
                    exc_info=True,
                )
                # Optionally write a failure record here? Need to decide if it's 'failed_request'
                queue.task_done()  # Mark task done even on pre-check error
                continue  # Skip this URL

            # --- Main Download Attempt ---
            result = None
            fetch_status = (
                "failed_request"  # Default to failure unless explicitly successful
            )
            error_message = "Download did not complete successfully."  # Default error
            content_md5 = None
            http_status = None
            detected_links = []
            final_local_path = ""  # Store path only if successfully saved

            try:
                logger.info(
                    f"Attempting download: {current_canonical_url} (Depth {current_depth}) -> {local_path}"
                )
                fetcher_kwargs = {
                    "url": current_canonical_url,  # Pass canonical URL to fetchers
                    "target_local_path": local_path,
                    "force": force,
                    "allowed_base_dir": content_base_dir,
                }

                # Choose the fetcher based on the flag
                if use_playwright:
                    logger.debug("Using Playwright fetcher")
                    result = await fetch_single_url_playwright(
                        **fetcher_kwargs, timeout=timeout_playwright
                    )
                else:
                    logger.debug("Using Requests (httpx) fetcher")
                    result = await fetch_single_url_requests(
                        **fetcher_kwargs,
                        timeout=timeout_requests,
                        client=client,
                        max_size=max_file_size,
                    )

                # --- Log the raw result from the fetcher ---
                logger.info(f"Fetcher raw result for {current_canonical_url}: {result!r}") # Use !r for detailed repr

                # --- Process the result dictionary ---
                if result:
                    status_from_result = result.get("status")
                    error_message_from_result = result.get("error_message")
                    content_md5 = result.get("content_md5")
                    http_status = result.get("http_status")
                    detected_links = result.get("detected_links", [])

                    # Map fetcher status to IndexRecord status
                    if status_from_result == "success":
                        fetch_status = "success"
                        final_local_path = local_path  # Store path on success
                        error_message = None
                    elif status_from_result == "skipped":
                        fetch_status = "skipped"
                        error_message = (
                            error_message_from_result or "Skipped (exists or TOCTOU)"
                        )
                        logger.info(
                            f"Skipped download for {current_canonical_url}: {error_message}"
                        )
                    elif (
                        status_from_result == "failed_paywall"
                    ):  # Handle specific failure types
                        fetch_status = "failed_paywall"
                        error_message = (
                            error_message_from_result
                            or "Failed due to potential paywall"
                        )
                    else:  # Consolidate other failures ('failed', 'failed_request')
                        fetch_status = "failed_request"
                        # Use error from result if available, otherwise keep default
                        error_message = (
                            error_message_from_result
                            or f"Fetcher failed with status '{status_from_result}'."
                        )

                    # Truncate error message if it exists
                    if error_message:
                        error_message = str(error_message)[:2000]  # Limit length

                else:
                    # This case should ideally not happen if fetchers always return a dict
                    logger.error(
                        f"Fetcher returned None for {current_canonical_url}, treating as failed_request."
                    )
                    error_message = "Fetcher returned None result."
                    fetch_status = "failed_request"

            except (
                Exception
            ) as fetch_exception:  # Catch ALL exceptions during the fetcher call itself
                tb = traceback.format_exc()
                error_msg_str = f"Exception during fetcher execution: {str(fetch_exception)} | Traceback: {tb}"
                logger.error(
                    f"Exception caught processing {current_canonical_url}: {error_msg_str}"
                )
                fetch_status = "failed_request"
                error_message = error_msg_str[:2000]  # Truncate

            # --- Create and Write Index Record ---
            try:
                record = IndexRecord(
                    original_url=current_canonical_url,  # Store the canonical URL fetched
                    canonical_url=current_canonical_url,
                    local_path=final_local_path
                    if fetch_status == "success"
                    else "",  # Store path only on success
                    content_md5=content_md5,
                    fetch_status=fetch_status,
                    http_status=http_status,
                    error_message=error_message,
                )
                logger.info(f"Preparing to write index record object: {record!r}")
                record_json = record.model_dump_json(exclude_none=True)
                logger.info(f"Writing index record JSON: {record_json}")
                logger.debug(f"Attempting to open index file for append: {index_path}") # Log before open
                async with aiofiles.open(index_path, "a", encoding="utf-8") as f:
                    await f.write(record_json + "\n")
                    logger.debug(f"Successfully wrote index record for {current_canonical_url}") # Log after write

            except Exception as write_e:
                logger.critical(
                    f"CRITICAL: Failed to create or write index record for {current_canonical_url}: {write_e}",
                    exc_info=True,
                )
                logger.error(
                    f"Failed Record Data Hint: status={fetch_status}, error='{error_message}'"
                )

            # --- Recurse if successful and within depth limit ---
            if fetch_status == "success" and current_depth < depth:
                logger.debug(
                    f"Successfully downloaded {current_canonical_url}. Found {len(detected_links)} potential links. Max depth {depth}, current {current_depth}."
                )
                if not detected_links:
                    logger.info(
                        f"No links detected or extracted from {current_canonical_url}"
                    )

                for link in detected_links:
                    abs_link = None
                    try:
                        # Construct absolute URL relative to the *current* page's canonical URL
                        abs_link = urljoin(current_canonical_url, link.strip())

                        # Basic filter: only http/https
                        parsed_abs_link = urlparse(abs_link)
                        if parsed_abs_link.scheme not in ["http", "https"]:
                            logger.debug(f"Skipping non-http(s) link: {abs_link}")
                            continue

                        # Canonicalize the potential next link
                        canon_link = canonicalize_url(abs_link)
                        canon_domain = urlparse(canon_link).netloc

                        # Check domain and visited status BEFORE putting in queue
                        if canon_domain == start_domain and canon_link not in visited:
                            visited.add(
                                canon_link
                            )  # Add to visited *before* adding to queue
                            logger.debug(
                                f"Adding link to queue: {canon_link} (from {link} on {current_canonical_url})"
                            )
                            await queue.put((canon_link, current_depth + 1))
                        # Optional: Log reasons for skipping links
                        # elif canon_domain != start_domain:
                        #     logger.debug(f"Skipping link to different domain: {canon_link}")
                        # elif canon_link in visited:
                        #     logger.debug(f"Skipping link already processed or in queue: {canon_link}")

                    except Exception as link_processing_exception:
                        logger.warning(
                            f"Failed to process or queue link '{link}' found in {current_canonical_url} (Absolute: {abs_link}): {link_processing_exception}",
                            exc_info=True,
                        )
                        # Continue to the next link even if one fails
            elif fetch_status == "success" and current_depth >= depth:
                logger.info(
                    f"Reached max depth {depth}, not recursing further from {current_canonical_url}"
                )

            # Mark the current task from the queue as done
            queue.task_done()

        # Wait for all tasks in the queue to be processed (optional, good practice)
        await queue.join()
        logger.info(
            f"Download queue processed. Download finished for ID: {download_id}"
        )


def usage_example():
    """Demonstrates programmatic usage of the downloader's start_recursive_download."""
    # Define a temporary directory for the example
    test_dir = "./downloader_usage_example_downloads"

    # Basic logging setup for the example
    log_level = logging.INFO
    log_format = (
        "%(asctime)s - %(levelname)s - [%(name)s:%(funcName)s:%(lineno)d] - %(message)s"
    )
    date_format = "%Y-%m-%d %H:%M:%S"
    logging.basicConfig(level=log_level, format=log_format, datefmt=date_format)
    logging.getLogger("httpx").setLevel(logging.WARNING)  # Quiet verbose logs

    async def run_example():
        logger.info(f"Starting example download to {test_dir}...")
        try:
            await start_recursive_download(
                start_url="https://example.com",  # Use a simple, allowed URL
                depth=0,  # Only download the start URL for a quick example
                force=True,  # Overwrite if run multiple times
                download_id="downloader_example_run",
                base_dir=test_dir,
                use_playwright=False,  # Use the faster httpx fetcher
                max_file_size=1024 * 1024,  # 1MB limit for example
            )
            logger.info("Example download function completed.")
            # Verify output
            index_file = os.path.join(test_dir, "index", "downloader_example_run.jsonl")
            content_file = os.path.join(
                test_dir, "content", "example.com", "index.html"
            )
            if os.path.exists(index_file):
                logger.info(f"Index file found: {index_file}")
            else:
                logger.error(f"Index file NOT found: {index_file}")
            if os.path.exists(content_file):
                logger.info(f"Content file found: {content_file}")
            else:
                logger.error(f"Content file NOT found: {content_file}")

        except Exception as e:
            logger.error(
                f"An error occurred during the usage example: {e}", exc_info=True
            )
        finally:
            # Optional: Clean up the test directory afterwards
            # import shutil
            # if os.path.exists(test_dir):
            #     logger.info(f"Cleaning up test directory: {test_dir}")
            #     shutil.rmtree(test_dir)
            pass

    asyncio.run(run_example())


if __name__ == "__main__":
    # This block executes when the script is run directly
    # (e.g., python -m src.mcp_doc_retriever.downloader)
    # It runs the usage example.
    usage_example()
