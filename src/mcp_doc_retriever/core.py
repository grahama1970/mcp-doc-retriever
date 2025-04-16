# src/mcp_doc_retriever/core.py
"""Core shared components: DB setup, helpers, executor, background task runner."""

import asyncio
import os
import sys
import traceback
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Any

import aiosqlite
from fastapi import HTTPException  # Keep for DB errors potentially raised to API
from loguru import logger

# Package imports (adjust based on actual structure if needed)
from mcp_doc_retriever import config
from mcp_doc_retriever.models import TaskStatus, DocDownloadRequest
from mcp_doc_retriever.downloader.workflow import fetch_documentation_workflow

# Configure a logger specific to this core module if desired, or use root
logger = logger.bind(module="core")

# --- Database Setup ---
DATABASE_URL = Path(config.DOWNLOAD_BASE_DIR) / "task_status.db"
db_connection: Optional[aiosqlite.Connection] = None


async def get_db() -> aiosqlite.Connection:
    """Dependency function placeholder (not used as direct dependency here, but pattern is useful)."""
    # Direct use of global db_connection in helpers below for simplicity in this structure
    if db_connection is None:
        logger.error("Database connection is not available.")
        raise HTTPException(status_code=503, detail="Database connection not available")
    return db_connection


async def init_db():
    """Initialize the database connection and create tables."""
    global db_connection
    if db_connection is not None:
        logger.info("Database connection already initialized.")
        return
    db_path = DATABASE_URL
    db_path.parent.mkdir(parents=True, exist_ok=True)
    logger.info(f"Initializing database connection to: {db_path}")
    try:
        db_connection = await aiosqlite.connect(db_path)
        db_connection.row_factory = aiosqlite.Row
        async with db_connection.cursor() as cursor:
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
        await db_connection.commit()
        logger.info(f"Database initialized and table 'download_status' ensured.")
    except Exception as e:
        logger.error(f"Database initialization failed: {e}", exc_info=True)
        db_connection = None


async def close_db():
    """Close the database connection."""
    global db_connection
    if db_connection:
        await db_connection.close()
        logger.info("Database connection closed.")
        db_connection = None


# --- Database Helper Functions ---


def _datetime_to_iso(dt: Optional[datetime]) -> Optional[str]:
    """Converts datetime object to timezone-aware ISO 8601 string (UTC)."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()


def _iso_to_datetime(iso_str: Optional[str]) -> Optional[datetime]:
    """Converts ISO 8601 string back to datetime object."""
    if iso_str is None:
        return None
    try:
        if iso_str.endswith("Z"):
            iso_str = iso_str[:-1] + "+00:00"
        return datetime.fromisoformat(iso_str)
    except Exception as e:
        logger.warning(f"Could not parse ISO date string '{iso_str}': {e}")
        return None


async def add_task_status(
    download_id: str,
    status: TaskStatus,
    db_conn_override: Optional[aiosqlite.Connection] = None,
):
    """Adds or replaces a task status entry in the database."""
    db = db_conn_override if db_conn_override else db_connection
    if not db:
        # If called from API context, raising HTTPException is okay
        # If called from background task, should just log error
        logger.error(f"DB connection not available for add_task_status on {download_id}")
        raise HTTPException(
            status_code=503, detail="DB connection not available for add_task_status"
        )
    try:
        async with db.cursor() as cursor:
            await cursor.execute(
                """
                INSERT OR REPLACE INTO download_status
                (download_id, status, message, start_time, end_time, error_details)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    download_id,
                    status.status,
                    status.message,
                    _datetime_to_iso(status.start_time),
                    _datetime_to_iso(status.end_time),
                    status.error_details,
                ),
            )
        await db.commit()
        logger.debug(f"Added/Replaced status for {download_id}: {status.status}")
    except Exception as e:
        logger.error(
            f"Failed to add/replace task status for {download_id}: {e}", exc_info=True
        )
        raise  # Re-raise to signal failure to caller (e.g., /download endpoint)


