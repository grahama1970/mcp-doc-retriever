"""
Module: searcher.py (Main Search Orchestrator)

Purpose:
Orchestrates the basic, two-phase search over downloaded HTML files:
1. Reads the download index file (`<download_id>.jsonl`).
2. Identifies successfully downloaded files relevant to the search.
3. Calls the keyword scanner (`keyword_scanner.py`) to find candidate files.
4. Calls the basic extractor (`basic_extractor.py`) to get text snippets from candidates.
5. Returns results as a list of SearchResultItem models.

Does NOT perform the advanced block-based extraction itself (see advanced_extractor.py).
Requires the base download directory to be provided explicitly.
"""

import logging
import json
import sys
from pathlib import Path
from typing import List, Optional, Dict

# Use relative imports for models, helpers, and sub-modules
from mcp_doc_retriever.models import IndexRecord, SearchResultItem
from .helpers import (
    is_allowed_path
)  # Only needs these two now
from .scanner import scan_files_for_keywords
from .basic_extractor import extract_text_with_selector


logger = logging.getLogger(__name__)

# --- Main Search Function ---


def perform_search(
    download_id: str,
    scan_keywords: List[str],
    selector: str,  # Keep selector for the basic text extraction mode
    extract_keywords: Optional[List[str]] = None,
    base_dir: Path = None,  # Make base_dir required and type Path
) -> List[SearchResultItem]:
    """
    Performs a basic search over downloaded files for a given download ID.
    Uses keyword scanning to find candidate files and then extracts simple text snippets.
    Requires `base_dir` to locate the index and content files.

    Args:
        download_id: The download session identifier (assumed sanitized).
        scan_keywords: Keywords to find candidate files (all must be present).
        selector: CSS selector for basic text extraction (e.g., 'title', or others for full text).
        extract_keywords: Optional keywords to filter extracted basic text snippets (all must be present).
        base_dir: The root Path object for the download data (required).

    Returns:
        List of SearchResultItem found, containing basic extracted text.
    """
    # --- Input Validation ---
    if base_dir is None:
        raise ValueError(
            "base_dir parameter (type Path) is required for perform_search"
        )
    if not isinstance(base_dir, Path):
        # Ensure correct type is passed
        raise TypeError(f"base_dir must be a pathlib.Path object, got {type(base_dir)}")
    if not base_dir.is_dir():
        # Ensure the provided base directory exists
        raise FileNotFoundError(
            f"Provided base_dir does not exist or is not a directory: {base_dir}"
        )

    # Resolve base_dir to an absolute path for reliable comparisons
    abs_search_base_dir = base_dir.resolve()
    allowed_base_dirs = [abs_search_base_dir]  # Define allowed area for security checks
    logger.info(
        f"Starting basic search for download_id='{download_id}' in base='{abs_search_base_dir}'"
    )
    logger.debug(
        f"Params: scan_kw={scan_keywords}, selector='{selector}', extract_kw={extract_keywords}"
    )

    search_results: List[SearchResultItem] = []
    # Construct index file path using pathlib
    index_path = abs_search_base_dir / "index" / f"{download_id}.jsonl"
    logger.info(f"Using index file: {index_path}")

    # --- Read Index File ---
    if not index_path.is_file():
        logger.error(f"Index file not found: {index_path}. Cannot perform search.")
        return []  # Return empty list if index doesn't exist

    # url_map stores resolved Path -> original_url mapping
    url_map: Dict[Path, str] = {}
    # successful_paths stores resolved Paths of successfully downloaded files
    successful_paths: List[Path] = []
    processed_lines = 0
    skipped_records = 0

    try:
        # Open and process the index file line by line
        with index_path.open("r", encoding="utf-8") as f:
            for i, line in enumerate(f):
                line_num = i + 1
                line = line.strip()
                if not line:
                    continue  # Skip empty lines

                try:
                    record_data = json.loads(line)
                    # Validate the line data against the IndexRecord model
                    record = IndexRecord(**record_data)
                    processed_lines += 1

                    # Process only records for successfully fetched files with a path
                    if record.fetch_status == "success" and record.local_path:
                        # --- Path Construction and Validation ---
                        # ASSUMPTION: record.local_path is stored relative to the base_dir.
                        # Example: "content/<download_id>/some/path/file.html"
                        # Construct the full path using pathlib's / operator.
                        full_path = abs_search_base_dir / record.local_path
                        # Resolve the path (handles symlinks, .., etc.) `strict=False` needed
                        resolved_path = full_path.resolve(strict=False)

                        # Security Check: Ensure resolved path is within the allowed base directory
                        # Uses helper function is_allowed_path
                        if is_allowed_path(resolved_path, allowed_base_dirs):
                            # Check if the resolved path points to an actual file *after* resolving
                            # No need for is_file_size_ok here, scanner will check size later
                            if resolved_path.is_file():
                                url_map[resolved_path] = (
                                    record.original_url
                                )  # Map resolved path to original URL
                                successful_paths.append(resolved_path)
                            else:
                                # Log if the path resolved but isn't a file (e.g., directory, broken link)
                                logger.warning(
                                    f"Index line {line_num}: Path '{record.local_path}' (resolved: {resolved_path}) is not a file. Skipping."
                                )
                                skipped_records += 1
                        else:
                            # Log security warning if path is outside allowed base
                            logger.warning(
                                f"Index line {line_num}: Path '{record.local_path}' (resolved: {resolved_path}) is outside allowed base '{abs_search_base_dir}'. Skipping path traversal attempt."
                            )
                            skipped_records += 1
                    else:
                        # Increment skipped count if status wasn't success or path missing
                        skipped_records += 1

                except json.JSONDecodeError:
                    logger.warning(
                        f"Skipping invalid JSON line {line_num} in index: {line[:100]}..."
                    )
                    skipped_records += 1
                except (
                    Exception
                ) as e:  # Catch Pydantic validation errors or other unexpected issues
                    logger.warning(
                        f"Skipping invalid record on line {line_num}: {e} - Data: {line[:100]}...",
                        exc_info=True,
                    )
                    skipped_records += 1

    except Exception as e:
        # Catch errors during file open or reading
        logger.error(
            f"Failed to open or process index file {index_path}: {e}", exc_info=True
        )
        return []  # Cannot proceed without index

    logger.info(
        f"Index processed. Found {len(successful_paths)} successful file paths from "
        f"{processed_lines} valid records ({skipped_records} skipped)."
    )

    # Exit early if no valid file paths were found
    if not successful_paths:
        logger.info("No downloadable content paths found in index. Search finished.")
        return []

    # --- Phase 1: Scan Files for Keywords (Delegate to keyword_scanner) ---
    logger.info("Starting Phase 1: Keyword scan...")
    try:
        # Calls scan_files_for_keywords from keyword_scanner.py
        candidate_paths = scan_files_for_keywords(
            successful_paths,
            scan_keywords,
            allowed_base_dirs=allowed_base_dirs,  # Pass allowed base Paths
        )
        logger.info(f"Keyword scan identified {len(candidate_paths)} candidate files.")
    except Exception as e:
        logger.error(f"Error during keyword scanning phase: {e}", exc_info=True)
        return []  # Stop search if scanning fails

    # Exit early if no files match the initial keyword scan
    if not candidate_paths:
        logger.info("No candidate files found after keyword scan. Search finished.")
        return []

    # --- Phase 2: Extract Snippets from Candidate Files (Delegate to basic_extractor) ---
    logger.info(
        f"Starting Phase 2: Extracting basic snippets using selector '{selector}'..."
    )
    extraction_count = 0
    for (
        candidate_path
    ) in candidate_paths:  # candidate_paths now contains resolved Paths
        try:
            # Calls extract_text_with_selector from basic_extractor.py
            snippets = extract_text_with_selector(
                candidate_path, selector, extract_keywords
            )

            if snippets:
                # Retrieve the original URL using the resolved candidate path
                original_url = url_map.get(candidate_path)
                if original_url is None:
                    # This indicates an internal consistency issue if a candidate path isn't in the map
                    logger.error(
                        f"Consistency Error: Candidate path '{candidate_path}' not found in url_map. Skipping."
                    )
                    continue

                logger.debug(
                    f"Found {len(snippets)} relevant basic snippet(s) in {candidate_path} for URL {original_url}"
                )
                # Create a SearchResultItem for each extracted snippet
                for snippet in snippets:
                    try:
                        item = SearchResultItem(
                            original_url=original_url,
                            extracted_content=snippet,
                            selector_matched=selector,  # Record the selector used for this basic extraction
                            # content_block and other advanced fields remain None for basic search
                        )
                        search_results.append(item)
                        extraction_count += 1
                    except Exception as e:
                        # Catch potential Pydantic validation errors when creating the item
                        logger.error(
                            f"Error creating SearchResultItem for basic snippet from {original_url}: {e}",
                            exc_info=True,
                        )
                        logger.debug(
                            f"Problematic snippet data: URL='{original_url}', Selector='{selector}', Snippet='{snippet[:100]}...'"
                        )
        except Exception as e:
            logger.error(
                f"Error during basic snippet extraction for file {candidate_path}: {e}",
                exc_info=True,
            )
            # Optionally continue to next file or stop? Continue for now.

    logger.info(
        f"Basic snippet extraction complete. Found {extraction_count} snippets "
        f"in {len(candidate_paths)} candidate files matching the criteria."
    )
    logger.info(f"Total search results: {len(search_results)}")
    return search_results


