"""
ArangoDB Hybrid Search & Graph Traversal Example

Description:
This script demonstrates setting up ArangoDB for keyword (BM25), semantic
(vector similarity), hybrid search, and graph traversal. It also shows basic
CRUD operations. It integrates with LiteLLM for embedding generation and
caching (Redis/in-memory fallback).

Key Steps:
1. Initializes LiteLLM caching.
2. Connects to ArangoDB, ensures database, collection, ArangoSearch view, and graph.
3. Inserts sample data if the collection is empty.
4. Demonstrates basic CRUD (Add, Get, Update, Delete) for lessons.
5. Demonstrates graph operations: adding a relationship and traversing the graph.
6. Demonstrates various search types (BM25, Semantic, Hybrid).

Third-Party Package Documentation:
- python-arango: https://docs.python-arango.com/en/main/
- Loguru: https://loguru.readthedocs.io/en/stable/
- LiteLLM: https://docs.litellm.ai/docs/embedding/supported_embedding_models
- ArangoDB AQL: https://docs.arangodb.com/stable/aql/
- ArangoSearch Views: https://docs.arangodb.com/stable/arangosearch/views/
- ArangoDB Graphs: https://docs.arangodb.com/stable/graphs/
- LiteLLM Caching: https://docs.litellm.ai/docs/proxy/caching

Setup:
(Same as previous version - ensure environment variables are set)

"""

import sys
import os
import json  # For logging graph results
from loguru import logger
from dotenv import load_dotenv
from typing import List, Dict  # For type hinting
from typing import Optional  # For type hinting

# --- Local Imports ---
try:
    # Import setup, search, crud APIs, config, embedding
    from mcp_doc_retriever.arangodb.arango_setup import (
        connect_arango,
        ensure_database,
        ensure_collection,
        ensure_edge_collection,  # Needed for graph ensure/ops
        ensure_view,
        ensure_graph,  # <-- Added
        insert_sample_if_empty,
    )
    from mcp_doc_retriever.arangodb.initialize_litellm_cache import (
        initialize_litellm_cache,
    )

    # Import graph_traverse from search_api
    from mcp_doc_retriever.arangodb.search_api import (
        search_bm25,
        search_semantic,
        hybrid_search,
        graph_traverse,  # <-- Added
    )

    # Import relationship functions from crud_api
    from mcp_doc_retriever.arangodb.crud_api import (
        add_lesson,
        get_lesson,
        update_lesson,
        delete_lesson,
        add_relationship,  # <-- Added
        delete_relationship,  # <-- Added (for cleanup)
    )
    from mcp_doc_retriever.arangodb.embedding_utils import get_embedding

    # Import config needed for graph name etc.
    from mcp_doc_retriever.arangodb.config import (
        GRAPH_NAME,  # <-- Added
        COLLECTION_NAME,  # <-- Added (for constructing _id)
        EDGE_COLLECTION_NAME,  # <-- Added (for ensure_graph)
        RELATIONSHIP_TYPE_RELATED,  # Example type
    )

except ImportError as e:
    init_msg = f"ERROR: Failed to import required modules: {e}. Ensure script is run correctly relative to the project structure or PYTHONPATH is set."
    # Use logger if available, otherwise print and exit
    try:
        logger.critical(init_msg)
    except NameError:
        print(init_msg, file=sys.stderr)
    sys.exit(1)

# Load environment variables from .env file if present
load_dotenv()

# --- Loguru Configuration ---
logger.remove()
log_level = os.environ.get("LOG_LEVEL", "INFO").upper()
valid_levels = ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
if log_level not in valid_levels:
    print(
        f"Warning: Invalid log level '{log_level}'. Defaulting to INFO.",
        file=sys.stderr,
    )
    log_level = "INFO"

logger.add(
    sys.stderr,
    level=log_level,
    format="{time:YYYY-MM-DD HH:mm:ss} | {level: <7} | {name}:{function}:{line} | {message}",
    backtrace=True,  # Keep True for debugging during development
    diagnose=True,  # Keep True for debugging during development
)


