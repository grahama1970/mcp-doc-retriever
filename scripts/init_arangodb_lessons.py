"""
Initializes the ArangoDB database, collection, and view for storing lessons learned.
"""

import os
import logging
from arango import ArangoClient
from arango.exceptions import (
    DatabaseCreateError,
    CollectionCreateError,
    ViewCreateError,
    DatabaseListError,
    CollectionListError,
)

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
        except (CollectionCreateError, CollectionListError) as e:
            logger.error(f"Error managing collection '{COLLECTION_NAME}': {e}")
            raise

        # Ensure the ArangoSearch view exists and is up-to-date.
        from arango.exceptions import ViewUpdateError, ArangoServerError # Import needed exceptions locally
        try:
            if not db.has_view(VIEW_NAME):
                logger.info(f"View '{VIEW_NAME}' not found. Creating...")
                db.create_view(VIEW_NAME, view_type="arangosearch", properties=VIEW_DEFINITION)
                logger.info(f"View '{VIEW_NAME}' created successfully.") # Changed to info
            else:
                logger.info(f"View '{VIEW_NAME}' already exists. Checking properties...")
                # Update properties if view exists to apply changes in definition
                current_props = db.view(VIEW_NAME).properties()
                # Basic check if properties seem different (doesn't compare perfectly nested structures always)
                # A more robust check might involve deep comparison if needed
                if current_props.get('links') != VIEW_DEFINITION.get('links') or \
                   current_props.get('consolidationPolicy') != VIEW_DEFINITION.get('consolidationPolicy') or \
                   current_props.get('primarySort') != VIEW_DEFINITION.get('primarySort'):
                    logger.info(f"Updating properties for view '{VIEW_NAME}'...")
                    db.update_view(VIEW_NAME, properties=VIEW_DEFINITION)
                    logger.info(f"View '{VIEW_NAME}' properties updated.") # Changed to info
                else:
                    logger.info(f"View '{VIEW_NAME}' properties seem up-to-date.")

        except (ViewCreateError, ViewUpdateError, ArangoServerError) as e: # Added ViewUpdateError
            logger.error(f"Failed to ensure view '{VIEW_NAME}': {e}")
            raise

        logger.info("ArangoDB initialization check completed successfully.") # Changed to info

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