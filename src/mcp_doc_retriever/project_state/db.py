# src/mcp_doc_retriever/project_state/db.py
"""
Handles database interactions for project state, specifically lessons learned.
Interacts with the lessons_learned.db SQLite database.
"""

import aiosqlite
import json
import sys
import asyncio
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
        return None

    def _iso_to_datetime(iso_str: Optional[str]) -> Optional[datetime]:
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
async def init_lessons_db(db_path: Path):
    """Initializes the SQLite database AND CREATES THE TABLE at the specified path."""
    conn = None
    try:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        logger.info(f"Initializing lessons database connection to: {db_path}")
        conn = await aiosqlite.connect(db_path)
        async with conn.cursor() as cursor:
            # Create lessons_learned table IF NOT EXISTS
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
                    tags TEXT, -- Store tags as JSON array string
                    context TEXT,
                    example TEXT
                )
            """)
        await conn.commit()
        logger.info(
            f"Lessons database initialized and table 'lessons_learned' ensured at {db_path}."
        )
    except Exception as e:
        logger.critical(
            f"Failed to initialize lessons database at {db_path}: {e}", exc_info=True
        )
        # Depending on requirements, you might want to raise this
        # raise
    finally:
        if conn:
            try:
                await conn.close()
                logger.debug(
                    f"Closed temporary connection used for initializing lessons DB: {db_path}"
                )
            except Exception as e:
                logger.error(f"Error closing temporary lessons DB init connection: {e}")


# --- Database CRUD Functions for Lessons (Accept connection) ---
async def add_lesson(
    db_conn: aiosqlite.Connection, lesson: LessonLearned
) -> Optional[int]:
    """Adds a new lesson learned record and returns the new row ID, or None on failure."""
    if not db_conn:
        logger.error("Database connection not provided, cannot add lesson.")
        return None
    try:
        lesson_data = lesson.model_dump(exclude={"id"})  # Exclude ID for insert
        async with db_conn.cursor() as cursor:
            await cursor.execute(
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
        await db_conn.commit()
        logger.info(f"Added lesson learned (ID: {new_id}) from role: {lesson.role}")
        return new_id
    except Exception as e:
        logger.error(f"Failed to add lesson learned: {e}", exc_info=True)
        return None


async def update_lesson(
    db_conn: aiosqlite.Connection, lesson_id: int, lesson_update_data: LessonLearned
) -> bool:
    """Updates an existing lesson learned record identified by its ID."""
    if not db_conn:
        logger.error("Database connection not provided, cannot update lesson.")
        return False
    if not lesson_id:
        logger.error("Lesson ID is required for update.")
        return False
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

        async with db_conn.cursor() as cursor:
            await cursor.execute(sql, tuple(params))
            rowcount = cursor.rowcount
        await db_conn.commit()

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


async def delete_lesson(db_conn: aiosqlite.Connection, lesson_id: int) -> bool:
    """Deletes a lesson learned record by its ID."""
    if not db_conn:
        logger.error("Database connection not provided, cannot delete lesson.")
        return False
    if not lesson_id:
        logger.error("Lesson ID is required for deletion.")
        return False
    try:
        async with db_conn.cursor() as cursor:
            await cursor.execute(
                "DELETE FROM lessons_learned WHERE id = ?", (lesson_id,)
            )
            rowcount = cursor.rowcount
        await db_conn.commit()
        if rowcount > 0:
            logger.info(f"Successfully deleted lesson ID: {lesson_id}")
            return True
        else:
            logger.warning(f"Lesson ID {lesson_id} not found for deletion.")
            return False
    except Exception as e:
        logger.error(f"Failed to delete lesson ID {lesson_id}: {e}", exc_info=True)
        return False


async def find_lessons(
    db_conn: aiosqlite.Connection,
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
    try:
        async with db_conn.cursor() as cursor:
            await cursor.execute(query, tuple(params))
            rows = await cursor.fetchall()

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


# --- Standalone Execution / Example ---
# if __name__ == "__main__":

#     async def _example_usage():
#         logger.remove()
#         logger.add(sys.stderr, level="DEBUG")

#         # Use a distinct name for the example DB file
#         example_db_path = Path("./temp_lessons_learned_example.db")
#         conn = None
#         try:
#             # Initialize using the function from this module
#             await init_lessons_db(example_db_path)

#             # Connect for CRUD operations
#             logger.info(
#                 f"Connecting to temporary DB for CRUD example: {example_db_path}"
#             )
#             conn = await aiosqlite.connect(example_db_path)

#             # Add lessons
#             lesson1_data = LessonLearned(
#                 role="Tester",
#                 problem="Initial problem",
#                 solution="Initial solution",
#                 tags=["init", "tag1"],
#             )
#             new_id1 = await add_lesson(conn, lesson1_data)
#             logger.info(f"Added lesson 1 with ID: {new_id1}")
#             lesson2_data = LessonLearned(
#                 role="Coder",
#                 problem="Coding issue",
#                 solution="Code fix",
#                 tags=["code", "fix"],
#             )
#             new_id2 = await add_lesson(conn, lesson2_data)
#             logger.info(f"Added lesson 2 with ID: {new_id2}")

#             # Find lessons
#             logger.info("\nFinding all lessons:")
#             found_all = await find_lessons(conn, limit=5)
#             for l in found_all:
#                 print(f"  - Found: {l.model_dump_json(indent=1)}")
#                 assert len(found_all) == 2

#             # Update a lesson
#             if new_id1:
#                 logger.info(f"\nUpdating lesson ID {new_id1}:")
#                 update_data = LessonLearned(
#                     role="Tester Updated",
#                     problem="Updated Problem",
#                     solution="Updated Solution",
#                     tags=["init", "tag1", "updated"],
#                     severity="WARN",
#                 )
#                 update_success = await update_lesson(conn, new_id1, update_data)
#                 logger.info(f"Update successful: {update_success}")
#                 assert update_success
#                 logger.info("\nFinding updated lesson:")
#                 found_updated = await find_lessons(conn, search_term="Updated Problem")
#                 assert len(found_updated) == 1
#                 assert found_updated[0].role == "Tester Updated"
#                 assert found_updated[0].severity == "WARN"
#                 assert "updated" in found_updated[0].tags
#                 for l in found_updated:
#                     print(f"  - Found Updated: {l.model_dump_json(indent=1)}")

#             # Delete a lesson
#             if new_id2:
#                 logger.info(f"\nDeleting lesson ID {new_id2}:")
#                 delete_success = await delete_lesson(conn, new_id2)
#                 logger.info(f"Delete successful: {delete_success}")
#                 assert delete_success
#                 logger.info("\nFinding all lessons after delete:")
#                 found_after_delete = await find_lessons(conn, limit=5)
#                 assert len(found_after_delete) == 1
#                 assert found_after_delete[0].id == new_id1
#                 for l in found_after_delete:
#                     print(f"  - Found After Delete: {l.model_dump_json(indent=1)}")

#             print("\n✓ Standalone CRUD tests PASSED.")

#         except Exception as e:
#             logger.error(f"Error during example usage: {e}", exc_info=True)
#             print("\n✗ Standalone CRUD tests FAILED.")
#         finally:
#             if conn:
#                 await conn.close()
#                 logger.info("Closed temporary DB connection.")
#             if example_db_path.exists():
#                 try:
#                     example_db_path.unlink()
#                     logger.info(f"Removed temporary DB: {example_db_path}")
#                 except OSError as e:
#                     logger.error(f"Failed to remove temporary DB: {e}")

#     try:
#         asyncio.run(_example_usage())
#     except KeyboardInterrupt:
#         print("Example interrupted.")
