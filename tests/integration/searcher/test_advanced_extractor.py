# tests/integration/searcher/test_advanced_extractor.py
"""
Integration tests for the advanced_extractor module.
"""
import pytest
from pathlib import Path
from typing import List, Optional # Added Optional

# Import the extractor and AdvancedSearchOptions from our module
from mcp_doc_retriever.searcher.advanced_extractor import (
    extract_advanced_snippets_with_options,
    AdvancedSearchOptions,
)
# Import models needed for validation
from mcp_doc_retriever.models import SearchResultItem, ContentBlock

# Define paths to test data files (assumed to be in a folder named "test_data")
TEST_DATA_DIR = Path("test_data")
HTML_FILE = TEST_DATA_DIR / "mixed_content.html"
MD_FILE = TEST_DATA_DIR / "mixed_content.md"
EMPTY_FILE = TEST_DATA_DIR / "empty_file.txt"  # Will be created temporarily


@pytest.fixture(scope="module", autouse=True)
def ensure_test_files_exist():
    """Ensure the necessary test files exist."""
    if not TEST_DATA_DIR.exists():
        pytest.fail(f"Test data directory not found: {TEST_DATA_DIR}")
    if not HTML_FILE.exists():
        pytest.fail(f"Test HTML file not found: {HTML_FILE}")
    if not MD_FILE.exists():
        pytest.fail(f"Test Markdown file not found: {MD_FILE}")
    # Create a temporary empty file for the no-match scenario
    EMPTY_FILE.touch()
    yield
    if EMPTY_FILE.exists():
        EMPTY_FILE.unlink()


def _validate_results(results: List[SearchResultItem], expected_count: int, check_context: Optional[str] = None):
    """Helper function to validate common properties of search results."""
    assert isinstance(results, list)
    assert len(results) == expected_count, f"Expected {expected_count} results, got {len(results)}"
    for item in results:
        assert isinstance(item, SearchResultItem)
        # Check standard model fields
        assert isinstance(item.content_block, ContentBlock)
        assert isinstance(item.content_block.content, str)
        assert len(item.content_block.content.strip()) > 0
        assert item.search_context in ["code", "json", "text"]

        # If a specific context is expected for all results in this list
        if check_context:
             assert item.search_context == check_context, f"Expected context '{check_context}', got '{item.search_context}'"

        # Check context-specific fields
        if item.search_context == "code":
            assert isinstance(item.code_block_score, float)
            assert item.code_block_score >= 0.0 # Score should be non-negative
            assert item.json_match_info is None
        elif item.search_context == "json":
            assert item.code_block_score is None
            # json_match_info can be None if only raw keyword matched invalid JSON
            assert isinstance(item.json_match_info, dict) or item.json_match_info is None
        else: # text
             assert item.code_block_score is None
             assert item.json_match_info is None


def test_extract_from_html():
    """Test extracting code and JSON snippets from an HTML file separately."""
    # Test finding JS block
    js_options = AdvancedSearchOptions(scan_keywords=["greet", "function"], search_json=False)
    js_results = extract_advanced_snippets_with_options(
        HTML_FILE,
        scan_keywords=js_options.scan_keywords,
        search_code_blocks=js_options.search_code_blocks,
        search_json=js_options.search_json,
    )
    _validate_results(js_results, 1, check_context="code")
    js_result = js_results[0]
    assert js_result.content_block.language == "javascript"
    assert "function greet()" in js_result.content_block.content
    assert "console.log(\"Hello, world!\");" in js_result.content_block.content
    assert js_result.code_block_score is not None and js_result.code_block_score > 0

    # Test finding JSON block
    json_options = AdvancedSearchOptions(scan_keywords=["key", "value"], search_code_blocks=False, json_match_mode="keys")
    json_results = extract_advanced_snippets_with_options(
        HTML_FILE,
        scan_keywords=json_options.scan_keywords,
        search_code_blocks=json_options.search_code_blocks,
        search_json=json_options.search_json,
        json_match_mode=json_options.json_match_mode,
    )
    _validate_results(json_results, 1, check_context="json")
    json_result = json_results[0]
    assert json_result.content_block.language == "json"
    assert '"key": "value"' in json_result.content_block.content
    assert '"number": 42' in json_result.content_block.content
    assert json_result.json_match_info is not None
    assert json_result.json_match_info.get("score", 0) > 0 # Should have matched key


