# File: src/mcp_doc_retriever/cli.py (Updated)

"""
Module: cli.py (Main Project CLI Entry Point)

Description:
Provides the main command-line interface for the MCP Document Retriever project
using Typer. It defines commands for downloading and potentially searching documentation.

Usage:
  python -m mcp_doc_retriever download ...
  python -m mcp_doc_retriever search ... (if search commands are added)
"""

import typer
import logging
import asyncio
import re
import os
import sys
import uuid
from pathlib import Path
from typing import Optional, List
from concurrent.futures import ThreadPoolExecutor

# Import necessary functions/modules from the package
from .downloader.workflow import fetch_documentation_workflow
from .downloader.git_downloader import check_git_dependency

# from .searcher import cli as searcher_cli # Example if search commands exist
from .utils import (
    TIMEOUT_REQUESTS,
    TIMEOUT_PLAYWRIGHT,
)  # Import defaults

# --- Typer App Initialization ---
app = typer.Typer(
    name="mcp-doc-retriever",
    help="MCP Document Retriever: Download and process documentation.",
    add_completion=False,
    no_args_is_help=True,
)

# --- Logging Configuration ---
# Configure root logger - basic setup
log_format = "%(asctime)s - %(levelname)-8s - [%(name)s] %(message)s"
date_format = "%Y-%m-%d %H:%M:%S"
logging.basicConfig(
    level=logging.INFO,
    format=log_format,
    datefmt=date_format,
    stream=sys.stdout,
    force=True,
)
logger_cli = logging.getLogger("cli")  # Logger for CLI specific messages

# Silence noisy libraries by default
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("websockets").setLevel(logging.WARNING)

# --- Shared ThreadPoolExecutor ---
# *** Create the executor here in the main CLI entry point ***
cli_executor = ThreadPoolExecutor(
    max_workers=os.cpu_count(), thread_name_prefix="CLI_SyncWorker"
)

# --- Define Commands Directly on the Main App ---


@app.command(
    "download",  # The command name itself
    help="Download documentation from Git, Website, or Playwright source.",
)
def download_command(
    # Arguments defined directly here now
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
        False, "--verbose", "-v", help="Enable debug logging for this download."
    ),
):
    """
    CLI command to initiate the documentation download workflow.
    Parses arguments, performs validation, sets logging, and calls the core workflow.
    """
    # --- Logging Setup (Adjust level based on verbose flag) ---
    log_level = logging.DEBUG if verbose else logging.INFO
    root_logger = logging.getLogger()
    for handler in root_logger.handlers:
        handler.setLevel(log_level)
    root_logger.setLevel(log_level)
    if not verbose:  # Re-silence if needed
        logging.getLogger("httpx").setLevel(logging.WARNING)
        logging.getLogger("websockets").setLevel(logging.WARNING)
    logger_cli.info(
        f"Log level set to {logging.getLevelName(log_level)} for download '{download_id}'"
    )
    logger_cli.debug(f"Download Arguments: {locals()}")

    # --- Argument Validation and Processing (same as before) ---
    source_type = source_type.lower()
    valid_source_types = ["git", "website", "playwright"]
    if source_type not in valid_source_types:
        logger_cli.error(
            f"Invalid source_type '{source_type}'. Choose from {valid_source_types}"
        )
        raise typer.BadParameter(f"Invalid source_type '{source_type}'.")

    if source_type == "git":
        if not source_location:
            raise typer.BadParameter("Missing repository URL for git.")
        if not check_git_dependency():
            logger_cli.error("Git dependency check failed.")
            raise typer.Exit(code=1)
        if doc_path is None:
            logger_cli.warning("doc_path not provided for git. Performing full clone.")
    elif source_type in ["website", "playwright"]:
        if not source_location:
            raise typer.BadParameter(f"Missing URL for {source_type}.")
        if doc_path:
            logger_cli.warning("--doc-path ignored for non-git source type.")
            doc_path = None

    safe_download_id = re.sub(r"[^\w\-]+", "_", download_id)
    if not safe_download_id or len(safe_download_id) < 3:
        safe_download_id = f"dl_{uuid.uuid4().hex[:8]}"
        logger_cli.warning(f"Using generated ID: {safe_download_id}")
    elif safe_download_id != download_id:
        logger_cli.warning(f"Sanitized ID '{download_id}' to '{safe_download_id}'")

    try:
        base_dir.mkdir(parents=True, exist_ok=True)
        logger_cli.info(f"Using base directory: {base_dir.resolve()}")
    except OSError as e:
        logger_cli.error(f"Failed to create base directory {base_dir}: {e}")
        raise typer.Exit(code=1)

    # --- Execute Async Workflow ---
    try:
        logger_cli.info(f"Starting download workflow for ID: {safe_download_id}")
        # *** Pass the cli_executor instance here ***
        asyncio.run(
            fetch_documentation_workflow(
                source_type=source_type,
                download_id=safe_download_id,
                repo_url=source_location if source_type == "git" else None,
                doc_path=doc_path,
                url=source_location if source_type != "git" else None,
                base_dir=base_dir,
                depth=depth,
                force=force,
                max_file_size=max_file_size
                if max_file_size and max_file_size > 0
                else None,
                timeout_requests=timeout_requests,
                timeout_playwright=timeout_playwright,
                max_concurrent_requests=max_concurrent,
                executor=cli_executor,  # Pass the executor created above
                logger_override=logging.getLogger(
                    "mcp_doc_retriever.downloader.workflow"
                ),
            )
        )
        logger_cli.info(
            f"Download workflow for '{download_id}' completed successfully."
        )

    except (ValueError, RuntimeError, FileNotFoundError) as e:
        logger_cli.error(
            f"Download workflow failed: {e}", exc_info=log_level <= logging.DEBUG
        )
        raise typer.Exit(code=1)
    except Exception as e:
        logger_cli.error(
            f"Unexpected error during download workflow: {e}", exc_info=True
        )
        raise typer.Exit(code=1)
    # No finally needed here for executor, managed globally if script exits


# --- Add Search Command (Example Placeholder) ---
@app.command(
    "search", help="Search previously downloaded documentation (Not implemented yet)."
)
def search_command(
    download_id: str = typer.Argument(..., help="ID of the download batch to search."),
    query: List[str] = typer.Option(
        ..., "--query", "-q", help="Keywords or terms to search for."
    ),
    # Add other search options as needed
):
    """Placeholder for search functionality via CLI."""
    logger_cli.info(f"Search command called for ID: {download_id} with query: {query}")
    print(
        f"Search functionality for '{download_id}' with query '{query}' is not yet implemented in the CLI."
    )
    print("You can use the API endpoint POST /search.")
    # Example:
    # try:
    #     from .searcher.searcher import perform_search
    #     base_dir_path = Path(config.DOWNLOAD_BASE_DIR).resolve() # Get from config
    #     results = perform_search(
    #          download_id=download_id, scan_keywords=query, selector="p", base_dir=base_dir_path
    #     )
    #     print(f"Found {len(results)} basic results:")
    #     # Print results...
    # except ImportError:
    #     print("Search module not found.")
    # except Exception as e:
    #     print(f"Error during search: {e}")


# --- Main Execution Guard ---
# Includes graceful shutdown for the global executor
if __name__ == "__main__":
    try:
        app()
    finally:
        # Ensure executor is shut down when CLI finishes/exits
        logger_cli.debug("Shutting down CLI thread pool executor.")
        cli_executor.shutdown(wait=True)  # Wait for sync tasks
        logger_cli.debug("CLI executor shutdown complete.")
