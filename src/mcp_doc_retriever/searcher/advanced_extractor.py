"""
Module: advanced_extractor.py

Description:
    Handles the advanced, single‐file snippet extraction based on pre‐parsed
    ContentBlocks. Includes logic specific to searching within code blocks
    and JSON structures, relevance scoring, and prioritization. Selects
    appropriate block extractor (HTML/Markdown) based on file type and
    integrates tree-sitter validation for code blocks.
"""

import logging
import json
from pathlib import Path
from typing import List, Optional, Dict, Any, Union # Added Union

# Use relative imports for models, helpers, and utils
from mcp_doc_retriever.models import SearchResultItem, ContentBlock
from mcp_doc_retriever.searcher.helpers import (
    read_file_with_fallback,
    extract_content_blocks_from_html, # Keep for HTML
    json_structure_search,
    code_block_relevance_score,
)
# Import the NEW Markdown extractor
from mcp_doc_retriever.searcher.markdown_extractor import extract_content_blocks_with_markdown_it
# Import the Tree-sitter validator
from mcp_doc_retriever.searcher.tree_sitter_extractor import validate_code_snippet
from mcp_doc_retriever.utils import contains_all_keywords


logger = logging.getLogger(__name__)


def extract_advanced_snippets_with_options(
    file_path: Union[str, Path], # Allow str or Path
    scan_keywords: List[str],
    extract_keywords: Optional[List[str]] = None,
    search_code_blocks: bool = True,
    search_json: bool = True,
    code_block_priority: bool = False,
    json_match_mode: str = "keys",
) -> List[SearchResultItem]:
    """
    Performs advanced snippet extraction from a SINGLE file using pre‐extracted content blocks.
    Selects the appropriate block extractor based on file type (HTML/Markdown).
    Validates code blocks using tree-sitter.
    Searches through content blocks for keywords and prioritizes code/JSON matches if requested.

    Args:
        file_path: Path to the HTML/Markdown file (as a Path object or str).
        scan_keywords: List of keywords used for initial filtering/matching.
        extract_keywords: Optional list of secondary keywords (all must match text/code).
        search_code_blocks: Whether to search code blocks.
        search_json: Whether to search JSON snippets.
        code_block_priority: Whether to prioritize code block matches.
        json_match_mode: The mode for JSON structure search (“keys”, “values”, or “structure”).

    Returns:
        A list of SearchResultItem instances.
    """
    if isinstance(file_path, str):
        file_path = Path(file_path) # Convert str to Path

    content = read_file_with_fallback(file_path)
    if content is None:
        logger.warning(f"Cannot read file for advanced extraction: {file_path}")
        return []
    # --- DEBUG: Log raw content before block extraction ---
    logger.debug(f"Raw content read for {file_path} (length: {len(content) if content else 0}):\n'''\n{content[:500] + '...' if content and len(content) > 500 else content}\n'''")

    results: List[SearchResultItem] = []
    file_uri = f"file://{file_path.resolve()}"
    content_blocks: List[ContentBlock] = []

    # --- Select appropriate block extractor based on file type ---
    file_suffix = file_path.suffix.lower()
    extractor_type = "unknown"
    try:
        if file_suffix in ['.md', '.markdown']:
            extractor_type = "Markdown"
            logger.debug(f"Using Markdown extractor (markdown-it) for {file_path}")
            content_blocks = extract_content_blocks_with_markdown_it(content, source_url=file_uri)
            # --- DEBUG: Log blocks received from Markdown extractor ---
            logger.debug(f"Received {len(content_blocks)} blocks from Markdown extractor for {file_path}:")
            for idx, b in enumerate(content_blocks):
                logger.debug(f"  Received MD Block {idx}: type={b.type}, lang={b.language}, content='{b.content[:50]}...'" )
        elif file_suffix in ['.html', '.htm']:
            extractor_type = "HTML"
            logger.debug(f"Using HTML extractor for {file_path}")
            content_blocks = extract_content_blocks_from_html(content, source_url=file_uri)
        else:
            extractor_type = "HTML (fallback)"
            logger.warning(f"Unsupported file type '{file_suffix}' for advanced extraction, attempting HTML: {file_path}")
            content_blocks = extract_content_blocks_from_html(content, source_url=file_uri) # Default to HTML for now

    except Exception as e:
        logger.error(
            f"Failed to extract content blocks from {file_path} using {extractor_type} extractor: {e}", exc_info=True
        )
        return [] # Cannot proceed if block extraction fails

    # Prepare keywords for case-insensitive search (filter empty/whitespace)
    scan_keywords_lower = [kw.lower() for kw in scan_keywords if kw and kw.strip()]
    extract_keywords_lower = [
        kw.lower() for kw in (extract_keywords or []) if kw and kw.strip()
    ]

    # Iterate through each extracted content block
    for block in content_blocks:
        ts_validated: bool = False
        ts_language: Optional[str] = None
        block_content = block.content if block.content else "" # Ensure content is not None
        block_content_lower = block_content.lower()  # Lowercase content for matching
        matched = False
        match_info: Dict[str, Any] = {}
        score: Optional[float] = None
        search_context = block.type  # Default context is the block's type

        # --- JSON Block Processing ---
        if block.type == "json" and search_json:
            all_check_keywords = scan_keywords_lower + extract_keywords_lower
            keyword_present_in_raw = False
            if all_check_keywords:
                 keyword_present_in_raw = any(
                    kw in block_content_lower for kw in all_check_keywords
                )

            structure_match_info: Dict[str, Any] = {}
            try:
                json_obj = json.loads(block_content)
                if scan_keywords:
                    structure_match_info = json_structure_search(
                        json_obj, scan_keywords, match_mode=json_match_mode
                    )
                if structure_match_info.get("score", 0) > 0 or keyword_present_in_raw:
                    matched = True
                    match_info = structure_match_info
                    if keyword_present_in_raw and structure_match_info.get("score", 0) == 0:
                        match_info["match_type"] = "keyword_in_raw_json"
                    search_context = "json"

            except json.JSONDecodeError:
                 logger.debug(f"Block in {file_path} is not valid JSON. Checking raw content...")
                 if keyword_present_in_raw:
                    matched = True
                    match_info = {
                        "matched_items": [kw for kw in all_check_keywords if kw in block_content_lower],
                        "score": 0.0,
                        "match_type": "invalid_json_raw",
                    }
                    search_context = "json"
            except Exception as e:
                logger.warning(f"Error during JSON structure search for block from {file_path}: {e}", exc_info=True)

        # --- Code Block Processing (with Tree-sitter validation) ---
        elif block.type == "code" and search_code_blocks:
            ts_validated, ts_language = validate_code_snippet(block_content, block.language)
            if ts_validated:
                logger.debug(f"Code block validated by tree-sitter as '{ts_language}' in {file_path}")
                if contains_all_keywords(
                    block_content_lower, scan_keywords_lower
                ) and contains_all_keywords(block_content_lower, extract_keywords_lower):
                    matched = True
                    try:
                        score = code_block_relevance_score(
                            block_content, scan_keywords, ts_language
                        )
                    except Exception as e:
                        logger.warning(f"Error calculating code block score for {file_path}: {e}")
                        score = 0.0
                    search_context = "code"
            else:  # Code block failed tree-sitter validation
                # Check if it's likely from Markdown (fenced block) before falling back to text search
                is_markdown_block = block.metadata and block.metadata.get("selector", "").startswith("fenced_")
                if is_markdown_block:
                    logger.debug(f"Markdown code block failed tree-sitter validation (lang hint: {block.language}) in {file_path}. Treating as text for keyword search.")
                    # Fallback: Treat as text and check for keywords
                    if contains_all_keywords(
                        block_content_lower, scan_keywords_lower
                    ) and contains_all_keywords(block_content_lower, extract_keywords_lower):
                        matched = True
                        search_context = "text" # Mark context as text since code validation failed
                        score = None # No code score applicable
                else:
                    # For non-Markdown blocks (e.g., HTML pre/code), discard if validation fails
                    logger.debug(f"Non-Markdown code block failed tree-sitter validation (lang hint: {block.language}) in {file_path}. Discarding.")
                    matched = False

        # --- Text Block Processing ---
        elif block.type == "text":
            if contains_all_keywords(
                block_content_lower, scan_keywords_lower
            ) and contains_all_keywords(block_content_lower, extract_keywords_lower):
                matched = True
                search_context = "text"

        # --- Append Result if Matched ---
        if matched:
            try:
                # Construct item data using fields from models.SearchResultItem
                item_data = {
                    "original_url": block.source_url or file_uri,
                    "local_path": str(file_path.resolve()), # Required field
                    "content_preview": block_content[:100], # Required field
                    "match_details": block_content, # Required field
                    "selector_matched": (block.metadata.get("selector") if block.metadata else block.type),
                    "content_block": block,
                    "code_block_score": score if search_context == "code" else None,
                    "json_match_info": match_info if search_context == "json" else None,
                    "search_context": search_context,
                    # Add tree-sitter validation info (will require model update later)
                    # "ts_validated": ts_validated if search_context == "code" else None,
                    # "ts_language": ts_language if search_context == "code" else None,
                }
                item = SearchResultItem(**item_data)
                results.append(item)
            except Exception as e:
                logger.error(
                    f"Error creating SearchResultItem for matched block from {file_path}: {e}",
                    exc_info=True,
                )

    # --- Sorting (Optional Prioritization) ---
    if code_block_priority and results:
        results = sorted(
            results,
            key=lambda r: (
                r.search_context != "code",
                -(r.code_block_score or 0),
            ),
        )

    logger.debug(
        f"Advanced extraction found {len(results)} relevant snippets in {file_path}"
    )
    return results


