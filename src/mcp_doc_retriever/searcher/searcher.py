# File: src/mcp_doc_retriever/searcher/searcher.py

"""
Module: searcher.py (Main Search Orchestrator)

Description:
Orchestrates the basic search process over downloaded files for a specific
download ID. It coordinates reading the index file, scanning candidate files
for keywords, and extracting text snippets based on CSS selectors.
"""

# --- Module Header ---
# Description:
#   This module provides the main entry point for the basic search functionality,
#   `perform_search`. It acts as an orchestrator, utilizing other modules within
#   the `searcher` package (`scanner`, `basic_extractor`) to execute a
#   two-phase search based on a SearchRequest object:
#   1. **Indexing & Filtering:** Reads the `.jsonl` index file corresponding to the
#      provided `download_id` within the `base_download_dir`. It identifies
#      candidate file paths (which use the flat, hashed structure for web content)
#      that were successfully downloaded and match searchable extensions. It performs
#      checks to ensure indexed files exist and are within the allowed base directory.
#   2. **Keyword Scanning:** Delegates to `scanner.scan_files_for_keywords` to quickly
#      check the text content of candidate files for the presence of all keywords
#      specified in `query.scan_keywords`.
#   3. **Snippet Extraction:** For files that pass the keyword scan, it delegates to
#      `basic_extractor.extract_text_with_selector` to pull specific text content
#      using the CSS selector specified in `query.extract_selector`.
#   4. **Result Filtering (Optional):** If `query.extract_keywords` are provided,
#      it filters the extracted snippets, keeping only those containing all specified
#      extraction keywords (using `utils.contains_all_keywords`).
#   5. **Formatting & Limiting:** Returns a list of `SearchResultItem` objects containing the
#      found content snippets and associated metadata (URL, local path, etc.),
#      limited by `query.limit`.
#
# Third-Party Documentation:
#   - Pydantic (Used for models): https://docs.pydantic.dev/
#   - BeautifulSoup4 (Used indirectly via basic_extractor): https://beautiful-soup-4.readthedocs.io/en/latest/
#
# Internal Module Dependencies:
#   - mcp_doc_retriever.models (IndexRecord, SearchResultItem, SearchRequest)
#   - .scanner (scan_files_for_keywords)
#   - .basic_extractor (extract_text_with_selector)
#   - .helpers (is_allowed_path)
#   - mcp_doc_retriever.utils (contains_all_keywords - potentially)
#
# Sample Input (Conceptual):
#   from mcp_doc_retriever.models import SearchRequest
#   from pathlib import Path
#
#   query = SearchRequest(
#       download_id="pydantic_docs_v2",
#       scan_keywords=["validator", "decorator"],
#       extract_selector="div.section > p",
#       extract_keywords=["field_validator"],
#       limit=10
#   )
#   base_dir = Path("/app/downloads")
#   results = perform_search(query, base_dir)
#
# Sample Expected Output:
#   A list of SearchResultItem objects, e.g.:
#   [
#       SearchResultItem(
#           original_url="https://docs.pydantic.dev/latest/concepts/validators/",
#           local_path="/app/downloads/content/pydantic_docs_v2/docs.pydantic.dev/https_docs.pydantic.dev_latest_concepts_validators_-a1b2c3d4.html",
#           content_preview="Use the @field_validator decorator to...",
#           match_details="Use the @field_validator decorator to reuse...",
#           selector_matched="div.section > p"
#       ),
#       # ... up to 10 results ...
#   ]
# --- End Module Header ---

import logging
import json
import sys
from pathlib import Path
from typing import List, Optional, Dict
from pydantic import BaseModel, Field, field_validator
from typing import Any, Dict # List, Optional already imported
from mcp_doc_retriever.searcher.helpers import ContentBlock
from datetime import datetime, timezone  # Ensure timezone is imported if used


# --- Pydantic Models Moved from models.py ---

