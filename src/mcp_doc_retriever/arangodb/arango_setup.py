# src/mcp_doc_retriever/arangodb/arango_setup.py

import os
import sys
import argparse
import json
from pathlib import Path  # Using Path for consistency
from typing import Optional, List, Dict, Any, Union  # Added Union

# Use the correct ArangoDB client library
from arango import ArangoClient
from arango.database import StandardDatabase
from arango.collection import StandardCollection
from arango.graph import Graph

# from arango.aql import AQL # Not used directly
from arango.exceptions import (
    ArangoClientError,
    ArangoServerError,
    DatabaseCreateError,
    CollectionCreateError,
    GraphCreateError,
    ViewCreateError,
    IndexCreateError,
    DocumentInsertError,
    DocumentGetError,
    DocumentDeleteError,
    AQLQueryExecuteError,
    IndexDeleteError,  # Added IndexDeleteError
)
from loguru import logger

# --- Local Imports ---
try:
    from mcp_doc_retriever.arangodb.embedding_utils import (
        get_text_for_embedding,
        get_embedding,
    )

    # Import the specific function from json_utils
    from mcp_doc_retriever.arangodb.json_utils import load_json_file
except ImportError as e:
    logger.error(
        f"Failed to import required utilities (embedding/json): {e}. Seeding/Setup might fail."
    )

    # Define dummy functions if needed for script execution without imports
    def get_text_for_embedding(doc_data: Dict[str, Any]) -> str:
        return ""

    def get_embedding(text: str, model: str = "") -> Optional[List[float]]:
        return None

    def load_json_file(file_path: str) -> Optional[Union[dict, list]]:
        return None  # Added Union type hint


# --- Configuration Loading ---
ARANGO_HOST = os.getenv("ARANGO_HOST", "http://localhost:8529")
ARANGO_USER = os.getenv("ARANGO_USER", "root")
ARANGO_PASSWORD = os.getenv("ARANGO_PASSWORD")
ARANGO_DB_NAME = os.getenv("ARANGO_DB_NAME", "doc_retriever")
COLLECTION_NAME = os.getenv("ARANGO_COLLECTION_NAME", "lessons_learned")
EDGE_COLLECTION_NAME = os.getenv("ARANGO_EDGE_COLLECTION_NAME", "relationships")
GRAPH_NAME = os.getenv("ARANGO_GRAPH_NAME", "lessons_graph")
SEARCH_VIEW_NAME = os.getenv("ARANGO_SEARCH_VIEW_NAME", "lessons_view")
VECTOR_INDEX_NAME = os.getenv("ARANGO_VECTOR_INDEX_NAME", "idx_lesson_embedding")
EMBEDDING_FIELD = os.getenv("ARANGO_EMBEDDING_FIELD", "embedding")
try:
    EMBEDDING_DIMENSION = int(os.getenv("EMBEDDING_DIMENSION", "1536"))
except ValueError:
    logger.warning("Invalid EMBEDDING_DIMENSION env var, using default 1536.")
    EMBEDDING_DIMENSION = 1536


# --- Helper Functions ---


def connect_arango() -> Optional[ArangoClient]:
    """Establishes a connection to the ArangoDB server."""
    if not ARANGO_PASSWORD:
        logger.error("ARANGO_PASSWORD environment variable not set. Cannot connect.")
        return None
    logger.info(f"Attempting to connect to ArangoDB at {ARANGO_HOST}...")
    try:
        client = ArangoClient(hosts=ARANGO_HOST)
        sys_db = client.db("_system", username=ARANGO_USER, password=ARANGO_PASSWORD)
        _ = sys_db.collections()  # Verify connection
        logger.success("Successfully connected to ArangoDB instance.")
        return client
    except (ArangoClientError, ArangoServerError) as e:
        logger.error(
            f"Failed to connect to ArangoDB at {ARANGO_HOST}. See traceback.",
            exc_info=True,
        )
        return None
    except Exception as e:
        logger.error(
            f"An unexpected error occurred during connection attempt. See traceback.",
            exc_info=True,
        )
        return None


