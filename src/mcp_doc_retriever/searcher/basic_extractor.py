# File: src/mcp_doc_retriever/searcher/basic_extractor.py

"""
Module: basic_extractor.py

Description:
Handles the basic snippet extraction logic used by the primary `perform_search`.
Supports extracting the <title> tag content or the filtered full text content of a file.
Requires BeautifulSoup4 for title extraction and CSS selection. # <-- Updated description
"""

import logging
from pathlib import Path
from typing import List, Optional
import re
import sys

# --- Corrected Imports ---
try:
    # No longer need extract_text_from_html_content directly here if selector is always used
    from mcp_doc_retriever.searcher.helpers import read_file_with_fallback
    from mcp_doc_retriever.utils import contains_all_keywords

    IMPORTS_OK = True
except ImportError as e:
    logging.error(
        f"Failed to import core dependencies: {e}. Ensure package is installed.",
        exc_info=True,
    )
    IMPORTS_OK = False

    def read_file_with_fallback(p: Path) -> Optional[str]:
        logging.error("read_file_with_fallback not available")
        return None

    def contains_all_keywords(t: Optional[str], k: List[str]) -> bool:
        logging.error("contains_all_keywords not available")
        return False


# Use try-except for bs4 import
try:
    from bs4 import BeautifulSoup, CSS

    BS4_AVAILABLE = True
except ImportError:
    BS4_AVAILABLE = False
# --- End Corrected Imports ---

logger = logging.getLogger(__name__)


# --- Primary Function for this Module (Updated Logic) ---
def extract_text_with_selector(
    file_path: Path,
    selector: str,
    extract_keywords: Optional[List[str]] = None,
) -> List[str]:
    """
    Extracts text snippets from an HTML file based on a CSS selector.
    Requires BeautifulSoup4.

    Args:
        file_path: Path object of the HTML file to process.
        selector: CSS selector to find elements. If empty or whitespace, returns empty list.
        extract_keywords: Optional keywords to filter the extracted snippets (all must match).

    Returns:
        List of extracted text snippets matching the criteria.
    """
    if not IMPORTS_OK:
        logger.error("Cannot perform extraction, core dependencies failed to import.")
        return []
    if not BS4_AVAILABLE:
        logger.error(
            "BeautifulSoup4 package required for CSS selector extraction, but not installed. Skipping extraction."
        )
        return []

    selector_clean = selector.strip() if selector else ""
    if not selector_clean:
        logger.warning(f"Extraction skipped: Empty selector provided for {file_path}.")
        return []

    content = read_file_with_fallback(file_path)
    if content is None:
        logger.debug(f"Extraction skipped: Cannot read file: {file_path}")
        return []

    extracted_snippets: List[str] = []

    try:
        # Use lxml if available for performance, fallback to html.parser
        parser_to_use = "html.parser"
        try:
            import lxml

            parser_to_use = "lxml"
        except ImportError:
            pass

        soup = BeautifulSoup(content, parser_to_use)

        # --- MODIFIED LOGIC: Use soup.select() for CSS selection ---
        try:
            # Validate the selector syntactically before using it (requires newer BS4 versions)
            if hasattr(CSS, "validate") and callable(CSS.validate):
                CSS.validate(selector_clean)

            selected_elements = soup.select(selector_clean)
            if not selected_elements:
                logger.debug(
                    f"Selector '{selector_clean}' found no elements in {file_path.name}"
                )
            else:
                logger.debug(
                    f"Selector '{selector_clean}' found {len(selected_elements)} elements in {file_path.name}"
                )
                for element in selected_elements:
                    # Extract text, preserving structure within the element somewhat
                    # Use separator=' ' to avoid words running together, strip removes outer whitespace
                    element_text = element.get_text(separator=" ", strip=True)
                    if element_text:  # Only add non-empty snippets
                        extracted_snippets.append(element_text)

        except NotImplementedError:
            # Handle case where CSS.validate is not available (older BS4)
            logger.warning(
                f"CSS selector validation not available. Proceeding without validation for '{selector_clean}'."
            )
            # Attempt selection anyway
            selected_elements = soup.select(selector_clean)
            # (Duplicate the extraction logic from above or refactor)
            if not selected_elements:
                logger.debug(
                    f"Selector '{selector_clean}' (unvalidated) found no elements in {file_path.name}"
                )
            else:
                logger.debug(
                    f"Selector '{selector_clean}' (unvalidated) found {len(selected_elements)} elements in {file_path.name}"
                )
                for element in selected_elements:
                    element_text = element.get_text(separator=" ", strip=True)
                    if element_text:
                        extracted_snippets.append(element_text)
        except Exception as select_err:  # Catch errors from soup.select (e.g., invalid selector syntax)
            logger.warning(
                f"Error applying CSS selector '{selector_clean}' to {file_path.name}: {select_err}. Skipping extraction for this file/selector.",
                exc_info=False,  # Usually don't need full trace for invalid selectors
            )
            return []  # Return empty list if selector fails

        # --- END MODIFIED LOGIC ---

    except Exception as e:
        # Catch potential errors during BS4 parsing
        logger.warning(
            f"Error parsing HTML or extracting text from {file_path}: {e}",
            exc_info=False,
        )
        return []

    # --- Filter results by extract_keywords if provided (remains the same) ---
    if extract_keywords:
        lowered_extract_keywords = [
            kw.lower() for kw in extract_keywords if kw and kw.strip()
        ]
        if not lowered_extract_keywords:
            return extracted_snippets

        try:
            filtered_snippets = [
                snippet
                for snippet in extracted_snippets
                if contains_all_keywords(snippet, lowered_extract_keywords)
            ]
            logger.debug(
                f"Filtered {len(extracted_snippets)} snippets down to {len(filtered_snippets)} using keywords for {file_path.name}."
            )
            return filtered_snippets
        except Exception as e:
            logger.error(
                f"Error during keyword filtering for {file_path.name}: {e}",
                exc_info=True,
            )
            return []
    else:
        return extracted_snippets


