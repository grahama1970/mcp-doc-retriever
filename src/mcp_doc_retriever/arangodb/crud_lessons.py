# src/mcp_doc_retriever/arangodb/crud_lessons.py
"""
ArangoDB CRUD Operations Module for Lesson Learned Vertices.

Description:
Provides functions for Create, Read, Update, and Delete (CRUD) operations
on the 'lessons_learned' vertex collection in ArangoDB. Handles automatic
timestamping, UUID key generation, embedding generation/update, and optional
cleanup of related edges when deleting lessons.

This file focuses specifically on the Lesson vertices. See related files for
Relationship (Edge) CRUD and Search operations.

Key Features:
- Idempotent delete operations (using ignore_missing=True).
- Automatic embedding generation via embedding_utils.
- Timestamping of creation and updates.
- Optional, robust edge cleanup during vertex deletion.
- Standalone verification script included (`if __name__ == "__main__":`).

Third-Party Package Documentation:
- python-arango: https://docs.python-arango.com/en/main/
- Loguru: https://loguru.readthedocs.io/en/stable/
"""

import uuid
import sys
import os
from datetime import datetime, timezone
from typing import Dict, Any, Optional, List, TypeVar, Union, cast, MutableMapping

# Type definitions for responses
T = TypeVar("T")
Json = Dict[str, Any]  # JSON response type

# Third-party imports
from loguru import logger
from arango.typings import DataTypes
from arango.database import StandardDatabase
from arango.cursor import Cursor
from arango.result import Result
from arango.exceptions import (
    DocumentInsertError,
    DocumentRevisionError,
    DocumentUpdateError,
    DocumentDeleteError,
    DocumentGetError,
    ArangoServerError,
    AQLQueryExecuteError,
    CollectionLoadError,
)

# Attempt to import local project dependencies
try:
    # Dynamic path adjustment (consider a better approach like setting PYTHONPATH)
    _project_root = os.path.abspath(
        os.path.join(os.path.dirname(__file__), "..", "..", "..")
    )
    if _project_root not in sys.path:
        sys.path.insert(0, _project_root)
    logger.trace(f"Attempting imports with project root: {_project_root}")

    # Essential dependencies for this file's functions
    from mcp_doc_retriever.arangodb.config import (
        COLLECTION_NAME,
        SEARCH_FIELDS,
        EDGE_COLLECTION_NAME,  # Needed for delete_lesson edge cleanup
        GRAPH_NAME,  # Used in logs/context, delete_lesson
    )
    from mcp_doc_retriever.arangodb.embedding_utils import (
        get_text_for_embedding,
        get_embedding,
    )

    logger.trace("Project-specific imports loaded successfully.")

except ImportError as e:
    logger.critical(
        f"Failed to import core dependencies needed for crud_lessons.py. Standalone test may fail. Error: {e}"
    )
    # Define minimal fallbacks if needed for the functions themselves
    COLLECTION_NAME = os.environ.get("ARANGO_VERTEX_COLLECTION", "lessons_learned")
    EDGE_COLLECTION_NAME = os.environ.get(
        "ARANGO_EDGE_COLLECTION", "lesson_relationships"
    )
    GRAPH_NAME = os.environ.get("ARANGO_GRAPH_NAME", "lessons_graph")
    SEARCH_FIELDS = ["problem", "solution", "context", "tags", "role"]  # Basic default
    logger.warning(f"Using fallback config: COLLECTION_NAME='{COLLECTION_NAME}', etc.")
    # Define dummy embedding functions if absolutely necessary (will break functionality)
    if "get_text_for_embedding" not in globals():

        def get_text_for_embedding(data):
            return f"{data.get('problem', '')} {data.get('solution', '')}"

        def get_embedding(text):
            logger.warning("Using dummy get_embedding!")
            return [0.0] * 10  # Dummy embedding

# --- Lesson (Vertex) CRUD Functions ---


