"""
Module: tests/unit/downloader/test_helpers.py

Description:
Unit tests for url_to_local_path function in downloader/helpers.py.
Tests cover basic URL conversion, edge cases, security validation, and error handling.
"""
import sys
from pathlib import Path

# Add project root to Python path
project_root = str(Path(__file__).resolve().parent.parent.parent.parent)
if project_root not in sys.path:
    sys.path.insert(0, project_root)

import pytest
from src.mcp_doc_retriever.downloader.helpers import url_to_local_path

def test_basic_url_conversion(tmp_path):
    """Test basic URL formats convert correctly"""
    result = url_to_local_path(tmp_path, "http://example.com/path/file.html")
    assert str(result.relative_to(tmp_path)) == "example.com/path/file.html"

def test_edge_cases(tmp_path):
    """Test edge cases like trailing slashes and special chars"""
    result = url_to_local_path(tmp_path, "http://example.com/path/")
    assert str(result.relative_to(tmp_path)) == "example.com/path/index.html"

def test_path_traversal_prevention(tmp_path):
    """Test path traversal attempts are blocked"""
    # Test with a path that would actually escape when resolved
    with pytest.raises(ValueError, match="escapes base directory"):
        url_to_local_path(tmp_path, "http://example.com/../../../../etc/passwd")
    
    # Test with a path containing multiple parent directory references
    with pytest.raises(ValueError, match="escapes base directory"):
        url_to_local_path(tmp_path, "http://example.com/a/../../b/../../../etc/passwd")

    # Test with encoded traversal attempts
    with pytest.raises(ValueError, match="escapes base directory"):
        url_to_local_path(tmp_path, "http://example.com/%2e%2e/%2e%2e/etc/passwd")

def test_long_path_handling(tmp_path):
    """Test long paths and filenames are handled correctly"""
    long_name = "x" * 100 + ".html"
    result = url_to_local_path(tmp_path, f"http://example.com/{long_name}")
    assert len(result.name) == len(long_name)

def test_error_cases(tmp_path):
    """Test error cases raise appropriate exceptions"""
    # Empty URL should fail hostname validation
    with pytest.raises(ValueError, match="hostname"):
        url_to_local_path(tmp_path, "")
        
    # Invalid URL format should fail parsing
    with pytest.raises(ValueError):
        url_to_local_path(tmp_path, "not_a_url")
        
    # Missing hostname should fail
    with pytest.raises(ValueError, match="hostname"):
        url_to_local_path(tmp_path, "http://")

    # Malformed URL should fail
    with pytest.raises(ValueError):
        url_to_local_path(tmp_path, "http:///missing/hostname")

def test_relative_base_dir(tmp_path):
    """Test that relative base_dir is resolved correctly"""
    rel_path = tmp_path / "relative_dir"
    rel_path.mkdir()
    result = url_to_local_path(rel_path, "http://example.com/file.html")
    assert str(result.relative_to(rel_path)) == "example.com/file.html"

if __name__ == "__main__":
    import sys
    pytest.main(sys.argv)