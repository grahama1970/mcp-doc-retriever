"""
ArangoDB CRUD Operations Module for Lesson Learned Vertices.

Provides Create, Read, Update, and Delete operations on the
'lessons_learned' vertex collection in ArangoDB, with robust
logging, configuration fallbacks, and a clean standalone test harness.
"""

import uuid
import sys
import os
from datetime import datetime, timezone
from typing import Dict, Any, Optional, List, TypeVar, cast

from loguru import logger
from arango.typings import DataTypes
from arango.database import StandardDatabase
from arango.cursor import Cursor
from arango.exceptions import (
    DocumentInsertError,
    DocumentGetError,
    DocumentUpdateError,
    DocumentRevisionError,
    DocumentDeleteError,
    ArangoServerError,
    AQLQueryExecuteError,
    CollectionLoadError,
)

# --- CONFIG IMPORT -------------------------------------------------
try:
    _root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
    if _root not in sys.path:
        sys.path.insert(0, _root)
    from mcp_doc_retriever.arangodb.config import (
        COLLECTION_NAME,
        EDGE_COLLECTION_NAME,
        GRAPH_NAME,
        SEARCH_FIELDS,
    )

    logger.debug(
        f"Loaded config: COLLECTION_NAME={COLLECTION_NAME}, "
        f"EDGE_COLLECTION_NAME={EDGE_COLLECTION_NAME}, GRAPH_NAME={GRAPH_NAME}, "
        f"SEARCH_FIELDS={SEARCH_FIELDS}"
    )
except ImportError as e:
    logger.warning(f"Config import failed, using defaults: {e}")
    COLLECTION_NAME = os.environ.get("ARANGO_VERTEX_COLLECTION", "lessons_learned")
    EDGE_COLLECTION_NAME = os.environ.get(
        "ARANGO_EDGE_COLLECTION", "lesson_relationships"
    )
    GRAPH_NAME = os.environ.get("ARANGO_GRAPH_NAME", "lessons_graph")
    SEARCH_FIELDS = ["problem", "solution", "context", "tags", "role"]
    logger.debug(
        f"Fallback config: COLLECTION_NAME={COLLECTION_NAME}, "
        f"EDGE_COLLECTION_NAME={EDGE_COLLECTION_NAME}, GRAPH_NAME={GRAPH_NAME}, "
        f"SEARCH_FIELDS={SEARCH_FIELDS}"
    )


T = TypeVar("T")
Json = Dict[str, Any]


# --- UTILS ---------------------------------------------------------
def _now_iso() -> str:
    return (
        datetime.now(timezone.utc)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )


# --- CRUD FUNCTIONS -----------------------------------------------
def add_lesson(
    db: StandardDatabase, lesson_data: Dict[str, Any]
) -> Optional[Dict[str, Any]]:
    """
    Adds a new lesson vertex, generating embedding and timestamp.
    """
    action_uuid = str(uuid.uuid4())
    with logger.contextualize(action="add_lesson", crud_id=action_uuid):
        # Required fields
        if not lesson_data.get("problem") or not lesson_data.get("solution"):
            logger.error("Missing required fields: 'problem' or 'solution'.")
            return None

        # Key assignment
        lesson_key = lesson_data.setdefault("_key", str(uuid.uuid4()))

        # Creation timestamp
        lesson_data.setdefault("timestamp_created", _now_iso())

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
            logger.exception(f"Unexpected error adding lesson: {e}")
            return None


def get_lesson(db: StandardDatabase, lesson_key: str) -> Optional[Dict[str, Any]]:
    """
    Retrieves a lesson vertex by its _key.
    """
    action_uuid = str(uuid.uuid4())
    with logger.contextualize(
        action="get_lesson", crud_id=action_uuid, lesson_key=lesson_key
    ):
        try:
            collection = db.collection(COLLECTION_NAME)
            doc = collection.get(lesson_key)
            if doc:
                doc_dict = cast(Dict[str, Any], doc)
                logger.success(f"Lesson retrieved: _key={lesson_key}")
                return doc_dict
            else:
                logger.info(f"Lesson not found: {lesson_key}")
                return None
        except (DocumentGetError, ArangoServerError, CollectionLoadError) as e:
            logger.error(f"DB error retrieving lesson (key: {lesson_key}): {e}")
            return None
        except Exception as e:
            logger.exception(f"Unexpected error getting lesson: {e}")
            return None


