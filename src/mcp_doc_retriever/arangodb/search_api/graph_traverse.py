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
                search_text=None,  # Not used in graph traversal
                bm25_threshold=None,  # Not used in graph traversal
                top_n=None,  # Not used in graph traversal
                offset=None,  # Not used in graph traversal
                tags=None,  # Not used in graph traversal
                similarity_threshold=None,  # Not used in graph traversal
                initial_k=None,  # Not used in graph traversal
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