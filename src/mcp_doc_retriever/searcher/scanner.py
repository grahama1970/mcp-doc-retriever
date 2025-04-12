"""
Module: keyword_scanner.py

Description:
Handles the first phase of the search: iterating through file paths provided
(typically from the index), reading file content safely, extracting plain text,
and checking if the text contains all specified keywords.
"""

import logging
from pathlib import Path
from typing import List

# Use relative imports for helpers and utils within the package
from mcp_doc_retriever.searcher.helpers import is_allowed_path, is_file_size_ok, read_file_with_fallback

from mcp_doc_retriever.searcher.helpers import (
    # contains_all_keywords moved to utils
    extract_text_from_html_content,
)
from mcp_doc_retriever.utils import contains_all_keywords
logger = logging.getLogger(__name__)


def scan_files_for_keywords(
    file_paths: List[Path],  # Expect list of Path objects
    scan_keywords: List[str],
    allowed_base_dirs: List[Path],  # Expect list of Path objects
) -> List[Path]:
    """
    Scans a list of files, checking if their text content contains all specified keywords.
    Performs security checks (path allowed, file size) before reading.
    NOTE: Reads full file content for keyword check - potentially inefficient.

    Args:
        file_paths: List of Path objects representing files to scan.
        scan_keywords: List of keywords that must all be present (case-insensitive).
        allowed_base_dirs: List of allowed base directory Paths for security.

    Returns:
        List of Path objects for files containing all keywords (using resolved paths).
    """
    candidate_paths: List[Path] = []
    if not scan_keywords:
        logger.warning("Keyword scan skipped: No scan_keywords provided.")
        return candidate_paths
    # Prepare keywords once (lowercase, filter empty)
    lowered_keywords = [kw.lower() for kw in scan_keywords if kw and kw.strip()]
    if not lowered_keywords:
        logger.warning("Keyword scan skipped: No valid non-empty keywords.")
        return candidate_paths

    logger.info(f"Scanning {len(file_paths)} files for keywords: {lowered_keywords}")
    files_scanned = 0
    for file_path in file_paths:
        # Security Check 1: Path traversal / allowed directory
        # Resolve path before checking against allowed bases
        # Use strict=False as file might be checked before creation in some edge cases,
        # but existence is checked later by is_file_size_ok -> is_file()
        resolved_path = file_path.resolve(strict=False)
        if not is_allowed_path(resolved_path, allowed_base_dirs):
            logger.warning(
                f"Skipping file outside allowed directories: {resolved_path}"
            )
            continue

        # Security Check 2: File size and existence/type using resolved path
        if not is_file_size_ok(resolved_path):
            # is_file_size_ok logs details if failed (incl. not found)
            continue

        # Read File Content using resolved path
        content = read_file_with_fallback(resolved_path)
        if content is None:
            # read_file_with_fallback logs details if failed
            continue

        # Extract Text (assuming a basic text extraction utility from utils)
        # Requires utils.extract_text_from_html_content(content: str) -> Optional[str]
        try:
            text = extract_text_from_html_content(content)
        except Exception as e:
            logger.error(
                f"Error during text extraction for {resolved_path}: {e}", exc_info=True
            )
            text = None  # Treat extraction error as no text found

        if text is None:
            logger.warning(
                f"Skipping file due to text extraction errors: {resolved_path}"
            )
            continue

        # Keyword Check (case-insensitive check via contains_all_keywords in utils)
        # Requires utils.contains_all_keywords(text: Optional[str], keywords: List[str]) -> bool
        try:
            if contains_all_keywords(text, lowered_keywords):
                logger.debug(f"Keywords found in: {resolved_path}")
                candidate_paths.append(resolved_path)  # Add the resolved path
        except Exception as e:
            logger.error(
                f"Error during keyword check for {resolved_path}: {e}", exc_info=True
            )

        files_scanned += 1

    logger.info(
        f"Keyword scan complete. Scanned {files_scanned} valid/accessible files. "
        f"Found {len(candidate_paths)} candidate files."
    )
    return candidate_paths


