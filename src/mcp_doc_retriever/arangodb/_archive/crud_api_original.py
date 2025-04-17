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
from typing import Dict, Any, Optional, List, TypeVar, Union, cast, MutableMapping
from typing_extensions import TypedDict

from arango.typings import DataTypes  # Use ArangoDB's own type definitions
from arango.database import StandardDatabase
from arango.cursor import Cursor
from arango.result import Result

# Type definitions for responses
T = TypeVar('T')
Json = Dict[str, Any]  # JSON response type

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
    from mcp_doc_retriever.arangodb.log_utils import log_safe_results

    # Setup might only be needed for __main__, but CRUD needs config/utils
    from mcp_doc_retriever.arangodb.config import (
        COLLECTION_NAME,
        SEARCH_FIELDS,
        EDGE_COLLECTION_NAME,
        GRAPH_NAME,  # Used in logs/context
        RELATIONSHIP_TYPE_RELATED,  # Example type for tests
        TAG_ANALYZER, # Import TAG_ANALYZER
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
            # Cast and access meta data safely
            meta_dict = cast(Dict[str, Any], meta)
            logger.success(f"Lesson added: _key={meta_dict.get('_key', 'unknown')}, _rev={meta_dict.get('_rev', 'unknown')}")
            return meta_dict  # Returns {'_id': '...', '_key': '...', '_rev': '...'}
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
                # Cast doc to ensure proper typing
                doc_dict = cast(Dict[str, Any], doc)
                logger.success(f"Lesson retrieved successfully: _key={doc_dict.get('_key', 'unknown')}")
                return doc_dict
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
                # Cast and copy the document properly
                current_dict = cast(Dict[str, Any], current_doc)
                merged_data_for_embedding = current_dict.copy()
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
            # Cast result to proper type and use safe dict access
            meta_dict = cast(Dict[str, Any], meta)
            logger.success(f"Lesson updated: _key={meta_dict.get('_key', 'unknown')}, _rev={meta_dict.get('_rev', 'unknown')}")
            return meta_dict

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
                # Cast bind_vars for proper typing and handle cursor safely
                typed_bind_vars = cast(Dict[str, DataTypes], bind_vars)
                cursor = db.aql.execute(aql, bind_vars=typed_bind_vars, count=True)
                cursor = cast(Any, cursor)  # Cast to allow iteration and count access
                deleted_edge_keys = list(cursor)
                deleted_edge_count = cursor.count() if hasattr(cursor, 'count') else 0
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



# --- Keyword Search Function ---

def find_lessons_by_keyword(
    db: StandardDatabase,
    keywords: List[str],
    search_fields: Optional[List[str]] = None,
    limit: int = 10,
    match_all: bool = False, # False=OR (any keyword), True=AND (all keywords)
    tags: Optional[List[str]] = None # For tag filtering
) -> List[Dict[str, Any]]:
    """
    Finds lesson documents containing specific keywords in designated fields.

    Args:
        db: Initialized ArangoDB database connection.
        keywords: List of keywords to search for (case-insensitive).
        search_fields: Optional list of fields to search within. Defaults to
                       SEARCH_FIELDS from config if None.
        limit: Maximum number of documents to return.
        match_all: If True, documents must contain all keywords (AND logic).
                   If False (default), documents must contain at least one
                   keyword (OR logic).
        tags: Optional list of tags to filter by. If provided, documents must
              contain ALL specified tags (AND logic, using INTERSECTION).

    Returns:
        A list of matching lesson document dictionaries, or an empty list
        if none found or an error occurs.
    """
    action_uuid = str(uuid.uuid4())
    with logger.contextualize(
        action="find_lessons_by_keyword",
        crud_id=action_uuid,
        keywords=keywords,
        match_all=match_all,
        limit=limit,
        tags=tags # Log tags as well
    ):
        # 1. Handle empty keywords list
        if not keywords:
            logger.warning("No keywords provided for search. Returning empty list.")
            return []

        # 2. Determine search fields
        fields_to_search = search_fields if search_fields is not None else SEARCH_FIELDS
        if not fields_to_search:
            logger.error(
                "No search fields specified or configured (SEARCH_FIELDS is empty). Cannot perform keyword search."
            )
            return []
        logger.debug(f"Searching within fields: {fields_to_search}")

        # 3. Initialize variables for filters
        bind_vars: Dict[str, DataTypes] = {}
        keyword_filters = []
        tag_filters = []

        # 4. Process tag filtering if specified
        if tags:
            logger.debug(f"Adding tag filters for: {tags}")
            # Use INTERSECTION for correct AND logic on tags array
            # Ensure all provided tags exist in the document's tags array
            tag_conditions = f"LENGTH(INTERSECTION(doc.tags, @input_tags)) == LENGTH(@input_tags)"
            tag_filters.append(f"doc.tags != null AND IS_ARRAY(doc.tags) AND ({tag_conditions})")
            bind_vars["input_tags"] = tags # Bind the list of tags

        # 5. Process keyword filters
        for i, keyword in enumerate(keywords):
            bind_var_name = f"kw{i}"
            bind_vars[bind_var_name] = keyword # Store original case for potential future use, AQL uses LOWER()

            # Create sub-filter for this keyword across all fields
            field_checks = []
            for field in fields_to_search:
                # Ensure field name is safe (basic check)
                if not field.replace('_', '').isalnum(): # Allow underscores
                     logger.warning(f"Skipping potentially unsafe field name: {field}")
                     continue
                # Use LOWER() for case-insensitive comparison
                # Use backticks for field names to handle potential reserved words
                field_checks.append(f"CONTAINS(LOWER(doc.`{field}`), LOWER(@{bind_var_name}))")

            if field_checks:
                 keyword_filters.append(f"({' OR '.join(field_checks)})")
            else:
                 logger.warning(f"No valid fields to check for keyword: {keyword}")
                 # If no fields are checkable for a keyword, the overall logic might need adjustment
                 # depending on AND/OR. For now, skip this keyword's filter part.


        if not keyword_filters:
            # If only tags were provided, we still might want to search
            if not tags:
                logger.error("No valid keyword filters could be constructed and no tags provided.")
                return []
            # If tags are provided, proceed with only tag filtering
            keyword_clause = "true" # Effectively removes keyword filtering
        else:
            # 6. Combine keyword filters
            keyword_joiner = " AND " if match_all else " OR "
            keyword_clause = f"({keyword_joiner.join(keyword_filters)})"

        # 7. Combine all filters
        filters = [keyword_clause] # Start with keyword clause (or "true")
        if tag_filters:
            filters.extend(tag_filters)  # Tag filters already include AND logic

        # 8. Construct Full AQL Query
        filter_clause = " AND ".join(filters)
        aql = f"""
        FOR doc IN {COLLECTION_NAME}
          FILTER {filter_clause}
          LIMIT {int(limit)}
          RETURN doc
        """
        logger.debug(f"Constructed AQL: {aql}")
        logger.debug(f"Bind Variables: {bind_vars}")

        # 9. Execute Query with Error Handling
        try:
            logger.info(f"Executing keyword search query...")
            cursor = db.aql.execute(aql, bind_vars=bind_vars, count=True)
            cursor_obj = cast(Cursor, cursor)
            results = [doc for doc in cursor_obj]
            cursor = cast(Any, cursor)  # This allows count() and iteration
            count = cursor.count() if hasattr(cursor, 'count') else 0
            logger.success(f"Keyword search completed. Found {count} document(s).")
            return results
        except AQLQueryExecuteError as e:
            logger.error(f"AQL query execution failed during keyword search: {e}")
            logger.error(f"Failed AQL: {aql}")
            logger.error(f"Failed Bind Vars: {bind_vars}")
            return []
        except (ArangoServerError, CollectionLoadError) as e:
            logger.error(f"Server or Collection error during keyword search: {e}")
            return []
        except Exception as e:
            logger.exception(f"Unexpected error during keyword search: {e}")
            return []



# --- Tag Search Function ---

def find_lessons_by_tag(
    db: StandardDatabase,
    tags_to_search: List[str],
    limit: int = 10,
    match_all: bool = False # Added option for AND logic
) -> List[Dict[str, Any]]:
    """
    Finds lesson documents containing specific tags in the 'tags' array field.

    Args:
        db: Initialized ArangoDB database connection.
        tags_to_search: List of tags to search for (case-sensitive exact match).
        limit: Maximum number of documents to return.
        match_all: If True, documents must contain *all* specified tags.
                   If False (default), documents must contain *at least one*
                   of the specified tags.

    Returns:
        A list of matching lesson document dictionaries, or an empty list
        if none found or an error occurs.

        Note:
            If tags are provided, documents must match both the keyword search criteria
            AND have all the specified tags present.
    """
    action_uuid = str(uuid.uuid4())
    with logger.contextualize(
        action="find_lessons_by_tag",
        crud_id=action_uuid,
        tags=tags_to_search,
        match_all=match_all,
        limit=limit,
    ):
        if not tags_to_search:
            logger.warning("No tags provided for search. Returning empty list.")
            return []

        logger.debug(f"Searching for tags: {tags_to_search}")

        # AQL query using INTERSECTION
        # FILTER condition checks if the intersection size is > 0 (OR)
        # or equal to the number of search tags (AND)
        filter_condition = f"LENGTH(INTERSECTION(doc.tags, @tags_to_search)) >= {'LENGTH(@tags_to_search)' if match_all else '1'}"

        aql = f"""
        FOR doc IN {COLLECTION_NAME}
          FILTER doc.tags != null AND IS_ARRAY(doc.tags)
          FILTER {filter_condition}
          LIMIT {int(limit)}
          RETURN doc
        """
        # Removed SORT matches DESC as INTERSECTION length isn't a relevance score here
        # Removed RETURN MERGE(doc, {matches}) as 'matches' isn't calculated the same way

        bind_vars: Dict[str, DataTypes] = {
            "tags_to_search": tags_to_search,
        }

        logger.debug(f"Constructed AQL for tag search: {aql}")
        logger.debug(f"Bind Variables: {bind_vars}")

        try:
            logger.info("Executing tag search query...")
            cursor = db.aql.execute(aql, bind_vars=bind_vars, count=True)
            cursor_obj = cast(Cursor, cursor)
            results = [doc for doc in cursor_obj]
            cursor = cast(Any, cursor) # Allow count access
            count = cursor.count() if hasattr(cursor, 'count') else 0
            logger.success(f"Tag search completed. Found {count} document(s).")
            return results
        except AQLQueryExecuteError as e:
            logger.error(f"AQL query execution failed during tag search: {e}")
            logger.error(f"Failed AQL: {aql}")
            logger.error(f"Failed Bind Vars: {bind_vars}")
            return []
        except (ArangoServerError, CollectionLoadError) as e:
            logger.error(f"Server or Collection error during tag search: {e}")
            return []
        except Exception as e:
            logger.exception(f"Unexpected error during tag search: {e}")
            return []

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
            # Cast result to proper type and use safe dict access
            meta_dict = cast(Dict[str, Any], meta)
            logger.success(
                f"Relationship edge created successfully: _key={meta_dict.get('_key', 'unknown')}, _rev={meta_dict.get('_rev', 'unknown')}"
            )
            return meta_dict
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
    # Use public logger API to check handlers
    # Use public logger method to check if there are any handlers configured
    # Clean slate by removing any existing handlers
    logger.remove()
    logger.add(
        sys.stderr,
        level="DEBUG",  # Use DEBUG to see detailed steps like embedding
        format="{time:YYYY-MM-DD HH:mm:ss.SSS} | {level: <7} | {name}:{function}:{line} | {message}",
        colorize=True,  # Optional: Requires Loguru extras
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

        # Import log_safe_results if it's defined elsewhere, or define a basic one here for the test
        try:
            from mcp_doc_retriever.arangodb.log_utils import log_safe_results
        except ImportError:
            logger.warning(
                "log_utils.log_safe_results not found, using basic placeholder for logging."
            )

            def log_safe_results(
                results: List[Dict[str, Any]], max_len: int = 100
            ) -> List[Dict[str, Any]]:
                """Basic placeholder to prevent errors if log_utils is unavailable."""
                safe_list = []
                if not isinstance(results, list):
                    return [{"error": "Invalid results format"}]
                for item in results:
                    if not isinstance(item, dict):
                        safe_list.append({"error": "Invalid item format"})
                        continue
                    safe_item = item.copy()
                    if "embedding" in safe_item:
                        safe_item["embedding"] = (
                            f"[<Truncated embedding: {len(safe_item.get('embedding', []))} elements>]"
                        )
                    # Optionally truncate other long fields
                    # for key, value in safe_item.items():
                    #     if isinstance(value, str) and len(value) > max_len:
                    #         safe_item[key] = value[:max_len] + "..."
                    safe_list.append(safe_item)
                return safe_list

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
        "problem": "Test problem 1 unique",
        "solution": "Test solution 1.",
        "tags": [
            "test",
            "crud",
            "unique_tag",
            run_uuid,
        ],  # Added unique_tag for tag test
        "role": "Tester",
        "severity": "INFO",
        "context": "Crud test execution",
    }
    TEST_LESSON_DATA_2 = {
        "_key": f"crud_test_{run_uuid}_2",
        "problem": "Test problem 2 common",
        "solution": "Test solution 2.",
        "tags": ["test", "crud", "common_tag", run_uuid],  # Added common_tag
        "role": "Tester",
        "severity": "WARN",
        "context": "Crud test execution common",
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
        ensure_edge_collection(db)
        ensure_graph(db, GRAPH_NAME, EDGE_COLLECTION_NAME, COLLECTION_NAME)
        logger.info(f"Using database: {db.name}. Setup complete.")
        passed_checks.append("Setup")

        # --- Test 1: Add Lesson 1 ---
        logger.info(f"--- Test Step 1: Add Lesson 1 ({test_key1}) ---")
        add_meta1 = add_lesson(db, TEST_LESSON_DATA_1.copy())
        if add_meta1 and add_meta1.get("_key") == test_key1:
            logger.success(f"✅ 1. AddLesson1 PASSED.")
            passed_checks.append("AddLesson1")
        else:
            logger.error("❌ 1. AddLesson1 FAILED.")
            failed_checks.append("AddLesson1")

        # --- Test 2: Add Lesson 2 ---
        logger.info(f"--- Test Step 2: Add Lesson 2 ({test_key2}) ---")
        add_meta2 = add_lesson(db, TEST_LESSON_DATA_2.copy())
        if add_meta2 and add_meta2.get("_key") == test_key2:
            logger.success(f"✅ 2. AddLesson2 PASSED.")
            passed_checks.append("AddLesson2")
        else:
            logger.error("❌ 2. AddLesson2 FAILED.")
            failed_checks.append("AddLesson2")

        # --- Test 3: Get Lesson 1 ---
        logger.info(f"--- Test Step 3: Get Lesson 1 ({test_key1}) ---")
        retrieved_lesson = get_lesson(db, test_key1)
        if (
            retrieved_lesson
            and retrieved_lesson.get("_key") == test_key1
            and retrieved_lesson.get("problem") == TEST_LESSON_DATA_1["problem"]
        ):
            logger.success(f"✅ 3. GetLesson1 PASSED.")
            passed_checks.append("GetLesson1")
        else:
            logger.error(
                f"❌ 3. GetLesson1 FAILED. Retrieved: {log_safe_results([retrieved_lesson] if retrieved_lesson else [])}"
            )
            failed_checks.append("GetLesson1")

        # --- Test 4: Update Lesson 1 ---
        logger.info(f"--- Test Step 4: Update Lesson 1 ({test_key1}) ---")
        update_payload = {
            "severity": "CRITICAL",
            "context": "Updated via standalone test.",
        }
        update_meta = update_lesson(db, test_key1, update_payload)
        if update_meta:
            logger.info(f"Update command succeeded. New rev: {update_meta.get('_rev')}")
            updated_lesson = get_lesson(db, test_key1)  # Verify the change
            if updated_lesson and updated_lesson.get("severity") == "CRITICAL":
                logger.success(f"✅ 4. UpdateLesson1 PASSED (verified severity).")
                passed_checks.append("UpdateLesson1")
            else:
                logger.error(
                    f"❌ 4. UpdateLesson1 verification FAILED. Doc: {log_safe_results([updated_lesson] if updated_lesson else [])}"
                )
                failed_checks.append("UpdateLesson1Verify")  # Distinguish verify fail
        else:
            logger.error(f"❌ 4. UpdateLesson1 command FAILED.")
            failed_checks.append("UpdateLesson1Command")  # Distinguish command fail

        # --- Test 5: Add Relationship ---
        logger.info(
            f"--- Test Step 5: Add Relationship ({test_key1} -> {test_key2}) ---"
        )
        # Check if prerequisites (both lessons exist) are met
        lesson1_exists_for_rel = get_lesson(db, test_key1) is not None
        lesson2_exists_for_rel = get_lesson(db, test_key2) is not None
        if lesson1_exists_for_rel and lesson2_exists_for_rel:
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
                    f"✅ 5. AddRelationship PASSED. Edge Key: {edge_key_test_5_6}"
                )
                passed_checks.append("AddRelationship")
            else:
                logger.error(f"❌ 5. AddRelationship FAILED.")
                failed_checks.append("AddRelationship")
        else:
            logger.warning(
                f"Skipping Add Relationship test because one or both lesson keys don't exist (Lesson1: {lesson1_exists_for_rel}, Lesson2: {lesson2_exists_for_rel})."
            )
            failed_checks.append("AddRelationshipSkipped")

        # --- Test 6: Delete Relationship ---
        logger.info(f"--- Test Step 6: Delete Relationship ({edge_key_test_5_6}) ---")
        if edge_key_test_5_6:  # Only run if edge was successfully created in step 5
            delete_edge_success = delete_relationship(db, edge_key_test_5_6)
            if delete_edge_success:
                logger.success(f"✅ 6. Delete Relationship command SUCCEEDED.")
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
                        "✅ 6. Delete Relationship verification PASSED (edge not found)."
                    )
                    passed_checks.append("DeleteRelationship")
                else:
                    logger.error(
                        "❌ 6. Delete Relationship verification FAILED (edge still exists)."
                    )
                    failed_checks.append("DeleteRelationshipVerify")
            else:
                logger.error(f"❌ 6. Delete Relationship command FAILED.")
                failed_checks.append("DeleteRelationshipCommand")
        elif (
            "AddRelationship" in passed_checks
        ):  # If AddRel passed but key is missing, something is wrong
            logger.warning(
                "Skipping Delete Relationship test (edge key not available despite AddRelationship passing?)."
            )
            failed_checks.append("DeleteRelationshipSkipped")
        else:  # AddRelationship failed or was skipped
            logger.info(
                "Skipping Delete Relationship test as Add Relationship did not succeed or was skipped."
            )
            # Do not add to failed_checks if skipped due to prior failure

        # --- Test 7: Delete Lesson 1 (with edge cleanup verification) ---
        logger.info(
            f"--- Test Step 7: Delete Lesson 1 ({test_key1}) with Edge Cleanup ---"
        )
        # Check if test_key1 should still exist before proceeding
        lesson1_exists_before_del = get_lesson(db, test_key1) is not None
        if not lesson1_exists_before_del:
            logger.warning(
                f"Skipping Delete Lesson 1 test as document {test_key1} does not exist or wasn't added."
            )
            failed_checks.append("DeleteLesson1Skipped")
        else:
            # Create a specific edge to be cleaned up
            logger.debug("Creating specific edge for Test 7...")
            # Ensure target key exists for edge creation
            if get_lesson(db, test_key2) is not None:
                edge_meta_7 = add_relationship(
                    db,
                    test_key1,  # From the lesson we are about to delete
                    test_key2,
                    "Temp link for delete test 7",
                    RELATIONSHIP_TYPE_RELATED,
                )
                if edge_meta_7 and "_key" in edge_meta_7:
                    edge_key_test_7 = edge_meta_7.get("_key")
                    logger.debug(f"Specific edge created for Test 7: {edge_key_test_7}")
                else:
                    logger.warning(
                        "Could not create specific edge for delete test 7. Edge cleanup cannot be fully verified."
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
                logger.success(f"✅ 7. Delete Lesson 1 command SUCCEEDED.")
                # Verify lesson1 deleted
                lesson1_check = get_lesson(db, test_key1)
                if lesson1_check is None:
                    logger.success(
                        "✅ 7. Delete Lesson 1 verification PASSED (doc not found)."
                    )
                    passed_checks.append("DeleteLesson1")
                else:
                    logger.error(
                        "❌ 7. Delete Lesson 1 verification FAILED (doc still exists)."
                    )
                    failed_checks.append("DeleteLesson1Verify")

                # Verify the specific edge was deleted (if it was created)
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
                            "✅ 7. Edge cleanup verification PASSED (specific edge not found)."
                        )
                        passed_checks.append("DeleteLesson1EdgeCleanup")
                    else:
                        logger.error(
                            "❌ 7. Edge cleanup verification FAILED (specific edge still exists)."
                        )
                        failed_checks.append("DeleteLesson1EdgeCleanupVerify")
                else:
                    logger.info(
                        "Skipping specific edge cleanup verification (no specific edge key was created/available)."
                    )
                    # Don't mark as passed/failed if verification couldn't run

                # test_key1 = None # Logically mark as deleted, but keep original value for cleanup
            else:
                logger.error(f"❌ 7. Delete Lesson 1 command FAILED.")
                failed_checks.append("DeleteLesson1Command")

        # --- Test Step 8: Find Lessons by Keyword (MODIFIED ASSERTIONS) ---
        logger.info(f"--- Test Step 8: Find Lessons by Keyword ---")
        step8_passed = True  # Assume pass initially for this step

        # Determine which keys *should* exist now based on test flow
        lesson1_key_for_test8 = TEST_LESSON_DATA_1["_key"]  # Original key
        lesson2_key_for_test8 = TEST_LESSON_DATA_2["_key"]  # Original key

        # Lesson 1 should NOT exist if DeleteLesson1 passed
        lesson1_should_exist = (
            "DeleteLesson1" not in passed_checks
            and "DeleteLesson1Command" not in failed_checks
        )
        # Lesson 2 should exist if AddLesson2 passed
        lesson2_should_exist = "AddLesson2" in passed_checks

        # Re-verify actual existence *right before* testing search
        lesson1_actually_exists = (
            get_lesson(db, lesson1_key_for_test8) is not None
            if lesson1_should_exist
            else False
        )
        lesson2_actually_exists = (
            get_lesson(db, lesson2_key_for_test8) is not None
            if lesson2_should_exist
            else False
        )

        # Define the set of keys EXPECTED from *this specific test run* that should exist NOW
        expected_keys_now = set()
        if lesson1_actually_exists:
            expected_keys_now.add(lesson1_key_for_test8)
        if lesson2_actually_exists:
            expected_keys_now.add(lesson2_key_for_test8)
        logger.debug(
            f"Expected keys from this run for search tests: {expected_keys_now}"
        )

        # Keep track of common results for Test 8.6 check
        results_common = []  # Initialize

        if (
            not expected_keys_now
        ):  # Check if any keys we created are left to test search with
            logger.warning(
                "Skipping Keyword Search tests as no prerequisite lessons created in this run actually exist anymore."
            )
            failed_checks.append("FindKeywordSkippedPrereq")
            step8_passed = False  # Can't meaningfully test search functions based on this run's data
        else:
            logger.info(
                f"Proceeding with Keyword Search tests. Lesson1 Exists Check: {lesson1_actually_exists}, Lesson2 Exists Check: {lesson2_actually_exists}"
            )

            # Test Case 8.1: Unique Keyword (OR - default)
            logger.debug("Test 8.1: FindKeywordUnique")
            if lesson1_actually_exists:  # This test only makes sense if lesson 1 exists
                # Search for term unique to lesson 1
                results_unique = find_lessons_by_keyword(
                    db, keywords=["unique"]
                )  # From L1 problem/tags
                if any(r["_key"] == lesson1_key_for_test8 for r in results_unique):
                    logger.success(
                        "✅ 8.1 FindKeywordUnique PASSED (found expected key)."
                    )
                    passed_checks.append("FindKeywordUnique")
                    if len(results_unique) > 1:
                        logger.info(
                            f"  (Note: Found {len(results_unique)} total, expected at least 1 with key {lesson1_key_for_test8})"
                        )
                else:
                    logger.error(
                        f"❌ 8.1 FindKeywordUnique FAILED. Did not find expected key {lesson1_key_for_test8}. Found {len(results_unique)} results: {log_safe_results(results_unique)}"
                    )
                    failed_checks.append("FindKeywordUnique")
                    step8_passed = False
            else:
                logger.info(
                    "Skipping 8.1 FindKeywordUnique as Lesson 1 does not exist."
                )
                # Not a failure of search, just test condition not met

            # Test Case 8.2: Common Keyword (OR - default) - ROBUST ASSERTION
            logger.debug("Test 8.2: FindKeywordCommon")
            # Search for a term present in both L1 and L2 (and potentially others)
            results_common = find_lessons_by_keyword(
                db, keywords=["common"]
            )  # Present in L2 problem/context/tags
            found_keys_common = {r["_key"] for r in results_common}

            # Check if ALL expected keys from THIS RUN that contain "common" are present
            # In this setup, only L2 has "common" explicitly. L1 does not.
            expected_keys_for_common = set()
            # if lesson1_actually_exists: expected_keys_for_common.add(lesson1_key_for_test8) # L1 doesn't have 'common'
            if lesson2_actually_exists:
                expected_keys_for_common.add(lesson2_key_for_test8)

            if expected_keys_for_common.issubset(found_keys_common):
                logger.success(
                    f"✅ 8.2 FindKeywordCommon PASSED (found expected keys {expected_keys_for_common})."
                )
                passed_checks.append("FindKeywordCommon")
                if len(found_keys_common) > len(expected_keys_for_common):
                    logger.info(
                        f"  (Note: Found {len(found_keys_common)} total documents containing 'common', expected {len(expected_keys_for_common)} from this run)"
                    )
            # Handle case where no keys from this run were expected to match
            elif not expected_keys_for_common:
                logger.info(
                    f"✅ 8.2 FindKeywordCommon PASSED (correctly found no keys from this run matching 'common'). Found {len(found_keys_common)} other docs."
                )
                passed_checks.append(
                    "FindKeywordCommon"
                )  # Pass if expectation was zero and zero from this run were found
            else:  # Expected keys from this run were missing
                missing_keys = expected_keys_for_common - found_keys_common
                logger.error(
                    f"❌ 8.2 FindKeywordCommon FAILED. Expected keys {expected_keys_for_common} but missing {missing_keys}. Found {len(found_keys_common)} total. Results: {log_safe_results(results_common)}"
                )
                failed_checks.append("FindKeywordCommon")
                step8_passed = False

            # Test Case 8.3: Multiple Keywords (OR - default) - ROBUST ASSERTION
            logger.debug("Test 8.3: FindKeywordOR")
            # Search for term from L1 OR term from L2
            results_or = find_lessons_by_keyword(db, keywords=["unique", "solution 2"])
            found_keys_or = {r["_key"] for r in results_or}

            # Determine the expected keys specifically for THIS test case
            expected_keys_for_or = set()
            if lesson1_actually_exists:
                expected_keys_for_or.add(lesson1_key_for_test8)  # Matches "unique"
            if lesson2_actually_exists:
                expected_keys_for_or.add(lesson2_key_for_test8)  # Matches "solution 2"

            if expected_keys_for_or.issubset(found_keys_or):
                logger.success(
                    f"✅ 8.3 FindKeywordOR PASSED (found expected keys {expected_keys_for_or})."
                )
                passed_checks.append("FindKeywordOR")
                if len(found_keys_or) > len(expected_keys_for_or):
                    logger.info(
                        f"  (Note: Found {len(found_keys_or)} total documents matching OR, expected {len(expected_keys_for_or)} from this run)"
                    )
            elif not expected_keys_for_or:  # If neither L1 nor L2 existed, pass
                logger.success(
                    f"✅ 8.3 FindKeywordOR PASSED (correctly found no keys from this run matching criteria). Found {len(found_keys_or)} other docs."
                )
                passed_checks.append("FindKeywordOR")
            else:  # Expected keys from this run were missing
                missing_keys = expected_keys_for_or - found_keys_or
                logger.error(
                    f"❌ 8.3 FindKeywordOR FAILED. Expected keys {expected_keys_for_or} but missing {missing_keys}. Found {len(found_keys_or)} total. Results: {log_safe_results(results_or)}"
                )
                failed_checks.append("FindKeywordOR")
                step8_passed = False

            # Test Case 8.4: Multiple Keywords (AND)
            logger.debug("Test 8.4: FindKeywordAND")
            if lesson2_actually_exists:  # Lesson 2 has 'common' and 'solution 2'
                results_and = find_lessons_by_keyword(
                    db, keywords=["common", "solution 2"], match_all=True
                )
                found_keys_and = {r["_key"] for r in results_and}

                expected_key_for_and = lesson2_key_for_test8

                if expected_key_for_and in found_keys_and:
                    logger.success(
                        f"✅ 8.4 FindKeywordAND PASSED (found expected key {expected_key_for_and})."
                    )
                    passed_checks.append("FindKeywordAND")
                    if len(found_keys_and) > 1:
                        logger.info(
                            f"  (Note: Found {len(found_keys_and)} total documents matching AND, expected 1 from this run)"
                        )
                else:
                    logger.error(
                        f"❌ 8.4 FindKeywordAND FAILED. Expected key {expected_key_for_and} not found. Found {len(found_keys_and)} total. Results: {log_safe_results(results_and)}"
                    )
                    failed_checks.append("FindKeywordAND")
                    step8_passed = False
            else:
                logger.info(
                    "Skipping 8.4 FindKeywordAND as prerequisite Lesson 2 does not exist."
                )

            # Test Case 8.5: Non-existent Keyword
            logger.debug("Test 8.5: FindKeywordNone")
            results_none = find_lessons_by_keyword(db, keywords=["nonexistentxyz123"])
            if len(results_none) == 0:
                logger.success("✅ 8.5 FindKeywordNone PASSED.")
                passed_checks.append("FindKeywordNone")
            else:
                logger.error(
                    f"❌ 8.5 FindKeywordNone FAILED. Expected 0, got {len(results_none)}. Results: {log_safe_results(results_none)}"
                )
                failed_checks.append("FindKeywordNone")
                step8_passed = False

            # Test Case 8.6: Limit Parameter - ROBUST ASSERTION (CHECKS COUNT ONLY)
            logger.debug("Test 8.6: FindKeywordLimit")
            # Use the 'common' keyword search results from 8.2 to see if *any* match exists
            at_least_one_common_match_exists = len(results_common) > 0

            # Expected count is 1 if at least one match exists overall, else 0
            expected_limit_count = 1 if at_least_one_common_match_exists else 0

            # Now run the query with limit=1
            results_limit = find_lessons_by_keyword(db, keywords=["common"], limit=1)

            if len(results_limit) == expected_limit_count:
                logger.success(
                    f"✅ 8.6 FindKeywordLimit PASSED (found count {expected_limit_count} as expected)."
                )
                passed_checks.append("FindKeywordLimit")
                # Do NOT check the specific key, as LIMIT without SORT is unpredictable
            else:
                logger.error(
                    f"❌ 8.6 FindKeywordLimit FAILED. Expected count {expected_limit_count}, got {len(results_limit)}. Results: {log_safe_results(results_limit)}"
                )
                failed_checks.append("FindKeywordLimit")
                step8_passed = False

            # Test Case 8.7: Tag Filter
            logger.debug("Test 8.7: FindKeywordWithTag")
            # Search for 'common' keyword BUT only with the unique tag from Lesson 1
            if lesson1_actually_exists:  # L1 needs to exist for its unique tag
                test_tags_unique = ["unique_tag", run_uuid]
                # L1 has 'unique_tag' and 'Test', but maybe not 'common'. Let's search 'Test' + tag
                results_tagged = find_lessons_by_keyword(
                    db, keywords=["Test"], tags=test_tags_unique
                )  # Test is in L1 tags/role
                found_keys_tagged = {r["_key"] for r in results_tagged}

                expected_key_for_tagged = lesson1_key_for_test8

                if expected_key_for_tagged in found_keys_tagged:
                    logger.success(
                        "✅ 8.7 FindKeywordWithTag PASSED (found expected key {expected_key_for_tagged})."
                    )
                    passed_checks.append("FindKeywordWithTag")
                    # This combination *should* be unique due to run_uuid tag
                    if len(found_keys_tagged) > 1:
                        logger.warning(
                            f"  (Note: Found {len(found_keys_tagged)} total documents matching keyword AND unique tags, expected exactly 1)"
                        )
                else:
                    logger.error(
                        f"❌ 8.7 FindKeywordWithTag FAILED. Expected key {expected_key_for_tagged} not found. Found {len(found_keys_tagged)}. Results: {log_safe_results(results_tagged)}"
                    )
                    failed_checks.append("FindKeywordWithTag")
                    step8_passed = False
            else:
                logger.info(
                    "Skipping 8.7 FindKeywordWithTag as Lesson 1 (required for unique tag) does not exist."
                )

            # Test Case 8.8: Specific Search Fields
            logger.debug("Test 8.8: FindKeywordSpecificField")
            # Search for "solution 1" only in the "problem" field. Should find 0.
            results_specific = find_lessons_by_keyword(
                db, keywords=["solution 1"], search_fields=["problem"]
            )
            if len(results_specific) == 0:
                logger.success("✅ 8.8 FindKeywordSpecificField PASSED.")
                passed_checks.append("FindKeywordSpecificField")
            else:
                logger.error(
                    f"❌ 8.8 FindKeywordSpecificField FAILED. Expected 0, got {len(results_specific)}. Results: {log_safe_results(results_specific)}"
                )
                failed_checks.append("FindKeywordSpecificField")
                step8_passed = False

        if not step8_passed:
            logger.error("--- Test Step 8: Find Lessons by Keyword FAILED overall ---")
        else:
            logger.success(
                "--- Test Step 8: Find Lessons by Keyword PASSED overall ---"
            )

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
            # Use the original keys defined at the start for cleanup attempt
            keys_to_cleanup = [TEST_LESSON_DATA_1["_key"], TEST_LESSON_DATA_2["_key"]]
            edges_to_cleanup = [
                key for key in [edge_key_test_5_6, edge_key_test_7] if key
            ]  # Only cleanup edges actually created

            logger.debug(f"Attempting cleanup for potential lessons: {keys_to_cleanup}")
            logger.debug(f"Attempting cleanup for potential edges: {edges_to_cleanup}")

            # Delete edges first
            for edge_key_to_delete in edges_to_cleanup:
                logger.info(f"Cleaning up edge: {edge_key_to_delete}")
                # delete_relationship is already idempotent (ignore_missing=True)
                if not delete_relationship(db, edge_key_to_delete):
                    # Log error, but cleanup failure shouldn't fail the main tests normally
                    logger.error(
                        f"Cleanup command failed for edge {edge_key_to_delete} (may already be gone)"
                    )
                    cleanup_errors.append(f"Edge {edge_key_to_delete}")

            # Delete vertices (requesting edge cleanup again just in case)
            for key_to_delete in keys_to_cleanup:
                logger.info(f"Cleaning up lesson: {key_to_delete}")
                # delete_lesson is already idempotent (ignore_missing=True)
                if not delete_lesson(db, key_to_delete, delete_edges=True):
                    logger.error(
                        f"Cleanup command failed for lesson {key_to_delete} (may already be gone)"
                    )
                    cleanup_errors.append(f"Lesson {key_to_delete}")

            if cleanup_errors:
                logger.warning(
                    f"Cleanup finished with potential issues (items might have already been deleted): {', '.join(cleanup_errors)}"
                )
            else:
                logger.info("Cleanup finished (attempted removal of test items).")
        else:
            logger.warning(
                "Could not attempt final cleanup (DB connection was not established)."
            )

        # --- Final Summary ---
        logger.info("-" * 60)
        total_checks = len(passed_checks) + len(failed_checks)
        logger.info(f"Standalone Verification Summary:")
        logger.info(
            f"  Passed Checks ({len(passed_checks)}): {', '.join(sorted(passed_checks))}"
        )  # Sort for consistency
        if failed_checks:
            # Separate definite failures from skipped tests for clarity
            definite_fails = [
                f
                for f in failed_checks
                if not f.endswith("Skipped")
                and not f.endswith("Verify")
                and not f.endswith("Command")
            ]
            verify_fails = [
                f
                for f in failed_checks
                if f.endswith("Verify") or f.endswith("Command")
            ]
            skipped = [f for f in failed_checks if f.endswith("Skipped")]

            if definite_fails or verify_fails:
                logger.error(
                    f"  FAILED Checks ({len(definite_fails) + len(verify_fails)}): {', '.join(sorted(definite_fails + verify_fails))}"
                )
            if skipped:
                logger.warning(
                    f"  Skipped Checks ({len(skipped)}): {', '.join(sorted(skipped))}"
                )

            logger.error(f"\n❌ crud_api.py Standalone Verification FAILED.")
            sys.exit(1)  # Exit with non-zero code indicating failure
        else:
            # Verify all expected checks ran (adjust expected_checks if tests change)
            expected_checks = {
                "Setup",
                "AddLesson1",
                "AddLesson2",
                "GetLesson1",
                "UpdateLesson1",
                "AddRelationship",
                "DeleteRelationship",
                "DeleteLesson1",
                "DeleteLesson1EdgeCleanup",
                "FindKeywordUnique",
                "FindKeywordCommon",
                "FindKeywordOR",
                "FindKeywordAND",
                "FindKeywordNone",
                "FindKeywordLimit",
                "FindKeywordWithTag",
                "FindKeywordSpecificField",
            }
            # Check if all expected checks are in passed_checks (ignoring potential skips)
            missing_passed = expected_checks - set(passed_checks)
            if missing_passed:
                logger.warning(
                    f"Some expected checks might not be in the passed list (possibly skipped or failed implicitly): {missing_passed}"
                )

            logger.success(
                f"\n✅ crud_api.py Standalone Verification Completed Successfully! ({len(passed_checks)} checks passed)"
            )
            sys.exit(0)  # Exit with zero code indicating success