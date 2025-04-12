"""
Module: advanced_extractor.py

Description:
    Handles the advanced, single-file snippet extraction based on pre-parsed
    ContentBlocks (from utils.extract_content_blocks_from_html). Includes logic
    specific to searching within code blocks and JSON structures, relevance scoring,
    and prioritization.
"""

from loguru import logger
import json
from pathlib import Path
from typing import List, Optional, Dict, Any

from mcp_doc_retriever.models import SearchResultItem, ContentBlock
from mcp_doc_retriever.searcher.helpers import (
    read_file_with_fallback,
    extract_content_blocks_from_html,
    json_structure_search,
    code_block_relevance_score,
)
from mcp_doc_retriever.utils import contains_all_keywords


# ------------------------------------------------------------------
# AdvancedSearchOptions defined as a Pydantic model
# ------------------------------------------------------------------
from pydantic import BaseModel


class AdvancedSearchOptions(BaseModel):
    scan_keywords: List[str]
    extract_keywords: Optional[List[str]] = None
    search_code_blocks: bool = True
    search_json: bool = True
    code_block_priority: bool = False
    json_match_mode: str = "keys"


# ------------------------------------------------------------------
# Main extraction function
# ------------------------------------------------------------------
def extract_advanced_snippets_with_options(
    file_path: Path,  # Expect a Path object
    scan_keywords: List[str],
    extract_keywords: Optional[List[str]] = None,
    search_code_blocks: bool = True,
    search_json: bool = True,
    code_block_priority: bool = False,
    json_match_mode: str = "keys",
) -> List[SearchResultItem]:
    """
    Performs advanced snippet extraction from a SINGLE file using pre-extracted content blocks.
    Searches through content blocks for keywords and prioritizes code/JSON matches if requested.

    Args:
        file_path: Path to the HTML/Markdown file.
        scan_keywords: List of keywords (all must match) used for search.
        extract_keywords: Optional list of secondary keywords (all must match).
        search_code_blocks: Whether to perform matching in code blocks.
        search_json: Whether to perform matching in JSON snippets.
        code_block_priority: Whether to prioritize code block matches in the output.
        json_match_mode: The mode for JSON structure search (“keys”, “values”, or “structure”).

    Returns:
        A list of SearchResultItem instances containing the results.
    """
    content = read_file_with_fallback(file_path)
    if content is None:
        logger.warning("Cannot read file for advanced extraction: {}", file_path)
        return []

    results: List[SearchResultItem] = []
    file_uri = f"file://{file_path.resolve()}"

    try:
        content_blocks = extract_content_blocks_from_html(content, source_url=file_uri)
    except Exception as e:
        logger.error(
            "Failed to extract content blocks from {}: {}", file_path, e, exc_info=True
        )
        return []

    # Pre-calculate lowercase versions of keywords.
    scan_keywords_lower = [kw.lower() for kw in scan_keywords if kw and kw.strip()]
    extract_keywords_lower = [
        kw.lower() for kw in (extract_keywords or []) if kw and kw.strip()
    ]

    for block in content_blocks:
        block_content_lower = block.content.lower()
        matched = False
        match_info: Dict[str, Any] = {}
        score: Optional[float] = None
        search_context = block.type  # Default context

        # --- JSON Block Processing ---
        if block.type == "json" and search_json:
            all_check_keywords = scan_keywords_lower + extract_keywords_lower
            if all_check_keywords:
                keyword_present_in_raw = any(
                    kw in block_content_lower for kw in all_check_keywords
                )
                structure_match_info: Dict[str, Any] = {}
                try:
                    json_obj = json.loads(block.content)
                    if scan_keywords:
                        structure_match_info = json_structure_search(
                            json_obj, scan_keywords, match_mode=json_match_mode
                        )
                    if (
                        structure_match_info.get("score", 0) > 0
                        or keyword_present_in_raw
                    ):
                        matched = True
                        match_info = structure_match_info
                        if (
                            keyword_present_in_raw
                            and structure_match_info.get("score", 0) == 0
                        ):
                            match_info["match_type"] = "keyword_in_raw_json"
                        search_context = "json"
                except json.JSONDecodeError:
                    logger.debug(
                        "Block in {} is not valid JSON. Checking raw content...",
                        file_path,
                    )
                    if keyword_present_in_raw:
                        matched = True
                        match_info = {"match_type": "keyword_in_invalid_json"}
                        search_context = block.type
                except Exception as e:
                    logger.warning(
                        "Error during JSON processing block from {}: {}",
                        file_path,
                        e,
                        exc_info=True,
                    )

        # --- Code Block Processing ---
        elif block.type == "code" and search_code_blocks:
            if contains_all_keywords(
                block_content_lower, scan_keywords_lower
            ) and contains_all_keywords(block_content_lower, extract_keywords_lower):
                matched = True
                try:
                    score = code_block_relevance_score(
                        block.content, scan_keywords, block.language
                    )
                except Exception as e:
                    logger.warning(
                        "Error calculating code block score for {}: {}", file_path, e
                    )
                    score = 0.0
                search_context = "code"

        # --- Text Block Processing ---
        elif block.type == "text":
            if contains_all_keywords(
                block_content_lower, scan_keywords_lower
            ) and contains_all_keywords(block_content_lower, extract_keywords_lower):
                matched = True
                search_context = "text"

        # --- If this block matches, construct a SearchResultItem.
        if matched:
            try:
                item = SearchResultItem(
                    original_url=block.source_url or file_uri,
                    local_path="",  # Provide empty string (or compute a path if needed)
                    content_preview=block.content[
                        :100
                    ],  # A preview: first 100 characters
                    match_details=block.content,  # Full content as details
                    selector_matched=(
                        block.metadata.get("selector", block.type)
                        if block.metadata
                        else block.type
                    ),
                    content_block=block,
                    code_block_score=score if search_context == "code" else None,
                    json_match_info=match_info if search_context == "json" else None,
                    search_context=search_context,
                )
                results.append(item)
            except Exception as e:
                logger.error(
                    "Error creating SearchResultItem for matched block from {}: {}",
                    file_path,
                    e,
                    exc_info=True,
                )

    # --- Optional sorting by code block priority ---
    if code_block_priority and results:
        results = sorted(
            results,
            key=lambda r: (r.search_context != "code", -(r.code_block_score or 0)),
        )

    logger.debug(
        "Advanced extraction found {} relevant snippets in {}", len(results), file_path
    )
    return results


