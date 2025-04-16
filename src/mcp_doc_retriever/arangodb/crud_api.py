# src/mcp_doc_retriever/arangodb/crud_api.py
import uuid
from datetime import datetime, timezone
from typing import Dict, Any, Optional, List

from loguru import logger
from arango.database import StandardDatabase
from arango.exceptions import (
    DocumentInsertError,
    DocumentRevisionError,
    DocumentNotFoundError,
    DocumentUpdateError,
    DocumentDeleteError,
    ArangoServerError,
)

# Import shared config and utilities
from .config import (
    COLLECTION_NAME,
    SEARCH_FIELDS,
)  # Need SEARCH_FIELDS to know what impacts embedding
from .embedding_utils import get_text_for_embedding, get_embedding

# --- CRUD Functions ---


def add_lesson(
    db: StandardDatabase, lesson_data: Dict[str, Any]
) -> Optional[Dict[str, Any]]:
    """
    Adds a new lesson document to the collection, including embedding generation.

    Args:
        db: The ArangoDB database connection.
        lesson_data: A dictionary containing the lesson details. Must include
                     at least 'problem' and 'solution'. Timestamp and _key
                     will be added if missing.

    Returns:
        The metadata (_key, _id, _rev) of the inserted document, or None if insertion fails.
    """
    action_uuid = str(uuid.uuid4())
    with logger.contextualize(action="add_lesson", crud_id=action_uuid):
        if not lesson_data.get("problem") or not lesson_data.get("solution"):
            logger.error("Cannot add lesson: Missing 'problem' or 'solution' field.")
            return None

        # Ensure essential fields are present
        if "_key" not in lesson_data:
            lesson_data["_key"] = str(uuid.uuid4())
            logger.debug(f"Generated _key: {lesson_data['_key']}")
        if "timestamp" not in lesson_data:
            lesson_data["timestamp"] = (
                datetime.now(timezone.utc)
                .isoformat(timespec="milliseconds")
                .replace("+00:00", "Z")
            )

        # --- Embedding Generation ---
        logger.debug("Generating embedding for new lesson...")
        text_to_embed = get_text_for_embedding(lesson_data)
        embedding = get_embedding(text_to_embed)

        if embedding:
            lesson_data["embedding"] = embedding
            logger.debug("Embedding generated successfully.")
        else:
            # Critical decision: Allow adding without embedding or fail? Let's fail for consistency.
            logger.error(
                f"Embedding generation failed for key {lesson_data['_key']}. Lesson not added."
            )
            return None  # Indicate failure

        # --- Database Insertion ---
        try:
            collection = db.collection(COLLECTION_NAME)
            logger.info(
                f"Inserting new lesson document with key: {lesson_data['_key']}"
            )
            meta = collection.insert(
                lesson_data, sync=True, return_new=False
            )  # return_new=False saves bandwidth
            logger.success(
                f"Lesson added successfully: _key={meta['_key']}, _rev={meta['_rev']}"
            )
            return meta  # Return ArangoDB metadata (_key, _id, _rev)
        except (DocumentInsertError, ArangoServerError) as e:
            logger.error(f"Failed to add lesson (key: {lesson_data['_key']}): {e}")
            return None
        except Exception as e:
            logger.exception(
                f"Unexpected error during lesson addition (key: {lesson_data['_key']}): {e}"
            )
            return None


def get_lesson(db: StandardDatabase, lesson_key: str) -> Optional[Dict[str, Any]]:
    """
    Retrieves a lesson document by its _key.

    Args:
        db: The ArangoDB database connection.
        lesson_key: The _key of the document to retrieve.

    Returns:
        The lesson document dictionary, or None if not found.
    """
    action_uuid = str(uuid.uuid4())
    with logger.contextualize(
        action="get_lesson", crud_id=action_uuid, lesson_key=lesson_key
    ):
        try:
            collection = db.collection(COLLECTION_NAME)
            logger.info(f"Attempting to retrieve lesson with key: {lesson_key}")
            doc = collection.get(lesson_key)
            if doc:
                logger.success(f"Lesson retrieved successfully: _key={doc['_key']}")
                return doc
            else:
                # collection.get returns None if not found, no exception needed here
                logger.warning(f"Lesson not found with key: {lesson_key}")
                return None
        except ArangoServerError as e:
            logger.error(f"Server error retrieving lesson (key: {lesson_key}): {e}")
            return None
        except Exception as e:
            logger.exception(
                f"Unexpected error retrieving lesson (key: {lesson_key}): {e}"
            )
            return None


