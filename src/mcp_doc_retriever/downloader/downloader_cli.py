"""
Module: downloader_cli.py (Main Entry Point)

Description:
Provides the Command Line Interface (CLI) using Typer to orchestrate the
downloading of documentation from various sources (Git repositories, websites via
HTTP requests, or websites via Playwright). It coordinates the overall workflow,
handles user input, sets up logging, and calls appropriate backend functions
for cloning, crawling, and processing documentation files. Includes progress
reporting via tqdm and concurrency limiting for web crawls.

Third-party Package Documentation:
- Typer (CLI): https://typer.tiangolo.com/
- httpx (HTTP Client): https://www.python-httpx.org/
- aiofiles (Async File I/O): https://github.com/Tinche/aiofiles
- Playwright (Browser Automation): https://playwright.dev/python/
- tqdm (Progress Bars): https://tqdm.github.io/
- Pydantic (Data Validation): https://docs.pydantic.dev/

Internal Modules Used:
- mcp_doc_retriever.downloader_workflow: Contains the main workflow logic.
- mcp_doc_retriever.utils: Shared helper functions (TIMEOUT constants).
- mcp_doc_retriever.git_downloader: Git specific checks.

Sample CLI Input:
1. Download Python docs (website source, recursive):
   python -m mcp_doc_retriever.downloader_cli download website https://docs.python.org/3/ python_docs --depth 3 -v

2. Download Flask tutorial docs (git source, specific path):
   python -m mcp_doc_retriever.downloader_cli download git https://github.com/pallets/flask flask_docs --doc-path examples/tutorial

3. Download Playwright intro page (playwright source, single page):
   python -m mcp_doc_retriever.downloader_cli download playwright https://playwright.dev/python/docs/intro playwright_intro --depth 0 --force

Expected Output:
- Depending on the source type:
    - Git: Clones the repository (potentially sparse checkout) into `./downloads/content/<download_id>/repo/`, scans for documentation files (.md, .rst, .html), and shows progress.
    - Website/Playwright: Crawls the website starting from the given URL up to the specified depth, downloading HTML pages into `./downloads/content/<download_id>/<path_structure>/`. Creates an index file `./downloads/index/<download_id>.jsonl` recording the status of each downloaded URL. Shows progress for pages processed.
- Logs detailed information about the process to the console.
- Creates the specified `base_dir` structure (`./downloads/` by default).
- Handles errors gracefully and reports them.
"""

import asyncio
import logging
import re
import os
from pathlib import Path
from typing import Optional
import typer
from concurrent.futures import ThreadPoolExecutor

# Assuming these are correctly placed relative to this file
from .workflow import fetch_documentation_workflow
from .git_downloader import check_git_dependency
from mcp_doc_retriever.utils import TIMEOUT_REQUESTS, TIMEOUT_PLAYWRIGHT  # Import defaults


# --- Global Config ---
# Configure logging (can be overridden by CLI)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - [%(name)s:%(funcName)s:%(lineno)d] - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)  # Logger for this module
# Silence noisy libraries unless verbose
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("websockets").setLevel(logging.WARNING)

# ThreadPoolExecutor for synchronous tasks (git clone, sync file scan)
# Defined here and passed down to avoid multiple pools
executor = ThreadPoolExecutor(
    max_workers=os.cpu_count(), thread_name_prefix="DownloaderSyncWorker"
)


# --- Typer CLI Definition ---
app = typer.Typer(
    add_completion=False,
    help="MCP Documentation Downloader: Fetches docs from Git, Websites, or Playwright.",
)