def test_extract_from_markdown():
    """Test extracting code and JSON snippets from a Markdown file separately."""
    # Test finding Python block
    py_options = AdvancedSearchOptions(scan_keywords=["def", "hello_world"], search_json=False)
    py_results = extract_advanced_snippets_with_options(
        MD_FILE,
        scan_keywords=py_options.scan_keywords,
        search_code_blocks=py_options.search_code_blocks,
        search_json=py_options.search_json,
    )
    _validate_results(py_results, 1, check_context="code")
    py_result = py_results[0]
    assert py_result.content_block.language == "python"
    assert "def hello_world():" in py_result.content_block.content
    assert "print(\"Hello, world!\")" in py_result.content_block.content
    assert py_result.code_block_score is not None and py_result.code_block_score > 0

    # Test finding JSON block
    json_options = AdvancedSearchOptions(scan_keywords=["key", "number"], search_code_blocks=False, json_match_mode="keys")
    json_results = extract_advanced_snippets_with_options(
        MD_FILE,
        scan_keywords=json_options.scan_keywords,
        search_code_blocks=json_options.search_code_blocks,
        search_json=json_options.search_json,
        json_match_mode=json_options.json_match_mode,
    )
    _validate_results(json_results, 1, check_context="json")
    json_result = json_results[0]
    assert json_result.content_block.language == "json" # Check MD extractor assigns lang
    assert '"key": "value"' in json_result.content_block.content
    assert '"number": 42' in json_result.content_block.content
    assert json_result.json_match_info is not None
    assert json_result.json_match_info.get("score", 0) > 0 # Should have matched key


def test_extract_no_matches():
    """Test extraction from a file with no relevant code or JSON blocks."""
    options = AdvancedSearchOptions(
        scan_keywords=["nonexistent_keyword_123"],
        extract_keywords=[],
        search_code_blocks=True,
        search_json=True,
    )
    # Test against HTML
    results_html = extract_advanced_snippets_with_options(
        HTML_FILE, scan_keywords=options.scan_keywords
    )
    assert isinstance(results_html, list)
    assert len(results_html) == 0
    # Test against MD
    results_md = extract_advanced_snippets_with_options(
        MD_FILE, scan_keywords=options.scan_keywords
    )
    assert isinstance(results_md, list)
    assert len(results_md) == 0
    # Test against Empty
    results_empty = extract_advanced_snippets_with_options(
        EMPTY_FILE, scan_keywords=options.scan_keywords
    )
    assert isinstance(results_empty, list)
    assert len(results_empty) == 0


def test_extract_only_code():
    """Test extracting only code snippets."""
    # Test HTML (JS code)
    html_options = AdvancedSearchOptions(
        scan_keywords=["function", "greet"], # Keywords present in JS block
        search_code_blocks=True,
        search_json=False, # Disable JSON
    )
    html_results = extract_advanced_snippets_with_options(
        HTML_FILE,
        scan_keywords=html_options.scan_keywords,
        search_code_blocks=html_options.search_code_blocks,
        search_json=html_options.search_json,
    )
    _validate_results(html_results, 1, check_context="code")
    assert html_results[0].content_block.language == "javascript"

    # Test MD (Python code)
    md_options = AdvancedSearchOptions(
        scan_keywords=["def", "hello_world"], # Keywords present in Python block
        search_code_blocks=True,
        search_json=False, # Disable JSON
    )
    md_results = extract_advanced_snippets_with_options(
        MD_FILE,
        scan_keywords=md_options.scan_keywords,
        search_code_blocks=md_options.search_code_blocks,
        search_json=md_options.search_json,
    )
    _validate_results(md_results, 1, check_context="code")
    assert md_results[0].content_block.language == "python"


