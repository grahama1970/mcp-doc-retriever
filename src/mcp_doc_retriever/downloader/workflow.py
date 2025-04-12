"""
Description:
  This module acts as the central orchestrator for the document download process.
  The main function, `fetch_documentation_workflow`, takes parameters defining
  the source (Git, Website, Playwright), target locations, and download options.
  Based on the `source_type`, it:
  1. Validates input parameters (e.g., ensures URL is provided for web sources).
  2. Creates necessary base directories (`index/` and `content/<download_id>/`).
  3. For 'git': Calls `git_downloader.run_git_clone` to clone the repository
     (potentially sparse) and `git_downloader.scan_local_files_async` to find
     relevant files. Includes progress reporting for file processing (TODO: Add processing).
  4. For 'website'/'playwright': Calls `web_downloader.start_recursive_download`
     to initiate the web crawl, passing along parameters like depth, timeouts,
     and progress bar instance.
  It utilizes a shared `ThreadPoolExecutor` provided by the caller (e.g., CLI)
  for running synchronous tasks like Git commands or disk I/O scans asynchronously.

Third-Party Documentation:
  - tqdm (Used for progress bars): https://tqdm.github.io/

Internal Module Dependencies:
  - .git_downloader (run_git_clone, scan_local_files_async)
  - .web_downloader (start_recursive_download)
  - mcp_doc_retriever.utils (TIMEOUT_REQUESTS, TIMEOUT_PLAYWRIGHT)

Sample Input (Conceptual - as called from CLI or API):
  # Git Example
  await fetch_documentation_workflow(
      source_type="git",
      download_id="flask_docs",
      repo_url="https://github.com/pallets/flask.git",
      doc_path="examples/tutorial",
      base_dir=Path("./downloads"),
      force=False,
      executor=ThreadPoolExecutor()
  )
  # Website Example
  await fetch_documentation_workflow(
      source_type="website",
      download_id="python_docs",
      url="https://docs.python.org/3/",
      base_dir=Path("./downloads"),
      depth=1,
      force=False,
      executor=ThreadPoolExecutor()
  )

Sample Expected Output:
  - Creates directories `./downloads/index/` and `./downloads/content/<download_id>/`.
  - If 'git': Clones repo into `./downloads/content/<download_id>/repo/` and logs found files.
  - If 'website'/'playwright': Creates `./downloads/index/<download_id>.jsonl` and
    populates `./downloads/content/<download_id>/` with downloaded files (flat structure).
  - Prints logs indicating workflow start, progress (via tqdm), and completion/errors.
"""

import asyncio
import logging
import shutil
from pathlib import Path
from typing import Optional, Set
from concurrent.futures import ThreadPoolExecutor

# Import tqdm for progress reporting
from tqdm.asyncio import tqdm

# Internal module imports
from .git_downloader import run_git_clone, scan_local_files_async
from .web_downloader import start_recursive_download
from mcp_doc_retriever.utils import (
    TIMEOUT_REQUESTS,
    TIMEOUT_PLAYWRIGHT,
)  # Default timeouts


# Get logger for this module
logger = logging.getLogger(__name__)


