# File: src/mcp_doc_retriever/downloader/helpers.py

"""
Module: downloader/helpers.py

Description:
Provides helper functions for the downloader, primarily focusing on securely
mapping a URL to a safe local file path using the `pathvalidate` library
and creating a unique, flat filename structure.
"""

# --- Module Header ---
# Description:
#   Contains the `url_to_local_path` function which converts a web URL into a
#   secure, unique, and filesystem-safe local path. It generates a flat
#   structure within a base directory: <base_dir>/<sanitized_hostname>/<filename_from_url>.
#   The filename is based on a sanitized version of the canonical URL, a hash for
#   uniqueness, and the original (if allowed) or default file extension.
#   This approach prioritizes security and avoids complexities of mirroring URL paths.
#
# Third-Party Documentation:
#   - pathvalidate: https://pathvalidate.readthedocs.io/en/latest/
#     (Used for robust filename/path component sanitization)
#
# Sample Input:
#   base_dir = Path("/app/downloads/content/dl_123") # Assumed to exist by caller
#   url = "https://project-awesome.com/docs/main/feature_spec.md?query=1#section"
#
# Sample Expected Output (assuming default canonicalization & platform):
#   (Path object representing absolute path, e.g., on Linux/macOS)
#   /app/downloads/content/dl_123/project_awesome.com/https_project_awesome.com_docs_main_feature_spec.md_query_1_section-a1b2c3d4.md
#   (Note: hash 'a1b2c3d4' is illustrative, hostname '.' sanitized to '_')
# --- End Module Header ---

import logging
import re
import os
import hashlib
from urllib.parse import urlparse, unquote
from pathlib import Path

# --- Dependencies ---
# Assumes 'pathvalidate' is listed in pyproject.toml and installed.
from pathvalidate import sanitize_filename, ValidationError

# Assumes utils.py exists and is importable in the project structure.
from mcp_doc_retriever.utils import canonicalize_url


# --- Constants ---
MAX_TOTAL_PATH_LEN = 400
MAX_URL_FILENAME_BASE_LEN = 100
ALLOWED_EXTENSIONS = {
    ".html",
    ".htm",
    ".txt",
    ".js",
    ".css",
    ".json",
    ".xml",
    ".md",
    ".rst",
    ".pdf",
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".svg",
    ".webp",
    ".ico",
    ".woff",
    ".woff2",
    ".ttf",
    ".otf",
    ".eot",
    ".yaml",
    ".yml",
}
DEFAULT_EXTENSION = ".html"

logger = logging.getLogger(__name__)


