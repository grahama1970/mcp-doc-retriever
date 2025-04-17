# main_usage.py
"""
ArangoDB Hybrid Search Example (BM25 + Semantic + Hybrid RRF)

Description:
This script demonstrates setting up an ArangoDB collection and an ArangoSearch view
configured for keyword (BM25), semantic (vector similarity), and hybrid
(BM25 + Semantic with Reciprocal Rank Fusion) search.
It integrates with LiteLLM for embedding generation and utilizes LiteLLM's
caching feature (attempting Redis first, then in-memory fallback) to optimize
API calls. It provides functions for performing all three types of searches.

Third-Party Package Documentation:
- python-arango: https://docs.python-arango.com/en/main/
- Loguru: https://loguru.readthedocs.io/en/stable/
- LiteLLM: https://docs.litellm.ai/docs/embedding/supported_embedding_models
- ArangoDB AQL: https://docs.arangodb.com/stable/aql/
- ArangoSearch Views: https://docs.arangodb.com/stable/arangosearch/views/
- Redis: https://redis.io/docs/about/
- LiteLLM Caching: https://docs.litellm.ai/docs/proxy/caching

Setup:
1. Install required packages: pip install -r requirements.txt
2. Set environment variables for ArangoDB connection (ARANGO_HOST, ARANGO_USER, ARANGO_PASSWORD, ARANGO_DB_NAME).
3. Set the API key for your chosen embedding provider (e.g., OPENAI_API_KEY="sk-...").
4. **Optional:** Set Redis connection variables (REDIS_HOST, REDIS_PORT, REDIS_PASSWORD) to enable Redis caching. If not set or Redis is unavailable, in-memory caching will be used.
5. Ensure the `cache_setup.py` file includes 'embedding' in `supported_call_types` if embedding caching is desired (this modification may be necessary).
6. Run the script: python main_usage.py

Sample Input Data (Structure):
{
    "_key": "<uuid>",
    "timestamp": "<iso_timestamp>",
    "severity": "WARN", "role": "Coder", "task": "T1", "phase": "Dev",
    "problem": "Text describing the problem.",
    "solution": "Text describing the solution.",
    "tags": ["tag1", "tag2"],
    "context": "Additional context.",
    "example": "Code example.",
    "embedding": [0.1, 0.2, ..., -0.05] # Vector embedding
}

Expected Output (Illustrative):
- Log messages indicating setup progress (Cache, DB, Collection, View).
- Log messages indicating sample data insertion (if collection was empty).
- Log messages for BM25 search execution and results (bm25_score).
 insert_sample_if_empty,- Log messages for Semantic search execution and results (similarity_score).
- Log messages for Hybrid search execution and results (rrf_score).
- Success or Error messages at the end.
"""

import sys
import os
from loguru import logger
from dotenv import load_dotenv

# Import setup, search, crud APIs, config, embedding
from mcp_doc_retriever.arangodb.arango_setup import (
    connect_arango,
    ensure_database,
    ensure_collection,
    ensure_search_view,
)

# Make sure to fix the caching logic in cache_setup if needed
from mcp_doc_retriever.arangodb.initialize_litellm_cache import initialize_litellm_cache
from mcp_doc_retriever.arangodb.search_advanced import search_bm25, search_semantic, hybrid_search
from mcp_doc_retriever.arangodb._archive.crud_api_original import (
    add_lesson,
    get_lesson,
    update_lesson,
    delete_lesson,

)  
from mcp_doc_retriever.arangodb.embedding_utils import get_embedding

# Load environment variables from .env file if present
load_dotenv()

# --- Loguru Configuration ---
logger.remove()
logger.add(
    sys.stderr,
    level=os.environ.get("LOG_LEVEL", "INFO").upper(),
    format="{time:YYYY-MM-DD HH:mm:ss} | {level: <7} | {name}:{function}:{line} | {message}",
    backtrace=True,
    diagnose=True,
)


# --- Search Result Logging Helper (Unchanged) ---
def log_search_results(search_data: dict, search_type: str, score_field: str):
    # ... (Code remains the same as previous version) ...
    results = search_data.get("results", [])
    total = search_data.get("total", 0)
    offset = search_data.get("offset", 0)
    limit = search_data.get("limit", len(results))

    logger.info(
        f"--- {search_type} Results (Showing {offset + 1}-{offset + len(results)} of {total} total matches/candidates) ---"
    )
    if not results:
        logger.info("No relevant documents found matching the criteria.")
    else:
        for i, result in enumerate(results, start=1):
            score = result.get(score_field, 0.0)
            doc = result.get("doc", {})
            key = doc.get("_key", "N/A")
            problem = doc.get("problem", "N/A")[:80] + "..."
            tags = ", ".join(doc.get("tags", []))
            other_scores = []
            if "bm25_score" in result and score_field != "bm25_score":
                other_scores.append(f"BM25: {result['bm25_score']:.4f}")
            if "similarity_score" in result and score_field != "similarity_score":
                other_scores.append(f"Sim: {result['similarity_score']:.4f}")
            other_scores_str = f" ({', '.join(other_scores)})" if other_scores else ""
            logger.info(
                f"  {offset + i}. Score: {score:.4f}{other_scores_str} | Key: {key} | Problem: {problem} | Tags: [{tags}]"
            )