def add_lesson(
    db: StandardDatabase, lesson_data: Dict[str, Any]
) -> Optional[Dict[str, Any]]:
    """
    Adds a new lesson document (vertex) to the specified collection,
    including mandatory embedding generation.

    Args:
        db: An initialized ArangoDB StandardDatabase connection object.
        lesson_data: A dictionary containing the lesson details. Must include
                     'problem' and 'solution' fields. If '_key' is not provided,
                     a UUID will be generated. 'embedding' field will be overwritten.

    Returns:
        A dictionary containing the metadata ('_id', '_key', '_rev') of the
        newly created document, or None if the operation failed.
    """
    action_uuid = str(uuid.uuid4())
    with logger.contextualize(action="add_lesson", crud_id=action_uuid):
        if not lesson_data.get("problem") or not lesson_data.get("solution"):
            logger.error("Missing required fields: 'problem' or 'solution'.")
            return None

        if "_key" not in lesson_data:
            lesson_data["_key"] = str(uuid.uuid4())
        lesson_key = lesson_data["_key"]

        if "timestamp_created" not in lesson_data:
            lesson_data["timestamp_created"] = (
                datetime.now(timezone.utc)
                .isoformat(timespec="milliseconds")
                .replace("+00:00", "Z")
            )

        logger.debug(f"Preparing to add lesson with potential key: {lesson_key}")

        try:
            text_to_embed = get_text_for_embedding(lesson_data)
            embedding = get_embedding(text_to_embed) if text_to_embed else None
            if (
                text_to_embed and embedding is None
            ):  # Check if embedding failed after getting text
                logger.error(f"Embedding generation failed for key {lesson_key}.")
                return None
            lesson_data["embedding"] = embedding
            if embedding:
                logger.trace(f"Embedding generated ({len(embedding)} dims).")
            else:
                logger.warning(f"No embedding generated for key {lesson_key}.")
        except Exception as embed_e:
            logger.exception(
                f"Error during embedding generation for key {lesson_key}: {embed_e}"
            )
            return None  # Don't proceed if embedding fails unexpectedly

        try:
            collection = db.collection(COLLECTION_NAME)
            logger.info(f"Inserting lesson vertex: {lesson_key}")
            meta = collection.insert(document=lesson_data, sync=True, return_new=False)
            meta_dict = cast(Dict[str, Any], meta)
            logger.success(f"Lesson added: _key={meta_dict.get('_key')}")
            return meta_dict
        except (DocumentInsertError, ArangoServerError, CollectionLoadError) as e:
            logger.error(f"DB error adding lesson (key: {lesson_key}): {e}")
            return None
        except Exception as e:
            logger.exception(f"Unexpected error adding lesson (key: {lesson_key}): {e}")
            return None


def get_lesson(db: StandardDatabase, lesson_key: str) -> Optional[Dict[str, Any]]:
    """
    Retrieves a specific lesson document (vertex) by its _key.

    Args:
        db: An initialized ArangoDB StandardDatabase connection object.
        lesson_key: The _key of the lesson document to retrieve.

    Returns:
        A dictionary representing the lesson document if found, otherwise None.
    """
    action_uuid = str(uuid.uuid4())
    with logger.contextualize(
        action="get_lesson", crud_id=action_uuid, lesson_key=lesson_key
    ):
        logger.debug(f"Attempting to retrieve lesson vertex: {lesson_key}")
        try:
            collection = db.collection(COLLECTION_NAME)
            doc = collection.get(lesson_key)
            if doc:
                doc_dict = cast(Dict[str, Any], doc)
                logger.success(
                    f"Lesson retrieved successfully: _key={doc_dict.get('_key')}"
                )
                return doc_dict
            else:
                logger.info(f"Lesson not found: {lesson_key}")
                return None
        except (DocumentGetError, ArangoServerError, CollectionLoadError) as e:
            logger.error(f"DB error retrieving lesson (key: {lesson_key}): {e}")
            return None
        except Exception as e:
            logger.exception(
                f"Unexpected error getting lesson (key: {lesson_key}): {e}"
            )
            return None


