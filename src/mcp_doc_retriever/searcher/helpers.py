"""
Module: utils.py

Description:
Provides shared, general-purpose utility functions for the MCP Document Retriever,
including URL canonicalization, ID generation, security checks, and basic
keyword/content matching helpers used across different components like the
downloader, searcher, and potentially an API layer.
"""

import hashlib
import ipaddress
import json
import logging
import socket  # Keep for gethostbyname/getaddrinfo in SSRF check
from typing import List, Optional, Set, Tuple
from urllib.parse import urlparse, urlunparse

# Assuming config is importable for SSRF override flag
from . import config

logger = logging.getLogger(__name__)

# --- Constants ---
# Default timeouts can reside here or in config.py
TIMEOUT_REQUESTS = 30
TIMEOUT_PLAYWRIGHT = 60


# --- URL Utilities ---


def canonicalize_url(url: str) -> str:
    """
    Normalize URL for consistent identification and processing.
    See implementation details below.

    Args:
        url: The input URL string.
    Returns:
        The canonicalized URL string.
    Raises:
        ValueError: If the URL cannot be parsed or canonicalized.
    """
    try:
        # Handle potential protocol-relative URLs first
        if url.startswith("//"):
            url = "http:" + url  # Default to http for parsing

        parsed = urlparse(url)

        if not parsed.scheme:
            # If still no scheme, try prepending http://
            if not url.startswith("http://") and not url.startswith("https://"):
                parsed = urlparse("http://" + url)
            else:  # If it starts with http(s) but parsing failed, raise
                raise ValueError("URL scheme is missing or invalid.")

        scheme = parsed.scheme.lower()
        netloc = parsed.netloc.lower()

        # Remove default ports
        if ":" in netloc:
            host, port_str = netloc.split(":", 1)
            try:
                port = int(port_str)
            except ValueError:
                port = None  # Handle non-integer port if necessary

            if port is not None and (
                (scheme == "http" and port == 80) or (scheme == "https" and port == 443)
            ):
                netloc = host

        path = parsed.path if parsed.path else "/"
        if not path.startswith("/"):
            path = "/" + path  # Should be rare
        if path != "/" and path.endswith("/"):
            path = path.rstrip("/")

        # Reconstruct URL: scheme, netloc, path, params, query, fragment
        return urlunparse((scheme, netloc, path, "", "", ""))
    except Exception as e:
        logger.error(f"Failed to canonicalize URL '{url}': {e}", exc_info=True)
        raise ValueError(f"Could not canonicalize URL: '{url}' - Error: {e}") from e


def generate_download_id(url: str) -> str:
    """
    Generate a unique download ID (MD5 hash) based on the canonical URL.

    Args:
        url: The input URL string.
    Returns:
        A string representing the MD5 hash of the canonical URL.
    Raises:
        ValueError: If the URL is invalid and cannot be canonicalized.
    """
    try:
        canonical_url_str = canonicalize_url(url)
        return hashlib.md5(canonical_url_str.encode("utf-8")).hexdigest()
    except ValueError as e:
        raise ValueError(
            f"Could not generate download ID for invalid URL: {url}"
        ) from e


# --- Security Utilities ---


