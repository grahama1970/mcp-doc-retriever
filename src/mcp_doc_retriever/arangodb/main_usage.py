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
- Log messages for Semantic search execution and results (similarity_score).
- Log messages for Hybrid search execution and results (rrf_score).
- Success or Error messages at the end.
"""

import sys
import os
from loguru import logger
from dotenv import load_dotenv

# Import setup, search, crud APIs, config, embedding
from .arango_setup import (
    connect_arango,
    ensure_database,
    ensure_collection,
    ensure_view,
    insert_sample_if_empty,
)

# Make sure to fix the caching logic in cache_setup if needed
from .cache_setup import initialize_litellm_cache
from .search_api import search_bm25, search_semantic, hybrid_search
from .crud_api import (
    add_lesson,
    get_lesson,
    update_lesson,
    delete_lesson,
)  # <-- Import CRUD
from .embedding_utils import get_embedding

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
def run_demo():
    """Executes the main demonstration workflow including CRUD examples."""
    logger.info("=" * 20 + " Starting ArangoDB Programmatic Demo " + "=" * 20)

    # --- Prerequisites & Initialization ---
    required_key = "OPENAI_API_KEY"
    if required_key not in os.environ:
        logger.error(f"Required env var {required_key} not set.")
        sys.exit(1)

    logger.info("--- Initializing LiteLLM Caching ---")
    initialize_litellm_cache()
    logger.info("--- Caching Initialized ---")

    try:
        # --- ArangoDB Setup ---
        logger.info("--- Running ArangoDB Setup Phase ---")
        client = connect_arango()
        db = ensure_database(client)
        collection = ensure_collection(db)
        ensure_view(db)
        insert_sample_if_empty(collection)
        logger.info("--- ArangoDB Setup Complete ---")

        # --- CRUD Examples ---
        logger.info("--- Running CRUD Examples ---")
        new_lesson_data = {
            "problem": "Docker build fails due to rate limits on base image pull.",
            "solution": "Use a caching proxy like Nexus or Harbor, or authenticate pulls.",
            "tags": ["docker", "ci", "rate-limit", "registry", "nexus"],
            "severity": "MEDIUM",
            "role": "DevOps Engineer",
            # Timestamp and _key will be added by add_lesson if needed
        }
        # Add
        added_meta = add_lesson(db, new_lesson_data)
        new_key = None
        if added_meta:
            new_key = added_meta.get("_key")
            logger.info(f"CRUD Add: Success, new key = {new_key}")
        else:
            logger.error("CRUD Add: Failed")

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
                "tags": ["docker", "ci", "rate-limit", "auth"],
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

        # Delete (if add succeeded)
        if new_key:
            deleted = delete_lesson(db, new_key)
            if deleted:
                logger.info(f"CRUD Delete: Success for {new_key}")
                # Verify delete
                deleted_doc = get_lesson(db, new_key)
                if not deleted_doc:
                    logger.info(
                        f"CRUD Delete Verify: Document {new_key} confirmed deleted."
                    )
            else:
                logger.error(f"CRUD Delete: Failed for {new_key}")

        logger.info("--- CRUD Examples Complete ---")

        # --- Search Phase (Keep examples) ---
        logger.info("--- Running Search Examples ---")

        # Example 1: BM25 Search
        print("\n" + "-" * 10 + " BM25 Search Example " + "-" * 10)
        bm25_query = "CI pipeline download problem"
        bm25_results = search_bm25(db, bm25_query, 0.05, 3, 0, ["ci"])
        log_search_results(bm25_results, "BM25", "bm25_score")

        # Example 2: Semantic Search
        print("\n" + "-" * 10 + " Semantic Search Example " + "-" * 10)
        semantic_query = "How to fix unreliable continuous integration downloads?"
        semantic_query_embedding = get_embedding(semantic_query)
        if semantic_query_embedding:
            semantic_results = search_semantic(db, semantic_query_embedding, 3, 0.75)
            log_search_results(semantic_results, "Semantic", "similarity_score")
        else:
            logger.error("Skipping semantic search example.")

        # Example 3: Hybrid Search (RRF)
        print("\n" + "-" * 10 + " Hybrid Search Example (RRF) " + "-" * 10)
        hybrid_query = "Fixing flaky CI downloads"
        hybrid_results = hybrid_search(db, hybrid_query, 5, 15, 0.01, 0.70)
        log_search_results(hybrid_results, "Hybrid (RRF)", "rrf_score")

        logger.success("\n" + "=" * 20 + " Demo Finished Successfully " + "=" * 20)

    except Exception as e:
        logger.exception(f"Demo failed due to an unexpected error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    run_demo()