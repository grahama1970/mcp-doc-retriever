# scripts/init_arangodb_lessons.py
"""
Initializes the ArangoDB database, collection, and view for storing lessons learned.

This script connects to ArangoDB using environment variables and ensures the necessary
database (`doc_retriever`), collection (`lessons_learned`), and ArangoSearch view
(`lessons_view`) are created idempotently.

Links:
- python-arango documentation: (Referencing local copy at git_downloader_test/arango_full/docs/)
  - Connection: git_downloader_test/arango_full/docs/connection.rst
  - Database Management: git_downloader_test/arango_full/docs/database.rst
  - Collection Management: git_downloader_test/arango_full/docs/collection.rst
  - View Management: git_downloader_test/arango_full/docs/view.rst

Environment Variables:
- ARANGO_HOST: The ArangoDB host URL (e.g., http://localhost:8529).
- ARANGO_USER: The ArangoDB username (e.g., root).
- ARANGO_PASSWORD: The ArangoDB password.
- ARANGO_DB: The target database name (e.g., doc_retriever).

Sample Usage (requires ArangoDB running and env vars set):
uv run python scripts/init_arangodb_lessons.py
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
    ViewListError,
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

# View definition for ArangoSearch
VIEW_DEFINITION = {
    "links": {
        COLLECTION_NAME: {
            "fields": {
                "problem": {"analyzers": ["text_en"]},
                "solution": {"analyzers": ["text_en"]},
                "context": {"analyzers": ["text_en"]},
                "example": {"analyzers": ["text_en"]},
                "tags": {"analyzers": ["identity"]}, # Treat tags as whole terms
            },
            "includeAllFields": False, # Only index specified fields
            "storeValues": "id", # Store document IDs for faster retrieval
            "trackListPositions": False,
        }
    },
    "primarySort": [{"field": "timestamp", "direction": "desc"}], # Optional: sort results by timestamp
    "primarySortCompression": "lz4",
    "storedValues": [ # Optional: store values directly in the view
        {"fields": ["timestamp", "severity", "role", "task", "phase"], "compression": "lz4"}
    ],
    "writebufferIdle": 64,
    "writebufferActive": 0,
    "writebufferSizeMax": 33554432,
    "consolidationIntervalMsec": 1000, # Consolidate frequently for smaller datasets/testing
    "consolidationPolicy": {
        "type": "tier",
        "segmentsMin": 1,
        "segmentsMax": 10,
        "segmentsBytesMax": 536870912,
        "segmentsBytesFloor": 2097152,
        "minScore": 0
    },
    "cleanupIntervalStep": 2
}

# BM25 scoring parameters (can be added to fields if needed, default is BM25)
# Example for 'problem' field:
# "problem": {"analyzers": ["text_en"], "searchField": True, "features": ["frequency", "norm", "position", "offset"], "trackListPositions": False, "cache": False, "primarySort": False, "primarySortCompression": "lz4", "storedValues": [], "analyzers": ["text_en"], "includeAllFields": False, "trackListPositions": False, "storeValues": "id", "fields": {}, "primarySort": [], "primarySortCompression": "lz4", "storedValues": [], "writebufferIdle": 64, "writebufferActive": 0, "writebufferSizeMax": 33554432, "consolidationIntervalMsec": 1000, "consolidationPolicy": {"type": "tier", "segmentsMin": 1, "segmentsMax": 10, "segmentsBytesMax": 536870912, "segmentsBytesFloor": 2097152, "minScore": 0}, "cleanupIntervalStep": 2}
# Note: BM25 is the default scorer in ArangoSearch, specific parameters (k1, b) can be set in AQL queries if needed.


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

        # Ensure the ArangoSearch view exists.
        try:
            if not db.has_view(VIEW_NAME):
                logger.info(f"ArangoSearch View '{VIEW_NAME}' not found. Creating...")
                # Create the view with the specified properties
                db.create_view(VIEW_NAME, view_type="arangosearch", properties=VIEW_DEFINITION)
                logger.info(f"ArangoSearch View '{VIEW_NAME}' created successfully.")
            else:
                logger.info(f"ArangoSearch View '{VIEW_NAME}' already exists.")
                # Optional: Check if properties match and update if necessary
                # view = db.view(VIEW_NAME)
                # current_props = view.properties()
                # if current_props != VIEW_DEFINITION: # Basic check, might need deeper comparison
                #     logger.warning(f"View '{VIEW_NAME}' exists but properties differ. Updating...")
                #     view.update_properties(VIEW_DEFINITION)
                #     logger.info(f"View '{VIEW_NAME}' properties updated.")

        except (ViewCreateError, ViewListError) as e:
            logger.error(f"Error managing ArangoSearch view '{VIEW_NAME}': {e}")
            raise

        logger.info("ArangoDB initialization check completed successfully.")

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