def is_url_private_or_internal(url: str) -> bool:
    """
    Checks if a URL resolves to an internal, private, loopback, or reserved IP address,
    or if its hostname matches common internal patterns. Designed to mitigate SSRF risks.
    Allows specific test hostnames/IPs if `config.ALLOW_TEST_INTERNAL_URLS` is True.

    Args:
        url: The URL string to check.
    Returns:
        True if the URL is considered internal/private/unsafe, False otherwise.
    """
    try:
        if not isinstance(url, str):
            return True  # Block non-strings
        parsed = urlparse(url)
        hostname = parsed.hostname
        if not hostname:
            logger.debug(f"SSRF check: Blocked URL with no hostname: {url}")
            return True

        allow_test_urls = getattr(config, "ALLOW_TEST_INTERNAL_URLS", False)
        if allow_test_urls:
            allowed_test_hosts = {
                "host.docker.internal",
                "localhost",
                "127.0.0.1",
            }  # Case handled below
            allowed_test_ips = {"172.17.0.1"}  # Example
            base_host_lower = hostname.lower().split(":")[0]
            if base_host_lower in allowed_test_hosts:
                logger.debug(f"SSRF check: Allowed test hostname: {hostname}")
                return False
            try:
                resolved_ip = socket.gethostbyname(
                    hostname
                )  # Simple resolution for test check
                if resolved_ip in allowed_test_ips:
                    logger.debug(
                        f"SSRF check: Allowed test IP: {resolved_ip} for {hostname}"
                    )
                    return False
            except (socket.gaierror, ValueError):
                pass  # Fall through if resolution fails or IP not in test list
            except Exception as e:
                logger.warning(
                    f"SSRF check: Unexpected error resolving {hostname} for test check: {e}"
                )

        lowered_host = hostname.lower()
        if lowered_host == "localhost" or lowered_host.endswith(
            (".localhost", ".local", ".internal", ".test", ".example", ".invalid")
        ):
            logger.debug(f"SSRF check: Blocked internal hostname pattern: {hostname}")
            return True

        resolved_ips = []
        try:
            addr_info_list = socket.getaddrinfo(
                hostname, parsed.port, proto=socket.IPPROTO_TCP
            )
            resolved_ips = list(set(info[4][0] for info in addr_info_list))
            if not resolved_ips:
                logger.debug(
                    f"SSRF check: Blocked - Could not resolve hostname: {hostname}"
                )
                return True
        except socket.gaierror:
            logger.debug(f"SSRF check: Blocked - DNS resolution failed: {hostname}")
            return True
        except Exception as e:
            logger.warning(f"SSRF check: Unexpected DNS error for {hostname}: {e}")
            return True

        for ip_str in resolved_ips:
            try:
                ip_obj = ipaddress.ip_address(ip_str)
                if (
                    ip_obj.is_private
                    or ip_obj.is_loopback
                    or ip_obj.is_link_local
                    or ip_obj.is_reserved
                    or ip_obj.is_multicast
                    or ip_obj.is_unspecified
                ):
                    logger.debug(
                        f"SSRF check: Blocked private/reserved IP {ip_str} for {hostname}"
                    )
                    return True
            except ValueError:
                logger.warning(
                    f"SSRF check: Invalid IP '{ip_str}' for {hostname}. Blocking."
                )
                return True

        logger.debug(
            f"SSRF check: Allowed public hostname/IPs for: {hostname} ({resolved_ips})"
        )
        return False

    except Exception as e:
        logger.error(f"SSRF check: Error processing URL '{url}': {e}", exc_info=True)
        return True


# --- Basic Content Matching Utilities ---


def contains_all_keywords(text: Optional[str], keywords: List[str]) -> bool:
    """
    Check if a given text string contains all specified keywords (case-insensitive).

    Args:
        text: The text content to search within.
        keywords: A list of keywords that must all be present.

    Returns:
        True if text is not None and contains all non-empty keywords, False otherwise.
    """
    if not text or not keywords:
        return False
    lowered_keywords = [kw.lower() for kw in keywords if kw and kw.strip()]
    if not lowered_keywords:
        return False
    text_lower = text.lower()
    return all(keyword in text_lower for keyword in lowered_keywords)


def _is_json_like(text: str) -> bool:
    """Heuristic check if a string looks like it might be JSON."""
    text = text.strip()
    if text and (
        (text.startswith("{") and text.endswith("}"))
        or (text.startswith("[") and text.endswith("]"))
    ):
        try:
            json.loads(text)
            return True
        except json.JSONDecodeError:
            return False
    return False


def _find_block_lines(
    block_text: str, source_lines: List[str], used_spans: Set[Tuple[int, int]]
) -> Tuple[Optional[int], Optional[int]]:
    """
    Approximates the start and end line numbers of a text block within source lines.
    Tries to find the first unused match. NOTE: Heuristic, may be inaccurate.

    Args:
        block_text: The text content of the block to find.
        source_lines: List of strings representing the lines of the source document.
        used_spans: Set of (start_line, end_line) tuples already assigned.

    Returns:
        Tuple (start_line, end_line), both 1-based, or (None, None).
    """
    # Normalize block text for comparison (strip whitespace from each line)
    block_lines = [
        line.strip() for line in block_text.strip().splitlines() if line.strip()
    ]
    if not block_lines:
        return None, None

    num_block_lines = len(block_lines)
    num_source_lines = len(source_lines)

    for i in range(num_source_lines - num_block_lines + 1):
        current_span = (i, i + num_block_lines - 1)  # 0-based span
        if current_span in used_spans:
            continue

        window_lines = source_lines[i : i + num_block_lines]
        normalized_window = [line.strip() for line in window_lines]

        if normalized_window == block_lines:
            used_spans.add(current_span)
            return i + 1, i + num_block_lines  # Return 1-based lines

    return None, None  # No unused match found


