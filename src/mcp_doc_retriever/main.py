# src/mcp_doc_retriever/main.py
"""
Module: main.py (FastAPI Server Entry Point & Core Components)

Description:
MCP Document Retriever FastAPI server. Initializes the app, database,
background task executor, and includes API routes from api.py.
Handles core setup, shutdown, background task execution logic, and DB interactions.

Uses an SQLite database (`task_status.db`) via aiosqlite to track the status of
background download tasks persistently.
"""

import asyncio
import os
import sys
from pathlib import Path
from typing import Optional

import aiosqlite
from fastapi import FastAPI
from loguru import logger

# Import necessary components from the new core module
from mcp_doc_retriever.core import (
    init_db,
    close_db,
    shared_executor,
    add_task_status,  # Needed for usage_example
    get_task_status_from_db, # Needed for usage_example
    update_task_status, # Needed for usage_example
    db_connection, # Needed for usage_example override check
)
from mcp_doc_retriever.models import TaskStatus # Needed for usage_example

# Import the API router
from mcp_doc_retriever.api import router as api_router


# Configure Loguru (Consider moving to a dedicated logging config file if complex)
# This configuration might be better placed in core.py or a dedicated logging setup file
# if other modules also need specific configuration. For now, keep it here as it sets
# the root logger level which core.py might rely on if it doesn't reconfigure.
from mcp_doc_retriever import config # Need config for log level
logger.configure(
    handlers=[
        {
            "sink": sys.stdout,
            "filter": lambda record: record["extra"].get("name")
            not in ["httpx", "websockets", "aiosqlite"],
            "level": getattr(config, "LOG_LEVEL", "INFO").upper(),
        }
    ]
)
logger.info(
    f"Root logger configured. Level: {getattr(config, 'LOG_LEVEL', 'INFO').upper()}"
)
logger = logger.bind(module="main")  # Bind logger for this module


# --- FastAPI App Initialization ---
app = FastAPI(
    title="MCP Document Retriever",
    description="API for downloading and searching documentation.",
    version="1.0.0",
)


# --- Startup and Shutdown Events ---
@app.on_event("startup")
async def app_startup():
    """Initialize database connection on startup."""
    await init_db() # Use function from core.py


@app.on_event("shutdown")
async def app_shutdown():
    """Gracefully shutdown the shared thread pool executor and DB connection."""
    logger.info("Application shutting down...")
    await close_db() # Use function from core.py
    logger.info("Closing thread pool executor.")
    shared_executor.shutdown(wait=True) # Use executor from core.py
    logger.info("Executor shutdown complete.")


# --- Include API Routes ---
app.include_router(api_router)
logger.info("API routes included from api.py")


