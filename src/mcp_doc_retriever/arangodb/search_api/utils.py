from typing import Optional, List
from loguru import logger

def validate_search_params(
    search_text: Optional[str],
    bm25_threshold: Optional[float],
    top_n: Optional[int],  # Can be None for graph traversal
    offset: Optional[int],  # Can be None for graph traversal
    tags: Optional[List[str]] = None,
    similarity_threshold: Optional[float] = None,
    initial_k: Optional[int] = None,
    # Graph traversal params
    min_depth: Optional[int] = None,
    max_depth: Optional[int] = None,
    direction: Optional[str] = None,
    limit: Optional[int] = None,
) -> None:
    """Validates common search parameters before executing a query."""
    logger.debug("Validating search parameters...")

    # Determine operation type
    is_bm25_or_hybrid = bm25_threshold is not None or initial_k is not None
    is_graph_traversal = (
        min_depth is not None or max_depth is not None or direction is not None
    )
    is_standard_search = not is_graph_traversal

    # Validate standard search parameters
    if is_standard_search:
        if top_n is not None and top_n < 1:
            raise ValueError(f"Top N limit must be at least 1, got {top_n}")
        if offset is not None and offset < 0:
            raise ValueError(f"Offset cannot be negative, got {offset}")
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
        # Check search text for BM25/Hybrid
        if is_bm25_or_hybrid and (search_text is None or not search_text.strip()):
            raise ValueError("Search text cannot be empty for BM25 or Hybrid search.")

    # Validate graph traversal parameters
    if is_graph_traversal:
        if min_depth is not None and min_depth < 0:
            raise ValueError(f"Minimum depth cannot be negative, got {min_depth}")
        if max_depth is not None and max_depth < min_depth:
            raise ValueError(
                f"Maximum depth must be >= minimum depth, got max_depth={max_depth}, min_depth={min_depth}"
            )
        if direction is not None and direction.upper() not in [
            "OUTBOUND",
            "INBOUND",
            "ANY",
        ]:
            raise ValueError(
                f"Direction must be one of: OUTBOUND, INBOUND, ANY, got {direction}"
            )
        if limit is not None and limit < 1:
            raise ValueError(f"Limit must be at least 1, got {limit}")

    # Common validations for all operations
    if tags and not isinstance(tags, list):
        raise ValueError(f"Tags must be a list of strings, got {type(tags)}")
    if tags and not all(isinstance(tag, str) and tag.strip() for tag in tags):
        raise ValueError("All tags must be non-empty strings")

    logger.debug("Search parameters validated successfully.")
