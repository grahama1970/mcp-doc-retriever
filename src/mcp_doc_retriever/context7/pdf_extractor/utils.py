"""
Utility functions for the PDF extraction pipeline.

This module provides shared helper functions for text normalization, TikToken
encoding, and table ID assignment, used across the PDF extraction scripts.

Dependencies:
- cleantext: For text cleaning.
- tiktoken: For token counting.
- fuzzywuzzy: For string similarity.
- loguru: For logging.
"""

import re
from typing import List, Dict, Optional
from loguru import logger
import tiktoken
from cleantext import clean
from fuzzywuzzy import fuzz

from .config import TIKTOKEN_ENCODING_MODEL


def _get_encoder() -> tiktoken.Encoding:
    """Gets the TikToken encoder."""
    try:
        return tiktoken.encoding_for_model(TIKTOKEN_ENCODING_MODEL)
    except Exception as e:
        logger.error(f"Error loading TikToken encoder: {e}")
        raise


def _normalize_text(text: Optional[str]) -> Optional[str]:
    """Cleans and normalizes a string."""
    if not text:
        return None
    try:
        cleaned = clean(
            text,
            no_line_breaks=True,
            no_html=True,
            normalize_whitespace=True,
            no_urls=True,
            no_emails=True,
            no_punct=False,
        )
        cleaned = re.sub(r"[●•◦▪️]", "- ", cleaned).strip()
        cleaned = re.sub(r"\\alpha|α", "alpha", cleaned, flags=re.IGNORECASE)
        if cleaned and not re.search(r"[.!?]$", cleaned):
            cleaned += "."
        return cleaned if cleaned else None
    except Exception as e:
        logger.warning(f"Error normalizing text: {e}")
        return text


def _assign_unique_table_ids(tables: List[Dict], source: str) -> List[Dict]:
    """Assigns unique IDs like 'camelot_p2_t0'."""
    page_counters: Dict[int, int] = {}
    for table in tables:
        page = table.get("page", 0)
        if page not in page_counters:
            page_counters[page] = 0
        table_index = page_counters[page]
        table["table_id"] = f"{source}_p{page}_t{table_index}"
        page_counters[page] += 1
    return tables


def usage_function():
    """
    Demonstrates utility functions.

    Returns:
        dict: Example results of utility functions.
    """
    sample_text = "Hello   world!\nVisit https://example.com"
    sample_tables = [{"page": 1}, {"page": 1}]
    normalized = _normalize_text(sample_text)
    tables_with_ids = _assign_unique_table_ids(sample_tables, "test")
    return {
        "normalized_text": normalized,
        "table_ids": [t["table_id"] for t in tables_with_ids],
    }


if __name__ == "__main__":
    # Test basic functionality
    result = usage_function()
    print("Utility Function Results:")
    print(result)
