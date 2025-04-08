import logging
import os
from bs4 import BeautifulSoup


def is_allowed_path(file_path: str, allowed_base_dirs: list[str]) -> bool:
    """
    Check if the file_path is within any of the allowed_base_dirs.

    Returns:
        bool: True if allowed, False otherwise.
    """
    real_file_path = os.path.realpath(file_path)
    for base_dir in allowed_base_dirs:
        real_base = os.path.realpath(base_dir)
        try:
            common = os.path.commonpath([real_file_path, real_base])
            if common == real_base:
                return True
        except ValueError:
            # On different drives or invalid paths
            continue
    return False


def is_file_size_ok(file_path: str, max_size_bytes: int = 10 * 1024 * 1024) -> bool:
    """
    Check if the file size is within the allowed limit.

    Returns:
        bool: True if size is acceptable, False otherwise.
    """
    try:
        size = os.path.getsize(file_path)
        return size <= max_size_bytes
    except OSError as e:
        logging.warning(f"Could not get size for {file_path}: {e}")
        return False


def read_file_with_fallback(file_path: str) -> str | None:
    """
    Attempt to read a file with multiple encodings.

    Returns:
        str | None: File content if successful, None otherwise.
    """
    for encoding in ('utf-8', 'latin-1'):
        try:
            with open(file_path, 'r', encoding=encoding) as f:
                return f.read()
        except FileNotFoundError:
            logging.warning(f"File not found: {file_path}")
            return None
        except UnicodeDecodeError:
            continue  # Try next encoding
        except Exception as e:
            logging.warning(f"Error reading {file_path}: {e}")
            return None
    return None


def extract_text_from_html(content: str) -> str | None:
    """
    Extract plain text from HTML content.

    Returns:
        str | None: Extracted text in lowercase, or None on failure.
    """
    try:
        # Note: BeautifulSoup does not support limiting parse depth or time.
        # To mitigate resource exhaustion, consider switching to a parser like lxml with limits,
        # but this is a known limitation for now.
        soup = BeautifulSoup(content, 'html.parser')
        return soup.get_text(separator=' ').lower()
    except Exception as e:
        logging.warning(f"Error parsing HTML content: {e}")
        return None


def contains_all_keywords(text: str, keywords: list[str]) -> bool:
    """
    Check if all keywords are present in the text.

    Args:
        text (str): The text to search.
        keywords (list[str]): List of lowercase keywords.

    Returns:
        bool: True if all keywords found, False otherwise.
    """
    return all(keyword in text for keyword in keywords)


def scan_files_for_keywords(
    file_paths: list[str],
    scan_keywords: list[str],
    allowed_base_dirs: list[str] = None
) -> list[str]:
    """
    Scan a list of HTML files for presence of all specified keywords.

    This function performs security checks:
    - Ensures files are within allowed directories (if provided).
    - Skips files larger than 10MB.
    - Handles decoding errors gracefully.

    Args:
        file_paths (list[str]): List of file paths to scan.
        scan_keywords (list[str]): List of keywords to search for.
        allowed_base_dirs (list[str], optional): Restrict scanning to these base directories.

    Returns:
        list[str]: List of file paths where all keywords were found.
    """
    matches = []
    lowered_keywords = [kw.lower() for kw in scan_keywords]

    for file_path in file_paths:
        # --- Security Check 1: Restrict file paths ---
        if allowed_base_dirs:
            if not is_allowed_path(file_path, allowed_base_dirs):
                logging.warning(f"Skipping file outside allowed directories: {file_path}")
                continue

        # --- Security Check 2: Limit file size ---
        if not is_file_size_ok(file_path):
            logging.warning(f"Skipping large or inaccessible file: {file_path}")
            continue

        content = read_file_with_fallback(file_path)
        if content is None:
            logging.warning(f"Skipping file due to read/decoding errors: {file_path}")
            continue

        text = extract_text_from_html(content)
        if text is None:
            logging.warning(f"Skipping file due to HTML parsing errors: {file_path}")
            continue

        if contains_all_keywords(text, lowered_keywords):
            matches.append(file_path)

    return matches


