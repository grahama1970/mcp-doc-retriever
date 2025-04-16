# src/mcp_doc_retriever/project_state/db.py
"""
Handles database interactions for project state, specifically lessons learned.
Interacts with the lessons_learned.db SQLite database using standard sqlite3.
"""

import sqlite3  # Changed from aiosqlite
import json
import sys
# Removed asyncio import
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional, Dict, Any, Tuple, Literal
from pydantic import BaseModel, Field, ConfigDict
from loguru import logger

# --- Import shared helper functions ---
try:
    from mcp_doc_retriever.utils import _datetime_to_iso, _iso_to_datetime
except ImportError:
    logger.error(
        "Could not import datetime helpers from utils. Ensure utils.py exists and contains them."
    )

    def _datetime_to_iso(dt: Optional[datetime]) -> Optional[str]:
        # Basic fallback
        return dt.isoformat() if dt else None

    def _iso_to_datetime(iso_str: Optional[str]) -> Optional[datetime]:
        # Basic fallback
        try:
            return datetime.fromisoformat(iso_str) if iso_str else None
        except (ValueError, TypeError):
             return None
# --- End helper import ---


# --- Pydantic Model for Lessons ---
class LessonLearned(BaseModel):
    """Represents a lesson learned record."""

    id: Optional[int] = (
        None  # Populated after reading from DB, REQUIRED for update/delete
    )
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    severity: Optional[Literal["INFO", "WARN", "ERROR", "CRITICAL"]] = "INFO"
    role: str
    task: Optional[str] = None
    phase: Optional[str] = None
    problem: str
    solution: str
    tags: List[str] = Field(default_factory=list)
    context: Optional[str] = None
    example: Optional[str] = None

    model_config = ConfigDict(from_attributes=True)


# --- Database Initialization Function ---
def init_lessons_db(db_path: Path, existing_conn: Optional[sqlite3.Connection] = None): # Changed to def, type hint to sqlite3.Connection
    """
    Ensures the lessons_learned table exists.
    If existing_conn is provided, uses it. Otherwise, creates a temporary connection.
    """
    conn_to_use = existing_conn
    is_temp_conn = False
    cursor = None
    try:
        if conn_to_use is None:
            db_path.parent.mkdir(parents=True, exist_ok=True)
            logger.info(f"Creating temporary connection to initialize lessons database: {db_path}")
            conn_to_use = sqlite3.connect(db_path, timeout=10) # Changed to sqlite3.connect
            is_temp_conn = True
        else:
             logger.debug(f"Using existing connection to ensure lessons table exists in {db_path}")

        cursor = conn_to_use.cursor()
        # Create lessons_learned table IF NOT EXISTS
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS lessons_learned (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                severity TEXT,
                role TEXT NOT NULL,
                task TEXT,
                phase TEXT,
                problem TEXT NOT NULL,
                solution TEXT NOT NULL,
                tags TEXT, -- Store tags as JSON array string
                context TEXT,
                example TEXT
            )
        """)
        conn_to_use.commit() # Commit on the connection used
        logger.info(
            f"Table 'lessons_learned' ensured in database {db_path}."
        )
    except Exception as e:
        logger.critical(
            f"Failed to ensure lessons_learned table in {db_path}: {e}", exc_info=True
        )
        # Raise the exception to signal failure
        raise
    finally:
        if cursor:
             cursor.close() # Ensure cursor is closed
        # Only close the connection if we created a temporary one
        if is_temp_conn and conn_to_use:
            try:
                conn_to_use.close() # Removed await
                logger.debug(
                    f"Closed temporary connection used for initializing lessons DB: {db_path}"
                )
            except Exception as e:
                logger.error(f"Error closing temporary lessons DB init connection: {e}")


# --- Database CRUD Functions for Lessons (Accept connection) ---
def add_lesson( # Changed to def
    db_conn: sqlite3.Connection, lesson: LessonLearned # Changed type hint
) -> Optional[int]:
    """Adds a new lesson learned record and returns the new row ID, or None on failure."""
    if not db_conn:
        logger.error("Database connection not provided, cannot add lesson.")
        return None
    cursor = None # Initialize cursor
    try:
        lesson_data = lesson.model_dump(exclude={"id"})  # Exclude ID for insert
        cursor = db_conn.cursor() # Changed from async with
        cursor.execute( # Removed await
            """
            INSERT INTO lessons_learned
            (timestamp, severity, role, task, phase, problem, solution, tags, context, example)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                _datetime_to_iso(lesson_data["timestamp"]),
                lesson_data["severity"],
                lesson_data["role"],
                lesson_data["task"],
                lesson_data["phase"],
                lesson_data["problem"],
                lesson_data["solution"],
                json.dumps(lesson_data["tags"]),
                lesson_data["context"],
                lesson_data["example"],
            ),
        )
        new_id = cursor.lastrowid
        db_conn.commit() # Removed await
        logger.info(f"Added lesson learned (ID: {new_id}) from role: {lesson.role}")
        return new_id
    except Exception as e:
        logger.error(f"Failed to add lesson learned: {e}", exc_info=True)
        return None
    finally:
        if cursor:
            cursor.close() # Ensure cursor is closed


