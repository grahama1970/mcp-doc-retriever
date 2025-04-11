"""
Module: basic_extractor.py

Description:
Handles the basic snippet extraction logic used by the primary `perform_search`.
Currently supports extracting the <title> tag content or extracting the filtered
full text content of a file. Requires BeautifulSoup4 for title extraction.
"""

import logging
from pathlib import Path
from typing import List, Optional

# Use relative imports for helpers and utils within the package
from .helpers import read_file_with_fallback
from .searcher import contains_all_keywords, extract_text_from_html_content

logger = logging.getLogger(__name__)


def extract_text_with_selector(
    file_path: Path,  # Expect Path object
    selector: str,
    extract_keywords: Optional[List[str]] = None,
) -> List[str]:
    """
    Extracts text snippets from a file based on a CSS selector (basic implementation).
    Currently handles 'title' specifically, otherwise extracts full filtered text.

    Args:
        file_path: Path object of the file to process.
        selector: CSS selector (supports 'title', otherwise ignored for full text).
        extract_keywords: Optional keywords to filter the extracted snippets (all must match).

    Returns:
        List of extracted text snippets matching the criteria.
    """
    content = read_file_with_fallback(file_path)
    if content is None:
        logger.warning(f"Basic extraction failed: Cannot read file: {file_path}")
        return []

    extracted_snippets: List[str] = []
    selector_lower = selector.strip().lower() if selector else ""

    # Special handling for 'title' selector requires BeautifulSoup
    if selector_lower == "title":
        try:
            # Dynamically import BeautifulSoup only when needed
            from bs4 import BeautifulSoup

            # Consider adding 'lxml' for performance if installed: BeautifulSoup(content, "lxml")
            soup = BeautifulSoup(content, "html.parser")
            title_tag = soup.title
            if title_tag and title_tag.string:
                title_text = title_tag.string.strip()
                if title_text:  # Only add non-empty titles
                    extracted_snippets.append(title_text)
        except ImportError:
            logger.error(
                "BeautifulSoup4 package is required for 'title' selector extraction but is not installed. Skipping title extraction."
            )
            # Cannot extract title, return empty list for this selector type
            return []
        except Exception as e:
            logger.warning(
                f"Error extracting <title> tag from {file_path}: {e}", exc_info=True
            )
            # Treat as no title found
            return []  # Return empty if title extraction fails
    else:
        # Fallback for other/empty selectors: Extract full text content
        # Requires utils.extract_text_from_html_content(content: str) -> Optional[str]
        try:
            full_text = extract_text_from_html_content(content)
            if full_text:
                extracted_snippets.append(full_text)
        except Exception as e:
            logger.error(
                f"Error during full text extraction for {file_path}: {e}", exc_info=True
            )
            # Return empty if full text extraction fails
            return []

    # --- Filter results by extract_keywords if provided ---
    if extract_keywords:
        # Prepare filter keywords
        lowered_extract_keywords = [
            kw.lower() for kw in extract_keywords if kw and kw.strip()
        ]
        if not lowered_extract_keywords:  # No valid keywords to filter by
            return extracted_snippets  # Return all found snippets

        # Filter the snippets based on the keywords
        # Requires utils.contains_all_keywords(text: Optional[str], keywords: List[str]) -> bool
        filtered_snippets = [
            snippet
            for snippet in extracted_snippets
            if contains_all_keywords(
                snippet, lowered_extract_keywords
            )  # Check all filter keywords present
        ]
        logger.debug(
            f"Filtered {len(extracted_snippets)} basic snippets down to {len(filtered_snippets)} using extract_keywords."
        )
        return filtered_snippets
    else:
        # No filtering needed
        return extracted_snippets