async def fetch_documentation_workflow(
    source_type: str,
    download_id: str,
    repo_url: Optional[str] = None,
    doc_path: Optional[str] = None,
    url: Optional[str] = None,
    base_dir: Path = Path("./downloads"),
    depth: int = 3,
    force: bool = False,
    max_file_size: Optional[int] = 10 * 1024 * 1024,
    timeout_requests: Optional[int] = None,
    timeout_playwright: Optional[int] = None,
    max_concurrent_requests: int = 50,
    executor: ThreadPoolExecutor = None,
    logger_override=None,
) -> None:
    """
    Orchestrates documentation fetching based on the source type. Delegates tasks.

    Public API Args (matches DocDownloadRequest model):
        source_type: Type of source to download from ('git', 'website', or 'playwright')
        download_id: Unique identifier for this download request
        repo_url: URL of git repository (required for 'git' source_type)
        doc_path: Path within git repository to documentation (required for 'git' source_type)
        url: URL to download from (required for 'website' and 'playwright' source_types)
        depth: Maximum depth to crawl for website/playwright downloads (default: 3)
        force: Whether to force re-download existing content (default: False)
        max_file_size: Maximum file size to download in bytes (default: 10MB)
        timeout_requests: Timeout in seconds for HTTP requests (default: None)
        timeout_playwright: Timeout in seconds for Playwright operations (default: None)

    Internal Implementation Args:
        base_dir: Base directory for downloads (default: ./downloads)
        max_concurrent_requests: Maximum concurrent requests for web downloads
        executor: ThreadPoolExecutor for running synchronous tasks
        logger_override: Optional logger instance to use instead of module logger
    """
    _logger = logger_override or logger  # Use override or module logger

    # Resolve timeouts to defaults if None
    req_timeout = timeout_requests if timeout_requests is not None else TIMEOUT_REQUESTS
    play_timeout = (
        timeout_playwright if timeout_playwright is not None else TIMEOUT_PLAYWRIGHT
    )

    _logger.info(f"Starting fetch workflow (ID: {download_id}, Type: {source_type})")
    if executor is None:
        # This should not happen if called from CLI, but handle direct calls
        _logger.error(
            "ThreadPoolExecutor instance not provided to fetch_documentation_workflow."
        )
        raise ValueError("Missing required ThreadPoolExecutor instance.")

    # Define content directory specific to this download ID
    content_base_dir = base_dir / "content" / download_id
    # Index directory is shared, file is specific
    index_dir = base_dir / "index"

    # Ensure base directories exist (CLI ensures base_dir, workflow ensures subdirs)
    try:
        content_base_dir.mkdir(parents=True, exist_ok=True)
        index_dir.mkdir(parents=True, exist_ok=True)
        _logger.debug(f"Ensured content directory exists: {content_base_dir}")
        _logger.debug(f"Ensured index directory exists: {index_dir}")
    except OSError as e:
        _logger.error(f"Failed to create required subdirectories under {base_dir}: {e}")
        raise RuntimeError(f"Directory creation failed for {download_id}") from e

    if source_type == "git":
        _logger.info(f"Processing git source for {download_id}...")
        if not repo_url:  # Validation should happen in CLI, but double-check
            raise ValueError("Missing repo_url for git source type in workflow.")

        repo_clone_path = (
            content_base_dir / "repo"
        )  # Standard location within content dir

        # --- Git Clone (using git_downloader) ---
        if force and repo_clone_path.exists():
            _logger.warning(
                f"Force flag set. Removing existing directory: {repo_clone_path}"
            )
            try:
                # Use executor for potentially slow shutil.rmtree
                loop = asyncio.get_running_loop()
                await loop.run_in_executor(executor, shutil.rmtree, repo_clone_path)
            except Exception as e:  # Catch broader exceptions during sync execution
                _logger.error(
                    f"Failed to remove existing directory {repo_clone_path}: {e}",
                    exc_info=True,
                )
                raise RuntimeError(
                    f"Failed to clear target directory {repo_clone_path}"
                ) from e

        if not repo_clone_path.exists():
            try:
                await run_git_clone(repo_url, repo_clone_path, doc_path, executor)
            except Exception as e:
                _logger.error(
                    f"Git clone process failed for {repo_url}: {e}", exc_info=True
                )
                return  # Stop workflow if clone fails
        else:
            _logger.info(
                f"Using existing repository directory (force=False): {repo_clone_path}"
            )

        # --- Scan Files & Progress (using git_downloader) ---
        target_scan_path = repo_clone_path / doc_path if doc_path else repo_clone_path
        allowed_extensions: Set[str] = {".html", ".htm", ".md", ".rst"}

        files_to_process = await scan_local_files_async(
            target_scan_path, allowed_extensions, executor
        )
        total_files = len(files_to_process)

        pbar_git_desc = f"Processing git files ({download_id})"
        pbar_git = tqdm(
            total=total_files,
            desc=pbar_git_desc,
            unit="file",
            smoothing=0.1,
            disable=(total_files == 0),
        )
        try:
            for file_path in files_to_process:
                # TODO: Implement actual file processing (copying, indexing, etc.)
                # Example: await process_git_file_async(file_path, content_base_dir, index_dir / f"{download_id}_git.jsonl")
                await asyncio.sleep(0.005)  # Simulate async work per file
                pbar_git.update(1)
        finally:
            pbar_git.close()

        _logger.info(f"Git processing finished for {download_id}.")

    elif source_type in ["website", "playwright"]:
        use_playwright = source_type == "playwright"
        _logger.info(
            f"Processing {source_type} source for {download_id} (URL: {url})..."
        )
        if not url:  # Validation should happen in CLI, but double-check
            raise ValueError(f"Missing url for {source_type} source type in workflow.")

        # --- TQDM Setup for Web Crawl ---
        # TODO: Implement optional pre-scan (sitemap/shallow crawl) here
        #       to determine total_links for a determinate progress bar.
        pbar_web_desc = f"Downloading ({source_type}, {download_id})"
        # Create indeterminate progress bar instance
        pbar_web = tqdm(desc=pbar_web_desc, unit="page", smoothing=0.1)

        # --- Start Web Download (using web_downloader) ---
        try:
            await start_recursive_download(
                start_url=url,
                depth=depth,
                force=force,
                download_id=download_id,  # Pass sanitized ID
                base_dir=base_dir,  # Pass base_dir (contains index/content)
                use_playwright=use_playwright,
                timeout_requests=req_timeout,
                timeout_playwright=play_timeout,
                max_file_size=max_file_size,
                progress_bar=pbar_web,  # Pass the tqdm instance
                max_concurrent_requests=max_concurrent_requests,
                executor=executor,  # Pass executor for potential sync tasks within web download
            )
        except Exception as e:
            _logger.error(
                f"Recursive download failed for {url} (ID: {download_id}): {e}",
                exc_info=True,
            )
            # Let finally block handle pbar closure
        finally:
            pbar_web.close()  # Ensure progress bar is always closed

        _logger.info(
            f"{source_type.capitalize()} processing finished for {download_id}."
        )

    else:
        # This case should be caught by CLI validation, but belts and suspenders
        _logger.error(f"Invalid source_type '{source_type}' encountered in workflow.")
        raise ValueError(f"Invalid source_type '{source_type}'")

    _logger.info(f"Fetch workflow completed for ID: {download_id}")


# --- Usage Example (if run directly, though unlikely) ---
async def _workflow_example():
    """Example of calling the workflow function directly."""
    print("Running direct workflow example...")
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(levelname)s - [%(name)s:%(funcName)s:%(lineno)d] - %(message)s",
    )
    test_executor = ThreadPoolExecutor(max_workers=2)
    try:
        await fetch_documentation_workflow(
            source_type="website",
            download_id="direct_example_test",
            url="https://httpbin.org/html",  # Simple test URL
            base_dir=Path("./direct_test_downloads"),
            depth=0,  # Only fetch the start URL
            force=True,
            max_concurrent_requests=5,
            executor=test_executor,
        )
    except Exception as e:
        print(f"Direct workflow example failed: {e}")
        example_passed = False # Mark as failed
    else:
        example_passed = True # Mark as passed if no exception
    finally:
        test_executor.shutdown()

    print("\n------------------------------------")
    if example_passed:
        print("✓ Direct workflow example finished successfully (though internal errors may have occurred).")
    else:
        print("✗ Direct workflow example failed.")
    print("------------------------------------")

    print("Direct workflow example finished.") # Keep original finish message


if __name__ == "__main__":
    # Example of how to run the workflow directly (mainly for testing)
    asyncio.run(_workflow_example())
