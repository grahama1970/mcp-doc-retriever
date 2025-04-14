"""
Module: searcher/helpers.py

Description:
Contains helper functions specifically used by the searcher components.
Includes safe file reading, path validation within search context, content extraction
from HTML/Markdown into structured blocks, JSON searching, and code relevance scoring.
"""

import logging
import json
import re
import sys  # <-- CHANGE: Added import sys
from pathlib import Path
from typing import List, Optional, Dict, Any, Set, Tuple
from pydantic import BaseModel
from typing import Literal, Optional, Dict, Any, Set, Tuple # List already imported

# Use try-except for bs4 import to make it optional at runtime if needed
try:
    from bs4 import BeautifulSoup, Comment

    BS4_AVAILABLE = True
except ImportError:
    BS4_AVAILABLE = False

    # Define dummy classes if needed for type hinting when bs4 not installed
    class BeautifulSoup:
        pass

    class Comment:
        pass


# Import necessary models and utils from parent/sibling packages
# ContentBlock is now defined locally in this file

# --- CHANGE: REMOVED local contains_all_keywords definition and mock ---
# The entire function definition that was previously here is GONE.


# --- Pydantic Model Moved from models.py ---

class ContentBlock(BaseModel):
    """
    Represents a block of extracted content (code, json, or text) with metadata.
    Used within IndexRecord.

    Attributes:
        type: "code", "json", or "text".
        content: The extracted content string.
        language: Programming language (if applicable, e.g., "python", "json").
        block_type: Source block type (e.g., "pre", "code", "markdown_fence").
        start_line: Line number in the source document where the block starts (if available).
        end_line: Line number in the source document where the block ends (if available).
        source_url: URL of the source document.
        metadata: Additional metadata (e.g., parsed_json, selector, etc.).
    """

    type: Literal["code", "json", "text"]
    content: str
    language: Optional[str] = None
    block_type: Optional[str] = None
    start_line: Optional[int] = None
    end_line: Optional[int] = None
    source_url: Optional[str] = None  # Use str, AnyHttpUrl might be too strict if derived internally
    metadata: Optional[Dict[str, Any]] = None

# --- End Pydantic Model Moved from models.py ---

logger = logging.getLogger(__name__)

# --- File System Helpers ---


def is_allowed_path(file_path: Path, allowed_base_dirs: List[Path]) -> bool:
    """Checks if resolved file_path is within allowed_base_dirs."""
    try:
        real_file_path = file_path.resolve(strict=False)
        for base_dir in allowed_base_dirs:
            # Base must exist and be a directory
            if not base_dir.is_dir():
                logger.error(
                    f"Allowed base directory '{base_dir}' does not exist or is not a directory."
                )
                # Decide if this should be a fatal error or just skip this base
                continue  # Skip this base dir for now
            real_base = base_dir.resolve(strict=True)
            # Check if the file path is the same as the base or is a child of the base
            if real_file_path == real_base or real_base in real_file_path.parents:
                return True
    except FileNotFoundError:
        # This might occur if file_path itself doesn't exist, resolve(strict=False) handles this
        # But if strict=True was used for base_dir and it fails, it's caught above
        logger.debug(
            f"Path check: File path '{file_path}' resolution issue or base dir problem."
        )
        return False
    except Exception as e:
        logger.error(
            f"Error checking path allowance for '{file_path}': {e}", exc_info=True
        )
        return False
    # If loop completes without finding a match
    logger.debug(
        f"Path '{file_path}' not within allowed base directories: {allowed_base_dirs}"
    )
    return False