def update_lesson(
    db: StandardDatabase, lesson_key: str, update_data: Dict[str, Any]
) -> Optional[Dict[str, Any]]:
    """
    Updates an existing lesson document (vertex). Regenerates embedding if
    fields relevant to embedding (defined in SEARCH_FIELDS config) are updated.

    Args:
        db: An initialized ArangoDB StandardDatabase connection object.
        lesson_key: The _key of the lesson document to update.
        update_data: A dictionary containing the fields to update. Core fields like
                     _key, _id, _rev, embedding will be ignored or handled internally.

    Returns:
        A dictionary containing the metadata ('_id', '_key', '_rev') of the
        updated document, or None if the update failed or the document
        was not found.
    """
    action_uuid = str(uuid.uuid4())
    with logger.contextualize(
        action="update_lesson", crud_id=action_uuid, lesson_key=lesson_key
    ):
        if not update_data:
            logger.warning("No update data provided.")
            return None

        logger.debug(f"Attempting to update lesson vertex: {lesson_key}")

        update_payload = update_data.copy()
        protected_keys = ["_key", "_id", "_rev", "embedding", "timestamp_created"]
        for key in protected_keys:
            update_payload.pop(key, None)

        if not update_payload:
            logger.warning("No valid fields to update.")
            return None

        try:
            collection = db.collection(COLLECTION_NAME)
            current_doc = collection.get(lesson_key)
            if not current_doc:
                logger.error(f"Lesson not found for update: {lesson_key}")
                return None

            current_dict = cast(Dict[str, Any], current_doc)
            doc_to_update = {"_key": lesson_key}

            try:
                embedding_fields_updated = any(
                    field in update_payload for field in SEARCH_FIELDS
                )
            except NameError:
                logger.warning("SEARCH_FIELDS not available.")
                embedding_fields_updated = False

            if embedding_fields_updated:
                logger.debug("Regenerating embedding...")
                merged_data = current_dict.copy()
                merged_data.update(update_payload)
                try:
                    text_to_embed = get_text_for_embedding(merged_data)
                    new_embedding = (
                        get_embedding(text_to_embed) if text_to_embed else None
                    )
                    if text_to_embed and new_embedding is None:
                        logger.error(
                            f"Embedding regeneration failed for {lesson_key}. Update aborted."
                        )
                        return None
                    doc_to_update["embedding"] = new_embedding
                    if new_embedding:
                        logger.trace("New embedding included.")
                except Exception as embed_e:
                    logger.exception(
                        f"Embedding regen error for {lesson_key}: {embed_e}"
                    )
                    return None

            doc_to_update.update(update_payload)
            doc_to_update["timestamp_updated"] = (
                datetime.now(timezone.utc)
                .isoformat(timespec="milliseconds")
                .replace("+00:00", "Z")
            )

            meta = collection.update(
                document=doc_to_update, sync=True, keep_none=False, merge=True
            )
            meta_dict = cast(Dict[str, Any], meta)
            logger.success(
                f"Lesson updated: _key={meta_dict.get('_key')}, _rev={meta_dict.get('_rev')}"
            )
            return meta_dict

        except (
            DocumentUpdateError,
            DocumentRevisionError,
            ArangoServerError,
            CollectionLoadError,
        ) as e:
            logger.error(f"DB error updating lesson (key: {lesson_key}): {e}")
            return None
        except Exception as e:
            logger.exception(
                f"Unexpected error updating lesson (key: {lesson_key}): {e}"
            )
            return None