def ensure_database(
    client: ArangoClient, db_name: str = ARANGO_DB_NAME
) -> Optional[StandardDatabase]:
    """Ensures the specified database exists."""
    try:
        sys_db = client.db("_system", username=ARANGO_USER, password=ARANGO_PASSWORD)
        if db_name not in sys_db.databases():
            logger.info(f"Database '{db_name}' not found. Creating...")
            sys_db.create_database(db_name)
            logger.success(f"Database '{db_name}' created successfully.")
        else:
            logger.debug(f"Database '{db_name}' already exists.")
        return client.db(db_name, username=ARANGO_USER, password=ARANGO_PASSWORD)
    except (DatabaseCreateError, ArangoServerError, ArangoClientError) as e:
        logger.error(
            f"Failed to ensure database '{db_name}'. See traceback.", exc_info=True
        )
        return None
    except Exception as e:
        logger.error(
            f"An unexpected error occurred ensuring database '{db_name}'. See traceback.",
            exc_info=True,
        )
        return None


# In arango_setup.py


def ensure_collection(
    db: StandardDatabase,
    collection_name: str = COLLECTION_NAME,
) -> StandardCollection:
    """
    Ensures the specified DOCUMENT collection exists in ArangoDB.
    - If it already exists and is type DOCUMENT, returns it.
    - If it does not exist (catches error_code 1203), creates it.
    - Raises on any other errors (permissions, network, bad type).
    """
    # 1) Existence & type check
    try:
        coll = db.collection(collection_name)
        props = coll.properties()  # will throw ArangoServerError(1203) if missing
        if props.get("type") != 2:
            bad = "Edge" if props.get("type") == 3 else "Unknown"
            raise TypeError(
                f"Collection '{collection_name}' exists but is {bad}, not DOCUMENT."
            )
        logger.debug(f"Collection '{collection_name}' exists and is DOCUMENT.")
        return coll

    except ArangoServerError as e:
        code = getattr(e, "error_code", None)
        # 1203 = "collection or view not found"
        if code == 1203 or "1203" in str(e):
            logger.info(f"Collection '{collection_name}' not found. Will create it.")
        else:
            logger.error(
                f"Server error checking collection '{collection_name}': {e}",
                exc_info=True,
            )
            raise

    except ArangoClientError as e:
        logger.error(
            f"Client error checking collection '{collection_name}': {e}", exc_info=True
        )
        raise

    # 2) Creation
    try:
        logger.info(f"Creating collection '{collection_name}' as DOCUMENT type...")
        coll = db.create_collection(collection_name, edge=False)
        logger.success(f"Collection '{collection_name}' created successfully.")
        return coll

    except (CollectionCreateError, ArangoServerError) as e:
        logger.error(
            f"Failed to create collection '{collection_name}': {e}", exc_info=True
        )
        raise

def ensure_edge_collection(
    db: StandardDatabase, edge_collection_name: str = EDGE_COLLECTION_NAME
) -> Optional[StandardCollection]:
    """Ensures the specified edge collection exists."""
    try:
        existing_collections = db.collections()
        collection_info = next(
            (c for c in existing_collections if c["name"] == edge_collection_name), None
        )
        if collection_info is None:
            logger.info(
                f"Edge collection '{edge_collection_name}' not found. Creating..."
            )
            edge_collection = db.create_collection(
                edge_collection_name, edge=True
            )  # edge=True is key
            logger.success(f"Edge collection '{edge_collection_name}' created.")
            return edge_collection
        else:
            # Corrected check: Compare type string
            collection_type = collection_info.get("type")
            logger.debug(
                f"Checking existing collection '{edge_collection_name}'. Reported type: '{collection_type}'"
            )
            if collection_type != "edge":  # Check the string type
                logger.error(
                    "Existing collection '{}' is not an edge collection (type={}). Please check configuration.",
                    edge_collection_name,
                    collection_type,
                )
                return None
            logger.debug(
                f"Edge collection '{edge_collection_name}' confirmed as type 'edge'."
            )
            return db.collection(edge_collection_name)
    except (CollectionCreateError, ArangoServerError) as e:
        logger.error(
            f"Failed to ensure edge collection '{edge_collection_name}'. See traceback.",
            exc_info=True,
        )
        return None
    except Exception as e:
        logger.error(
            f"An unexpected error occurred ensuring edge collection '{edge_collection_name}'. See traceback.",
            exc_info=True,
        )
        return None