def update_lesson(
    db: StandardDatabase, lesson_key: str, update_data: Dict[str, Any]
) -> Optional[Dict[str, Any]]:
    """
    Updates an existing lesson document. Regenerates embedding if relevant fields change.

    Args:
        db: The ArangoDB database connection.
        lesson_key: The _key of the document to update.
        update_data: A dictionary containing the fields and new values to update.

    Returns:
        The metadata (_key, _id, _rev, _old_rev) of the updated document, or None if update fails.
    """
    action_uuid = str(uuid.uuid4())
    with logger.contextualize(
        action="update_lesson", crud_id=action_uuid, lesson_key=lesson_key
    ):
        if not update_data:
            logger.warning("No update data provided. Skipping update.")
            return None

        # Check if any fields relevant to embedding are being updated
        embedding_fields_updated = any(
            field in update_data for field in SEARCH_FIELDS
        )  # SEARCH_FIELDS from config

        if embedding_fields_updated:
            logger.debug(
                "Embedding-relevant fields detected in update. Fetching current doc for regeneration..."
            )
            # Fetch the current full document to regenerate embedding
            current_doc = get_lesson(db, lesson_key)
            if not current_doc:
                logger.error(
                    f"Cannot update lesson: Document not found with key {lesson_key}"
                )
                return None  # Doc must exist to update

            # Create a merged view for embedding generation
            merged_data_for_embedding = current_doc.copy()
            merged_data_for_embedding.update(update_data)

            logger.debug("Regenerating embedding for updated lesson...")
            text_to_embed = get_text_for_embedding(merged_data_for_embedding)
            new_embedding = get_embedding(text_to_embed)

            if new_embedding:
                update_data["embedding"] = (
                    new_embedding  # Add the new embedding to the update payload
                )
                logger.debug("New embedding generated and included in update.")
            else:
                logger.error(
                    f"Embedding regeneration failed for key {lesson_key}. Update aborted."
                )
                return None  # Fail update if embedding regen fails

        # Ensure timestamp is updated on modification? Optional.
        # update_data["timestamp"] = datetime.now(timezone.utc).isoformat(timespec='milliseconds').replace('+00:00', 'Z')

        # --- Database Update ---
        try:
            collection = db.collection(COLLECTION_NAME)
            logger.info(f"Updating lesson document with key: {lesson_key}")
            # Use update method. merge=True is default. keep_none=False prevents setting fields to null accidentally.
            meta = collection.update(
                lesson_key,
                update_data,
                sync=True,
                keep_none=False,
                return_new=False,
                return_old=False,
            )
            logger.success(
                f"Lesson updated successfully: _key={meta['_key']}, _rev={meta['_rev']}"
            )
            return meta  # Return metadata including new _rev
        except DocumentNotFoundError:
            logger.error(
                f"Failed to update lesson: Document not found with key {lesson_key}"
            )
            return None
        except (DocumentUpdateError, DocumentRevisionError, ArangoServerError) as e:
            logger.error(f"Failed to update lesson (key: {lesson_key}): {e}")
            return None
        except Exception as e:
            logger.exception(
                f"Unexpected error during lesson update (key: {lesson_key}): {e}"
            )
            return None


def delete_lesson(db: StandardDatabase, lesson_key: str) -> bool:
    """
    Deletes a lesson document by its _key.

    Args:
        db: The ArangoDB database connection.
        lesson_key: The _key of the document to delete.

    Returns:
        True if deletion was successful, False otherwise.
    """
    action_uuid = str(uuid.uuid4())
    with logger.contextualize(
        action="delete_lesson", crud_id=action_uuid, lesson_key=lesson_key
    ):
        try:
            collection = db.collection(COLLECTION_NAME)
            logger.info(f"Attempting to delete lesson with key: {lesson_key}")
            deleted = collection.delete(lesson_key, sync=True, return_old=False)
            # delete returns True on success, False if ignore_missing=True and not found
            # Raises DocumentNotFoundError if ignore_missing=False (default) and not found
            if deleted:  # Should always be true if no exception
                logger.success(f"Lesson deleted successfully: _key={lesson_key}")
                return True
            else:
                # This path should not be reached with default ignore_missing=False
                logger.warning(
                    f"Lesson deletion call returned False for key: {lesson_key}"
                )
                return False

        except DocumentNotFoundError:
            logger.error(
                f"Failed to delete lesson: Document not found with key {lesson_key}"
            )
            return False
        except (DocumentDeleteError, ArangoServerError) as e:
            logger.error(f"Failed to delete lesson (key: {lesson_key}): {e}")
            return False
        except Exception as e:
            logger.exception(
                f"Unexpected error during lesson deletion (key: {lesson_key}): {e}"
            )
            return False