# --- Standalone Example Usage (kept as requested, now uses core functions) ---
def usage_example():
    """Tests DB helpers with an explicit connection in standalone mode."""
    import uuid
    import shutil
    from datetime import datetime, timezone # Import datetime/timezone locally for example
    from concurrent.futures import ThreadPoolExecutor # Import locally for example

    example_executor = ThreadPoolExecutor(
        max_workers=1
    )
    temp_db_path = Path("./downloads_standalone_test/standalone_status.db")
    test_id = f"standalone_helper_test_{uuid.uuid4().hex[:4]}"
    db_ok = False
    example_db_conn: Optional[aiosqlite.Connection] = None

    async def run_example():
        nonlocal example_db_conn, db_ok
        # print("[DEBUG] Entering run_example") # Removed debug print
        print("\n=== Starting Standalone Example (DB Helper Test) ===")
        print(f"Test ID: {test_id}")

        # Clean up previous test run DB
        if temp_db_path.exists():
            try:
                temp_db_path.unlink()
                print(f"Removed previous test DB: {temp_db_path}")
            except OSError as e:
                print(f"Error removing previous test DB: {e}")
        temp_db_path.parent.mkdir(parents=True, exist_ok=True)

        try:
            # print("[DEBUG] Entering try block") # Removed debug print
            # 1. Initialize LOCAL DB connection for example
            print(f"Initializing temporary DB for example: {temp_db_path}")
            # print("[DEBUG] Before aiosqlite.connect") # Removed debug print
            example_db_conn = await aiosqlite.connect(temp_db_path)
            example_db_conn.row_factory = aiosqlite.Row
            async with example_db_conn.cursor() as cursor:
                # Ensure table exists in the temp DB (Copied from core.init_db)
                await cursor.execute("""
                    CREATE TABLE IF NOT EXISTS download_status (
                        download_id TEXT PRIMARY KEY, status TEXT NOT NULL, message TEXT,
                        start_time TEXT NOT NULL, end_time TEXT, error_details TEXT
                    )""")
            await example_db_conn.commit()
            print("Temporary DB initialized.")
            # print("[DEBUG] After aiosqlite.connect and table creation") # Removed debug print

            # 2. Test add_task_status with local connection override
            print(f"\nTesting add_task_status for ID: {test_id}")
            # print("[DEBUG] Before add_task_status") # Removed debug print
            initial_status = TaskStatus( # Use imported TaskStatus model
                download_id=test_id,
                status="pending",
                start_time=datetime.now(timezone.utc),
                message="Initial add test",
            )
            # Pass the explicit connection using the helper from core.py
            await add_task_status(
                test_id, initial_status, db_conn_override=example_db_conn
            )
            print("add_task_status called.")
            # print("[DEBUG] After add_task_status") # Removed debug print

            # 3. Test get_task_status_from_db with local connection override
            print(f"\nTesting get_task_status_from_db for ID: {test_id}")
            # print("[DEBUG] Before get_task_status_from_db (1st call)") # Removed debug print
            # Pass the explicit connection using the helper from core.py
            read_status_1 = await get_task_status_from_db(
                test_id, db_conn_override=example_db_conn
            )
            print(
                f"Read after add: {read_status_1.status if read_status_1 else 'Not Found'}"
            )
            # print(f"[DEBUG] After get_task_status_from_db (1st call): {read_status_1}") # Removed debug print
            if not (read_status_1 and read_status_1.status == "pending"):
                raise ValueError("Failed to read back initial status after add.")

            # 4. Test update_task_status with local connection override
            print(f"\nTesting update_task_status for ID: {test_id}")
            # print("[DEBUG] Before update_task_status") # Removed debug print
            # Pass the explicit connection using the helper from core.py
            await update_task_status(
                test_id,
                {"status": "running", "message": "Update test"},
                db_conn_override=example_db_conn,
            )
            print("update_task_status called.")
            # print("[DEBUG] After update_task_status") # Removed debug print

            # 5. Test get_task_status_from_db again
            print(f"\nTesting get_task_status_from_db again for ID: {test_id}")
            # print("[DEBUG] Before get_task_status_from_db (2nd call)") # Removed debug print
            # Pass the explicit connection using the helper from core.py
            read_status_2 = await get_task_status_from_db(
                test_id, db_conn_override=example_db_conn
            )
            print(
                f"Read after update: {read_status_2.status if read_status_2 else 'Not Found'}"
            )
            # print(f"[DEBUG] After get_task_status_from_db (2nd call): {read_status_2}") # Removed debug print
            if not (read_status_2 and read_status_2.status == "running"):
                raise ValueError("Failed to read back updated status.")

            print("\n✓ DB Helper functions test successful.")
            db_ok = True

        except Exception as e:
            # print("[DEBUG] Entering except block") # Removed debug print
            print(f"Standalone Example FAILED with exception: {e}")
            # Use root logger if main module logger isn't configured yet in standalone
            logger.error("Standalone example failed", exc_info=True)
            db_ok = False
        finally:
            # 6. Cleanup
            # print("[DEBUG] Entering finally block") # Removed debug print
            if example_db_conn:
                print("Closing example DB connection...")
                # print("[DEBUG] Before example_db_conn.close()") # Removed debug print
                await example_db_conn.close()
                print("Example DB connection closed.")
                # print("[DEBUG] After example_db_conn.close()") # Removed debug print
            # print("[DEBUG] Before example_executor.shutdown()") # Removed debug print
            example_executor.shutdown(wait=True)
            print("\nStandalone example finished.")
            # print("[DEBUG] After example_executor.shutdown()") # Removed debug print

        # Print success/failure
        print("\n------------------------------------")
        if db_ok:
            print("✓ Standalone main.py example finished successfully (DB helpers).")
        else:
            print("✗ Standalone main.py example failed (DB helpers).")
        print("------------------------------------")

    # Logging is already configured by the time this runs if main.py is executed
    # print("[DEBUG] Before asyncio.run(run_example())") # Removed debug print
    asyncio.run(run_example())
    # print("[DEBUG] After asyncio.run(run_example())") # Removed debug print


if __name__ == "__main__":
    print(
        """
NOTE: Run as FastAPI server: uvicorn src.mcp_doc_retriever.main:app --reload --port 8000 --host 0.0.0.0
Running directly executes usage_example (testing DB helpers directly).""",
        file=sys.stderr,
    )
    usage_example()
