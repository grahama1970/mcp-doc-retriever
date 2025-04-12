"""
Module: advanced_extractor.py

Description:
Handles the advanced, single-file snippet extraction based on pre-parsed
ContentBlocks (from utils.extract_content_blocks_from_html). Includes logic
specific to searching within code blocks and JSON structures, relevance scoring,
and prioritization.
"""

import logging
import json
from pathlib import Path
from typing import List, Optional, Dict, Any

# Use relative imports for models, helpers, and utils
from mcp_doc_retriever.models import SearchResultItem, ContentBlock
from mcp_doc_retriever.searcher.helpers import (
    read_file_with_fallback,
    extract_content_blocks_from_html,
    json_structure_search,
    code_block_relevance_score
)
from mcp_doc_retriever.utils import contains_all_keywords


logger = logging.getLogger(__name__)


def extract_advanced_snippets_with_options(
    file_path: Path,  # Expect Path object
    scan_keywords: List[str],
    extract_keywords: Optional[List[str]] = None,
    search_code_blocks: bool = True,
    search_json: bool = True,
    code_block_priority: bool = False,
    json_match_mode: str = "keys",
) -> List[SearchResultItem]:
    """
    Performs advanced snippet extraction from a SINGLE file using pre-extracted content blocks.
    Searches through content blocks for keywords, prioritizing code/JSON matches if requested.
    Assumes `extract_content_blocks_from_html` utility can parse the file content.

    Args:
        file_path: Path to the HTML/Markdown file.
        scan_keywords: List of keywords to search for within blocks (all must match text/code).
        extract_keywords: Optional list of secondary keywords (all must match text/code).
        search_code_blocks: Whether to search code blocks.
        search_json: Whether to search JSON snippets.
        code_block_priority: Whether to prioritize code block matches higher in results.
        json_match_mode: "keys", "values", or "structure" for JSON structure search.

    Returns:
        List of SearchResultItem with metadata for matches found in this file.
    """
    content = read_file_with_fallback(file_path)
    if content is None:
        logger.warning(f"Cannot read file for advanced extraction: {file_path}")
        return []

    results: List[SearchResultItem] = []
    # Generate a file URI for the source URL if not otherwise available
    # Useful if the original URL isn't passed down or readily available
    file_uri = f"file://{file_path.resolve()}"

    # Extract content blocks using the utility function
    # Requires utils.extract_content_blocks_from_html(content: str, source_url: str) -> List[ContentBlock]
    try:
        content_blocks = extract_content_blocks_from_html(content, source_url=file_uri)
    except Exception as e:
        logger.error(
            f"Failed to extract content blocks from {file_path}: {e}", exc_info=True
        )
        return []  # Cannot proceed if block extraction fails

    # Prepare keywords for case-insensitive search (filter empty/whitespace)
    scan_keywords_lower = [kw.lower() for kw in scan_keywords if kw and kw.strip()]
    extract_keywords_lower = [
        kw.lower() for kw in (extract_keywords or []) if kw and kw.strip()
    ]

    # Iterate through each extracted content block
    for block in content_blocks:
        block_content_lower = block.content.lower()  # Lowercase content for matching
        matched = False
        match_info: Dict[str, Any] = {}
        score: Optional[float] = None
        search_context = block.type  # Default context is the block's type

        # --- JSON Block Processing ---
        if block.type == "json" and search_json:
            # Combine all keywords for checking raw JSON presence
            all_check_keywords = scan_keywords_lower + extract_keywords_lower
            # Only process if there are keywords to check
            if all_check_keywords:
                # Check if *any* keyword exists in the raw JSON string representation
                keyword_present_in_raw = any(
                    kw in block_content_lower for kw in all_check_keywords
                )

                structure_match_info: Dict[str, Any] = {}
                try:
                    json_obj = json.loads(block.content)  # Attempt to parse the JSON

                    # Perform structure search only if scan_keywords are provided
                    # Requires utils.json_structure_search(obj: Any, keywords: List[str], match_mode: str) -> Dict
                    if (
                        scan_keywords
                    ):  # Use original case keywords for structure search if needed
                        structure_match_info = json_structure_search(
                            json_obj, scan_keywords, match_mode=json_match_mode
                        )

                    # A match occurs if structure search finds something OR any keyword is in the raw string
                    if (
                        structure_match_info.get("score", 0) > 0
                        or keyword_present_in_raw
                    ):
                        matched = True
                        match_info = structure_match_info
                        # Add context if the match was only due to raw keyword presence
                        if (
                            keyword_present_in_raw
                            and structure_match_info.get("score", 0) == 0
                        ):
                            match_info["match_type"] = "keyword_in_raw_json"
                        search_context = "json"  # Set context specifically to JSON

                except json.JSONDecodeError:
                    logger.debug(
                        f"Block in {file_path} is not valid JSON. Checking raw content..."
                    )
                    # Fallback: If JSON parsing fails, still check if keywords match the raw block content
                    if keyword_present_in_raw:
                        matched = True
                        match_info = {"match_type": "keyword_in_invalid_json"}
                        # Keep original block type as context (e.g., 'code' if it was a code block)
                        search_context = block.type
                except Exception as e:
                    # Catch other errors during JSON processing (e.g., structure search issues)
                    logger.warning(
                        f"Error during JSON processing block from {file_path}: {e}",
                        exc_info=True,
                    )

        # --- Code Block Processing ---
        # Use 'elif' to avoid processing the same block twice if JSON parsing failed but it was 'code' type
        elif block.type == "code" and search_code_blocks:
            # Requires utils.contains_all_keywords
            # Check if *all* scan keywords AND *all* extract keywords are present
            if contains_all_keywords(
                block_content_lower, scan_keywords_lower
            ) and contains_all_keywords(block_content_lower, extract_keywords_lower):
                matched = True
                # Calculate relevance score using original case keywords if needed by scoring function
                # Requires utils.code_block_relevance_score(code: str, keywords: List[str], language: Optional[str]) -> float
                try:
                    score = code_block_relevance_score(
                        block.content, scan_keywords, block.language
                    )
                except Exception as e:
                    logger.warning(
                        f"Error calculating code block score for {file_path}: {e}"
                    )
                    score = 0.0  # Assign default score on error
                search_context = "code"

        # --- Text Block Processing ---
        elif block.type == "text":
            # Requires utils.contains_all_keywords
            # Check if *all* scan keywords AND *all* extract keywords are present
            if contains_all_keywords(
                block_content_lower, scan_keywords_lower
            ) and contains_all_keywords(block_content_lower, extract_keywords_lower):
                matched = True
                search_context = "text"

        # --- Append Result if Matched ---
        if matched:
            try:
                # Create the SearchResultItem Pydantic model instance
                item = SearchResultItem(
                    original_url=block.source_url
                    or file_uri,  # Use URL from block metadata or fallback to file URI
                    extracted_content=block.content,  # Store the full content of the matched block
                    # Use selector from block metadata if available, otherwise use block type
                    selector_matched=block.metadata.get("selector", block.type)
                    if block.metadata
                    else block.type,
                    content_block=block,  # Embed the full ContentBlock object
                    code_block_score=score if search_context == "code" else None,
                    json_match_info=match_info if search_context == "json" else None,
                    search_context=search_context,  # Store the determined context ('json', 'code', 'text')
                )
                results.append(item)
            except Exception as e:  # Catch potential Pydantic validation errors
                logger.error(
                    f"Error creating SearchResultItem for matched block from {file_path}: {e}",
                    exc_info=True,
                )

    # --- Sorting (Optional Prioritization) ---
    if code_block_priority and results:
        # Sort primarily by context ('code' first), then by score (higher first)
        results = sorted(
            results,
            key=lambda r: (
                r.search_context
                != "code",  # False (0) for code, True (1) for others, puts code first
                -(
                    r.code_block_score or 0
                ),  # Negate score to sort descending (highest score first)
            ),
        )

    logger.debug(
        f"Advanced extraction found {len(results)} relevant snippets in {file_path}"
    )
    return results


