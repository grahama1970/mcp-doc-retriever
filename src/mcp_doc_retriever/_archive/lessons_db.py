# src/mcp_doc_retriever/main.py
"""
Module: main.py (FastAPI Server Entry Point)

Description:
MCP Document Retriever FastAPI server.
- Provides `/download` endpoint to start recursive downloads (as background tasks).
- Provides `/status/{download_id}` endpoint to check download progress/status.
- Provides `/search` endpoint to search downloaded content.
- Provides `/` endpoint for SSE connection (MCP protocol).
- Provides `/health` endpoint for basic health checks.

Uses SQLite (project_state.db) for persistent state tracking via `aiosqlite`,
including download task statuses, lessons learned, and project configurations.
The database file is stored within the configured persistent download directory.

Handles basic URL validation and SSRF protection on the `/download` endpoint.
Delegates actual download logic to `downloader.start_recursive_download` and
search logic to `searcher.perform_search`.

Third-Party Documentation:
- FastAPI: https://fastapi.tiangolo.com/
- Uvicorn: https://www.uvicorn.org/
- sse-starlette: https://github.com/sysid/sse-starlette
- Loguru: https://loguru.readthedocs.io/
- aiosqlite: https://github.com/omnilib/aiosqlite

Sample Input/Output:

Input (Start Download):
  curl -X POST "http://127.0.0.1:8000/download" -H "Content-Type: application/json" -d '{"url": "https://example.com", "source_type": "website"}'
Output (Start Download):
  {"download_id": "some-uuid-string", "status": "pending", "message": "Download task accepted."}

Input (Check Status):
  curl "http://127.0.0.1:8000/status/some-uuid-string"
Output (Check Status):
  {"download_id": "some-uuid-string", "status": "completed", "start_time": "...", "end_time": "...", "error_message": null}

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
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Any, Tuple, Union

import aiosqlite
from pydantic import (
    BaseModel,
    Field,
    field_validator,
    AnyHttpUrl,
    model_validator,
    ConfigDict,
)
from typing import Literal

from fastapi import BackgroundTasks, FastAPI, HTTPException
from loguru import logger
from sse_starlette.sse import EventSourceResponse

# --- Package Imports ---
import mcp_doc_retriever.config as config
# --- Import shared utils (assuming datetime helpers moved here) ---
from mcp_doc_retriever.utils import (
     is_url_private_or_internal,
     _datetime_to_iso, # Assuming moved from lessons_db/main
     _iso_to_datetime  # Assuming moved from lessons_db/main
)
# --- Import lesson DB functions (now separate) ---
# Note: LessonLearned model is NOT imported here, only used in usage_example
# --- End lesson DB imports ---
from mcp_doc_retriever.downloader.workflow import fetch_documentation_workflow
from mcp_doc_retriever.downloader.git_downloader import check_git_dependency
from mcp_doc_retriever.searcher.searcher import (
    SearchRequest,
    SearchResultItem,
)
from mcp_doc_retriever.searcher.searcher import perform_search

# --- Pydantic Models ---

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
                self.depth = 5 # Default depth if not provided
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
    Matches the structure stored in the database.
    """
    download_id: str
    status: Literal["pending", "running", "completed", "failed"]
    message: Optional[str] = None
    start_time: Optional[datetime] = None
    end_time: Optional[datetime] = None
    error_details: Optional[str] = None # Store traceback or error summary if failed

    model_config = ConfigDict(from_attributes=True) # Allow creating from DB Row objects

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


# --- Logger Configuration ---
logger.configure(handlers=[{
    "sink": sys.stdout,
    "filter": lambda record: record["extra"].get("name") not in ["httpx", "websockets"]
}])
logger.info("Root logger configured.")
logger = logger.bind(module="main")

# --- Global State & Resources ---
db_conn: Optional[aiosqlite.Connection] = None
# --- Use new DB name ---
DATABASE_PATH = Path(config.DOWNLOAD_BASE_DIR) / "project_state.db"
# --- End DB name change ---

shared_executor = ThreadPoolExecutor(
    max_workers=os.cpu_count(), thread_name_prefix="FastAPI_SyncWorker"
)
# -----------------------------