def extract_text_with_selector(
    file_path: str,
    selector: str,
    extract_keywords: list[str] | None = None
) -> list[str]:
    """
    Extract text snippets from elements matching a CSS selector in an HTML file.

    Args:
        file_path (str): Path to the HTML file.
        selector (str): CSS selector string.
        extract_keywords (list[str], optional): Keywords to filter snippets. Defaults to None.

    Returns:
        list[str]: List of extracted (and optionally filtered) text snippets.
    """
    content = read_file_with_fallback(file_path)
    if content is None:
        logging.warning(f"Failed to read file: {file_path}")
        return []

    try:
        soup = BeautifulSoup(content, 'html.parser')
    except Exception as e:
        logging.warning(f"Error parsing HTML in {file_path}: {e}")
        return []

    try:
        elements = soup.select(selector)
    except Exception as e:
        logging.warning(f"Invalid CSS selector '{selector}' for file {file_path}: {e}")
        return []

    snippets = []
    for el in elements:
        text = el.get_text(separator=' ', strip=True)
        snippets.append(text)
    print("Snippets before filtering:", snippets)

    if extract_keywords:
        lowered_keywords = [kw.lower() for kw in extract_keywords if kw]
        if lowered_keywords:
            filtered = []
            for snippet in snippets:
                snippet_lower = snippet.lower()
                if all(kw in snippet_lower for kw in lowered_keywords):
                    filtered.append(snippet)
            return filtered
    return snippets

import json
import re
from . import config
from .models import IndexRecord, SearchResultItem


def perform_search(
    download_id: str,
    scan_keywords: list[str],
    selector: str,
    extract_keywords: list[str] | None = None
) -> list[SearchResultItem]:
    """
    Perform a search over downloaded HTML files using keyword scanning and content extraction.

    Args:
        download_id (str): The download session identifier.
        scan_keywords (list[str]): Keywords to scan files for.
        selector (str): CSS selector to extract content.
        extract_keywords (list[str] | None): Optional keywords to filter extracted snippets.

    Returns:
        list[SearchResultItem]: List of search result items.
    """
    # Validate download_id to prevent path traversal
    if not re.fullmatch(r"[a-zA-Z0-9_-]+", download_id):
        logging.error("Invalid download_id format: %s", download_id)
        return []

    allowed_base_dirs = [os.path.realpath(config.DOWNLOAD_BASE_DIR)]

    search_results: list[SearchResultItem] = []
    index_path = os.path.join(config.DOWNLOAD_BASE_DIR, 'index', f"{download_id}.jsonl")

    try:
        with open(index_path, 'r', encoding='utf-8') as f:
            lines = f.readlines()
    except FileNotFoundError:
        logging.error(f"Index file not found: %s", index_path)
        return []

    url_map: dict[str, str] = {}
    successful_paths: list[str] = []

    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            record_data = json.loads(line)
            record = IndexRecord(**record_data)
        except json.JSONDecodeError:
            logging.warning("Skipping invalid JSON line in index: %s...", line[:100])
            continue
        except Exception as e:
            logging.warning("Skipping invalid record in index: %s", e)
            continue

        if record.fetch_status == 'success':
            # Validate local_path is within allowed directories
            if is_allowed_path(record.local_path, allowed_base_dirs):
                real_path = os.path.realpath(record.local_path)
                url_map[real_path] = record.original_url
                successful_paths.append(real_path)
            else:
                logging.warning("Skipping disallowed local_path in index: %s", record.local_path)

    logging.debug("Successful paths before scan: %s", successful_paths)

    candidate_paths = scan_files_for_keywords(
        successful_paths,
        scan_keywords,
        allowed_base_dirs=allowed_base_dirs
    )

    logging.debug("Candidate paths after scan: %s", candidate_paths)

    print("Successful paths before scan:", successful_paths)
    print("Candidate paths after scan:", candidate_paths)

    for candidate_path in candidate_paths:
        print("Candidate path before url_map lookup:", candidate_path)
        snippets = extract_text_with_selector(candidate_path, selector, extract_keywords)
        if not snippets:
            continue

        original_url = url_map.get(candidate_path, "")
        print("Snippets for candidate:", candidate_path, snippets)
        print("Before snippet loop, snippets:", snippets)
        print("Type of snippets:", type(snippets))
        for snippet in snippets:
            try:
                print("Adding snippet:", snippet)
                item = SearchResultItem(
                    original_url=original_url,
                    extracted_content=snippet,
                    selector_matched=selector
                )
                search_results.append(item)
            except Exception as e:
                print("Error creating SearchResultItem:", e)

    return search_results