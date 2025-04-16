# arango_search_api.py
import uuid
from typing import List, Dict, Any, Optional

from loguru import logger
from arango.database import StandardDatabase
from arango.exceptions import AQLQueryExecuteError, ArangoServerError

# Import config variables and embedding utils
from .config import (
    SEARCH_FIELDS,
    ALL_DATA_FIELDS_PREVIEW,
    TEXT_ANALYZER,
    TAG_ANALYZER,
    VIEW_NAME,
    GRAPH_NAME
)
from .embedding_utils import get_embedding

# --- Input Validation ---


def validate_search_params(
    search_text: Optional[str],
    bm25_threshold: Optional[float],
    top_n: int,
    offset: int,  # Offset primarily applies to BM25 direct calls
    tags: Optional[List[str]] = None,
    similarity_threshold: Optional[float] = None,
    # Add params relevant to hybrid if needed (e.g., initial_k)
    initial_k: Optional[int] = None,
) -> None:
    """Validates common search parameters before executing a query."""
    logger.debug("Validating search parameters...")
    if top_n < 1:
        raise ValueError(f"Top N limit must be at least 1, got {top_n}")
    # Offset validation only relevant if offset is used directly
    if offset < 0:
        raise ValueError(f"Offset cannot be negative, got {offset}")
    if tags and not isinstance(tags, list):
        raise ValueError(f"Tags must be a list of strings, got {type(tags)}")
    if tags and not all(isinstance(tag, str) and tag.strip() for tag in tags):
        raise ValueError("All tags must be non-empty strings")
    if similarity_threshold is not None and not 0.0 <= similarity_threshold <= 1.0:
        raise ValueError(
            f"Similarity threshold must be between 0.0 and 1.0, got {similarity_threshold}"
        )
    if bm25_threshold is not None and not 0.0 <= bm25_threshold <= 100.0:
        raise ValueError(f"BM25 threshold must be >= 0.0, got {bm25_threshold}")
    if initial_k is not None and initial_k < 1:
        raise ValueError(
            f"Initial K for hybrid search must be at least 1, got {initial_k}"
        )

    # Check if search_text is provided when required (e.g., for BM25 or Hybrid)
    is_bm25_or_hybrid = bm25_threshold is not None or initial_k is not None
    if is_bm25_or_hybrid and (search_text is None or not search_text.strip()):
        raise ValueError("Search text cannot be empty for BM25 or Hybrid search.")

    logger.debug("Search parameters validated successfully.")


# --- Search Functions (search_bm25 and search_semantic remain largely unchanged) ---