# --- Database Helper Functions ---
async def init_db():
    """Initializes the SQLite database connection and creates tables."""
    global db_conn
    try:
        DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)
        logger.info(f"DEBUG_DB: Initializing DB connection. Current db_conn: {db_conn}") # DEBUG
        logger.info(f"Initializing database connection to: {DATABASE_PATH}")
        db_conn = await aiosqlite.connect(DATABASE_PATH)
        db_conn.row_factory = aiosqlite.Row

        async with db_conn.cursor() as cursor:
            # Task Status Table
            await cursor.execute("""
                CREATE TABLE IF NOT EXISTS download_status (
                    download_id TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    message TEXT,
                    start_time TEXT NOT NULL,
                    end_time TEXT,
                    error_details TEXT
                )
            """)
            # Lessons Learned Table
            await cursor.execute("""
                CREATE TABLE IF NOT EXISTS lessons_learned (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    severity TEXT,
                    role TEXT NOT NULL,
                    task TEXT,
                    phase TEXT,
                    problem TEXT NOT NULL,
                    solution TEXT NOT NULL,
                    tags TEXT,
                    context TEXT,
                    example TEXT
                )
            """)
            # Project Config Table
            await cursor.execute("""
                CREATE TABLE IF NOT EXISTS project_config (
                    config_key TEXT PRIMARY KEY,
                    config_value TEXT NOT NULL -- Store JSON as text
                )
            """)

        # Load and Insert .roomodes
        # Assuming .roomodes is at the project root relative to this file
        project_root = Path(__file__).resolve().parent.parent.parent # Adjust if main.py moves
        roomodes_path = project_root / ".roomodes"
        if roomodes_path.exists():
            logger.info(f"Loading .roomodes from {roomodes_path}")
            try:
                with open(roomodes_path, "r", encoding="utf-8") as f:
                    roomodes_content = f.read()
                json.loads(roomodes_content) # Validate JSON
                async with db_conn.cursor() as cursor:
                    await cursor.execute(
                        """
                        INSERT OR REPLACE INTO project_config (config_key, config_value)
                        VALUES (?, ?)
                        """,
                        ("roomodes", roomodes_content)
                    )
                logger.info("Stored .roomodes content in project_config table.")
            except json.JSONDecodeError as e:
                logger.error(f"Failed to parse .roomodes as JSON: {e}. Skipping DB storage.")
            except Exception as e:
                logger.error(f"Failed to read or store .roomodes file: {e}", exc_info=True)
        else:
            logger.warning(f".roomodes file not found at {roomodes_path}. Cannot store in DB.")

        await db_conn.commit()
        logger.info("Database initialized and tables 'download_status', 'lessons_learned', 'project_config' ensured.")
    except Exception as e:
        logger.critical(f"Failed to initialize database: {e}", exc_info=True)
        raise

async def close_db():
    """Closes the database connection."""
    global db_conn
    if db_conn:
        try:
            await db_conn.close()
            logger.info("Database connection closed.")
        except Exception as e:
            logger.error(f"Error closing database connection: {e}", exc_info=True)
    db_conn = None

# --- Removed Datetime Helpers (Assume moved to utils.py) ---