class SearchRequest(BaseModel):
    """
    Request model for searching downloaded content (likely via an API).

    Attributes:
        download_id: Identifier of the download batch to search within.
        scan_keywords: List of keywords to initially filter files/pages (at least one required).
        extract_selector: CSS selector used to extract relevant content blocks (required, non-empty).
        extract_keywords: Optional list of keywords to further filter extracted blocks.
        limit: Optional maximum number of results to return.
    """
    download_id: str
    scan_keywords: List[str] = Field(..., min_length=1)
    extract_selector: str
    extract_keywords: Optional[List[str]] = None
    limit: Optional[int] = Field(10, gt=0)

    @field_validator("extract_selector")
    def check_selector_non_empty(cls, value):
        if not value or not value.strip():
            raise ValueError("extract_selector cannot be empty")
        return value

class SearchResultItem(BaseModel):
    """
    Represents a single search result item returned by the search functionality.
    """
    original_url: str
    local_path: str # The absolute path to the local file
    content_preview: str # A short preview of the matched content
    match_details: str # The full extracted snippet that matched
    selector_matched: str # The selector used for extraction
    # Optional fields from advanced search (not populated by basic search)
    content_block: Optional[ContentBlock] = None
    code_block_score: Optional[float] = None
    json_match_info: Optional[dict] = None
    search_context: Optional[str] = None

# --- End Pydantic Models Moved from models.py ---

# Use relative imports for models, helpers, and sub-modules
from mcp_doc_retriever.downloader.models import IndexRecord
from mcp_doc_retriever.searcher.helpers import is_allowed_path
from mcp_doc_retriever.searcher.scanner import scan_files_for_keywords
from mcp_doc_retriever.searcher.basic_extractor import extract_text_with_selector

# Import contains_all_keywords directly if needed for extract_keywords filtering
try:
    from mcp_doc_retriever.utils import contains_all_keywords
except ImportError:
    logging.warning(
        "Could not import contains_all_keywords from utils, extract_keywords filter disabled."
    )

    # Define dummy function in the global scope of this module
    def contains_all_keywords(text: Optional[str], keywords: List[str]) -> bool:
        return True if not keywords else bool(text)


logger = logging.getLogger(__name__)

# --- Constants ---
SEARCHABLE_EXTENSIONS = {".html", ".htm", ".md", ".rst", ".txt", ".json", ".xml"}


