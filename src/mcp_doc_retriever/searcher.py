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