# --- Task Status DB Functions ---
async def add_task_status(task: TaskStatus):
    """Adds or replaces a task status record in the database."""
    if not db_conn:
        logger.error("Database not connected, cannot add task status.")
        return
    try:
        async with db_conn.cursor() as cursor:
            await cursor.execute(
                """
                INSERT OR REPLACE INTO download_status
                (download_id, status, message, start_time, end_time, error_details)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    task.download_id,
                    task.status,
                    task.message,
                    _datetime_to_iso(task.start_time), # Use imported helper
                    _datetime_to_iso(task.end_time),   # Use imported helper
                    task.error_details,
                ),
            )
        # NOTE: Explicit commit moved to /download endpoint after this call
        logger.debug(f"Added/Replaced status for {task.download_id}: {task.status}")
    except Exception as e:
        logger.error(f"Failed to add/replace task status for {task.download_id}: {e}", exc_info=True)

async def update_task_status(
    download_id: str,
    status: Optional[Literal["pending", "running", "completed", "failed"]] = None,
    message: Optional[str] = None,
    end_time: Optional[datetime] = None,
    error_details: Optional[str] = None,
    set_end_time_now: bool = False
    ):
    """Updates specific fields for a task status record."""
    if not db_conn:
        logger.error("Database not connected, cannot update task status.")
        return

    updates: Dict[str, Any] = {}
    params: List[Any] = []

    if status is not None: updates["status"] = "?"; params.append(status)
    if message is not None: updates["message"] = "?"; params.append(message)
    if set_end_time_now: updates["end_time"] = "?"; params.append(_datetime_to_iso(datetime.now(timezone.utc))) # Use helper
    elif end_time is not None: updates["end_time"] = "?"; params.append(_datetime_to_iso(end_time)) # Use helper
    if error_details is not None: updates["error_details"] = "?"; params.append(error_details)

    if not updates:
        logger.warning(f"No fields provided to update status for {download_id}.")
        return

    set_clause = ", ".join(f"{field} = ?" for field in updates.keys()) # Corrected placeholder usage
    sql = f"UPDATE download_status SET {set_clause} WHERE download_id = ?"
    params.append(download_id)

    try:
        async with db_conn.cursor() as cursor:
            await cursor.execute(sql, tuple(params))
        await db_conn.commit()
        logger.debug(f"Updated status for {download_id}. Fields: {list(updates.keys())}")
    except Exception as e:
        logger.error(f"Failed to update task status for {download_id}: {e}", exc_info=True)

async def get_task_status_from_db(download_id: str) -> Optional[TaskStatus]:
    """Retrieves task status from the database and converts it to a Pydantic model."""
    logger.info(f"DEBUG_DB: Querying status for {download_id}. DB Path: {DATABASE_PATH}") # DEBUG
    if not db_conn:
        logger.error("Database not connected, cannot get task status.")
        return None
    try:
        async with db_conn.cursor() as cursor:
            await cursor.execute( "SELECT * FROM download_status WHERE download_id = ?", (download_id,) )
            row = await cursor.fetchone()

        if row:
            task_data = dict(row)
            task_data['start_time'] = _iso_to_datetime(task_data.get('start_time')) # Use helper
            task_data['end_time'] = _iso_to_datetime(task_data.get('end_time'))     # Use helper
            return TaskStatus.model_validate(task_data)
        else:
            logger.warning(f"DEBUG_DB: No status row found for download_id '{download_id}' in database.") # DEBUG
            return None
    except Exception as e:
        logger.error(f"DEBUG_DB: Error getting status for {download_id}", exc_info=True) # DEBUG
        logger.error(f"Failed to get task status for {download_id}: {e}", exc_info=True)
        return None

# --- Project Config DB Function (New) ---
async def get_project_config_value(config_key: str) -> Optional[str]:
    """Retrieves a configuration value from the project_config table."""
    if not db_conn:
        logger.error("Database not connected, cannot get project config.")
        return None
    try:
        async with db_conn.cursor() as cursor:
            await cursor.execute(
                "SELECT config_value FROM project_config WHERE config_key = ?", (config_key,)
            )
            row = await cursor.fetchone()
        return row['config_value'] if row else None
    except Exception as e:
        logger.error(f"Failed to get project config for key '{config_key}': {e}", exc_info=True)
        return None

# --- FastAPI App Initialization ---
app = FastAPI(
    title="MCP Document Retriever",
    description="API for downloading and searching documentation.",
    version="1.0.0",
)

# --- Startup and Shutdown Event Handlers ---
@app.on_event("startup")
async def startup_event():
    await init_db()

@app.on_event("shutdown")
async def shutdown_event():
    logger.info("Application shutting down...")
    await close_db()
    logger.info("Closing thread pool executor...")
    shared_executor.shutdown(wait=True)
    logger.info("Executor shutdown complete.")

# --- Background Task Wrapper ---
async def run_download_workflow_task( download_id: str, request_data: DocDownloadRequest ):
    """Wrapper to run the download workflow and update task status in the database."""
    logger.info( f"Background task started for download_id: {download_id} ({request_data.source_type})" )
    await update_task_status( download_id=download_id, status="running", message="Download workflow starting..." )
    try:
        base_dir_path = Path(config.DOWNLOAD_BASE_DIR).resolve()
        req_timeout = config.TIMEOUT_REQUESTS
        play_timeout = config.TIMEOUT_PLAYWRIGHT
        await fetch_documentation_workflow(
            source_type=request_data.source_type,
            download_id=download_id,
            repo_url=str(request_data.repo_url) if request_data.repo_url else None,
            doc_path=request_data.doc_path,
            url=str(request_data.url) if request_data.url else None,
            base_dir=base_dir_path,
            depth=request_data.depth if request_data.depth is not None else 5,
            force=request_data.force or False,
            max_file_size=None,
            timeout_requests=req_timeout,
            timeout_playwright=play_timeout,
            max_concurrent_requests=50,
            executor=shared_executor,
            logger_override=logger.bind(workflow=download_id),
        )
        await update_task_status(
            download_id=download_id,
            status="completed",
            message="Download workflow finished successfully.",
            set_end_time_now=True
        )
        logger.info(f"Background task completed successfully for download_id: {download_id}" )
    except Exception as e:
        tb_str = traceback.format_exc()
        error_msg = f"{type(e).__name__}: {e}"
        logger.error(f"Background task failed for download_id: {download_id}. Error: {error_msg}\nTraceback: {tb_str}")
        await update_task_status(
            download_id=download_id,
            status="failed",
            message=f"Download failed: {error_msg}",
            error_details=f"{error_msg}\n{tb_str[:2000]}", # Limit traceback length
            set_end_time_now=True
        )

# --- API Endpoints ---
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

@app.get("/health")
async def health():
    """Basic health check endpoint."""
    logger.debug("Health check requested.")
    return {"status": "healthy", "timestamp": datetime.now(timezone.utc).isoformat()}

@app.post("/download", response_model=TaskStatus, status_code=202)
async def download_docs(request: DocDownloadRequest, background_tasks: BackgroundTasks):
    """Accepts download request, validates, saves initial status to DB, schedules background task."""
    logger.info( f"Received download request: {request.model_dump_json(exclude_none=True)}" )
    # --- Validation ---
    target_location = None
    if request.source_type == "git":
        if not check_git_dependency(): raise HTTPException(status_code=503, detail="Git command not found.")
        target_location = str(request.repo_url)
    elif request.source_type in ["website", "playwright"]:
        target_location = str(request.url)
        if is_url_private_or_internal(target_location): raise HTTPException(status_code=400, detail="Blocked potential SSRF URL.")
    else: raise HTTPException(status_code=400, detail="Invalid source_type.")

    # --- ID Generation/Sanitization ---
    if request.download_id is None: safe_download_id = f"dl_{uuid.uuid4().hex[:8]}"; logger.info(f"Generated download_id: {safe_download_id}")
    else: safe_download_id = re.sub(r"[^\w\-\_]+", "_", request.download_id); logger.warning(f"Sanitized ID '{request.download_id}' to '{safe_download_id}'") if safe_download_id != request.download_id else None

    # --- Check existing status in DB ---
    existing_status = await get_task_status_from_db(safe_download_id)
    if existing_status and existing_status.status == "running": raise HTTPException( status_code=409, detail=f"Task '{safe_download_id}' already running." )

    # --- Add initial task to DB ---
    initial_task_status = TaskStatus( download_id=safe_download_id, status="pending", start_time=datetime.now(timezone.utc), message=f"Download task for {target_location} queued.", )
    await add_task_status(initial_task_status)
    # --- Add explicit commit ---
    if db_conn:
        try:
            await db_conn.commit()
            logger.info(f"DEBUG_DB: Explicit commit after adding initial status for {safe_download_id}")
        except Exception as commit_err:
            logger.error(f"DEBUG_DB: Error during explicit commit for {safe_download_id}: {commit_err}")

    logger.info(f"Task {safe_download_id} created in DB for {target_location}")
    # --- Schedule background task ---
    background_tasks.add_task( run_download_workflow_task, download_id=safe_download_id, request_data=request )
    logger.debug(f"Background task added for {safe_download_id}")
    return initial_task_status

@app.get("/status/{download_id}", response_model=TaskStatus)
async def get_status(download_id: str):
    """Retrieves the current status of a specific download task from the database."""
    logger.info(f"DEBUG_STATUS: Entering get_status for {download_id}") # DEBUG
    logger.debug(f"Status query for: {download_id}")
    task_info = await get_task_status_from_db(download_id)
    logger.info(f"DEBUG_STATUS: Result for {download_id} from DB: {task_info}") # DEBUG
    if task_info is None:
        raise HTTPException(status_code=404, detail="Download ID not found")
    return task_info

async def _perform_search( download_id: str, scan_keywords: List[str], extract_selector: str, extract_keywords: Optional[List[str]] = None, limit: int = 100 ) -> List[SearchResultItem]:
    """Shared search logic used by search endpoints (modified to check DB status)."""
    task_info = await get_task_status_from_db(download_id)
    if task_info is None: raise HTTPException( status_code=404, detail=f"Download ID '{download_id}' not found." )
    if task_info.status != "completed": raise HTTPException( status_code=409, detail=f"Task status is '{task_info.status}', requires 'completed'.", )
    try:
        base_dir_path = Path(config.DOWNLOAD_BASE_DIR).resolve()
        if not base_dir_path.is_dir(): raise HTTPException( status_code=500, detail="Server config error: Base download directory not found.", )
        search_request = SearchRequest( download_id=download_id, scan_keywords=scan_keywords, extract_selector=extract_selector, extract_keywords=extract_keywords or [], limit=limit, )
        results = perform_search(search_request, base_dir_path)
        logger.info(f"Search for '{download_id}' yielded {len(results)} results.")
        return results
    except FileNotFoundError as e: logger.error(f"Search failed for '{download_id}': Index file not found. {e}", exc_info=True); raise HTTPException( status_code=404, detail=f"Index file not found for download ID '{download_id}'.", )
    except Exception as e: logger.error(f"Search failed for '{download_id}': {e}", exc_info=True); raise HTTPException( status_code=500, detail=f"Internal error during search: {type(e).__name__}" )

@app.post("/search", response_model=List[SearchResultItem])
async def search_docs(request: SearchRequest):
    """Searches downloaded content (POST with full SearchRequest JSON body)."""
    logger.info(f"POST /search request for: {request.download_id}")
    if not request.scan_keywords or not request.extract_selector: raise HTTPException( status_code=422, detail="Both 'scan_keywords' and 'extract_selector' are required." )
    return await _perform_search( download_id=request.download_id, scan_keywords=request.scan_keywords, extract_selector=request.extract_selector, extract_keywords=request.extract_keywords, limit=request.limit or 100, )

@app.post("/search/{download_id}", response_model=List[SearchResultItem])
async def search_docs_by_id(download_id: str, request_body: SearchRequestBody):
    """Searches downloaded content (POST with SearchRequestBody). Supports simple 'query' or specific fields."""
    logger.info(f"POST /search/{download_id} request.")
    scan_kw: List[str] = []; selector: str = ""
    if request_body.scan_keywords and request_body.extract_selector: logger.info("Using scan_keywords and extract_selector from request body."); scan_kw = request_body.scan_keywords; selector = request_body.extract_selector
    elif request_body.query: logger.info(f"Using simple 'query' parameter: '{request_body.query}'"); scan_kw = [request_body.query]; selector = "body"
    else: raise HTTPException( status_code=422, detail="Request must contain either a 'query' field or both 'scan_keywords' and 'extract_selector'.", )
    return await _perform_search( download_id=download_id, scan_keywords=scan_kw, extract_selector=selector, extract_keywords=request_body.extract_keywords, limit=request_body.limit or 100, )

# --- Standalone Example Usage ---
def usage_example():
    """Demonstrates programmatic usage (runs workflow and interacts with lessons/config)."""
    import uuid
    import shutil
    # --- Import lesson functions for example ---
    from mcp_doc_retriever.persistence.lessons_db import LessonLearned, add_lesson, find_lessons

    example_db_path = Path("./downloads_standalone_test/project_state.db") # Use new name
    example_db_path.parent.mkdir(parents=True, exist_ok=True)

    async def run_example():
        print("\n=== Starting Standalone Example ===")
        global db_conn
        db_conn_orig = db_conn
        db_conn = await aiosqlite.connect(example_db_path)
        db_conn.row_factory = aiosqlite.Row
        # Run init_db AFTER connecting, it uses the global db_conn
        await init_db()

        # --- Verify .roomodes loaded ---
        print("\nChecking if .roomodes was loaded into DB...")
        roomodes_data = await get_project_config_value("roomodes")
        if roomodes_data:
            print("✓ .roomodes content found in database.")
            try:
                parsed_modes = json.loads(roomodes_data)
                print(f"  - Parsed successfully, found {len(parsed_modes.get('customModes',[]))} custom modes.")
            except json.JSONDecodeError: print("  - ERROR: Content in DB is not valid JSON!")
        else: print("  - .roomodes content NOT found in database (was .roomodes file present at startup?).")

        # --- Add and find lessons ---
        print("\nAdding a sample lesson...")
        test_lesson = LessonLearned( role="Example Role", problem="Example problem description.", solution="Example solution.", tags=["example", "testing"], context="Standalone test context." )
        await add_lesson(db_conn, test_lesson) # Pass connection

        print("\nFinding lessons tagged 'testing'...")
        found = await find_lessons(db_conn, tags=["testing"]) # Pass connection
        print(f"Found {len(found)} lessons:")
        for lesson in found: print(f"  - ID: {getattr(lesson, 'id', 'N/A')}, Role: {lesson.role}, Problem: {lesson.problem[:30]}..., Tags: {lesson.tags}")
        assert len(found) >= 1

        # --- Download Workflow Example ---
        test_url = "https://httpbin.org/html"; test_id = f"standalone_{uuid.uuid4().hex[:8]}"; test_dir = Path("./downloads_standalone_test").resolve(); print(f"ID: {test_id}, Base Dir: {test_dir}"); test_dir.mkdir(parents=True, exist_ok=True)
        start_status = TaskStatus( download_id=test_id, status="pending", start_time=datetime.now(timezone.utc) ); await add_task_status(start_status); await db_conn.commit() # Commit initial status
        print(f"\nRunning download for {test_url}..."); dl_ok = False; example_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="ExampleWorker")
        try:
            await update_task_status(test_id, status="running", message="Starting...")
            await fetch_documentation_workflow( source_type="website", download_id=test_id, url=test_url, base_dir=test_dir, depth=0, force=True, executor=example_executor, timeout_requests=config.TIMEOUT_REQUESTS, timeout_playwright=config.TIMEOUT_PLAYWRIGHT, )
            await update_task_status( test_id, status="completed", message="Done.", set_end_time_now=True ); print("Download workflow OK."); dl_ok = True
        except Exception as e: print(f"Download FAILED: {e}"); logger.error("Standalone failed", exc_info=True); await update_task_status( test_id, status="failed", message=f"Error: {e}", error_details=traceback.format_exc(), set_end_time_now=True )
        final_status = await get_task_status_from_db(test_id); print(f"\nFinal Status from DB: {final_status}")
        if final_status and final_status.status == "completed": idx_path = test_dir / "index" / f"{test_id}.jsonl"; print(f"\nIndex created: {idx_path.is_file()}"); print("\nTesting Search..."); try: search_request = SearchRequest( download_id=test_id, scan_keywords=["h1"], extract_selector="p", limit=5 ); results = perform_search(search_request, test_dir); print(f"Found {len(results)} search results.") except Exception as e: print(f"Search FAILED: {e}"); dl_ok = False
        example_executor.shutdown(wait=True); print("\nStandalone example finished.")
        print("\n------------------------------------"); print("✓ Standalone main.py example finished successfully.") if final_status and final_status.status == "completed" else print("✗ Standalone main.py example failed."); print("------------------------------------")

        await close_db()
        db_conn = db_conn_orig

        # Clean up DB file after example run?
        # try:
        #     os.remove(example_db_path)
        #     # Potentially remove parent dir if empty? Careful.
        # except Exception as e:
        #     print(f"Warning: Could not clean up example DB/dir: {e}")

    asyncio.run(run_example())

if __name__ == "__main__":
    print( """ NOTE: Run as FastAPI server: uvicorn src.mcp_doc_retriever.main:app --reload --port 8000 --host 0.0.0.0\nRunning directly executes usage_example.""", file=sys.stderr, )
    usage_example()