def ensure_graph(
    db: StandardDatabase,
    graph_name: str = GRAPH_NAME,
    edge_collection_name: str = EDGE_COLLECTION_NAME,
    vertex_collection_name: str = COLLECTION_NAME,
) -> Optional[Graph]:
    """Ensures the graph defining relationships exists."""
    try:
        existing_graphs = db.graphs()
        graph_info = next((g for g in existing_graphs if g["name"] == graph_name), None)
        if graph_info is None:
            logger.info(f"Graph '{graph_name}' not found. Creating...")
            edge_definition = {
                "edge_collection": edge_collection_name,
                "from_vertex_collections": [vertex_collection_name],
                "to_vertex_collections": [vertex_collection_name],
            }
            graph = db.create_graph(graph_name, edge_definitions=[edge_definition])
            logger.success(f"Graph '{graph_name}' created.")
            return graph
        else:
            logger.debug(f"Graph '{graph_name}' already exists.")
            return db.graph(graph_name)
    except (GraphCreateError, ArangoServerError) as e:
        logger.error(
            f"Failed to ensure graph '{graph_name}'. See traceback.", exc_info=True
        )
        return None
    except Exception as e:
        logger.error(
            f"An unexpected error occurred ensuring graph '{graph_name}'. See traceback.",
            exc_info=True,
        )
        return None


def ensure_search_view(
    db: StandardDatabase,
    view_name: str = SEARCH_VIEW_NAME,
    collection_name: str = COLLECTION_NAME,
) -> bool:
    """Ensures an ArangoSearch View exists for keyword searching (BM25)."""
    view_properties = {
        "links": {
            collection_name: {
                "fields": {
                    "problem": {"analyzers": ["text_en"]},
                    "solution": {"analyzers": ["text_en"]},
                    "context": {"analyzers": ["text_en"]},
                    "tags": {"analyzers": ["identity"]},
                    # Add other fields like 'lesson', 'role' if needed for keyword search
                    "lesson": {"analyzers": ["text_en"]},
                    "role": {"analyzers": ["identity"]},  # identity for exact match
                },
                "includeAllFields": False,
                "storeValues": "id",
                "trackListPositions": False,
                "analyzers": ["identity", "text_en"],  # List analyzers used in the link
            }
        },
        # Add other view properties if needed (defaults from previous example)
        "consolidationIntervalMsec": 1000,
        "commitIntervalMsec": 1000,
        "cleanupIntervalStep": 2,
        # ... other potential view settings ...
    }
    try:
        existing_views = db.views()
        view_info = next((v for v in existing_views if v["name"] == view_name), None)
        if view_info is None:
            logger.info(f"ArangoSearch View '{view_name}' not found. Creating...")
            db.create_view(
                view_name, view_type="arangosearch", properties=view_properties
            )
            logger.success(f"ArangoSearch View '{view_name}' created.")
            return True
        else:
            logger.debug(
                f"ArangoSearch View '{view_name}' already exists. Ensuring properties are up-to-date..."
            )
            try:
                # Corrected: Use db method to replace properties
                db.replace_view(view_name, view_properties)
                logger.info(
                    f"ArangoSearch View '{view_name}' properties updated/verified."
                )
                return True
            except (ArangoServerError, ArangoClientError) as update_err:
                logger.error(
                    f"Failed to update properties for ArangoSearch View '{view_name}'. See traceback.",
                    exc_info=True,
                )
                return False  # Indicate failure on update error
    except (ViewCreateError, ArangoServerError) as e:
        logger.error(
            f"Failed to ensure ArangoSearch View '{view_name}'. See traceback.",
            exc_info=True,
        )
        return False
    except Exception as e:
        logger.error(
            f"An unexpected error occurred ensuring ArangoSearch View '{view_name}'. See traceback.",
            exc_info=True,
        )
        return False


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

        # Set higher when you get more records
        # nList_count = max(len(collection.count()), 2)

        index_definition = {
            "type": "vector",  # <-- CORRECT TYPE based on troubleshooting for ERR 9
            "name": index_name,
            "fields": [embedding_field],  # Field containing the vector array
            "params": {  # Parameters specific to the vector index
                "dimension": dimensions,
                "metric": "cosine",  # Or "euclidean" / "l2"
                "nLists": 2,  # Optional: Add if using IVF-type backend (less common now, HNSW often default)
            },
            # DO NOT add analyzers or analyzerDefinitions for this type
            # "inBackground": True,      # Optional: Can still use background creation
        }

        logger.debug(
            "Attempting to add 'vector' index with definition: {}", index_definition
        )
        collection.add_index(index_definition)  # <--- Attempt creation

        logger.success(
            "Dedicated 'vector' index '{}' on field '{}' created.",
            index_name,
            embedding_field,
        )
        return True

    # Keep the detailed error logging
    except (IndexCreateError, ArangoServerError, KeyError) as e:
        logger.error(
            "Failed to create vector index '{}' on collection '{}'. See traceback for details.",
            index_name,
            collection_name,
            exc_info=True,
        )
        # import traceback # Uncomment for explicit print if needed
        # traceback.print_exc()
        return False
    except Exception as e:
        logger.error(
            "An unexpected error occurred ensuring vector index '{}'. See traceback for details.",
            index_name,
            exc_info=True,
        )
        # import traceback # Uncomment for explicit print if needed
        # traceback.print_exc()
        return False


