"""
Module: downloader/helpers.py

Description:
Contains helper functions specifically used by the downloader components,
particularly for generating local file paths from URLs.
"""

import logging
import re
from pathlib import Path
from urllib.parse import urlparse, unquote

# Import necessary functions from top-level utils if needed
from ..utils import canonicalize_url

logger = logging.getLogger(__name__)

def validate_raw_url(url: str) -> None:
    """Validate raw URL before any processing or canonicalization.
    
    Raises:
        ValueError: If URL is invalid or contains potential security issues.
    """
    if not url:
        raise ValueError("URL must have a valid hostname (netloc) for path generation.")
    
    # Check for basic URL structure
    if not re.match(r'^[a-zA-Z]+://', url):
        raise ValueError("Invalid URL format")
    
    # Check for path traversal attempts (including URL-encoded)
    decoded_url = unquote(url)
    if any(seg == '..' for seg in decoded_url.split('/')):
        raise ValueError("Constructed path escapes base directory")

def url_to_local_path(base_dir: Path, url: str) -> Path:
    """
    Generate a local filesystem Path from a URL, mirroring the structure.
    Creates path: base_dir / safe_hostname / safe_path / safe_filename[index.html]
    Ensures path safety and validates against escaping the base directory.

    Args:
        base_dir: The root Path object where download content is stored.
        url: The URL to convert.

    Returns:
        A Path object representing the local file path relative to base_dir.

    Raises:
        ValueError: If the URL is invalid, cannot be parsed, or path validation fails.
        RuntimeError: For unexpected errors during path generation.
    """
    try:
        # First validate the raw URL before any processing
        validate_raw_url(url)
        
        # Then canonicalize for consistent processing
        parsed = urlparse(canonicalize_url(url))
        if not parsed.netloc:
            raise ValueError("URL must have a valid hostname (netloc) for path generation.")

        # Sanitize hostname (allow letters, numbers, dot, hyphen; replace others)
        safe_hostname = re.sub(r"[^a-zA-Z0-9\.\-]+", "_", parsed.netloc)

        # Handle path segments safely
        path_segment = parsed.path
        dir_parts = []
        filename_part = "index.html"  # Default filename

        if path_segment:
            # Check for trailing slash explicitly
            has_trailing_slash = path_segment.endswith('/')
            
            # Remove leading slash and split
            clean_segment = path_segment.lstrip('/')
            parts = Path(clean_segment).parts
            
            # Process directory parts
            for part in parts[:-1]:
                if part:  # Skip empty parts
                    dir_parts.append(part)
            
            # Process filename part
            if not has_trailing_slash and parts[-1]:
                filename_part = parts[-1]

        # Construct the final path
        target_path = base_dir.joinpath(safe_hostname, *dir_parts, filename_part)

        # --- Path Validation (Security Check) ---
        if not base_dir.is_absolute():
            logger.warning(f"Base directory '{base_dir}' was not absolute. Resolving now.")
            base_dir = base_dir.resolve(strict=True)

        abs_target_path = target_path.resolve(strict=False)
        if not (abs_target_path == base_dir or base_dir in abs_target_path.parents):
            logger.error(f"Path Traversal Attempt Blocked: URL '{url}'")
            raise ValueError("Constructed path escapes base directory.")

        return abs_target_path

    except Exception as e:
        logger.error(f"Error generating path for URL '{url}': {str(e)}")
        raise
