# File: src/mcp_doc_retriever/searcher/basic_extractor.py

"""
Module: basic_extractor.py

Description:
Handles the basic snippet extraction logic used by the primary `perform_search`.
Supports extracting the <title> tag content or the filtered full text content of a file.
Requires BeautifulSoup4 for title extraction.
"""

import logging
from pathlib import Path
from typing import List, Optional
import re  # Keep re as it might be used indirectly or in future expansion
import sys  # <-- CHANGE: Added import sys

# --- Corrected Imports ---
# These imports should now work correctly after fixes in helpers.py and utils.py
try:
    from mcp_doc_retriever.searcher.helpers import extract_text_from_html_content
    from mcp_doc_retriever.searcher.helpers import read_file_with_fallback
    from mcp_doc_retriever.utils import contains_all_keywords

    IMPORTS_OK = True
except ImportError as e:
    # Raise a more informative error if essential helpers/utils are missing during normal import
    # This might happen if the package isn't installed correctly
    logging.error(
        f"Failed to import core dependencies: {e}. Ensure package is installed.",
        exc_info=True,
    )
    IMPORTS_OK = False

    # Define dummy functions to avoid crashing later if used conditionally, but log error
    def read_file_with_fallback(p: Path) -> Optional[str]:
        logging.error("read_file_with_fallback not available")
        return None

    def extract_text_from_html_content(c: str) -> Optional[str]:
        logging.error("extract_text_from_html_content not available")
        return None

    def contains_all_keywords(t: Optional[str], k: List[str]) -> bool:
        logging.error("contains_all_keywords not available")
        return False


# Use try-except for bs4 import to make it optional at runtime if needed
try:
    from bs4 import BeautifulSoup

    BS4_AVAILABLE = True
except ImportError:
    BS4_AVAILABLE = False
    # No need for dummy class here if usage is guarded by BS4_AVAILABLE flag
# --- End Corrected Imports ---

logger = logging.getLogger(__name__)


# --- Primary Function for this Module ---


def extract_text_with_selector(
    file_path: Path,
    selector: str,
    extract_keywords: Optional[List[str]] = None,
) -> List[str]:
    """
    Extracts text snippets from a file based on a CSS selector (basic implementation).
    Handles 'title' specifically, otherwise extracts full filtered text content using helpers.

    Args:
        file_path: Path object of the file to process.
        selector: CSS selector (supports 'title', otherwise extracts full filtered text).
        extract_keywords: Optional keywords to filter the extracted snippets (all must match).

    Returns:
        List of extracted text snippets matching the criteria.
    """
    if not IMPORTS_OK:
        logger.error("Cannot perform extraction, core dependencies failed to import.")
        return []

    content = read_file_with_fallback(file_path)  # Uses helper from helpers.py
    if content is None:
        # read_file_with_fallback already logs warnings/errors
        logger.debug(f"Basic extraction skipped: Cannot read file: {file_path}")
        return []

    extracted_snippets: List[str] = []
    selector_lower = selector.strip().lower() if selector else ""

    # Special handling for 'title' selector
    if selector_lower == "title":
        if not BS4_AVAILABLE:
            logger.error(
                "BeautifulSoup4 package required for 'title' selector, but not installed. Skipping title extraction."
            )
            return []  # Cannot extract title without BS4
        try:
            # Use lxml if available for performance, fallback to html.parser
            parser_to_use = "html.parser"  # Default
            try:
                # Check if lxml is importable without importing globally
                import lxml

                parser_to_use = "lxml"
            except ImportError:
                pass  # Stick with html.parser

            soup = BeautifulSoup(content, parser_to_use)

            title_tag = soup.title
            if (
                title_tag and title_tag.string
            ):  # Check if tag exists and has string content
                # .string gets the text if there's only one string child
                # strip() removes leading/trailing whitespace
                title_text = title_tag.string.strip()
                if title_text:  # Only add non-empty titles
                    extracted_snippets.append(title_text)
            # If no title tag or empty title, extracted_snippets remains empty, which is correct.
        except Exception as e:
            # Catch potential errors during BS4 parsing or title access
            logger.warning(
                f"Error extracting <title> tag from {file_path}: {e}",
                exc_info=False,  # Set True for debug stack trace
            )
            # Treat as no title found, return empty for this specific case
            return []
    else:
        # Fallback for other/empty selectors: Extract full text content using helper
        # The helper function now includes title text due to the fix.
        try:
            # Calls function from searcher/helpers.py
            full_text = extract_text_from_html_content(content)
            if full_text:
                # Append the single block of extracted text
                extracted_snippets.append(full_text)
            # If helper returns None (e.g., error or empty doc), snippets list remains empty.
        except Exception as e:
            # Error during the helper function execution itself
            logger.error(
                f"Error during full text extraction helper call for {file_path}: {e}",
                exc_info=True,
            )
            return []  # Return empty if full text extraction fails

    # --- Filter results by extract_keywords if provided ---
    if extract_keywords:
        # Normalize keywords: lowercase and remove empty/None
        lowered_extract_keywords = [
            kw.lower() for kw in extract_keywords if kw and kw.strip()
        ]
        # If no valid keywords remain after filtering, no filtering is needed.
        if not lowered_extract_keywords:
            return extracted_snippets

        # Filter the snippets based on the keywords using the utility function
        try:
            # Calls function from utils.py
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
            # Catch potential errors during the filtering process itself
            logger.error(
                f"Error during keyword filtering for {file_path.name}: {e}",
                exc_info=True,
            )
            return []  # Return empty list on filtering error
    else:
        # No filtering keywords were provided, return all extracted snippets
        return extracted_snippets


