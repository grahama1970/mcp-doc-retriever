# src/mcp_doc_retriever/lessons_cli.py
"""
Command Line Interface for managing the Lessons Learned SQLite database.

Allows agents (or humans) to add and find lessons learned records
without needing the main FastAPI application to be running.
"""

import asyncio
import typer
import sys
import json
from pathlib import Path
from typing import List, Optional
import aiosqlite
from loguru import logger

# --- Need to adjust imports based on project structure ---
# Assuming utils.py and project_state/db.py are importable
try:
    # Assumes running from project root or src is in PYTHONPATH
    from mcp_doc_retriever.project_state.db import (
        LessonLearned,
        init_lessons_db,
        add_lesson,
        find_lessons,
        # Import update/delete if needed for CLI later
    )
    import mcp_doc_retriever.config as config
except ImportError as e: # Capture the specific error
    # Handle cases where relative imports might fail if run strangely
    logger.error(
        f"Failed to import necessary modules: {e}. Ensure PYTHONPATH includes the project root or 'src'."
    )
    # Provide dummy functions/classes to allow script to load for --help, maybe?
    config = type(
        "obj", (object,), {"DOWNLOAD_BASE_DIR": "./downloads"}
    )()  # Mock config

    class LessonLearned:
        pass  # Mock model

    async def init_lessons_db(*args, **kwargs):
        logger.error("DB init unavailable")
        raise SystemExit(1)

    async def add_lesson(*args, **kwargs):
        logger.error("DB add unavailable")
        raise SystemExit(1)

    async def find_lessons(*args, **kwargs):
        logger.error("DB find unavailable")
        return []
    raise SystemExit(1) # Exit if imports fail

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

# --- Database Path (Consistent with main.py) ---
# Get base dir from config, default to ./downloads if config import fails
DEFAULT_BASE_DIR = getattr(config, "DOWNLOAD_BASE_DIR", "./downloads")
# Correct DB filename to match .roorules
DEFAULT_LESSONS_DB_PATH = Path(DEFAULT_BASE_DIR) / "project_state.db"


# --- Helper to get DB connection ---
async def get_db_conn(db_path: Path) -> aiosqlite.Connection:
    """Establishes and returns an async SQLite connection."""
    try:
        # Ensure directory exists
        db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = await aiosqlite.connect(db_path)
        conn.row_factory = aiosqlite.Row  # Useful for accessing columns by name
        return conn
    except Exception as e:
        logger.critical(
            f"Failed to connect to database at {db_path}: {e}", exc_info=True
        )
        raise typer.Exit(code=1)


# --- CLI Commands ---


@app.command("add", help="Add a new lesson to the knowledge base.")
def add_lesson_command(
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

    async def _add_async():
        # Ensure DB and table exist before trying to add
        # Note: init_lessons_db connects, creates table, commits, and closes.
        await init_lessons_db(db_path)

        # Open connection for the add operation
        conn = await get_db_conn(db_path)
        try:
            # Create Pydantic model instance for validation and structure
            lesson_model = LessonLearned(
                role=role,
                problem=problem,
                solution=solution,
                tags=tags or [],
                severity=severity.upper()
                if severity
                else "INFO",  # Ensure uppercase for Literal
                task=task,
                phase=phase,
                context=context,
                example=example,
            )
            logger.debug(
                f"Lesson model created: {lesson_model.model_dump_json(exclude={'timestamp'})}"
            )  # Exclude timestamp for cleaner debug

            new_id = await add_lesson(conn, lesson_model)  # Pass the connection
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
        except Exception as e:
            logger.error(
                f"An unexpected error occurred during add operation: {e}", exc_info=True
            )
            print(f"Error: An unexpected error occurred - {e}", file=sys.stderr)
            raise typer.Exit(code=1)
        finally:
            if conn:
                await conn.close()
                logger.debug("Closed DB connection for add operation.")

    asyncio.run(_add_async())


@app.command("find", help="Find lessons matching search criteria.")
def find_lessons_command(
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
        5, "--limit", "-l", help="Maximum number of results to return."
    ),
    db_path: Path = typer.Option(
        DEFAULT_LESSONS_DB_PATH,
        "--db-path",
        help="Path to the lessons learned database file.",
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

    async def _find_async():
        # Ensure DB and table exist before querying
        await init_lessons_db(db_path)

        conn = await get_db_conn(db_path)
        try:
            results = await find_lessons(
                db_conn=conn,  # Pass connection
                search_term=term,
                tags=tag,
                role=role,
                limit=limit,
            )

            if not results:
                print("No lessons found matching the criteria.")
                logger.info("No matching lessons found.")
                return  # Exit cleanly

            print(f"Found {len(results)} lesson(s):")
            print("-" * 20)
            # Pretty print results
            for lesson in results:
                print(f"ID: {lesson.id}")
                print(f"Timestamp: {lesson.timestamp.strftime('%Y-%m-%d %H:%M:%S %Z')}")
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

        except Exception as e:
            logger.error(
                f"An unexpected error occurred during find operation: {e}",
                exc_info=True,
            )
            print(f"Error: An unexpected error occurred - {e}", file=sys.stderr)
            raise typer.Exit(code=1)
        finally:
            if conn:
                await conn.close()
                logger.debug("Closed DB connection for find operation.")

    asyncio.run(_find_async())


# --- Main Execution Guard ---
if __name__ == "__main__":
    app()
