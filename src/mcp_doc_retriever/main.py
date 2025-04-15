"""
Module: main.py (FastAPI Server Entry Point)

Description:
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

Third-Party Documentation:
- FastAPI: https://fastapi.tiangolo.com/
- Uvicorn: https://www.uvicorn.org/
- sse-starlette: https://github.com/sysid/sse-starlette
- Loguru: https://loguru.readthedocs.io/

Sample Input/Output:

Input (Start Download):
  curl -X POST "http://127.0.0.1:8000/download" -H "Content-Type: application/json" -d '{"url": "https://example.com", "source_type": "website"}'
Output (Start Download):
  {"download_id": "some-uuid-string", "status": "pending", "message": "Download task accepted."}

Input (Check Status):
  curl "http://127.0.0.1:8000/status/some-uuid-string"
Output (Check Status):
  {"download_id": "some-uuid-string", "status": "completed", "start_time": "...", "end_time": "...", "total_files": 5, "error_message": null}

Input (Search):
  curl -X POST "http://127.0.0.1:8000/search" -H "Content-Type: application/json" -d '{"download_id": "some-uuid-string", "scan_keywords": ["fastapi", "example"], "selector": "p"}'
Output (Search):
  [{"original_url": "https://example.com/page1", "extracted_content": "...", ...}, ...]
"""
import asyncio
import json
import os
import re
import sys
import time
import traceback
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional
from pydantic import (
    BaseModel,
    Field,
    field_validator,
    AnyHttpUrl,
    model_validator,
    ConfigDict,
)
from typing import Literal, Any # Literal and Any are also needed

from fastapi import BackgroundTasks, FastAPI, HTTPException
from loguru import logger
from sse_starlette.sse import EventSourceResponse
import os
# Package imports
import mcp_doc_retriever.config as config
from mcp_doc_retriever.utils import is_url_private_or_internal
from mcp_doc_retriever.downloader.workflow import fetch_documentation_workflow
from mcp_doc_retriever.downloader.git_downloader import check_git_dependency
from mcp_doc_retriever.searcher.searcher import SearchRequest, SearchResultItem
from mcp_doc_retriever.searcher.searcher import perform_search



# --- Pydantic Models Moved from models.py ---

class DocDownloadRequest(BaseModel):
    """
    Defines the expected request body for an API endpoint that triggers a download.
    Validates conditional requirements based on source_type.
    """
    source_type: Literal["git", "website", "playwright"]
    # Git fields
    repo_url: Optional[AnyHttpUrl] = None
    doc_path: Optional[str] = None
    # Website/Playwright fields
    url: Optional[AnyHttpUrl] = None
    # Common fields
    download_id: Optional[str] = Field(
        None, description="Optional client-provided unique ID. If not provided, the server will generate one."
    )
    depth: Optional[int] = Field(None, ge=0, description="Crawling depth for website/playwright")
    force: Optional[bool] = Field(None, description="Overwrite existing download data")

    @model_validator(mode="after")
    def check_conditional_fields(self):
        st = self.source_type
        if st == "git":
            if self.url or self.depth is not None:
                raise ValueError("url and depth are not applicable when source_type is 'git'")
            if not self.repo_url:
                raise ValueError("repo_url is required when source_type is 'git'")
            if self.doc_path is None:
                self.doc_path = ""  # Default to empty string if not provided
        elif st in ("website", "playwright"):
            if self.repo_url or self.doc_path is not None:
                raise ValueError(
                    "repo_url and doc_path are not applicable when source_type is 'website' or 'playwright'"
                )
            if not self.url:
                raise ValueError(
                    "url is required when source_type is 'website' or 'playwright'"
                )
            if self.depth is None:
                self.depth = 5
        return self

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "summary": "Git Example",
                    "value": {
                        "source_type": "git",
                        "repo_url": "https://github.com/pydantic/pydantic",
                        "doc_path": "docs/",
                        "download_id": "pydantic_git_docs",
                    },
                },
                {
                    "summary": "Website Example",
                    "value": {
                        "source_type": "website",
                        "url": "https://docs.pydantic.dev/latest/",
                        "download_id": "pydantic_web_docs",
                        "depth": 2,
                        "force": False,
                    },
                },
                {
                    "summary": "Playwright Example",
                    "value": {
                        "source_type": "playwright",
                        "url": "https://playwright.dev/python/",
                        "download_id": "playwright_web_docs",
                        "depth": 0,
                    },
                },
            ]
        }
    )

class TaskStatus(BaseModel):
    """
    Response model for querying the status of a background download task via an API.
    """
    status: Literal["pending", "running", "completed", "failed"]
    message: Optional[str] = None
    start_time: Optional[datetime] = None
    end_time: Optional[datetime] = None
    error_details: Optional[str] = None  # Store traceback or error summary if failed

class SearchRequestBody(BaseModel):
    """
    Request body model specifically for the POST /search/{download_id} endpoint.
    Excludes download_id as it's provided in the path.
    """
    query: Optional[str] = None # Added to support simple query
    scan_keywords: Optional[List[str]] = None # Made optional
    extract_selector: Optional[str] = None # Made optional
    extract_keywords: Optional[List[str]] = None
    limit: Optional[int] = Field(10, gt=0)

    # Validator removed as field is optional
    # def check_selector_non_empty(cls, value):
    #     if not value or not value.strip():
    #         raise ValueError("extract_selector cannot be empty")
    #     return value

# --- End Pydantic Models Moved from models.py ---


