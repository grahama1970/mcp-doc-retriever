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



# --- Configuration Loading ---
ARANGO_HOST = os.getenv("ARANGO_HOST", "http://localhost:8529")
ARANGO_USER =from mcp_doc_retriever.arangodb.embedding_utils import (
    get_text_for_embedding,
    get_embedding,
)

# Import the specific function from json_utils
from mcp_doc_retriever.arangodb.json_utils import load_json_file
os.getenv("ARANGO_USER", "root")
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
                "metric": "cosine" # Common choice for semantic similarity ('euclidean', 'dotproduct' also possible)
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
        initialize_litellm_cache(redis_required=False) # Allow fallback if Redis not configured
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

```

**`src/mcp_doc_retriever/arangodb/search_api/semantic.py`**

```python
# src/mcp_doc_retriever/arangodb/search_api/semantic.py
import sys
import os
import uuid
import time
from typing import List, Dict, Any, Optional

from loguru import logger
from arango.database import StandardDatabase
# Removed StandardCollection import as it's no longer directly used in test harness
# from arango.collection import StandardCollection
from arango.exceptions import (
    AQLQueryExecuteError,
    ArangoServerError,
    # CollectionCreateError, # No longer creating directly in test harness
    ArangoClientError,
)

# --- Module Level Imports ---
# Imports needed for the search_semantic function itself and config defaults
try:
    from mcp_doc_retriever.arangodb.config import (
        ALL_DATA_FIELDS_PREVIEW,
        # TAG_ANALYZER, # Not directly used in this semantic AQL version
        VIEW_NAME as BASE_VIEW_NAME,  # Default function arg value
        COLLECTION_NAME as BASE_COLLECTION_NAME,  # Used for test naming convention base
        EMBEDDING_MODEL,  # For embedding generation info & API key checks
        # VECTOR_INDEX_FIELD, # Handled via EMBEDDING_FIELD in arango_setup
        # VECTOR_INDEX_PARAMS, # Handled via EMBEDDING_DIMENSION/params in arango_setup
    )
    # Needed for generating query embeddings & potentially seeding if setup fails
    from mcp_doc_retriever.arangodb.embedding_utils import get_embedding
    # Needed for input validation
    from mcp_doc_retriever.arangodb.search_api.utils import validate_search_params
    # Needed for the actual setup process in test harness
    from mcp_doc_retriever.arangodb.arango_setup import EMBEDDING_FIELD # Import shared constant
except ImportError as e:
    logger.critical(
        f"CRITICAL: Failed module-level import in semantic.py: {e}. Functionality will be broken."
    )
    # Define fallback if needed for basic script execution without full package
    if __name__ != "__main__": # Avoid exit if imported as module
        raise # Re-raise if imported

    # Fallbacks if run as main and imports failed (test harness will likely fail anyway)
    logger.error("Defining fallback variables/functions due to import errors.")
    ALL_DATA_FIELDS_PREVIEW = ['_key', 'problem', 'solution', 'tags']
    BASE_VIEW_NAME = "lessons_view_fallback"
    BASE_COLLECTION_NAME = "lessons_learned_fallback"
    EMBEDDING_MODEL = "unknown-model"
    EMBEDDING_FIELD = "embedding_fallback"
    def get_embedding(text: str) -> Optional[List[float]]: return None
    def validate_search_params(**kwargs): pass

    # Exit if run as main script and basic imports fail
    if __name__ == "__main__":
        sys.exit(1)