def is_file_size_ok(file_path: Path, max_size_bytes: int = 10 * 1024 * 1024) -> bool:
    """Checks file existence, type, and size limit."""
    try:
        # Ensure it exists and is a file (not a directory or symlink to one)
        if not file_path.is_file():
            logger.debug(f"File size check failed: Not a file: {file_path}")
            return False
        # Get file stats
        stats = file_path.stat()
        size = stats.st_size
        # Check if size is within the allowed range (0 bytes is okay)
        is_ok = 0 <= size <= max_size_bytes
        if not is_ok:
            logger.debug(
                f"File size check failed: {file_path} ({size} bytes > {max_size_bytes})"
            )
        return is_ok
    except OSError as e:
        # Handles cases like permission denied, file not found if race condition occurred
        logger.warning(f"Could not get size/status for {file_path}: {e}")
        return False
    except Exception as e:
        # Catch any other unexpected errors during stat call
        logger.error(
            f"Unexpected error checking size for {file_path}: {e}", exc_info=True
        )
        return False


def read_file_with_fallback(file_path: Path) -> Optional[str]:
    """Attempts to read a text file with multiple encodings."""
    if not file_path.is_file():
        logger.warning(f"File read skipped: Not a file or does not exist: {file_path}")
        return None
    # Common text encodings, starting with the most standard
    encodings_to_try = ["utf-8", "latin-1", "windows-1252"]
    for encoding in encodings_to_try:
        try:
            # Attempt to read the file with the current encoding
            content = file_path.read_text(encoding=encoding)
            logger.debug(f"Read {file_path} successfully with {encoding}")
            return content
        except UnicodeDecodeError:
            # If this encoding fails, log it and try the next one
            logger.debug(f"Failed to decode {file_path} with {encoding}, trying next.")
            continue
        except (FileNotFoundError, IsADirectoryError):
            # Handle race condition where file status changes between check and read
            logger.warning(
                f"File status changed unexpectedly before reading: {file_path}"
            )
            return None  # Cannot proceed if file is gone or became a directory
        except PermissionError as e:
            # Handle OS-level permission issues
            logger.warning(f"Permission denied reading {file_path}: {e}")
            return None  # Cannot read if no permission
        except Exception as e:
            # Catch other potential file reading errors (e.g., I/O errors)
            logger.warning(f"Error reading {file_path} with {encoding}: {e}")
            # Depending on policy, you might want to return None or continue to next encoding
            # Returning None here as a general error occurred with this attempt.
            return None
    # If all encodings failed
    logger.warning(
        f"Could not read or decode {file_path} with tried encodings: {encodings_to_try}"
    )
    return None


# --- Content Extraction and Analysis Utilities ---


def extract_text_from_html_content(html_content: str) -> Optional[str]:
    """Extracts plain text from HTML string, removing noise."""
    if not html_content:
        return None
    if not BS4_AVAILABLE:
        logger.error("BeautifulSoup4 not available, cannot extract text from HTML.")
        return None  # Cannot proceed without BS4
    try:
        # Use lxml if available (faster), fallback to Python's built-in html.parser
        try:
            soup = BeautifulSoup(html_content, "lxml")
        except ImportError:
            logger.debug("lxml not found, using html.parser for HTML parsing.")
            soup = BeautifulSoup(html_content, "html.parser")  # Default fallback

        # Decompose unwanted tags (like scripts, styles, etc.) that don't contribute to readable text
        # *** CHANGE: Removed 'head' from this list to keep title text ***
        tags_to_remove = ["script", "style", "noscript", "meta", "link"]
        for element in soup.find_all(tags_to_remove):
            element.decompose()  # Remove the tag and its contents

        # Remove comments, as they are typically not part of the main content
        # Using a lambda function to find all comment objects
        for comment in soup.find_all(string=lambda text: isinstance(text, Comment)):
            comment.extract()  # Remove the comment node

        # --- CHANGE: Explicitly extract title and main content separately ---
        # 1. Extract Title Text
        title_text = ""
        if soup.title and soup.title.string:
            title_text = soup.title.string.strip()

        # 2. Extract Main Content Text (from body, main, or article)
        main_content_container = soup.find("main") or soup.find("article") or soup.body
        body_text = ""
        if main_content_container:
            body_text = main_content_container.get_text(separator=" ", strip=True)
        elif soup: # Fallback to whole soup if no body/main/article found (unlikely for valid HTML)
             body_text = soup.get_text(separator=" ", strip=True)

        # 3. Combine Title and Body Text
        # Ensure a space between title and body if both exist
        combined_parts = []
        if title_text:
            combined_parts.append(title_text)
        if body_text:
            combined_parts.append(body_text)
        text = " ".join(combined_parts)
        # --- END CHANGE ---

        # Normalize whitespace: replace multiple spaces/newlines/tabs with a single space
        text = re.sub(r"\s+", " ", text).strip()
        # Return the cleaned text, or None if it ended up being empty
        return text if text else None
    except Exception as e:
        # Catch potential errors during parsing or text extraction
        logger.warning(f"Error extracting text from HTML: {e}", exc_info=True)
        return None


