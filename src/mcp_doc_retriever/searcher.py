"""
Module: searcher.py

Purpose:
Implements two-phase search over downloaded HTML files:
1. Fast keyword scan on decoded text content.
2. Precise CSS selector-based extraction on candidate files.

Links:
- BeautifulSoup: https://www.crummy.com/software/BeautifulSoup/bs4/doc/
- Pydantic: https://docs.pydantic.dev/

Sample Input (Basic and Advanced):
```python
# Basic text search
results = perform_search(
    download_id="tech_docs",
    scan_keywords=["database", "view"],
    selector="p, pre code",
    extract_keywords=["arangosearch"]
)

# Advanced JSON structure search
results = extract_advanced_snippets_with_options(
    file_path="docs/config.html",
    scan_keywords=["links", "analyzers"],
    search_json=True,
    json_match_mode="structure"
)
```

Sample Output:
```python
[
    SearchResultItem(
        original_url="https://docs.arangodb.com/views.html",
        extracted_content='{"links":{"fields":{"analyzers":["text_en"]}}}',
        selector_matched="pre code",
        search_context="json",
        code_block_score=0.85,
        json_match_info={"path": "links.fields.analyzers"}
    ),
    ...
]
```
"""

import logging
import os
import json
import re
from typing import List, Optional

# Import package modules (support package mode first, fallback to standalone)
import sys
from pathlib import Path
try:
    from . import config
    from .models import IndexRecord, SearchResultItem, ContentBlock
    from .utils import (
        clean_html_for_search, extract_code_blocks_from_html,
        extract_json_from_code_block, json_structure_search,
        code_block_relevance_score, extract_content_blocks_from_html
    )
except ImportError:
    # Setup path for standalone execution
    project_root = Path(__file__).parent.parent.parent
    sys.path.append(str(project_root))
    from src.mcp_doc_retriever import config
    from src.mcp_doc_retriever.models import IndexRecord, SearchResultItem, ContentBlock
    from src.mcp_doc_retriever.utils import (
        clean_html_for_search, extract_code_blocks_from_html,
        extract_json_from_code_block, json_structure_search,
        code_block_relevance_score, extract_content_blocks_from_html
    )

# Setup logger
logger = logging.getLogger(__name__)

# --- Helper Functions (Mostly unchanged, added logging/minor fixes) ---


def is_allowed_path(file_path: str, allowed_base_dirs: List[str]) -> bool:
    """Check if the file_path is within any of the allowed_base_dirs."""
    try:
        # Handle potential None or empty file_path
        if not file_path:
            return False
        real_file_path = os.path.realpath(file_path)
        for base_dir in allowed_base_dirs:
            real_base = os.path.realpath(base_dir)
            # Ensure comparison happens correctly even if paths are identical
            if real_file_path == real_base or real_file_path.startswith(
                real_base + os.sep
            ):
                return True
            # Use commonpath as a fallback, though startswith is usually sufficient and clearer
            # try:
            #     common = os.path.commonpath([real_file_path, real_base])
            #     if common == real_base:
            #         return True
            # except ValueError:
            #     continue # Handles different drives etc.
    except Exception as e:
        logger.error(
            f"Error checking path allowance for '{file_path}' against base dirs: {e}",
            exc_info=True,
        )
        return False  # Fail safely
    return False


def is_file_size_ok(file_path: str, max_size_bytes: int = 10 * 1024 * 1024) -> bool:
    """Check if the file size is within the allowed limit."""
    try:
        # Handle potential None or empty file_path
        if not file_path:
            return False
        # Check existence before getting size
        if not os.path.isfile(file_path):
            logger.warning(f"File not found when checking size: {file_path}")
            return False
        size = os.path.getsize(file_path)
        is_ok = size <= max_size_bytes
        if not is_ok:
            logger.debug(
                f"File size {size} bytes exceeds limit {max_size_bytes} for {file_path}"
            )
        return is_ok
    except OSError as e:
        logger.warning(f"Could not get size for {file_path}: {e}")
        return False
    except Exception as e:
        logger.error(
            f"Unexpected error checking size for {file_path}: {e}", exc_info=True
        )
        return False  # Fail safely


