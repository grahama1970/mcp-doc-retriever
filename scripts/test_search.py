import os
import sys
import pytest

# Ensure src is in path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../src')))

from mcp_doc_retriever import config, searcher

def setup_module(module):
    # Patch DOWNLOAD_BASE_DIR to test_data
    config.DOWNLOAD_BASE_DIR = 'test_data'

def test_perform_search_basic():
    results = searcher.perform_search(
        download_id='test_dl',
        scan_keywords=['apple'],
        selector='p',
        extract_keywords=None
    )
    assert isinstance(results, list)
    assert len(results) >= 1
    assert any('apple' in item.extracted_content for item in results)
    for item in results:
        assert item.selector_matched == 'p'
        assert item.original_url == 'http://example.com/page1'

def test_perform_search_with_extract_keywords():
    results = searcher.perform_search(
        download_id='test_dl',
        scan_keywords=['apple', 'banana'],
        selector='p',
        extract_keywords=['banana']
    )
    # Should only include snippets containing 'banana'
    assert all('banana' in item.extracted_content for item in results)

def test_perform_search_no_keyword_match():
    results = searcher.perform_search(
        download_id='test_dl',
        scan_keywords=['nonexistentkeyword'],
        selector='p',
        extract_keywords=None
    )
    # No files should match scan keywords
    assert results == []

def test_perform_search_no_selector_match():
    results = searcher.perform_search(
        download_id='test_dl',
        scan_keywords=['apple'],
        selector='.nonexistent-class',
        extract_keywords=None
    )
    # No snippets should be extracted
    assert results == []