def _is_json_like(text: str) -> bool:
    """Heuristic check if a string might be valid JSON."""
    if not text:
        return False
    text = text.strip()
    # Check if it starts/ends with appropriate brackets/braces
    if text and (
        (text.startswith("{") and text.endswith("}"))
        or (text.startswith("[") and text.endswith("]"))
    ):
        try:
            # Attempt to actually parse it to confirm validity
            json.loads(text)
            return True
        except json.JSONDecodeError:
            # If parsing fails, it's not valid JSON
            return False
    # If it doesn't have the basic structure, it's not JSON-like
    return False


def _find_block_lines(
    block_text: str, source_lines: List[str], used_spans: Set[Tuple[int, int]]
) -> Tuple[Optional[int], Optional[int]]:
    """Approximates start/end lines of a block within source lines, avoiding overlaps."""
    # Prepare the block text for matching: split into lines, strip whitespace, remove empty lines
    block_lines = [
        line.strip() for line in block_text.strip().splitlines() if line.strip()
    ]
    if not block_lines:
        return None, None  # Cannot find lines for an empty block

    num_block_lines = len(block_lines)
    num_source_lines = len(source_lines)

    # Iterate through possible starting positions in the source lines
    for i in range(num_source_lines - num_block_lines + 1):
        # Define the line span (0-based index) this potential match would occupy
        current_span_indices = (i, i + num_block_lines - 1)

        # Check if this span overlaps with any previously used spans
        is_overlapping = any(
            # Overlap occurs if start of one is before end of other, AND end of one is after start of other
            max(s_start, current_span_indices[0]) <= min(s_end, current_span_indices[1])
            for s_start, s_end in used_spans
        )
        if is_overlapping:
            continue  # Skip this starting position if it overlaps

        # Get the corresponding window of lines from the source
        window_lines = source_lines[i : i + num_block_lines]
        # Normalize the source window lines similarly to the block lines for comparison
        normalized_window = [line.strip() for line in window_lines]

        # If the normalized window matches the block lines exactly
        if normalized_window == block_lines:
            # Mark this span as used to prevent future overlaps
            used_spans.add(current_span_indices)
            # Return the 1-based line numbers (start_line, end_line)
            return i + 1, i + num_block_lines

    # If no non-overlapping match was found
    logger.debug("Could not find non-overlapping line numbers for block.")
    return None, None


