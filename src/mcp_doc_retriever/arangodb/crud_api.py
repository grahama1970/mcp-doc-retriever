# src/mcp_doc_retriever/arangodb/crud_api.py
"""
ArangoDB CRUD Operations Module for Lessons Learned and Relationships.

Description:
Provides functions for Create, Read, Update, and Delete (CRUD) operations
on the 'lessons_learned' vertex collection and the 'lesson_relationships'
edge collection in ArangoDB. Handles automatic timestamping, UUID key generation,
embedding generation/update, and optional cleanup of related edges when
deleting lessons.

Key Features:
- Idempotent delete operations (using ignore_missing=True).
- Automatic embedding generation via embedding_utils.
- Timestamping of creation and updates.
- Optional, robust edge cleanup during vertex deletion.
- Standalone verification script included (`if __name__ == "__main__":`).

Third-Party Package Documentation:
- python-arango: https://docs.python-arango.com/en/main/
- Loguru: https://loguru.readthedocs.io/en/stable/

Sample Usage (Illustrative):
    from arango.database import StandardDatabase
    # Assuming db is an initialized StandardDatabase object
    # from mcp_doc_retriever.arangodb.arango_setup import ...

    # Add
    lesson_data = {"problem": "...", "solution": "...", "tags": [...]}
    meta1 = add_lesson(db, lesson_data)
    key1 = meta1['_key'] if meta1 else None

    # Get
    lesson = get_lesson(db, key1)

    # Update
    update_payload = {"severity": "HIGH", "tags": ["updated"]}
    meta_updated = update_lesson(db, key1, update_payload)

    # Add Relationship
    # (assuming another lesson with key2 exists)
    edge_meta = add_relationship(db, key1, key2, "These are related.", "RELATED")
    edge_key = edge_meta['_key'] if edge_meta else None

    # Delete Relationship
    del_rel_success = delete_relationship(db, edge_key)

    # Delete Lesson (with edge cleanup)
    del_lesson_success = delete_lesson(db, key1, delete_edges=True)

"""

import uuid
import sys  # For __main__ block exit codes
import os  # For __main__ block path manipulation
from datetime import datetime, timezone
from typing import Dict, Any, Optional, List

# Third-party imports
from loguru import logger
from arango.database import StandardDatabase
from arango.exceptions import (
    DocumentInsertError,
    DocumentRevisionError,  # Important for optimistic locking if implemented
    DocumentUpdateError,
    DocumentDeleteError,
    DocumentGetError,  # Specific error for get failures
    ArangoServerError,
    EdgeDefinitionListError,
    AQLQueryExecuteError,
    CollectionLoadError,  # Can occur when accessing collections
)

# Attempt to import local project dependencies
try:
    # Setup might only be needed for __main__, but CRUD needs config/utils
    from mcp_doc_retriever.arangodb.config import (
        COLLECTION_NAME,
        SEARCH_FIELDS,
        EDGE_COLLECTION_NAME,
        GRAPH_NAME,  # Used in logs/context
        RELATIONSHIP_TYPE_RELATED,  # Example type for tests
    )
    from mcp_doc_retriever.arangodb.embedding_utils import (
        get_text_for_embedding,
        get_embedding,
    )
    # Only needed if dynamically ensuring collections within functions (not typical)
    # from mcp_doc_retriever.arangodb.arango_setup import ensure_collection