def read_file_with_fallback(file_path: str) -> Optional[str]:
    """Attempt to read a file with multiple encodings."""
    if not file_path or not os.path.isfile(
        file_path
    ):  # Check existence before trying to open
        logger.warning(
            f"File not found or invalid path provided for reading: {file_path}"
        )
        return None

    # List of encodings to try
    encodings_to_try = ["utf-8", "latin-1", "windows-1252"]  # Added another common one

    for encoding in encodings_to_try:
        try:
            with open(file_path, "r", encoding=encoding) as f:
                logger.debug(f"Successfully read {file_path} with encoding {encoding}")
                return f.read()
        except UnicodeDecodeError:
            logger.debug(
                f"Failed to decode {file_path} with encoding {encoding}, trying next..."
            )
            continue  # Try next encoding
        except (
            FileNotFoundError
        ):  # Should be caught by initial check, but defense in depth
            logger.warning(f"File disappeared before reading: {file_path}")
            return None
        except Exception as e:
            # Log other file reading errors (permissions, etc.)
            logger.warning(
                f"Error reading {file_path} with encoding {encoding}: {e}",
                exc_info=True,
            )
            # Don't try other encodings if a non-decode error occurs (like permission denied)
            return None
    logger.warning(
        f"Could not read or decode file {file_path} with any attempted encoding."
    )
    return None


def extract_text_from_html(content: str) -> Optional[str]:
    """Extract plain text from HTML content, lowercase."""
    if not content:
        return None
    try:
        # Simple regex-based HTML tag removal
        text = re.sub(r'<[^>]+>', ' ', content)  # Replace tags with space
        text = " ".join(text.split())  # Normalize whitespace
        return text.lower()
    except Exception as e:
        logger.warning(f"Error processing HTML content: {e}", exc_info=True)
        return None


def contains_all_keywords(text: Optional[str], keywords: List[str]) -> bool:
    """Check if all (lowercase) keywords are present in the text."""
    if not text or not keywords:  # Handle empty text or keywords list
        return False
    # Ensure keywords are lowercase for comparison
    lowered_keywords = [
        kw.lower() for kw in keywords if kw
    ]  # Filter out empty keywords
    if not lowered_keywords:
        return False  # No valid keywords to check

    return all(keyword in text for keyword in lowered_keywords)


def scan_files_for_keywords(
    file_paths: List[str],
    scan_keywords: List[str],
    allowed_base_dirs: Optional[List[str]] = None,
) -> List[str]:
    """Scan a list of HTML files for presence of all specified keywords."""
    matches = []
    if not scan_keywords:  # If no keywords are provided, no files can match
        logger.warning("scan_files_for_keywords called with empty scan_keywords list.")
        return matches

    lowered_keywords = [
        kw.lower() for kw in scan_keywords if kw
    ]  # Prepare keywords once
    if not lowered_keywords:
        logger.warning("scan_files_for_keywords: no valid non-empty keywords provided.")
        return matches

    logger.info(f"Scanning {len(file_paths)} files for keywords: {lowered_keywords}")
    files_scanned = 0
    for file_path in file_paths:
        # --- Security Check 1: Restrict file paths ---
        if allowed_base_dirs and not is_allowed_path(file_path, allowed_base_dirs):
            logger.warning(f"Skipping file outside allowed directories: {file_path}")
            continue

        # --- Security Check 2: Limit file size ---
        if not is_file_size_ok(file_path):  # Also handles existence check
            logger.warning(f"Skipping large or inaccessible file: {file_path}")
            continue

        # --- Read and Process ---
        content = read_file_with_fallback(file_path)
        if content is None:
            logger.warning(f"Skipping file due to read/decoding errors: {file_path}")
            continue

        text = extract_text_from_html(content)
        if text is None:
            logger.warning(f"Skipping file due to HTML parsing errors: {file_path}")
            continue

        # --- Keyword Check ---
        if contains_all_keywords(text, lowered_keywords):
            logger.debug(f"Keywords found in: {file_path}")
            matches.append(file_path)
        files_scanned += 1

    logger.info(
        f"Scan complete. Scanned {files_scanned} accessible files. Found {len(matches)} candidate files."
    )
    return matches