def update_lesson( # Changed to def
    db_conn: sqlite3.Connection, lesson_id: int, lesson_update_data: LessonLearned # Changed type hint
) -> bool:
    """Updates an existing lesson learned record identified by its ID."""
    if not db_conn:
        logger.error("Database connection not provided, cannot update lesson.")
        return False
    if not lesson_id:
        logger.error("Lesson ID is required for update.")
        return False
    cursor = None # Initialize cursor
    try:
        update_data = lesson_update_data.model_dump(exclude={"id"}, exclude_unset=True)
        if not update_data:
            logger.warning(f"No fields provided to update for lesson ID {lesson_id}.")
            return True

        set_parts = []
        params = []
        for key, value in update_data.items():
            set_parts.append(f"{key} = ?")
            if key == "tags":
                params.append(json.dumps(value))
            elif key == "timestamp":
                params.append(_datetime_to_iso(value))
            else:
                params.append(value)

        set_clause = ", ".join(set_parts)
        sql = f"UPDATE lessons_learned SET {set_clause} WHERE id = ?"
        params.append(lesson_id)

        cursor = db_conn.cursor() # Changed from async with
        cursor.execute(sql, tuple(params)) # Removed await
        rowcount = cursor.rowcount
        db_conn.commit() # Removed await

        if rowcount > 0:
            logger.info(
                f"Successfully updated lesson ID: {lesson_id}. Fields updated: {list(update_data.keys())}"
            )
            return True
        else:
            logger.warning(
                f"Lesson ID {lesson_id} not found or no changes applied during update."
            )
            return False
    except Exception as e:
        logger.error(f"Failed to update lesson ID {lesson_id}: {e}", exc_info=True)
        return False
    finally:
        if cursor:
            cursor.close() # Ensure cursor is closed


def delete_lesson(db_conn: sqlite3.Connection, lesson_id: int) -> bool: # Changed to def, type hint
    """Deletes a lesson learned record by its ID."""
    if not db_conn:
        logger.error("Database connection not provided, cannot delete lesson.")
        return False
    if not lesson_id:
        logger.error("Lesson ID is required for deletion.")
        return False
    cursor = None # Initialize cursor
    try:
        cursor = db_conn.cursor() # Changed from async with
        cursor.execute( # Removed await
            "DELETE FROM lessons_learned WHERE id = ?", (lesson_id,)
        )
        rowcount = cursor.rowcount
        db_conn.commit() # Removed await
        if rowcount > 0:
            logger.info(f"Successfully deleted lesson ID: {lesson_id}")
            return True
        else:
            logger.warning(f"Lesson ID {lesson_id} not found for deletion.")
            return False
    except Exception as e:
        logger.error(f"Failed to delete lesson ID {lesson_id}: {e}", exc_info=True)
        return False
    finally:
        if cursor:
            cursor.close() # Ensure cursor is closed