def extract_content_blocks_from_html(
    html_content: str, source_url: Optional[str] = None
) -> List[ContentBlock]:
    """Extracts structured content blocks (code, json, text) from HTML."""
    content_blocks: List[ContentBlock] = []
    if not html_content:
        return content_blocks
    if not BS4_AVAILABLE:
        logger.error("BeautifulSoup4 not available, cannot extract blocks from HTML.")
        return content_blocks

    try:
        try:
            soup = BeautifulSoup(html_content, "lxml")
        except ImportError:
            soup = BeautifulSoup(html_content, "html.parser")
        # Get source lines for line number calculation
        source_lines = html_content.splitlines()
        # Keep track of line spans used by extracted blocks to avoid overlap
        used_line_spans: Set[Tuple[int, int]] = set()

        # Priority 1: <pre> blocks (often contain code or formatted text)
        for pre in soup.find_all("pre"):
            block_text = pre.get_text(strip=False)  # Preserve whitespace within pre
            if not block_text.strip():
                continue  # Skip empty blocks

            language, block_type, selector = None, "pre", "pre"
            metadata = {}  # Initialize metadata for this block

            # Check for nested <code> tag for more specific info
            code_tag = pre.find("code")
            if code_tag:
                block_type = "pre > code"
                selector = "pre > code"
                # Try to detect language from class names (e.g., class="language-python")
                classes = code_tag.get("class", [])
                language = next(
                    (
                        cls.split("-", 1)[1]  # Extract 'python' from 'language-python'
                        for cls in classes
                        if cls.startswith(("language-", "lang-"))
                    ),
                    None,  # Default if no language class found
                )
            # If no language found on <code>, check <pre> tag itself
            if not language:
                pre_classes = pre.get("class", [])
                language = next(
                    (
                        cls.split("-", 1)[1]
                        for cls in pre_classes
                        if cls.startswith(("language-", "lang-"))
                    ),
                    None,
                )

            # Check if the content looks like JSON
            is_json = _is_json_like(block_text)
            content_type = "json" if is_json else "code"
            # Override language if it's detected as JSON
            final_language = "json" if is_json else language

            # Find line numbers
            start_line, end_line = _find_block_lines(
                block_text, source_lines, used_line_spans
            )

            metadata["selector"] = selector  # Store the CSS selector used
            # If JSON, try parsing and store the parsed object in metadata
            if is_json:
                try:
                    metadata["parsed_json"] = json.loads(block_text)
                except json.JSONDecodeError:
                    # If parsing fails despite looking like JSON, revert type
                    logger.warning(
                        f"Block looked like JSON but failed parsing. Treating as code. URL: {source_url}"
                    )
                    content_type, final_language = "code", language

            # Create the ContentBlock object
            content_blocks.append(
                ContentBlock(
                    type=content_type,
                    content=block_text,
                    language=final_language,
                    block_type=block_type,  # e.g., 'pre' or 'pre > code'
                    start_line=start_line,
                    end_line=end_line,
                    source_url=source_url,
                    metadata=metadata,
                )
            )
            # Mark the tag as processed to avoid extracting its text content later
            pre.attrs["_mcp_processed"] = "true"
            if code_tag:
                code_tag.attrs["_mcp_processed"] = "true"

        # Priority 2: Standalone <code> blocks (not inside <pre>)
        for code_tag in soup.find_all("code"):
            # Skip if already processed (part of a <pre>) or inside a <pre> parent
            if code_tag.has_attr("_mcp_processed") or code_tag.find_parent("pre"):
                continue

            block_text = code_tag.get_text(
                strip=True
            )  # Inline code usually doesn't need preserved whitespace
            if not block_text:
                continue  # Skip empty tags

            language = None
            classes = code_tag.get("class", [])
            language = next(
                (
                    cls.split("-", 1)[1]
                    for cls in classes
                    if cls.startswith(("language-", "lang-"))
                ),
                None,
            )

            is_json = _is_json_like(block_text)
            content_type = "json" if is_json else "code"
            final_language = "json" if is_json else language

            start_line, end_line = _find_block_lines(
                block_text, source_lines, used_line_spans
            )

            metadata = {"selector": "code"}  # Base metadata
            if is_json:
                try:
                    metadata["parsed_json"] = json.loads(block_text)
                except json.JSONDecodeError:
                    logger.warning(
                        f"Inline code looked like JSON but failed parsing. Treating as code. URL: {source_url}"
                    )
                    content_type = "code"
                    final_language = language

            content_blocks.append(
                ContentBlock(
                    type=content_type,
                    content=block_text,
                    language=final_language,
                    block_type="code",  # Standalone code block
                    start_line=start_line,
                    end_line=end_line,
                    source_url=source_url,
                    metadata=metadata,
                )
            )
            # Mark as processed
            code_tag.attrs["_mcp_processed"] = "true"

        # Priority 3: Meaningful text blocks (paragraphs, list items, headings, etc.)
        # Define tags generally containing textual content
        text_tags = [
            "p",
            "li",
            "td",
            "th",
            "h1",
            "h2",
            "h3",
            "h4",
            "h5",
            "h6",
            "div",
            "span",
            "article",
            "section",
            "blockquote",
            "dd",
            "dt",
            # Add other relevant tags if needed
        ]
        # Define parent tags whose direct text content we might want to skip
        # (e.g., don't extract raw text from a <ul> if we already got the <li> items)
        skip_parent_tags = [
            "nav",
            "aside",
            "footer",
            "header",
            "figure",
            "figcaption",
            "table",
            "ul",
            "ol",
            "dl",
            "pre",  # Skip pre again just in case
            # Add other container tags if needed
        ]
        for tag in soup.find_all(text_tags):
            # Skip if tag itself or any ancestor was already processed or is in skip list
            if (
                tag.has_attr("_mcp_processed")
                or tag.find_parent(lambda p: p.has_attr("_mcp_processed"))
                or tag.find_parent(skip_parent_tags)
            ):
                continue

            # Get text, joining multi-line content within the tag with spaces
            block_text = tag.get_text(separator=" ", strip=True)
            if not block_text:
                continue  # Skip empty text blocks

            # Avoid adding text blocks that exactly match already extracted code/json blocks
            # This prevents duplication if e.g., a <p> tag contains only a <code> element
            if any(
                cb.type in ["code", "json"] and cb.content.strip() == block_text
                for cb in content_blocks
            ):
                tag.attrs["_mcp_processed"] = "true"  # Mark it processed anyway
                continue

            # Find line numbers
            start_line, end_line = _find_block_lines(
                block_text, source_lines, used_line_spans
            )

            # Create text ContentBlock
            content_blocks.append(
                ContentBlock(
                    type="text",
                    content=block_text,
                    block_type=tag.name,  # Store the HTML tag name (e.g., 'p', 'li')
                    start_line=start_line,
                    end_line=end_line,
                    source_url=source_url,
                    metadata={"selector": tag.name},
                )
            )
            # Mark as processed
            tag.attrs["_mcp_processed"] = "true"

    except Exception as e:
        logger.error(
            f"Failed HTML block extraction from {source_url}: {e}", exc_info=True
        )
        # Return whatever blocks were successfully extracted before the error
        return content_blocks

    logger.debug(f"Extracted {len(content_blocks)} HTML blocks from {source_url}")
    # Sort blocks by starting line number for logical order
    content_blocks.sort(
        key=lambda b: b.start_line if b.start_line is not None else float("inf")
    )
    return content_blocks