# --- Standalone Execution / Example (Simplified - Tests moved primarily to module files) ---
if __name__ == "__main__":
    import shutil  # Keep shutil for cleanup

    # Setup basic logging for direct execution testing
    logging.basicConfig(
        level=logging.INFO, format="[%(levelname)-8s] %(name)s:%(lineno)d - %(message)s"
    )
    logger.info("Running searcher.py standalone example (basic orchestration)...")

    # Setup a temporary test environment
    test_base_dir = Path("./searcher_orchestration_test").resolve()
    logger.info(f"Setting up test directory: {test_base_dir}")
    if test_base_dir.exists():
        shutil.rmtree(test_base_dir)
    content_root = test_base_dir / "content"
    index_dir = test_base_dir / "index"
    index_dir.mkdir(parents=True, exist_ok=True)
    download_id_test = "orchestration_dl"
    content_dir = content_root / download_id_test
    content_dir.mkdir(parents=True, exist_ok=True)

    # Mock necessary utils if they aren't importable directly in this context
    # (Ideally, run tests in a proper package environment)
    try:
        from mcp_doc_retriever.utils import contains_all_keywords, extract_text_from_html_content

        logger.info("Using actual utils for searcher orchestration test.")
    except ImportError:
        logger.warning("Mocking utils for searcher orchestration test.")

        def contains_all_keywords(text: Optional[str], keywords: List[str]) -> bool:
            return True  # Simple mock

        def extract_text_from_html_content(content: str) -> Optional[str]:
            return content  # Simple mock

    try:
        # Create minimal test data
        file1 = content_dir / "search_test.html"
        file1.write_text(
            "<html><head><title>Search Test</title></head><body><p>This is a test for the main search orchestrator with keywords one and two.</p></body></html>",
            encoding="utf-8",
        )
        index_file = index_dir / f"{download_id_test}.jsonl"
        with index_file.open("w", encoding="utf-8") as f:
            rec = IndexRecord(
                original_url="http://example.com/search_test",
                canonical_url="http://example.com/search_test",
                local_path=str(
                    file1.relative_to(test_base_dir)
                ),  # Path relative to base
                fetch_status="success",
            )
            f.write(rec.model_dump_json(exclude_none=True) + "\n")
        logger.info("Minimal test data created.")

        # --- Execute Test Search ---
        logger.info("\n--- Running Orchestration Test ---")
        results = perform_search(
            download_id=download_id_test,
            scan_keywords=["orchestrator", "two"],  # Keywords present in file1
            selector="p",  # Extract paragraph
            base_dir=test_base_dir,  # MUST provide base_dir
        )

        # --- Verify Basic Outcome ---
        logger.info(f"Search finished. Found {len(results)} results.")
        if results:
            print("Sample Result:")
            print(f"  URL: {results[0].original_url}")
            print(f"  Selector: {results[0].selector_matched}")
            print(f"  Content: '{results[0].extracted_content[:80]}...'")
            assert len(results) == 1, "Expected exactly one result"
            assert "orchestrator" in results[0].extracted_content.lower()
            logger.info("Orchestration test PASSED.")
        else:
            logger.error("Orchestration test FAILED: No results found.")
            sys.exit(1)

    except Exception as e:
        logger.error(f"Error during searcher orchestration test: {e}", exc_info=True)
        sys.exit(1)
    finally:
        # Clean up test data
        logger.info(f"Cleaning up test directory: {test_base_dir}")
        if test_base_dir.exists():
            try:
                shutil.rmtree(test_base_dir)
                logger.info("Test directory cleaned up.")
            except Exception as e:
                logger.error(f"Failed to clean up test directory {test_base_dir}: {e}")