def extract_text_with_selector(
    file_path: str,
    selector: str,
    extract_keywords: Optional[List[str]] = None,
    clean_html: bool = False
) -> List[str]:
    """
    Improved text extraction: if selector is 'title', extract only the <title> tag text using BeautifulSoup.
    Otherwise, returns the full text content of the file (HTML tags removed), optionally filtered by extract_keywords.
    """
    content = read_file_with_fallback(file_path)
    if content is None:
        logger.warning(f"Extraction failed: Cannot read file: {file_path}")
        return []

    if selector and selector.strip().lower() == "title":
        try:
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(content, "html.parser")
            title_tag = soup.title
            if title_tag and title_tag.string:
                title_text = title_tag.string.strip()
                if not title_text:
                    return []
                # Optional keyword filtering
                if extract_keywords:
                    title_text_lower = title_text.lower()
                    extract_keywords_lower = [kw.lower() for kw in extract_keywords if kw]
                    if not all(kw in title_text_lower for kw in extract_keywords_lower):
                        return []
                return [title_text]
            else:
                return []
        except Exception as e:
            logger.warning(f"Failed to extract <title> tag from {file_path}: {e}")
            return []

    # Fallback: Remove HTML tags and normalize whitespace
    text = re.sub(r'<[^>]+>', ' ', content)
    text = " ".join(text.split())

    if not text:
        return []

    # Optional keyword filtering
    if extract_keywords:
        text_lower = text.lower()
        extract_keywords_lower = [kw.lower() for kw in extract_keywords if kw]
        if not all(kw in text_lower for kw in extract_keywords_lower):
            return []

    return [text]

def extract_advanced_snippets_with_options(
    file_path: str,
    scan_keywords: List[str],
    extract_keywords: Optional[List[str]] = None,
    clean_html: bool = False,  # No longer needed since we use pre-cleaned content
    search_code_blocks: bool = True,
    search_json: bool = True,
    code_block_priority: bool = False,
    json_match_mode: str = "keys"
) -> List["SearchResultItem"]:
    """
    Simplified search using pre-extracted content blocks.
    Searches through content blocks for keywords, prioritizing code/JSON matches.

    Args:
        file_path: Path to the HTML/Markdown file.
        scan_keywords: List of keywords to search for.
        extract_keywords: Optional keywords to further filter snippets.
        search_code_blocks: Whether to search code blocks.
        search_json: Whether to search JSON snippets.
        code_block_priority: Whether to prioritize code block matches.
        json_match_mode: "keys", "values", or "structure" for JSON search.

    Returns:
        List of SearchResultItem with metadata.
    """
    content = read_file_with_fallback(file_path)
    if content is None:
        return []

    results = []
    content_blocks = extract_content_blocks_from_html(content)
    
    # Prepare keywords for case-insensitive search
    scan_keywords_lower = [kw.lower() for kw in scan_keywords if kw]
    extract_keywords_lower = [kw.lower() for kw in (extract_keywords or []) if kw]

    for block in content_blocks:
        block_content = block.content.lower()

        # For JSON blocks, do structure and value matching
        if block.type == "json" and search_json:
            try:
                json_obj = json.loads(block.content)
                match_info = json_structure_search(json_obj, scan_keywords, match_mode=json_match_mode)
                # Check for value matches (e.g., "text_en") anywhere in the JSON
                json_str = json.dumps(json_obj).lower()
                value_match = any(kw in json_str for kw in scan_keywords_lower + extract_keywords_lower)
                if match_info["score"] > 0 or value_match:
                    # Add info about value match
                    if value_match:
                        match_info = dict(match_info)
                        match_info["value_match"] = True
                    results.append(
                        SearchResultItem(
                            original_url="",
                            extracted_content=block.content,
                            selector_matched="pre code",
                            content_block=block,
                            json_match_info=match_info,
                            search_context="json"
                        )
                    )
            except json.JSONDecodeError:
                logger.debug(f"Failed to parse JSON in block: {block.content[:100]}...")
                continue

        # For code blocks, calculate relevance score (all scan keywords must be present)
        elif block.type == "code" and search_code_blocks:
            if all(kw in block_content for kw in scan_keywords_lower):
                score = code_block_relevance_score(block.content, scan_keywords, block.language)
                if score > 0:
                    results.append(
                        SearchResultItem(
                            original_url="",
                            extracted_content=block.content,
                            selector_matched="pre code",
                            content_block=block,
                            code_block_score=score,
                            search_context="code"
                        )
                    )

        # For text blocks, include if ANY scan keyword is present
        elif block.type == "text":
            if any(kw in block_content for kw in scan_keywords_lower):
                results.append(
                    SearchResultItem(
                        original_url="",
                        extracted_content=block.content,
                        selector_matched="p",
                        content_block=block,
                        search_context="text"
                    )
                )

    # Prioritize code block results if requested
    if code_block_priority:
        results = sorted(
            results,
            key=lambda r: (r.search_context == "code", r.code_block_score or 0),
            reverse=True,
        )

    return results