# --- Standalone Execution / Example ---
if __name__ == "__main__":
    import tempfile
    import shutil

    # Ensure sys and Path are available if running standalone
    from pathlib import Path

    # Setup logging for the example
    logging.basicConfig(level=logging.INFO, format="[%(levelname)-8s] %(message)s")
    logger.info("Running basic_extractor standalone test...")

    # --- CHANGE: Setup sys.path and Imports ONLY for standalone execution ---
    # This allows running `python src/mcp_doc_retriever/searcher/basic_extractor.py` directly
    # Find the project root directory (adjust based on your structure)
    # Assuming this script is in src/mcp_doc_retriever/searcher/basic_extractor.py
    project_root_dir = Path(__file__).resolve().parent.parent.parent.parent
    src_dir = project_root_dir / "src"  # Path to the 'src' directory
    if str(src_dir) not in sys.path:
        sys.path.insert(0, str(src_dir))
        print(f"DEBUG: Added {src_dir} to sys.path for standalone execution.")

    # --- CHANGE: Re-import using absolute path for test context with error handling ---
    try:
        # Re-import functions using the package path now that sys.path is set
        from mcp_doc_retriever.searcher.helpers import (
            read_file_with_fallback as standalone_read_file,  # Use aliases to avoid name clashes if needed
            extract_text_from_html_content as standalone_extract_html,
        )
        from mcp_doc_retriever.utils import (
            contains_all_keywords as standalone_contains_kw,
        )

        STANDALONE_IMPORTS_OK = True
        print("DEBUG: Successfully imported helpers/utils for standalone test.")
        # Now, ensure the main function uses these potentially re-imported functions if necessary,
        # or rely on the global imports if they succeeded. Safest is to potentially pass them?
        # For simplicity here, we assume the global IMPORTS_OK reflects if these work.
        if not IMPORTS_OK:
            print(
                "ERROR: Initial global imports failed, standalone test may not reflect real behavior."
            )
            # Re-assign globals to the standalone versions IF the initial import failed
            read_file_with_fallback = standalone_read_file
            extract_text_from_html_content = standalone_extract_html
            contains_all_keywords = standalone_contains_kw
            IMPORTS_OK = True  # Assume standalone imports worked for test execution

    except ImportError as e:
        print(
            f"ERROR: Could not import utils/helpers for standalone test via package path: {e}"
        )
        print(
            "Ensure package is installed (`uv pip install -e .`) or PYTHONPATH is set correctly."
        )
        STANDALONE_IMPORTS_OK = False
        # Exit if essential functions can't be imported for the test
        sys.exit(1)

    # Create a temporary directory for test files
    with tempfile.TemporaryDirectory() as tmpdir:
        base_dir = Path(tmpdir)
        logger.info(f"Test files will be created in temporary directory: {base_dir}")

        # Define test file contents
        html_content_with_title = (
            "<!DOCTYPE html>\n"
            "<html><head><meta charset='utf-8'><title> My Test Title </title></head>\n"
            "<body>\n"
            "  <p>Some paragraph text with <strong>keyword</strong>.</p>\n"
            "  <script>console.log('ignored');</script>\n"
            "  <p>Another paragraph.</p>\n"
            "</body></html>"
        )
        html_content_no_title = "<html><body><p>Just paragraph text here, another keyword.</p></body></html>"

        # Write test files
        html_with_title_path = base_dir / "title_test.html"
        html_with_title_path.write_text(html_content_with_title, encoding="utf-8")

        html_no_title_path = base_dir / "no_title_test.html"
        html_no_title_path.write_text(html_content_no_title, encoding="utf-8")

        logger.info(
            f"Test files created: {html_with_title_path.name}, {html_no_title_path.name}"
        )

        # --- Test Cases ---
        all_passed = True  # Track overall success

        print("\n--- Test Case 1: Selector 'title' (File with Title) ---")
        try:
            results1 = extract_text_with_selector(html_with_title_path, "title")
            print(f"Found {len(results1)} snippets: {results1}")
            assert len(results1) == 1, "T1 Fail: Expected 1 title snippet"
            assert results1[0] == "My Test Title", "T1 Fail: Incorrect title content"
            print("Test Case 1 PASSED")
        except Exception as e:
            print(f"Test 1 FAILED: {e}")
            all_passed = False

        print(
            "\n--- Test Case 2: Selector 'title', Keywords ['test'] (File with Title) ---"
        )
        try:
            results2 = extract_text_with_selector(
                html_with_title_path, "title", extract_keywords=["test"]
            )
            print(f"Found {len(results2)} snippets: {results2}")
            assert len(results2) == 1, "T2 Fail: Expected 1 filtered title snippet"
            assert results2[0] == "My Test Title", (
                "T2 Fail: Incorrect filtered title content"
            )
            print("Test Case 2 PASSED")
        except Exception as e:
            print(f"Test 2 FAILED: {e}")
            all_passed = False

        print(
            "\n--- Test Case 3: Selector 'title', Keywords ['nothere'] (File with Title) ---"
        )
        try:
            results3 = extract_text_with_selector(
                html_with_title_path, "title", extract_keywords=["nothere"]
            )
            print(f"Found {len(results3)} snippets: {results3}")
            assert len(results3) == 0, (
                "T3 Fail: Expected 0 snippets when keyword doesn't match title"
            )
            print("Test Case 3 PASSED")
        except Exception as e:
            print(f"Test 3 FAILED: {e}")
            all_passed = False

        print(
            "\n--- Test Case 4: Selector 'p' (uses full text extraction) (File with Title) ---"
        )
        # This now tests if the fixed helper includes the title in the full text
        try:
            results4 = extract_text_with_selector(
                html_with_title_path, "p"
            )  # 'p' triggers fallback to full text
            print(f"Found {len(results4)} snippets (full text): {results4}")
            assert len(results4) == 1, "T4 Fail: Expected 1 full text snippet"
            # --- CHANGE: Assertion now expected to PASS ---
            assert "My Test Title" in results4[0], (
                "T4 Fail: Title missing in extracted full text"
            )
            assert "Some paragraph text with keyword" in results4[0], (
                "T4 Fail: Paragraph text missing"
            )
            assert "Another paragraph" in results4[0], (
                "T4 Fail: Second paragraph missing"
            )
            assert "console.log" not in results4[0], (
                "T4 Fail: Script content was not removed"
            )
            print("Test Case 4 PASSED")
        except Exception as e:
            print(f"Test 4 FAILED: {e}")
            all_passed = False

        print(
            "\n--- Test Case 5: Selector '' (full text), Keywords ['paragraph', 'keyword'] (File with Title) ---"
        )
        try:
            results5 = extract_text_with_selector(
                html_with_title_path, "", extract_keywords=["paragraph", "keyword"]
            )
            print(f"Found {len(results5)} snippets: {results5}")
            assert len(results5) == 1, "T5 Fail: Expected 1 filtered full text snippet"
            assert "paragraph" in results5[0].lower(), (
                "T5 Fail: 'paragraph' keyword missing"
            )
            assert "keyword" in results5[0].lower(), (
                "T5 Fail: 'keyword' keyword missing"
            )
            print("Test Case 5 PASSED")
        except Exception as e:
            print(f"Test 5 FAILED: {e}")
            all_passed = False

        print(
            "\n--- Test Case 6: Selector '' (full text), Keywords ['another'] (File with Title) ---"
        )
        try:
            # This keyword only appears in the second paragraph of the full text
            results6 = extract_text_with_selector(
                html_with_title_path, "", extract_keywords=["another"]
            )
            print(f"Found {len(results6)} snippets: {results6}")
            assert len(results6) == 1, (
                "T6 Fail: Expected 1 snippet containing 'another'"
            )
            assert "Another paragraph" in results6[0], "T6 Fail: Content mismatch"
            print("Test Case 6 PASSED")
        except Exception as e:
            print(f"Test 6 FAILED: {e}")
            all_passed = False

        print("\n--- Test Case 7: No Title file, Selector 'title' ---")
        try:
            results7 = extract_text_with_selector(html_no_title_path, "title")
            print(f"Found {len(results7)} snippets: {results7}")
            assert len(results7) == 0, (
                "T7 Fail: Expected 0 snippets when file has no title tag"
            )
            print("Test Case 7 PASSED")
        except Exception as e:
            print(f"Test 7 FAILED: {e}")
            all_passed = False

        print(
            "\n--- Test Case 8: No Title file, Selector '' (full text), Keywords ['another keyword'] ---"
        )
        try:
            results8 = extract_text_with_selector(
                html_no_title_path, "", extract_keywords=["another keyword"]
            )
            print(f"Found {len(results8)} snippets: {results8}")
            assert len(results8) == 1, (
                "T8 Fail: Expected 1 snippet from file without title"
            )
            assert "Just paragraph text here, another keyword." in results8[0], (
                "T8 Fail: Content mismatch"
            )
            print("Test Case 8 PASSED")
        except Exception as e:
            print(f"Test 8 FAILED: {e}")
            all_passed = False

    # Final Verdict
    print("\n------------------------------------")
    if all_passed:
        print("✓ All basic_extractor tests passed successfully.") # Added print statement
        logger.info("✓ All basic_extractor tests passed successfully.")
    else:
        print("✗ Some basic_extractor tests failed.") # Added print statement
        logger.error("✗ Some basic_extractor tests failed.")
        # --- CHANGE: Exit with error code if tests fail ---
        sys.exit(1)

    logger.info("Basic extractor tests finished.")