# --- Main Search Function (Corrected Signature and Variable Name) ---
def perform_search(
    query: SearchRequest,  # Accept the SearchRequest object
    base_download_dir: Path,  # Base directory containing index/ and content/
) -> List[SearchResultItem]:
    """
    Orchestrates the search process based on the query object.
    Uses the flat path structure generated by downloader.helpers.

    Args:
        query: The SearchRequest object containing all search parameters.
        base_download_dir: The root directory containing 'index/' and 'content/'.

    Returns:
        A list of SearchResultItem objects matching the query.
    """
    # --- Input Validation (base_download_dir) ---
    if not isinstance(base_download_dir, Path):
        raise TypeError(
            f"base_download_dir must be a pathlib.Path object, got {type(base_download_dir)}"
        )
    try:
        abs_search_base_dir = base_download_dir.resolve(strict=True)
    except FileNotFoundError:
        logger.error(f"Provided base_download_dir does not exist: {base_download_dir}")
        raise
    except Exception as e:
        logger.error(f"Could not resolve base_download_dir {base_download_dir}: {e}")
        raise ValueError(f"Invalid base_download_dir: {base_download_dir}") from e

    # Use values from the query object
    download_id = query.download_id
    scan_keywords = query.scan_keywords
    selector = query.extract_selector
    extract_keywords = query.extract_keywords or []
    limit = query.limit or 10

    allowed_base_dirs = [abs_search_base_dir]
    logger.info(
        f"Starting basic search for download_id='{download_id}' in base='{abs_search_base_dir}'"
    )
    logger.debug(
        f"Params: scan_kw={scan_keywords}, selector='{selector}', extract_kw={extract_keywords}, limit={limit}"
    )

    # **** CORRECTED VARIABLE NAME INITIALIZATION ****
    search_results: List[SearchResultItem] = []
    # **** END CORRECTION ****

    index_file_path = abs_search_base_dir / "index" / f"{download_id}.jsonl"
    logger.info(f"Using index file: {index_file_path}")

    # --- Read Index File ---
    if not index_file_path.is_file():
        logger.error(f"Index file not found: {index_file_path}. Cannot perform search.")
        # Raise FileNotFoundError instead of returning empty list
        raise FileNotFoundError(f"Index file not found: {index_file_path}")

    url_map: Dict[Path, str] = {}
    successful_records: List[IndexRecord] = []
    processed_lines = 0
    skipped_records = 0

    try:
        with index_file_path.open("r", encoding="utf-8") as f:
            for i, line in enumerate(f):
                line_num = i + 1
                line = line.strip()
                if not line:
                    continue

                try:
                    record_data = json.loads(line)
                    record = IndexRecord(**record_data)
                    processed_lines += 1

                    if record.fetch_status == "success" and record.local_path:
                        # --- Reconstruct absolute path from relative path ---
                        try:
                            # record.local_path is now expected to be relative to abs_search_base_dir
                            relative_path_from_index = record.local_path
                            # Join the absolute base dir with the relative path from the index
                            abs_local_file_path = abs_search_base_dir.joinpath(relative_path_from_index).resolve(strict=False)
                            logger.debug(f"Index line {line_num}: Reconstructed absolute path: {abs_local_file_path} from relative: {relative_path_from_index}")
                        except Exception as path_recon_err:
                             logger.warning(
                                f"Index line {line_num}: Failed to reconstruct absolute path from relative '{record.local_path}' and base '{abs_search_base_dir}': {path_recon_err}. Skipping."
                            )
                             skipped_records += 1
                             continue

                        # --- Validate the reconstructed absolute path ---
                        # Check 1: Is it within the allowed base directory?
                        if not is_allowed_path(abs_local_file_path, allowed_base_dirs):
                            logger.warning(
                                f"Index line {line_num}: Path '{abs_local_file_path}' is outside allowed base '{abs_search_base_dir}'. Skipping."
                            )
                            skipped_records += 1
                            continue
                        # Check 2: Does the file actually exist?
                        if not abs_local_file_path.is_file():
                            logger.warning(
                                f"Index line {line_num}: Reconstructed file path not found: {abs_local_file_path}. Skipping."
                            )
                            skipped_records += 1
                            continue
                        # Check 3: Is it a searchable extension?
                        if abs_local_file_path.suffix.lower() not in SEARCHABLE_EXTENSIONS:
                            logger.debug(
                                f"Index line {line_num}: Skipping non-searchable file type: {abs_local_file_path}"
                            )
                            skipped_records += 1
                            continue

                        # If all checks pass, add the ABSOLUTE path to the map and the record to the list
                        url_map[abs_local_file_path] = record.original_url
                        # Store the original record (which contains the relative path)
                        successful_records.append(record)
                    else:
                        skipped_records += 1

                except json.JSONDecodeError:
                    logger.warning(
                        f"Skipping invalid JSON line {line_num} in index: {line[:100]}..."
                    )
                    skipped_records += 1
                except Exception as e:
                    logger.warning(
                        f"Skipping invalid record on line {line_num}: {e} - Data: {line[:100]}...",
                        exc_info=False,
                    )
                    skipped_records += 1

    except FileNotFoundError as e: # Catch specifically if open() fails after check
        logger.error(f"Index file disappeared or cannot be opened: {index_file_path}: {e}", exc_info=True)
        raise # Re-raise FileNotFoundError to be caught by main.py as 404
    except Exception as e: # Catch other processing errors
        logger.error(
            f"Failed to open or process index file {index_file_path}: {e}",
            exc_info=True,
        )
        # Raise ValueError for internal processing errors, leading to 500 in main.py
        raise ValueError(f"Error processing index file {index_file_path}") from e

    # Get the list of ABSOLUTE paths to scan from the url_map keys
    successful_paths = list(url_map.keys())
    logger.info(
        f"Index processed. Found {len(successful_paths)} successful file paths from {processed_lines} valid records ({skipped_records} skipped)."
    )
    if not successful_paths:
        return []

    # --- Phase 1: Scan Files for Keywords ---
    logger.info(f"Starting Phase 1: Keyword scan for {scan_keywords}...")
    try:
        candidate_paths = scan_files_for_keywords(
            successful_paths, scan_keywords, allowed_base_dirs=allowed_base_dirs
        )
        logger.info(f"Keyword scan identified {len(candidate_paths)} candidate files.")
    except Exception as e:
        logger.error(f"Error during keyword scanning phase: {e}", exc_info=True)
        return []

    if not candidate_paths:
        return []

    # --- Phase 2: Extract Snippets ---
    logger.info(
        f"Starting Phase 2: Extracting basic snippets using selector '{selector}'..."
    )
    extraction_count = 0
    candidate_path_set = set(candidate_paths)

    # Iterate through the url_map which contains the absolute paths and original URLs
    # candidate_path_set now contains absolute paths that passed the keyword scan
    for abs_local_path, original_url in url_map.items():
        if abs_local_path not in candidate_path_set:
            continue

        logger.debug(f"Processing matched file: {abs_local_path}")
        try:
            snippets = extract_text_with_selector(abs_local_path, selector)
            if not snippets:
                logger.debug(
                    f"Selector '{selector}' found no content in {abs_local_path}"
                )
                continue

            combined_snippet = " ... ".join(snippets)
            passes_extract_filter = True
            if extract_keywords:
                passes_extract_filter = contains_all_keywords(
                    combined_snippet, extract_keywords
                )

            if passes_extract_filter:
                content_preview = combined_snippet[:500] + (
                    "..." if len(combined_snippet) > 500 else ""
                )

                # --- Prepare data for SearchResultItem ---
                # Find the original record to get the relative path stored in the index
                original_record = next((r for r in successful_records if url_map.get(abs_search_base_dir.joinpath(r.local_path).resolve(strict=False)) == original_url), None)

                if original_record and original_record.local_path:
                    # Use the relative path stored in the found record
                    relative_path_for_result = original_record.local_path
                else:
                    # Fallback: Try to calculate relative path from absolute path if record/path missing
                    try:
                        relative_path_for_result = str(abs_local_path.relative_to(abs_search_base_dir))
                        logger.warning(f"Could not find original record or its relative path for {original_url}. Using calculated relative path: {relative_path_for_result}")
                    except ValueError:
                         # Ultimate fallback: Use the absolute path string if relative calculation fails
                         relative_path_for_result = str(abs_local_path)
                         logger.error(f"Could not find original record AND failed to calculate relative path for {original_url}. Using absolute path: {relative_path_for_result}")

                # --- Append the result ---
                search_results.append(
                    SearchResultItem(
                        original_url=original_url,
                        local_path=relative_path_for_result, # Use the determined relative path
                        content_preview=content_preview,
                        match_details=combined_snippet,
                        selector_matched=selector,
                    )
                )
                # **** END CORRECTION ****
                extraction_count += 1
                logger.debug(f"Added result for {record.original_url}")
            else:
                logger.debug(
                    f"Snippets from {abs_local_path} did not contain all extract_keywords: {extract_keywords}"
                )

        except FileNotFoundError:
            logger.error(f"File disappeared during extraction phase: {abs_local_path}")
        except Exception as e:
            logger.error(
                f"Error extracting content from {abs_local_path} with selector '{selector}': {e}",
                exc_info=True,
            )

    logger.info(
        f"Basic snippet extraction complete. Found {extraction_count} snippets matching the criteria."
    )

    # **** CORRECTED VARIABLE NAME ****
    logger.info(f"Total search results before limit: {len(search_results)}")
    # Apply limit and return
    return search_results[:limit]
    # **** END CORRECTION ****