def delete_lesson(
    db: StandardDatabase, lesson_key: str, delete_edges: bool = True
) -> bool:
    """
    Deletes a lesson document (vertex) and optionally cleans up its connected edges.

    Args:
        db: An initialized ArangoDB StandardDatabase connection object.
        lesson_key: The _key of the vertex document to delete.
        delete_edges: If True (default), also delete incoming and outgoing edges
                      connected to this lesson in the configured edge collection.

    Returns:
        True if the vertex deletion was successful or the vertex was already gone.
        False if an error occurred during vertex deletion (edge deletion errors
        are logged but do not cause this function to return False).
    """
    action_uuid = str(uuid.uuid4())
    lesson_id = f"{COLLECTION_NAME}/{lesson_key}"
    with logger.contextualize(
        action="delete_lesson", crud_id=action_uuid, lesson_id=lesson_id
    ):
        edge_deletion_errors = False

        if delete_edges:
            logger.debug(
                f"Attempting edge cleanup for vertex {lesson_id} in '{EDGE_COLLECTION_NAME}'..."
            )
            aql = f"""
            FOR edge IN @@edge_collection
              FILTER edge._from == @vertex_id OR edge._to == @vertex_id
              REMOVE edge IN {EDGE_COLLECTION_NAME} OPTIONS {{ ignoreErrors: true }}
              RETURN OLD._key
            """
            bind_vars = {
                "vertex_id": lesson_id,
                "@edge_collection": EDGE_COLLECTION_NAME,
            }
            try:
                typed_bind_vars = cast(Dict[str, DataTypes], bind_vars)
                cursor = db.aql.execute(aql, bind_vars=typed_bind_vars, count=True)
                count = cursor.count() if hasattr(cursor, "count") else "unknown"
                logger.trace(f"AQL edge cleanup attempted removal of {count} edge(s).")
            except AQLQueryExecuteError as aqle:
                logger.error(f"AQL edge deletion failed for {lesson_id}: {aqle}")
                edge_deletion_errors = True
            except Exception as edge_e:
                logger.exception(
                    f"Unexpected error during AQL edge deletion for {lesson_id}: {edge_e}"
                )
                edge_deletion_errors = True

        logger.info(f"Attempting to delete lesson vertex: {lesson_id}")
        try:
            collection = db.collection(COLLECTION_NAME)
            deleted = collection.delete(
                document=lesson_key, sync=True, ignore_missing=True
            )
            if deleted:
                logger.success(f"Lesson vertex deleted: _key={lesson_key}")
            else:
                logger.warning(
                    f"Lesson vertex not found or already deleted: _key={lesson_key}"
                )

            if edge_deletion_errors:
                logger.warning(
                    f"Vertex {lesson_key} deleted, but edge cleanup had errors."
                )
            return True  # Vertex is gone or was already gone

        except (DocumentDeleteError, ArangoServerError, CollectionLoadError) as e:
            logger.error(f"DB error deleting lesson vertex (key: {lesson_key}): {e}")
            return False
        except Exception as e:
            logger.exception(
                f"Unexpected error deleting lesson vertex (key: {lesson_key}): {e}"
            )
            return False