def find_lessons( # Changed to def
    db_conn: sqlite3.Connection, # Changed type hint
    search_term: Optional[str] = None,
    tags: Optional[List[str]] = None,
    role: Optional[str] = None,
    limit: int = 10,
) -> List[LessonLearned]:
    """Finds lessons learned matching criteria from the lessons_learned table."""
    if not db_conn:
        logger.error("Database connection not provided, cannot find lessons.")
        return []

    conditions: List[str] = []
    params: List[Any] = []

    if search_term:
        conditions.append(
            "(problem LIKE ? OR solution LIKE ? OR context LIKE ? OR example LIKE ?)"
        )
        term_like = f"%{search_term}%"
        params.extend([term_like, term_like, term_like, term_like])
    if tags:
        for tag in tags:
            if isinstance(tag, str):
                conditions.append("tags LIKE ?")
                params.append(
                    f'%"{tag}"%'
                )  # Look for the tag quoted within the JSON array string
            else:
                logger.warning(f"Ignoring non-string tag during search: {tag}")
    if role:
        conditions.append("role = ?")
        params.append(role)

    query = "SELECT id, timestamp, severity, role, task, phase, problem, solution, tags, context, example FROM lessons_learned"
    if conditions:
        query += " WHERE " + " AND ".join(conditions)
    query += " ORDER BY timestamp DESC LIMIT ?"
    params.append(limit)

    results: List[LessonLearned] = []
    cursor = None # Initialize cursor
    try:
        # Use a context manager for the cursor for standard sqlite3
        with db_conn.cursor() as cursor:
            # Set row factory *after* creating cursor for standard sqlite3
            cursor.row_factory = sqlite3.Row
            cursor.execute(query, tuple(params)) # Removed await
            rows = cursor.fetchall() # Removed await

        logger.debug(
            f"Fetched {len(rows)} rows from lessons_learned table."
        )  # Debug fetch

        for row in rows:
            lesson_id_for_logging = "UNKNOWN"  # Default for logging if row access fails
            try:
                # Access ID early for logging, handle if row is None (unlikely)
                if row:
                    lesson_id_for_logging = row["id"]
                else:
                    logger.warning("Encountered a None row during iteration.")
                    continue  # Skip None rows

                # Now proceed with processing
                row_data = dict(row)
                logger.debug(
                    f"Processing row ID {lesson_id_for_logging}: {row_data}"
                )  # Debug row data

                tags_json = row_data.get("tags")
                parsed_tags = []
                if tags_json:
                    try:
                        parsed_tags = json.loads(tags_json)
                        if not isinstance(parsed_tags, list):
                            logger.warning(
                                f"Parsed 'tags' is not a list for lesson ID {lesson_id_for_logging}, defaulting to empty list. Value: {parsed_tags}"
                            )
                            parsed_tags = []
                    except (json.JSONDecodeError, TypeError) as json_err:
                        logger.warning(
                            f"Could not parse 'tags' JSON for lesson ID {lesson_id_for_logging}, defaulting to empty list. Value: {tags_json}. Error: {json_err}"
                        )
                        parsed_tags = []
                row_data["tags"] = parsed_tags

                row_data["timestamp"] = _iso_to_datetime(row_data.get("timestamp"))

                # --- Explicitly log before validation ---
                logger.debug(
                    f"Attempting model_validate for lesson ID {lesson_id_for_logging} with data: {row_data}"
                )
                validated_lesson = LessonLearned.model_validate(row_data)
                results.append(validated_lesson)
                logger.debug(
                    f"Successfully validated and added lesson ID {lesson_id_for_logging} to results."
                )

            except Exception as parse_err:  # Catch Pydantic validation errors too
                # --- CORRECTED LOGGING: Use lesson_id_for_logging ---
                logger.warning(
                    f"Skipping lesson ID {lesson_id_for_logging} due to validation/parsing error: {parse_err}",
                    exc_info=True,
                )  # Add traceback for parsing errors
                # --- END CORRECTION ---
                continue  # Skip this record if parsing/validation fails
        logger.info(
            f"Processed {len(rows)} rows. Returning {len(results)} valid lessons matching criteria."
        )
        return results
    except Exception as e:
        logger.error(
            f"Failed during find_lessons database query or processing: {e}",
            exc_info=True,
        )
        return []
    # No finally needed for cursor if using 'with' context manager

