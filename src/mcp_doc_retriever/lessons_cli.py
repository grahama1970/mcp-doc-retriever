# src/mcp_doc_retriever/lessons_cli.py
"""
Command Line Interface for managing the Lessons Learned ArangoDB collection.

Allows agents (or humans) to add and find lessons learned records
without needing the main FastAPI application to be running. Uses python-arango.
Connects to ArangoDB using environment variables (ARANGO_HOST, ARANGO_USER, ARANGO_PASSWORD, ARANGO_DB).
"""

import typer
import sys
import json
from pathlib import Path
from typing import List, Optional, Dict, Any
from loguru import logger
import datetime
from arango.exceptions import ArangoServerError, ServerConnectionError as ArangoConnectionError # Import Arango exceptions

# --- Import ArangoDB functions ---
try:
    from mcp_doc_retriever.project_state.arango_db import (
        add_lesson as arango_add_lesson,
        find_lessons as arango_find_lessons,
    )
except ImportError as e:
    logger.critical(
        f"Failed to import ArangoDB functions: {e}. Ensure 'src' is in PYTHONPATH."
    )
    print(f"Error: Failed to import ArangoDB functions: {e}", file=sys.stderr)
    sys.exit(1)

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

# --- CLI Commands ---
# Database connection is now handled within the arango_db module via env vars

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
    verbose: bool = typer.Option(
        False, "--verbose", "-v", help="Enable debug logging."
    ),
):
    """CLI command handler for adding a lesson to ArangoDB."""
    if verbose:
        logger.remove()
        logger.add(
            sys.stderr,
            level="DEBUG",
            format="<level>{level: <8}</level> | <cyan>{name}:{function}:{line}</cyan> - <level>{message}</level>",
        )
    logger.debug(f"Attempting to add lesson with provided arguments.")

    try:
        # Prepare lesson data dictionary
        lesson_data: Dict[str, Any] = {
            "role": role,
            "problem": problem,
            "solution": solution,
            "tags": tags or [],
            "severity": severity.upper() if severity else "INFO",
            "task": task,
            "phase": phase,
            "context": context,
            "example": example,
            # Timestamp is added automatically by arango_add_lesson if not present
        }
        logger.debug(f"Prepared lesson data for ArangoDB: { {k:v for k,v in lesson_data.items() if k != 'example' or not v or len(v) < 100} }") # Log safely

        # Call the ArangoDB add function
        meta = arango_add_lesson(lesson_data)
        print(f"Successfully added lesson with ArangoDB key: {meta['_key']}")
        logger.info(f"Successfully added lesson. ArangoDB meta: {meta}")

    except (ArangoConnectionError, ArangoServerError) as db_err:
        logger.error(f"ArangoDB error during add operation: {db_err}", exc_info=True)
        print(f"Error: Database operation failed - {db_err}", file=sys.stderr)
        raise typer.Exit(code=1)
    except ValueError as ve: # Catch validation errors from arango_db module
        logger.error(f"Invalid input data for lesson: {ve}")
        print(f"Error: Invalid input data - {ve}", file=sys.stderr)
        raise typer.Exit(code=1)
    except Exception as e:
        logger.error(
            f"An unexpected error occurred during add operation: {e}", exc_info=True
        )
        print(f"Error: An unexpected error occurred - {e}", file=sys.stderr)
        raise typer.Exit(code=1)


@app.command("find", help="Find lessons matching search criteria.")
def find_lessons_command( # Changed to def
    keyword: Optional[List[str]] = typer.Option( # Changed from term to keyword, allow multiple
        None,
        "--keyword",
        "-k", # Changed shortcut
        help="Search keyword(s) for text fields (problem, solution, etc.). Use multiple times.",
    ),
    tag: Optional[List[str]] = typer.Option(
        None, "--tag", "-t", help="Filter by tag (can be used multiple times, exact match)."
    ),
    # Role filtering removed as it's not directly supported by the basic Arango search view function
    limit: int = typer.Option(
        10,
        "--limit",
        "-l",
        help="Maximum number of results to return.",
    ),
    verbose: bool = typer.Option(
        False, "--verbose", "-v", help="Enable debug logging."
    ),
):
    """CLI command handler for finding lessons in ArangoDB."""
    if verbose:
        logger.remove()
        logger.add(
            sys.stderr,
            level="DEBUG",
            format="<level>{level: <8}</level> | <cyan>{name}:{function}:{line}</cyan> - <level>{message}</level>",
        )
    logger.debug(
        f"Searching lessons with: keywords={keyword}, tags={tag}, limit={limit}"
    )

    try:
        # Call the ArangoDB find function
        results = arango_find_lessons(
            keywords=keyword,
            tags=tag,
            limit=limit,
        )

        if not results:
            print("No lessons found matching the criteria.")
            logger.info("No matching lessons found.")
            return

        print(f"Found {len(results)} lesson(s):")
        print("-" * 20)
        # Pretty print results (ArangoDB returns dicts)
        for lesson in results:
            # Safely get values from the dictionary
            lesson_id = lesson.get('_id', 'N/A')
            ts_str = lesson.get('timestamp', 'N/A') # Should be ISO string
            severity = lesson.get('severity', 'N/A')
            role_val = lesson.get('role', 'N/A') # Renamed variable
            task = lesson.get('task')
            phase = lesson.get('phase')
            problem = lesson.get('problem', 'N/A')
            solution = lesson.get('solution', 'N/A')
            tags_list = lesson.get('tags') # Renamed variable
            context = lesson.get('context')
            example = lesson.get('example')

            print(f"ID: {lesson_id}")
            print(f"Timestamp: {ts_str}")
            print(f"Severity: {severity}")
            print(f"Role: {role_val}")
            if task:
                print(f"Task: {task}")
            if phase:
                print(f"Phase: {phase}")
            print(f"Problem: {problem}")
            print(f"Solution: {solution}")
            if tags_list:
                print(f"Tags: {', '.join(tags_list)}")
            if context:
                print(f"Context: {context}")
            if example:
                print(f"Example:\n{example}")
            print("-" * 20)

    except (ArangoConnectionError, ArangoServerError) as db_err:
        logger.error(f"ArangoDB error during find operation: {db_err}", exc_info=True)
        print(f"Error: Database operation failed - {db_err}", file=sys.stderr)
        raise typer.Exit(code=1)
    except Exception as e:
        logger.error(
            f"An unexpected error occurred during find operation: {e}",
            exc_info=True,
        )
        print(f"Error: An unexpected error occurred - {e}", file=sys.stderr)
        raise typer.Exit(code=1)


# --- Main Guard for Typer app execution ---
if __name__ == "__main__":
    # Standalone test function removed, use arango_db.py's main block for testing
    app() # Call the typer app directly