# --- Example Usage ---
# --- Example Usage (if run directly) ---
if __name__ == "__main__":
    import socket  # Keep socket import here for the SSRF test

    print("--- Top-Level Utility Function Examples ---")
    # Configure logging for example output visibility
    logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")

    # --- URL Canonicalization ---
    print("\n--- Canonicalization ---")
    urls_to_canon = [
        "http://Example.Com:80/Path/../Index.html?a=1#frag",
        "https://google.com:443/",
        "example.com/test",  # No scheme
        "//cdn.example.com/lib.js",  # Protocol relative
        "https://test.com/a/b/c/",  # Trailing slash
        "http://test.com/a/b/c",  # No trailing slash
        "invalid url",  # Invalid
    ]
    for url in urls_to_canon:
        try:
            canon_url = canonicalize_url(url)
            # Example of using generate_download_id
            try:
                dl_id = generate_download_id(url)  # Or use canon_url if preferred
                print(f"'{url}' -> Canon='{canon_url}', ID='{dl_id}'")
            except (
                ValueError
            ) as id_e:  # Catch potential ID generation error separately if needed
                print(f"'{url}' -> Canon='{canon_url}', ID ERROR: {id_e}")
        except ValueError as e:
            # Catch canonicalization errors
            print(f"'{url}' -> CANON ERROR: {e}")

    # --- SSRF Check ---
    print("\n--- SSRF Checks ---")
    ssrf_urls_to_test = [
        "http://example.com",
        "http://google.com",
        "http://localhost",
        "http://localhost:8000",
        "http://127.0.0.1",
        "https://10.0.0.1/path",
        "http://192.168.1.1",
        "http://172.16.5.4",
        "http://169.254.169.254/latest/meta-data/",  # AWS metadata
        "http://metadata.google.internal/computeMetadata/v1/",  # GCP metadata
        "http://[::1]:8080",  # IPv6 loopback
        "http://[fe80::1]/path",  # IPv6 link-local
        "http://internal.service.local",
        "http://service.internal",
        # Add a known public IP if possible for testing False case reliably
    ]
    # Try to add a known public IP for testing
    try:
        # Resolve google.com's IP address
        google_ip = socket.gethostbyname("google.com")
        ssrf_urls_to_test.append(f"http://{google_ip}")
        print(f"(Using resolved google.com IP for testing: {google_ip})")
    except socket.gaierror as e:
        # Catch specific DNS resolution error
        print(f"Warning: Could not resolve google.com for SSRF test: {e}")
    except Exception as e:
        # Catch any other unexpected error during DNS lookup
        print(f"Warning: Unexpected error resolving google.com: {e}")

    # Test each URL
    for url in ssrf_urls_to_test:
        try:
            is_internal = is_url_private_or_internal(url)
            print(f"'{url}' -> Internal/Private: {is_internal}")
        except Exception as e:
            # Catch potential errors within the check function itself if not handled internally
            print(f"'{url}' -> ERROR during SSRF check: {e}")

    # --- Keyword Check ---
    print("\n--- Keyword Check ---")
    text_sample = "This is sample text for testing keywords."
    print(
        f"'{text_sample}' contains ['sample', 'keywords']: {contains_all_keywords(text_sample, ['sample', 'keywords'])}"
    )
    print(
        f"'{text_sample}' contains ['sample', 'missing']: {contains_all_keywords(text_sample, ['sample', 'missing'])}"
    )
    print(
        f"'{text_sample}' contains ['SAMPLE', 'TEXT'] (case-insensitive): {contains_all_keywords(text_sample, ['SAMPLE', 'TEXT'])}"
    )
    print(
        f"'{text_sample}' contains []: {contains_all_keywords(text_sample, [])}"
    )  # Should be False
    print(
        f"'{text_sample}' contains [' ']: {contains_all_keywords(text_sample, [' '])}"
    )  # Should be False (empty keyword filtered)
    print(
        f"None contains ['test']: {contains_all_keywords(None, ['test'])}"
    )  # Should be False

    print("\n--- Top-Level Utils Examples Finished ---")