def search_bm25(
    db: StandardDatabase,
    search_text: str,
    bm25_threshold: float = 0.1,
    top_n: int = 5,
    offset: int = 0,
    tags: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """
    Performs BM25 keyword search with pagination, tag filtering, and total count.
    """
    search_uuid = str(uuid.uuid4())
    with logger.contextualize(action="search_bm25", search_id=search_uuid):
        try:
            validate_search_params(
                search_text, bm25_threshold, top_n, offset, tags, None
            )
        except ValueError as e:
            logger.error(f"Invalid BM25 params: {e}")
            raise

        logger.info(
            f"Executing BM25 search: text='{search_text}', tags={tags}, th={bm25_threshold}, top_n={top_n}, offset={offset}"
        )

        search_field_conditions = " OR ".join(
            [
                f'doc.`{field}` IN TOKENS(@search_text, "{TEXT_ANALYZER}")'
                for field in SEARCH_FIELDS
            ]
        )

        tag_filter_clause = ""
        bind_vars = {
            "search_text": search_text,
            "bm25_threshold": bm25_threshold,
            "top_n": top_n,
            "offset": offset,
        }
        if tags:
            tag_conditions = " AND ".join(
                [
                    f'TOKENS(@tag_{i}, "{TAG_ANALYZER}") ALL IN doc.tags'
                    for i in range(len(tags))
                ]
            )
            tag_filter_clause = f"FILTER {tag_conditions}"
            bind_vars.update({f"tag_{i}": tag for i, tag in enumerate(tags)})

        # This AQL fetches the specific page requested (offset, top_n)
        # It also calculates total matching count before pagination.
        aql = f"""
        LET matching_docs = (
            FOR doc IN {VIEW_NAME}
                SEARCH ANALYZER({search_field_conditions}, "{TEXT_ANALYZER}")
                {tag_filter_clause}
                LET score = BM25(doc)
                FILTER score >= @bm25_threshold
                RETURN {{ doc: doc, score: score }} // Return full doc here temporarily for hybrid processing needs
                // RETURN {{ _key: doc._key, score: score }} // More efficient if only key/score needed before hybrid merge
        )
        LET total_count = LENGTH(matching_docs) // Calculate total before sorting/limiting for pagination info

        LET paged_results = (
            FOR item IN matching_docs
                SORT item.score DESC
                LIMIT @offset, @top_n
                RETURN {{
                    // Use KEEP here for final output format consistency
                    doc: KEEP(item.doc, '_key', '_id', {", ".join([f'"{f}"' for f in ALL_DATA_FIELDS_PREVIEW])}),
                    bm25_score: item.score
                }}
        )
        RETURN {{ results: paged_results, total: total_count }}
        """
        logger.debug(f"BM25 AQL (ID: {search_uuid}):\n{aql}")
        try:
            cursor = db.aql.execute(aql, bind_vars=bind_vars)
            data = cursor.next()
            logger.success(
                f"BM25 OK (ID: {search_uuid}). Found {len(data.get('results', []))} (total: {data.get('total', 0)})"
            )
            # Note: The 'results' here contains already processed docs (via KEEP)
            return {
                "results": data.get("results", []),
                "total": data.get("total", 0),
                "offset": offset,
                "limit": top_n,
            }
        except AQLQueryExecuteError as e:
            logger.error(f"BM25 AQL Error (ID: {search_uuid}): {e}\nQuery:\n{aql}")
            raise
        except Exception as e:
            logger.exception(f"BM25 Unexpected Error (ID: {search_uuid}): {e}")
            raise


def search_semantic(
    db: StandardDatabase,
    query_embedding: List[float],  # Expect pre-computed embedding
    top_n: int = 5,
    similarity_threshold: float = 0.75,
    tags: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """
    Performs semantic search using a pre-computed query vector.
    Returns top_n results above the similarity_threshold, filtered by tags.
    """
    search_uuid = str(uuid.uuid4())
    # Note: We don't call get_embedding here; it's assumed to be done by the caller (e.g., hybrid_search)
    with logger.contextualize(action="search_semantic", search_id=search_uuid):
        try:
            # Validate params relevant to semantic search (text is None here as embedding is provided)
            validate_search_params(None, None, top_n, 0, tags, similarity_threshold)
        except ValueError as e:
            logger.error(f"Invalid Semantic search parameters: {e}")
            raise

        logger.info(
            f"Executing Semantic search: tags={tags}, th={similarity_threshold}, top_n={top_n}"
        )

        tag_filter_clause = ""
        bind_vars = {
            "query_embedding": query_embedding,
            "similarity_threshold": similarity_threshold,
            "top_n": top_n,
        }
        if tags:
            tag_conditions = " AND ".join(
                [
                    f'TOKENS(@tag_{i}, "{TAG_ANALYZER}") ALL IN doc.tags'
                    for i in range(len(tags))
                ]
            )
            tag_filter_clause = f"FILTER {tag_conditions}"
            bind_vars.update({f"tag_{i}": tag for i, tag in enumerate(tags)})

        # AQL fetches top N based purely on similarity score
        aql = f"""
        LET matching_docs = (
            FOR doc IN {VIEW_NAME}
                FILTER doc.embedding != null
                {tag_filter_clause}
                LET score = COSINE_SIMILARITY(doc.embedding, @query_embedding)
                FILTER score >= @similarity_threshold
                // Keep score and doc for sorting
                RETURN {{ doc: doc, score: score }}
        )
        LET total_count = LENGTH(matching_docs) // Get total count matching threshold

        LET paged_results = (
            FOR item IN matching_docs
                SORT item.score DESC
                LIMIT @top_n // Apply limit for top N results
                RETURN {{
                    // Use KEEP for final output format consistency
                    doc: KEEP(item.doc, '_key', '_id', {", ".join([f'"{f}"' for f in ALL_DATA_FIELDS_PREVIEW])}),
                    similarity_score: item.score
                }}
        )
        RETURN {{ results: paged_results, total: total_count }}
        """
        logger.debug(f"Semantic AQL (ID: {search_uuid}):\n{aql}")
        try:
            cursor = db.aql.execute(aql, bind_vars=bind_vars)
            data = cursor.next()
            logger.success(
                f"Semantic OK (ID: {search_uuid}). Found {len(data.get('results', []))} (total: {data.get('total', 0)})"
            )
            # Offset is 0 because we fetch top N directly
            return {
                "results": data.get("results", []),
                "total": data.get("total", 0),
                "offset": 0,
                "limit": top_n,
            }
        except AQLQueryExecuteError as e:
            logger.error(f"Semantic AQL Error (ID: {search_uuid}): {e}\nQuery:\n{aql}")
            raise
        except Exception as e:
            logger.exception(f"Semantic Unexpected Error (ID: {search_uuid}): {e}")
            raise


# --- Hybrid Search Function ---


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


# --- Helper functions for hybrid_search to fetch candidates ---
# These encapsulate the AQL needed to get raw candidates (doc + score)


def _fetch_bm25_candidates(
    db: StandardDatabase,
    search_text: str,
    threshold: float,
    limit: int,
    tags: Optional[List[str]],
    parent_search_id: str,
) -> List[Dict]:
    """Internal helper to fetch BM25 candidates for hybrid search."""
    with logger.contextualize(
        action="_fetch_bm25_candidates", parent_search_id=parent_search_id
    ):
        search_field_conditions = " OR ".join(
            [
                f'doc.`{field}` IN TOKENS(@search_text, "{TEXT_ANALYZER}")'
                for field in SEARCH_FIELDS
            ]
        )
        tag_filter_clause = ""
        bind_vars = {
            "search_text": search_text,
            "bm25_threshold": threshold,
            "limit": limit,
        }
        if tags:
            tag_conditions = " AND ".join(
                [
                    f'TOKENS(@tag_{i}, "{TAG_ANALYZER}") ALL IN doc.tags'
                    for i in range(len(tags))
                ]
            )
            tag_filter_clause = f"FILTER {tag_conditions}"
            bind_vars.update({f"tag_{i}": tag for i, tag in enumerate(tags)})

        # Fetch top K based on BM25, returning required fields for merging
        aql = f"""
            FOR doc IN {VIEW_NAME}
                SEARCH ANALYZER({search_field_conditions}, "{TEXT_ANALYZER}")
                {tag_filter_clause}
                LET score = BM25(doc)
                FILTER score >= @bm25_threshold
                SORT score DESC
                LIMIT @limit
                RETURN {{
                    doc: KEEP(doc, '_key', '_id', {", ".join([f'"{f}"' for f in ALL_DATA_FIELDS_PREVIEW])}),
                    bm25_score: score
                }}
        """
        logger.debug(f"Fetching BM25 candidates AQL:\n{aql}")
        cursor = db.aql.execute(aql, bind_vars=bind_vars)
        return list(cursor)


def _fetch_semantic_candidates(
    db: StandardDatabase,
    query_embedding: List[float],
    threshold: float,
    limit: int,
    tags: Optional[List[str]],
    parent_search_id: str,
) -> List[Dict]:
    """Internal helper to fetch Semantic candidates for hybrid search."""
    with logger.contextualize(
        action="_fetch_semantic_candidates", parent_search_id=parent_search_id
    ):
        tag_filter_clause = ""
        bind_vars = {
            "query_embedding": query_embedding,
            "similarity_threshold": threshold,
            "limit": limit,
        }
        if tags:
            tag_conditions = " AND ".join(
                [
                    f'TOKENS(@tag_{i}, "{TAG_ANALYZER}") ALL IN doc.tags'
                    for i in range(len(tags))
                ]
            )
            tag_filter_clause = f"FILTER {tag_conditions}"
            bind_vars.update({f"tag_{i}": tag for i, tag in enumerate(tags)})

        # Fetch top K based on similarity, returning required fields for merging
        aql = f"""
            FOR doc IN {VIEW_NAME}
                FILTER doc.embedding != null
                {tag_filter_clause}
                LET score = COSINE_SIMILARITY(doc.embedding, @query_embedding)
                FILTER score >= @similarity_threshold
                SORT score DESC
                LIMIT @limit
                RETURN {{
                    doc: KEEP(doc, '_key', '_id', {", ".join([f'"{f}"' for f in ALL_DATA_FIELDS_PREVIEW])}),
                    similarity_score: score
                }}
        """
        logger.debug(f"Fetching Semantic candidates AQL:\n{aql}")
        cursor = db.aql.execute(aql, bind_vars=bind_vars)
        return list(cursor)


def graph_traverse(
    db: StandardDatabase,
    start_node_id: str,
    graph_name: str = GRAPH_NAME,  # Use from config by default
    min_depth: int = 1,
    max_depth: int = 1,
    direction: str = "OUTBOUND",
    limit: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """
    Performs graph traversal starting from a given node.

    Args:
        db: ArangoDB database connection.
        start_node_id: The _id of the starting vertex (e.g., "lessons_learned/some_key").
        graph_name: The name of the graph to traverse.
        min_depth: Minimum traversal depth (0 includes start node, 1 starts with neighbors).
        max_depth: Maximum traversal depth.
        direction: Traversal direction ('OUTBOUND', 'INBOUND', 'ANY').
        limit: Optional limit on the total number of paths returned.

    Returns:
        A list of dictionaries, each representing a path found, containing
        'vertex', 'edge', and 'path' objects. Returns empty list on error.
    """
    traverse_uuid = str(uuid.uuid4())
    with logger.contextualize(
        action="graph_traverse", traverse_id=traverse_uuid, start_node=start_node_id
    ):
        # Validate inputs
        try:
            validate_search_params(
                search_text=None,
                bm25_threshold=None,
                top_n=None,
                offset=None,
                tags=None,
                similarity_threshold=None,
                initial_k=None,
                start_node_id=start_node_id,
                min_depth=min_depth,
                max_depth=max_depth,
                direction=direction,
                limit=limit,
            )
            # Ensure direction is uppercase for AQL f-string insertion
            direction = direction.upper()
        except ValueError as e:
            logger.error(f"Invalid graph traversal parameters: {e}")
            raise  # Re-raise validation error

        logger.info(
            f"Executing Graph Traversal: start='{start_node_id}', graph='{graph_name}', depth={min_depth}..{max_depth}, dir={direction}, limit={limit}"
        )

        # Build AQL query
        # IMPORTANT: graph_name and direction MUST use f-strings, not bind vars.
        # start_node_id, depths, and limit CAN use bind vars.
        limit_clause = "LIMIT @limit" if limit is not None else ""
        bind_vars = {
            "start_node": start_node_id,
            "min_depth": min_depth,
            "max_depth": max_depth,
        }
        if limit is not None:
            bind_vars["limit"] = limit

        aql = f"""
        FOR v, e, p IN @min_depth..@max_depth {direction} @start_node GRAPH "{graph_name}"
            {limit_clause}
            RETURN {{
                vertex: v,
                edge: e,
                path: p
            }}
        """
        # Note: The above RETURN includes full vertex/edge documents.
        # For large graphs, consider returning only specific attributes using KEEP or projection.
        # Example: RETURN {{ vertex: KEEP(v, '_key', 'name'), edge: e._key, path_len: LENGTH(p.edges) }}

        logger.debug(f"Graph Traversal AQL (ID: {traverse_uuid}):\n{aql}")

        try:
            cursor = db.aql.execute(aql, bind_vars=bind_vars)
            results = list(cursor)
            logger.success(
                f"Graph traversal successful (ID: {traverse_uuid}). Found {len(results)} paths."
            )
            return results
        except AQLQueryExecuteError as e:
            logger.error(
                f"Graph traversal AQL query failed (ID: {traverse_uuid}): {e}\nQuery:\n{aql}"
            )
            raise  # Re-raise AQL execution errors
        except Exception as e:
            logger.exception(
                f"Unexpected error during graph traversal (ID: {traverse_uuid}): {e}"
            )
            raise  # Re-raise other unexpected errors