def truncate_collections(
    db: StandardDatabase, collections_to_truncate: List[str], force: bool = False
) -> bool:
    """Truncates (empties) the specified collections."""
    if not force:
        confirm = input(
            f"WARNING: This will permanently delete all data from collections: "
            f"{', '.join(collections_to_truncate)} in database '{db.name}'.\n"
            f"Are you sure? (yes/no): "
        )
        if confirm.lower() != "yes":
            logger.warning("Truncation cancelled by user.")
            return False

    logger.warning(f"Attempting to truncate collections: {collections_to_truncate}")
    all_successful = True
    existing_collections = [c["name"] for c in db.collections()]
    for collection_name in collections_to_truncate:
        if collection_name in existing_collections:
            try:
                logger.info(f"Truncating collection '{collection_name}'...")
                db.collection(collection_name).truncate()
                logger.success(
                    f"Successfully truncated collection '{collection_name}'."
                )
            except (ArangoServerError, ArangoClientError) as e:
                logger.error(
                    f"Failed to truncate collection '{collection_name}'. See traceback.",
                    exc_info=True,
                )
                all_successful = False
            except Exception as e:
                logger.error(
                    f"Unexpected error truncating collection '{collection_name}'. See traceback.",
                    exc_info=True,
                )
                all_successful = False
        else:
            logger.info(
                f"Collection '{collection_name}' not found, skipping truncation."
            )
    return all_successful


# --- Modified Seeding Function (accepts list) ---
def seed_initial_data(
    db: StandardDatabase, lessons_to_seed: List[Dict[str, Any]]
) -> bool:
    """Generates embeddings and inserts lesson documents (from a provided list) into the collection."""
    logger.info(
        f"Starting data seeding for collection '{COLLECTION_NAME}' with {len(lessons_to_seed)} lessons..."
    )
    try:
        if COLLECTION_NAME not in [c["name"] for c in db.collections()]:
            logger.error(
                f"Cannot seed data: Collection '{COLLECTION_NAME}' does not exist."
            )
            return False
        collection = db.collection(COLLECTION_NAME)
    except Exception as e:
        logger.error(
            f"Failed to get collection '{COLLECTION_NAME}' for seeding. Error: {e}",
            exc_info=True,
        )
        return False

    success_count, fail_count = 0, 0
    for i, lesson_doc in enumerate(lessons_to_seed):
        logger.debug(f"Processing lesson {i + 1}/{len(lessons_to_seed)}...")
        doc_to_insert = lesson_doc.copy()
        text_to_embed = get_text_for_embedding(doc_to_insert)
        if not text_to_embed:
            logger.warning(
                f"Skipping lesson {i + 1} due to empty text for embedding. Data: {lesson_doc.get('_key', lesson_doc.get('problem', 'N/A'))[:50]}..."
            )
            fail_count += 1
            continue
        embedding_vector = get_embedding(text_to_embed)
        if embedding_vector is None:
            logger.error(
                f"Failed to generate embedding for lesson {i + 1}. Skipping insertion. Data: {lesson_doc.get('_key', lesson_doc.get('problem', 'N/A'))[:50]}..."
            )
            fail_count += 1
            continue
        doc_to_insert[EMBEDDING_FIELD] = embedding_vector
        try:
            doc_key = doc_to_insert.pop("_key", None)
            if doc_key:
                doc_to_insert["_key"] = doc_key
            meta = collection.insert(doc_to_insert, overwrite=True)
            logger.info(
                f"Successfully inserted/updated lesson {i + 1} with key '{meta['_key']}'."
            )
            success_count += 1
        except (DocumentInsertError, ArangoServerError) as e:
            logger.error(
                f"Failed to insert lesson {i + 1} (Key: {doc_key}). See traceback.",
                exc_info=True,
            )
            fail_count += 1
        except Exception as e:
            logger.error(
                f"Unexpected error inserting lesson {i + 1} (Key: {doc_key}). See traceback.",
                exc_info=True,
            )
            fail_count += 1
    logger.info(
        f"Seeding finished. Success/Updated: {success_count}, Failed/Skipped: {fail_count}"
    )
    return success_count > 0