# --- search_semantic Function ---
def search_semantic(
    db: StandardDatabase,
    query_embedding: List[float],
    top_n: int = 5,
    similarity_threshold: float = 0.5,
    tags: Optional[List[str]] = None,
    view_name: str = BASE_VIEW_NAME, # Uses default from config
    embedding_field: str = EMBEDDING_FIELD, # Uses default from config/setup
) -> Dict[str, Any]:
    """
    Performs semantic search using a pre-computed query vector via an ArangoSearch VIEW.

    Args:
        db: ArangoDB database connection object.
        query_embedding: The vector representation of the search query.
        top_n: Maximum number of results to return.
        similarity_threshold: Minimum cosine similarity score for results (0.0 to 1.0).
        tags: Optional list of tags to filter results by (exact match on 'tags' field).
        view_name: The name of the ArangoSearch view configured for semantic search.
        embedding_field: The name of the document field containing the vector embeddings.

    Returns:
        A dictionary containing 'results', 'total' matches, 'offset', and 'limit'.

    Assumptions:
        - An ArangoSearch view (`view_name`) exists and is linked to the collection.
        - The view link includes the `embedding_field` and `tags` field.
        - A vector index is configured appropriately (typically outside the view definition,
          directly on the collection for the `embedding_field`).
        - The `COSINE_SIMILARITY` function is available and works with the index.
    """
    search_uuid = str(uuid.uuid4())[:8]
    with logger.contextualize(
        action="search_semantic", search_id=search_uuid, view=view_name, embedding_field=embedding_field
    ):
        # --- Input Validation ---
        try:
            validate_search_params(
                search_text=None, # Not used in pure semantic search
                bm25_threshold=None, # Not used here
                top_n=top_n,
                offset=0, # Offset not handled by this specific AQL pattern
                tags=tags,
                semantic_threshold=similarity_threshold,
            )
            if (
                not query_embedding
                or not isinstance(query_embedding, list)
                or not all(isinstance(x, (int, float)) for x in query_embedding)
            ):
                raise ValueError("query_embedding must be a non-empty list of numbers.")
            if not isinstance(view_name, str) or not view_name:
                 raise ValueError("view_name must be a non-empty string.")
            if not isinstance(embedding_field, str) or not embedding_field:
                 raise ValueError("embedding_field must be a non-empty string.")

        except ValueError as e:
            logger.error(f"Invalid Semantic search parameters: {e}")
            raise # Re-raise validation errors

        logger.info(
            f"Executing Semantic search: tags={tags}, threshold={similarity_threshold}, top_n={top_n}"
        )

        # --- Build AQL Query ---
        tag_filter_aql = ""
        bind_vars: Dict[str, Any] = {
            "@view": view_name, # Bind the view name for security and clarity
            "embedding_field_name": embedding_field, # Bind field name (used if field name needs escaping) - not directly in AQL below
            "query_embedding": query_embedding,
            "similarity_threshold": similarity_threshold,
            "top_n": top_n,
        }

        # Tag filtering using ArangoSearch syntax (more efficient than array containment if indexed)
        # Assumes 'tags' field is linked in the view with 'identity' analyzer
        if tags:
            valid_tags = [str(t).strip() for t in tags if t and isinstance(t, str) and t.strip()]
            if valid_tags:
                # Using PHRASE for exact tag matches (requires identity analyzer on tags field)
                tag_conditions = " AND ".join(
                    [f"PHRASE(doc.tags, @tag_{i}, 'identity')" for i in range(len(valid_tags))]
                )
                tag_filter_aql = f"SEARCH {tag_conditions}" # Add SEARCH keyword for view filtering
                bind_vars.update({f"tag_{i}": tag for i, tag in enumerate(valid_tags)})
                logger.debug(f"Applying tag filter: {tag_conditions}")
            else:
                logger.debug("Empty or invalid tags provided, skipping tag filter.")

        # Fields to return (using KEEP for efficiency)
        # Ensure _key is always included if it's in the preview, otherwise add it
        fields_to_keep = set(ALL_DATA_FIELDS_PREVIEW)
        fields_to_keep.add("_key") # Ensure _key is always kept
        keep_fields_str = ", ".join([f"'{f}'" for f in sorted(list(fields_to_keep)) if isinstance(f, str)])
        keep_clause = f"KEEP(doc, {keep_fields_str})" if keep_fields_str else "doc" # Fallback to full doc if no fields specified


        # AQL query focused on vector similarity within the view context
        # Uses bind variables extensively for safety and clarity
        # Assumes `embedding_field` holds the vector and is accessible via the view link
        aql = f"""
        LET results = (
            FOR doc IN @@view
                {tag_filter_aql} // Apply ArangoSearch tag filter first
                // Ensure embedding field exists and is not null (check might be redundant if index requires it)
                FILTER HAS(doc, @embedding_field_name) AND doc.@embedding_field_name != null

                // Calculate cosine similarity
                LET similarity = COSINE_SIMILARITY(doc.@embedding_field_name, @query_embedding)

                // Filter by similarity threshold
                FILTER similarity >= @similarity_threshold

                // Sort by similarity DESCENDING and limit
                SORT similarity DESC
                LIMIT @top_n

                // Return selected fields and score
                RETURN {{
                    doc: {keep_clause},
                    similarity_score: similarity
                }}
        )

        // Calculate total count matching the criteria (apply same filters)
        LET total_count = COUNT(
            FOR doc IN @@view
                {tag_filter_aql} // Apply ArangoSearch tag filter
                FILTER HAS(doc, @embedding_field_name) AND doc.@embedding_field_name != null
                LET similarity = COSINE_SIMILARITY(doc.@embedding_field_name, @query_embedding)
                FILTER similarity >= @similarity_threshold
                RETURN 1 // Just return 1 for counting
        )

        // Final return structure
        RETURN {{
            results: results,
            total: total_count
        }}
        """
        # Add the actual embedding field name separately for direct use in AQL dot notation
        bind_vars["embedding_field_name"] = embedding_field

        # --- Execute Query ---
        logger.debug(f"Semantic AQL (ID: {search_uuid}):\n{aql}")
        # Log bind vars carefully, potentially masking sensitive data or truncating large ones like embeddings
        log_bind_vars = {k: (f"list[{len(v)}]" if isinstance(v, list) else v) for k, v in bind_vars.items()}
        logger.debug(f"Bind Vars (ID: {search_uuid}): {log_bind_vars}")

        try:
            start_time = time.monotonic()
            cursor = db.aql.execute(aql, bind_vars=bind_vars, stream=False) # Use stream=False for single result
            query_duration = time.monotonic() - start_time

            if cursor:
                data = cursor.pop() # Get the single result document
                results_list = data.get("results", [])
                total_matches = data.get("total", 0)
                logger.success(
                    f"Semantic OK (ID: {search_uuid}). Found {len(results_list)} results (total matches: {total_matches}). Time: {query_duration:.4f}s"
                )
                return {
                    "results": results_list,
                    "total": total_matches,
                    "offset": 0, # Matches AQL structure
                    "limit": top_n, # Matches input limit
                }
            else:
                # Should not happen with stream=False unless there's a connection issue post-execute
                 logger.error(f"Semantic AQL Error (ID: {search_uuid}): Cursor was empty after execution.")
                 raise ArangoServerError("AQL execution returned empty cursor.", url="", method="", http_exception=None) # Simulate server error

        except AQLQueryExecuteError as e:
            # Log detailed AQL error information
            logger.error(
                f"Semantic AQL Error (ID: {search_uuid}): Code={e.error_code}, Msg='{e.error_message}'. HTTP Status: {e.http_status_code}\nQuery:\n{aql}",
                exc_info=False # Don't need full traceback if error message is clear
            )
            raise # Re-raise AQL execution errors
        except (ArangoServerError, ArangoClientError) as e:
            # Handle other potential ArangoDB communication errors
            logger.error(
                f"ArangoDB Error during semantic search (ID: {search_uuid}): {e}",
                exc_info=True # Include traceback for server/client errors
            )
            raise
        except Exception as e:
            # Catch any unexpected Python errors
            logger.exception(f"Unexpected Error during semantic search (ID: {search_uuid}): {e}")
            raise


