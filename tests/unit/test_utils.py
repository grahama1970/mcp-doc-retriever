"""
Unit tests for src/mcp_doc_retriever/utils.py

Covers:
- canonicalize_url
- generate_download_id
- is_url_private_or_internal
- contains_all_keywords
"""

import pytest
from mcp_doc_retriever import utils
from unittest.mock import patch

# --- Fixtures ---

@pytest.fixture
def url_variants():
    return [
        # (input, expected)
        ("http://example.com", "http://example.com/"),
        ("http://example.com:80", "http://example.com/"),
        ("https://example.com:443", "https://example.com/"),
        ("https://example.com:444", "https://example.com:444/"),
        ("//example.com/path", "http://example.com/path"),
        ("example.com", "http://example.com/"),
        ("example.com/test", "http://example.com/test"),
        ("http://example.com/path/", "http://example.com/path"),
        ("http://example.com/path#frag", "http://example.com/path"),
        ("https://EXAMPLE.com:443/Some/Path/", "https://example.com/Some/Path"),
        ("http://example.com:8080", "http://example.com:8080/"),
    ]

@pytest.fixture
def invalid_urls():
    return [
        "http://",  # No host
        "://missing.scheme.com",
        "not a url",
        "http://:80",
        "",
        None,
    ]

@pytest.fixture
def keyword_cases():
    return [
        # (text, keywords, expected)
        ("The quick brown fox", ["quick", "fox"], True),
        ("The quick brown fox", ["Quick", "FOX"], True),  # Case-insensitive
        ("The quick brown fox", ["quick", "dog"], False),
        ("", ["quick"], False),
        (None, ["quick"], False),
        ("The quick brown fox", [], False),
        ("The quick brown fox", [" "], False),
        ("The quick brown fox", ["quick", " "], True),
        ("The quick brown fox", ["quick", ""], True),
        ("The quick brown fox", ["quick", "brown", "fox"], True),
        ("The quick brown fox", ["quick", "brown", "fox", "wolf"], False),
    ]

# --- canonicalize_url ---

@pytest.mark.parametrize("input_url,expected", [
    ("http://example.com", "http://example.com/"),
    ("http://example.com:80", "http://example.com/"),
    ("https://example.com:443", "https://example.com/"),
    ("https://example.com:444", "https://example.com:444/"),
    ("//example.com/path", "http://example.com/path"),
    ("example.com", "http://example.com/"),
    ("example.com/test", "http://example.com/test"),
    ("http://example.com/path/", "http://example.com/path"),
    ("http://example.com/path#frag", "http://example.com/path"),
    ("https://EXAMPLE.com:443/Some/Path/", "https://example.com/Some/Path"),
    ("http://example.com:8080", "http://example.com:8080/"),
])
def test_canonicalize_url_valid(input_url, expected):
    assert utils.canonicalize_url(input_url) == expected

@pytest.mark.parametrize("bad_url", [
    "http://", "://missing.scheme.com", "not a url", "http://:80", "", None
])
def test_canonicalize_url_invalid(bad_url):
    with pytest.raises(ValueError):
        utils.canonicalize_url(bad_url)

# --- generate_download_id ---

def test_generate_download_id_consistency():
    url = "https://example.com/test"
    id1 = utils.generate_download_id(url)
    id2 = utils.generate_download_id(url)
    assert id1 == id2
    # Canonicalization: different forms, same canonical, same ID
    id3 = utils.generate_download_id("https://EXAMPLE.com:443/test")
    assert id1 == id3

def test_generate_download_id_different():
    url1 = "https://example.com/test"
    url2 = "https://example.com/other"
    id1 = utils.generate_download_id(url1)
    id2 = utils.generate_download_id(url2)
    assert id1 != id2

@pytest.mark.parametrize("bad_url", [
    "http://", "://missing.scheme.com", "not a url", "http://:80", "", None
])
def test_generate_download_id_invalid(bad_url):
    with pytest.raises(ValueError):
        utils.generate_download_id(bad_url)

# --- is_url_private_or_internal ---

@pytest.mark.parametrize("url,expected,addrinfo_return", [
    # Public
    ("http://example.com", False, [("family", "type", "proto", "canon", ("93.184.216.34", 80))]),
    ("http://google.com", False, [("family", "type", "proto", "canon", ("142.250.190.78", 80))]),
    # Private IPv4
    ("http://10.0.0.1", True, [("family", "type", "proto", "canon", ("10.0.0.1", 80))]),
    ("http://192.168.1.1", True, [("family", "type", "proto", "canon", ("192.168.1.1", 80))]),
    ("http://172.16.0.1", True, [("family", "type", "proto", "canon", ("172.16.0.1", 80))]),
    ("http://172.31.255.255", True, [("family", "type", "proto", "canon", ("172.31.255.255", 80))]),
    # Loopback
    ("http://localhost", True, [("family", "type", "proto", "canon", ("127.0.0.1", 80))]),
    ("http://127.0.0.1", True, [("family", "type", "proto", "canon", ("127.0.0.1", 80))]),
    # Special hostnames
    ("http://service.internal", True, [("family", "type", "proto", "canon", ("93.184.216.34", 80))]),
    ("http://foo.local", True, [("family", "type", "proto", "canon", ("93.184.216.34", 80))]),
    ("http://bar.test", True, [("family", "type", "proto", "canon", ("93.184.216.34", 80))]),
    ("http://baz.example", True, [("family", "type", "proto", "canon", ("93.184.216.34", 80))]),
    # Unresolvable
    ("http://unresolvable.host", True, Exception("gaierror")),
])
def test_is_url_private_or_internal(url, expected, addrinfo_return):
    # Patch socket.getaddrinfo to control IP resolution
    with patch("socket.getaddrinfo") as mock_getaddrinfo:
        if isinstance(addrinfo_return, Exception):
            mock_getaddrinfo.side_effect = addrinfo_return
        else:
            mock_getaddrinfo.return_value = addrinfo_return
        result = utils.is_url_private_or_internal(url)
        assert result is expected

def test_is_url_private_or_internal_non_string():
    assert utils.is_url_private_or_internal(12345) is True

def test_is_url_private_or_internal_no_hostname():
    # URL with no hostname
    assert utils.is_url_private_or_internal("http:///") is True

# --- contains_all_keywords ---

@pytest.mark.parametrize("text,keywords,expected", [
    ("The quick brown fox", ["quick", "fox"], True),
    ("The quick brown fox", ["Quick", "FOX"], True),
    ("The quick brown fox", ["quick", "dog"], False),
    ("", ["quick"], False),
    (None, ["quick"], False),
    ("The quick brown fox", [], False),
    ("The quick brown fox", [" "], False),
    ("The quick brown fox", ["quick", " "], True),
    ("The quick brown fox", ["quick", ""], True),
    ("The quick brown fox", ["quick", "brown", "fox"], True),
    ("The quick brown fox", ["quick", "brown", "fox", "wolf"], False),
])
def test_contains_all_keywords(text, keywords, expected):
    assert utils.contains_all_keywords(text, keywords) is expected