# --- Standalone Execution / Example ---
if __name__ == "__main__":
    import tempfile
    # Mock utils for standalone testing if needed, or ensure utils are importable
    # For simplicity, assume utils are available in the path or mock them here.

    # Basic Mocking for utils needed by scan_files_for_keywords
    def mock_extract_text(content: str) -> str:
        return content  # Simple mock

    def mock_contains_all(text: str, keywords: List[str]) -> bool:
        if not text:
            return False
        t_lower = text.lower()
        return all(k.lower() in t_lower for k in keywords)

    # Inject mocks if utils not directly importable in test setup
    # (Better approach is setting up test environment correctly)
    try:
        from mcp_doc_retriever.utils import contains_all_keywords, extract_text_from_html_content

        logger.info("Using actual utils.")
    except ImportError:
        logger.warning("Mocking utils for standalone test.")
        contains_all_keywords = mock_contains_all
        extract_text_from_html_content = mock_extract_text

    logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")
    logger.info("Running keyword_scanner standalone test...")

    with tempfile.TemporaryDirectory() as tmpdir:
        base_dir = Path(tmpdir).resolve()
        allowed_dirs = [base_dir]

        # Create test files
        file1 = base_dir / "file_apple_banana.txt"
        file1.write_text("This file has apple and banana.", encoding="utf-8")

        file2 = base_dir / "file_apple_only.txt"
        file2.write_text("This file has apple only.", encoding="utf-8")

        file3 = base_dir / "file_banana_grape.txt"
        file3.write_text("This file has banana and grape.", encoding="utf-8")

        file_list = [file1, file2, file3]
        logger.info(f"Test files created in {base_dir}")

        # Test Case 1: Find files with "apple" AND "banana"
        print("\n--- Test Case 1: Keywords ['apple', 'banana'] ---")
        results1 = scan_files_for_keywords(file_list, ["apple", "banana"], allowed_dirs)
        print(f"Found {len(results1)} files: {[p.name for p in results1]}")
        assert len(results1) == 1
        assert results1[0].name == "file_apple_banana.txt"
        print("Test Case 1 PASSED")

        # Test Case 2: Find files with "apple"
        print("\n--- Test Case 2: Keywords ['apple'] ---")
        results2 = scan_files_for_keywords(file_list, ["apple"], allowed_dirs)
        print(f"Found {len(results2)} files: {[p.name for p in results2]}")
        assert len(results2) == 2
        assert {p.name for p in results2} == {
            "file_apple_banana.txt",
            "file_apple_only.txt",
        }
        print("Test Case 2 PASSED")

        # Test Case 3: Find files with non-existent keyword
        print("\n--- Test Case 3: Keywords ['orange'] ---")
        results3 = scan_files_for_keywords(file_list, ["orange"], allowed_dirs)
        print(f"Found {len(results3)} files: {[p.name for p in results3]}")
        assert len(results3) == 0
        print("Test Case 3 PASSED")

        # Test Case 4: Empty keywords list
        print("\n--- Test Case 4: Keywords [] ---")
        results4 = scan_files_for_keywords(file_list, [], allowed_dirs)
        print(f"Found {len(results4)} files: {[p.name for p in results4]}")
        assert len(results4) == 0
        print("Test Case 4 PASSED")

    # Determine final status based on assertions
    all_scanner_tests_passed = True # Assume true unless an assert failed above
    # (No explicit tracking variable needed as asserts halt execution on failure)

    print("\n------------------------------------")
    if all_scanner_tests_passed: # This will only be reached if all asserts passed
        print("✓ All Keyword Scanner tests passed successfully.")
    # No explicit else needed, as script exits on assert failure
    print("------------------------------------")

    logger.info("Keyword scanner tests finished.")