# --- Standalone Verification Harness (Semantic) ---

def print_usage():
    """Prints usage instructions for the standalone semantic test mode."""
    # Updated usage text reflecting use of setup_arango_collection
    print(f"""
Usage: python {os.path.basename(__file__)} [-h|--help]

This script runs a self-contained test for the search_semantic function.
It requires ArangoDB connection details AND an Embedding API key to be set via environment variables.

Prerequisites:
 - ArangoDB running and connection details in env vars:
   (ARANGO_HOST, ARANGO_USER, ARANGO_PASSWORD, ARANGO_DB_NAME)
 - API Key for the embedding model specified in config.py (EMBEDDING_MODEL: '{EMBEDDING_MODEL}').
   Example: If using OpenAI, set OPENAI_API_KEY. Check arango_setup.py for provider logic.
 - Required Python packages installed (see requirements.txt/pyproject.toml).
 - Your `arango_setup.py` must contain functional `connect_arango`, `ensure_database`,
   and `setup_arango_collection` functions.
 - Optional: Redis env vars for caching (REDIS_HOST, etc.) - used by initialize_litellm_cache.

It will:
 - Check for necessary API key based on EMBEDDING_MODEL.
 - Initialize LiteLLM caching (Redis or fallback).
 - Connect to ArangoDB and ensure the database (ARANGO_DB_NAME: '{os.getenv("ARANGO_DB_NAME", "doc_retriever")}') exists.
 - Call `setup_arango_collection` to create/configure test resources:
     * A temporary collection (e.g., '_semantic_test_runID').
     * A linked ArangoSearch view (e.g., '_semantic_test_runID_view').
     * A vector index on the collection (e.g., '_semantic_test_runID_vector_idx').
     * Generate embeddings for sample documents using '{EMBEDDING_MODEL}'.
     * Insert sample documents with embeddings into the test collection.
 - Generate embeddings for test queries using '{EMBEDDING_MODEL}'.
 - Run Semantic search tests (basic, tag filtered, threshold tests) against the generated TEST VIEW.
 - Print PASS/FAIL status for each test.
 - Clean up the temporary collection and view created by the test.

NOTE: Test execution time depends on embedding generation speed. Caching helps on reruns.
""")


