# src/mcp_doc_retriever/lessons_cli.py
"""
Command Line Interface for managing the Lessons Learned SQLite database.

Allows agents (or humans) to add and find lessons learned records
without needing the main FastAPI application to be running. Uses standard sqlite3.
"""

# Removed asyncio import
import typer
import sys
import json
from pathlib import Path
from typing import List, Optional
import sqlite3  # Changed from aiosqlite
from loguru import logger
import datetime  # Import datetime for timestamp formatting

# --- Need to adjust imports based on project structure ---
# Assuming utils.py and project_state/db.py are importable
try:
    # Assumes running from project root or src is in PYTHONPATH
    # These db functions should now be synchronous
    from mcp_doc_retriever.project_state.db import (
        LessonLearned,
        init_lessons_db,
        add_lesson,
        find_lessons,
    )
    # Import config safely
    try:
        import mcp_doc_retriever.config as config
        DEFAULT_BASE_DIR = getattr(
            config, "DOWNLOAD_BASE_DIR", Path("./downloads")
        )
    except ImportError:
        logger.warning("mcp_doc_retriever.config not found, using default './downloads'.")
        DEFAULT_BASE_DIR = Path("./downloads")

except ImportError as e:  # Capture the specific error
    # Handle cases where relative imports might fail if run strangely
    logger.error(
        f"Failed to import necessary modules (db or config): {e}. Ensure PYTHONPATH includes the project root or 'src'."
    )
    print(f"Error: Failed to import necessary modules: {e}", file=sys.stderr)
    print(
        "Ensure you are running from the project root or 'src' is in PYTHONPATH.",
        file=sys.stderr,
    )
    sys.exit(1)  # Use sys.exit instead of raise

# --- End imports ---


# --- Typer App Initialization ---
app = typer.Typer(
    name="lessons-kb",
    help="Manage the Lessons Learned Knowledge Base.",
    add_completion=False,
    no_args_is_help=True,
)

# --- Configure Logging ---
logger.remove()
logger.add(
    sys.stderr,
    level="INFO",  # Default level for CLI
    format="<level>{level: <8}</level> | <cyan>{name}:{function}:{line}</cyan> - <level>{message}</level>",
)

# --- Database Path ---
# Correct DB filename
DEFAULT_LESSONS_DB_PATH = Path(DEFAULT_BASE_DIR) / "project_state.db"


# --- Helper to get DB connection (Synchronous) ---
def get_db_conn(db_path: Path) -> sqlite3.Connection: # Changed to def, type hint
    """Establishes and returns a synchronous SQLite connection."""
    try:
        # Ensure directory exists
        db_path.parent.mkdir(parents=True, exist_ok=True)
        logger.debug(f"Attempting to connect to DB at: {db_path}")
        conn = sqlite3.connect(db_path, timeout=10)  # Changed to sqlite3.connect
        conn.row_factory = sqlite3.Row  # Useful for accessing columns by name
        logger.debug(f"Successfully connected to DB: {db_path}")
        return conn
    except Exception as e:
        logger.critical(
            f"Failed to connect to database at {db_path}: {e}", exc_info=True
        )
        raise typer.Exit(code=1)


# --- CLI Commands (Synchronous) ---

