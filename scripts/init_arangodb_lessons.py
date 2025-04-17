"""
Initializes the ArangoDB database, collection, and view for storing lessons learned.
"""

import os
import logging
from arango.client import ArangoClient # Corrected import
from arango.exceptions import (
    DatabaseCreateError,
    CollectionCreateError,
    ViewCreateError,
    DatabaseListError,
    CollectionListError,
    CollectionTruncateError, # Added for truncate
    DocumentInsertError,     # Added for insert_many
)
import json # Added for loading seed data

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# --- Configuration ---
ARANGO_HOST = os.environ.get("ARANGO_HOST", "http://localhost:8529")
ARANGO_USER = os.environ.get("ARANGO_USER", "root")
ARANGO_PASSWORD = os.environ.get("ARANGO_PASSWORD", "openSesame") # Default from task, use env var in real scenarios
ARANGO_DB_NAME = os.environ.get("ARANGO_DB", "doc_retriever")
COLLECTION_NAME = "lessons_learned"
VIEW_NAME = "lessons_view"

# --- Constants for Fields (from user example) ---
from typing import List, Dict, Any # Add typing import
SEARCH_FIELDS: List[str] = ["problem", "solution", "context", "example"]
STORED_VALUE_FIELDS: List[str] = ["timestamp", "severity", "role", "task", "phase"]
TEXT_ANALYZER = "text_en"
TAG_ANALYZER = "identity"

# --- Improved View Definition (from user example) ---
VIEW_DEFINITION = {
    # Link the view to the collection
    "links": {
        COLLECTION_NAME: {
            # Define fields to be indexed and how
            "fields": {
                # Boost relevance for matches in 'problem' and 'solution'
                "problem": {"analyzers": [TEXT_ANALYZER], "boost": 2.0},
                "solution": {"analyzers": [TEXT_ANALYZER], "boost": 1.5},
                "context": {"analyzers": [TEXT_ANALYZER]},
                "example": {"analyzers": [TEXT_ANALYZER]},
                "tags": {"analyzers": [TAG_ANALYZER]}, # Use identity for exact tag matches
            },
            "includeAllFields": False, # Only index specified fields
            "storeValues": "id",      # Store doc IDs for efficient retrieval
            "trackListPositions": False, # Typically not needed for BM25, saves space
        }
    },
    # Default sort order (can be overridden in queries)
    "primarySort": [{"field": "timestamp", "direction": "desc"}],
    "primarySortCompression": "lz4",
    # Store specific fields directly in the view for potential optimizations
    "storedValues": [
        {"fields": STORED_VALUE_FIELDS, "compression": "lz4"}
    ],
    # Tuning parameters for index maintenance (adjust based on workload)
    "consolidationPolicy": {
        "type": "tier",         # Strategy for merging index segments
        "threshold": 0.1,       # Min ratio of segments to consolidate
        "segmentsMin": 1,       # Min number of segments before consolidation
        "segmentsMax": 10,      # Max number of segments before consolidation
        "segmentsBytesMax": 5 * 1024**3,  # Max total size (5GB)
        "segmentsBytesFloor": 2 * 1024**2,   # Segments smaller than this are always candidates (2MB)
    },
    "commitIntervalMsec": 1000, # How often changes are committed to the index (ms)
    "consolidationIntervalMsec": 10000, # How often consolidation checks run (ms)
}