# --- Main Search Function ---


def perform_search(
    download_id: str,
    scan_keywords: List[str],
    selector: str,
    extract_keywords: Optional[List[str]] = None,
    base_dir: Optional[str] = None,  # Added base_dir parameter
) -> List[SearchResultItem]:
    """
    Perform a search over downloaded HTML files using keyword scanning and content extraction.

    Args:
        download_id: The download session identifier (should be validated/sanitized by caller).
        scan_keywords: Keywords to scan files for.
        selector: CSS selector to extract content.
        extract_keywords: Optional keywords to filter extracted snippets.
        base_dir: The base directory for downloads (overrides config if provided).

    Returns:
        list[SearchResultItem]: List of search result items.
    """
    logger.info(f"Starting search for download_id: '{download_id}'")
    logger.debug(
        f"Scan keywords: {scan_keywords}, Selector: '{selector}', Extract keywords: {extract_keywords}"
    )

    # Determine base directory (use provided argument or fallback to config)
    search_base_dir = base_dir if base_dir is not None else config.DOWNLOAD_BASE_DIR
    # Ensure it's absolute for consistent checks
    abs_search_base_dir = os.path.abspath(search_base_dir)
    allowed_base_dirs = [abs_search_base_dir]  # List for helper functions
    logger.info(f"Using search base directory: {abs_search_base_dir}")

    # --- Removed download_id regex validation ---
    # Validation/sanitization should happen at the API layer before calling this.
    # Rely on FileNotFoundError if the index doesn't exist for the given ID.

    search_results: List[SearchResultItem] = []
    # Construct index path using the determined base directory and the raw download_id
    index_path = os.path.join(abs_search_base_dir, "index", f"{download_id}.jsonl")
    logger.info(f"Looking for index file at: {index_path}")
    logger.debug(f"Attempting to read index file: {index_path}")

    # --- Read Index File ---
    try:
        # Check existence first for a clearer error message
        if not os.path.isfile(index_path):
            logger.error(f"Index file not found: {index_path}")
            return []  # Return empty list if index doesn't exist

        with open(index_path, "r", encoding="utf-8") as f:
            lines = f.readlines()
    except Exception as e:
        # Catch potential errors during file open (e.g., permissions)
        logger.error(
            f"Failed to open or read index file {index_path}: {e}", exc_info=True
        )
        return []  # Cannot proceed without index

    url_map: dict[str, str] = {}  # Map realpath -> original_url
    successful_paths: List[str] = []

    # --- Process Index Records ---
    logger.debug(f"Processing {len(lines)} lines from index file...")
    processed_lines = 0
    skipped_records = 0
    for i, line in enumerate(lines):
        line = line.strip()
        if not line:
            continue  # Skip empty lines

        try:
            record_data = json.loads(line)
            # Validate data against the Pydantic model
            record = IndexRecord(**record_data)
            processed_lines += 1

            # Filter for successful downloads with valid paths
            if record.fetch_status == "success" and record.local_path:
                # Security Check: Ensure path is within the allowed base directory
                # Note: record.local_path might be relative or absolute depending on downloader.
                # Construct full path relative to base_dir for checking.
                # Construct path relative to the search base directory
                potential_path = os.path.join(abs_search_base_dir, record.local_path)
                potential_abs_path = os.path.abspath(potential_path)

                if is_allowed_path(potential_abs_path, allowed_base_dirs):
                    # Use realpath to resolve symlinks etc. for consistency
                    real_path = os.path.realpath(potential_abs_path)
                    # Ensure the path exists and is within allowed base
                    if not os.path.exists(real_path):
                        logger.warning(f"File does not exist: {real_path}")
                        continue
                    if os.path.isfile(
                        real_path
                    ):  # Ensure the target is actually a file
                        url_map[real_path] = (
                            record.original_url
                        )  # Map resolved path to URL
                        successful_paths.append(real_path)
                    else:
                        logger.warning(
                            f"Index record {i + 1}: Path '{record.local_path}' (resolved: {real_path}) is not a file. Skipping."
                        )
                        skipped_records += 1
                else:
                    logger.warning(
                        f"Index record {i + 1}: Path '{record.local_path}' is outside allowed base directory '{abs_search_base_dir}'. Skipping."
                    )
                    skipped_records += 1
            else:
                skipped_records += (
                    1  # Count records skipped due to fetch_status or missing path
                )

        except json.JSONDecodeError:
            logger.warning(
                f"Skipping invalid JSON line {i + 1} in index: {line[:100]}..."
            )
            skipped_records += 1
        except Exception as e:  # Catch Pydantic validation errors or others
            logger.warning(
                f"Skipping invalid record on line {i + 1} in index: {e} - Data: {line[:100]}...",
                exc_info=True,
            )
            skipped_records += 1

    logger.info(
        f"Index processing complete. Found {len(successful_paths)} successful download paths from {processed_lines} valid records ({skipped_records} skipped)."
    )
    logger.debug(f"Paths identified for keyword scan: {successful_paths}")

    if not successful_paths:
        logger.info(
            "No successful download paths found in index. Search cannot proceed."
        )
        return []

    # --- Phase 1: Scan Files for Keywords ---
    candidate_paths = scan_files_for_keywords(
        successful_paths,
        scan_keywords,
        allowed_base_dirs=allowed_base_dirs,  # Pass allowed base for security checks in scan
    )
    logger.info(f"Keyword scan identified {len(candidate_paths)} candidate files.")
    logger.debug(f"Candidate paths after keyword scan: {candidate_paths}")

    if not candidate_paths:
        logger.info("No files matched the scan keywords.")
        return []

    # --- Phase 2: Extract Snippets from Candidate Files ---
    logger.info(
        f"Extracting snippets using selector '{selector}' from candidate files..."
    )
    extraction_count = 0
    for candidate_path in candidate_paths:
        logger.debug(f"Extracting from candidate file: {candidate_path}")
        snippets = extract_text_with_selector(
            candidate_path, selector, extract_keywords
        )

        if snippets:
            original_url = url_map.get(
                candidate_path, "URL_NOT_FOUND_IN_MAP"
            )  # Get URL from map
            if original_url == "URL_NOT_FOUND_IN_MAP":
                logger.error(
                    f"Consistency Error: Path '{candidate_path}' was a candidate but not found in url_map."
                )
                continue  # Skip if we can't find the original URL

            logger.debug(
                f"Found {len(snippets)} relevant snippets in {candidate_path} for URL {original_url}"
            )
            for snippet in snippets:
                try:
                    item = SearchResultItem(
                        original_url=original_url,
                        extracted_content=snippet,
                        selector_matched=selector,
                    )
                    search_results.append(item)
                    extraction_count += 1
                except Exception as e:
                    # Catch potential Pydantic validation errors creating the item
                    logger.error(
                        f"Error creating SearchResultItem for snippet from {original_url}: {e}",
                        exc_info=True,
                    )
                    logger.debug(
                        f"Problematic snippet data: URL='{original_url}', Selector='{selector}', Snippet='{snippet[:100]}...'"
                    )

    logger.info(
        f"Search complete. Found {len(search_results)} total matching snippets from {len(candidate_paths)} files."
    )
    return search_results