# --- Standalone Execution / Example ---
if __name__ == "__main__":
    import tempfile
    import sys

    project_root_dir = Path(__file__).resolve().parent.parent.parent
    src_dir = project_root_dir / "src"
    if str(src_dir) not in sys.path:
        sys.path.insert(0, str(src_dir))
        print(f"DEBUG: Added {src_dir} to sys.path for standalone execution.")

    logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")
    logger = logging.getLogger(__name__)

    logger.info("Running advanced_extractor standalone test...")

    with tempfile.TemporaryDirectory() as tmpdir:
        base_dir = Path(tmpdir)

        test_html_content = """
        <html><body>
            <p>Some text with scan keyword.</p>
            <pre><code class="language-javascript">function greet() { console.log("Hello scan"); }</code></pre>
            <pre><code class="language-json">{ "key": "value", "scan": "yes", "extract": "maybe" }</code></pre>
            <pre><code># Invalid python code with scan
            def scan_func(
            </code></pre>
        </body></html>
        """
        test_md_content = """
# MD Test

Some text with scan keyword.

```python
# Valid python with scan
def hello_world(scan):
    print("Hello")
```

```json
{ "key": "md_value", "number": 42, "scan": "yes", "extract": "maybe" }
```

```plaintext
Just some plain text block with scan.
```

```bad-python
# Invalid python with scan
def scan_again(
```
"""
        html_file = base_dir / "advanced_test.html"
        md_file = base_dir / "advanced_test.md"
        html_file.write_text(test_html_content, encoding="utf-8")
        md_file.write_text(test_md_content, encoding="utf-8")
        logger.info(f"Test files created: {html_file.resolve()}, {md_file.resolve()}")

        print("\n--- Test Case: HTML Scan for 'scan' ---")
        res_html_scan = extract_advanced_snippets_with_options(
            file_path=html_file, scan_keywords=["scan"], json_match_mode="values"
        )
        print(f"Found {len(res_html_scan)} results:")
        for r in res_html_scan: print(f"- Context: {r.search_context}, Preview: '{r.content_preview[:30]}...'")
        assert len(res_html_scan) == 3, f"HTML 'scan': Expected 3, Got {len(res_html_scan)}"
        assert any(r.search_context == "text" for r in res_html_scan)
        assert any(r.search_context == "code" for r in res_html_scan) # JS block is valid code
        assert any(r.search_context == "json" for r in res_html_scan)
        print("Test Case HTML 'scan' PASSED")

        print("\n--- Test Case: MD Scan for 'scan' ---")
        res_md_scan = extract_advanced_snippets_with_options(
            file_path=md_file, scan_keywords=["scan"], json_match_mode="values"
        )
        print(f"Found {len(res_md_scan)} results:")
        for r in res_md_scan: print(f"- Context: {r.search_context}, Preview: '{r.content_preview[:30]}...'")
        assert len(res_md_scan) == 5, f"MD 'scan': Expected 5, Got {len(res_md_scan)}"
        # The heading+paragraph counts as one text block, plus two invalid code blocks treated as text
        assert sum(1 for r in res_md_scan if r.search_context == "text") == 3
        assert any(r.search_context == "code" for r in res_md_scan) # Python block is valid code
        assert any(r.search_context == "json" for r in res_md_scan)
        print("Test Case MD 'scan' PASSED")

        print("\n--- Test Case: HTML Scan for 'greet' (code only) ---")
        res_html_code = extract_advanced_snippets_with_options(
            file_path=html_file, scan_keywords=["greet"], search_json=False
        )
        print(f"Found {len(res_html_code)} results:")
        for r in res_html_code: print(f"- Context: {r.search_context}, Preview: '{r.content_preview[:30]}...'")
        assert len(res_html_code) == 1, f"HTML 'greet': Expected 1, Got {len(res_html_code)}"
        assert res_html_code[0].search_context == "code"
        print("Test Case HTML 'greet' PASSED")

        print("\n--- Test Case: MD Scan for 'hello_world' (code only) ---")
        res_md_code = extract_advanced_snippets_with_options(
            file_path=md_file, scan_keywords=["hello_world"], search_json=False
        )
        print(f"Found {len(res_md_code)} results:")
        for r in res_md_code: print(f"- Context: {r.search_context}, Preview: '{r.content_preview[:30]}...'")
        assert len(res_md_code) == 1, f"MD 'hello_world': Expected 1, Got {len(res_md_code)}"
        assert res_md_code[0].search_context == "code"
        print("Test Case MD 'hello_world' PASSED")

        print("\n--- Test Case: HTML Scan+Extract ('scan', 'extract') ---")
        res_html_scan_extract = extract_advanced_snippets_with_options(
            file_path=html_file, scan_keywords=["scan"], extract_keywords=["extract"]
        )
        print(f"Found {len(res_html_scan_extract)} results:")
        for r in res_html_scan_extract: print(f"- Context: {r.search_context}, Preview: '{r.content_preview[:30]}...'")
        assert len(res_html_scan_extract) == 1, f"HTML scan+extract: Expected 1, Got {len(res_html_scan_extract)}"
        assert res_html_scan_extract[0].search_context == "json"
        print("Test Case HTML scan+extract PASSED")

        print("\n--- Test Case: MD Scan+Extract ('scan', 'extract') ---")
        res_md_scan_extract = extract_advanced_snippets_with_options(
            file_path=md_file, scan_keywords=["scan"], extract_keywords=["extract"]
        )
        print(f"Found {len(res_md_scan_extract)} results:")
        for r in res_md_scan_extract: print(f"- Context: {r.search_context}, Preview: '{r.content_preview[:30]}...'")
        assert len(res_md_scan_extract) == 1, f"MD scan+extract: Expected 1, Got {len(res_md_scan_extract)}"
        assert res_md_scan_extract[0].search_context == "json"
        print("Test Case MD scan+extract PASSED")

    logger.info("Advanced extractor tests finished.")