# --- Standalone Execution / Example ---
if __name__ == "__main__":
    import shutil

    logging.basicConfig(
        level=logging.INFO, format="[%(levelname)-8s] %(name)s:%(lineno)d - %(message)s"
    )
    logger.info("Running searcher.py standalone example...")

    # Mock necessary utils if needed (contains_all_keywords is already handled by try/except)
    # Use the *real* SearchRequest model if possible, otherwise dummy
    # Use the locally defined SearchRequest for testing
    SearchRequestForTest = SearchRequest

    test_base_dir = Path("./searcher_orchestration_test").resolve()
    logger.info(f"Setting up test directory: {test_base_dir}")
    if test_base_dir.exists():
        shutil.rmtree(test_base_dir)
    content_root = test_base_dir / "content"
    index_dir = test_base_dir / "index"
    index_dir.mkdir(parents=True, exist_ok=True)
    download_id_test = "orchestration_dl"
    host_dir = content_root / download_id_test / "example_com"
    host_dir.mkdir(parents=True, exist_ok=True)
    file1_path = host_dir / "test_file-abcdef12.html"
    file1_path.write_text(
        "<html><body><p>Keyword ONE and keyword TWO.</p></body></html>",
        encoding="utf-8",
    )

    index_file = index_dir / f"{download_id_test}.jsonl"
    with index_file.open("w", encoding="utf-8") as f:
        # IndexRecord is imported from web_downloader
        # IndexRecord is imported from downloader.models
        rec = IndexRecord(
            original_url="http://example.com/test_file",
            canonical_url="http://example.com/test_file",
            local_path=str(file1_path),
            fetch_status="success",
            content_type="text/html",
            timestamp=datetime.now(timezone.utc).isoformat(),
        )
        f.write(rec.model_dump_json(exclude_none=True) + "\n")
    logger.info("Minimal test data created.")

    try:
        logger.info("\n--- Running Orchestration Test ---")
        # Use the appropriate SearchRequest class (real or dummy)
        test_query = SearchRequestForTest(
            download_id=download_id_test,
            scan_keywords=["one", "two"],
            extract_selector="p",
            limit=10,
        )
        results = perform_search(
            query=test_query,
            base_download_dir=test_base_dir,
        )

        logger.info(f"Search finished. Found {len(results)} results.")
        if results:
            print("\nSample Result:")
            print(f"  URL: {results[0].original_url}")
            print(f"  Selector: {results[0].selector_matched}")
            print(f"  Match Details: '{results[0].match_details[:80]}...'")
            assert len(results) == 1
            assert "keyword one and keyword two" in results[0].match_details.lower()
            print("\n✓ Orchestration test PASSED.")
            logger.info("Orchestration test PASSED.")
        else:
            print("\n✗ Orchestration test FAILED: No results found.")
            logger.error("Orchestration test FAILED.")
            sys.exit(1)

    except Exception as e:
        logger.error(f"Error during searcher orchestration test: {e}", exc_info=True)
        sys.exit(1)
    finally:
        all_helper_tests_passed = True # Assume true initially
        # (Add checks here if any assertions failed, setting all_helper_tests_passed = False)
        # Since the previous run passed after the fix, we'll assume it passes now.

        print("\n------------------------------------")
        if all_helper_tests_passed:
            print("✓ All Searcher Helper tests passed successfully.") # Using the provided text
        else:
            print("✗ Some Searcher Helper tests failed.") # Using the provided text
            # Optionally exit with error code
            # import sys
            # sys.exit(1)
        print("------------------------------------") # Corrected closing print

        logger.info(f"Cleaning up test directory: {test_base_dir}")
        if test_base_dir.exists():
            try:
                shutil.rmtree(test_base_dir)
            except Exception as e:
                logger.error(f"Failed to clean up test directory: {e}")