def seed_test_relationship(db: StandardDatabase) -> bool:
    """Creates a single test edge relationship between known seed documents."""
    logger.info("Attempting to seed a test relationship...")
    try:
        edge_collection = db.collection(EDGE_COLLECTION_NAME)
    except Exception as e:
        logger.error(
            f"Failed to get edge collection '{EDGE_COLLECTION_NAME}' for seeding relationship. Traceback:",
            exc_info=True,
        )
        return False

    # Define the keys and relationship details
    from_key = "planner_jq_tags_error_20250412195032"
    to_key = "planner_human_verification_context_202504141035"
    from_id = f"{COLLECTION_NAME}/{from_key}"
    to_id = f"{COLLECTION_NAME}/{to_key}"

    edge_doc = {
        "_from": from_id,
        "_to": to_id,
        "type": "RELATED",  # Example relationship type
        "rationale": "Example relationship seeded by setup script for testing.",
        "source": "arango_setup_seed",
    }

    try:
        # Check if edge already exists (simple check based on from/to/type)
        # A more robust check might involve a unique hash or specific key if needed
        cursor = db.aql.execute(
            f"FOR e IN {EDGE_COLLECTION_NAME} FILTER e._from == @from AND e._to == @to AND e.type == @type RETURN e._key LIMIT 1",
            bind_vars={"from": from_id, "to": to_id, "type": edge_doc["type"]},
        )
        if cursor.count() > 0:
            logger.info(
                f"Test relationship from '{from_key}' to '{to_key}' already exists."
            )
            return True

        # Insert the new edge
        meta = edge_collection.insert(edge_doc)
        logger.success(
            f"Successfully seeded test relationship with key '{meta['_key']}' from '{from_key}' to '{to_key}'."
        )
        return True
    except (DocumentInsertError, ArangoServerError) as e:
        logger.error(
            f"Failed to insert test relationship from '{from_key}' to '{to_key}'. Does source/target exist? Traceback:",
            exc_info=True,
        )
        return False
    except Exception as e:
        logger.error(
            f"Unexpected error inserting test relationship. Traceback:", exc_info=True
        )
        return False
    
# --- Modified initialize_database Function ---
def initialize_database(
    run_setup: bool = True,
    truncate: bool = False,
    force_truncate: bool = False,
    seed_file_path: Optional[str] = None,
) -> Optional[StandardDatabase]:
    """Main function to connect, optionally truncate, optionally seed, and ensure ArangoDB components."""
    client = connect_arango()
    if not client:
        return None
    db = ensure_database(client)
    if not db:
        return None

    if truncate:  # Truncation logic
        logger.warning("--- TRUNCATE REQUESTED ---")
        collections_to_clear = [COLLECTION_NAME, EDGE_COLLECTION_NAME]
        if not truncate_collections(db, collections_to_clear, force=force_truncate):
            logger.error("Truncation failed/cancelled. Aborting.")
            return None
        logger.info("--- Truncation complete ---")

    logger.info("Ensuring base collections exist...")
    collection_obj = ensure_collection(db, COLLECTION_NAME)
    edge_collection_obj = ensure_edge_collection(db, EDGE_COLLECTION_NAME)
    # Corrected check based on previous debug
    if collection_obj is None or edge_collection_obj is None:
        logger.error("Failed to ensure base collections exist. Aborting.")
        return None

    # Seeding Logic
    seeding_occurred = False  # Flag to track if seeding happened
    if seed_file_path:
        logger.info(f"--- SEED DATA REQUESTED from file: {seed_file_path} ---")
        try:
            seed_data = load_json_file(seed_file_path)
            if seed_data is None:
                logger.error(f"Seeding aborted: file empty/not found: {seed_file_path}")
                return None
            lessons_list = seed_data.get("lessons")
            if not isinstance(lessons_list, list):
                logger.error(
                    f"Seed file invalid: needs {{'lessons': [...]}}. Found: {type(lessons_list)}"
                )
                return None

            if seed_initial_data(db, lessons_list):
                logger.info("--- Seeding documents complete ---")
                seeding_occurred = True  # Mark that seeding happened
                # --- Seed test relationship ONLY if document seeding occurred ---
                if not seed_test_relationship(db):
                    logger.warning("Failed to seed test relationship.")
                # --- End seed test relationship ---
            else:
                logger.warning(
                    "Data seeding process inserted no data or encountered errors."
                )
        except Exception as e:
            logger.error(
                f"Error during seeding from file '{seed_file_path}'. Traceback:",
                exc_info=True,
            )
            return None

    # Run structure setup (Graph, View, Index)
    if run_setup:
        logger.info(
            "Starting ArangoDB structure setup/verification (Graph, View, Index)..."
        )
        graph_obj = ensure_graph(db)
        view_ok = ensure_search_view(db)
        index_ok = ensure_vector_index(db)
        if not all([graph_obj, view_ok, index_ok]):
            logger.error("Setup steps failed (Graph/View/Index). Check logs.")
            return None
        else:
            logger.success(
                "ArangoDB structure setup/verification complete (Graph, View, Index)."
            )

    return db


