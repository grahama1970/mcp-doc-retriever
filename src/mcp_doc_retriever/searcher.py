"""
Module: searcher.py

Purpose:
Implements two-phase search over downloaded HTML files:
1. Fast keyword scan on decoded text content.
2. Precise CSS selector-based extraction on candidate files.

Links:
- BeautifulSoup: https://www.crummy.com/software/BeautifulSoup/bs4/doc/
- Pydantic: https://docs.pydantic.dev/

Sample Input:
perform_search(
    download_id="test_dl",
    scan_keywords=["apple", "banana"],
    selector="p",
    extract_keywords=["banana"]
)

Sample Output:
[
    SearchResultItem(
        original_url="http://example.com/page1",
        extracted_content="This paragraph mentions apple and banana.",
        selector_matched="p"
    ),
    ...
]
"""

import logging
import os
import json
import re
from bs4 import BeautifulSoup
from typing import List, Optional  # Added Optional and List

# Import config and models relative to the package structure
from . import config
from .models import IndexRecord, SearchResultItem

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
        # Consider using 'lxml' for potentially better performance and robustness if available
        # soup = BeautifulSoup(content, 'lxml')
        soup = BeautifulSoup(content, "html.parser")  # Default parser
        # Get text, replacing multiple whitespace chars with a single space
        text = " ".join(soup.get_text(separator=" ").split()).lower()
        return text
    except Exception as e:
        # Catch potential errors during parsing (e.g., recursion depth)
        logger.warning(f"Error parsing HTML content: {e}", exc_info=True)
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
    file_path: str, selector: str, extract_keywords: Optional[List[str]] = None
) -> List[str]:
    """Extract text snippets from elements matching a CSS selector."""
    content = read_file_with_fallback(file_path)
    if content is None:
        logger.warning(f"Extraction failed: Cannot read file: {file_path}")
        return []

    try:
        # Consider 'lxml' for performance
        soup = BeautifulSoup(content, "html.parser")
    except Exception as e:
        logger.warning(
            f"Extraction failed: Error parsing HTML in {file_path}: {e}", exc_info=True
        )
        return []

    try:
        # Use select() which is generally robust, but catch potential issues
        elements = soup.select(selector)
        if not elements:
            logger.debug(
                f"No elements matched selector '{selector}' in file {file_path}"
            )
            return []
    except Exception as e:
        # Catch errors from invalid selectors (though BS4 is often lenient)
        logger.warning(
            f"Invalid or problematic CSS selector '{selector}' for file {file_path}: {e}",
            exc_info=True,
        )
        return []

    snippets = []
    for el in elements:
        try:
            # Extract text, remove leading/trailing whitespace, normalize internal space
            text = " ".join(el.get_text(separator=" ", strip=True).split())
            if text:  # Only add non-empty snippets
                snippets.append(text)
        except Exception as e:
            # Catch potential errors getting text from a specific element
            logger.warning(
                f"Error getting text from element matching '{selector}' in {file_path}: {e}",
                exc_info=True,
            )
            continue  # Skip this element

    logger.debug(
        f"Extracted {len(snippets)} snippets using selector '{selector}' from {file_path} before filtering."
    )
    # logger.debug(f"Snippets before filtering: {snippets}") # Optionally log snippets

    # --- Optional Keyword Filtering ---
    if extract_keywords:
        # Prepare filter keywords once (lowercase, remove empty)
        lowered_filter_keywords = [kw.lower() for kw in extract_keywords if kw]
        if lowered_filter_keywords:
            filtered_snippets = []
            for snippet in snippets:
                snippet_lower = snippet.lower()
                # Check if ALL filter keywords are in the snippet
                if all(kw in snippet_lower for kw in lowered_filter_keywords):
                    filtered_snippets.append(snippet)

            logger.debug(
                f"Filtered snippets down to {len(filtered_snippets)} based on keywords: {lowered_filter_keywords}"
            )
            return filtered_snippets
        else:
            logger.warning(
                "extract_keywords provided but contained no valid keywords after filtering."
            )
            # Return all snippets if filter keywords were empty/invalid
            return snippets
    else:
        # No filtering requested
        return snippets


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
    import sys
    # Note: This requires config to be importable relative to this file.

    # --- Setup for Standalone Example ---
    # Basic logging setup
    logging.basicConfig(
        level=logging.DEBUG, format="[%(levelname)s] %(name)s: %(message)s"
    )

    logger.info("Running minimal standalone verification of perform_search()...")

    # Define test parameters
    test_base = "searcher_test_data"
    test_download_id = "search_test_dl"
    test_scan_kws = ["example", "document"]
    test_selector = "p"
    test_extract_kws = ["example"]  # Filter for paragraphs containing "example"

    # --- Create dummy files/dirs for test ---
    try:
        # Paths
        test_index_dir = os.path.join(test_base, "index")
        test_content_dir = os.path.join(test_base, "content", "example.com")
        test_index_file = os.path.join(test_index_dir, f"{test_download_id}.jsonl")
        test_html_file = os.path.join(test_content_dir, "test.html")
        test_html_file_abs = os.path.abspath(test_html_file)

        # Create directories
        os.makedirs(test_index_dir, exist_ok=True)
        os.makedirs(test_content_dir, exist_ok=True)

        # Create dummy index file content
        # Note: local_path should be relative to the base_dir for searcher logic
        relative_html_path = os.path.join("content", "example.com", "test.html")
        index_records = [
            IndexRecord(
                original_url="http://example.com/test.html",
                canonical_url="http://example.com/test.html",
                local_path=relative_html_path,  # Path relative to base_dir
                content_md5="dummy_md5",
                fetch_status="success",
                http_status=200,
                error_message=None,
            ).model_dump_json(exclude_none=True),
            IndexRecord(
                original_url="http://example.com/skipped.html",
                canonical_url="http://example.com/skipped.html",
                local_path="content/example.com/skipped.html",
                fetch_status="skipped",
            ).model_dump_json(exclude_none=True),
            IndexRecord(
                original_url="http://example.com/failed.html",
                canonical_url="http://example.com/failed.html",
                local_path="",
                fetch_status="failed_request",
                http_status=500,
                error_message="Server Error",
            ).model_dump_json(exclude_none=True),
        ]
        with open(test_index_file, "w", encoding="utf-8") as f:
            for record_json in index_records:
                f.write(record_json + "\n")
        logger.info(f"Created dummy index file: {test_index_file}")

        # Create dummy HTML file content
        html_content = """
        <!DOCTYPE html><html><head><title>Test Page</title></head>
        <body><h1>Test Heading</h1>
        <p>This is the first paragraph of the example document.</p>
        <p>This second paragraph is another example.</p>
        <div><p>A nested paragraph, still part of the example.</p></div>
        <p>This one does not match the filter keyword.</p>
        </body></html>
        """
        with open(test_html_file, "w", encoding="utf-8") as f:
            f.write(html_content)
        logger.info(f"Created dummy HTML file: {test_html_file}")

        # --- Run the search ---
        # Override config temporarily for the test
        original_config_dir = config.DOWNLOAD_BASE_DIR
        config.DOWNLOAD_BASE_DIR = test_base  # Point config to test dir

        results = perform_search(
            download_id=test_download_id,
            scan_keywords=test_scan_kws,
            selector=test_selector,
            extract_keywords=test_extract_kws,
            # base_dir=test_base # Optionally pass base_dir directly
        )

        # Restore config
        config.DOWNLOAD_BASE_DIR = original_config_dir

        # --- Print Results ---
        print("-" * 20)
        print(f"Found {len(results)} results:")
        expected_count = 3  # Expecting 3 paragraphs containing "example"
        for r in results:
            print(f"- URL: {r.original_url} | Selector: {r.selector_matched}")
            print(f"  Content: '{r.extracted_content}'")
        print("-" * 20)

        if len(results) == expected_count:
            print("Standalone verification PASSED.")
            exit_code = 0
        else:
            print(
                f"Standalone verification FAILED (Expected {expected_count}, Got {len(results)})."
            )
            exit_code = 1

        # Optional: Clean up test data
        # import shutil
        # logger.info(f"Cleaning up test directory: {test_base}")
        # shutil.rmtree(test_base)

        sys.exit(exit_code)

    except Exception as e:
        logger.error(f"Error during standalone verification: {e}", exc_info=True)
        sys.exit(1)