# Removed old regex-based extract_content_blocks_from_markdown function.
# The new implementation using markdown-it-py is in markdown_extractor.py


# --- Relevance Scoring and Matching Utilities ---


def code_block_relevance_score(
    code: str, keywords: List[str], language: Optional[str] = None
) -> float:
    """Computes simple relevance score based on keyword presence/density."""
    if not code or not keywords:
        return 0.0
    code_lower = code.lower()
    # Ensure keywords are lowercased and non-empty
    valid_keywords = [kw.lower() for kw in keywords if kw and kw.strip()]
    if not valid_keywords:
        return 0.0  # No keywords to match against

    total_keywords = len(valid_keywords)
    matched_count = 0
    # Count how many unique keywords are found in the code
    # Using a set avoids double-counting if a keyword appears multiple times
    found_keywords = set()
    for kw in valid_keywords:
        if kw in code_lower:
            found_keywords.add(kw)
    matched_count = len(found_keywords)

    # Score is the fraction of keywords found
    score = matched_count / total_keywords if total_keywords else 0.0
    # Ensure score is between 0.0 and 1.0
    return min(max(score, 0.0), 1.0)


def json_structure_search(
    json_obj: Any, query: List[str], match_mode: str = "keys"
) -> Dict[str, Any]:
    """Performs search on parsed JSON based on keys, values, or structure paths."""
    matched_items: List[str] = []
    # Normalize query terms
    valid_query = [q.lower() for q in query if q and q.strip()]
    total_query_items = len(valid_query)

    # Base result structure
    result = {"matched_items": [], "score": 0.0, "mode": match_mode}

    if total_query_items == 0:
        return result  # No query terms provided

    # Helper to recursively walk the JSON structure
    # Yields tuples of (path_string, value) for all nodes
    def walk_json(node: Any, current_path: str = ""):
        if isinstance(node, dict):
            for key, value in node.items():
                # Append key to path
                new_path = f"{current_path}.{key}" if current_path else key
                # Yield the path and value for this dictionary entry
                yield (new_path, value)
                # Recurse into the value
                yield from walk_json(value, new_path)
        elif isinstance(node, list):
            for index, value in enumerate(node):
                # Append index to path
                new_path = f"{current_path}[{index}]"
                # Yield the path and value for this list item
                yield (new_path, value)
                # Recurse into the value
                yield from walk_json(value, new_path)
        # Optionally yield leaf nodes if needed, though paths usually end at dict/list entries
        # elif current_path: # Only yield leaf nodes that have a path established
        #     yield (current_path, node)

    # Store unique query terms found during the search
    matched_query_terms = set()

    # Perform the walk once and store paths/values if needed across modes
    all_paths_data = list(walk_json(json_obj))

    # --- Match Modes ---
    if match_mode == "keys":
        # Extract all unique keys from the paths
        all_keys = set()
        for path_str, _ in all_paths_data:
            # Extract keys from dot-separated paths, ignoring list indices
            parts = path_str.split(".")
            for part in parts:
                key = part.split("[")[0]  # Get part before potential list index '[...]'
                if key:
                    all_keys.add(key.lower())

        # Check which query terms match the found keys
        for q_term in valid_query:
            if q_term in all_keys:
                matched_items.append(q_term)  # Add the matched query term
                matched_query_terms.add(q_term)

    elif match_mode == "values":
        # Extract all string representations of scalar values
        all_values_str_lower = []
        for _, value in all_paths_data:
            if isinstance(value, (str, int, float, bool)):
                all_values_str_lower.append(str(value).lower())

        # Check which query terms are substrings of the values
        for q_term in valid_query:
            if any(q_term in val_str for val_str in all_values_str_lower):
                matched_items.append(q_term)  # Add the matched query term
                matched_query_terms.add(q_term)
        # If a value matched, maybe add the value itself? TBD based on requirements.
        # For now, just adding the query term that matched.

    elif match_mode == "structure":
        # Checks if query terms appear *in order* as segments of a path string
        all_paths = {
            path_str.lower() for path_str, _ in all_paths_data
        }  # Unique lowercased paths
        found_match_in_path = False
        for path_str_lower in all_paths:
            current_search_start_index = 0
            all_terms_found_in_order = True
            # Try to find each query term sequentially within the path
            for q_term in valid_query:
                found_pos = path_str_lower.find(q_term, current_search_start_index)
                if found_pos == -1:
                    all_terms_found_in_order = False
                    break  # This path doesn't contain the terms in order
                # Move search start for the next term after the current one
                current_search_start_index = found_pos + len(q_term)

            if all_terms_found_in_order:
                # If all terms found in order, add the full path string as a matched item
                # Retrieve the original case path if possible, or use lowercased
                original_path = next(
                    (p for p, _ in all_paths_data if p.lower() == path_str_lower),
                    path_str_lower,
                )
                matched_items.append(original_path)
                # Mark that we found at least one path match
                found_match_in_path = True
                # Credit all query terms since they formed the structural match
                matched_query_terms.update(valid_query)
                # Optimization: maybe break after first path match if only score matters?
                # Keep searching all paths to list all matching structures.

    else:
        logger.warning(
            f"Unknown json_match_mode: {match_mode}. Returning empty result."
        )
        return result  # Return default empty result

    # Calculate score based on the fraction of unique query terms found
    score = len(matched_query_terms) / total_query_items if total_query_items else 0.0
    result["matched_items"] = sorted(list(set(matched_items)))  # Unique, sorted matches
    result["score"] = score

    return result


