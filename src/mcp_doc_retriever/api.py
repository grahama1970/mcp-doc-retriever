# src/mcp_doc_retriever/api.py
"""API Endpoints for the MCP Document Retriever using FastAPI Router."""

import asyncio
import json
import re
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, BackgroundTasks, HTTPException
from loguru import logger
from sse_starlette.sse import EventSourceResponse

# Import models from the new models file
from .models import (
    DocDownloadRequest,
    TaskStatus,
    SearchRequestBody,
    SearchRequest,
    SearchResultItem,
)

# Import necessary components from main or other modules
# Use absolute imports
from mcp_doc_retriever import config
from mcp_doc_retriever.core import ( # Import shared components from core.py
    get_task_status_from_db,
    add_task_status,
    # update_task_status is used internally by run_download_workflow_task in core.py
    run_download_workflow_task,
    shared_executor,
    # db_connection is managed within core.py
)
# Import other necessary modules using absolute paths
from mcp_doc_retriever.utils import is_url_private_or_internal
from mcp_doc_retriever.downloader.git_downloader import check_git_dependency
from mcp_doc_retriever.searcher.searcher import perform_search

# Create router instance
router = APIRouter()
logger = logger.bind(module="api")  # Bind logger to this module


# --- Search Helper ---
async def _perform_search(
    download_id: str,
    scan_keywords: Optional[List[str]],
    extract_selector: Optional[str],
    extract_keywords: Optional[List[str]] = None,
    limit: int = 10,
) -> List[SearchResultItem]:
    """Shared search logic used by endpoints, checking DB status."""
    task_info = await get_task_status_from_db(download_id)
    if task_info is None:
        raise HTTPException(
            status_code=404, detail=f"Download ID '{download_id}' not found."
        )
    if task_info.status != "completed":
        raise HTTPException(
            status_code=409,
            detail=f"Task status is '{task_info.status}', requires 'completed'.",
        )

    try:
        base_dir_path = Path(config.DOWNLOAD_BASE_DIR).resolve()
        if not base_dir_path.is_dir():
            logger.error(f"Base download directory not found: {base_dir_path}")
            raise HTTPException(
                status_code=500,
                detail="Server configuration error: Base download directory not found.",
            )

        # Construct SearchRequest carefully, handling optional fields
        search_request = SearchRequest(
            download_id=download_id,
            scan_keywords=scan_keywords if scan_keywords else [],
            extract_selector=extract_selector if extract_selector else "body",
            extract_keywords=extract_keywords if extract_keywords else [],
            limit=limit,
        )

        # Assuming perform_search is synchronous and needs to run in executor
        loop = asyncio.get_running_loop()
        results = await loop.run_in_executor(
            shared_executor,  # Use the shared executor from main.py
            perform_search,  # The function to run
            search_request,  # Arguments for perform_search
            base_dir_path,
        )

        logger.info(f"Search for '{download_id}' yielded {len(results)} results.")
        return results
    except FileNotFoundError as e:
        logger.error(
            f"Search failed for '{download_id}': Index file likely missing. {e}",
            exc_info=True,
        )
        raise HTTPException(
            status_code=404,
            detail=f"Index file not found for download ID '{download_id}'. Ensure download completed successfully.",
        )
    except Exception as e:
        logger.error(f"Search failed for '{download_id}': {e}", exc_info=True)
        raise HTTPException(
            status_code=500, detail=f"Internal error during search: {type(e).__name__}"
        )


# --- API Endpoints ---


