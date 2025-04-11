"""
MCP Document Retriever FastAPI server.

- Provides `/download` endpoint to start recursive downloads (as background tasks).
- Provides `/status/{download_id}` endpoint to check download progress/status.
- Provides `/search` endpoint to search downloaded content.
- Provides `/` endpoint for SSE connection (MCP protocol).
- Provides `/health` endpoint for basic health checks.

Uses an in-memory dictionary (`DOWNLOAD_TASKS`) to track the status of
background download tasks. This state is lost on server restart. For persistent
task tracking, consider integrating Redis, a database, or file-based storage.

Handles basic URL validation and SSRF protection on the `/download` endpoint.
Delegates actual download logic to `downloader.start_recursive_download` and
search logic to `searcher.perform_search`.
"""

import logging
import os
import re
import sys
from sse_starlette.sse import EventSourceResponse
import asyncio
import json
import time
import traceback
import uuid
from datetime import datetime
from typing import Dict, List
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor  # Import executor
from fastapi import BackgroundTasks, FastAPI, HTTPException

# Assuming the new structure with sub-packages:
from . import config
from .utils import is_url_private_or_internal
from .downloader.workflow import fetch_documentation_workflow
from .downloader.git_downloader import check_git_dependency
from .models import DocDownloadRequest, TaskStatus, SearchRequest, SearchResultItem

# Updated import path for searcher
from .searcher.searcher import perform_search  # Import from searcher.searcher



# --- Logger Setup ---
log_format = (
    "%(asctime)s - %(levelname)s - [%(name)s:%(funcName)s:%(lineno)d] - %(message)s"
)
logging.basicConfig(
    level=logging.INFO, format=log_format, stream=sys.stdout, force=True
)
logger_setup = logging.getLogger("main_setup")
logger_setup.info("Root logger configured.")
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("websockets").setLevel(logging.WARNING)
# --------------------


# --- Global State & Resources ---
DOWNLOAD_TASKS: Dict[str, TaskStatus] = {}

# *** Create ONE shared ThreadPoolExecutor for the entire application lifetime ***
shared_executor = ThreadPoolExecutor(
    max_workers=os.cpu_count(), thread_name_prefix="FastAPI_SyncWorker"
)
# -----------------------------

# --- FastAPI App Initialization ---
app = FastAPI(
    title="MCP Document Retriever",
    description="API for downloading and searching documentation.",
    version="1.0.0",
)
logger = logging.getLogger(__name__)  # Logger for API endpoints


# --- Background Task Wrapper ---
async def run_download_workflow_task(
    download_id: str,
    request_data: DocDownloadRequest,
    # No need to pass executor explicitly if using the global `shared_executor`
):
    """Wrapper to run the download workflow and update task status."""
    task_info = DOWNLOAD_TASKS.get(download_id)
    if not task_info:
        logger.error(
            f"Task {download_id} not found in store at start of background task."
        )
        return

    task_info.status = "running"
    task_info.message = "Download workflow starting..."
    logger.info(
        f"Background task started for download_id: {download_id} ({request_data.source_type})"
    )

    try:
        base_dir_path = Path(config.DOWNLOAD_BASE_DIR).resolve()
        req_timeout = config.TIMEOUT_REQUESTS
        play_timeout = config.TIMEOUT_PLAYWRIGHT

        # *** Pass the global shared_executor to the workflow ***
        await fetch_documentation_workflow(
            source_type=request_data.source_type,
            download_id=download_id,
            repo_url=str(request_data.repo_url) if request_data.repo_url else None,
            doc_path=request_data.doc_path,
            url=str(request_data.url) if request_data.url else None,
            base_dir=base_dir_path,
            depth=request_data.depth if request_data.depth is not None else 5,
            force=request_data.force or False,
            max_file_size=None,  # Consider adding to API request model if needed
            timeout_requests=req_timeout,
            timeout_playwright=play_timeout,
            max_concurrent_requests=50,  # Make configurable?
            executor=shared_executor,  # Pass the shared executor instance
            logger_override=logging.getLogger("mcp_doc_retriever.downloader.workflow"),
        )

        task_info.status = "completed"
        task_info.message = "Download workflow finished successfully."
        task_info.end_time = datetime.now()
        logger.info(
            f"Background task completed successfully for download_id: {download_id}"
        )

    except Exception as e:
        tb_str = traceback.format_exc()
        error_msg = f"{type(e).__name__}: {e}"
        logger.error(
            f"Background task failed for download_id: {download_id}. Error: {error_msg}\nTraceback: {tb_str}"
        )
        task_info.status = "failed"
        task_info.message = f"Download failed: {error_msg}"
        task_info.error_details = f"{error_msg}\n{tb_str[:2000]}"
        task_info.end_time = datetime.now()