# --- Main Demo Execution ---
# In main_usage.py

# ... (Keep imports: sys, os, logger, load_dotenv) ...
# --- Imports from your project ---
from mcp_doc_retriever.arangodb.arango_setup import (
    connect_arango,
    ensure_database,
    ensure_collection,
    ensure_search_view,
    # REMOVED insert_sample_if_empty import
)
from mcp_doc_retriever.arangodb.initialize_litellm_cache import initialize_litellm_cache
from mcp_doc_retriever.arangodb.search_advanced import (
    search_bm25,
    search_semantic,
    hybrid_search,
)
from mcp_doc_retriever.arangodb._archive.crud_api_original import (
    add_lesson,
    get_lesson,
    update_lesson,
    delete_lesson,
)
from mcp_doc_retriever.arangodb.embedding_utils import get_embedding
# --- End Imports ---

# ... (Keep load_dotenv(), logger configuration, log_search_results function) ...


# --- Main Demo Execution (Complete Function) ---
def run_demo():
    """Executes the main demonstration workflow including CRUD examples."""
    logger.info("=" * 20 + " Starting ArangoDB Programmatic Demo " + "=" * 20)
    logger.info("Assumes DB structures (Collections, View, Index) exist.")
    logger.info("Run 'arango_setup.py --seed-file ...' first if data is required.")

    # --- Prerequisites & Initialization ---
    required_key = "OPENAI_API_KEY"  # Assuming OpenAI for embeddings via LiteLLM
    if required_key not in os.environ:
        logger.error(
            f"Required env var {required_key} not set for embedding generation."
        )
        # Decide if the demo can proceed without embeddings or should exit
        # sys.exit(1) # Uncomment to make API key strictly required

    logger.info("--- Initializing LiteLLM Caching ---")
    initialize_litellm_cache()  # Handles Redis or in-memory fallback
    logger.info("--- Caching Initialized ---")

    try:
        # --- ArangoDB Setup Verification Phase ---
        logger.info("--- Verifying ArangoDB Setup Phase ---")
        client = connect_arango()
        if not client:
            # connect_arango logs the specific error
            raise ConnectionError("Failed to connect to ArangoDB instance.")

        db = ensure_database(client)
        if not db:
            # ensure_database logs the specific error
            raise ConnectionError("Failed to connect to or ensure target database.")

        # Ensure collection exists (needed for CRUD/Search)
        collection = ensure_collection(db)
        if not collection:
            # ensure_collection logs the specific error
            raise ConnectionError("Failed to ensure document collection exists.")
        else:
            logger.info(f"Verified document collection '{collection.name}' exists.")

        # Ensure search view exists (needed for BM25/Hybrid)
        if not ensure_search_view(db):
            # ensure_search_view logs the specific error
            logger.warning(
                "Failed to ensure search view exists or update properties. Keyword/Hybrid search might fail."
            )
            # Decide if this is critical - maybe proceed but log warning
        else:
            logger.info(
                f"Verified ArangoSearch view '{os.getenv('ARANGO_SEARCH_VIEW_NAME', 'lessons_view')}' exists/updated."
            )

        # NOTE: We don't call ensure_vector_index or ensure_graph here, as main_usage
        # primarily focuses on CRUD and search API usage, assuming setup is complete.
        # The search functions themselves rely on the index/graph existing.

        # REMOVED: insert_sample_if_empty(collection) call

        logger.info("--- ArangoDB Setup Verification Complete (Structures assumed) ---")

        # --- CRUD Examples ---
        logger.info("--- Running CRUD Examples ---")
        new_lesson_data = {
            "problem": "Docker build fails frequently due to upstream network timeouts during package downloads.",
            "solution": "Implement retries with backoff in Dockerfile RUN commands. Consider using a local package mirror or caching layer.",
            "tags": ["docker", "build", "network", "timeout", "retry", "cache"],
            "severity": "HIGH",
            "role": "Build Engineer",
            "context": "Observed during automated CI builds.",
            # Timestamp and _key will be added by add_lesson if needed
        }
        # Add
        added_meta = add_lesson(
            db, new_lesson_data
        )  # This now generates embedding internally
        new_key = None
        if added_meta and isinstance(added_meta, dict):
            new_key = added_meta.get("_key")
            logger.info(f"CRUD Add: Success, new key = {new_key}")
        else:
            logger.error("CRUD Add: Failed")

        # Get (if add succeeded)
        if new_key:
            retrieved_doc = get_lesson(db, new_key)
            if retrieved_doc and isinstance(retrieved_doc, dict):
                logger.info(
                    f"CRUD Get: Success, retrieved problem: '{retrieved_doc.get('problem', 'N/A')[:60]}...'"
                )
                # Check if embedding was added
                if "embedding" in retrieved_doc and isinstance(
                    retrieved_doc["embedding"], list
                ):
                    logger.info(
                        f"CRUD Get Verify: Embedding field found (dim={len(retrieved_doc['embedding'])})."
                    )
                else:
                    logger.warning(
                        "CRUD Get Verify: Embedding field missing or invalid after add_lesson."
                    )
            else:
                logger.error(f"CRUD Get: Failed to retrieve {new_key}")

        # Update (if add succeeded)
        if new_key:
            update_payload = {
                "severity": "CRITICAL",  # Update severity
                "solution": "Implement retries with exponential backoff in Dockerfile RUN commands. Set up and use a local package mirror (e.g., Artifactory, Nexus).",  # Refine solution
                "tags": [
                    "docker",
                    "build",
                    "network",
                    "timeout",
                    "retry",
                    "mirror",
                    "artifactory",
                ],  # Update tags
            }
            updated_meta = update_lesson(
                db, new_key, update_payload
            )  # This should re-generate embedding if relevant fields change
            if updated_meta and isinstance(updated_meta, dict):
                logger.info(
                    f"CRUD Update: Success, new rev = {updated_meta.get('_rev')}"
                )
                # Verify update
                updated_doc = get_lesson(db, new_key)
                if updated_doc and isinstance(updated_doc, dict):
                    logger.info(
                        f"CRUD Update Verify: Severity = {updated_doc.get('severity')}, Tags = {updated_doc.get('tags')}"
                    )
                    # Optionally re-check embedding if update should have triggered regeneration
            else:
                logger.error(f"CRUD Update: Failed for {new_key}")

        # Delete (if add succeeded)
        if new_key:
            # Assuming delete_lesson takes db and key (add delete_edges=True if needed)
            deleted = delete_lesson(db, new_key)
            if deleted:  # Assuming it returns True/False or similar
                logger.info(f"CRUD Delete: Success for {new_key}")
                # Verify delete
                deleted_doc = get_lesson(db, new_key)
                if not deleted_doc:
                    logger.info(
                        f"CRUD Delete Verify: Document {new_key} confirmed deleted."
                    )
                else:
                    logger.error(
                        f"CRUD Delete Verify: Document {new_key} still found after delete attempt."
                    )
            else:
                logger.error(f"CRUD Delete: Failed for {new_key}")

        logger.info("--- CRUD Examples Complete ---")

        # --- Search Phase ---
        # Assumes data exists from previous seeding or manual insertion
        logger.info("--- Running Search Examples ---")
        logger.info("Note: Search results depend on data previously seeded or added.")

        # Example 1: BM25 Search
        print("\n" + "-" * 10 + " BM25 Search Example " + "-" * 10)
        bm25_query = "shell script json comment issue"
        # Example: Search with a filter tag
        bm25_results = search_bm25(
            db, bm25_query, threshold=0.01, limit=3, offset=0, filter_tags=["shell"]
        )
        log_search_results(bm25_results, "BM25 (tag='shell')", "bm25_score")

        # Example 2: Semantic Search
        print("\n" + "-" * 10 + " Semantic Search Example " + "-" * 10)
        semantic_query = "how to make command line tools handle arguments correctly"
        semantic_query_embedding = get_embedding(
            semantic_query
        )  # Requires API key if not cached
        if semantic_query_embedding:
            semantic_results = search_semantic(
                db, semantic_query_embedding, limit=3, similarity_threshold=0.75
            )
            log_search_results(semantic_results, "Semantic", "similarity_score")
        else:
            logger.error(
                "Failed to get embedding for semantic search query. Skipping example."
            )

        # Example 3: Hybrid Search (RRF)
        print("\n" + "-" * 10 + " Hybrid Search Example (RRF) " + "-" * 10)
        hybrid_query = "debugging tests involving python and shell"
        # Using default thresholds, adjusting top_n and initial_k
        hybrid_results = hybrid_search(
            db, hybrid_query, top_n=5, initial_k=20
        )  # Uses default thresholds from search_api
        log_search_results(hybrid_results, "Hybrid (RRF)", "rrf_score")

        logger.success("\n" + "=" * 20 + " Demo Finished Successfully " + "=" * 20)

    except ConnectionError as ce:
        # Log connection errors specifically if raised by setup checks
        logger.error(f"Demo aborted due to connection/setup issue: {ce}")
        sys.exit(1)
    except Exception as e:
        # Catch any other unexpected errors during CRUD or Search
        logger.exception(f"Demo failed due to an unexpected error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    logger.info(
        "Running main_usage.py demo. Assumes database structure and seed data (if needed) already exist."
    )
    logger.info(
        "Run 'python -m src.mcp_doc_retriever.arangodb.arango_setup --seed-file ...' first if data is required."
    )
    run_demo()