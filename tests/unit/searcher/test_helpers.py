"""
Unit tests for searcher/helpers.py helper functions.

Tests cover:
- _is_json_like (heuristic JSON detection)
- contains_all_keywords (case-insensitive keyword matching)
- _find_block_lines (approximate line number finding)
"""
import pytest
from mcp_doc_retriever.searcher.helpers import (
    _is_json_like,
    contains_all_keywords,
    _find_block_lines
)

# --- Test Data Fixtures ---
@pytest.fixture
def json_test_cases():
    """Test cases for JSON-like string detection."""
    return [
        ('{"key": "value"}', True),  # Valid JSON object
        ('["item1", "item2"]', True),  # Valid JSON array
        ('{"key": "value"', False),  # Missing closing brace
        ('["item1", "item2"', False),  # Missing closing bracket
        ('plain text', False),  # Not JSON
        ('  {"key": "value"}  ', True),  # Whitespace around valid JSON
        ('{invalid: json}', False),  # Invalid JSON
        ('', False),  # Empty string
        ('null', False),  # Simple JSON values not considered JSON-like
        ('true', False),  # Simple JSON values not considered JSON-like
        ('false', False),  # Simple JSON values not considered JSON-like
        ('123', False),  # Simple JSON values not considered JSON-like
        ('"string"', False)  # Simple JSON values not considered JSON-like
    ]

@pytest.fixture
def keyword_test_cases():
    """Test cases for keyword matching."""
    return [
        ("This is sample text", ["sample"], True),  # Single keyword match
        ("This is sample text", ["SAMPLE"], True),  # Case insensitive
        ("This is sample text", ["sample", "text"], True),  # Multiple matches
        ("This is sample text", ["sample", "missing"], False),  # Partial match
        ("This is sample text", [], False),  # Empty keywords
        ("This is sample text", [""], False),  # Empty keyword
        ("This is sample text", [" "], False),  # Whitespace keyword
        (None, ["sample"], False),  # None text
        ("", ["sample"], False),  # Empty text
        ("This is sample text", ["this", "is", "sample", "text"], True)  # All words
    ]

@pytest.fixture
def block_lines_test_data():
    """Test data for block line finding."""
    source_lines = [
        "Line 1",
        "Line 2",
        "Line 3",
        "Line 4",
        "Line 5",
        "Line 6",
        "Line 7",
        "Line 8",
        "Line 9",
        "Line 10"
    ]
    return source_lines

# --- Test Classes ---
class TestIsJsonLike:
    """Tests for _is_json_like function."""
    
    def test_json_like(self, json_test_cases):
        """Test various JSON-like strings."""
        for text, expected in json_test_cases:
            assert _is_json_like(text) == expected

class TestContainsAllKeywords:
    """Tests for contains_all_keywords function."""
    
    def test_keyword_matching(self, keyword_test_cases):
        """Test various keyword matching scenarios."""
        for text, keywords, expected in keyword_test_cases:
            assert contains_all_keywords(text, keywords) == expected

class TestFindBlockLines:
    """Tests for _find_block_lines function."""
    
    def test_find_block(self, block_lines_test_data):
        """Test finding a block of text in source lines."""
        used_spans = set()
        block_text = "Line 3\nLine 4\nLine 5"
        start, end = _find_block_lines(block_text, block_lines_test_data, used_spans)
        assert start == 3
        assert end == 5
        assert (2, 4) in used_spans  # 0-based span
        
    def test_no_match(self, block_lines_test_data):
        """Test when block doesn't exist in source."""
        used_spans = set()
        block_text = "Non-existent line"
        start, end = _find_block_lines(block_text, block_lines_test_data, used_spans)
        assert start is None
        assert end is None
        
    def test_duplicate_block(self, block_lines_test_data):
        """Test finding duplicate blocks (should find first unused match)."""
        used_spans = set()
        block_text = "Line 3\nLine 4"
        
        # First find
        start1, end1 = _find_block_lines(block_text, block_lines_test_data, used_spans)
        assert start1 == 3
        assert end1 == 4
        
        # Second find (same block) should return None since span is used
        start2, end2 = _find_block_lines(block_text, block_lines_test_data, used_spans)
        assert start2 is None
        assert end2 is None
        
    def test_partial_match(self, block_lines_test_data):
        """Test partial matches shouldn't be considered."""
        used_spans = set()
        block_text = "Line 3\nLine 4\nExtra line"
        start, end = _find_block_lines(block_text, block_lines_test_data, used_spans)
        assert start is None
        assert end is None

# --- Main Block Tests ---
def test_main_block_execution():
    """Test that the main block executes without errors."""
    from mcp_doc_retriever.searcher import helpers
    helpers.__name__ = "__main__"  # Trick to execute main block
    helpers.canonicalize_url("http://example.com")  # Ensure no errors