@app.command("add", help="Add a new lesson to the knowledge base.")
def add_lesson_command( # Changed to def
    role: str = typer.Option(
        ...,
        "--role",
        "-r",
        help="Role performing the action (e.g., 'Planner', 'Senior Coder').",
    ),
    problem: str = typer.Option(
        ..., "--problem", "-p", help="Description of the problem encountered."
    ),
    solution: str = typer.Option(
        ..., "--solution", "-s", help="Description of the solution or workaround."
    ),
    tags: Optional[List[str]] = typer.Option(
        None, "--tag", "-t", help="Add a tag (can be used multiple times)."
    ),
    severity: Optional[str] = typer.Option(
        "INFO", "--severity", help="Severity level (INFO, WARN, ERROR, CRITICAL)."
    ),
    task: Optional[str] = typer.Option(
        None, "--task", help="Associated task name, if any."
    ),
    phase: Optional[str] = typer.Option(
        None, "--phase", help="Associated project phase, if any."
    ),
    context: Optional[str] = typer.Option(
        None, "--context", help="Additional context about the situation."
    ),
    example: Optional[str] = typer.Option(
        None,
        "--example",
        help="Code snippet or command example illustrating the lesson.",
    ),
    db_path: Path = typer.Option(
        DEFAULT_LESSONS_DB_PATH,
        "--db-path",
        help="Path to the lessons learned database file.",
        exists=False,
        file_okay=True,
        dir_okay=False,
        writable=True,
        resolve_path=True,
    ),
    verbose: bool = typer.Option(
        False, "--verbose", "-v", help="Enable debug logging."
    ),
):
    """CLI command handler for adding a lesson."""
    if verbose:
        logger.remove()
        logger.add(
            sys.stderr,
            level="DEBUG",
            format="<level>{level: <8}</level> | <cyan>{name}:{function}:{line}</cyan> - <level>{message}</level>",
        )
    logger.debug(f"Attempting to add lesson with provided arguments.")

    conn = None  # Initialize conn to None
    try:
        # Open connection ONCE
        conn = get_db_conn(db_path) # Removed await
        logger.debug(f"Opened DB connection for add operation: {db_path}")

        # Ensure table exists using the OPEN connection
        init_lessons_db( # Removed await
            db_path, existing_conn=conn
        )
        logger.debug("Ensured lessons_learned table exists.")

        # Create Pydantic model instance for validation and structure
        lesson_model = LessonLearned(
            role=role,
            problem=problem,
            solution=solution,
            tags=tags or [],
            severity=severity.upper() if severity else "INFO",
            task=task,
            phase=phase,
            context=context,
            example=example,
        )
        logger.debug(
            f"Lesson model created: {lesson_model.model_dump_json(exclude={'timestamp', 'example'} if example and len(example) > 100 else {'timestamp'})}"
        )

        new_id = add_lesson(conn, lesson_model)  # Removed await, Pass the connection
        if new_id is not None:
            print(f"Successfully added lesson with ID: {new_id}")
            logger.info(f"Successfully added lesson ID {new_id}")
        else:
            print("Error: Failed to add lesson to the database.", file=sys.stderr)
            logger.error("add_lesson function returned None, indicating failure.")
            raise typer.Exit(code=1)
    except ValueError as ve:  # Catch Pydantic validation errors
        logger.error(f"Invalid input data for lesson: {ve}")
        print(f"Error: Invalid input data - {ve}", file=sys.stderr)
        raise typer.Exit(code=1)
    except sqlite3.Error as db_err:  # Catch specific DB errors (changed from aiosqlite)
        logger.error(f"Database error during add operation: {db_err}", exc_info=True)
        print(f"Error: Database operation failed - {db_err}", file=sys.stderr)
        raise typer.Exit(code=1)
    except Exception as e:
        logger.error(
            f"An unexpected error occurred during add operation: {e}", exc_info=True
        )
        print(f"Error: An unexpected error occurred - {e}", file=sys.stderr)
        raise typer.Exit(code=1)
    finally:
        if conn:
            conn.close() # Removed await
            logger.debug("Closed DB connection for add operation.")


@app.command("find", help="Find lessons matching search criteria.")
def find_lessons_command( # Changed to def
    term: Optional[str] = typer.Option(
        None,
        "--term",
        "-s",
        help="Search term for problem, solution, context, or example fields.",
    ),
    tag: Optional[List[str]] = typer.Option(
        None, "--tag", "-t", help="Filter by tag (can be used multiple times)."
    ),
    role: Optional[str] = typer.Option(None, "--role", "-r", help="Filter by role."),
    limit: int = typer.Option(
        10,
        "--limit",
        "-l",
        help="Maximum number of results to return.",
    ),
    db_path: Path = typer.Option(
        DEFAULT_LESSONS_DB_PATH,
        "--db-path",
        help="Path to the lessons learned database file.",
        exists=True,
        file_okay=True,
        dir_okay=False,
        readable=True,
        resolve_path=True,
    ),
    verbose: bool = typer.Option(
        False, "--verbose", "-v", help="Enable debug logging."
    ),
):
    """CLI command handler for finding lessons."""
    if verbose:
        logger.remove()
        logger.add(
            sys.stderr,
            level="DEBUG",
            format="<level>{level: <8}</level> | <cyan>{name}:{function}:{line}</cyan> - <level>{message}</level>",
        )
    logger.debug(
        f"Searching lessons with: term='{term}', tags={tag}, role='{role}', limit={limit}"
    )

    conn = None  # Initialize conn to None
    try:
        # Open connection ONCE
        conn = get_db_conn(db_path) # Removed await
        logger.debug(f"Opened DB connection for find operation: {db_path}")

        # Ensure table exists? Less critical for find, but good practice
        init_lessons_db(db_path, existing_conn=conn) # Removed await
        logger.debug("Ensured lessons_learned table exists (or checked).")

        results = find_lessons( # Removed await
            db_conn=conn,
            search_term=term,
            tags=tag,
            role=role,
            limit=limit,
        )

        if not results:
            print("No lessons found matching the criteria.")
            logger.info("No matching lessons found.")
            return

        print(f"Found {len(results)} lesson(s):")
        print("-" * 20)
        # Pretty print results
        for lesson in results:
            ts_str = "N/A"
            # Ensure timestamp is datetime object before formatting
            timestamp_val = lesson.timestamp # Use intermediate variable
            if isinstance(timestamp_val, datetime.datetime):
                ts_str = (
                    timestamp_val.strftime("%Y-%m-%d %H:%M:%S %Z")
                    if timestamp_val.tzinfo
                    else timestamp_val.strftime("%Y-%m-%d %H:%M:%S UTC")
                )
            elif isinstance(timestamp_val, str):
                ts_str = timestamp_val
            elif timestamp_val is not None:
                ts_str = str(timestamp_val)

            print(f"ID: {lesson.id}")
            print(f"Timestamp: {ts_str}")
            print(f"Severity: {lesson.severity}")
            print(f"Role: {lesson.role}")
            if lesson.task:
                print(f"Task: {lesson.task}")
            if lesson.phase:
                print(f"Phase: {lesson.phase}")
            print(f"Problem: {lesson.problem}")
            print(f"Solution: {lesson.solution}")
            if lesson.tags:
                print(f"Tags: {', '.join(lesson.tags)}")
            if lesson.context:
                print(f"Context: {lesson.context}")
            if lesson.example:
                print(f"Example:\n{lesson.example}")
            print("-" * 20)

    except sqlite3.Error as db_err:  # Catch specific DB errors (changed from aiosqlite)
        logger.error(f"Database error during find operation: {db_err}", exc_info=True)
        print(f"Error: Database operation failed - {db_err}", file=sys.stderr)
        raise typer.Exit(code=1)
    except Exception as e:
        logger.error(
            f"An unexpected error occurred during find operation: {e}",
            exc_info=True,
        )
        print(f"Error: An unexpected error occurred - {e}", file=sys.stderr)
        raise typer.Exit(code=1)
    finally:
        if conn:
            conn.close() # Removed await
            logger.debug("Closed DB connection for find operation.")