except ImportError as e:
    logger.critical(
        f"Failed to import core dependencies. Ensure PYTHONPATH is set correctly or run from project root. Error: {e}"
    )
    # Exit if core components can't be imported, as the module is unusable
    sys.exit(1)


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
                     a UUID will be generated.

    Returns:
        A dictionary containing the metadata ('_id', '_key', '_rev') of the
        newly created document, or None if the operation failed.
    """
    action_uuid = str(uuid.uuid4())  # Unique ID for tracing this specific action
    with logger.contextualize(action="add_lesson", crud_id=action_uuid):
        # 1. Input Validation
        if not lesson_data.get("problem") or not lesson_data.get("solution"):
            logger.error(
                "Missing required fields: 'problem' or 'solution'. Cannot add lesson."
            )
            return None

        # 2. Ensure Key and Timestamp
        if "_key" not in lesson_data:
            lesson_data["_key"] = str(uuid.uuid4())
        lesson_key = lesson_data["_key"]  # Store for logging context

        if "timestamp_created" not in lesson_data:  # Use specific timestamp field
            lesson_data["timestamp_created"] = (
                datetime.now(timezone.utc)
                .isoformat(timespec="milliseconds")
                .replace("+00:00", "Z")
            )

        # Add context for potentially failing operation
        logger.debug(f"Preparing to add lesson with potential key: {lesson_key}")

        # 3. Embedding Generation (Critical Step)
        logger.debug("Generating embedding...")
        text_to_embed = get_text_for_embedding(lesson_data)
        if not text_to_embed:
            logger.warning(
                f"Could not extract text for embedding from lesson data (key: {lesson_key}). Proceeding without embedding."
            )
            lesson_data["embedding"] = (
                None  # Or handle as error if embedding is mandatory
            )
        else:
            embedding = get_embedding(text_to_embed)
            if (
                embedding is None
            ):  # Check specifically for None, as [] could be valid later
                logger.error(
                    f"Embedding generation failed for key {lesson_key}. Lesson not added."
                )
                return None
            lesson_data["embedding"] = embedding
            logger.debug(f"Embedding generated successfully ({len(embedding)} dims).")

        # 4. Database Insertion
        try:
            collection = db.collection(COLLECTION_NAME)
            logger.info(f"Inserting lesson vertex: {lesson_key}")
            meta = collection.insert(
                document=lesson_data,
                sync=True,  # Wait for operation to complete on server
                return_new=False,  # Don't need the full doc back
                silent=False,  # Raise exceptions on error (default)
            )
            logger.success(f"Lesson added: _key={meta['_key']}, _rev={meta['_rev']}")
            return meta  # Returns {'_id': '...', '_key': '...', '_rev': '...'}
        except DocumentInsertError as e:
            # More specific error for insertion failures (e.g., key exists)
            logger.error(f"Failed to insert lesson document (key: {lesson_key}): {e}")
            return None
        except (ArangoServerError, CollectionLoadError) as e:
            logger.error(
                f"Server or Collection error adding lesson (key: {lesson_key}): {e}"
            )
            return None
        except Exception as e:
            # Catch any other unexpected exceptions during the process
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
        logger.info(f"Attempting to retrieve lesson vertex: {lesson_key}")
        try:
            collection = db.collection(COLLECTION_NAME)
            doc = collection.get(lesson_key)  # Returns document dict or None
            if doc:
                logger.success(f"Lesson retrieved successfully: _key={doc['_key']}")
                return doc
            else:
                # Document not found is not an error, but expected behaviour
                logger.warning(f"Lesson not found: {lesson_key}")
                return None
        except DocumentGetError as e:
            # Specific error for get failures other than not found (e.g., access denied)
            logger.error(f"Error retrieving lesson document (key: {lesson_key}): {e}")
            return None
        except (ArangoServerError, CollectionLoadError) as e:
            logger.error(
                f"Server or Collection error getting lesson (key: {lesson_key}): {e}"
            )
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
    fields relevant to embedding (defined in SEARCH_FIELDS) are updated.

    Args:
        db: An initialized ArangoDB StandardDatabase connection object.
        lesson_key: The _key of the lesson document to update.
        update_data: A dictionary containing the fields to update.

    Returns:
        A dictionary containing the metadata ('_id', '_key', '_rev') of the
        updated document, or None if the update failed or the document
        was not found.
    """
    action_uuid = str(uuid.uuid4())
    with logger.contextualize(
        action="update_lesson", crud_id=action_uuid, lesson_key=lesson_key
    ):
        # 1. Basic Input Check
        if not update_data:
            logger.warning("No update data provided. Skipping update.")
            return None

        logger.info(f"Attempting to update lesson vertex: {lesson_key}")

        try:
            collection = db.collection(COLLECTION_NAME)

            # 2. Check Existence (optional but recommended before embedding regen)
            current_doc = collection.get(lesson_key)
            if not current_doc:
                logger.error(f"Lesson not found for update: {lesson_key}")
                return None

            # 3. Handle Embedding Regeneration
            embedding_fields_updated = any(
                field in update_data for field in SEARCH_FIELDS
            )
            if embedding_fields_updated:
                logger.debug("Relevant fields updated. Regenerating embedding...")
                merged_data_for_embedding = current_doc.copy()
                merged_data_for_embedding.update(update_data)

                text_to_embed = get_text_for_embedding(merged_data_for_embedding)
                if not text_to_embed:
                    logger.warning(
                        f"Could not extract text for embedding regeneration (key: {lesson_key}). Updating without changing embedding."
                    )
                    update_data.pop("embedding", None)
                else:
                    new_embedding = get_embedding(text_to_embed)
                    if new_embedding is None:
                        logger.error(
                            f"Embedding regeneration failed for {lesson_key}. Update aborted."
                        )
                        return None
                    update_data["embedding"] = new_embedding
                    logger.debug("New embedding generated and included in update.")

            # 4. Add/Update Timestamp
            update_data["timestamp_updated"] = (
                datetime.now(timezone.utc)
                .isoformat(timespec="milliseconds")
                .replace("+00:00", "Z")
            )

            # 5. Prepare the *full* document dictionary for the update method
            # Ensure '_key' is NOT in the incoming update_data itself, but add it here.
            if "_key" in update_data:
                logger.warning(
                    "'_key' field found in update_data and will be ignored during update preparation."
                )
                del update_data["_key"]
            if "_id" in update_data:
                del update_data["_id"]
            if "_rev" in update_data:
                del update_data["_rev"]  # Ignore unless using explicit revision check

            # Create the document to pass to update()
            doc_to_update = {"_key": lesson_key}
            doc_to_update.update(update_data)  # Merge the changes

            # 6. Perform Update using the correct signature
            meta = collection.update(
                document=doc_to_update,  # Pass the combined dict as the 'document' positional arg
                sync=True,
                keep_none=False,
                return_new=False,
                merge=True,  # Default, ensures fields are merged not replaced entirely
            )
            logger.success(f"Lesson updated: _key={meta['_key']}, _rev={meta['_rev']}")
            return meta

        except (DocumentUpdateError, DocumentRevisionError) as e:
            logger.error(f"Failed to update lesson document (key: {lesson_key}): {e}")
            return None
        except (ArangoServerError, CollectionLoadError) as e:
            logger.error(
                f"Server or Collection error updating lesson (key: {lesson_key}): {e}"
            )
            return None
        except TypeError as te:  # Catch the specific error reported
            logger.error(
                f"TypeError during collection.update call (likely incorrect arguments): {te}"
            )
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

    Uses an f-string for the collection name in the AQL REMOVE clause due to potential
    limitations with @@ collection binding in that specific context.

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
    lesson_id = (
        f"{COLLECTION_NAME}/{lesson_key}"  # Full _id for context and edge filtering
    )

    with logger.contextualize(
        action="delete_lesson", crud_id=action_uuid, lesson_id=lesson_id
    ):
        edge_deletion_errors = False  # Track edge cleanup status

        # --- Step 1: Optional Edge Cleanup ---
        if delete_edges:
            logger.info(
                f"Attempting edge cleanup for vertex {lesson_id} in edge collection '{EDGE_COLLECTION_NAME}'..."
            )
            # AQL Explanation:
            # - FOR edge IN @@edge_collection: Iterate through the specified edge collection.
            # - FILTER edge._from == @vertex_id OR edge._to == @vertex_id: Find edges connected to our vertex.
            # - REMOVE { _key: edge._key } IN {EDGE_COLLECTION_NAME}: Remove the found edge by its key.
            #   -> Uses f-string for target collection due to potential AQL parser limitations.
            # - RETURN OLD._key: Return the key for logging/counting purposes.
            aql = f"""
            FOR edge IN @@edge_collection
              FILTER edge._from == @vertex_id OR edge._to == @vertex_id
              REMOVE {{ _key: edge._key }} IN {EDGE_COLLECTION_NAME} OPTIONS {{ ignoreErrors: false }}
              RETURN OLD._key
            """
            # Note: ignoreErrors: false in REMOVE will make the *whole query* fail if *one* remove fails.
            # Set to true if partial success is acceptable for edge cleanup.

            bind_vars = {
                "vertex_id": lesson_id,
                "@edge_collection": EDGE_COLLECTION_NAME,  # Bind collection for FOR loop
            }

            try:
                cursor = db.aql.execute(aql, bind_vars=bind_vars, count=True)
                deleted_edge_keys = list(cursor)
                deleted_edge_count = cursor.count()
                logger.info(
                    f"AQL edge cleanup executed. Attempted removal of {deleted_edge_count} edge(s) connected to {lesson_id}."
                )
                if deleted_edge_count > 0:
                    logger.debug(
                        f"Removed edge keys during cleanup: {deleted_edge_keys}"
                    )
            except AQLQueryExecuteError as aqle:
                # Log the error but proceed to vertex deletion attempt
                logger.error(
                    f"AQL query execution failed during edge deletion for {lesson_id}: {aqle}. Query: {aql}, BindVars: {bind_vars}"
                )
                edge_deletion_errors = True
            except Exception as edge_e:
                # Log unexpected errors during AQL execution
                logger.exception(
                    f"Unexpected error during AQL edge deletion for {lesson_id}: {edge_e}"
                )
                edge_deletion_errors = True

        # --- Step 2: Delete the Vertex ---
        logger.info(f"Attempting to delete lesson vertex: {lesson_id}")
        try:
            collection = db.collection(COLLECTION_NAME)
            # Use ignore_missing=True for idempotency
            delete_result = collection.delete(
                document=lesson_key,  # Can pass key directly
                sync=True,
                ignore_missing=True,
            )

            # delete() returns True if deleted, False if ignore_missing=True and not found
            if delete_result:
                logger.success(f"Lesson vertex deleted successfully: _key={lesson_key}")
            else:
                logger.warning(
                    f"Lesson vertex not found or already deleted: _key={lesson_key}"
                )

            # Report edge deletion issues if they occurred
            if edge_deletion_errors:
                logger.warning(
                    f"Lesson vertex {lesson_key} deletion state achieved, but encountered errors/issues during associated edge cleanup."
                )
            # Return True because the desired state (vertex gone) is achieved.
            return True

        except DocumentDeleteError as e:
            # Less likely with ignore_missing=True, but possible for other reasons (e.g., permissions)
            logger.error(f"Failed to delete lesson vertex (key: {lesson_key}): {e}")
            return False
        except (ArangoServerError, CollectionLoadError) as e:
            logger.error(
                f"Server or Collection error deleting lesson vertex (key: {lesson_key}): {e}"
            )
            return False
        except Exception as e:
            logger.exception(
                f"Unexpected error during lesson vertex deletion (key: {lesson_key}): {e}"
            )
            return False


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
    # Construct full _id for _from and _to fields
    from_id = f"{COLLECTION_NAME}/{from_lesson_key}"
    to_id = f"{COLLECTION_NAME}/{to_lesson_key}"

    with logger.contextualize(
        action="add_relationship",
        crud_id=action_uuid,
        from_id=from_id,
        to_id=to_id,
        type=relationship_type,
    ):
        # 1. Input Validation
        if not rationale or not relationship_type:
            logger.error(
                "Rationale and relationship_type are required to add a relationship."
            )
            return None

        # 2. Prepare Edge Data
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
            # Ensure attributes don't overwrite core fields unintentionally
            safe_attributes = {
                k: v for k, v in attributes.items() if k not in edge_data
            }
            if len(safe_attributes) != len(attributes):
                logger.warning(
                    "Some provided attributes conflict with core edge fields and were ignored."
                )
            edge_data.update(safe_attributes)

        logger.info(
            f"Creating relationship edge from {from_id} to {to_id} (Type: {relationship_type})"
        )

        # 3. Database Insertion
        try:
            edge_collection = db.collection(EDGE_COLLECTION_NAME)
            meta = edge_collection.insert(
                document=edge_data, sync=True, return_new=False
            )
            logger.success(
                f"Relationship edge created successfully: _key={meta['_key']}, _rev={meta['_rev']}"
            )
            return meta
        except DocumentInsertError as e:
            logger.error(
                f"Failed to insert relationship edge ({from_id} -> {to_id}): {e}"
            )
            return None
        except (ArangoServerError, CollectionLoadError, EdgeDefinitionListError) as e:
            # EdgeDefinitionListError if edge collection not part of graph definition?
            logger.error(
                f"Server/Collection/Definition error adding relationship edge ({from_id} -> {to_id}): {e}"
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
    edge_id = f"{EDGE_COLLECTION_NAME}/{edge_key}"  # For logging context
    with logger.contextualize(
        action="delete_relationship", crud_id=action_uuid, edge_id=edge_id
    ):
        logger.info(f"Attempting to delete relationship edge with key: {edge_key}")
        try:
            edge_collection = db.collection(EDGE_COLLECTION_NAME)
            # Use ignore_missing=True for idempotency, important for cleanup
            deleted = edge_collection.delete(
                document=edge_key, sync=True, ignore_missing=True
            )
            if deleted:
                logger.success(
                    f"Relationship edge deleted successfully: _key={edge_key}"
                )
            else:
                logger.warning(
                    f"Relationship edge not found or already deleted: _key={edge_key}"
                )
            # Return True as the desired state (edge gone) is achieved
            return True
        except DocumentDeleteError as e:
            # Less likely with ignore_missing=True, but possible
            logger.error(f"Failed to delete relationship edge (key: {edge_key}): {e}")
            return False
        except (ArangoServerError, CollectionLoadError) as e:
            logger.error(
                f"Server or Collection error deleting relationship edge (key: {edge_key}): {e}"
            )
            return False
        except Exception as e:
            logger.exception(
                f"Unexpected error during relationship edge deletion (key: {edge_key}): {e}"
            )
            return False


# --- Standalone Execution Block for Verification ---
if __name__ == "__main__":
    # --- Configure Logging for Standalone Run ---
    # Ensure there's a default handler if none are configured by external setup
    if not logger._core.handlers:
        logger.add(
            sys.stderr,
            level="DEBUG",  # Use DEBUG to see detailed steps like embedding
            format="{time:YYYY-MM-DD HH:mm:ss.SSS} | {level: <7} | {name}:{function}:{line} | {message}",
            # colorize=True # Optional: Requires Loguru extras
        )
    # ---------------------------------------------

    logger.info("--- Running crud_api.py Standalone Verification ---")

    # Ensure src directory is in path for imports if run directly
    # Adjust depth (../../..) based on where crud_api.py lives relative to project root (e.g., src/)
    project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../.."))
    if project_root not in sys.path:
        sys.path.insert(0, project_root)
        logger.debug(f"Added project root to sys.path: {project_root}")

    # --- Imports needed ONLY for the standalone test ---
    try:
        from mcp_doc_retriever.arangodb.arango_setup import (
            connect_arango,
            ensure_database,
            ensure_collection,
            ensure_edge_collection,
            ensure_graph,
        )

        # Config import needed again if not already imported globally above
        from mcp_doc_retriever.arangodb.config import (
            RELATIONSHIP_TYPE_RELATED,
            COLLECTION_NAME,
            EDGE_COLLECTION_NAME,
            GRAPH_NAME,
        )

        logger.debug("Successfully imported setup and config for standalone test.")
    except ImportError as e:
        logger.critical(
            f"Standalone test import failed. Ensure PYTHONPATH includes project root or run from project root. Error: {e}"
        )
        sys.exit(1)
    # ---------------------------------------------------

    # --- Test Data ---
    # Generate unique keys for each run to avoid collisions if cleanup fails
    run_uuid = str(uuid.uuid4())[:8]  # Short UUID for readability in keys
    TEST_LESSON_DATA_1 = {
        "_key": f"crud_test_{run_uuid}_1",
        "problem": "Test problem 1",
        "solution": "Test solution 1.",
        "tags": ["test", "crud", "edge", run_uuid],
        "role": "Tester",
        "severity": "INFO",
        "context": "Crud test execution",
    }
    TEST_LESSON_DATA_2 = {
        "_key": f"crud_test_{run_uuid}_2",
        "problem": "Test problem 2",
        "solution": "Test solution 2.",
        "tags": ["test", "crud", "edge", run_uuid],
        "role": "Tester",
        "severity": "WARN",
        "context": "Crud test execution",
    }

    # --- State Variables ---
    test_key1 = TEST_LESSON_DATA_1["_key"]
    test_key2 = TEST_LESSON_DATA_2["_key"]
    edge_key_test_5_6 = None  # For edge created in test 5, deleted in 6
    edge_key_test_7 = None  # For edge created specifically for test 7
    db: Optional[StandardDatabase] = None
    passed_checks = []  # List to store names of passed checks
    failed_checks = []  # List to store names of failed checks

    # --- Test Execution ---
    try:
        # 1. Connect and Setup
        logger.info("--- Test Step 0: Connect & Setup ---")
        client = connect_arango()
        if not client:
            failed_checks.append("ConnectArango")
            raise ConnectionError("Failed to connect to ArangoDB")
        db = ensure_database(client)
        if not db:
            failed_checks.append("EnsureDatabase")
            raise ConnectionError(f"Failed to ensure database")

        ensure_collection(db, COLLECTION_NAME)
        ensure_edge_collection(db)  # Assuming signature: ensure_edge_collection(db)
        ensure_graph(db, GRAPH_NAME, EDGE_COLLECTION_NAME, COLLECTION_NAME)
        logger.info(f"Using database: {db.name}. Setup complete.")
        passed_checks.append("Setup")

        # --- Test 1: Add Lesson 1 ---
        logger.info(f"--- Test Step 1: Add Lesson 1 ({test_key1}) ---")
        add_meta1 = add_lesson(db, TEST_LESSON_DATA_1.copy())
        if add_meta1 and add_meta1.get("_key") == test_key1:
            logger.success(f"✅ Add 1 PASSED.")
            passed_checks.append("AddLesson1")
        else:
            logger.error("❌ Add 1 FAILED.")
            failed_checks.append("AddLesson1")
            # Decide if fatal: raise RuntimeError(f"Add 1 failed for key {test_key1}")

        # --- Test 2: Add Lesson 2 ---
        logger.info(f"--- Test Step 2: Add Lesson 2 ({test_key2}) ---")
        add_meta2 = add_lesson(db, TEST_LESSON_DATA_2.copy())
        if add_meta2 and add_meta2.get("_key") == test_key2:
            logger.success(f"✅ Add 2 PASSED.")
            passed_checks.append("AddLesson2")
        else:
            logger.error("❌ Add 2 FAILED.")
            failed_checks.append("AddLesson2")

        # --- Test 3: Get Lesson 1 ---
        logger.info(f"--- Test Step 3: Get Lesson 1 ({test_key1}) ---")
        retrieved_lesson = get_lesson(db, test_key1)
        if (
            retrieved_lesson
            and retrieved_lesson.get("_key") == test_key1
            and retrieved_lesson.get("problem") == TEST_LESSON_DATA_1["problem"]
        ):
            logger.success(f"✅ Get PASSED.")
            passed_checks.append("GetLesson1")
        else:
            logger.error(f"❌ Get FAILED. Retrieved: {retrieved_lesson}")
            failed_checks.append("GetLesson1")

        # --- Test 4: Update Lesson 1 ---
        logger.info(f"--- Test Step 4: Update Lesson 1 ({test_key1}) ---")
        update_payload = {
            "severity": "CRITICAL",
            "context": "Updated via standalone test.",
        }
        update_meta = update_lesson(
            db, test_key1, update_payload
        )  # Call the corrected function
        if update_meta:
            logger.info(f"Update command succeeded. New rev: {update_meta.get('_rev')}")
            updated_lesson = get_lesson(db, test_key1)  # Verify the change
            if updated_lesson and updated_lesson.get("severity") == "CRITICAL":
                logger.success(f"✅ Update PASSED (verified severity).")
                passed_checks.append("UpdateLesson1")
            else:
                logger.error(f"❌ Update verification FAILED. Doc: {updated_lesson}")
                failed_checks.append("UpdateLesson1Verify")
        else:
            logger.error(f"❌ Update command FAILED.")
            failed_checks.append("UpdateLesson1Command")  # This was the failure point

        # --- Test 5: Add Relationship ---
        logger.info(
            f"--- Test Step 5: Add Relationship ({test_key1} -> {test_key2}) ---"
        )
        # Ensure test_key1 still exists if update failed but didn't stop execution
        if (
            "UpdateLesson1Command" in failed_checks
            or "UpdateLesson1Verify" in failed_checks
        ):
            logger.warning(
                "Skipping Add Relationship test because previous update failed."
            )
            failed_checks.append("AddRelationshipSkipped")
        elif get_lesson(db, test_key1) and get_lesson(
            db, test_key2
        ):  # Check both keys exist
            edge_meta_5 = add_relationship(
                db,
                test_key1,
                test_key2,
                "Standalone test link 5-6",
                RELATIONSHIP_TYPE_RELATED,
            )
            if edge_meta_5 and "_key" in edge_meta_5:
                edge_key_test_5_6 = edge_meta_5["_key"]
                logger.success(
                    f"✅ Add Relationship PASSED. Edge Key: {edge_key_test_5_6}"
                )
                passed_checks.append("AddRelationship")
            else:
                logger.error(f"❌ Add Relationship FAILED.")
                failed_checks.append("AddRelationship")
        else:
            logger.warning(
                "Skipping Add Relationship test because one or both lesson keys don't exist."
            )
            failed_checks.append("AddRelationshipSkipped")

        # --- Test 6: Delete Relationship ---
        logger.info(f"--- Test Step 6: Delete Relationship ({edge_key_test_5_6}) ---")
        if edge_key_test_5_6:
            delete_edge_success = delete_relationship(db, edge_key_test_5_6)
            if delete_edge_success:
                logger.success(f"✅ Delete Relationship command SUCCEEDED.")
                # Verify edge deleted
                edge_check = None
                try:
                    edge_check = db.collection(EDGE_COLLECTION_NAME).get(
                        edge_key_test_5_6
                    )
                except Exception:
                    pass  # Ignore errors during verification check
                if edge_check is None:
                    logger.success(
                        "✅ Delete Relationship verification PASSED (edge not found)."
                    )
                    passed_checks.append("DeleteRelationship")
                else:
                    logger.error(
                        "❌ Delete Relationship verification FAILED (edge still exists)."
                    )
                    failed_checks.append("DeleteRelationshipVerify")
            else:
                logger.error(f"❌ Delete Relationship command FAILED.")
                failed_checks.append("DeleteRelationshipCommand")
        elif (
            "AddRelationship" in passed_checks
        ):  # Only fail skip if AddRelationship should have passed
            logger.warning(
                "Skipping Delete Relationship test (edge key not available despite Add passing?)."
            )
            failed_checks.append("DeleteRelationshipSkipped")
        else:  # AddRelationship failed or was skipped
            logger.info(
                "Skipping Delete Relationship test as Add Relationship did not succeed."
            )

        # --- Test 7: Delete Lesson 1 (with edge cleanup verification) ---
        logger.info(
            f"--- Test Step 7: Delete Lesson 1 ({test_key1}) with Edge Cleanup ---"
        )
        # Check if test_key1 should still exist before proceeding
        if "AddLesson1" not in passed_checks:
            logger.warning("Skipping Delete Lesson 1 test as AddLesson1 failed.")
            failed_checks.append("DeleteLesson1Skipped")
        elif get_lesson(db, test_key1) is None:
            logger.warning(
                f"Skipping Delete Lesson 1 test as document {test_key1} already seems deleted."
            )
            failed_checks.append("DeleteLesson1Skipped")
        else:
            # Create a specific edge to be cleaned up
            logger.debug("Creating specific edge for Test 7...")
            # Ensure target key exists for edge creation
            if get_lesson(db, test_key2):
                edge_meta_7 = add_relationship(
                    db,
                    test_key1,
                    test_key2,
                    "Temp link for delete test 7",
                    RELATIONSHIP_TYPE_RELATED,
                )
                if edge_meta_7 and "_key" in edge_meta_7:
                    edge_key_test_7 = edge_meta_7.get("_key")
                    logger.debug(f"Specific edge created for Test 7: {edge_key_test_7}")
                else:
                    logger.warning(
                        "Could not create specific edge for delete test 7. Edge cleanup cannot be verified."
                    )
                    edge_key_test_7 = None  # Ensure it's None if creation failed
            else:
                logger.warning(
                    f"Target lesson {test_key2} not found. Cannot create specific edge for Test 7."
                )
                edge_key_test_7 = None

            # Attempt to delete lesson 1, requesting edge cleanup
            delete_lesson1_success = delete_lesson(db, test_key1, delete_edges=True)

            if delete_lesson1_success:
                logger.success(f"✅ Delete Lesson 1 command SUCCEEDED.")
                # Verify lesson1 deleted
                lesson1_check = get_lesson(db, test_key1)
                if lesson1_check is None:
                    logger.success(
                        "✅ Delete Lesson 1 verification PASSED (doc not found)."
                    )
                    passed_checks.append("DeleteLesson1")
                else:
                    logger.error(
                        "❌ Delete Lesson 1 verification FAILED (doc still exists)."
                    )
                    failed_checks.append("DeleteLesson1Verify")

                # Verify the specific edge was deleted
                if edge_key_test_7:
                    edge7_check = None
                    try:
                        edge7_check = db.collection(EDGE_COLLECTION_NAME).get(
                            edge_key_test_7
                        )
                    except Exception:
                        pass  # Ignore errors during verification check
                    if edge7_check is None:
                        logger.success(
                            "✅ Edge cleanup verification PASSED (specific edge not found)."
                        )
                        passed_checks.append("DeleteLesson1EdgeCleanup")
                    else:
                        logger.error(
                            "❌ Edge cleanup verification FAILED (specific edge still exists)."
                        )
                        failed_checks.append("DeleteLesson1EdgeCleanupVerify")
                else:
                    logger.info(
                        "Skipping specific edge cleanup verification (no specific edge key was created)."
                    )

                test_key1 = None  # Mark as deleted for final cleanup logic
            else:
                logger.error(f"❌ Delete Lesson 1 command FAILED.")
                failed_checks.append("DeleteLesson1Command")

    except Exception as e:
        logger.exception(
            f"An unexpected error occurred during standalone verification tests: {e}"
        )
        failed_checks.append("UnexpectedException")

    # --- Final Cleanup ---
    finally:
        logger.info("--- Final Cleanup Phase ---")
        cleanup_errors = []
        if db:
            # Collect all known keys that *might* still exist
            keys_to_cleanup = [key for key in [test_key1, test_key2] if key]
            edges_to_cleanup = [
                key for key in [edge_key_test_5_6, edge_key_test_7] if key
            ]

            logger.debug(f"Attempting cleanup for lessons: {keys_to_cleanup}")
            logger.debug(f"Attempting cleanup for edges: {edges_to_cleanup}")

            # Delete edges first (less critical if vertex is gone, but good practice)
            for edge_key_to_delete in edges_to_cleanup:
                logger.info(f"Cleaning up edge: {edge_key_to_delete}")
                if not delete_relationship(db, edge_key_to_delete):
                    # Log error, but cleanup failure shouldn't fail the main tests
                    logger.error(f"Cleanup failed for edge {edge_key_to_delete}")
                    cleanup_errors.append(f"Edge {edge_key_to_delete}")

            # Delete vertices (requesting edge cleanup again just in case)
            for key_to_delete in keys_to_cleanup:
                logger.info(f"Cleaning up lesson: {key_to_delete}")
                if not delete_lesson(db, key_to_delete, delete_edges=True):
                    logger.error(f"Cleanup failed for lesson {key_to_delete}")
                    cleanup_errors.append(f"Lesson {key_to_delete}")

            if cleanup_errors:
                logger.warning(
                    f"Cleanup finished with errors for: {', '.join(cleanup_errors)}"
                )
            else:
                logger.info("Cleanup finished successfully.")
        else:
            logger.warning(
                "Could not attempt final cleanup (DB connection was not established)."
            )

        # --- Final Summary ---
        logger.info("-" * 60)
        total_checks = len(passed_checks) + len(failed_checks)
        logger.info(f"Standalone Verification Summary:")
        logger.info(
            f"  Passed Checks ({len(passed_checks)}): {', '.join(passed_checks)}"
        )
        if failed_checks:
            logger.error(
                f"  FAILED Checks ({len(failed_checks)}): {', '.join(failed_checks)}"
            )
            logger.error(f"\n❌ crud_api.py Standalone Verification FAILED.")
            sys.exit(1)  # Exit with non-zero code indicating failure
        else:
            # Ensure all expected checks were at least attempted (passed or failed)
            # Define expected check names based on the test flow
            expected_checks = {
                "Setup",
                "AddLesson1",
                "AddLesson2",
                "GetLesson1",
                "UpdateLesson1",  # This combines command + verify logic now
                "AddRelationship",
                "DeleteRelationship",
                "DeleteLesson1",
                "DeleteLesson1EdgeCleanup",
            }
            # Note: Skipped checks are in failed_checks list currently
            all_attempted = all(
                chk in passed_checks
                or f"{chk}Skipped" in failed_checks
                or f"{chk}Command" in failed_checks
                or f"{chk}Verify" in failed_checks
                or chk in failed_checks
                for chk in expected_checks
            )

            if not all_attempted:
                logger.warning(
                    "Some expected checks might not have been executed due to earlier failures."
                )

            logger.success(
                f"\n✅ crud_api.py Standalone Verification Completed Successfully! ({len(passed_checks)} checks passed)"
            )
            sys.exit(0)  # Exit with zero code indicating success