# --- API Endpoints (SSE, Download, Status, Search, Health - Largely unchanged internally, except download call) ---


@app.get("/")
async def mcp_sse():
    """SSE endpoint for MCP protocol connection (placeholder)."""

    async def event_generator():
        yield {
            "event": "connected",
            "data": json.dumps(
                {
                    "service": "DocRetriever",
                    "version": app.version,
                    "capabilities": [
                        "document_download",
                        "document_search",
                        "task_status",
                    ],
                }
            ),
        }
        while True:
            await asyncio.sleep(15)
            yield {"event": "heartbeat", "data": json.dumps({"timestamp": time.time()})}

    return EventSourceResponse(event_generator())


@app.post("/download", response_model=TaskStatus, status_code=202)
async def download_docs(request: DocDownloadRequest, background_tasks: BackgroundTasks):
    """Accepts download request, validates, schedules background task, returns initial status."""
    logger.info(
        f"Received download request: {request.model_dump_json(exclude_none=True)}"
    )
    # --- Validation ---
    target_location = None
    if request.source_type == "git":
        if not check_git_dependency():
            raise HTTPException(status_code=503, detail="Git command not found.")
        target_location = str(request.repo_url)
    elif request.source_type in ["website", "playwright"]:
        target_location = str(request.url)
        if is_url_private_or_internal(target_location):
            raise HTTPException(status_code=400, detail="Blocked potential SSRF URL.")
    else:
        raise HTTPException(status_code=400, detail="Invalid source_type.")

    safe_download_id = re.sub(r"[^\w\-\_]+", "_", request.download_id)
    if not safe_download_id or len(safe_download_id) < 3:
        safe_download_id = f"dl_{uuid.uuid4().hex[:8]}"
        logger.warning(f"Using generated ID: {safe_download_id}")
    elif safe_download_id != request.download_id:
        logger.warning(f"Sanitized ID '{request.download_id}' to '{safe_download_id}'")

    if (
        safe_download_id in DOWNLOAD_TASKS
        and DOWNLOAD_TASKS[safe_download_id].status == "running"
    ):
        raise HTTPException(
            status_code=409, detail=f"Task '{safe_download_id}' already running."
        )

    # --- Add task to store ---
    initial_task_status = TaskStatus(
        status="pending",
        start_time=datetime.now(),
        message=f"Download task for {target_location} queued.",
    )
    DOWNLOAD_TASKS[safe_download_id] = initial_task_status
    logger.info(f"Task {safe_download_id} created and queued for {target_location}")

    # --- Schedule background task ---
    background_tasks.add_task(
        run_download_workflow_task, download_id=safe_download_id, request_data=request
    )
    logger.debug(f"Background task added for {safe_download_id}")
    return initial_task_status


@app.get("/status/{download_id}", response_model=TaskStatus)
async def get_status(download_id: str):
    """Retrieves the current status of a specific download task."""
    logger.debug(f"Status query for: {download_id}")
    task_info = DOWNLOAD_TASKS.get(download_id)
    if task_info is None:
        raise HTTPException(status_code=404, detail="Download ID not found")
    return task_info


