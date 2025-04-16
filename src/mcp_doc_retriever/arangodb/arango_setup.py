# arango_setup.py
import uuid
from datetime import datetime, timezone
from typing import Dict, Any, Optional

from loguru import logger
from arango import ArangoClient
from arango.database import StandardDatabase
from arango.collection import StandardCollection
from arango.view import View
from arango.exceptions import (
    ArangoClientError,
    ArangoServerError,
    DatabaseCreateError,
    CollectionCreateError,
    ViewCreateError,
    ViewUpdateError,
    DocumentInsertError,
)

# Import config and embedding utils
from config import (
    ARANGO_HOST,
    ARANGO_USER,
    ARANGO_PASSWORD,
    ARANGO_DB_NAME,
    COLLECTION_NAME,
    VIEW_NAME,
    VIEW_DEFINITION,
)
from .embedding_utils import get_text_for_embedding, get_embedding

# --- Connection & Resource Management ---


def connect_arango() -> ArangoClient:
    """Establishes and verifies connection to ArangoDB."""
    with logger.contextualize(action="connect_arango"):
        logger.info(f"Connecting to ArangoDB at {ARANGO_HOST}")
        try:
            # Initialize the client
            client = ArangoClient(hosts=ARANGO_HOST)
            # Verify connection by making a simple call (e.g., getting server version)
            client.version()
            logger.success("Connection established successfully.")
            return client
        except ArangoClientError as e:
            # Log connection errors clearly
            logger.error(f"ArangoDB connection failed: {e}")
            raise  # Re-raise the exception to halt execution if connection fails


def ensure_database(client: ArangoClient) -> StandardDatabase:
    """Ensures the target database exists, creating it if necessary."""
    with logger.contextualize(action="ensure_database", database=ARANGO_DB_NAME):
        try:
            # Connect to the system database to check/create other databases
            sys_db = client.db(
                "_system", username=ARANGO_USER, password=ARANGO_PASSWORD
            )
            if not sys_db.has_database(ARANGO_DB_NAME):
                logger.info(f"Database '{ARANGO_DB_NAME}' not found. Creating...")
                sys_db.create_database(ARANGO_DB_NAME)
                logger.success(f"Database '{ARANGO_DB_NAME}' created.")
            else:
                logger.info(f"Database '{ARANGO_DB_NAME}' already exists.")
            # Return a database object connected to the target database
            return client.db(
                ARANGO_DB_NAME, username=ARANGO_USER, password=ARANGO_PASSWORD
            )
        except (DatabaseCreateError, ArangoServerError) as e:
            logger.error(f"Failed to ensure database '{ARANGO_DB_NAME}': {e}")
            raise


def ensure_collection(db: StandardDatabase) -> StandardCollection:
    """Ensures the target collection exists, creating it if necessary."""
    with logger.contextualize(action="ensure_collection", collection=COLLECTION_NAME):
        try:
            if not db.has_collection(COLLECTION_NAME):
                logger.info(f"Collection '{COLLECTION_NAME}' not found. Creating...")
                # Create the collection (can add options like waitForSync if needed)
                collection = db.create_collection(COLLECTION_NAME)
                logger.success(f"Collection '{COLLECTION_NAME}' created.")
                return collection
            else:
                logger.info(f"Collection '{COLLECTION_NAME}' already exists.")
                # Get the existing collection object
                return db.collection(COLLECTION_NAME)
        except (CollectionCreateError, ArangoServerError) as e:
            logger.error(f"Failed to ensure collection '{COLLECTION_NAME}': {e}")
            raise


def ensure_view(db: StandardDatabase) -> View:
    """
    Ensures the ArangoSearch view exists and its properties match the definition.
    Creates the view if it doesn't exist, or updates properties if it does.
    """
    with logger.contextualize(action="ensure_view", view=VIEW_NAME):
        try:
            if not db.has_view(VIEW_NAME):
                logger.info(
                    f"View '{VIEW_NAME}' not found. Creating with definition..."
                )
                # Create view using type 'arangosearch' and properties from config
                db.create_view(
                    VIEW_NAME, view_type="arangosearch", properties=VIEW_DEFINITION
                )
                logger.success(f"View '{VIEW_NAME}' created successfully.")
            else:
                logger.info(
                    f"View '{VIEW_NAME}' exists. Ensuring properties match definition..."
                )
                view = db.view(VIEW_NAME)
                # Use replace_properties for simplicity and idempotency.
                # This ensures the view configuration matches VIEW_DEFINITION exactly.
                # It overwrites existing properties.
                view.replace_properties(VIEW_DEFINITION)
                logger.success(f"View '{VIEW_NAME}' properties ensured/updated.")
            # Return the view object
            return db.view(VIEW_NAME)
        except (ViewCreateError, ViewUpdateError, ArangoServerError) as e:
            logger.error(f"Failed to ensure view '{VIEW_NAME}': {e}")
            raise


# --- Sample Data Handling ---


def create_sample_lesson_data() -> Optional[Dict[str, Any]]:
    """Creates sample lesson data dictionary and generates its embedding."""
    # Define the base document structure
    doc_data = {
        "_key": str(uuid.uuid4()),  # Generate a unique client-side key
        "timestamp": datetime.now(timezone.utc)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z"),
        "severity": "WARN",
        "role": "Senior Coder",
        "task": "Task 1.4",
        "phase": "Debugging",
        "problem": "Playwright download failed intermittently in CI environment.",
        "solution": "Increased default timeout for Playwright browser launch and added retry logic for download command.",
        "tags": ["playwright", "ci", "timeout", "download", "retry"],
        "context": "Running e2e tests in GitHub Actions workflow.",
        "example": "await page.goto(url, { timeout: 60000 }); // Increased timeout",
    }
    # Prepare text and generate embedding
    text_to_embed = get_text_for_embedding(doc_data)
    embedding = get_embedding(text_to_embed)  # Calls function from embedding_utils

    if embedding:
        doc_data["embedding"] = embedding  # Add embedding to the document
        return doc_data
    else:
        # Log error if embedding generation failed
        logger.error(
            f"Embedding generation failed for sample doc (key: {doc_data['_key']}). Sample document will not be created."
        )
        return None  # Return None to indicate failure


def insert_sample_if_empty(collection: StandardCollection) -> None:
    """Inserts a sample document (with embedding) if the collection is empty."""
    with logger.contextualize(action="insert_sample", collection=collection.name):
        try:
            # Check if the collection currently has no documents
            if collection.count() == 0:
                logger.info(
                    "Collection is empty. Creating and inserting sample document with embedding..."
                )
                sample_doc = create_sample_lesson_data()
                if sample_doc:
                    # Insert the document. waitForSync=True ensures the write is durable before returning.
                    meta = collection.insert(sample_doc, sync=True)
                    logger.success(
                        f"Successfully inserted sample document: _key={meta['_key']}"
                    )
                else:
                    # Log if sample creation failed (likely due to embedding)
                    logger.warning(
                        "Sample document creation failed. Skipping insertion."
                    )
            else:
                logger.info(
                    f"Collection '{collection.name}' already contains documents. Skipping sample insertion."
                )
        except (DocumentInsertError, ArangoServerError) as e:
            logger.error(f"Failed to insert sample document: {e}")
            # Consider whether to raise the exception depending on requirements
            # raise
