# arango_search_api.py
import uuid
from typing import List, Dict, Any, Optional

from loguru import logger
from arango.database import StandardDatabase
from arango.exceptions import AQLQueryExecuteError, ArangoServerError

# Import config variables and embedding utils
# --- Configuration and Imports ---
from mcp_doc_retriever.arangodb.config import (
    SEARCH_FIELDS,
    ALL_DATA_FIELDS_PREVIEW,
    TEXT_ANALYZER,
    TAG_ANALYZER,
    VIEW_NAME,
    GRAPH_NAME,
)
from mcp_doc_retriever.arangodb.embedding_utils import get_embedding


def hybrid_search(
    db: StandardDatabase,
    query_text: str,
    top_n: int = 10,  # Final number of results to return
    initial_k: int = 20,  # Number of candidates to fetch from each search type
    bm25_threshold: float = 0.01,  # Lower threshold to capture more potential candidates
    similarity_threshold: float = 0.70,  # Lower threshold to capture more potential candidates
    tags: Optional[List[str]] = None,
    rrf_k: int = 60,  # Constant for Reciprocal Rank Fusion (common default)
) -> Dict[str, Any]:
    """
    Performs hybrid search by combining BM25 and Semantic search results
    using Reciprocal Rank Fusion (RRF) for re-ranking.

    Args:
        db: ArangoDB database connection.
        query_text: The user's search query.
        top_n: The final number of ranked results to return.
        initial_k: Number of results to initially fetch from BM25 and Semantic searches.
        bm25_threshold: Minimum BM25 score for initial candidates.
        similarity_threshold: Minimum similarity score for initial candidates.
        tags: Optional list of tags to filter results.
        rrf_k: Constant used in the RRF calculation (default 60).

    Returns:
        A dictionary containing the ranked 'results', 'total' unique documents found,
        'offset' (always 0 for hybrid), and 'limit' (final top_n).
    """
    search_uuid = str(uuid.uuid4())
    with logger.contextualize(action="hybrid_search", search_id=search_uuid):
        try:
            # Validate combined parameters
            validate_search_params(
                query_text,
                bm25_threshold,
                top_n,
                0,
                tags,
                similarity_threshold,
                initial_k,
            )
        except ValueError as e:
            logger.error(f"Invalid Hybrid search parameters: {e}")
            raise

        logger.info(
            f"Executing Hybrid search: text='{query_text}', tags={tags}, k={initial_k}, final_n={top_n}"
        )

        # 1. Get Query Embedding (once)
        query_embedding = get_embedding(query_text)
        if query_embedding is None:
            logger.error(
                "Failed to generate query embedding. Cannot perform hybrid search."
            )
            # Return empty results if embedding fails
            return {"results": [], "total": 0, "offset": 0, "limit": top_n}

        # 2. Fetch Initial Candidates from BM25 and Semantic Search
        # We modify the AQL within the search functions slightly to return full docs
        # needed for merging, or adjust the calls here. For simplicity, let's assume
        # the base functions can return enough info or we adapt them slightly.
        # We'll use simplified calls here, assuming they give us lists of results
        # where each item has {'doc': {...}, 'bm25_score': ...} or {'doc': {...}, 'similarity_score': ...}

        try:
            # Fetch BM25 top K (Note: using internal AQL adjusted to fetch full docs)
            bm25_candidates_raw = _fetch_bm25_candidates(
                db, query_text, bm25_threshold, initial_k, tags, search_uuid
            )
            logger.info(f"Fetched {len(bm25_candidates_raw)} BM25 candidates.")
        except Exception as e:
            logger.warning(
                f"BM25 candidate fetch failed: {e}. Proceeding without BM25 results."
            )
            bm25_candidates_raw = []

        try:
            # Fetch Semantic top K (using pre-computed embedding)
            semantic_candidates_raw = _fetch_semantic_candidates(
                db, query_embedding, similarity_threshold, initial_k, tags, search_uuid
            )
            logger.info(f"Fetched {len(semantic_candidates_raw)} Semantic candidates.")
        except Exception as e:
            logger.warning(
                f"Semantic candidate fetch failed: {e}. Proceeding without Semantic results."
            )
            semantic_candidates_raw = []

        # 3. Combine and De-duplicate Results
        # Store results by document key for easy merging and de-duplication
        combined_results: Dict[str, Dict[str, Any]] = {}

        # Process BM25 results, assigning ranks
        for rank, item in enumerate(bm25_candidates_raw):
            doc = item.get("doc")
            score = item.get("bm25_score")
            key = doc.get("_key") if doc else None
            if not key:
                continue  # Skip if no key

            if key not in combined_results:
                combined_results[key] = {
                    "doc": doc,  # Store the actual document data
                    "bm25_score": score,
                    "bm25_rank": rank + 1,  # 1-based rank
                    "similarity_score": 0.0,  # Initialize
                    "semantic_rank": None,  # Initialize
                }
            else:  # Should theoretically not happen if _fetch returns unique keys, but safe guard
                combined_results[key]["bm25_score"] = max(
                    combined_results[key].get("bm25_score", 0.0), score
                )
                combined_results[key]["bm25_rank"] = min(
                    combined_results[key].get("bm25_rank", rank + 1), rank + 1
                )

        # Process Semantic results, assigning ranks and merging
        for rank, item in enumerate(semantic_candidates_raw):
            doc = item.get("doc")
            score = item.get("similarity_score")
            key = doc.get("_key") if doc else None
            if not key:
                continue

            if key not in combined_results:
                combined_results[key] = {
                    "doc": doc,
                    "bm25_score": 0.0,  # Initialize
                    "bm25_rank": None,  # Initialize
                    "similarity_score": score,
                    "semantic_rank": rank + 1,  # 1-based rank
                }
            else:
                # Update existing entry with semantic info
                combined_results[key]["similarity_score"] = max(
                    combined_results[key].get("similarity_score", 0.0), score
                )
                combined_results[key]["semantic_rank"] = (
                    rank + 1
                )  # Found in semantic list

        # 4. Apply Reciprocal Rank Fusion (RRF)
        ranked_list = []
        for key, data in combined_results.items():
            rrf_score = 0.0
            if data["bm25_rank"] is not None:
                rrf_score += 1.0 / (rrf_k + data["bm25_rank"])
            if data["semantic_rank"] is not None:
                rrf_score += 1.0 / (rrf_k + data["semantic_rank"])

            ranked_list.append(
                {
                    "doc": data["doc"],  # Keep the selected fields from KEEP
                    "bm25_score": data["bm25_score"],
                    "similarity_score": data["similarity_score"],
                    "rrf_score": rrf_score,  # The combined score
                }
            )

        # Sort by RRF score descending
        ranked_list.sort(key=lambda x: x["rrf_score"], reverse=True)

        # 5. Return Final Top N Results
        final_results = ranked_list[:top_n]
        total_unique_found = len(
            combined_results
        )  # Total unique docs found by either method

        logger.success(
            f"Hybrid search successful (ID: {search_uuid}). Returning {len(final_results)} ranked results from {total_unique_found} unique candidates."
        )

        return {
            "results": final_results,
            "total": total_unique_found,
            "offset": 0,  # Offset is not applicable after re-ranking
            "limit": top_n,
        }
