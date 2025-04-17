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

# --- Input Validation ---


def search_semantic(
    db: StandardDatabase,
    query_embedding: List[float],  # Expect pre-computed embedding
    top_n: int = 5,
    similarity_threshold: float = 0.5,  # Lower threshold to catch more semantic matches
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

        # Simpler AQL query that directly gets top N results
        aql = f"""
        LET embedding_results = (
            FOR doc IN {VIEW_NAME}
                FILTER doc.embedding != null
                {tag_filter_clause}
                LET similarity = COSINE_SIMILARITY(doc.embedding, @query_embedding)
                FILTER similarity >= @similarity_threshold
                SORT similarity DESC
                LIMIT @top_n
                RETURN {{
                    doc: KEEP(doc, '_key', '_id', {", ".join([f'"{f}"' for f in ALL_DATA_FIELDS_PREVIEW])}),
                    similarity_score: similarity
                }}
        )

        LET total = (
            FOR doc IN {VIEW_NAME}
                FILTER doc.embedding != null
                {tag_filter_clause}
                LET similarity = COSINE_SIMILARITY(doc.embedding, @query_embedding)
                FILTER similarity >= @similarity_threshold
                COLLECT WITH COUNT INTO total
                RETURN total
        )

        RETURN {{
            results: embedding_results,
            total: total[0]
        }}
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