async def update_task_status(
    download_id: str,
    updates: Dict[str, Any],
    db_conn_override: Optional[aiosqlite.Connection] = None,
):
    """Updates specific fields for a task status entry."""
    db = db_conn_override if db_conn_override else db_connection
    if not db:
        logger.error(
            f"DB connection not available for update_task_status on {download_id}"
        )
        # Avoid raising HTTPException from background task directly, just log error
        return
    fields, values = [], []
    for key, value in updates.items():
        if isinstance(value, datetime):
            value = _datetime_to_iso(value)
        fields.append(f"{key} = ?")
        values.append(value)
    if not fields:
        return
    sql = f"UPDATE download_status SET {', '.join(fields)} WHERE download_id = ?"
    values.append(download_id)
    try:
        async with db.cursor() as cursor:
            await cursor.execute(sql, tuple(values))
        await db.commit()
        logger.debug(
            f"Updated status for {download_id}. Fields: {list(updates.keys())}"
        )
    except Exception as e:
        logger.error(
            f"Failed to update task status for {download_id}: {e}", exc_info=True
        )
        # Log error, but don't crash the background task


async def get_task_status_from_db(
    download_id: str, db_conn_override: Optional[aiosqlite.Connection] = None
) -> Optional[TaskStatus]:
    """Retrieves task status from the database."""
    db = db_conn_override if db_conn_override else db_connection
    if not db:
         # If called from API context, raising HTTPException is okay
        logger.error(f"DB connection not available for get_task_status_from_db on {download_id}")
        raise HTTPException(
            status_code=503,
            detail="DB connection not available for get_task_status_from_db",
        )
    try:
        async with db.cursor() as cursor:
            await cursor.execute(
                "SELECT * FROM download_status WHERE download_id = ?", (download_id,)
            )
            row = await cursor.fetchone()
        if row:
            return TaskStatus(
                download_id=row["download_id"],
                status=row["status"],
                message=row["message"],
                start_time=_iso_to_datetime(row["start_time"]),
                end_time=_iso_to_datetime(row["end_time"]),
                error_details=row["error_details"],
            )
        return None
    except Exception as e:
        logger.error(
            f"Failed to get task status for {download_id} from DB: {e}", exc_info=True
        )
        raise HTTPException(
            status_code=500, detail="Failed to query task status"
        )  # Signal error to API caller


# --- Global Resources ---
shared_executor = ThreadPoolExecutor(
    max_workers=os.cpu_count(), thread_name_prefix="FastAPI_SyncWorker"
)


# --- Background Task Wrapper ---
async def run_download_workflow_task(
    download_id: str,
    request_data: DocDownloadRequest,
):
    """Wrapper to run the download workflow and update task status using the database."""
    logger.info(
        f"Background task started for download_id: {download_id} ({request_data.source_type})"
    )
    # Update status in DB to 'running'
    try:
        # Use the helper function directly (as db_connection is global here)
        await update_task_status(
            download_id,
            {"status": "running", "message": "Download workflow starting..."},
        )
    except Exception as e:
        logger.error(f"Failed to set task {download_id} to running in DB: {e}")
        return  # Abort if initial status update fails

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
            depth=request_data.depth
            if request_data.depth is not None
            else getattr(config, "DEFAULT_WEB_DEPTH", 5),
            force=request_data.force or False,
            max_file_size=getattr(config, "MAX_FILE_SIZE_BYTES", 10 * 1024 * 1024),
            timeout_requests=req_timeout,
            timeout_playwright=play_timeout,
            max_concurrent_requests=getattr(config, "DEFAULT_WEB_CONCURRENCY", 50),
            executor=shared_executor,  # Pass executor to workflow if needed
            logger_override=logger.bind(workflow=download_id),
        )

        await update_task_status(
            download_id,
            {
                "status": "completed",
                "message": "Download workflow finished successfully.",
                "end_time": datetime.now(timezone.utc),
            },
        )
        logger.info(
            f"Background task completed successfully for download_id: {download_id}"
        )

    except Exception as e:
        tb_str = traceback.format_exc()
        error_msg = f"{type(e).__name__}: {e}"
        logger.error(
            f"Background task failed for download_id: {download_id}. Error: {error_msg}\nTraceback: {tb_str}"
        )
        try:
            await update_task_status(
                download_id,
                {
                    "status": "failed",
                    "message": f"Download failed: {error_msg}",
                    "error_details": f"{error_msg}\n{tb_str[:2000]}",
                    "end_time": datetime.now(timezone.utc),
                },
            )
        except Exception as db_err:
            logger.error(
                f"Failed to update DB status to failed for {download_id} after task error: {db_err}"
            )