"""
Unit tests for downloader/helpers.py module
"""
import pytest
from pathlib import Path
from mcp_doc_retriever.downloader import helpers

@pytest.fixture
def tmp_download_dir(tmp_path):
    """Fixture providing a temporary download directory"""
    return tmp_path / "downloads"

def test_url_to_local_path_basic(tmp_download_dir):
    """Test basic URL to path conversion"""
    url = "http://example.com/file.html"
    result = helpers.url_to_local_path(tmp_download_dir, url)
    assert str(result).startswith(str(tmp_download_dir))
    assert "example.com/file.html" in str(result)

def test_url_to_local_path_with_path(tmp_download_dir):
    """Test URL with path components"""
    url = "https://example.com/path/to/resource"
    result = helpers.url_to_local_path(tmp_download_dir, url)
    assert "example.com/path/to/resource/index.html" in str(result)

def test_url_to_local_path_ipv6(tmp_download_dir):
    """Test IPv6 URL handling"""
    url = "http://[::1]:8080/special"
    result = helpers.url_to_local_path(tmp_download_dir, url)
    assert "_::_1__8080/special/index.html" in str(result)

def test_url_to_local_path_special_chars(tmp_download_dir):
    """Test URL with special characters"""
    url = 'http://example.com/a<b>c:d/e"f/g?h/i*j.html'
    result = helpers.url_to_local_path(tmp_download_dir, url)
    assert "example.com/a_b_c_d_e_f_g_h_i_j.html" in str(result)

def test_url_to_local_path_traversal_attempt(tmp_download_dir):
    """Test path traversal attempt"""
    with pytest.raises(ValueError, match="Constructed path escapes base directory"):
        helpers.url_to_local_path(tmp_download_dir, "http://example.com/../../etc/passwd")

def test_url_to_local_path_long_filename(tmp_download_dir):
    """Test long filename handling"""
    long_name = "a" * 300 + ".html"
    url = f"http://example.com/{long_name}"
    result = helpers.url_to_local_path(tmp_download_dir, url)
    assert len(result.name) <= 200  # Should be truncated
    assert result.name.endswith(".html")

def test_url_to_local_path_query_params(tmp_download_dir):
    """Test URL with query parameters"""
    url = "http://example.com/search?q=test&page=1"
    result = helpers.url_to_local_path(tmp_download_dir, url)
    assert "example.com/search/index.html" in str(result)

def test_url_to_local_path_fragment(tmp_download_dir):
    """Test URL with fragment"""
    url = "http://example.com/page#section"
    result = helpers.url_to_local_path(tmp_download_dir, url)
    assert "example.com/page/index.html" in str(result)

def test_url_to_local_path_empty(tmp_download_dir):
    """Test empty URL"""
    with pytest.raises(ValueError):
        helpers.url_to_local_path(tmp_download_dir, "")

def test_url_to_local_path_invalid(tmp_download_dir):
    """Test invalid URL format"""
    with pytest.raises(ValueError):
        helpers.url_to_local_path(tmp_download_dir, "not-a-url")
