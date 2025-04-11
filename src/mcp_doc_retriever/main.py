"""
MCP Document Retriever FastAPI server.

- Provides `/download` endpoint to start recursive downloads.
- Provides `/status/{download_id}` endpoint to check download progress. # ADDED
- Provides `/search` endpoint to search downloaded content.
- Health check at `/health`.

... (rest of docstring) ...
"""

import logging
import os
import sys
from sse_starlette.sse import EventSourceResponse
import asyncio
import json

# --- Force Root Logger Configuration ---
# Attempt to configure logging ASAP, before FastAPI/Uvicorn might interfere
log_format = "%(asctime)s - %(levelname)s - [%(name)s:%(funcName)s:%(lineno)d] - %(message)s"
logging.basicConfig(level=logging.DEBUG, format=log_format, stream=sys.stdout, force=True)
logger_setup = logging.getLogger(__name__) # Use a distinct name here maybe?
logger_setup.info("Root logger configured forcefully.") # Log confirmation
# ---------------------------------------

import time
import traceback # Added traceback
import uuid
from datetime import datetime # Added datetime
from typing import Dict, List # Added Dict
from urllib.parse import urlparse, urlunparse

from fastapi import BackgroundTasks, FastAPI, HTTPException

from . import config # Import config for base_dir
from .utils import is_url_private_or_internal
from .downloader import start_recursive_download
from .models import ( # Import new TaskStatus
    DownloadRequest,
    DownloadStatus,
    SearchRequest,
    SearchResultItem,
    TaskStatus,
)
from .searcher import perform_search


# --- Global State for Task Status ---
# Simple in-memory store. Will be lost on server restart.
# For persistence, use Redis, a database, or file-based storage.
DOWNLOAD_TASKS: Dict[str, TaskStatus] = {}

app = FastAPI(title="MCP Document Retriever")
logger = logging.getLogger(__name__) # Use logger

@app.get("/")
async def mcp_sse():
    """SSE endpoint for MCP protocol connection"""
    async def event_generator():
        # Send initial connection confirmation
        yield {
            "event": "connected",
            "data": json.dumps({
                "service": "DocRetriever",
                "version": "1.0",
                "capabilities": ["document_download", "document_search"]
            })
        }
        
        # Send periodic heartbeats
        while True:
            await asyncio.sleep(15)
            yield {"event": "heartbeat", "data": json.dumps({"status": "active"})}
    
    return EventSourceResponse(event_generator())

# --- Background Task Wrapper ---

async def run_download_task(
    download_id: str,
    start_url: str,
    depth: int,
    force: bool,
    base_dir: str,
    use_playwright: bool,
    timeout_requests: int,
    timeout_playwright: int,
    max_file_size: int | None,
):
    """Wrapper to run the download and update status."""
    task_info = DOWNLOAD_TASKS.get(download_id)
    if not task_info:
        logger.error(f"Task {download_id} not found in store at start of background task.")
        # Should not happen if added correctly in /download endpoint
        return

    # Update status to running
    task_info.status = "running"
    task_info.message = "Download process starting..."
    DOWNLOAD_TASKS[download_id] = task_info # Update store

    logger.info(f"Background task started for download_id: {download_id}")

    try:
        # --- Execute the actual download ---
        # Assuming TIMEOUT_PLAYWRIGHT is defined in config or has a default
        playwright_timeout = timeout_playwright if timeout_playwright is not None else getattr(config, 'TIMEOUT_PLAYWRIGHT', 300)

        await start_recursive_download(
            start_url=start_url,
            depth=depth,
            force=force,
            download_id=download_id,
            base_dir=base_dir,
            use_playwright=use_playwright,
            timeout_requests=timeout_requests,
            timeout_playwright=playwright_timeout, # Use resolved timeout
            max_file_size=max_file_size,
        )
        # --- Update status on successful completion ---
        task_info.status = "completed"
        task_info.message = "Download finished successfully."
        task_info.end_time = datetime.now()
        logger.info(f"Background task completed successfully for download_id: {download_id}")

    except Exception as e:
        # --- Update status on failure ---
        tb_str = traceback.format_exc()
        logger.error(f"Background task failed for download_id: {download_id}. Error: {e}\nTraceback: {tb_str}")
        task_info.status = "failed"
        task_info.message = f"Download failed: {type(e).__name__}"
        task_info.error_details = f"{str(e)}\n{tb_str}" # Store detailed error
        task_info.end_time = datetime.now()

    finally:
        # Ensure the final status is saved back to the store
        DOWNLOAD_TASKS[download_id] = task_info


# --- API Endpoints ---