def test_extract_only_json():
    """Test extracting only JSON snippets."""
    options = AdvancedSearchOptions(
        scan_keywords=["key", "number"], # Keywords present in JSON blocks
        search_code_blocks=False, # Disable Code
        search_json=True,
        json_match_mode="keys" # Match these keywords as keys
    )
    # Test HTML
    html_results = extract_advanced_snippets_with_options(
        HTML_FILE,
        scan_keywords=options.scan_keywords,
        search_code_blocks=options.search_code_blocks,
        search_json=options.search_json,
        json_match_mode=options.json_match_mode,
    )
    _validate_results(html_results, 1, check_context="json")
    assert html_results[0].content_block.language == "json"
    assert html_results[0].json_match_info is not None
    assert html_results[0].json_match_info.get("score", 0) > 0

    # Test MD
    md_results = extract_advanced_snippets_with_options(
        MD_FILE,
        scan_keywords=options.scan_keywords,
        search_code_blocks=options.search_code_blocks,
        search_json=options.search_json,
        json_match_mode=options.json_match_mode,
    )
    _validate_results(md_results, 1, check_context="json") # Expecting JSON context now
    assert md_results[0].content_block.language == "json"
    assert md_results[0].json_match_info is not None
    assert md_results[0].json_match_info.get("score", 0) > 0


# -------------------------------------------------------
# Summary reporting when running tests as a script.
# (Keep this for potential direct execution/debugging)
# -------------------------------------------------------
if __name__ == "__main__":
    # This block allows running the tests directly with `python tests/integration/...`
    # It's useful for quick debugging but pytest is the standard runner.
    import sys
    test_names = [
        "test_extract_from_html",
        "test_extract_from_markdown",
        "test_extract_no_matches",
        "test_extract_only_code",
        "test_extract_only_json",
    ]
    summary_all_passed = True
    results_summary = {}
    print("\n--- Running Integration Tests for Advanced Extractor ---")
    # Ensure files exist before running tests
    try:
        # Manually call the setup part of the fixture
        if not TEST_DATA_DIR.exists(): raise FileNotFoundError(f"Test data directory not found: {TEST_DATA_DIR}")
        if not HTML_FILE.exists(): raise FileNotFoundError(f"Test HTML file not found: {HTML_FILE}")
        if not MD_FILE.exists(): raise FileNotFoundError(f"Test Markdown file not found: {MD_FILE}")
        EMPTY_FILE.touch()
        print("Test files checked/created.")
    except Exception as setup_e:
         print(f"ERROR during test setup: {setup_e}")
         summary_all_passed = False

    if summary_all_passed: # Only run tests if setup passed
        for name in test_names:
            print(f"\nRunning {name}...")
            try:
                globals()[name]()
                results_summary[name] = "PASS"
                print(f"{name}: PASS")
            except AssertionError as e:
                results_summary[name] = f"FAIL: {e}"
                summary_all_passed = False
                print(f"{name}: FAIL - {e}")
            except Exception as e:
                results_summary[name] = f"ERROR: {e}"
                summary_all_passed = False
                print(f"{name}: ERROR - {e}")

    print("\n-------------------- TEST SUMMARY --------------------")
    for name in test_names:
        result = results_summary.get(name, "SKIPPED (Setup Failed)")
        print(f"- {name}: {result}")
    print("------------------------------------------------------")
    if summary_all_passed:
        print("✓ All Advanced Extractor tests passed!")
    else:
        print("✗ Some Advanced Extractor tests FAILED or were SKIPPED.")
    print("------------------------------------------------------")
    # Clean up the empty file if it exists (manual cleanup for direct run)
    if EMPTY_FILE.exists():
        try:
            EMPTY_FILE.unlink()
            print("Cleaned up temporary empty file.")
        except Exception as cleanup_e:
            print(f"Warning: Could not clean up empty file: {cleanup_e}")