# --- Standalone Execution / Example (Synchronous Version) ---
# Note: The original async example is commented out below the sync version
def _example_usage_sync():
    logger.remove()
    logger.add(sys.stderr, level="DEBUG")

    example_db_path = Path("./temp_lessons_learned_sync_example.db")
    conn = None
    try:
        # Initialize using the function from this module
        init_lessons_db(example_db_path)

        # Connect for CRUD operations
        logger.info(
            f"Connecting to temporary DB for CRUD example: {example_db_path}"
        )
        conn = sqlite3.connect(example_db_path, timeout=10)

        # Add lessons
        lesson1_data = LessonLearned(
            role="SyncTester",
            problem="Sync problem",
            solution="Sync solution",
            tags=["sync", "tag1"],
        )
        new_id1 = add_lesson(conn, lesson1_data)
        logger.info(f"Added sync lesson 1 with ID: {new_id1}")
        lesson2_data = LessonLearned(
            role="SyncCoder",
            problem="Sync coding issue",
            solution="Sync code fix",
            tags=["sync_code", "fix"],
        )
        new_id2 = add_lesson(conn, lesson2_data)
        logger.info(f"Added sync lesson 2 with ID: {new_id2}")

        # Find lessons
        logger.info("\nFinding all sync lessons:")
        found_all = find_lessons(conn, limit=5)
        for l in found_all:
            print(f"  - Found Sync: {l.model_dump_json(indent=1)}")
        assert len(found_all) == 2

        # Update a lesson
        if new_id1:
            logger.info(f"\nUpdating sync lesson ID {new_id1}:")
            update_data = LessonLearned(
                role="SyncTester Updated",
                problem="Updated Sync Problem",
                solution="Updated Sync Solution",
                tags=["sync", "tag1", "updated"],
                severity="WARN",
            )
            update_success = update_lesson(conn, new_id1, update_data)
            logger.info(f"Sync Update successful: {update_success}")
            assert update_success
            logger.info("\nFinding updated sync lesson:")
            found_updated = find_lessons(conn, search_term="Updated Sync Problem")
            assert len(found_updated) == 1
            assert found_updated[0].role == "SyncTester Updated"
            assert found_updated[0].severity == "WARN"
            assert "updated" in found_updated[0].tags
            for l in found_updated:
                print(f"  - Found Updated Sync: {l.model_dump_json(indent=1)}")

        # Delete a lesson
        if new_id2:
            logger.info(f"\nDeleting sync lesson ID {new_id2}:")
            delete_success = delete_lesson(conn, new_id2)
            logger.info(f"Sync Delete successful: {delete_success}")
            assert delete_success
            logger.info("\nFinding all sync lessons after delete:")
            found_after_delete = find_lessons(conn, limit=5)
            assert len(found_after_delete) == 1
            assert found_after_delete[0].id == new_id1
            for l in found_after_delete:
                print(f"  - Found Sync After Delete: {l.model_dump_json(indent=1)}")

        print("\n✓ Standalone Synchronous CRUD tests PASSED.")

    except Exception as e:
        logger.error(f"Error during sync example usage: {e}", exc_info=True)
        print("\n✗ Standalone Synchronous CRUD tests FAILED.")
    finally:
        if conn:
            conn.close()
            logger.info("Closed temporary sync DB connection.")
        if example_db_path.exists():
            try:
                example_db_path.unlink()
                logger.info(f"Removed temporary sync DB: {example_db_path}")
            except OSError as e:
                logger.error(f"Failed to remove temporary sync DB: {e}")

if __name__ == "__main__":
    try:
        _example_usage_sync()
    except KeyboardInterrupt:
        print("Example interrupted.")
    except Exception:
        # Error already logged in _example_usage_sync
        sys.exit(1)

# --- Original Async Example (Commented Out) ---
# if __name__ == "__main__":
#     async def _example_usage():
#         # ... (original async example code) ...
#     try:
#         asyncio.run(_example_usage())
#     except KeyboardInterrupt:
#         print("Example interrupted.")