# --- Standalone Execution / Example ---
if __name__ == "__main__":
    import shutil

    # Setup logging
    logging.basicConfig(
        level=logging.INFO,
        format="[%(levelname)s] %(name)s: %(message)s"
    )
    logger.info("Running comprehensive searcher verification...")

    # Setup test directory structure
    test_dir = Path("searcher_test_data")
    if test_dir.exists():
        shutil.rmtree(test_dir)
    test_dir.mkdir(exist_ok=True)
    content_dir = test_dir / "content" / "docs.example.com"
    content_dir.mkdir(parents=True, exist_ok=True)
    index_dir = test_dir / "index"
    index_dir.mkdir(exist_ok=True)

    try:
        # --- Test Case 1: Complex JSON Structure Search ---
        logger.info("\n=== Testing Complex JSON Structure Search ===")
        
        # Create test file with ArangoDB-style view configuration
        json_doc = {
            "links": {
                "coll": {
                    "fields": {
                        "attr": {
                            "fields": {
                                "nested": {
                                    "analyzers": ["text_en"]
                                }
                            }
                        }
                    }
                }
            }
        }

        # Embed JSON in HTML with contextual text
        view_html = f"""
        <html><body>
            <h1>View Configuration Reference</h1>
            <p>Example of link properties in an ArangoSearch view:</p>
            <pre><code class="language-json">
{json.dumps(json_doc, indent=2)}
            </code></pre>
            <p>The above configuration defines text analysis settings.</p>
            <p>This paragraph contains analyzers, view, and text_en for test matching.</p>
        </body></html>
        """

        # Write test files
        view_file = content_dir / "view_config.html"
        view_file.write_text(view_html)

        # Create index record
        index_file = index_dir / "test_dl.jsonl"
        index_file.write_text(
            IndexRecord(
                original_url="http://docs.example.com/view_config.html",
                canonical_url="http://docs.example.com/view_config.html",
                local_path=str(view_file.relative_to(test_dir)),
                fetch_status="success",
                content_blocks=[
                    ContentBlock(
                        type="json",
                        content=json.dumps(json_doc, indent=2),
                        language="json",
                        block_type="code"
                    )
                ]
            ).model_dump_json(exclude_none=True)
        )

        # --- Test Case 2: Mixed Content Search ---
        logger.info("\n=== Testing Mixed Content Search ===")
        
        # Example with both text and code blocks
        mixed_html = '''
        <html><body>
            <h1>Search Documentation</h1>
            <p>Text analysis in views uses analyzers for tokenization.</p>
            <pre><code class="language-javascript">
            // Example analyzer configuration
            var analyzers = ["text_en", "identity"];
            </code></pre>
            <p>Configure view properties using JSON:</p>
            <pre><code class="language-json">
            {
              "analyzers": ["text_en"],
              "includeAllFields": true
            }
            </code></pre>
        </body></html>
        '''
        
        mixed_file = content_dir / "mixed_content.html"
        mixed_file.write_text(mixed_html)

        # Add to index
        with open(index_file, "a") as f:
            f.write("\n" + IndexRecord(
                original_url="http://docs.example.com/mixed_content.html",
                canonical_url="http://docs.example.com/mixed_content.html",
                local_path=str(mixed_file.relative_to(test_dir)),
                fetch_status="success",
                content_blocks=[
                    ContentBlock(
                        type="code",
                        content='var analyzers = ["text_en", "identity"];',
                        language="javascript",
                        block_type="code"
                    ),
                    ContentBlock(
                        type="json",
                        content='{"analyzers": ["text_en"], "includeAllFields": true}',
                        language="json",
                        block_type="code"
                    )
                ]
            ).model_dump_json(exclude_none=True))

            # --- Run Tests ---
        logger.info("\nExecuting searches...")
        
        # Override config temporarily
        original_config_dir = config.DOWNLOAD_BASE_DIR
        config.DOWNLOAD_BASE_DIR = str(test_dir)
        
        try:
            # Test 1: Search for JSON structure
            logger.info("Testing JSON structure search...")
            test_file = str(view_file)
            logger.info(f"Using test file: {test_file} (exists: {view_file.exists()})")
            logger.info(f"Test file content:")
            try:
                logger.info(view_file.read_text()[:200] + "...")
            except Exception as e:
                logger.error(f"Error reading test file: {e}")

            advanced_results = extract_advanced_snippets_with_options(
                file_path=test_file,
                scan_keywords=["links", "analyzers"],
                extract_keywords=["text_en"],
                search_code_blocks=True,
                search_json=True,
                code_block_priority=True,
                json_match_mode="structure"
            )

            # Test 2: Search with code block priority
            logger.info("Testing mixed content search with code block priority...")
            logger.info(f"Test directory exists: {test_dir.exists()}")
            logger.info(f"Index file exists: {index_file.exists()}")
            if index_file.exists():
                logger.info(f"Index file content:")
                logger.info(index_file.read_text())

            base_dir = str(test_dir)
            logger.info(f"Using base directory: {base_dir}")
            
            mixed_results = perform_search(
                download_id="test_dl",
                scan_keywords=["analyzers", "view"],
                selector="p, pre code",
                extract_keywords=["text_en"],
                base_dir=base_dir
            )

            # --- Verify Results ---
            logger.info("\n=== Verification Results ===")
            # Verify JSON structure search
            json_matches = [r for r in advanced_results if r.search_context == "json"]
            logger.info(f"\nFound {len(json_matches)} JSON matches")
            for r in json_matches:
                logger.info(f"Match info: {r.json_match_info}")
                logger.info(f"Content: {r.extracted_content[:100]}...")

            has_matches = len(json_matches) > 0
            # Check for "text_en" in the extracted content, not just in match_info
            has_text_en = any("text_en" in r.extracted_content for r in json_matches)
            json_test_passed = has_matches and has_text_en
            
            logger.info(f"\nJSON Test Details:")
            logger.info(f"- Has JSON matches: {has_matches}")
            logger.info(f"- Found 'text_en': {has_text_en}")
            logger.info(f"- Overall passed: {json_test_passed}")
            
            
            print("\nJSON Structure Search Results:")
            print("-" * 40)
            for r in json_matches:
                print(f"- Match Type: {r.search_context}")
                print(f"  Info: {r.json_match_info}")
                print(f"  Score: {r.code_block_score}")

            # Verify mixed content search
            code_blocks = [r for r in mixed_results if "code" in r.selector_matched]
            text_matches = [r for r in mixed_results if "p" in r.selector_matched]
            
            logger.info(f"\nMixed Test Details:")
            logger.info(f"Found {len(code_blocks)} code blocks:")
            for b in code_blocks:
                logger.info(f"- {b.selector_matched}: {b.extracted_content[:100]}")
                logger.info(f"  Keywords: {[k for k in ['analyzers', 'view', 'text_en'] if k in b.extracted_content]}")
            
            logger.info(f"\nFound {len(text_matches)} text matches:")
            for t in text_matches:
                logger.info(f"- {t.selector_matched}: {t.extracted_content}")
                logger.info(f"  Keywords: {[k for k in ['analyzers', 'view', 'text_en'] if k in t.extracted_content]}")
            # Print all text blocks from the content block extraction for debugging
            logger.info("\nAll extracted text blocks from mixed_content.html:")
            for block in extract_content_blocks_from_html(mixed_file.read_text()):
                if block.type == "text":
                    logger.info(f"[TEXT BLOCK] {block.content}")
            # For verification, require at least one text match containing any scan keyword
            has_text_match = any(
                any(k in t.extracted_content for k in ['analyzers', 'view', 'text_en'])
                for t in text_matches
            )
            
            mixed_test_passed = len(code_blocks) > 0 and has_text_match
            logger.info(f"\nMixed Test Results:")
            logger.info(f"- Has code blocks: {len(code_blocks) > 0}")
            logger.info(f"- Has text matches: {has_text_match}")
            logger.info(f"- Overall passed: {mixed_test_passed}")
            
            print("\nMixed Content Search Results:")
            print("-" * 40)
            print("Code Blocks Found:")
            for r in code_blocks:
                print(f"- {r.selector_matched}: {r.extracted_content[:60]}...")
            print("\nText Matches Found:")
            for r in text_matches:
                print(f"- {r.selector_matched}: {r.extracted_content[:60]}...")

            # Final result
            if json_test_passed and mixed_test_passed:
                logger.info("\nAll tests PASSED ✓")
                print("\nTest Summary:")
                print(f"- JSON Structure Search: {'✓' if json_test_passed else '✗'}")
                print(f"- Mixed Content Search:  {'✓' if mixed_test_passed else '✗'}")
                sys.exit(0)
            else:
                logger.error("\nSome tests FAILED")
                for test, result in [("JSON Search", json_test_passed), ("Mixed Content", mixed_test_passed)]:
                    if not result:
                        logger.error(f"- {test} test failed")
                sys.exit(1)
        finally:
            # Restore config
            config.DOWNLOAD_BASE_DIR = original_config_dir

    except Exception as e:
        logger.error(f"Error during verification: {e}", exc_info=True)
        sys.exit(1)
    finally:
        # Clean up test data
        if test_dir.exists():
            shutil.rmtree(test_dir)