@app.post("/search", response_model=List[SearchResultItem])
async def search_docs(request: SearchRequest):
    """Searches downloaded content for a completed download ID."""
    logger.info(f"Search request for: {request.download_id}")
    task_info = DOWNLOAD_TASKS.get(request.download_id)
    if task_info is None:
        raise HTTPException(
            status_code=404, detail=f"ID '{request.download_id}' not found."
        )
    if task_info.status != "completed":
        raise HTTPException(
            status_code=409,
            detail=f"Task status is '{task_info.status}', requires 'completed'.",
        )

    try:
        base_dir_path = Path(config.DOWNLOAD_BASE_DIR).resolve()
        if not base_dir_path.is_dir():
            raise HTTPException(
                status_code=500,
                detail="Server config error: Base download directory not found.",
            )

        # *** Use updated import path for perform_search ***
        results = perform_search(
            download_id=request.download_id,
            scan_keywords=request.scan_keywords,
            selector=request.extract_selector,
            extract_keywords=request.extract_keywords,
            base_dir=base_dir_path,
        )
        logger.info(
            f"Search for '{request.download_id}' yielded {len(results)} results."
        )
        return results
    except FileNotFoundError as e:
        logger.error(
            f"Search failed for '{request.download_id}': File not found. {e}",
            exc_info=True,
        )
        raise HTTPException(
            status_code=404,
            detail=f"Search data incomplete/missing for ID '{request.download_id}'.",
        )
    except Exception as e:
        logger.error(f"Search failed for '{request.download_id}': {e}", exc_info=True)
        raise HTTPException(
            status_code=500, detail=f"Internal error during search: {type(e).__name__}"
        )


@app.get("/health")
async def health():
    """Basic health check endpoint."""
    logger.debug("Health check requested.")
    return {"status": "healthy", "timestamp": datetime.now().isoformat()}


# --- Cleanup Hook ---
@app.lifespan("shutdown")
async def app_shutdown():
    """Gracefully shutdown the shared thread pool executor."""
    logger.info("Application shutting down. Closing thread pool executor.")
    shared_executor.shutdown(wait=True)
    logger.info("Executor shutdown complete.")


# --- Standalone Example Usage ---
def usage_example():
    """Demonstrates programmatic usage (runs workflow directly)."""
    # ... (usage_example code remains largely the same, but ensure it creates its own executor
    #      or potentially uses the global one for the example run, managing its lifecycle) ...
    import asyncio
    import uuid
    import shutil
    from mcp_doc_retriever.downloader.workflow import fetch_documentation_workflow
    from mcp_doc_retriever.searcher.searcher import perform_search
    from mcp_doc_retriever import config

    example_executor = ThreadPoolExecutor(
        max_workers=2, thread_name_prefix="ExampleWorker"
    )

    async def run_example():
        print("\n=== Starting Standalone Example ===")
        test_url = "https://httpbin.org/html"
        test_id = f"standalone_{uuid.uuid4().hex[:8]}"
        test_dir = Path("./downloads_standalone_test").resolve()
        print(f"ID: {test_id}, Base Dir: {test_dir}")
        if test_dir.exists():
            shutil.rmtree(test_dir)
            test_dir.mkdir(parents=True)
        print(f"\nRunning download for {test_url}...")
        dl_ok = False
        try:
            await fetch_documentation_workflow(
                source_type="website",
                download_id=test_id,
                url=test_url,
                base_dir=test_dir,
                depth=0,
                force=True,
                executor=example_executor,
                timeout_requests=config.TIMEOUT_REQUESTS,
                timeout_playwright=config.TIMEOUT_PLAYWRIGHT,
            )
            print("Download workflow OK.")
            dl_ok = True
        except Exception as e:
            print(f"Download FAILED: {e}")
            logger.error("Standalone failed", exc_info=True)
        if dl_ok:
            idx_path = test_dir / "index" / f"{test_id}.jsonl"
            print(f"\nIndex created: {idx_path.is_file()}")
            print("\nTesting Search...")
            try:
                results = perform_search(test_id, ["H1"], "title", None, test_dir)
                print(f"Found {len(results)} title results.")
                results_p = perform_search(test_id, ["paragraph"], "p", None, test_dir)
                print(f"Found {len(results_p)} paragraph results.")
            except Exception as e:
                print(f"Search FAILED: {e}")
        example_executor.shutdown(wait=True)
        print("\nStandalone example finished.")

    logging.basicConfig(
        level=logging.INFO, format=log_format, stream=sys.stdout, force=True
    )
    asyncio.run(run_example())


if __name__ == "__main__":
    print(
        """
NOTE: Run as FastAPI server: uvicorn src.mcp_doc_retriever.main:app --reload --port 8000 --host 0.0.0.0
Running directly executes usage_example.""",
        file=sys.stderr,
    )
    usage_example()