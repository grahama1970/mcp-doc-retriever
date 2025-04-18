# src/mcp_doc_retriever/arangodb/crud_relationships.py
"""
ArangoDB CRUD Operations Module for Lesson Relationships (Edges).

Description:
Provides functions for Create and Delete operations on the
'lesson_relationships' edge collection in ArangoDB.

This file focuses specifically on the Relationship edges connecting Lesson vertices.
See related files for Lesson (Vertex) CRUD and Search operations.

Key Features:
- Idempotent delete operations (using ignore_missing=True).
- Basic validation for required fields.
- Standalone verification script included (`if __name__ == "__main__":`).

Third-Party Package Documentation:
- python-arango: https://docs.python-arango.com/en/main/
- Loguru: https://loguru.readthedocs.io/en/stable/

Sample Usage (Illustrative):
    from arango.database import StandardDatabase
    # Assuming db is an initialized StandardDatabase object
    # and lesson keys key1, key2 exist

    # Add Relationship
    edge_meta = add_relationship(db, key1, key2, "These are related.", "RELATED")
    edge_key = edge_meta['_key'] if edge_meta else None

    # Delete Relationship
    del_rel_success = delete_relationship(db, edge_key)

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
from arango import ArangoClient
from loguru import logger
from arango.typings import DataTypes
from arango.database import StandardDatabase
from arango.cursor import Cursor
from arango.result import Result
from arango.collection import StandardCollection
from arango.exceptions import (
    DocumentInsertError,
    DocumentDeleteError,
    ArangoServerError,
    CollectionLoadError,
    EdgeDefinitionListError,  # Relevant for edge operations
    IndexCreateError,
    IndexDeleteError,
    ArangoClientError,
)

# --- Import Embedding Utilities ---
from mcp_doc_retriever.arangodb.embedding_utils import get_embedding

# Module-level constants from environment with defaults
COLLECTION_NAME = os.environ.get('ARANGO_VERTEX_COLLECTION', 'lessons_learned')
EDGE_COLLECTION_NAME = os.environ.get('ARANGO_EDGE_COLLECTION', 'lesson_relationships')
EMBEDDING_FIELD = os.environ.get("ARANGO_EMBEDDING_FIELD", "embedding")
EMBEDDING_DIMENSION = int(os.environ.get("ARANGO_EMBEDDING_DIMENSION", "1536"))
VECTOR_INDEX_NAME = os.environ.get("ARANGO_VECTOR_INDEX_NAME", "idx_lesson_embedding")
USERNAME = os.environ.get("ARANGO_USERNAME", "root")
PASSWORD = os.environ.get("ARANGO_PASSWORD", "openSesame")
HOST = os.environ.get("ARANGO_HOST", "http://localhost:8529")
DATABASE_NAME = os.environ.get("ARANGO_DATABASE", "doc_retriever")

# Log configuration on module import
logger.info(
    f"Using collections: vertex='{COLLECTION_NAME}', edge='{EDGE_COLLECTION_NAME}'"
)

# --- Relationship (Edge) CRUD Functions ---


def add_relationship(
    db: StandardDatabase,
    from_lesson_key: str,
    to_lesson_key: str,
    rationale: str,
    relationship_type: str,
    attributes: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    """
    Creates a directed relationship edge between two lesson documents in the
    configured edge collection.

    Args:
        db: An initialized ArangoDB StandardDatabase connection object.
        from_lesson_key: The _key of the source lesson vertex.
        to_lesson_key: The _key of the target lesson vertex.
        rationale: A string explaining the reason for the relationship.
        relationship_type: A string categorizing the relationship (e.g., "RELATED").
        attributes: An optional dictionary of additional properties for the edge.

    Returns:
        A dictionary containing the metadata ('_id', '_key', '_rev') of the
        newly created edge document, or None if the operation failed.
    """
    action_uuid = str(uuid.uuid4())
    from_id = f"{COLLECTION_NAME}/{from_lesson_key}"
    to_id = f"{COLLECTION_NAME}/{to_lesson_key}"

    with logger.contextualize(
        action="add_relationship", crud_id=action_uuid, from_id=from_id, to_id=to_id
    ):
        if not rationale or not relationship_type:
            logger.error("Rationale and relationship_type are required.")
            return None

        edge_data = {
            "_from": from_id,
            "_to": to_id,
            "rationale": rationale,
            "type": relationship_type,
            "timestamp": datetime.now(timezone.utc)
            .isoformat(timespec="milliseconds")
            .replace("+00:00", "Z"),
        }
        if attributes:
            safe_attributes = {
                k: v for k, v in attributes.items() if k not in edge_data
            }
            if len(safe_attributes) != len(attributes):
                logger.warning("Ignoring attributes conflicting with core edge fields.")
            edge_data.update(safe_attributes)

        logger.info(
            f"Creating relationship edge from {from_id} to {to_id} (Type: {relationship_type})"
        )

        try:
            edge_collection = db.collection(EDGE_COLLECTION_NAME)
            meta = edge_collection.insert(
                document=edge_data, sync=True, return_new=False
            )
            meta_dict = cast(Dict[str, Any], meta)
            logger.success(f"Relationship edge created: _key={meta_dict.get('_key')}")
            return meta_dict
        except (
            DocumentInsertError,
            ArangoServerError,
            CollectionLoadError,
            EdgeDefinitionListError,
        ) as e:
            logger.error(
                f"DB error adding relationship edge ({from_id} -> {to_id}): {e}"
            )
            return None
        except Exception as e:
            logger.exception(
                f"Unexpected error adding relationship edge ({from_id} -> {to_id}): {e}"
            )
            return None


def delete_relationship(db: StandardDatabase, edge_key: str) -> bool:
    """
    Deletes a specific relationship edge document by its _key from the edge collection.

    Args:
        db: An initialized ArangoDB StandardDatabase connection object.
        edge_key: The _key of the edge document to delete.

    Returns:
        True if the deletion was successful or the edge was already gone.
        False if an error occurred during deletion.
    """
    action_uuid = str(uuid.uuid4())
    edge_id = f"{EDGE_COLLECTION_NAME}/{edge_key}"
    with logger.contextualize(
        action="delete_relationship", crud_id=action_uuid, edge_id=edge_id
    ):
        logger.info(f"Attempting to delete relationship edge: {edge_key}")
        try:
            edge_collection = db.collection(EDGE_COLLECTION_NAME)
            deleted = edge_collection.delete(
                document=edge_key, sync=True, ignore_missing=True
            )
            if deleted:
                logger.success(f"Relationship edge deleted: _key={edge_key}")
            else:
                logger.warning(
                    f"Relationship edge not found or already deleted: _key={edge_key}"
                )
            return True  # Idempotent success
        except (DocumentDeleteError, ArangoServerError, CollectionLoadError) as e:
            logger.error(f"DB error deleting relationship edge (key: {edge_key}): {e}")
            return False
        except Exception as e:
            logger.exception(
                f"Unexpected error deleting relationship edge (key: {edge_key}): {e}"
            )
            return False


# Ensure vector index  helper code
def ensure_vector_index(
    db: StandardDatabase,
    collection_name: str = COLLECTION_NAME,
    index_name: str = VECTOR_INDEX_NAME,
    embedding_field: str = EMBEDDING_FIELD,
    dimensions: int = EMBEDDING_DIMENSION,
) -> bool:
    """
    Ensures a dedicated 'vector' index exists on the collection.
    Reverted to this type based on troubleshooting for ERR 9.

    Args:
        db: The StandardDatabase object.
        collection_name: Name of the collection containing embeddings.
        index_name: Desired name for the vector index.
        embedding_field: Name of the field storing vector embeddings.
        dimensions: The dimensionality of the vectors.

    Returns:
        True if the index exists or was created successfully, False otherwise.
    """
    try:
        if collection_name not in [c["name"] for c in db.collections()]:
            logger.error(
                "Cannot create vector index: Collection '{}' does not exist.",
                collection_name,
            )
            return False
        collection = db.collection(collection_name)

        # --- Drop existing index by name first for idempotency ---
        try:
            indexes = collection.indexes()
            existing_index_info = next(
                (idx for idx in indexes if idx.get("name") == index_name), None
            )
            if existing_index_info:
                logger.warning(
                    "Found existing index named '{}'. Attempting to drop it before creation...",
                    index_name,
                )
                index_id_or_name = existing_index_info.get("id", index_name)
                if collection.delete_index(index_id_or_name, ignore_missing=True):
                    logger.info("Successfully dropped existing index '{}'.", index_name)
                else:
                    logger.warning(
                        "Attempted to drop index '{}', but delete_index returned False.",
                        index_name,
                    )
        except (IndexDeleteError, ArangoServerError, ArangoClientError) as drop_err:
            logger.error(
                "Error encountered while trying to drop existing index '{}'. Proceeding. Error: {}. See traceback.",
                index_name,
                drop_err,
                exc_info=True,
            )
        # --- END DROP LOGIC ---

        # --- Attempt creation using "type": "vector" ---
        logger.info(
            "Attempting to create dedicated 'vector' index '{}' on field '{}'...",
            index_name,
            embedding_field,
        )

        index_definition = {
            "type": "vector",  # <-- CORRECT TYPE based on troubleshooting for ERR 9
            "name": index_name,
            "fields": [embedding_field],  # Field containing the vector array
            "params": {  # Parameters specific to the vector index
                "dimension": dimensions,
                "metric": "cosine",  # Or "euclidean" / "l2"
                "nLists": 2
            },
        }

        logger.debug(
            "Attempting to add 'vector' index with definition: {}", index_definition
        )
        try:
            collection.add_index(index_definition)  # <--- Attempt creation
        except IndexCreateError as e:
            logger.error(f"Index creation failed: {e}. Definition: {index_definition}")
            return False

        logger.success(
            "Dedicated 'vector' index '{}' on field '{}' created.",
            index_name,
            embedding_field,
        )
        return True

    # Keep the detailed error logging
    except (ArangoServerError, KeyError) as e:
        logger.error(
            "Failed to create vector index '{}' on collection '{}'. See traceback for details.",
            index_name,
            collection_name,
            exc_info=True,
        )
        return False
    except Exception as e:
        logger.error(
            "An unexpected error occurred ensuring vector index '{}'. See traceback for details.",
            index_name,
            exc_info=True,
        )
        return False
    
    
# Embed text code
def get_embedding(text: str) -> List[float]:
    """
    Generates an embedding for the given text.

    Args:
        text: The text to embed.

    Returns:
        A list of floats representing the embedding.
    """
    # Implement your embedding generation logic here
    # For example, you could use a pre-trained model like Word2Vec or GloVe
    # and return the embedding for the given text.
    # For now, let's return a dummy embedding
    return [0.0] * EMBEDDING_DIMENSION
# --- Standalone Execution Block for Verification (Focused) ---
if __name__ == "__main__":
    # Basic logging setup
    logger.remove()
    logger.add(
        sys.stderr,
        level="DEBUG",  # Use DEBUG for more detail
        format="{time:HH:mm:ss} | {level: <7} | {message}",
        colorize=True,
    )
    logger.info(
        "--- Running crud_relationships.py Standalone Verification (Focused) ---"
    )

    # Imports needed ONLY for this limited test
    try:
        from mcp_doc_retriever.arangodb.arango_setup import (
            connect_arango,
            ensure_database,
            ensure_collection,
            ensure_edge_collection,
        )
        from mcp_doc_retriever.arangodb.config import RELATIONSHIP_TYPE_RELATED
        from mcp_doc_retriever.arangodb.embedding_utils import (
            get_text_for_embedding,
            get_embedding,
        )
    except ImportError as e:
        logger.critical(f"Standalone test setup import failed: {e}. Cannot run test.")
        sys.exit(1)

    # Test Data
    run_uuid = str(uuid.uuid4())[:6]
    TEST_VERTEX_KEY_1 = f"rel_test_v1_{run_uuid}"
    TEST_VERTEX_KEY_2 = f"rel_test_v2_{run_uuid}"
    TEST_EDGE_TYPE = "RELATED"  # Or load from config
    TEST_RATIONALE = f"Test relationship {run_uuid}"  # Or load from config

    db: Optional[StandardDatabase] = None
    created_edge_key: Optional[str] = None
    passed = True
    vertex_coll_obj: Optional[StandardCollection] = None  # Store handle

    # --- Helper functions (_ensure_test_vertex, _delete_test_vertex) ---
    # Keep the function definitions here, but we won't call _ensure_test_vertex initially
    def _ensure_test_vertex(db_conn: StandardDatabase, key: str) -> bool:
        # Keep the latest version of this function (using direct insert)
        coll = db_conn.collection(COLLECTION_NAME)
        try:  # Optional Type Check
            props = coll.properties()
            coll_type_code = props.get("type", 0)
            logger.trace(
                f"Helper: Collection '{coll.name}' type code {coll_type_code} (2=Doc)"
            )
            if coll_type_code != 2:
                raise TypeError("Helper: Collection not type 2")
        except Exception as prop_err:
            logger.warning(f"Helper: Prop check warn: {prop_err}")

        if not coll.has(key):
            logger.debug(f"Helper: Creating placeholder vertex: {key}")
            lesson_text = f"Vertex with key: {key}"  # Example text
            embedding = get_embedding(lesson_text)  # Call embedding utils
            doc_to_insert = {"_key": key, EMBEDDING_FIELD: embedding}
            try:
                meta = coll.insert(doc_to_insert, sync=True)
                if not isinstance(meta, dict) or not meta.get("_key"):
                    raise RuntimeError(f"Unexpected meta: {meta}")
                logger.debug(f"Helper: Inserted {key}: {meta}")
            except DocumentInsertError as e:
                logger.error(f"Helper: Insert error {key}: {e}")
                raise e
            except Exception as e:
                logger.exception(f"Helper: Unexpected error {key}: {e}")
                raise RuntimeError() from e
        else:
            logger.trace(f"Helper: Placeholder vertex {key} already exists.")
        return True

    def _delete_test_vertex(db_conn: StandardDatabase, key: str) -> None:
        # Keep this helper as is
        if db_conn:
            try:
                db_conn.collection(COLLECTION_NAME).delete(
                    key, ignore_missing=True, sync=True
                )
                logger.debug(f"Cleaned up placeholder vertex: {key}")
            except Exception as e:
                logger.warning(f"Ignoring vertex cleanup error {key}: {e}")

    # --- End Helpers ---

    # --- Main Test Execution ---
    try:
        # 1. Connect & Setup
        logger.info("Connecting and ensuring collections...")
        client = ArangoClient(hosts=HOST)
        db = client.db(DATABASE_NAME, username=USERNAME, password=PASSWORD)

        if not db:
            raise ConnectionError("Connect failed")

        # client = connect_arango()
        # if not client:
        #     raise ConnectionError("Connect failed")
        # db = ensure_database(client)
        # if not db:
        #     raise ConnectionError("Ensure DB failed")

        vertex_coll_obj = db.collection(COLLECTION_NAME)
        if vertex_coll_obj is None:
            raise RuntimeError(f"Setup failed: DOC collection '{COLLECTION_NAME}'")
        edge_coll_obj = db.collection(EDGE_COLLECTION_NAME)
        if edge_coll_obj is None:
            raise RuntimeError(
                f"Setup failed: EDGE collection '{EDGE_COLLECTION_NAME}'"
            )

         # Ensure vector index BEFORE any inserts
        logger.info("Ensuring vector index...")
        index_ok = ensure_vector_index(db)
        if not index_ok:
            raise RuntimeError("Failed to ensure vector index. Aborting.")
        logger.info("Vector index ensured.")

        logger.info(
            f"Collections '{COLLECTION_NAME}' and '{EDGE_COLLECTION_NAME}' ensured."
        )
        # --- <<< NEW DIRECT INSERT TEST >>> ---
        logger.info(
            f"Attempting DIRECT insert into '{COLLECTION_NAME}' for key '{TEST_VERTEX_KEY_1}'..."
        )
        lesson_text = f"Direct insert of vertex with key {TEST_VERTEX_KEY_1}"
        embedding = get_embedding(lesson_text)  # embedding
        direct_insert_doc = {"_key": TEST_VERTEX_KEY_1, EMBEDDING_FIELD: embedding}
        direct_insert_meta = None
        try:
            # Use the verified handle vertex_coll_obj
            direct_insert_meta = vertex_coll_obj.insert(direct_insert_doc, sync=True)
            logger.success(f"DIRECT insert SUCCEEDED. Meta: {direct_insert_meta}")
        except DocumentInsertError as direct_err:
            logger.error(
                f"DIRECT insert FAILED with DocumentInsertError: {direct_err} - BODY: {direct_err.http_exception.response.text if hasattr(direct_err, 'http_exception') and hasattr(direct_err.http_exception, 'response') else 'N/A'}"
            )
            passed = False
        except Exception as direct_err:
            logger.exception(
                f"DIRECT insert FAILED with unexpected error: {direct_err}"
            )
            passed = False
        # --- <<< END DIRECT INSERT TEST >>> ---

        # If direct insert failed, maybe stop? Or proceed to see if add_relationship fails differently?
        if not passed:
            raise RuntimeError(
                "Direct insert test failed, stopping."
            )  # Stop early if direct insert fails

        # --- If Direct Insert WORKED, now try ensuring vertices with helper ---
        logger.info("Direct insert seemed okay, now trying _ensure_test_vertex...")
        # These calls might now succeed if the direct insert somehow "fixed" a state issue,
        # or they might still fail, indicating the issue is specific to the helper's context.
        _ensure_test_vertex(db, TEST_VERTEX_KEY_1)  # Should report "already exists" now
        _ensure_test_vertex(db, TEST_VERTEX_KEY_2)
        logger.info(f"Placeholder vertices ensured via helper (or existed).")

        # --- Test Core Relationship Functions ---
        # (Only run if setup and vertex ensure worked)
        logger.info("Proceeding to test relationship functions...")
        # ... (rest of add_relationship / delete_relationship tests as before) ...
        # 2. Add Relationship
        logger.info(
            f"Testing add_relationship ({TEST_VERTEX_KEY_1} -> {TEST_VERTEX_KEY_2})..."
        )
        add_meta = add_relationship(
            db, TEST_VERTEX_KEY_1, TEST_VERTEX_KEY_2, TEST_RATIONALE, TEST_EDGE_TYPE
        )
        if not (add_meta and add_meta.get("_key")):
            logger.error(f"❌ Add Relationship FAILED. Meta: {add_meta}")
            passed = False
        else:
            created_edge_key = add_meta.get("_key")
            logger.info(f"✅ Add Relationship PASSED. Key: {created_edge_key}")

        # 3. Delete Relationship (only if Add passed)
        if passed and created_edge_key:
            logger.info(f"Testing delete_relationship ({created_edge_key})...")
            delete_ok = delete_relationship(db, created_edge_key)
            if not delete_ok:
                logger.error(f"❌ Delete Relationship FAILED")
                passed = False
            else:
                try:
                    get_deleted = edge_coll_obj.get(created_edge_key)
                    if get_deleted is None:
                        logger.info("✅ Delete Relationship PASSED (Verified).")
                        created_edge_key = None
                    else:
                        logger.error(
                            f"❌ Delete Relationship Verification FAILED (Edge exists)."
                        )
                        passed = False
                except Exception as e:
                    logger.error(
                        f"❌ Delete Relationship Verification FAILED (Error checking): {e}"
                    )
                    passed = False

    except Exception as e:
        logger.exception(f"An error occurred during test execution: {e}")
        passed = False

    # --- Final Result ---
    finally:
        # Cleanup test vertices and edge
        if db:
            if created_edge_key:
                logger.warning(f"Cleanup: Removing edge {created_edge_key}")
                delete_relationship(db, created_edge_key)
            _delete_test_vertex(db, TEST_VERTEX_KEY_1)
            _delete_test_vertex(db, TEST_VERTEX_KEY_2)

        logger.info("-" * 40)
        if passed:
            logger.success("\n✅ crud_relationships.py Standalone Verification PASSED")
            sys.exit(0)
        else:
            logger.error("\n❌ crud_relationships.py Standalone Verification FAILED")
            sys.exit(1)