# --- Standalone Test Function (Synchronous) ---
def _standalone_test(): # Changed to def
    """Runs a simple add and find test using a temporary DB."""
    logger.remove()
    logger.add(sys.stderr, level="DEBUG")
    test_db_path = Path("./temp_lessons_standalone.db")
    conn = None
    test_lesson_id = None
    logger.info(f"--- Running Standalone Test (DB: {test_db_path}) ---")

    try:
        # 1. Connect and Init
        conn = get_db_conn(test_db_path) # Removed await
        init_lessons_db(test_db_path, existing_conn=conn) # Removed await
        logger.info("Standalone: Connected and initialized temp DB.")

        # 2. Add a lesson
        test_lesson = LessonLearned(
            role="StandaloneTest",
            problem="Test Problem",
            solution="Test Solution",
            tags=["test", "standalone"],
            severity="INFO",
        )
        test_lesson_id = add_lesson(conn, test_lesson) # Removed await
        assert test_lesson_id is not None, "Failed to add test lesson"
        logger.info(f"Standalone: Added test lesson with ID: {test_lesson_id}")

        # 3. Find the added lesson by unique problem text
        found_lessons = find_lessons(conn, search_term="Test Problem", limit=1) # Removed await
        assert len(found_lessons) == 1, "Failed to find the added test lesson"
        assert found_lessons[0].id == test_lesson_id, "Found lesson ID mismatch"
        assert found_lessons[0].role == "StandaloneTest", "Found lesson role mismatch"
        logger.info(f"Standalone: Successfully found added test lesson: {found_lessons[0].id}")

        # 4. Find by tag
        found_by_tag = find_lessons(conn, tags=["standalone"], limit=1) # Removed await
        assert len(found_by_tag) == 1, "Failed to find lesson by tag 'standalone'"
        assert found_by_tag[0].id == test_lesson_id, "Tag search ID mismatch"
        logger.info("Standalone: Successfully found lesson by tag.")

        logger.success("--- Standalone Test PASSED ---")

    except Exception as e:
        logger.error(f"--- Standalone Test FAILED: {e} ---", exc_info=True)
        raise # Re-raise after logging

    finally:
        if conn:
            conn.close() # Removed await
            logger.debug("Standalone: Closed temp DB connection.")
        # Clean up the temp database file
        if test_db_path.exists():
            try:
                test_db_path.unlink()
                logger.info(f"Standalone: Removed temp DB: {test_db_path}")
            except OSError as e_unlink:
                logger.error(f"Standalone: Failed to remove temp DB: {e_unlink}")

# --- Main Guard for Typer app execution ---
if __name__ == "__main__":
    # To run the standalone test: execute this script directly
    # e.g., python src/mcp_doc_retriever/lessons_cli.py
    # To run the Typer CLI: execute via runner or without the main guard check
    # e.g., uv run python src/mcp_doc_retriever/lessons_cli.py add ...
    # _standalone_test() # Comment this out to enable Typer app execution
    app() # Call the typer app directly