# ------------------------------------------------------------------
# Standalone Execution / Testing Section
# ------------------------------------------------------------------
if __name__ == "__main__":
    import tempfile
    from mcp_doc_retriever.models import ContentBlock  # For test data

    # For testing we create some mock functions in case the real ones aren’t available.
    def mock_extract_blocks(content: str, source_url: str) -> List[ContentBlock]:
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
        return {"score": 0.5} if "scan" in str(obj) else {"score": 0}

    def mock_code_score(code: str, keywords: List[str], lang: Optional[str]) -> float:
        return 0.8 if "scan" in code else 0.1

    def mock_contains_all(text: str, keywords: List[str]) -> bool:
        return all(k in text for k in keywords)

    try:
        from mcp_doc_retriever.searcher.helpers import (
            read_file_with_fallback,
            extract_content_blocks_from_html,
            json_structure_search,
            code_block_relevance_score,
        )
        from mcp_doc_retriever.utils import contains_all_keywords

        logger.info("Using actual utils for advanced extractor test.")
    except ImportError:
        logger.warning("Mocking utils for advanced extractor test.")
        contains_all_keywords = mock_contains_all
        extract_content_blocks_from_html = mock_extract_blocks
        json_structure_search = mock_json_search
        code_block_relevance_score = mock_code_score

    logger.info("Running advanced_extractor standalone test...")

    # Dictionary to capture test results
    test_results = {}
    summary_all_passed = True

    with tempfile.TemporaryDirectory() as tmpdir:
        base_dir = Path(tmpdir)
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
        logger.info("Test file created: {}", test_file)

        # Test Case 1: Scan for "scan"
        try:
            results1 = extract_advanced_snippets_with_options(
                file_path=test_file,
                scan_keywords=["scan"],
                search_code_blocks=True,
                search_json=True,
            )
            if len(results1) == 3:
                test_results["Test Case 1"] = "PASS"
            else:
                test_results["Test Case 1"] = f"FAIL (Expected 3, got {len(results1)})"
        except Exception as ex:
            test_results["Test Case 1"] = f"ERROR: {ex}"

        # Test Case 2: Scan for "scan" with extract filter "extract" (expects JSON only)
        try:
            results2 = extract_advanced_snippets_with_options(
                file_path=test_file,
                scan_keywords=["scan"],
                extract_keywords=["extract"],
                search_code_blocks=True,
                search_json=True,
            )
            if len(results2) == 1 and results2[0].search_context == "json":
                test_results["Test Case 2"] = "PASS"
            else:
                test_results["Test Case 2"] = (
                    f"FAIL (Expected 1 JSON match, got {len(results2)})"
                )
        except Exception as ex:
            test_results["Test Case 2"] = f"ERROR: {ex}"

        # Test Case 3: Scan for "scan", prioritize code blocks
        try:
            results3 = extract_advanced_snippets_with_options(
                file_path=test_file,
                scan_keywords=["scan"],
                search_code_blocks=True,
                search_json=True,
                code_block_priority=True,
            )
            if len(results3) == 3 and results3[0].search_context == "code":
                test_results["Test Case 3"] = "PASS"
            else:
                test_results["Test Case 3"] = (
                    f"FAIL (Expected code block first; got context {results3[0].search_context if results3 else 'None'})"
                )
        except Exception as ex:
            test_results["Test Case 3"] = f"ERROR: {ex}"

        # Test Case 4: Only search JSON (disable code block search)
        try:
            results4 = extract_advanced_snippets_with_options(
                file_path=test_file,
                scan_keywords=["scan"],
                search_code_blocks=False,
                search_json=True,
            )
            if len(results4) == 2 and not any(
                r.search_context == "code" for r in results4
            ):
                test_results["Test Case 4"] = "PASS"
            else:
                test_results["Test Case 4"] = (
                    f"FAIL (Expected 2 non-code matches; got {len(results4)})"
                )
        except Exception as ex:
            test_results["Test Case 4"] = f"ERROR: {ex}"

        # Print test-by-test summary
        print("\n--------------------")
        for name in sorted(test_results.keys()):
            result = test_results.get(name, "UNKNOWN (Test did not run)")
            print(f"- {name}: {result}")
            if "FAIL" in result or "ERROR" in result or "UNKNOWN" in result:
                summary_all_passed = False
        print("\n--------------------")
        if summary_all_passed:
            print("✓ All Advanced Extractor tests passed!")
        else:
            print("✗ Some Advanced Extractor tests FAILED or were SKIPPED.")
        print("--------------------")

    logger.info("Advanced extractor tests finished.")