def initialize_arangodb():
    """Connects to ArangoDB and ensures database, collection, and view exist."""
    logger.info(f"Connecting to ArangoDB at {ARANGO_HOST}")
    try:
        # Initialize the ArangoDB client.
        client = ArangoClient(hosts=ARANGO_HOST)

        # Connect to "_system" database as the user to manage databases.
        sys_db = client.db("_system", username=ARANGO_USER, password=ARANGO_PASSWORD)
        logger.info("Connected to _system database.")

        # Ensure the target database exists.
        try:
            if not sys_db.has_database(ARANGO_DB_NAME):
                logger.info(f"Database '{ARANGO_DB_NAME}' not found. Creating...")
                sys_db.create_database(ARANGO_DB_NAME)
                logger.info(f"Database '{ARANGO_DB_NAME}' created successfully.")
            else:
                logger.info(f"Database '{ARANGO_DB_NAME}' already exists.")
        except (DatabaseCreateError, DatabaseListError) as e:
            logger.error(f"Error managing database '{ARANGO_DB_NAME}': {e}")
            raise

        # Connect to the target database.
        db = client.db(ARANGO_DB_NAME, username=ARANGO_USER, password=ARANGO_PASSWORD)
        logger.info(f"Connected to target database '{ARANGO_DB_NAME}'.")

        # Ensure the collection exists.
        try:
            if not db.has_collection(COLLECTION_NAME):
                logger.info(f"Collection '{COLLECTION_NAME}' not found. Creating...")
                db.create_collection(COLLECTION_NAME)
                logger.info(f"Collection '{COLLECTION_NAME}' created successfully.")
            else:
                logger.info(f"Collection '{COLLECTION_NAME}' already exists.")
                # Verify collection type if it exists
                try:
                    collection_properties = db.collection(COLLECTION_NAME).properties()
                    collection_type = collection_properties.get('type')
                    # Type 2 is Document collection, Type 3 is Edge collection
                    if collection_type != 2:
                        logger.critical(f"CRITICAL ERROR: Collection '{COLLECTION_NAME}' exists but is not a Document collection (type {collection_type}). Please drop it manually and re-run.")
                        raise TypeError(f"Collection '{COLLECTION_NAME}' is not a Document collection.")
                    else:
                        logger.info(f"Collection '{COLLECTION_NAME}' confirmed as Document collection (type 2).")
                except Exception as prop_e:
                    logger.error(f"Failed to verify properties for collection '{COLLECTION_NAME}': {prop_e}")
                    raise

        except (CollectionCreateError, CollectionListError) as e:
            logger.error(f"Error managing collection '{COLLECTION_NAME}': {e}")
            raise

        # Ensure the ArangoSearch view exists and is up-to-date.
        from arango.exceptions import ViewUpdateError, ArangoServerError, ViewGetError # Import needed exceptions locally
        try:
            # Check if view exists by trying to get its details (which include properties)
            try:
                logger.debug(f"Checking if view '{VIEW_NAME}' exists by fetching details...")
                # db.view() returns a dict with view details if it exists
                view_details = db.view(VIEW_NAME)
                # Access the properties *within* the returned dictionary
                current_props = view_details.get('properties', {}) # Safely get properties dict
                logger.info(f"View '{VIEW_NAME}' already exists. Checking properties...")

                # Compare relevant parts of the properties dictionary
                # Note: This is a simplified comparison. A deep diff might be needed for full robustness.
                if current_props.get('links') != VIEW_DEFINITION.get('links') or \
                   current_props.get('consolidationPolicy') != VIEW_DEFINITION.get('consolidationPolicy') or \
                   current_props.get('primarySort') != VIEW_DEFINITION.get('primarySort'):
                    logger.info(f"Updating properties for view '{VIEW_NAME}'...")
                    # Use update_view which expects the properties dict directly
                    db.update_view(VIEW_NAME, properties=VIEW_DEFINITION)
                    logger.info(f"View '{VIEW_NAME}' properties updated.")
                else:
                    logger.info(f"View '{VIEW_NAME}' properties seem up-to-date.")
            except ViewGetError:
                # View does not exist, create it
                logger.info(f"View '{VIEW_NAME}' not found. Creating...")
                db.create_view(VIEW_NAME, view_type="arangosearch", properties=VIEW_DEFINITION)
                logger.info(f"View '{VIEW_NAME}' created successfully.")

        except (ViewCreateError, ViewUpdateError, ArangoServerError) as e:
            logger.error(f"Failed to ensure view '{VIEW_NAME}': {e}")
            raise

        logger.info("ArangoDB schema initialization check completed successfully.")

        # --- Step 4: Clear and Load Seed Data ---
        seed_file_path = "src/mcp_doc_retriever/arangodb/examples/lessons_learned_seed.json"
        try:
            logger.info(f"Attempting to clear collection '{COLLECTION_NAME}'...")
            lessons_collection = db.collection(COLLECTION_NAME)
            lessons_collection.truncate()
            logger.info(f"Collection '{COLLECTION_NAME}' cleared.")

            logger.info(f"Loading seed data from '{seed_file_path}'...")
            with open(seed_file_path, 'r') as f:
                seed_data = json.load(f)

            if "lessons" in seed_data and isinstance(seed_data["lessons"], list):
                lessons_to_insert = seed_data["lessons"]
                if lessons_to_insert:
                    logger.info(f"Inserting {len(lessons_to_insert)} documents one by one...")
                    inserted_count = 0
                    failed_count = 0
                    for lesson_doc in lessons_to_insert:
                        # Ensure embedding is generated if missing (mimics add_lesson logic)
                        # This assumes the init script should handle embedding for seed data
                        if "embedding" not in lesson_doc:
                             try:
                                 from mcp_doc_retriever.arangodb.embedding_utils import get_text_for_embedding, get_embedding
                                 text_to_embed = get_text_for_embedding(lesson_doc)
                                 if text_to_embed:
                                     embedding = get_embedding(text_to_embed)
                                     if embedding:
                                         lesson_doc["embedding"] = embedding
                                         logger.debug(f"Generated embedding for seed doc {lesson_doc.get('_key', 'N/A')}")
                                     else:
                                         logger.warning(f"Failed to generate embedding for seed doc {lesson_doc.get('_key', 'N/A')}")
                                 else:
                                      logger.warning(f"No text found to generate embedding for seed doc {lesson_doc.get('_key', 'N/A')}")
                             except Exception as emb_err:
                                 logger.error(f"Error generating embedding for seed doc {lesson_doc.get('_key', 'N/A')}: {emb_err}")
                                 # Decide if this should prevent insertion - for now, continue without embedding

                        doc_key = lesson_doc.get("_key", "N/A")
                        try:
                            # Use individual insert
                            lessons_collection.insert(lesson_doc, sync=True)
                            inserted_count += 1
                            logger.debug(f"Successfully inserted document: {doc_key}")
                        except DocumentInsertError as insert_err:
                            logger.error(f"Failed to insert document {doc_key}: {insert_err}")
                            failed_count += 1
                        except Exception as general_err:
                             logger.error(f"Unexpected error inserting document {doc_key}: {general_err}")
                             failed_count += 1

                    logger.info(f"Finished inserting documents. Success: {inserted_count}, Failed: {failed_count}")
                    if failed_count > 0:
                         logger.warning("Some documents failed to insert. Check logs above.")
                         # If any insertion fails, raise an error to signify incomplete seeding
                         raise DocumentInsertError(f"{failed_count} documents failed to insert.")
                else:
                    logger.info("No lessons found in seed file to insert.")
            else:
                logger.warning(f"Seed file '{seed_file_path}' does not contain a 'lessons' list.")

        except FileNotFoundError:
            logger.error(f"Seed file not found at '{seed_file_path}'. Cannot load data.")
            raise # Re-raise to indicate failure
        except json.JSONDecodeError as e:
            logger.error(f"Error decoding JSON from seed file '{seed_file_path}': {e}")
            raise
        except CollectionTruncateError as e:
            logger.error(f"Error clearing collection '{COLLECTION_NAME}': {e}")
            raise
        except DocumentInsertError as e:
            # Log the main error message, as .errors attribute is not guaranteed
            logger.error(f"Error during bulk insert into '{COLLECTION_NAME}': {e}")
            # No need to check for e.errors specifically here
            raise
        except Exception as e:
            logger.error(f"An unexpected error occurred during data loading: {e}")
            raise

        logger.info("ArangoDB initialization (schema check and data load) completed successfully.")


    except Exception as e:
        logger.error(f"An unexpected error occurred during ArangoDB initialization: {e}")
        raise


if __name__ == "__main__":
    logger.info("Starting ArangoDB initialization script...")
    try:
        initialize_arangodb()
        logger.info("Script finished successfully.")
    except Exception as e:
        logger.error(f"Script failed: {e}", exc_info=True)
        exit(1)