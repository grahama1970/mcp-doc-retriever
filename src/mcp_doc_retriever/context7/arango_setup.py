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

# --- Initialize LiteLLM Cache Import ---
# Note: Moved initialization logic out of setup_arango_collection
# It should be called once, e.g., when the application/script starts.
from mcp_doc_retriever.arangodb.initialize_litellm_cache import initialize_litellm_cache

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
        logger.warning("Using dummy get_text_for_embedding")
        return ""

    def get_embedding(text: str, model: str = "") -> Optional[List[float]]:
        logger.warning("Using dummy get_embedding")
        return None

    def load_json_file(file_path: str) -> Optional[Union[dict, list]]:
        logger.warning("Using dummy load_json_file")
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
ARANGO_VECTOR_NLISTS = int(os.getenv("ARANGO_VECTOR_NLISTS", 2))
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
        # Verify connection by trying to access _system db
        sys_db = client.db("_system", username=ARANGO_USER, password=ARANGO_PASSWORD)
        _ = sys_db.collections()  # Simple operation to check connectivity
        logger.success("Successfully connected to ArangoDB instance.")
        return client
    except (ArangoClientError, ArangoServerError) as e:
        logger.error(
            f"Failed to connect to ArangoDB at {ARANGO_HOST}. Error: {e}",
            exc_info=True, # Include traceback for connection errors
        )
        return None
    except Exception as e:
        logger.error(
            f"An unexpected error occurred during ArangoDB connection attempt: {e}",
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
        # Return the handle to the specific database
        return client.db(db_name, username=ARANGO_USER, password=ARANGO_PASSWORD)
    except (DatabaseCreateError, ArangoServerError, ArangoClientError) as e:
        logger.error(
            f"Failed to ensure database '{db_name}'. Error: {e}", exc_info=True
        )
        return None
    except Exception as e:
        logger.error(
            f"An unexpected error occurred ensuring database '{db_name}'. Error: {e}",
            exc_info=True,
        )
        return None


def ensure_collection(
    db: StandardDatabase,
    collection_name: str = COLLECTION_NAME,
) -> Optional[StandardCollection]:
    """
    Ensures the specified DOCUMENT collection exists in ArangoDB.
    Returns the collection object or None on failure.
    """
    try:
        if collection_name not in [c["name"] for c in db.collections()]:
             logger.info(f"Collection '{collection_name}' not found. Creating as DOCUMENT type...")
             collection = db.create_collection(collection_name, edge=False)
             logger.success(f"Collection '{collection_name}' created successfully.")
             return collection
        else:
             collection = db.collection(collection_name)
             props = collection.properties()
             if props.get("type") == 2: # 2 for document, 3 for edge
                  logger.debug(f"Collection '{collection_name}' exists and is DOCUMENT type.")
                  return collection
             else:
                  coll_type = "Edge" if props.get("type") == 3 else "Unknown"
                  logger.error(
                      f"Collection '{collection_name}' exists but is type '{coll_type}', not DOCUMENT."
                  )
                  return None # Return None for incorrect type
    except (CollectionCreateError, ArangoServerError, ArangoClientError) as e:
        logger.error(
            f"Failed to ensure collection '{collection_name}'. Error: {e}", exc_info=True
        )
        return None # Return None on error
    except Exception as e:
         logger.error(
             f"An unexpected error occurred ensuring collection '{collection_name}'. Error: {e}",
             exc_info=True
         )
         return None


def ensure_edge_collection(
    db: StandardDatabase, edge_collection_name: str = EDGE_COLLECTION_NAME
) -> Optional[StandardCollection]:
    """Ensures the specified EDGE collection exists."""
    try:
        if edge_collection_name not in [c["name"] for c in db.collections()]:
            logger.info(
                f"Edge collection '{edge_collection_name}' not found. Creating as EDGE type..."
            )
            edge_collection = db.create_collection(edge_collection_name, edge=True) # edge=True is key
            logger.success(f"Edge collection '{edge_collection_name}' created.")
            return edge_collection
        else:
            collection = db.collection(edge_collection_name)
            props = collection.properties()
            # Check 'type' property (3 for edge)
            if props.get("type") == 3:
                  logger.debug(
                      f"Edge collection '{edge_collection_name}' exists and is EDGE type."
                  )
                  return collection
            else:
                  coll_type = "Document" if props.get("type") == 2 else "Unknown"
                  logger.error(
                      f"Collection '{edge_collection_name}' exists but is type '{coll_type}', not EDGE."
                  )
                  return None
    except (CollectionCreateError, ArangoServerError, ArangoClientError) as e:
        logger.error(
            f"Failed to ensure edge collection '{edge_collection_name}'. Error: {e}",
            exc_info=True,
        )
        return None
    except Exception as e:
        logger.error(
            f"An unexpected error occurred ensuring edge collection '{edge_collection_name}': {e}",
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
        # Check if vertex and edge collections exist first (optional but good practice)
        if vertex_collection_name not in [c["name"] for c in db.collections()]:
            logger.error(f"Cannot ensure graph '{graph_name}': Vertex collection '{vertex_collection_name}' not found.")
            return None
        if edge_collection_name not in [c["name"] for c in db.collections()]:
             logger.error(f"Cannot ensure graph '{graph_name}': Edge collection '{edge_collection_name}' not found.")
             return None

        if not db.has_graph(graph_name):
            logger.info(f"Graph '{graph_name}' not found. Creating...")
            # Define the edge relationship within the graph
            edge_definition = {
                "edge_collection": edge_collection_name,
                "from_vertex_collections": [vertex_collection_name],
                "to_vertex_collections": [vertex_collection_name], # Assuming self-relationships are possible
            }
            graph = db.create_graph(graph_name, edge_definitions=[edge_definition])
            logger.success(f"Graph '{graph_name}' created.")
            return graph
        else:
            logger.debug(f"Graph '{graph_name}' already exists.")
            return db.graph(graph_name)
    except (GraphCreateError, ArangoServerError, ArangoClientError) as e:
        logger.error(
            f"Failed to ensure graph '{graph_name}'. Error: {e}", exc_info=True
        )
        return None
    except Exception as e:
        logger.error(
            f"An unexpected error occurred ensuring graph '{graph_name}': {e}",
            exc_info=True,
        )
        return None


def ensure_search_view(
    db: StandardDatabase,
    view_name: str = SEARCH_VIEW_NAME,
    collection_name: str = COLLECTION_NAME,
) -> bool:
    """Ensures an ArangoSearch View exists for keyword searching (BM25). Links specified collection."""
    # Define view properties including necessary fields and analyzers
    # Adjust fields based on what needs to be text-searchable
    view_properties = {
        "type": "arangosearch", # Specify type explicitly
        "links": {
            collection_name: {
                "fields": {
                    # Use 'text_en' analyzer for general English text fields
                    "problem": {"analyzers": ["text_en"]},
                    "solution": {"analyzers": ["text_en"]},
                    "context": {"analyzers": ["text_en"]},
                    "lesson": {"analyzers": ["text_en"]},
                    # Use 'identity' for exact matching (like tags, IDs, roles)
                    "tags": {"analyzers": ["identity"]},
                    "role": {"analyzers": ["identity"]},
                    # Include 'embedding' field if needed for filtering/access within the view context,
                    # but primary vector search uses the dedicated vector index.
                    # If included here, 'identity' might be suitable if not analyzing the vector itself.
                    # EMBEDDING_FIELD: {"analyzers": ["identity"]} # Optional: include embedding field
                },
                "includeAllFields": False, # Only include specified fields
                "storeValues": "id", # Store only document IDs to save space
                "trackListPositions": False, # Not usually needed for basic search
                "analyzers": ["identity", "text_en"], # List all analyzers used in this link
            }
        },
        # Consolidation policy - adjust based on update frequency and query needs
        "consolidationIntervalMsec": 1000, # How often segments are merged (higher = less merge overhead, slower visibility)
        "commitIntervalMsec": 1000, # How often changes are committed (higher = delay in visibility)
        "cleanupIntervalStep": 2, # How often cleanup runs relative to commits/consolidations
        # primarySort, storingValues, etc. can be added if needed
    }
    try:
        if not db.has_view(view_name):
            logger.info(f"ArangoSearch View '{view_name}' not found. Creating...")
            db.create_view(view_name, properties=view_properties)
            logger.success(f"ArangoSearch View '{view_name}' created successfully.")
        else:
            logger.debug(
                f"ArangoSearch View '{view_name}' already exists. Ensuring properties are updated..."
            )
            # Update properties to match the desired state
            db.replace_view_properties(view_name, view_properties)
            logger.info(
                f"ArangoSearch View '{view_name}' properties updated/verified."
            )
        return True
    except (ViewCreateError, ArangoServerError, ArangoClientError) as e:
        logger.error(
            f"Failed to ensure ArangoSearch View '{view_name}' for collection '{collection_name}'. Error: {e}",
            exc_info=True,
        )
        return False
    except Exception as e:
        logger.error(
            f"An unexpected error occurred ensuring ArangoSearch View '{view_name}'. Error: {e}",
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
    Ensures a dedicated 'vector' index exists on the specified collection field.
    Attempts to drop existing index by name first for idempotency.

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
         # Check if collection exists
        if collection_name not in [c["name"] for c in db.collections()]:
             logger.error(
                 f"Cannot create vector index '{index_name}': Collection '{collection_name}' does not exist."
             )
             return False
        collection = db.collection(collection_name)

        # --- Drop existing index by name first for idempotency ---
        existing_index = None
        try:
            indexes = collection.indexes()
            existing_index = next((idx for idx in indexes if idx.get("name") == index_name), None)
            if existing_index:
                logger.warning(
                    f"Found existing index named '{index_name}'. Attempting to drop it before creation..."
                )
                # Use ID if available, otherwise name (ID is more reliable)
                index_id_or_name = existing_index.get("id", index_name)
                if collection.delete_index(index_id_or_name, ignore_missing=True):
                     logger.info(f"Successfully dropped existing index '{index_name}' (ID: {index_id_or_name}).")
                     existing_index = None # Mark as dropped
                else:
                    # This case might happen if the index exists but couldn't be dropped (permissions?)
                     logger.warning(
                         f"Attempted to drop index '{index_name}' (ID: {index_id_or_name}), but delete_index returned False or it was already gone."
                     )
        except (IndexDeleteError, ArangoServerError, ArangoClientError) as drop_err:
             # Log error but proceed with creation attempt
             logger.error(
                 f"Error encountered while trying to drop existing index '{index_name}'. Proceeding with creation attempt. Error: {drop_err}.",
                 exc_info=True,
             )
        # --- END DROP LOGIC ---

        # If index still seems to exist after drop attempt (or drop failed), log and return False?
        # Or assume creation will fail informatively? Let's try creation.
        if existing_index:
             logger.error(f"Failed to reliably drop existing index '{index_name}'. Aborting creation to avoid conflicts.")
             return False


        # --- Attempt creation using "type": "vector" ---
        logger.info(
            f"Creating 'vector' index '{index_name}' on collection '{collection_name}', field '{embedding_field}' (dim={dimensions})..."
        )

        # Define the index using the correct 'vector' type syntax for recent ArangoDB versions
        index_definition = {
            "type": "vector",
            "name": index_name,
            "fields": [embedding_field], # Field containing the vector array
            "storedValues": [], # Optional: Fields to store directly in the index for faster retrieval (e.g., ['_key', 'tags'])
            "cacheEnabled": False, # Optional: Enable index caching (check performance impact)
            "estimate": False, # Optional: Use estimates for faster counts (can be less accurate)
            # Specific parameters depend on the chosen vector index backend (e.g., HNSW is common)
             # Example assumes default or HNSW-like parameters if applicable:
            "params": {
                "dimension": dimensions,
                "metric": "cosine", # Common choice for semantic similarity ('euclidean', 'dotproduct' also possible)
                "nLists": ARANGO_VECTOR_NLISTS,
                # Possible HNSW parameters (consult ArangoDB docs for current options):
                # "m": 16, # Max connections per node per layer
                # "efConstruction": 100, # Size of dynamic list for neighbor selection during build
                # "efSearch": 100 # Size of dynamic list during search
            }
             # "inBackground": True, # Create index in background (useful for large collections)
        }

        logger.debug(
            f"Attempting to add 'vector' index with definition: {json.dumps(index_definition, indent=2)}"
        )
        result = collection.add_index(index_definition) # Attempt creation

        if isinstance(result, dict) and result.get('id'):
            logger.success(
                f"Successfully created 'vector' index '{index_name}' on field '{embedding_field}' (ID: {result['id']})."
            )
            return True
        else:
            # Should ideally not happen if no exception occurred, but check just in case
            logger.error(f"Index creation for '{index_name}' seemed successful but did not return expected ID. Result: {result}")
            return False

    # --- Error Handling ---
    except (IndexCreateError, ArangoServerError, ArangoClientError, KeyError) as e:
        # Specific error for index creation issues
        err_code = getattr(e, 'error_code', 'N/A')
        err_msg = getattr(e, 'error_message', str(e))
        logger.error(
            f"Failed to create vector index '{index_name}' on collection '{collection_name}'. Error Code: {err_code}, Message: {err_msg}",
            exc_info=True, # Include traceback
        )
        return False
    except Exception as e:
        # Catch any other unexpected errors
        logger.error(
            f"An unexpected error occurred ensuring vector index '{index_name}'. Error: {e}",
            exc_info=True,
        )
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
    existing_collections = [c["name"] for c in db.collections()] # Get list once

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
                    f"Failed to truncate collection '{collection_name}'. Error: {e}",
                    exc_info=True,
                )
                all_successful = False
            except Exception as e:
                logger.error(
                    f"Unexpected error truncating collection '{collection_name}': {e}",
                    exc_info=True,
                )
                all_successful = False
        else:
            logger.info(
                f"Collection '{collection_name}' not found, skipping truncation."
            )
    return all_successful


# --- Modified Seeding Function (accepts collection_name, embedding_field, list) ---
def seed_initial_data(
    db: StandardDatabase,
    collection_name: str,  # Added parameter
    embedding_field: str,  # Added parameter
    lessons_to_seed: List[Dict[str, Any]],
) -> bool:
    """Generates embeddings and inserts lesson documents (from a provided list) into the specified collection."""
    logger.info(
        f"Starting data seeding for collection '{collection_name}' with {len(lessons_to_seed)} lessons..."
    )
    try:
        # Use the passed collection_name
        if collection_name not in [c["name"] for c in db.collections()]:
            logger.error(
                f"Cannot seed data: Collection '{collection_name}' does not exist."
            )
            return False
        collection = db.collection(collection_name)  # Use passed name
    except Exception as e:
        logger.error(
            f"Failed to get collection '{collection_name}' for seeding. Error: {e}",
            exc_info=True,
        )
        return False

    success_count, fail_count = 0, 0
    # Ensure LiteLLM cache is initialized *before* this loop if embeddings are generated here
    # Consider calling initialize_litellm_cache() once before calling seed_initial_data

    for i, lesson_doc in enumerate(lessons_to_seed):
        doc_key = lesson_doc.get("_key", f"lesson_{i+1}_{os.urandom(4).hex()}") # Generate key if missing
        logger.debug(f"Processing lesson {i + 1}/{len(lessons_to_seed)} (Key: {doc_key})...")
        doc_to_insert = lesson_doc.copy()

        # Ensure _key is handled correctly
        if "_key" in doc_to_insert:
            doc_key = doc_to_insert.pop("_key") # Use provided key
        doc_to_insert['_key'] = doc_key # Ensure _key is in the doc for insertion


        # Check if embedding already exists (e.g., if re-seeding)
        if embedding_field in doc_to_insert and doc_to_insert[embedding_field]:
             logger.debug(f"Skipping embedding generation for {doc_key}, field '{embedding_field}' already exists.")
        else:
            text_to_embed = get_text_for_embedding(doc_to_insert)
            if not text_to_embed:
                logger.warning(
                    f"Skipping embedding generation for {doc_key} due to empty text. Data: {str(lesson_doc)[:100]}..."
                )
                # Decide whether to insert doc without embedding or skip entirely
                # Skipping entirely for now, as vector search relies on it.
                fail_count += 1
                continue

            # Generate embedding
            try:
                 embedding_vector = get_embedding(text_to_embed) # Assumes get_embedding handles model selection/API keys
                 if embedding_vector and isinstance(embedding_vector, list):
                     # Use the passed embedding_field name
                     doc_to_insert[embedding_field] = embedding_vector
                     logger.debug(f"Generated embedding for {doc_key} (dim={len(embedding_vector)})")
                 else:
                     logger.error(
                         f"Failed to generate valid embedding for {doc_key}. Skipping insertion."
                     )
                     fail_count += 1
                     continue # Don't insert if embedding failed
            except Exception as embed_err:
                 logger.error(f"Error generating embedding for {doc_key}: {embed_err}", exc_info=True)
                 fail_count += 1
                 continue # Don't insert if embedding failed

        # Insert or update the document
        try:
            meta = collection.insert(doc_to_insert, overwrite=True) # Insert into correct collection, overwrite allows re-seeding
            logger.info(
                f"Successfully inserted/updated lesson {i + 1} with key '{meta['_key']}' into '{collection_name}'."
            )
            success_count += 1
        except (DocumentInsertError, ArangoServerError, ArangoClientError) as e:
            logger.error(
                f"Failed to insert/update lesson {i + 1} (Key: {doc_key}) into '{collection_name}'. Error: {e}",
                exc_info=True,
            )
            fail_count += 1
        except Exception as e: # Catch any other unexpected errors during insertion
             logger.error(
                 f"Unexpected error inserting lesson {i + 1} (Key: {doc_key}) into '{collection_name}'. Error: {e}",
                 exc_info=True,
             )
             fail_count += 1

    logger.info(
        f"Seeding for '{collection_name}' finished. Success/Updated: {success_count}, Failed/Skipped: {fail_count}"
    )
    # Return True only if all documents were seeded successfully? Or if at least one was?
    # Let's return True if there were no failures.
    return fail_count == 0 and success_count > 0


def seed_test_relationship(db: StandardDatabase) -> bool:
    """Creates a single test edge relationship between known seed documents."""
    # Check if edge collection exists
    if EDGE_COLLECTION_NAME not in [c["name"] for c in db.collections()]:
        logger.warning(f"Cannot seed test relationship: Edge collection '{EDGE_COLLECTION_NAME}' not found.")
        return False

    logger.info(f"Attempting to seed a test relationship in '{EDGE_COLLECTION_NAME}'...")
    try:
        edge_collection = db.collection(EDGE_COLLECTION_NAME)
    except Exception as e:
        logger.error(
            f"Failed to get edge collection '{EDGE_COLLECTION_NAME}' for seeding relationship. Error: {e}",
            exc_info=True,
        )
        return False

    # --- Define the keys and relationship details ---
    # Hardcoding keys is brittle; consider making these configurable or based on actual seeded data.
    # These keys MUST exist in the COLLECTION_NAME for the edge to be valid.
    from_key = "planner_jq_tags_error_20250412195032" # Example Key 1
    to_key = "planner_human_verification_context_202504141035" # Example Key 2
    # ------------------------------------------------

    # Construct full document IDs
    from_id = f"{COLLECTION_NAME}/{from_key}"
    to_id = f"{COLLECTION_NAME}/{to_key}"

    # Define the edge document
    edge_doc = {
        "_from": from_id,
        "_to": to_id,
        "type": "RELATED_SETUP_TEST", # Example relationship type
        "rationale": "Example relationship seeded by setup script for graph testing.",
        "source": "arango_setup_seed_relationship",
        "_key": f"rel_{from_key}_{to_key}" # Define a predictable key if needed
    }

    try:
        # Check if the specific edge key already exists
        if edge_collection.has(edge_doc["_key"]):
             logger.info(
                 f"Test relationship with key '{edge_doc['_key']}' already exists in '{EDGE_COLLECTION_NAME}'."
             )
             return True

        # Verify source and target documents exist before creating edge (optional but recommended)
        try:
            if not db.collection(COLLECTION_NAME).has(from_key):
                 logger.error(f"Cannot create relationship: Source document '{from_id}' not found.")
                 return False
            if not db.collection(COLLECTION_NAME).has(to_key):
                 logger.error(f"Cannot create relationship: Target document '{to_id}' not found.")
                 return False
        except (DocumentGetError, ArangoServerError, ArangoClientError) as doc_err:
             logger.error(f"Error checking source/target document existence: {doc_err}", exc_info=True)
             return False


        # Insert the new edge
        meta = edge_collection.insert(edge_doc, overwrite=False) # Don't overwrite if key exists
        logger.success(
            f"Successfully seeded test relationship with key '{meta['_key']}' from '{from_id}' to '{to_id}'."
        )
        return True
    except (DocumentInsertError, ArangoServerError, ArangoClientError) as e:
         # Check for specific errors like "edge source/target vertex does not exist"
        err_code = getattr(e, 'http_exception', {}).status_code if hasattr(e, 'http_exception') else 'N/A'
        logger.error(
            f"Failed to insert test relationship from '{from_id}' to '{to_id}'. Potential missing vertices? HTTP Status: {err_code}. Error: {e}",
            exc_info=True,
        )
        return False
    except Exception as e:
        logger.error(
            f"Unexpected error inserting test relationship: {e}", exc_info=True
        )
        return False


# --- initialize_database Function (Orchestrator) ---
def initialize_database(
    run_setup: bool = True,
    truncate: bool = False,
    force_truncate: bool = False,
    seed_file_path: Optional[str] = None,
) -> Optional[StandardDatabase]:
    """
    Main function to connect, optionally truncate, optionally seed, and ensure ArangoDB components.

    Args:
        run_setup: If True, ensure graph, view, and index structures are created/verified.
        truncate: If True, delete data from main data/edge collections before setup.
        force_truncate: If True, bypass the confirmation prompt for truncation.
        seed_file_path: Path to a JSON file containing {'lessons': [...]} to seed data.

    Returns:
        StandardDatabase object if successful, None otherwise.
    """
    # Initialize LiteLLM Cache once at the beginning
    try:
        logger.info("Initializing LiteLLM Cache...")
        initialize_litellm_cache() # Allow fallback if Redis not configured
        logger.info("LiteLLM Caching initialized (check logs for details).")
    except Exception as cache_err:
         logger.warning(f"Could not initialize LiteLLM Cache (may impact performance/cost): {cache_err}")
         # Decide if this is fatal. If embedding needed for seeding, it might be.
         if seed_file_path:
              logger.error("Cannot seed data without successful LiteLLM initialization.")
              return None


    client = connect_arango()
    if not client:
        return None # Connection failed, logged in connect_arango

    db = ensure_database(client) # Uses default ARANGO_DB_NAME
    if not db:
        return None # DB ensure failed, logged in ensure_database

    if truncate:
        logger.warning("--- TRUNCATE REQUESTED ---")
        # Define collections based on constants
        collections_to_clear = [COLLECTION_NAME, EDGE_COLLECTION_NAME]
        if not truncate_collections(db, collections_to_clear, force=force_truncate):
            logger.error("Truncation failed or was cancelled. Aborting setup.")
            return None
        logger.info("--- Truncation complete ---")

    logger.info("Ensuring base collections exist...")
    # Ensure base document and edge collections exist using the defaults
    collection_obj = ensure_collection(db, COLLECTION_NAME)
    edge_collection_obj = ensure_edge_collection(db, EDGE_COLLECTION_NAME)
    if collection_obj is None or edge_collection_obj is None:
        logger.error("Failed to ensure base document or edge collections exist. Aborting.")
        return None
    logger.success(f"Base collections '{COLLECTION_NAME}' and '{EDGE_COLLECTION_NAME}' ensured.")


    # --- Seeding Logic ---
    seeding_successful = False
    if seed_file_path:
        logger.info(f"--- SEED DATA REQUESTED from file: {seed_file_path} ---")
        resolved_path = Path(seed_file_path).resolve()
        if not resolved_path.is_file():
             logger.error(f"Seed file not found at resolved path: {resolved_path}")
             return None # Cannot seed if file doesn't exist

        try:
            seed_data = load_json_file(str(resolved_path))
            if seed_data is None:
                logger.error(f"Seeding aborted: loaded data is None from: {resolved_path}")
                return None

            # Expecting format {'lessons': [...]}
            lessons_list = seed_data.get("lessons")
            if not isinstance(lessons_list, list):
                logger.error(
                    f"Seed file '{resolved_path}' has invalid format: Top level key 'lessons' with a list value is required. Found type: {type(lessons_list)}"
                )
                return None

            # Seed the documents using the main collection name and embedding field
            if seed_initial_data(db, COLLECTION_NAME, EMBEDDING_FIELD, lessons_list):
                logger.info(f"--- Seeding documents into '{COLLECTION_NAME}' complete. ---")
                seeding_successful = True
                # Optionally seed test relationship IF document seeding occurred
                # Note: seed_test_relationship uses hardcoded keys, ensure they exist in your seed data
                if not seed_test_relationship(db):
                     logger.warning("Failed to seed test relationship (check logs and key existence).")
            else:
                logger.warning(
                    f"Data seeding process for '{COLLECTION_NAME}' encountered errors or did not complete successfully. Check logs."
                )
                # Decide if this is fatal. For now, let setup continue.
                # return None # Uncomment if seeding failure should abort entire setup

        except Exception as e:
            logger.error(
                f"Error during seeding process from file '{resolved_path}'. Error: {e}",
                exc_info=True,
            )
            return None # Abort if seeding process itself throws unexpected error
    else:
        logger.info("No seed file provided, skipping data seeding.")


    # --- Run structure setup (Graph, View, Index) ---
    if run_setup:
        logger.info(
            "Starting ArangoDB structure setup/verification (Graph, View, Index)..."
        )
        # Ensure graph using default names
        graph_obj = ensure_graph(db, GRAPH_NAME, EDGE_COLLECTION_NAME, COLLECTION_NAME)
        if not graph_obj:
             logger.warning(f"Graph '{GRAPH_NAME}' setup failed or encountered issues. Check logs.")
             # Decide if this is fatal. Let's continue for now.

        # Ensure view using default names, linked to main collection
        view_ok = ensure_search_view(db, SEARCH_VIEW_NAME, COLLECTION_NAME)
        if not view_ok:
             logger.warning(f"ArangoSearch View '{SEARCH_VIEW_NAME}' setup failed or encountered issues. Check logs.")
             # Decide if this is fatal. Continue for now.

        # Ensure vector index using default names on main collection/field
        index_ok = ensure_vector_index(db, COLLECTION_NAME, VECTOR_INDEX_NAME, EMBEDDING_FIELD, EMBEDDING_DIMENSION)
        if not index_ok:
             logger.error(f"Vector Index '{VECTOR_INDEX_NAME}' setup FAILED. This might break vector search. Check logs.")
             # Make index failure fatal? Yes, usually required for functionality.
             return None
        else:
             logger.success(f"Vector Index '{VECTOR_INDEX_NAME}' ensured successfully.")

        if not all([graph_obj, view_ok, index_ok]):
            # Changed severity to warning as graph/view might not be strictly needed everywhere
            logger.warning("One or more setup steps (Graph/View/Index) encountered issues. Setup finished, but check logs carefully.")
        else:
            logger.success(
                "ArangoDB structure setup/verification complete (Graph, View, Index)."
            )
    else:
        logger.info("Skipping structure setup (Graph, View, Index) as run_setup=False.")


    # Return the database handle if all critical steps succeeded
    return db


# --- setup_arango_collection Function (More Generic Setup) ---
def setup_arango_collection(
    db_name: str,
    collection_name: str,
    seed_data: Optional[List[Dict[str, Any]]] = None,
    truncate: bool = False, # Added truncate argument
    embedding_field: str = EMBEDDING_FIELD,   # Allow override, default to global
    embedding_dimension: int = EMBEDDING_DIMENSION, # Allow override
    create_view: bool = True, # Flag to control view creation
    create_index: bool = True, # Flag to control index creation
) -> Optional[StandardDatabase]:
    """
    Sets up a specific ArangoDB collection with optional view, index, and data seeding.
    Useful for creating temporary/test collections.

    Args:
        db_name: Name of the database to use/create.
        collection_name: Name of the specific collection to use/create.
        seed_data: Optional list of documents (WITHOUT embeddings) to seed.
                   Embeddings will be generated during seeding if embedding_field is set.
        truncate: If True, truncate the collection before seeding (if it exists).
        embedding_field: The field name where embeddings will be stored/indexed.
        embedding_dimension: The dimension expected for the vector index.
        create_view: If True, create an ArangoSearch view linked to this collection.
        create_index: If True, create a vector index on the embedding_field.

    Returns:
        StandardDatabase object if successful, None if any critical step fails.
    """
    # Note: LiteLLM Cache should be initialized *before* calling this function
    # if seeding requires embedding generation.

    # Connect to ArangoDB
    client = connect_arango()
    if not client:
        logger.error("Failed to connect to ArangoDB")
        return None

    # Ensure database exists
    db = ensure_database(client, db_name)
    if not db:
        logger.error(f"Failed to ensure database '{db_name}'")
        return None

    # Truncate if requested (and collection exists)
    if truncate:
        logger.warning(f"Truncate requested for collection '{collection_name}' in db '{db_name}'")
        try:
            if collection_name in [c["name"] for c in db.collections()]:
                 logger.info(f"Truncating collection '{collection_name}'...")
                 db.collection(collection_name).truncate()
                 logger.success(f"Successfully truncated collection '{collection_name}'.")
            else:
                logger.info(f"Collection '{collection_name}' not found for truncation, skipping.")
        except (ArangoServerError, ArangoClientError) as e:
            logger.error(f"Failed to truncate collection '{collection_name}'. Error: {e}", exc_info=True)
            # Make truncation failure fatal for this specific setup function? Yes, likely intended.
            return None


    # Create collection
    try:
        collection = ensure_collection(db, collection_name)
        if not collection:
            logger.error(f"Failed to ensure collection '{collection_name}' in db '{db_name}'")
            return None # Collection is fundamental

        # --- Use specific names for view and index based on collection ---
        view_name = f"{collection_name}_view"
        index_name = f"{collection_name}_vector_idx"
        # ---------------------------------------------------------------

        # Create search view linked to the collection if requested
        if create_view:
            if not ensure_search_view(db, view_name, collection_name): # Use generated view name
                logger.error(f"Failed to create search view '{view_name}' for '{collection_name}'")
                # Make view failure fatal? Depends on usage. Let's make it fatal here.
                return None
            logger.info(f"Successfully ensured search view '{view_name}' for '{collection_name}'.")

        # Create vector index if requested
        if create_index:
            if not ensure_vector_index(
                db,
                collection_name=collection_name,
                index_name=index_name, # Use generated index name
                embedding_field=embedding_field, # Pass the embedding field name
                dimensions=embedding_dimension, # Pass the dimension
            ):
                logger.error(f"Failed to create vector index '{index_name}' for '{collection_name}'")
                # Make index failure fatal? Yes, required for vector search.
                return None
            logger.info(f"Successfully ensured vector index '{index_name}' for '{collection_name}'.")

        # Seed data if provided
        if seed_data:
            logger.info(
                f"Attempting to seed {len(seed_data)} documents into '{collection_name}'..."
            )
            # Pass collection_name and embedding_field to seed_initial_data
            if not seed_initial_data(db, collection_name, embedding_field, seed_data):
                logger.warning(f"Seeding process for '{collection_name}' completed with failures or no successes.")
                # Make seeding failure fatal? If data is required for tests, yes.
                return None
            logger.info(f"Successfully completed seeding for '{collection_name}'.")


        return db # Return the database handle if all required steps succeeded

    except Exception as e:
        logger.error(f"Error during setup for collection '{collection_name}' in db '{db_name}': {e}", exc_info=True)
        return None


# --- Main Execution Block (for running setup script directly) ---
if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Initialize or setup the ArangoDB database for MCP Doc Retriever."
    )
    parser.add_argument(
        "--truncate",
        action="store_true",
        help=f"WARNING: Delete ALL data from '{COLLECTION_NAME}' and '{EDGE_COLLECTION_NAME}' before setup.",
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
        choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
        help="Set logging level (DEBUG, INFO, WARNING, ERROR, CRITICAL).",
    )
    parser.add_argument(
        "--skip-setup",
        action="store_true",
        help="Skip ensuring Graph, View, and Index structures (only connect, truncate, seed)."
    )
    args = parser.parse_args()

    # Configure logging
    log_level = args.log_level
    logger.remove()
    logger.add(
        sys.stderr,
        level=log_level,
        format="{time:YYYY-MM-DD HH:mm:ss.SSS} | {level: <7} | {name}:{function}:{line} - {message}", # Detailed format
        colorize=True
    )

    logger.info("======== Running ArangoDB Setup Script ========")
    if args.truncate:
        logger.warning("Truncate flag [--truncate] is set. Existing data in main collections will be deleted.")
    if args.seed_file:
        logger.info(
            f"Seed file [--seed-file] provided: {args.seed_file}. Data will be inserted if file is valid."
        )
    if args.skip_setup:
        logger.info("Skip setup [--skip-setup] flag is set. Graph, View, Index creation/verification will be skipped.")

    # Call the main orchestrator function
    final_db = initialize_database(
        run_setup=(not args.skip_setup), # Pass contrário of skip_setup
        truncate=args.truncate,
        force_truncate=args.yes,
        seed_file_path=args.seed_file,
    )

    if final_db:
        logger.info(
            f"Successfully connected to database '{final_db.name}'. Setup process completed (check logs for details)."
        )
        logger.info("Performing final checks...")
        try:
            # Verify collection counts
            coll = final_db.collection(COLLECTION_NAME)
            edge_coll = final_db.collection(EDGE_COLLECTION_NAME)
            logger.info(f"Collection '{COLLECTION_NAME}' count: {coll.count()}")
            logger.info(f"Edge Collection '{EDGE_COLLECTION_NAME}' count: {edge_coll.count()}")

            # Verify view existence (if setup was run)
            if not args.skip_setup:
                if final_db.has_view(SEARCH_VIEW_NAME):
                    logger.info(f"Search View '{SEARCH_VIEW_NAME}' confirmed.")
                else:
                    logger.warning(f"Search View '{SEARCH_VIEW_NAME}' check FAILED post-setup.")

                # Verify vector index existence (if setup was run)
                indexes = coll.indexes()
                if any(i.get('name') == VECTOR_INDEX_NAME and i.get('type') == 'vector' for i in indexes):
                    logger.info(f"Vector Index '{VECTOR_INDEX_NAME}' confirmed.")
                else:
                    logger.warning(f"Vector Index '{VECTOR_INDEX_NAME}' check FAILED post-setup.")
        except Exception as check_err:
            logger.warning(f"Could not perform all post-setup checks: {check_err}")

        logger.info("======== ArangoDB Setup Script Finished Successfully ========")
        sys.exit(0)
    else:
        logger.error("======== ArangoDB Setup Script FAILED ========")
        sys.exit(1)