@router.get("/")
async def mcp_sse():
    """SSE endpoint for MCP protocol connection (placeholder)."""

    async def event_generator():
        yield {
            "event": "connected",
            "data": json.dumps(
                {
                    "service": "DocRetriever",
                    # Version info is part of the main app instance, not needed here directly
                    # If needed, it could be passed via dependency injection or config
                    "version": "1.0.0", # Placeholder or fetch from config if needed
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


@router.post("/download", response_model=TaskStatus, status_code=202)
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
        # Should be caught by Pydantic, but as a safeguard
        raise HTTPException(status_code=400, detail="Invalid source_type.")

    # Generate download_id if not provided
    if request.download_id is None:
        safe_download_id = f"dl_{uuid.uuid4().hex[:8]}"
        logger.info(f"Generated download_id: {safe_download_id}")
    else:
        # Sanitize if provided
        safe_download_id = re.sub(r"[^\w\-\_]+", "_", request.download_id)
        if safe_download_id != request.download_id:
            logger.warning(
                f"Sanitized ID '{request.download_id}' to '{safe_download_id}'"
            )

    # Check if task already exists and is running in DB
    existing_task = await get_task_status_from_db(safe_download_id)
    if existing_task and existing_task.status == "running":
        raise HTTPException(
            status_code=409, detail=f"Task '{safe_download_id}' already running."
        )

    # --- Add task to DB store ---
    initial_task_status = TaskStatus(
        download_id=safe_download_id,
        status="pending",
        start_time=datetime.now(timezone.utc),
        message=f"Download task for {target_location} queued.",
    )
    try:
        await add_task_status(safe_download_id, initial_task_status)
        logger.info(f"Task {safe_download_id} created in DB for {target_location}")
    except Exception as e:
        logger.error(
            f"Failed to add initial task status to DB for {safe_download_id}: {e}"
        )
        raise HTTPException(
            status_code=500, detail="Failed to record task status in database."
        )

    # --- Schedule background task (using the imported function) ---
    background_tasks.add_task(
        run_download_workflow_task, download_id=safe_download_id, request_data=request
    )
    logger.debug(f"Background task added for {safe_download_id}")

    # Return the initial status
    return initial_task_status


@router.get("/status/{download_id}", response_model=TaskStatus)
async def get_status(download_id: str):
    """Retrieves the current status of a specific download task from the database."""
    logger.debug(f"Status query for: {download_id}")
    task_info = await get_task_status_from_db(download_id)
    if task_info is None:
        raise HTTPException(
            status_code=404, detail=f"Download ID '{download_id}' not found"
        )
    return task_info


# Keep the POST /search endpoint if needed for compatibility or specific use cases
@router.post("/search", response_model=List[SearchResultItem])
async def search_docs_post_body(request: SearchRequest):
    """Searches downloaded content (POST with full SearchRequest body)."""
    logger.info(f"POST /search request for: {request.download_id}")
    # Basic validation (can be enhanced in model)
    if request.extract_selector is not None and not request.extract_selector.strip():
        raise HTTPException(
            status_code=422, detail="extract_selector cannot be empty if provided"
        )
    if not request.scan_keywords:
        raise HTTPException(
            status_code=422, detail="scan_keywords cannot be empty for this endpoint."
        )

    return await _perform_search(
        download_id=request.download_id,
        scan_keywords=request.scan_keywords,
        extract_selector=request.extract_selector,
        extract_keywords=request.extract_keywords,
        limit=request.limit,
    )


@router.post("/search/{download_id}", response_model=List[SearchResultItem])
async def search_docs_post_path(
    download_id: str,
    request_body: SearchRequestBody,  # Use the specific body model
):
    """Searches downloaded content (POST with simplified body, ID in path)."""
    logger.info(f"POST /search/{download_id} request")

    # Logic to derive parameters from the simplified body
    # Model validator already ensures query or scan_keywords is present
    # and selector is non-empty if provided
    scan_kw = request_body.scan_keywords
    if (
        request_body.query and not scan_kw
    ):  # Prefer scan_keywords if both provided? Or combine? Currently uses query only if scan_keywords absent.
        scan_kw = [request_body.query]

    selector = (
        request_body.extract_selector if request_body.extract_selector else "body"
    )

    # Call the internal search function
    return await _perform_search(
        download_id=download_id,
        scan_keywords=scan_kw,  # Use derived scan keywords
        extract_selector=selector,
        extract_keywords=request_body.extract_keywords,
        limit=request_body.limit,
    )


@router.get("/health")
async def health():
    """Basic health check endpoint."""
    logger.debug("Health check requested.")
    return {"status": "healthy", "timestamp": datetime.now(timezone.utc).isoformat()}