# --- Main Execution Block ---
if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Initialize or setup the ArangoDB database for MCP Doc Retriever."
    )
    parser.add_argument(
        "--truncate",
        action="store_true",
        help=f"WARNING: Delete data from '{COLLECTION_NAME}', '{EDGE_COLLECTION_NAME}' before setup.",
    )
    parser.add_argument(
        "--seed-file",
        type=str,
        default=None,
        help=f"Path to a JSON file for seeding '{COLLECTION_NAME}'. Format: {{'lessons': [...]}}.",
    )
    parser.add_argument(
        "--yes",
        "-y",
        action="store_true",
        help="Bypass confirmation prompt if --truncate is used.",
    )
    parser.add_argument(
        "--log-level",
        default=os.environ.get("LOG_LEVEL", "INFO").upper(),
        help="Set logging level.",
    )
    args = parser.parse_args()

    # Configure logging
    log_level = args.log_level.upper()
    valid_levels = ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
    if log_level not in valid_levels:
        print(
            f"Warning: Invalid log level '{log_level}'. Defaulting to INFO.",
            file=sys.stderr,
        )
        log_level = "INFO"
    logger.remove()
    logger.add(
        sys.stderr, level=log_level, format="{time:HH:mm:ss} | {level: <7} | {message}"
    )

    logger.info("Running ArangoDB setup script...")
    if args.truncate:
        logger.warning("Truncate flag is set. Data will be deleted.")
    if args.seed_file:
        logger.info(
            f"Seed file provided: {args.seed_file}. Data will be inserted if file is valid."
        )

    final_db = initialize_database(
        run_setup=True,
        truncate=args.truncate,
        force_truncate=args.yes,
        seed_file_path=args.seed_file,
    )

    if final_db:
        logger.info(
            f"Successfully connected to database '{final_db.name}'. Setup process completed."
        )
        # Optional: Add post-setup checks again if desired
        try:
            coll = final_db.collection(COLLECTION_NAME)
            edge_coll = final_db.collection(EDGE_COLLECTION_NAME)
            logger.info(f"Collection '{COLLECTION_NAME}' count: {coll.count()}")
            logger.info(
                f"Edge Collection '{EDGE_COLLECTION_NAME}' count: {edge_coll.count()}"
            )
            views = final_db.views()
            if any(v["name"] == SEARCH_VIEW_NAME for v in views):
                logger.info(f"Search View '{SEARCH_VIEW_NAME}' confirmed.")
            else:
                logger.warning(
                    f"Search View '{SEARCH_VIEW_NAME}' check failed post-setup."
                )
            indexes = coll.indexes()
            if any(i.get("name") == VECTOR_INDEX_NAME for i in indexes):
                logger.info(f"Vector Index '{VECTOR_INDEX_NAME}' confirmed.")
            else:
                logger.warning(
                    f"Vector Index '{VECTOR_INDEX_NAME}' check failed post-setup."
                )
        except Exception as check_err:
            logger.warning(f"Could not perform post-setup checks: {check_err}")
        sys.exit(0)
    else:
        logger.error("ArangoDB connection or setup failed.")
        sys.exit(1)