@app.command(
    "download",
    help="Download documentation from a specified source.",
)
def download_command(
    source_type: str = typer.Argument(
        ...,
        help='Type of source: "git", "website", or "playwright". Case-insensitive.',
        metavar="SOURCE_TYPE",
    ),
    source_location: str = typer.Argument(
        ...,
        help="URL (for website/playwright) or Git repository URL.",
        metavar="URL_OR_REPO",
    ),
    download_id: str = typer.Argument(
        ...,
        help="Unique identifier for this download batch (used in paths).",
        metavar="DOWNLOAD_ID",
    ),
    doc_path: Optional[str] = typer.Option(
        None,
        "--doc-path",
        "-p",
        help="Path within git repo containing docs (for git source type & sparse checkout).",
    ),
    depth: int = typer.Option(
        5, "--depth", "-d", help="Max recursion depth for website/playwright crawl."
    ),
    base_dir: Path = typer.Option(
        Path("./downloads"),
        "--base-dir",
        "-b",
        help="Root directory for downloads.",
        resolve_path=True,
        file_okay=False,
        dir_okay=True,
        writable=True,
    ),
    force: bool = typer.Option(
        False, "--force", "-f", help="Overwrite existing clone/files if they exist."
    ),
    max_file_size: Optional[int] = typer.Option(
        10 * 1024 * 1024,
        "--max-file-size",
        help="Max file size in bytes (0 or negative for unlimited).",
    ),
    timeout_requests: Optional[int] = typer.Option(
        None,
        "--timeout-req",
        help=f"Timeout for HTTP requests (default: {TIMEOUT_REQUESTS}s).",
    ),
    timeout_playwright: Optional[int] = typer.Option(
        None,
        "--timeout-play",
        help=f"Timeout for Playwright operations (default: {TIMEOUT_PLAYWRIGHT}s).",
    ),
    max_concurrent: int = typer.Option(
        50,
        "--max-concurrent",
        "-c",
        help="Maximum concurrent download requests for web crawls.",
    ),
    verbose: bool = typer.Option(
        False, "--verbose", "-v", help="Enable debug logging."
    ),
):
    """
    CLI command to initiate the documentation download workflow.
    """
    # Configure logging level based on verbosity flag
    log_level = logging.DEBUG if verbose else logging.INFO
    logging.getLogger().setLevel(log_level)  # Set root logger level
    # Update handler levels as well if already configured
    for handler in logging.getLogger().handlers:
        handler.setLevel(log_level)
    # Re-silence noisy libraries if not verbose
    if not verbose:
        logging.getLogger("httpx").setLevel(logging.WARNING)
        logging.getLogger("websockets").setLevel(logging.WARNING)
    logger.info(f"Log level set to {logging.getLevelName(log_level)}")
    logger.debug(f"Initial arguments: {locals()}")  # Log args if verbose

    # Normalize source_type
    source_type = source_type.lower()
    valid_source_types = ["git", "website", "playwright"]
    if source_type not in valid_source_types:
        logger.error(
            f"Invalid source_type '{source_type}'. Must be one of {valid_source_types}"
        )
        raise typer.BadParameter(
            f"Invalid source_type '{source_type}'. Choose from {valid_source_types}."
        )

    # Basic validation
    if source_type == "git":
        if not source_location:
            logger.error("Missing repository URL for git source type.")
            raise typer.BadParameter("Missing repository URL for git source type.")
        # Check git dependency early if source type is git
        if not check_git_dependency():
            # Error already logged by check_git_dependency
            raise typer.Exit(code=1)
    elif source_type in ["website", "playwright"]:
        if not source_location:
            logger.error(f"Missing URL for {source_type} source type.")
            raise typer.BadParameter(f"Missing URL for {source_type} source type.")
        if doc_path:
            logger.warning(
                "--doc-path is only applicable for 'git' source type. Ignoring."
            )
            doc_path = None  # Ensure it's None if not git type

    # Sanitize download_id for path usage (allow letters, numbers, underscore, hyphen)
    # Keep original for potential display purposes if needed later
    safe_download_id = re.sub(r"[^\w\-]+", "_", download_id)
    if not safe_download_id:
        safe_download_id = "default_download"
        logger.warning(
            f"Download ID '{download_id}' was empty or invalid, using '{safe_download_id}'"
        )
    elif safe_download_id != download_id:
        logger.warning(
            f"Download ID '{download_id}' sanitized to '{safe_download_id}' for filesystem paths."
        )

    # Ensure base directory exists
    try:
        base_dir.mkdir(parents=True, exist_ok=True)
        logger.info(f"Using base directory: {base_dir.resolve()}")
    except OSError as e:
        logger.error(f"Failed to create or access base directory {base_dir}: {e}")
        raise typer.Exit(code=1)

    # Run the async workflow
    try:
        asyncio.run(
            fetch_documentation_workflow(
                source_type=source_type,
                download_id=safe_download_id,  # Use sanitized ID for workflow
                repo_url=source_location if source_type == "git" else None,
                doc_path=doc_path,
                url=source_location if source_type != "git" else None,
                base_dir=base_dir,
                depth=depth,
                force=force,
                max_file_size=max_file_size
                if max_file_size and max_file_size > 0
                else None,
                timeout_requests=timeout_requests,  # Defaults handled downstream
                timeout_playwright=timeout_playwright,  # Defaults handled downstream
                max_concurrent_requests=max_concurrent,
                executor=executor,  # Pass the shared executor
                logger_override=logger,  # Pass logger instance if needed downstream (optional)
            )
        )
        logger.info(f"Workflow for '{download_id}' completed successfully.")
        # Typer automatically exits with 0 on successful return
    except (ValueError, RuntimeError, FileNotFoundError) as e:
        # These are expected configuration or setup errors
        logger.error(
            f"Workflow failed for '{download_id}': {e}",
            exc_info=log_level <= logging.DEBUG,
        )  # Show traceback if verbose
        raise typer.Exit(code=1)
    except Exception as e:
        # Catch-all for unexpected errors
        logger.error(
            f"An unexpected error occurred during workflow for '{download_id}': {e}",
            exc_info=True,
        )  # Always show traceback
        raise typer.Exit(code=1)
    finally:
        # Clean up the executor when the application exits
        logger.debug("Shutting down thread pool executor.")
        executor.shutdown(wait=True)  # Wait for sync tasks like file scans to finish
        logger.debug("Executor shutdown complete.")


# --- Main execution ---
if __name__ == "__main__":
    # This allows running the CLI via `python -m mcp_doc_retriever.downloader_cli ...`
    # or `python path/to/downloader_cli.py ...`
    app()