# --- Standalone Execution Block for Verification (Trimmed) ---
if __name__ == "__main__":
    # Basic logging setup
    logger.remove()
    logger.add(
        sys.stderr,
        level="INFO",
        format="{time:HH:mm:ss} | {level: <7} | {message}",
        colorize=True,
    )
    logger.info("--- Running crud_lessons.py Standalone Verification (Focused) ---")

    # Imports needed ONLY for this limited test
    try:
        # Assuming setup utilities are accessible relative to this file's potential location
        from mcp_doc_retriever.arangodb.arango_setup import (
            connect_arango,
            ensure_database,
            ensure_collection,
            ensure_edge_collection,
        )

        # Import log_safe_results if available
        try:
            from mcp_doc_retriever.arangodb.log_utils import log_safe_results
        except ImportError:
            log_safe_results = lambda x: str(x)  # Basic fallback
    except ImportError as e:
        logger.critical(f"Standalone test setup import failed: {e}. Cannot run test.")
        sys.exit(1)

    # Test Data (Minimal)
    run_uuid = str(uuid.uuid4())[:6]
    TEST_KEY = f"lesson_crud_{run_uuid}"
    TEST_DATA = {
        "_key": TEST_KEY,
        "problem": f"Problem {run_uuid}",
        "solution": f"Solution {run_uuid}",
        "tags": ["test"],
    }
    UPDATE_DATA = {"role": "Tester", "severity": "WARN"}

    db: Optional[StandardDatabase] = None
    passed = True

    try:
        # 1. Connect & Setup (Minimal)
        logger.info("Connecting and ensuring collections...")
        client = connect_arango()
        if not client:
            raise ConnectionError("Connect failed")
        db = ensure_database(client)
        if not db:
            raise ConnectionError("Ensure DB failed")
        ensure_collection(db, COLLECTION_NAME)
        ensure_edge_collection(db)  # Needed for delete test context
        logger.info(f"Using DB: {db.name}")

        # --- Test Core Functions ---
        # 2. Add
        logger.info(f"Testing add_lesson ({TEST_KEY})...")
        add_meta = add_lesson(db, TEST_DATA.copy())
        if not (add_meta and add_meta.get("_key") == TEST_KEY):
            logger.error(f"❌ Add FAILED. Meta: {add_meta}")
            passed = False
        else:
            logger.info("✅ Add PASSED.")

        # 3. Get (only if Add passed)
        if passed:
            logger.info(f"Testing get_lesson ({TEST_KEY})...")
            get_data = get_lesson(db, TEST_KEY)
            if not (get_data and get_data.get("_key") == TEST_KEY):
                logger.error(
                    f"❌ Get FAILED. Data: {log_safe_results([get_data] if get_data else [])}"
                )
                passed = False
            else:
                logger.info("✅ Get PASSED.")

        # 4. Update (only if Get passed)
        if passed:
            logger.info(f"Testing update_lesson ({TEST_KEY})...")
            update_meta = update_lesson(db, TEST_KEY, UPDATE_DATA.copy())
            if not update_meta:
                logger.error(f"❌ Update FAILED. Meta: {update_meta}")
                passed = False
            else:
                # Verify update
                get_updated = get_lesson(db, TEST_KEY)
                if get_updated and get_updated.get("role") == UPDATE_DATA["role"]:
                    logger.info("✅ Update PASSED (Verified).")
                else:
                    logger.error(
                        f"❌ Update Verification FAILED. Data: {log_safe_results([get_updated] if get_updated else [])}"
                    )
                    passed = False

        # 5. Delete (Run regardless of previous steps to ensure cleanup)
        logger.info(f"Testing delete_lesson ({TEST_KEY})...")
        # Note: We are NOT explicitly testing edge cleanup here to keep it focused.
        # The delete_lesson function *will* attempt it if delete_edges=True (default).
        delete_ok = delete_lesson(db, TEST_KEY, delete_edges=True)
        if not delete_ok:
            # This might fail if e.g. permissions are wrong, even if doc doesn't exist
            logger.error(f"❌ Delete Command FAILED (returned False).")
            passed = False  # Treat delete failure as a test failure
        else:
            # Verify deletion
            get_deleted = get_lesson(db, TEST_KEY)
            if get_deleted is None:
                logger.info("✅ Delete PASSED (Verified).")
            else:
                logger.error(f"❌ Delete Verification FAILED (Doc still exists).")
                passed = False

    except Exception as e:
        logger.exception(f"An unexpected error occurred: {e}")
        passed = False

    # --- Final Result ---
    finally:
        # Attempt cleanup again just in case delete test failed but doc exists
        if db and get_lesson(db, TEST_KEY):
            logger.warning(f"Attempting final cleanup for {TEST_KEY}")
            delete_lesson(db, TEST_KEY, delete_edges=True)

        logger.info("-" * 40)
        if passed:
            logger.success("\n✅ crud_lessons.py Standalone Verification PASSED")
            sys.exit(0)
        else:
            logger.error("\n❌ crud_lessons.py Standalone Verification FAILED")
            sys.exit(1)