# --- Standalone Execution / Example ---
if __name__ == "__main__":
    import tempfile
    from mcp_doc_retriever.models import ContentBlock  # Need this for creating test data

    # Mock utils or ensure they are importable
    def mock_extract_blocks(content: str, source_url: str) -> List[ContentBlock]:
        # Simple mock for testing structure
        blocks = []
        if '"type": "json"' in content:
            blocks.append(
                ContentBlock(
                    type="json",
                    content='{"key": "value", "scan": "yes"}',
                    source_url=source_url,
                )
            )
        if "def func" in content:
            blocks.append(
                ContentBlock(
                    type="code",
                    content="def func(scan):\n  pass",
                    language="python",
                    source_url=source_url,
                )
            )
        blocks.append(
            ContentBlock(
                type="text",
                content="Some text with scan keyword.",
                source_url=source_url,
            )
        )
        return blocks

    def mock_json_search(obj: Any, keywords: List[str], match_mode: str) -> Dict:
        return {"score": 0.5} if "scan" in obj else {"score": 0}

    def mock_code_score(code: str, keywords: List[str], lang: Optional[str]) -> float:
        return 0.8 if "scan" in code else 0.1

    def mock_contains_all(text: str, keywords: List[str]) -> bool:
        return all(k in text for k in keywords)

    try:
        from mcp_doc_retriever.searcher.helpers import (
        read_file_with_fallback,
        extract_content_blocks_from_html,
        json_structure_search,
        code_block_relevance_score
        )
        from mcp_doc_retriever.utils import contains_all_keywords


        logger.info("Using actual utils for advanced extractor test.")
    except ImportError:
        logger.warning("Mocking utils for advanced extractor test.")
        contains_all_keywords = mock_contains_all
        extract_content_blocks_from_html = mock_extract_blocks
        json_structure_search = mock_json_search
        code_block_relevance_score = mock_code_score

    logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")
    logger.info("Running advanced_extractor standalone test...")

    with tempfile.TemporaryDirectory() as tmpdir:
        base_dir = Path(tmpdir)

        # Create a test file with mixed content types
        test_html_content = """
        <html><body>
            <p>Some text with scan keyword.</p>
            <pre><code class="language-python">def func(scan):
      pass</code></pre>
            <pre><code class="language-json">{
      "key": "value",
      "scan": "yes",
      "extract": "maybe"
    }</code></pre>
            <p>Another paragraph.</p>
        </body></html>
        """
        test_file = base_dir / "advanced_test.html"
        test_file.write_text(test_html_content, encoding="utf-8")
        logger.info(f"Test file created: {test_file}")

        # --- Test Cases ---
        print("\n--- Test Case 1: Scan for 'scan' ---")
        results1 = extract_advanced_snippets_with_options(
            file_path=test_file,
            scan_keywords=["scan"],
            search_code_blocks=True,
            search_json=True,
        )
        print(f"Found {len(results1)} results:")
        for r in results1:
            print(
                f"- Context: {r.search_context}, Score: {r.code_block_score}, JSON Info: {r.json_match_info}, Content: '{r.extracted_content[:30]}...'"
            )
        assert len(results1) == 3  # Expect text, code, json matches
        print("Test Case 1 PASSED")

        print("\n--- Test Case 2: Scan for 'scan', Filter for 'extract' ---")
        results2 = extract_advanced_snippets_with_options(
            file_path=test_file,
            scan_keywords=["scan"],
            extract_keywords=["extract"],  # Only JSON block should match this
            search_code_blocks=True,  # Code block has 'scan' but not 'extract'
            search_json=True,
        )
        print(f"Found {len(results2)} results:")
        for r in results2:
            print(
                f"- Context: {r.search_context}, Score: {r.code_block_score}, JSON Info: {r.json_match_info}, Content: '{r.extracted_content[:30]}...'"
            )
        assert len(results2) == 1  # Only JSON block has both 'scan' and 'extract'
        assert results2[0].search_context == "json"
        print("Test Case 2 PASSED")

        print("\n--- Test Case 3: Scan for 'scan', Prioritize Code ---")
        results3 = extract_advanced_snippets_with_options(
            file_path=test_file,
            scan_keywords=["scan"],
            search_code_blocks=True,
            search_json=True,
            code_block_priority=True,  # Sort code block first
        )
        print(f"Found {len(results3)} results (sorted):")
        for r in results3:
            print(
                f"- Context: {r.search_context}, Score: {r.code_block_score}, JSON Info: {r.json_match_info}, Content: '{r.extracted_content[:30]}...'"
            )
        assert len(results3) == 3
        assert (
            results3[0].search_context == "code"
        )  # Code block should be first due to sorting
        print("Test Case 3 PASSED")

        print("\n--- Test Case 4: Only search JSON ---")
        results4 = extract_advanced_snippets_with_options(
            file_path=test_file,
            scan_keywords=["scan"],
            search_code_blocks=False,  # Disable code search
            search_json=True,
        )
        print(f"Found {len(results4)} results:")
        for r in results4:
            print(
                f"- Context: {r.search_context}, Score: {r.code_block_score}, JSON Info: {r.json_match_info}, Content: '{r.extracted_content[:30]}...'"
            )
        assert len(results4) == 2  # Expect text and json matches
        assert not any(r.search_context == "code" for r in results4)
        print("Test Case 4 PASSED")

    logger.info("Advanced extractor tests finished.")
