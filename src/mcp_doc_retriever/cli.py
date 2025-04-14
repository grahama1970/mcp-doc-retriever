# File: src/mcp_doc_retriever/cli.py (Updated)

"""
Module: cli.py (Main Project CLI Entry Point)

Description:
Provides the main command-line interface for the MCP Document Retriever project
using Typer. It defines commands for downloading and potentially searching documentation.

Third-Party Documentation:
- Typer: https://typer.tiangolo.com/
- Loguru: https://loguru.readthedocs.io/

Sample Input/Output:
Input (Command Line):
  python -m mcp_doc_retriever download website https://example.com my_download_id
Output (Expected):
  - Log messages indicating download progress.
  - Downloaded files stored under './downloads/content/my_download_id/'.
  - Index file created at './downloads/index/my_download_id.jsonl'.
"""
import typer
import asyncio
import re
import os
import sys
import uuid
from pathlib import Path
from typing import Optional, List
from concurrent.futures import ThreadPoolExecutor
from loguru import logger

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

# --- Configure Loguru ---
# Remove default handler and add custom one
logger.remove()
logger.add(
    sys.stdout,
    format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level: <8}</level> | <cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - <level>{message}</level>",
    filter=lambda record: record["extra"].get("name") not in ["httpx", "websockets"],
    level="INFO"
)

# Create a named logger for CLI
logger_cli = logger.bind(name="cli")

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
    log_level = "DEBUG" if verbose else "INFO"
    logger.remove()  # Remove any existing handlers
    logger.add(
        sys.stdout,
        format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level: <8}</level> | <cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - <level>{message}</level>",
        filter=lambda record: record["extra"].get("name") not in ["httpx", "websockets"],
        level=log_level
    )
    logger_cli.info(f"Log level set to {log_level} for download '{download_id}'")
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
                logger_override=logger.bind(name="mcp_doc_retriever.downloader.workflow"),
            )
        )
        logger_cli.info(
            f"Download workflow for '{download_id}' completed successfully."
        )

    except (ValueError, RuntimeError, FileNotFoundError) as e:
        logger_cli.error(f"Download workflow failed: {e}")
        if logger.level("DEBUG").no >= logger.level(log_level).no:
            logger_cli.exception("Detailed error:")
        raise typer.Exit(code=1)
    except Exception as e:
        logger_cli.error(f"Unexpected error during download workflow: {e}")
        logger_cli.exception("Detailed error:")
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
if __name__ == "__main__" and len(sys.argv) == 1: # Only run example if no args passed
    # --- Standalone Usage Example ---
    # This block allows running `uv run -m mcp_doc_retriever.cli`
    # to verify basic download workflow orchestration without CLI arguments.
    # It replaces the direct call to app() for standalone testing.

    import tempfile
    import shutil
    from pathlib import Path
    import asyncio
    from concurrent.futures import ThreadPoolExecutor

    # Setup standalone logger using loguru
    standalone_logger = logger.bind(name="standalone_cli_test")

    standalone_logger.info("--- Running Standalone CLI Usage Example (Download Workflow) ---")

    # Define sample arguments for a simple download
    example_source_type = "website"
    example_url = "http://example.com" # Public, simple site
    example_download_id = "standalone_cli_example_com"
    # Create a temporary directory for this run
    temp_base_dir_obj = tempfile.TemporaryDirectory()
    example_base_dir = Path(temp_base_dir_obj.name)

    standalone_logger.info(f"Using temp base directory: {example_base_dir}")
    standalone_logger.info(f"Attempting download for URL: {example_url}")

    # Create a local thread pool executor for this standalone run
    example_executor = ThreadPoolExecutor(max_workers=2) # Small pool for example
    example_passed = False # Flag to track success

    try:
        # Call the core workflow function directly (mimicking the 'download' command)
        asyncio.run(
            fetch_documentation_workflow(
                source_type=example_source_type,
                download_id=example_download_id,
                repo_url=None,
                doc_path=None,
                url=example_url,
                base_dir=example_base_dir,
                depth=0, # Limit depth for example
                force=True,
                max_file_size=1*1024*1024, # 1MB limit
                timeout_requests=15, # Reasonable timeout
                timeout_playwright=30,
                max_concurrent_requests=3,
                executor=example_executor, # Pass the local executor
                logger_override=standalone_logger, # Pass the distinct logger
            )
        )
        example_passed = True # Mark as passed if no exception

    except Exception as e:
        standalone_logger.error(f"--- Standalone CLI Usage Example FAILED: {e} ---")
        standalone_logger.exception("Detailed error:")
        # example_passed remains False

    finally:
        # Clean up the local executor and temporary directory
        standalone_logger.debug("Shutting down example executor.")
        example_executor.shutdown(wait=True)
        try:
            temp_base_dir_obj.cleanup()
            standalone_logger.info(f"Cleaned up temp directory: {example_base_dir}")
        except Exception as cleanup_e:
            standalone_logger.error(f"Error cleaning up temp directory {example_base_dir}: {cleanup_e}")

        # Print final status
        print("\n------------------------------------")
        if example_passed:
            print("✓ Standalone CLI download example finished successfully (though internal errors may have occurred).")
        else:
            print("✗ Standalone CLI download example failed.")
        print("------------------------------------")

        # Exit with appropriate code for scripting
        sys.exit(0 if example_passed else 1)