if __name__ == "__main__":
    # Imports required ONLY for the test harness execution
    from loguru import logger as _logger # Use distinct logger for harness messages
    # Removed StandardCollection import

    try:
        # Setup functions - Import setup_arango_collection and core connection utils
        from mcp_doc_retriever.arangodb.arango_setup import (
            connect_arango,
            ensure_database,
            setup_arango_collection, # Main function for setup
            # No longer need ensure_collection, ensure_search_view, ensure_vector_index directly
            EMBEDDING_FIELD as SETUP_EMBEDDING_FIELD, # Import the field name used by setup
            EMBEDDING_DIMENSION as SETUP_EMBEDDING_DIMENSION # Import the dimension used by setup
        )
        # LiteLLM cache initialization
        from mcp_doc_retriever.arangodb.initialize_litellm_cache import (
            initialize_litellm_cache,
        )
    except ImportError as e:
        # Use standard print for critical errors before logger might be set up
        print(f"CRITICAL: Failed to import test setup dependencies: {e}. Cannot run test harness.", file=sys.stderr)
        sys.exit(1)

    # --- Argument Parsing (Help only) ---
    if "--help" in sys.argv or "-h" in sys.argv:
        print_usage()
        sys.exit(0)

    # --- Logger Configuration ---
    # Configure logger specifically for the test harness output
    _logger.remove()
    _logger.add(
        sys.stderr,
        level=os.environ.get("LOG_LEVEL", "INFO").upper(),
        format="{time:YYYY-MM-DD HH:mm:ss.SSS} | {level: <7} | {name}:{function}:{line} | {message}",
        colorize=True,
    )
    _logger.info("=" * 30 + " Starting Semantic Standalone Test " + "=" * 30)


    # --- PREREQUISITE CHECK: API Key ---
    # (API key check remains largely the same, using EMBEDDING_MODEL from config)
    _logger.info(f"Checking API key requirement for model: '{EMBEDDING_MODEL}'")
    required_key = None
    # Basic heuristic for required env var based on model name part
    if ("openai" in EMBEDDING_MODEL or "ada" in EMBEDDING_MODEL or "gpt" in EMBEDDING_MODEL) and "azure" not in EMBEDDING_MODEL:
        required_key = "OPENAI_API_KEY"
    elif "azure" in EMBEDDING_MODEL:
        # Azure OpenAI can use different keys, check common ones
        if not ("AZURE_API_KEY" in os.environ or ("OPENAI_API_KEY" in os.environ and "AZURE_API_BASE" in os.environ)):
             _logger.error(
                 f"❌ FATAL: Azure model '{EMBEDDING_MODEL}' requires 'AZURE_API_KEY' OR ('OPENAI_API_KEY' and 'AZURE_API_BASE') env vars to be set."
             )
             sys.exit(1)
        else:
             _logger.success("Found necessary environment variables for Azure.")
             required_key = "AZURE_CONFIG_FOUND" # Mark as found
    elif "mistral" in EMBEDDING_MODEL:
        required_key = "MISTRAL_API_KEY"
    elif "cohere" in EMBEDDING_MODEL:
        required_key = "COHERE_API_KEY"
    elif "google" in EMBEDDING_MODEL or "gemini" in EMBEDDING_MODEL:
        required_key = "GOOGLE_API_KEY" # Or potentially other auth methods
    # Add other providers as needed
    else:
        _logger.warning(
             f"Unrecognized embedding model provider for '{EMBEDDING_MODEL}'. API key requirement unknown. Relying on LiteLLM's auto-detection/configuration."
        )

    # Check specific key if identified and not already handled (like Azure)
    if required_key and required_key != "AZURE_CONFIG_FOUND" and required_key not in os.environ:
       _logger.error(
           f"❌ FATAL: Required environment variable '{required_key}' for embedding model '{EMBEDDING_MODEL}' is not set."
       )
       sys.exit(1)
    elif required_key and required_key != "AZURE_CONFIG_FOUND":
       _logger.success(f"Found required environment variable '{required_key}'.")
    elif not required_key:
        _logger.info("API key requirement undetermined, proceeding.")


    # --- Test Setup Variables ---
    run_id = str(uuid.uuid4())[:6]
    # Use base collection name from config to build test name
    test_coll_name = f"_{BASE_COLLECTION_NAME}_semantic_test_{run_id}"
    # View name is now determined by setup_arango_collection convention
    test_view_name_generated = f"{test_coll_name}_view"
    db: Optional[StandardDatabase] = None
    db_conn_info: Optional[Dict] = None # To store DB connection details if needed
    passed = True
    inserted_keys_expected = set() # Store expected keys from raw data


    # --- Main Test Execution Block ---
    try:
        # 1. Initialize LiteLLM Cache (Important before embedding generation)
        _logger.info("--- Step 1: Initializing LiteLLM Caching ---")
        try:
            initialize_litellm_cache(redis_required=False) # Allow fallback memory cache
            _logger.info("LiteLLM Caching initialized (using Redis if configured, otherwise fallback).")
        except Exception as cache_err:
             _logger.warning(f"Could not initialize LiteLLM Cache (will impact performance/cost): {cache_err}")
             # Do not exit, but performance will be worse


        # 2. Connect to ArangoDB & Ensure DB
        _logger.info("--- Step 2: Connecting to ArangoDB ---")
        client = connect_arango()
        if not client:
            _logger.error("❌ FATAL: Failed to connect to ArangoDB instance.")
            sys.exit(1) # connect_arango logs specifics
        _logger.success("Successfully connected to ArangoDB instance.")

        # Use ARANGO_DB_NAME from environment/defaults
        db_name_to_use = os.getenv("ARANGO_DB_NAME", "doc_retriever")
        db = ensure_database(client, db_name=db_name_to_use)
        if not db:
             _logger.error(f"❌ FATAL: Failed to ensure database '{db_name_to_use}'.")
             sys.exit(1) # ensure_database logs specifics
        _logger.success(f"Ensured database exists: '{db.name}'")
        db_conn_info = {"name": db.name} # Store for potential use


        # 3. Prepare Test Data (RAW, without embeddings)
        _logger.info("--- Step 3: Preparing Raw Test Documents ---")
        # Define the raw documents that setup_arango_collection will process
        TEST_DOCS_RAW = [ # Using keys with run_id for isolation
             {
                "_key": f"sem_doc1_{run_id}",
                "problem": "Parsing command line arguments in shell scripts.",
                "solution": "Use 'getopts' for POSIX compatibility or leverage bash-specific features like arrays for simple cases. Consider libraries like 'argparse' in Python.",
                "tags": ["shell", "script", "arguments", "posix", "cli", "bash"],
                "context": "Common task in automation scripts needing input parameters.",
                "lesson": "Choose the right tool based on complexity and portability needs.",
                "role": "developer",
            },
            {
                "_key": f"sem_doc2_{run_id}",
                "problem": "Gracefully handling JSON decoding errors in Python applications.",
                "solution": "Wrap `json.loads()` calls in a try-except block catching `json.JSONDecodeError`. Log the error and provide a default value or raise a custom application error.",
                "tags": ["python", "json", "error-handling", "validation", "robustness", "api"],
                "context": "Essential for processing external API responses or file inputs reliably.",
                "lesson": "Anticipate failures with external data formats.",
                 "role": "backend-dev",
            },
            {
                "_key": f"sem_doc3_{run_id}",
                "problem": "Detecting and debugging potential race conditions in concurrent Go programs.",
                "solution": "Compile and run the application or tests with the Go race detector enabled: `go run -race main.go` or `go test -race ./...`. Analyze the output for reported data races.",
                "tags": ["golang", "concurrency", "race-condition", "debug", "testing", "go"],
                "context": "Critical for ensuring correctness and stability in multi-threaded Go applications.",
                 "lesson": "Leverage built-in tooling for complex concurrency issues.",
                 "role": "systems-programmer",
            },
            {
                "_key": f"sem_doc4_{run_id}",
                "problem": "Optimizing database query performance when dealing with large tables.",
                "solution": "Analyze query execution plans using EXPLAIN (or similar). Add appropriate indexes on columns used in WHERE clauses, JOIN conditions, and ORDER BY clauses. Consider denormalization or caching for frequently accessed data.",
                "tags": ["database", "performance", "sql", "indexing", "optimization", "query-tuning", "dba"],
                "context": "Fundamental aspect of database administration and scalable application development.",
                 "lesson": "Indexing is key, but understand the trade-offs.",
                 "role": "database-admin",
            },
        ]
        inserted_keys_expected = {doc["_key"] for doc in TEST_DOCS_RAW}
        _logger.info(f"Prepared {len(TEST_DOCS_RAW)} raw test documents with keys like '{list(inserted_keys_expected)[0]}'.")


        # 4. *** CRITICAL: Use setup_arango_collection ***
        _logger.info("--- Step 4: Setting up Test Collection, View, Index & Seeding Data ---")
        _logger.info(
            f"Calling setup_arango_collection for: DB='{db.name}', Collection='{test_coll_name}'"
        )
        # This function now handles: collection creation, view creation, index creation, and seeding (including embedding generation).
        # It uses the embedding field/dimension constants defined in arango_setup.py
        db_after_setup = setup_arango_collection(
            db_name=db.name,
            collection_name=test_coll_name,
            seed_data=TEST_DOCS_RAW, # Pass the raw documents for seeding
            truncate=False, # Don't truncate, it's a new unique collection per run
            create_view=True, # Ensure view is created for the test
            create_index=True, # Ensure index is created for the test
            embedding_field=SETUP_EMBEDDING_FIELD, # Pass field from setup module
            embedding_dimension=SETUP_EMBEDDING_DIMENSION # Pass dimension from setup module
        )

        if not db_after_setup:
            # setup_arango_collection logs the specific error leading to failure
            raise RuntimeError(f"❌ FATAL: setup_arango_collection failed for collection '{test_coll_name}'. Cannot proceed with tests.")

        _logger.success(f"Setup complete via setup_arango_collection. Resources created/verified:")
        _logger.success(f"  - Collection: '{test_coll_name}'")
        _logger.success(f"  - View:       '{test_view_name_generated}' (Target for tests)")
        _logger.success(f"  - Index Name: '{test_coll_name}_vector_idx'")
        _logger.success(f"  - Seed Data:  {len(TEST_DOCS_RAW)} documents processed (check detailed logs from setup).")


        # 5. Optional: Verify document count after seeding (allow time for consistency)
        _logger.info("--- Step 5: Verifying Document Count Post-Setup ---")
        time.sleep(3) # Short delay consistency after seeding/indexing
        try:
             count = db.collection(test_coll_name).count()
             if count == len(TEST_DOCS_RAW):
                 _logger.info(f"✅ Verified document count in '{test_coll_name}': {count}")
             else:
                  _logger.warning(f"⚠️ Document count mismatch in '{test_coll_name}'. Expected: {len(TEST_DOCS_RAW)}, Found: {count}. Seeding might have had issues.")
                  # Mark as non-fatal for now, tests might still partially work
                  # passed = False # Uncomment if exact count is critical for test validity
        except Exception as count_err:
              _logger.warning(f"⚠️ Could not verify document count: {count_err}")


        # 6. Wait for Indexing / View Consolidation (Crucial before querying)
        _logger.info("--- Step 6: Waiting for Indexing & View Consolidation ---")
        wait_time = 5 # seconds - adjust based on data size and system speed
        _logger.info(f"Waiting {wait_time}s...")
        time.sleep(wait_time)


        # 7. Run Test Cases using the generated view name
        _logger.info("--- Step 7: Running Semantic Search Test Cases ---")
        _logger.info(f"--- Targeting Test View: '{test_view_name_generated}' ---")

        # Test Case 1: Basic Semantic Match
        _logger.info("Test Case 1: Query 'shell script command line arguments'")
        query1 = "shell script command line arguments"
        try:
            query1_embedding = get_embedding(query1)
            if not query1_embedding: raise ValueError("Failed to get embedding for query 1")
            results1 = search_semantic(
                db=db,
                query_embedding=query1_embedding,
                top_n=3,
                similarity_threshold=0.75, # May need adjustment
                tags=None,
                view_name=test_view_name_generated, # Use the generated view name
                embedding_field=SETUP_EMBEDDING_FIELD # Pass the correct field name
            )
            keys1 = {r["doc"]["_key"] for r in results1.get("results", [])}
            expected_key1 = f"sem_doc1_{run_id}"
            # Check if the expected key is the top result or at least present
            is_present = expected_key1 in keys1
            is_top = results1.get("results") and results1["results"][0]["doc"]["_key"] == expected_key1

            if is_present and results1.get("total", 0) >= 1:
                top_score = results1["results"][0]['similarity_score'] if is_top else 'N/A'
                _logger.success(
                    f"✅ TC1 PASSED. Found '{expected_key1}' (Top: {is_top}, Score: {top_score:.4f}). Keys:{keys1}, Total:{results1.get('total')}"
                )
            else:
                _logger.error(
                    f"❌ TC1 FAILED. Expected '{expected_key1}'. Got Keys:{keys1}, Total:{results1.get('total')}"
                )
                _logger.error(f"   Results: {results1.get('results')}") # Log results on failure
                passed = False
        except Exception as e1:
            _logger.error(f"❌ TC1 FAILED with exception: {e1}", exc_info=True)
            passed = False

        # Test Case 2: Semantic Match with Tag Filter
        _logger.info("Test Case 2: Query 'python json decode issues', Tag 'python'")
        query2 = "python json decode issues"
        try:
            query2_embedding = get_embedding(query2)
            if not query2_embedding: raise ValueError("Failed to get embedding for query 2")
            results2 = search_semantic(
                db=db,
                query_embedding=query2_embedding,
                top_n=3,
                similarity_threshold=0.75, # May need adjustment
                tags=["python"], # Apply tag filter
                view_name=test_view_name_generated, # Use the generated view name
                embedding_field=SETUP_EMBEDDING_FIELD # Pass the correct field name
            )
            keys2 = {r["doc"]["_key"] for r in results2.get("results", [])}
            expected_key2 = f"sem_doc2_{run_id}"
            # Expecting only the python doc due to tag filter
            if keys2 == {expected_key2} and results2.get("total", 0) == 1:
                score = results2["results"][0]['similarity_score']
                _logger.success(
                    f"✅ TC2 PASSED. Found exact key '{expected_key2}' with tag filter (Score: {score:.4f}). Total:{results2.get('total')}"
                )
            else:
                _logger.error(
                    f"❌ TC2 FAILED. Expected {{'{expected_key2}'}} with tag filter. Got Keys:{keys2}, Total:{results2.get('total')}"
                )
                _logger.error(f"   Results: {results2.get('results')}")
                passed = False
        except Exception as e2:
            _logger.error(f"❌ TC2 FAILED with exception: {e2}", exc_info=True)
            passed = False

        # Test Case 3: High Threshold (Likely No Results)
        _logger.info("Test Case 3: Query 'go concurrency race detection', Threshold 0.999")
        query3 = "go concurrency race detection"
        try:
            query3_embedding = get_embedding(query3)
            if not query3_embedding: raise ValueError("Failed to get embedding for query 3")
            results3 = search_semantic(
                db=db,
                query_embedding=query3_embedding,
                top_n=3,
                similarity_threshold=0.999, # Extremely high threshold
                tags=None,
                view_name=test_view_name_generated, # Use the generated view name
                 embedding_field=SETUP_EMBEDDING_FIELD # Pass the correct field name
           )
            keys3 = {r["doc"]["_key"] for r in results3.get("results", [])}
            # Expecting 0 results due to the high threshold
            if not keys3 and results3.get("total", 0) == 0:
                _logger.success(
                    f"✅ TC3 PASSED. Found 0 results as expected (high threshold 0.999). Total:{results3.get('total')}"
                )
            else:
                _logger.error(
                    f"❌ TC3 FAILED. Expected 0 results with high threshold. Got Keys:{keys3}, Total:{results3.get('total')}"
                )
                _logger.error(f"   Results: {results3.get('results')}")
                passed = False
        except Exception as e3:
            _logger.error(f"❌ TC3 FAILED with exception: {e3}", exc_info=True)
            passed = False

        # Test Case 4: Irrelevant Query (Likely No Results or Low Score)
        _logger.info("Test Case 4: Query 'best chocolate chip cookie recipe'")
        query4 = "best chocolate chip cookie recipe"
        try:
            query4_embedding = get_embedding(query4)
            if not query4_embedding: raise ValueError("Failed to get embedding for query 4")
            # Use a reasonable threshold, e.g., 0.6 or 0.7, results *should* be below this
            test_threshold = 0.7
            results4 = search_semantic(
                db=db,
                query_embedding=query4_embedding,
                top_n=3,
                similarity_threshold=test_threshold,
                tags=None,
                view_name=test_view_name_generated, # Use the generated view name
                 embedding_field=SETUP_EMBEDDING_FIELD # Pass the correct field name
           )
            keys4 = {r["doc"]["_key"] for r in results4.get("results", [])}
            # Expecting 0 results as the query is irrelevant
            if not keys4 and results4.get("total", 0) == 0:
                _logger.success(
                    f"✅ TC4 PASSED. Found 0 results as expected (irrelevant query, threshold {test_threshold}). Total:{results4.get('total')}"
                )
            else:
                 # If it *does* find results above threshold, log them but fail the test's intent
                 _logger.error(
                    f"❌ TC4 FAILED. Expected 0 results for irrelevant query above threshold {test_threshold}. Got Keys:{keys4}, Total:{results4.get('total')}"
                )
                 _logger.error(f"   Results: {results4.get('results')}")
                 passed = False
        except Exception as e4:
            _logger.error(f"❌ TC4 FAILED with exception: {e4}", exc_info=True)
            passed = False

    # --- Error Handling for Setup/Execution ---
    except RuntimeError as e:
        # Catch setup-specific RuntimeErrors (like setup_arango_collection failure)
        _logger.error(f"🛑 Test harness setup failed: {e}", exc_info=False) # Don't need full trace usually
        passed = False
    except Exception as e:
        # Catch any other unexpected errors during the main try block
        _logger.exception( # Use exception to get full traceback for unexpected errors
            f"💥 An unexpected error occurred during the test harness execution: {e}"
        )
        passed = False

    # --- Cleanup Phase ---
    finally:
        _logger.info("--- Step 8: Cleaning Up Test Resources ---")
        if db: # Only attempt cleanup if DB connection was established
            # Drop view first (as it depends on the collection)
            try:
                _logger.debug(f"Attempting to drop test view: '{test_view_name_generated}'")
                # Use the generated view name for deletion
                db.delete_view(test_view_name_generated, ignore_missing=True)
                _logger.info(
                    f"Drop view command sent for '{test_view_name_generated}' (ignore_missing=True)."
                )
            except (ArangoServerError, ArangoClientError) as e_del_v:
                # Log warning but continue cleanup
                _logger.warning(f"⚠️ Could not reliably drop test view '{test_view_name_generated}': {e_del_v}")
            except Exception as e_del_v_unexpected:
                 _logger.warning(f"⚠️ Unexpected error dropping view '{test_view_name_generated}': {e_del_v_unexpected}")


            # Drop collection (this should also remove associated standard/vector indexes)
            try:
                _logger.debug(f"Attempting to drop test collection: '{test_coll_name}'")
                db.delete_collection(test_coll_name, ignore_missing=True)
                _logger.info(
                    f"Drop collection command sent for '{test_coll_name}' (ignore_missing=True)."
                )
            except (ArangoServerError, ArangoClientError) as e_del_c:
                 # Log warning but finish execution
                 _logger.warning(
                    f"⚠️ Could not reliably drop test collection '{test_coll_name}': {e_del_c}"
                )
            except Exception as e_del_c_unexpected:
                 _logger.warning(f"⚠️ Unexpected error dropping collection '{test_coll_name}': {e_del_c_unexpected}")

        else:
            _logger.warning("DB connection was not established, skipping ArangoDB cleanup.")

        _logger.info("-" * 80)
        if passed:
            _logger.success(
                "✅✅✅ Semantic Standalone Test Completed Successfully ✅✅✅"
            )
            sys.exit(0)
        else:
            _logger.error(
                "❌❌❌ Semantic Standalone Test FAILED (check logs above for specific errors) ❌❌❌"
            )
            sys.exit(1)