# --- Standalone Execution / Example ---
if __name__ == "__main__":
    import tempfile

    # Basic Mocking for utils if needed
    def mock_extract_text(content: str) -> str:
        import re

        return re.sub("<[^>]+>", " ", content)

    def mock_contains_all(text: str, keywords: List[str]) -> bool:
        if not text:
            return False
        t_lower = text.lower()
        return all(k.lower() in t_lower for k in keywords)

    # Inject mocks or ensure utils are importable
    try:
        from mcp_doc_retriever.utils import contains_all_keywords, extract_text_from_html_content

        logger.info("Using actual utils for basic extractor test.")
    except ImportError:
        logger.warning("Mocking utils for basic extractor test.")
        contains_all_keywords = mock_contains_all
        extract_text_from_html_content = mock_extract_text

    logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")
    logger.info("Running basic_extractor standalone test...")

    with tempfile.TemporaryDirectory() as tmpdir:
        base_dir = Path(tmpdir)

        # Create test files
        html_with_title = base_dir / "title_test.html"
        html_with_title.write_text(
            "<html><head><title> My Test Title </title></head><body><p>Some paragraph text with keyword.</p></body></html>",
            encoding="utf-8",
        )

        html_no_title = base_dir / "no_title_test.html"
        html_no_title.write_text(
            "<html><body><p>Just paragraph text here, another keyword.</p></body></html>",
            encoding="utf-8",
        )

        logger.info(f"Test files created in {base_dir}")

        # Test Case 1: Extract Title
        print("\n--- Test Case 1: Selector 'title' ---")
        results1 = extract_text_with_selector(html_with_title, "title")
        print(f"Found {len(results1)} snippets: {results1}")
        assert len(results1) == 1
        assert results1[0] == "My Test Title"
        print("Test Case 1 PASSED")

        # Test Case 2: Extract Title with keyword filter (match)
        print("\n--- Test Case 2: Selector 'title', Keywords ['test'] ---")
        results2 = extract_text_with_selector(
            html_with_title, "title", extract_keywords=["test"]
        )
        print(f"Found {len(results2)} snippets: {results2}")
        assert len(results2) == 1
        assert results2[0] == "My Test Title"
        print("Test Case 2 PASSED")

        # Test Case 3: Extract Title with keyword filter (no match)
        print("\n--- Test Case 3: Selector 'title', Keywords ['nothere'] ---")
        results3 = extract_text_with_selector(
            html_with_title, "title", extract_keywords=["nothere"]
        )
        print(f"Found {len(results3)} snippets: {results3}")
        assert len(results3) == 0
        print("Test Case 3 PASSED")

        # Test Case 4: Extract Full Text (selector ignored)
        print("\n--- Test Case 4: Selector 'p' (ignored, full text) ---")
        results4 = extract_text_with_selector(
            html_with_title, "p"
        )  # Selector 'p' falls back to full text
        print(f"Found {len(results4)} snippets: {results4}")
        assert len(results4) == 1
        # Check if extracted text contains expected content (whitespace normalized)
        assert "My Test Title" in results4[0]  # Title should be in extracted text
        assert "Some paragraph text" in results4[0]
        print("Test Case 4 PASSED")

        # Test Case 5: Extract Full Text with keyword filter
        print("\n--- Test Case 5: Selector '', Keywords ['paragraph', 'keyword'] ---")
        results5 = extract_text_with_selector(
            html_with_title, "", extract_keywords=["paragraph", "keyword"]
        )
        print(f"Found {len(results5)} snippets: {results5}")
        assert len(results5) == 1
        assert "paragraph" in results5[0].lower()
        assert "keyword" in results5[0].lower()
        print("Test Case 5 PASSED")

        # Test Case 6: Extract Full Text with keyword filter (no match)
        print("\n--- Test Case 6: Selector '', Keywords ['another'] ---")
        results6 = extract_text_with_selector(
            html_with_title, "", extract_keywords=["another"]
        )
        print(f"Found {len(results6)} snippets: {results6}")
        assert len(results6) == 0
        print("Test Case 6 PASSED")

        # Test Case 7: File with no title, selector 'title'
        print("\n--- Test Case 7: No Title file, Selector 'title' ---")
        results7 = extract_text_with_selector(html_no_title, "title")
        print(f"Found {len(results7)} snippets: {results7}")
        assert len(results7) == 0
        print("Test Case 7 PASSED")

    logger.info("Basic extractor tests finished.")