@app.post("/download", response_model=DownloadStatus)
async def download(request: DownloadRequest, background_tasks: BackgroundTasks):
    logger.info(f"Received download request: {request.url}, depth={request.depth}")
    # Validate URL
    try:
        parsed = urlparse(request.url)
        if not parsed.scheme or not parsed.netloc:
            # Try to add scheme if missing
            temp_url = "http://" + request.url if not request.url.startswith(("http://", "https://")) else request.url
            parsed = urlparse(temp_url)
            if not parsed.scheme or not parsed.netloc:
                raise ValueError("Invalid URL format after attempting to add scheme.")
        # Canonicalize URL (ensure scheme, normalize path)
        canonical_url = urlunparse(parsed._replace(path=parsed.path or "/", query="", fragment=""))
        # SSRF protection: block internal/private URLs
        if is_url_private_or_internal(canonical_url):
            logger.warning(f"Blocked SSRF-prone/internal URL: {canonical_url}")
            return DownloadStatus(
                status="failed_validation",
                message="Blocked download: URL resolves to an internal/private address or forbidden hostname (potential SSRF).",
                download_id=None
            )
    except Exception as e:
        logger.warning(f"Invalid URL received: {request.url} - Error: {e}")
        # Return failure status directly without starting task
        return DownloadStatus(
            status="failed_validation",
            message=f"Invalid URL format: {e}",
            download_id=None
        )

    # Use default depth=1 if not provided or invalid (already handled by Pydantic ge=0)
    depth = request.depth

    # Generate unique download_id
    download_id = str(uuid.uuid4())

    # --- Add task to global store with 'pending' status ---
    DOWNLOAD_TASKS[download_id] = TaskStatus(
        status="pending",
        start_time=datetime.now(),
        message="Download task queued."
    )
    logger.info(f"Download task {download_id} created for URL: {canonical_url}")

    # Get base directory from config (or default)
    base_dir = config.DOWNLOAD_BASE_DIR
    # Resolve timeouts, using config as fallback
    req_timeout = request.timeout if request.timeout is not None else config.TIMEOUT_REQUESTS
    # Assuming TIMEOUT_PLAYWRIGHT exists in config or provide a default
    playwright_timeout = request.timeout if request.timeout is not None else getattr(config, 'TIMEOUT_PLAYWRIGHT', 300)


    # --- Schedule the background task ---
    logger.debug(f"Adding background task for download ID: {download_id}, URL: {canonical_url}") # ADDED FOR DEBUG
    background_tasks.add_task(
        run_download_task, # Use the wrapper function
        download_id=download_id,
        start_url=canonical_url,
        depth=depth,
        force=request.force,
        base_dir=base_dir,
        use_playwright=request.use_playwright or False,
        timeout_requests=req_timeout, # Use resolved timeout
        timeout_playwright=playwright_timeout, # Use resolved timeout
        max_file_size=request.max_file_size # Pass max_size alias value
    )
    logger.debug(f"Background task added for download ID: {download_id}") # ADDED FOR DEBUG

    # Return 'started' status and the ID
    return DownloadStatus(
        status="started",
        message=f"Download initiated for {canonical_url}",
        download_id=download_id,
    )


# --- NEW Status Endpoint ---
@app.get("/status/{download_id}", response_model=TaskStatus)
async def get_status(download_id: str):
    """Check the status of a download task."""
    logger.debug(f"Status query received for download_id: {download_id}")
    task_info = DOWNLOAD_TASKS.get(download_id)
    if task_info is None:
        logger.warning(f"Status query for unknown download_id: {download_id}")
        raise HTTPException(status_code=404, detail="Download ID not found")
    logger.debug(f"Returning status for {download_id}: {task_info.status}")
    return task_info


# --- Existing Search and Health Endpoints ---