# Configure Loguru with main handler and filter external modules
logger.configure(handlers=[{
    "sink": sys.stdout,
    "filter": lambda record: record["extra"].get("name") not in ["httpx", "websockets"]
}])
logger.info("Root logger configured.")


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
# Logger for this module
logger = logger.bind(module="main")


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
            logger_override=logger.bind(workflow=download_id),
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

    # Generate download_id if not provided
    if request.download_id is None:
        safe_download_id = f"dl_{uuid.uuid4().hex[:8]}"
        logger.info(f"Generated download_id: {safe_download_id}")
    else:
        # Sanitize if provided
        safe_download_id = re.sub(r"[^\w\-\_]+", "_", request.download_id)
        if safe_download_id != request.download_id:
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


async def _perform_search(
    download_id: str,
    scan_keywords: List[str],
    extract_selector: str,
    extract_keywords: Optional[List[str]] = None
) -> List[SearchResultItem]:
    """Shared search logic used by both GET and POST endpoints."""
    task_info = DOWNLOAD_TASKS.get(download_id)
    if task_info is None:
        raise HTTPException(
            status_code=404, detail=f"ID '{download_id}' not found."
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

        search_request = SearchRequest(
            download_id=download_id,
            scan_keywords=scan_keywords,
            extract_selector=extract_selector,
            extract_keywords=extract_keywords or [],
            limit=100  # Default limit
        )
        results = perform_search(search_request, base_dir_path)
        logger.info(f"Search for '{download_id}' yielded {len(results)} results.")
        return results
    except FileNotFoundError as e:
        logger.error(
            f"Search failed for '{download_id}': File not found. {e}",
            exc_info=True,
        )
        # Updated detail message for clarity when index is missing
        raise HTTPException(
            status_code=404,
            detail=f"Index file not found for download ID '{download_id}'.",
        )
    except Exception as e:
        logger.error(f"Search failed for '{download_id}': {e}", exc_info=True)
        raise HTTPException(
            status_code=500, detail=f"Internal error during search: {type(e).__name__}"
        )

@app.post("/search", response_model=List[SearchResultItem])
async def search_docs(request: SearchRequest):
    """Searches downloaded content for a completed download ID (POST with JSON body)."""
    logger.info(f"POST search request for: {request.download_id}")
    return await _perform_search(
        download_id=request.download_id,
        scan_keywords=request.scan_keywords,
        extract_selector=request.extract_selector,
        extract_keywords=request.extract_keywords
    )

@app.post("/search/{download_id}", response_model=List[SearchResultItem])
async def search_docs_get(
    download_id: str, # download_id comes from the path
    request_body: SearchRequestBody # Use the new model for the request body
):
    """Searches downloaded content for a completed download ID (POST with JSON body)."""
    logger.info(f"POST search request for: {download_id}")
    
    # Determine search parameters based on request body content
    scan_kw: Optional[List[str]] = None
    selector: Optional[str] = None

    if request_body.query:
        logger.info(f"Using simple query parameter: '{request_body.query}'")
        scan_kw = [request_body.query]
        selector = "body" # Default selector for simple query
    elif request_body.scan_keywords and request_body.extract_selector:
        logger.info("Using scan_keywords and extract_selector parameters.")
        scan_kw = request_body.scan_keywords
        selector = request_body.extract_selector
    else:
        # Neither simple query nor specific fields provided correctly
        raise HTTPException(
            status_code=422,
            detail="Request must contain either a 'query' field or both 'scan_keywords' and 'extract_selector'."
        )

    # Call the internal search function with the determined parameters
    return await _perform_search(
        download_id=download_id,
        scan_keywords=scan_kw,
        extract_selector=selector,
        extract_keywords=request_body.extract_keywords # Pass this through if provided
    )


@app.get("/health")
async def health():
    """Basic health check endpoint."""
    logger.debug("Health check requested.")
    return {"status": "healthy", "timestamp": datetime.now().isoformat()}


# --- Cleanup Hook ---
@app.on_event("shutdown") # Use older decorator for compatibility
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
    import uuid
    import shutil
    # Using imports from above

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
                # Prepare and execute searches
                search_request_title = SearchRequest(
                    download_id=test_id,
                    scan_keywords=["H1"],
                    extract_selector="title"
                )
                
                search_request_p = SearchRequest(
                    download_id=test_id,
                    scan_keywords=["paragraph"],
                    extract_selector="p"
                )
                
                results = perform_search(search_request_title, test_dir)
                print(f"Found {len(results)} title results.")
                
                results_p = perform_search(search_request_p, test_dir)
                print(f"Found {len(results_p)} paragraph results.")

                # Restore working directory
                os.chdir(Path(test_dir).parent)
            except Exception as e:
                print(f"Search FAILED: {e}")
                # If search fails, the overall example might be considered failed
                # We'll rely on the dl_ok flag primarily for the success message
        example_executor.shutdown(wait=True)
        print("\nStandalone example finished.")

        # Print success/failure based on download status
        print("\n------------------------------------")
        if dl_ok:
             print("✓ Standalone main.py example finished successfully (download part).")
        else:
             print("✗ Standalone main.py example failed (download part).")
        print("------------------------------------")

    # Logging is already configured via config.py
    asyncio.run(run_example())


if __name__ == "__main__":
    print(
        """
NOTE: Run as FastAPI server: uvicorn src.mcp_doc_retriever.main:app --reload --port 8000 --host 0.0.0.0
Running directly executes usage_example.""",
        file=sys.stderr,
    )
    usage_example()