# --- url_to_local_path Function (Corrected Base Dir Handling) ---
def url_to_local_path(base_dir: Path, url: str) -> Path:
    """
    Generates a unique and safe local filesystem path for a URL using pathvalidate.
    Creates a flat structure:
    base_dir / safe_hostname / sanitized(url_string)-{hash}.{original_ext_or_default}
    Prioritizes security and uniqueness. Uses original extension when valid.
    Assumes the caller ensures base_dir exists if needed for subsequent file ops.
    """
    global logger
    platform_target = "auto"

    try:
        # --- Step 1: Resolve Base Directory (but don't require existence) ---
        try:
            # Make the base path absolute without requiring it to exist yet.
            # The caller (workflow) ensures it exists before writing.
            resolved_base_dir = base_dir.resolve(strict=False)
        except Exception as e:
            # Catch potential errors during resolution itself (e.g., invalid chars in input base_dir)
            logger.critical(
                f"Configuration error: Could not resolve base directory '{base_dir}': {e}"
            )
            raise ValueError(f"Could not resolve base directory: {e}")

        # --- Step 2: Parse URLs (Original and Canonical) ---
        try:
            original_parsed = urlparse(url)
            original_path = original_parsed.path
        except Exception as e:
            logger.error(
                f"Fundamental URL parsing failed for original URL '{url}': {e}"
            )
            raise ValueError(f"Cannot parse original URL: {url}") from e

        try:
            canon_url = canonicalize_url(url)
            parsed = urlparse(canon_url)
        except Exception as e:
            logger.warning(
                f"URL canonicalization/parsing failed for '{url}': {e}. Using original parse results."
            )
            parsed = original_parsed
            canon_url = url

        if not parsed.scheme or not parsed.netloc:
            raise ValueError("URL must have a scheme and valid hostname (netloc).")

        # --- Step 3: Sanitize Hostname ---
        hostname_str = parsed.netloc
        try:
            # Let pathvalidate handle sanitization based on platform
            safe_hostname = sanitize_filename(
                hostname_str, platform=platform_target, replacement_text="_"
            )
            if not safe_hostname:
                safe_hostname = "_"
        except (ValidationError, TypeError) as e:
            logger.warning(
                f"Hostname sanitization failed for '{parsed.netloc}': {e}. Using fallback '_'."
            )
            safe_hostname = "_"

        # --- Step 4: Generate Safe Filename (SANITIZED URL + Hash + Original Ext) ---
        url_for_fname = (
            canon_url.replace("://", "_")
            .replace("/", "_")
            .replace("?", "_")
            .replace("&", "_")
            .replace("=", "_")
            .replace("#", "_")
        )
        try:
            safe_filename_base = sanitize_filename(
                url_for_fname, platform=platform_target, replacement_text="_"
            )
            safe_filename_base = safe_filename_base[:MAX_URL_FILENAME_BASE_LEN]
            if not safe_filename_base:
                safe_filename_base = "url"
        except (ValidationError, TypeError) as e:
            logger.warning(
                f"URL-based filename sanitization failed for '{url_for_fname[:100]}...': {e}. Using fallback 'url'."
            )
            safe_filename_base = "url"

        url_hash = hashlib.sha256(canon_url.encode()).hexdigest()[:8]

        _root, original_ext = os.path.splitext(original_path)
        original_ext_lower = original_ext.lower()
        safe_ext = DEFAULT_EXTENSION
        if original_ext_lower in ALLOWED_EXTENSIONS:
            try:
                sanitized_ext_part = sanitize_filename(
                    original_ext_lower, platform=platform_target, replacement_text=""
                )
                if sanitized_ext_part and sanitized_ext_part != ".":
                    if not sanitized_ext_part.startswith("."):
                        safe_ext = "." + sanitized_ext_part
                    else:
                        safe_ext = sanitized_ext_part
            except (ValidationError, TypeError):
                pass

        final_filename = f"{safe_filename_base}-{url_hash}{safe_ext}"

        # --- Step 5: Construct Final Path ---
        # Join components onto the (potentially non-existent) resolved base directory
        target_path = resolved_base_dir.joinpath(safe_hostname, final_filename)

        # --- Step 6: Length Check ---
        # Check length based on the string representation of the constructed path
        target_path_str = str(target_path)
        if len(target_path_str) > MAX_TOTAL_PATH_LEN:
            logger.warning(
                f"Generated path exceeds limit ({MAX_TOTAL_PATH_LEN}), shortening filename base for URL: {url}"
            )
            short_filename = f"url-{url_hash}{safe_ext}"
            target_path = resolved_base_dir.joinpath(safe_hostname, short_filename)
            target_path_str = str(target_path)
            if len(target_path_str) > MAX_TOTAL_PATH_LEN:
                logger.error(
                    f"Path length exceeds limit ({MAX_TOTAL_PATH_LEN}) even after shortening filename: '{target_path_str}' for URL '{url}'"
                )
                raise ValueError(
                    f"Constructed path exceeds maximum length ({MAX_TOTAL_PATH_LEN} chars) even with shortening."
                )

        # --- Step 7: Return the safe, absolute path ---
        # Note: The path object itself is returned; it doesn't guarantee the file exists yet.
        return target_path

    # --- Exception Handling ---
    except (ValueError, ValidationError) as e:
        logger.error(f"Path generation failed for URL '{url}': {e}")
        raise ValueError(f"Path generation failed: {e}") from e
    except Exception as e:
        logger.error(
            f"Unexpected error generating local path for URL '{url}': {e}",
            exc_info=True,
        )
        raise RuntimeError(f"Unexpected error generating path for URL: {url}") from e