@app.post("/search", response_model=List[SearchResultItem])
async def search(request: SearchRequest):
    logger.info(f"Search request received for download_id: {request.download_id}")
    # Check task status first (optional but good practice)
    task_info = DOWNLOAD_TASKS.get(request.download_id)
    if task_info and task_info.status not in ["completed"]:
         logger.warning(f"Search attempted on non-completed task {request.download_id} (status: {task_info.status})")
         # Decide: Allow search anyway, or raise error? Let's allow, but log.
         # Or raise: raise HTTPException(status_code=409, detail=f"Download task status is '{task_info.status}', not 'completed'.")

    # Get base directory from config
    base_dir = config.DOWNLOAD_BASE_DIR

    # Construct index path using the determined base directory and the raw download_id
    # Note: searcher also calculates this, maybe pass base_dir to searcher?
    index_path = os.path.join(base_dir, "index", f"{request.download_id}.jsonl")

    if not os.path.exists(index_path):
         # Check if the task failed, providing a more informative error
        if task_info and task_info.status == "failed":
             raise HTTPException(status_code=404, detail=f"Index file not found. Task failed: {task_info.message}")
        # Check if task is still pending/running
        elif task_info and task_info.status in ["pending", "running"]:
             raise HTTPException(status_code=404, detail=f"Index file not found. Task status is '{task_info.status}'.")
        # Otherwise, generic not found
        else:
             logger.warning(f"Index file not found for search: {index_path} (Task ID: {request.download_id})")
             raise HTTPException(status_code=404, detail="Index file not found for the given download ID.")

    try:
        results = perform_search(
            download_id=request.download_id,
            scan_keywords=request.scan_keywords,
            selector=request.extract_selector,
            extract_keywords=request.extract_keywords,
            base_dir=base_dir # Pass base_dir for consistency
        )
        logger.info(f"Search for {request.download_id} yielded {len(results)} results.")
        return results
    except FileNotFoundError: # Should be caught above, but defense in depth
         logger.error(f"Search failed because index file disappeared: {index_path}")
         raise HTTPException(status_code=404, detail="Index file not found during search execution.")
    except Exception as e:
         logger.error(f"Search failed for download_id {request.download_id}: {e}", exc_info=True)
         raise HTTPException(status_code=500, detail=f"An internal error occurred during search: {e}")


@app.get("/health")
async def health():
    """Health check endpoint"""
    return {"status": "healthy"}


# --- Existing Usage Example ---
def usage_example():
    """
    Demonstrates programmatic usage of the main module.

    Note: Normally this module runs as a FastAPI server. This example shows
    direct usage of the underlying functionality. Includes status check.
    """
    import asyncio
    import uuid
    # Re-import necessary components for standalone run
    from mcp_doc_retriever.downloader import start_recursive_download
    from mcp_doc_retriever.searcher import perform_search
    # from mcp_doc_retriever import config # Import config for base_dir # Already imported

    async def run_example():
        print("\n=== Starting Download Test ===")
        test_url = "https://example.com"
        test_download_id = str(uuid.uuid4())
        test_base_dir = "./downloads_test_polling" # Use separate dir

        # --- Simulate API call ---
        print(f"Simulating download request for {test_url}...")
        # 1. Add initial status
        DOWNLOAD_TASKS[test_download_id] = TaskStatus(
            status="pending", start_time=datetime.now()
        )
        # 2. Run the task (using wrapper logic conceptually)
        print(f"Running download task for ID: {test_download_id}")
        await run_download_task( # Manually call the wrapper for the example
             download_id=test_download_id,
             start_url=test_url,
             depth=0,
             force=True,
             base_dir=test_base_dir, # Use test dir
             use_playwright=False,
             timeout_requests=30,
             timeout_playwright=60, # Assuming default
             max_file_size=None,
        )

        print("\n=== Checking Task Status ===")
        status = DOWNLOAD_TASKS.get(test_download_id)
        if status:
             print(f"Status: {status.status}")
             print(f"Message: {status.message}")
             if status.error_details:
                 print(f"Error: {status.error_details[:100]}...") # Print snippet
        else:
             print("Error: Task status not found in store!")

        print("\n=== Verifying Download (if completed) ===")
        if status and status.status == "completed":
            index_path = os.path.join(test_base_dir, "index", f"{test_download_id}.jsonl")
            if os.path.exists(index_path):
                print(f"Success: Index file created at {index_path}")
                print("\n=== Testing Search ===")
                try:
                    results = perform_search(
                        test_download_id,
                        scan_keywords=["Example"],
                        selector="title",
                        extract_keywords=None,
                        base_dir=test_base_dir # Pass test dir
                    )
                    print(f"Found {len(results)} search results")
                    for result in results:
                        print(f"- {result.original_url}: {result.extracted_content}")
                except Exception as e:
                    print(f"Search failed: {e}")
            else:
                print(f"Error: Index file not found at {index_path} despite 'completed' status.")
        else:
            print("Skipping file verification and search as task did not complete successfully.")

    # --- Setup Logging for Example ---
    log_level = logging.INFO
    log_format = "%(asctime)s - %(levelname)s - [%(name)s:%(funcName)s:%(lineno)d] - %(message)s"
    logging.basicConfig(level=log_level, format=log_format)
    # --- Run Example ---
    asyncio.run(run_example())


if __name__ == "__main__":
    print("""
Note: This module is primarily designed to run as a FastAPI server.
The usage example demonstrates direct functionality but may have import limitations.

To run properly:
1. Install the package: pip install -e .
2. Run the FastAPI server: uvicorn src.mcp_doc_retriever.main:app --reload --port 8000
""") # Changed port to 8000 to match Dockerfile CMD
    usage_example()