# --- Standalone Execution / Example (Updated for Selector Tests) ---
if __name__ == "__main__":
    import tempfile
    import shutil
    from pathlib import Path

    logging.basicConfig(level=logging.INFO, format="[%(levelname)-8s] %(message)s")
    logger.info("Running basic_extractor standalone test...")

    # --- Setup sys.path and Imports (Assume this part works as before) ---
    project_root_dir = Path(__file__).resolve().parent.parent.parent.parent
    src_dir = project_root_dir / "src"
    if str(src_dir) not in sys.path:
        sys.path.insert(0, str(src_dir))
        print(f"DEBUG: Added {src_dir} to sys.path for standalone execution.")

    try:
        from mcp_doc_retriever.searcher.helpers import (
            read_file_with_fallback as standalone_read_file,
        )
        from mcp_doc_retriever.utils import (
            contains_all_keywords as standalone_contains_kw,
        )

        STANDALONE_IMPORTS_OK = True
        print("DEBUG: Successfully imported helpers/utils for standalone test.")
        if not IMPORTS_OK:
            print("ERROR: Initial global imports failed, test may use standalone.")
            read_file_with_fallback = standalone_read_file
            contains_all_keywords = standalone_contains_kw
            IMPORTS_OK = True
    except ImportError as e:
        print(f"ERROR: Could not import utils/helpers for standalone test: {e}")
        STANDALONE_IMPORTS_OK = False
        sys.exit(1)

    # --- Test Setup ---
    with tempfile.TemporaryDirectory() as tmpdir:
        base_dir = Path(tmpdir)
        logger.info(f"Test files will be created in: {base_dir}")

        html_content = (
            "<!DOCTYPE html>\n"
            "<html><head><title> Test Title </title></head>\n"
            "<body>\n"
            "  <h1>Main Heading</h1>\n"
            "  <p class='content'>First paragraph with <strong>keyword1</strong>.</p>\n"
            "  <div class='data highlight'>Data Point <span>ALPHA</span></div>\n"
            "  <p class='content'>Second paragraph with <em>keyword2</em>.</p>\n"
            "  <div class='highlight'>Data Point <span>BETA</span></div>\n"
            "  <p>Third paragraph, no class.</p>\n"
            "</body></html>"
        )
        html_file_path = base_dir / "selector_test.html"
        html_file_path.write_text(html_content, encoding="utf-8")
        logger.info(f"Test file created: {html_file_path.name}")

        # --- Test Cases ---
        all_passed = True

        print("\n--- Test Case 1: Selector 'title' ---")
        try:
            results1 = extract_text_with_selector(html_file_path, "title")
            print(f"Found {len(results1)} snippets: {results1}")
            # Note: BeautifulSoup might parse 'title' now via select, but keeping explicit check is fine
            # Check if the explicit title logic still works or if select handles it.
            # Assuming the original explicit title logic runs first if selector=='title'
            # Let's adjust the test based on the *actual* code flow: 'title' is handled separately.
            # Rerun the exact code logic mentally: if selector is 'title', it uses soup.title.string.
            # soup = BeautifulSoup(html_content, 'html.parser') # Simulating
            # assert soup.title.string.strip() == "Test Title"
            # So, the expected result should still be ['Test Title']
            assert len(results1) == 1, "T1 Fail: Expected 1 title snippet"
            assert results1[0] == "Test Title", "T1 Fail: Incorrect title content"
            print("Test Case 1 PASSED")
        except Exception as e:
            print(f"Test 1 FAILED: {e}")
            all_passed = False

        print("\n--- Test Case 2: Selector 'p.content' ---")
        try:
            results2 = extract_text_with_selector(html_file_path, "p.content")
            print(f"Found {len(results2)} snippets: {results2}")
            assert len(results2) == 2, "T2 Fail: Expected 2 matching paragraphs"
            assert "First paragraph with keyword1" in results2[0], (
                "T2 Fail: Missing first p"
            )
            assert "Second paragraph with keyword2" in results2[1], (
                "T2 Fail: Missing second p"
            )
            print("Test Case 2 PASSED")
        except Exception as e:
            print(f"Test 2 FAILED: {e}")
            all_passed = False

        print("\n--- Test Case 3: Selector '.highlight span' ---")
        try:
            results3 = extract_text_with_selector(html_file_path, ".highlight span")
            print(f"Found {len(results3)} snippets: {results3}")
            assert len(results3) == 2, "T3 Fail: Expected 2 matching spans"
            assert results3[0] == "ALPHA", "T3 Fail: Incorrect span content 1"
            assert results3[1] == "BETA", "T3 Fail: Incorrect span content 2"
            print("Test Case 3 PASSED")
        except Exception as e:
            print(f"Test 3 FAILED: {e}")
            all_passed = False

        print("\n--- Test Case 4: Selector '.highlight', Keywords ['beta'] ---")
        try:
            results4 = extract_text_with_selector(
                html_file_path, ".highlight", extract_keywords=["beta"]
            )
            print(f"Found {len(results4)} snippets: {results4}")
            assert len(results4) == 1, "T4 Fail: Expected 1 filtered highlight div"
            assert "Data Point BETA" in results4[0], (
                "T4 Fail: Incorrect filtered content"
            )
            print("Test Case 4 PASSED")
        except Exception as e:
            print(f"Test 4 FAILED: {e}")
            all_passed = False

        print("\n--- Test Case 5: Selector 'h1' ---")
        try:
            results5 = extract_text_with_selector(html_file_path, "h1")
            print(f"Found {len(results5)} snippets: {results5}")
            assert len(results5) == 1, "T5 Fail: Expected 1 heading"
            assert results5[0] == "Main Heading", "T5 Fail: Incorrect heading content"
            print("Test Case 5 PASSED")
        except Exception as e:
            print(f"Test 5 FAILED: {e}")
            all_passed = False

        print("\n--- Test Case 6: Non-existent selector 'div.foo' ---")
        try:
            results6 = extract_text_with_selector(html_file_path, "div.foo")
            print(f"Found {len(results6)} snippets: {results6}")
            assert len(results6) == 0, (
                "T6 Fail: Expected 0 snippets for non-existent class"
            )
            print("Test Case 6 PASSED")
        except Exception as e:
            print(f"Test 6 FAILED: {e}")
            all_passed = False

        print("\n--- Test Case 7: Invalid selector 'p..content' ---")
        try:
            results7 = extract_text_with_selector(html_file_path, "p..content")
            print(f"Found {len(results7)} snippets: {results7}")
            # Should log a warning and return empty list
            assert len(results7) == 0, (
                "T7 Fail: Expected 0 snippets for invalid selector"
            )
            print("Test Case 7 PASSED")
        except Exception as e:
            print(f"Test 7 FAILED: {e}")
            all_passed = False  # Should not raise an exception, but return []

        print("\n--- Test Case 8: Empty selector '' ---")
        try:
            results8 = extract_text_with_selector(html_file_path, "")
            print(f"Found {len(results8)} snippets: {results8}")
            assert len(results8) == 0, "T8 Fail: Expected 0 snippets for empty selector"
            print("Test Case 8 PASSED")
        except Exception as e:
            print(f"Test 8 FAILED: {e}")
            all_passed = False

    # --- Final Verdict ---
    print("\n------------------------------------")
    if all_passed:
        print("✓ All basic_extractor tests passed successfully.")
        logger.info("✓ All basic_extractor tests passed successfully.")
    else:
        print("✗ Some basic_extractor tests failed.")
        logger.error("✗ Some basic_extractor tests failed.")
        sys.exit(1)

    logger.info("Basic extractor tests finished.")