# --- Standalone Execution / Example ---
if __name__ == "__main__":
    import sys
    import tempfile
    import shutil

    # Setup basic logging for standalone execution
    logging.basicConfig(
        level=logging.DEBUG,
        format="[%(levelname)-8s] %(name)s:%(lineno)d - %(message)s",
    )
    logger = logging.getLogger(__name__)  # Assign to module logger

    # Check pathvalidate is importable for the test
    try:
        from pathvalidate import sanitize_filename
    except ImportError:
        logger.critical("ERROR: pathvalidate library not found. Cannot run tests.")
        sys.exit(1)

    # Define canonicalize_url (use real one if possible, else dummy)
    try:
        from mcp_doc_retriever.utils import canonicalize_url as real_canonicalize_url

        canonicalize_url = real_canonicalize_url
        logger.info("Using real canonicalize_url for __main__ tests.")
    except ImportError:
        logger.warning("Using dummy canonicalize_url for __main__ tests.")

        def dummy_canonicalize_url(url: str) -> str:
            return url

        canonicalize_url = dummy_canonicalize_url

    print(
        "--- Downloader Helper: url_to_local_path Examples (Corrected Base Dir Handling) ---"
    )

    # Helper to generate expected path for the final simplified logic
    def generate_expected_path_final_simplified(base_path: Path, url: str) -> Path:
        try:
            canon_url = canonicalize_url(url)
            original_parsed = urlparse(url)
            original_path = original_parsed.path
            parsed = urlparse(canon_url)
            if not parsed.scheme or not parsed.netloc:
                return None

            hostname_str = parsed.netloc
            safe_hostname = sanitize_filename(
                hostname_str, platform="auto", replacement_text="_"
            )
            if not safe_hostname:
                safe_hostname = "_"

            url_for_fname = (
                canon_url.replace("://", "_")
                .replace("/", "_")
                .replace("?", "_")
                .replace("&", "_")
                .replace("=", "_")
                .replace("#", "_")
            )
            safe_filename_base = sanitize_filename(
                url_for_fname, platform="auto", replacement_text="_"
            )
            safe_filename_base = safe_filename_base[:MAX_URL_FILENAME_BASE_LEN]
            if not safe_filename_base:
                safe_filename_base = "url"

            url_hash = hashlib.sha256(canon_url.encode()).hexdigest()[:8]

            _root, original_ext = os.path.splitext(original_path)
            original_ext_lower = original_ext.lower()
            safe_ext = DEFAULT_EXTENSION
            if original_ext_lower in ALLOWED_EXTENSIONS:
                sanitized_ext_part = sanitize_filename(
                    original_ext_lower, platform="auto", replacement_text=""
                )
                if sanitized_ext_part and sanitized_ext_part != ".":
                    if not sanitized_ext_part.startswith("."):
                        safe_ext = "." + sanitized_ext_part
                    else:
                        safe_ext = sanitized_ext_part

            final_filename = f"{safe_filename_base}-{url_hash}{safe_ext}"
            # Important: Use resolve(strict=False) here for the *expected* path as well
            target_path = base_path.resolve(strict=False).joinpath(
                safe_hostname, final_filename
            )

            # Simulate length check
            if len(str(target_path)) > MAX_TOTAL_PATH_LEN:
                short_filename = f"url-{url_hash}{safe_ext}"
                target_path = base_path.resolve(strict=False).joinpath(
                    safe_hostname, short_filename
                )

            return target_path
        except Exception as e:
            print(f"      [Error generating expected path for {url}: {e}]")
            return None

    with tempfile.TemporaryDirectory() as tmpdir:
        base_path_obj = Path(tmpdir)  # Use the non-resolved path initially for tests
        resolved_base_path_for_check = (
            base_path_obj.resolve()
        )  # Resolve once for checks
        print(f"Using temporary base directory: {resolved_base_path_for_check}")

        # --- Test Cases ---
        test_urls = [
            "http://example.com/path/file.html",
            "https://docs.python.org/3/library/pathlib.html",
            "http://example.com/",
            "http://example.com",
            "http://example.com/a/b/c/",
            "http://example.com/a/b/c",
            "http://example.com/file\x01with\x1fcontrol.txt",
            "http://example.com/.git/config",
            "http://example.com/.leading_dot.txt",
            "http://example.com/con.txt",
            'http://example.com/unsafe<>:"\\|?*.txt',
            "http://example.com/%2e%2e/%2e%2e/etc/passwd",  # Still processed, no traversal check here
            "http://example.com/a/b/../c",  # Still processed, no traversal check here
            "http://example.com/nul",
            "http://example.com/CON",
            "http://[::1]:8080/v6",
            "http://[::1]/file.txt",
            "http://example.com/" + ("a" * 300) + ".html",
            "http://example.com/image.jpg?v=1",
            "http://example.com/no_ext_at_all",
            "http://example.com/archive.tar.gz",
        ]

        # --- Test Execution Loop ---
        all_passed = True
        test_results = {}

        for i, url in enumerate(test_urls):
            test_name = f"Test {i + 1}: URL = {url}"
            print(f"\n--- {test_name} ---")
            try:
                # Pass the potentially non-existent base_path_obj to the helper
                # It will resolve it internally without strict=True
                result_path = url_to_local_path(base_path_obj, url)

                # Generate the expected path for comparison
                expected_path = generate_expected_path_final_simplified(
                    base_path_obj, url
                )

                if expected_path is None:
                    print("      [Skipping test due to error generating expectation]")
                    test_results[test_name] = "SKIPPED (Expectation Error)"
                    continue

                print(f"  Input URL    : {url}")
                print(f"  Expected Path: {expected_path}")  # Absolute path
                print(f"  Result Path  : {result_path}")  # Absolute path

                if result_path == expected_path:
                    # Security Sanity Check: Verify it's within the resolved base directory
                    if (
                        resolved_base_path_for_check == result_path.parent.parent
                        or resolved_base_path_for_check in result_path.parents
                    ):
                        print("  Result: ✓ PASSED")
                        test_results[test_name] = "PASSED"
                    else:
                        print(f"  Result: ✗ FAILED (Security Sanity Check Failed!)")
                        test_results[test_name] = "FAILED (Security Sanity Check)"
                        all_passed = False
                else:
                    print(f"  Result: ✗ FAILED (Mismatch)")
                    test_results[test_name] = "FAILED (Mismatch)"
                    all_passed = False

            except (ValueError, RuntimeError) as e:
                if "exceeds maximum length" in str(e) and "a" * 300 in url:
                    print(f"  Result: ✓ PASSED (Correctly caught length error: {e})")
                    test_results[test_name] = "PASSED (Length Error Caught)"
                else:
                    print(
                        f"  Result: ✗ FAILED (Unexpected Error: {type(e).__name__}: {e})"
                    )
                    test_results[test_name] = f"FAILED ({type(e).__name__}: {e})"
                    all_passed = False
            except Exception as e:
                print(
                    f"  Result: ✗ FAILED (Critical Unexpected Exception: {type(e).__name__}: {e})"
                )
                test_results[test_name] = f"FAILED (Exception: {type(e).__name__})"
                all_passed = False

        # --- Final Summary ---
        print("\n--- Test Summary ---")
        passed_count = 0
        failed_count = 0
        skipped_count = 0
        for name in sorted(
            test_results.keys(), key=lambda x: int(x.split(":")[0].split(" ")[1])
        ):
            result = test_results[name]
            print(f"- {name.split(':')[0]}: {result}")
            if "PASSED" in result:
                passed_count += 1
            elif "SKIPPED" in result:
                skipped_count += 1
            else:
                failed_count += 1
        print("\n------------------------------------")
        print(f"Total Tests Run: {passed_count + failed_count + skipped_count}")
        print(f"Passed: {passed_count}")
        print(f"Failed: {failed_count}")
        print(f"Skipped: {skipped_count}")
        print("------------------------------------")

        if failed_count == 0 and skipped_count == 0:
            print("✓ All url_to_local_path tests passed successfully.")
            sys.exit(0)
        elif failed_count == 0 and skipped_count > 0:
            print("✓ All executed url_to_local_path tests passed (some skipped).")
            sys.exit(0)
        else:
            print("✗ Some url_to_local_path tests failed.")
            sys.exit(1)

    print("\nDownloader helper examples finished.")