# --- Search Result Logging Helper (Unchanged) ---
def log_search_results(search_data: dict, search_type: str, score_field: str):
    """Logs search results in a readable format."""
    if not isinstance(search_data, dict):
        logger.warning(f"_display_results expected a dict, got {type(search_data)}")
        return
    results = search_data.get("results", [])
    total = search_data.get("total", len(results))  # Estimate total if not provided
    offset = search_data.get("offset", 0)
    # limit = search_data.get("limit", len(results)) # limit not always present

    logger.info(
        f"--- {search_type} Results (Showing {offset + 1}-{offset + len(results)} of ~{total}) ---"
    )
    if not results:
        logger.info("No relevant documents found matching the criteria.")
    else:
        for i, result_item in enumerate(results, start=1):
            if not isinstance(result_item, dict):
                continue  # Skip non-dict results

            score = result_item.get(score_field, 0.0)
            doc = result_item.get("doc", result_item if "_key" in result_item else {})

            key = doc.get("_key", "N/A")
            problem = (
                str(doc.get("problem", "N/A"))[:80].replace("\n", " ") + "..."
            )  # Ensure string, limit length
            tags = ", ".join(doc.get("tags", []))

            # Log other scores if present (for hybrid search)
            other_scores = []
            if "bm25_score" in result_item and score_field != "bm25_score":
                other_scores.append(f"BM25: {result_item['bm25_score']:.4f}")
            if "similarity_score" in result_item and score_field != "similarity_score":
                other_scores.append(f"Sim: {result_item['similarity_score']:.4f}")
            other_scores_str = f" ({', '.join(other_scores)})" if other_scores else ""

            logger.info(
                f"  {offset + i}. Score: {score:.4f}{other_scores_str} | Key: {key} | Problem: {problem} | Tags: [{tags}]"
            )


# --- Graph Traversal Result Logging Helper ---
def log_traversal_results(results: Optional[List[Dict]], start_node: str):
    """Logs graph traversal results."""
    logger.info(f"--- Graph Traversal Results (Starting from: {start_node}) ---")
    if results is None:  # Indicates an error occurred during traversal
        logger.error("Traversal failed or returned None.")
        return
    if not results:
        logger.info("No paths found matching the traversal criteria.")
        return

    logger.info(f"Found {len(results)} paths:")
    # For complex paths, JSON is often best. Limit output length for console.
    try:
        results_json = json.dumps(results, indent=2)
        # Limit log output size
        max_log_len = 2000
        if len(results_json) > max_log_len:
            results_json = results_json[:max_log_len] + "\n... (results truncated)"
        logger.info(f"\n{results_json}")
    except TypeError as e:
        logger.error(f"Could not serialize traversal results to JSON: {e}")
        # Fallback: Log path count or basic info
        for i, path in enumerate(results):
            logger.info(
                f"  Path {i + 1}: Vertices: {len(path.get('vertices', []))}, Edges: {len(path.get('edges', []))}"
            )