def update_lesson(
    db: StandardDatabase, lesson_key: str, update_data: Dict[str, Any]
) -> Optional[Dict[str, Any]]:
    """
    Updates fields on a lesson vertex, regenerating timestamp.
    """
    action_uuid = str(uuid.uuid4())
    with logger.contextualize(
        action="update_lesson", crud_id=action_uuid, lesson_key=lesson_key
    ):
        if not update_data:
            logger.warning("No update data provided.")
            return None

        # Protect core keys
        for k in ("_key", "_id", "_rev", "timestamp_created"):
            update_data.pop(k, None)

        if not update_data:
            logger.warning("No valid fields left to update.")
            return None

        try:
            collection = db.collection(COLLECTION_NAME)
            current = collection.get(lesson_key)
            if not current:
                logger.error(f"Lesson not found: {lesson_key}")
                return None

            payload = {"_key": lesson_key, **update_data}
            payload["timestamp_updated"] = _now_iso()

            meta = collection.update(
                document=payload, sync=True, keep_none=False, merge=True
            )
            meta_dict = cast(Dict[str, Any], meta)
            logger.success(
                f"Lesson updated: _key={meta_dict.get('_key')} _rev={meta_dict.get('_rev')}"
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
            logger.exception(f"Unexpected error updating lesson: {e}")
            return None


def delete_lesson(
    db: StandardDatabase, lesson_key: str, delete_edges: bool = True
) -> bool:
    """
    Deletes a lesson vertex and optionally its edges.
    """
    action_uuid = str(uuid.uuid4())
    lesson_id = f"{COLLECTION_NAME}/{lesson_key}"
    with logger.contextualize(
        action="delete_lesson", crud_id=action_uuid, lesson_id=lesson_id
    ):
        if delete_edges:
            try:
                aql = f"""
                FOR edge IN {EDGE_COLLECTION_NAME}
                  FILTER edge._from == @vid OR edge._to == @vid
                  REMOVE edge IN {EDGE_COLLECTION_NAME} OPTIONS {{ ignoreErrors: true }}
                """
                bind = {"vid": lesson_id}
                cursor: Cursor = db.aql.execute(aql, bind_vars=bind)
                logger.trace(
                    f"Edge cleanup attempted; rows: {cursor.count() if hasattr(cursor, 'count') else 'unknown'}"
                )
            except AQLQueryExecuteError as e:
                logger.error(f"AQL edge deletion failed: {e}")
            except Exception as e:
                logger.exception(f"Unexpected edge cleanup error: {e}")

        try:
            collection = db.collection(COLLECTION_NAME)
            collection.delete(document=lesson_key, sync=True, ignore_missing=True)
            logger.success(f"Lesson vertex deleted: _key={lesson_key}")
            return True
        except (DocumentDeleteError, ArangoServerError, CollectionLoadError) as e:
            logger.error(f"DB error deleting lesson (key: {lesson_key}): {e}")
            return False
        except Exception as e:
            logger.exception(f"Unexpected error deleting lesson: {e}")
            return False


# --- STANDALONE TEST HARNESS ---------------------------------------
if __name__ == "__main__":
    from mcp_doc_retriever.arangodb.arango_setup import (
        connect_arango,
        ensure_database,
    )

    logger.remove()
    logger.add(
        sys.stderr, level="INFO", format="{time:HH:mm:ss} | {level:<7} | {message}"
    )

    run_id = str(uuid.uuid4())[:6]
    test_coll = f"{COLLECTION_NAME}_tests_{run_id}"

    # Connect & prepare test collection
    client = connect_arango()
    db = ensure_database(client)
    if db.has_collection(test_coll):
        db.delete_collection(test_coll)
    db.create_collection(test_coll)
    COLLECTION_NAME = test_coll
    logger.info(f"Using test collection: {COLLECTION_NAME}")

    # Sample document
    TEST_KEY = f"lesson_crud_{run_id}"
    TEST_DATA = {
        "_key": TEST_KEY,
        "problem": f"Problem {run_id}",
        "solution": f"Solution {run_id}",
        "tags": ["test"],
    }
    UPDATE_DATA = {"role": "Tester"}

    passed = True

    # 1) Add
    meta = add_lesson(db, TEST_DATA.copy())
    if not (meta and meta.get("_key") == TEST_KEY):
        logger.error("❌ Add FAILED")
        passed = False
    else:
        logger.success("✅ Add PASSED")

    # 2) Get
    if passed:
        got = get_lesson(db, TEST_KEY)
        if not (got and got.get("_key") == TEST_KEY):
            logger.error("❌ Get FAILED")
            passed = False
        else:
            logger.success("✅ Get PASSED")

    # 3) Update
    if passed:
        upd = update_lesson(db, TEST_KEY, UPDATE_DATA.copy())
        if not upd or get_lesson(db, TEST_KEY).get("role") != UPDATE_DATA["role"]:
            logger.error("❌ Update FAILED")
            passed = False
        else:
            logger.success("✅ Update PASSED")

    # 4) Delete
    ok = delete_lesson(db, TEST_KEY, delete_edges=True)
    if not ok or get_lesson(db, TEST_KEY) is not None:
        logger.error("❌ Delete FAILED")
        passed = False
    else:
        logger.success("✅ Delete PASSED")

    # Teardown
    db.delete_collection(COLLECTION_NAME)
    logger.info(f"Dropped test collection: {COLLECTION_NAME}")
    logger.info("-" * 50)
    if passed:
        logger.success("=== Standalone Verification PASSED ===")
        sys.exit(0)
    else:
        logger.error("=== Standalone Verification FAILED ===")
        sys.exit(1)