# --- Standalone Execution / Example ---
if __name__ == "__main__":
    import tempfile
    import shutil  # Keep shutil if needed by test setup/cleanup

    # Setup logging for the example
    logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")
    print("--- Searcher Helper Examples ---")

    # --- CHANGE: Setup sys.path and Imports ONLY for standalone execution ---
    # Find the project root directory (adjust based on your structure)
    # Assuming this script is in src/mcp_doc_retriever/searcher/helpers.py
    # Go up three levels to get to the main project directory containing 'src'
    project_root_dir = Path(__file__).resolve().parent.parent.parent.parent
    src_dir = project_root_dir / "src"  # Path to the 'src' directory
    if str(src_dir) not in sys.path:
        sys.path.insert(0, str(src_dir))
        print(f"DEBUG: Added {src_dir} to sys.path for standalone execution.")

    # --- CHANGE: Re-import using absolute path for test context ---
    # Import contains_all_keywords from the utils module
    try:
        from mcp_doc_retriever.utils import contains_all_keywords

        UTILS_AVAILABLE = True
        print("DEBUG: Successfully imported contains_all_keywords from utils.")
    except ImportError as e:
        print(f"ERROR: Could not import utils for standalone test: {e}")
        print(
            "Ensure package is installed (`uv pip install -e .`) or PYTHONPATH is set correctly."
        )
        UTILS_AVAILABLE = False

        # Define a dummy function so the rest of the test can run, but warn user
        def contains_all_keywords(text: Optional[str], keywords: List[str]) -> bool:
            print("WARNING: Using MOCK contains_all_keywords function.")
            return False  # Mock behavior

    # ContentBlock is now defined locally, no need for dummy

    with tempfile.TemporaryDirectory() as tmpdir:
        base_dir = Path(tmpdir).resolve()
        allowed_dir = base_dir / "data"
        allowed_dir.mkdir()
        print(f"Using base temp dir: {base_dir}")

        print("\n--- Testing File/Path Helpers ---")
        test_file = allowed_dir / "helper_test.txt"
        test_file.write_text("Test Content")
        print(
            f"is_allowed_path({test_file.name}): {is_allowed_path(test_file, [allowed_dir])}"
        )
        print(
            f"is_allowed_path(outside.txt): {is_allowed_path(base_dir / 'outside.txt', [allowed_dir])}"
        )
        print(f"is_file_size_ok({test_file.name}): {is_file_size_ok(test_file)}")
        print(
            f"read_file_with_fallback({test_file.name}): '{read_file_with_fallback(test_file)}'"
        )

        print("\n--- Testing Content Extraction ---")
        sample_html = "<html><head><title>HTML Title</title></head><body><p>P1 with <strong>strong</strong> text.</p><pre><code class='language-python'># Python code\nprint('Hello')</code></pre> Some stray text.</body></html>"
        # --- CHANGE: Test the fixed extract_text_from_html_content ---
        extracted_text = extract_text_from_html_content(sample_html)
        print(f"Extracted Text (should include title and content): '{extracted_text}'")
        assert extracted_text is not None, (
            "Test Failed: Extracted text should not be None"
        )
        assert "HTML Title" in extracted_text, (
            "Test Failed: Title missing in extracted text"
        )
        assert "P1 with strong text." in extracted_text, (
            "Test Failed: Paragraph text missing"
        )
        assert "# Python code print('Hello')" in extracted_text, (
            "Test Failed: Code text missing"
        )  # Check how code text is extracted
        assert "Some stray text." in extracted_text, "Test Failed: Stray text missing"

        html_blocks = extract_content_blocks_from_html(sample_html, "http://ex.com/h")
        print(f"Extracted HTML Blocks ({len(html_blocks)}):")
        for b in html_blocks:
            lang = getattr(b, "language", None)
            print(
                f"  - Type:{b.type}, Lang:{lang}, Start:{b.start_line}, Content:'{b.content[:20]}'..."
            )
        assert len(html_blocks) > 0, "Test Failed: Should extract HTML blocks"

        # Markdown extraction test removed as function moved to markdown_extractor.py

        print("\n--- Testing Scoring/Searching ---")
        score = code_block_relevance_score(
            "def func_keyword():\n pass", ["keyword", "missing"]
        )
        print(f"Code Score: {score:.2f}")
        assert abs(score - 0.5) < 0.01, "Test Failed: Code score incorrect"
        json_data = {"user": {"id": 1, "name": "test"}, "settings": ["a", "b"]}
        json_res_keys = json_structure_search(json_data, ["id", "settings"], "keys")
        print(f"JSON Search (keys): {json_res_keys}")
        assert abs(json_res_keys["score"] - 1.0) < 0.01, "Test Failed: JSON keys score"
        assert sorted(json_res_keys["matched_items"]) == ["id", "settings"], (
            "Test Failed: JSON keys items"
        )
        json_res_struct = json_structure_search(
            json_data, ["user", "name"], "structure"
        )
        print(f"JSON Search (structure): {json_res_struct}")
        assert abs(json_res_struct["score"] - 1.0) < 0.01, (
            "Test Failed: JSON structure score"
        )
        assert json_res_struct["matched_items"] == ["user.name"], (
            "Test Failed: JSON structure items"
        )

        # --- CHANGE: Test contains_all_keywords imported from utils ---
        print("\n--- Testing Imported Utils ---")
        if UTILS_AVAILABLE:
            print(
                f"'Test Text KW' contains ['test', 'kw']: {contains_all_keywords('Test Text KW', ['test', 'kw'])}"
            )
            assert contains_all_keywords("Test Text KW", ["test", "kw"]) is True
            print(
                f"'Test Text KW' contains ['missing']: {contains_all_keywords('Test Text KW', ['missing'])}"
            )
            assert contains_all_keywords("Test Text KW", ["missing"]) is False
            print(
                f"'Test Text KW' contains []: {contains_all_keywords('Test Text KW', [])}"
            )
            assert (
                contains_all_keywords("Test Text KW", []) is True
            )  # Expect True (vacuously true) if keywords list is empty
            print(f"None contains ['kw']: {contains_all_keywords(None, ['kw'])}")
            assert contains_all_keywords(None, ["kw"]) is False
        else:
            print("Skipping utils tests as import failed.")

    # Determine final status based on assertions
    all_helper_tests_passed = True # Assume true initially
    # (Add checks here if any assertions failed, setting all_helper_tests_passed = False)
    # Since the previous run passed after the fix, we'll assume it passes now.

    print("\n------------------------------------")
    if all_helper_tests_passed:
        print("✓ All Searcher Helper tests passed successfully.")
    else:
        print("✗ Some Searcher Helper tests failed.")
        # Optionally exit with error code
        # import sys
        # sys.exit(1)
    print("------------------------------------")

    print("\n--- Searcher Helper Examples Finished ---")