# --- Main Demo Execution ---
def run_demo():
    """Executes the main demonstration workflow including CRUD and Graph examples."""
    logger.info("=" * 20 + " Starting ArangoDB Programmatic Demo " + "=" * 20)

    # --- Prerequisites & Initialization ---
    required_key = "OPENAI_API_KEY"  # Or your specific embedding API key env var
    if required_key not in os.environ:
        logger.error(f"Required env var {required_key} not set for embeddings.")
        # sys.exit(1) # Exit if embeddings are crucial

    logger.info("--- Initializing LiteLLM Caching ---")
    try:
        initialize_litellm_cache()
        logger.info("--- Caching Initialized ---")
    except Exception as cache_err:
        logger.warning(f"Could not initialize LiteLLM cache: {cache_err}")

    db = None  # Initialize db to None for finally block
    new_key = None  # Track key created in CRUD
    demo_edge_key = None  # Track edge created in Graph demo

    try:
        # --- ArangoDB Setup ---
        logger.info("--- Running ArangoDB Setup Phase ---")
        client = connect_arango()
        db = ensure_database(client)
        collection = ensure_collection(db, COLLECTION_NAME)  # Pass name explicitly
        ensure_edge_collection(db)  # Ensure edge collection exists
        ensure_view(db)  # Ensure ArangoSearch view exists
        ensure_graph(
            db, GRAPH_NAME, EDGE_COLLECTION_NAME, COLLECTION_NAME
        )  # <-- Ensure Graph
        insert_sample_if_empty(collection)  # Insert sample data
        logger.info("--- ArangoDB Setup Complete ---")

        # --- CRUD Examples ---
        logger.info("--- Running CRUD Examples ---")
        new_lesson_data = {
            "problem": "Docker build fails due to rate limits on base image pull.",
            "solution": "Use a caching proxy like Nexus or Harbor, or authenticate pulls.",
            "tags": ["docker", "ci", "rate-limit", "registry", "nexus", "demo"],
            "severity": "MEDIUM",
            "role": "DevOps Engineer",
            # Timestamp and _key will be added by add_lesson if needed
        }
        # Add
        added_meta = add_lesson(db, new_lesson_data)
        if added_meta:
            new_key = added_meta.get("_key")
            logger.info(f"CRUD Add: Success, new key = {new_key}")
        else:
            logger.error("CRUD Add: Failed")
            new_key = None  # Ensure key is None if add failed

        # Get (if add succeeded)
        if new_key:
            retrieved_doc = get_lesson(db, new_key)
            if retrieved_doc:
                logger.info(
                    f"CRUD Get: Success, retrieved problem: {retrieved_doc.get('problem')}"
                )
            else:
                logger.error(f"CRUD Get: Failed to retrieve {new_key}")

        # Update (if add succeeded)
        if new_key:
            update_payload = {
                "severity": "HIGH",
                "tags": ["docker", "ci", "rate-limit", "auth", "updated-demo"],
            }
            updated_meta = update_lesson(db, new_key, update_payload)
            if updated_meta:
                logger.info(
                    f"CRUD Update: Success, new rev = {updated_meta.get('_rev')}"
                )
                # Verify update
                updated_doc = get_lesson(db, new_key)
                if updated_doc:
                    logger.info(
                        f"CRUD Update Verify: Severity = {updated_doc.get('severity')}, Tags = {updated_doc.get('tags')}"
                    )
            else:
                logger.error(f"CRUD Update: Failed for {new_key}")

        # Note: Deletion is moved after graph examples to keep the node available

        logger.info("--- CRUD Add/Get/Update Examples Complete ---")

        # --- Graph Examples ---
        logger.info("--- Running Graph Examples ---")
        # Need existing keys. Try sample keys first, fallback to new_key if created.
        # Assuming 'insert_sample_if_empty' creates keys like 'sample_doc_1', 'sample_doc_2'
        key1 = "sample_doc_1"
        key2 = "sample_doc_2"
        key1_id = f"{COLLECTION_NAME}/{key1}"
        key2_id = f"{COLLECTION_NAME}/{key2}"

        # Check if these keys actually exist
        doc1_exists = get_lesson(db, key1) is not None
        doc2_exists = get_lesson(db, key2) is not None
        new_key_exists = new_key and (get_lesson(db, new_key) is not None)

        start_node_key = None
        target_node_key = None

        if doc1_exists:
            start_node_key = key1
            if doc2_exists:
                target_node_key = key2
            elif new_key_exists:
                target_node_key = new_key  # Fallback target
        elif new_key_exists:  # Use new_key if doc1 doesn't exist
            start_node_key = new_key
            # Need another key for relationship, maybe skip relationship add if only one key found?
            if doc2_exists:
                target_node_key = key2

        # Add Relationship (if we have two valid keys)
        if start_node_key and target_node_key and start_node_key != target_node_key:
            logger.info(
                f"Attempting to add relationship: {start_node_key} -> {target_node_key}"
            )
            rel_meta = add_relationship(
                db,
                start_node_key,
                target_node_key,
                rationale="Demo link between documents",
                relationship_type=RELATIONSHIP_TYPE_RELATED.upper(),  # Use config constant
            )
            if rel_meta:
                demo_edge_key = rel_meta.get("_key")
                logger.success(
                    f"Graph Add Relationship: Success, edge key = {demo_edge_key}"
                )
            else:
                logger.error("Graph Add Relationship: Failed")
        else:
            logger.warning(
                "Skipping graph relationship add example (could not find two distinct keys)."
            )

        # Graph Traversal (if we have a valid start key)
        if start_node_key:
            start_node_id = f"{COLLECTION_NAME}/{start_node_key}"
            logger.info(
                f"Attempting graph traversal from {start_node_id} (Depth 1, OUTBOUND)"
            )
            traversal_results = graph_traverse(
                db=db,
                start_node_id=start_node_id,
                graph_name=GRAPH_NAME,
                min_depth=1,
                max_depth=1,  # Keep depth shallow for demo
                direction="OUTBOUND",
                limit=5,  # Limit results
            )
            log_traversal_results(traversal_results, start_node_id)

            # Example: Traverse INBOUND from target node (if relationship was added)
            if demo_edge_key and target_node_key:
                target_node_id = f"{COLLECTION_NAME}/{target_node_key}"
                logger.info(
                    f"Attempting graph traversal from {target_node_id} (Depth 1, INBOUND)"
                )
                inbound_results = graph_traverse(
                    db, target_node_id, GRAPH_NAME, 1, 1, "INBOUND", 5
                )
                log_traversal_results(inbound_results, target_node_id)

        else:
            logger.warning(
                "Skipping graph traversal example (could not determine a valid start key)."
            )

        logger.info("--- Graph Examples Complete ---")

        # --- Search Phase (Keep examples) ---
        logger.info("--- Running Search Examples ---")

        # Example 1: BM25 Search
        print("\n" + "-" * 10 + " BM25 Search Example " + "-" * 10)
        bm25_query = "CI pipeline download problem"
        # Example: Filter BM25 by tag added during CRUD update
        bm25_results = search_bm25(db, bm25_query, 0.05, 3, 0, ["updated-demo"])
        log_search_results(bm25_results, "BM25", "bm25_score")

        # Example 2: Semantic Search
        print("\n" + "-" * 10 + " Semantic Search Example " + "-" * 10)
        semantic_query = "How to fix unreliable continuous integration downloads?"
        semantic_query_embedding = get_embedding(semantic_query)
        if semantic_query_embedding:
            semantic_results = search_semantic(db, semantic_query_embedding, 3, 0.75)
            log_search_results(semantic_results, "Semantic", "similarity_score")
        else:
            logger.error("Skipping semantic search example (embedding failed).")

        # Example 3: Hybrid Search (RRF)
        print("\n" + "-" * 10 + " Hybrid Search Example (RRF) " + "-" * 10)
        hybrid_query = "Fixing flaky CI downloads"
        hybrid_results = hybrid_search(db, hybrid_query, 5, 15, 0.01, 0.70)
        log_search_results(hybrid_results, "Hybrid (RRF)", "rrf_score")

    except Exception as e:
        logger.exception(f"Demo failed due to an unexpected error: {e}")
        sys.exit(1)

    finally:
        # --- Cleanup Phase ---
        logger.info("--- Running Cleanup Phase ---")
        if db:
            # Clean up the relationship edge created specifically for this demo
            if demo_edge_key:
                logger.info(
                    f"Attempting to cleanup demo relationship edge: {demo_edge_key}"
                )
                deleted_edge = delete_relationship(db, demo_edge_key)
                if deleted_edge:
                    logger.info(f"Demo edge {demo_edge_key} cleaned up.")
                else:
                    logger.warning(f"Failed to cleanup demo edge {demo_edge_key}.")

            # Clean up the lesson added during the CRUD demo
            if new_key:
                logger.info(f"Attempting cleanup for demo lesson: {new_key}")
                # Use delete_lesson which also cleans its edges (idempotent)
                deleted_lesson = delete_lesson(db, new_key, delete_edges=True)
                if deleted_lesson:
                    logger.info(f"Demo lesson {new_key} cleaned up.")
                else:
                    logger.warning(f"Failed to cleanup demo lesson {new_key}.")
        else:
            logger.warning("Skipping cleanup as DB connection was not established.")

        logger.success("\n" + "=" * 20 + " Demo Finished " + "=" * 20)


if __name__ == "__main__":
    run_demo()
