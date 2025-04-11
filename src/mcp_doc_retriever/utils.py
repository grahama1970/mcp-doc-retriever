# File: src/mcp_doc_retriever/utils.py (Updated)

"""
Module: utils.py

Description:
Provides shared, general-purpose utility functions for the MCP Document Retriever,
including URL canonicalization, ID generation, security checks (SSRF), and basic
keyword matching helpers used across different components like the downloader,
searcher, and potentially an API layer.
"""

import hashlib
import ipaddress
import logging
import socket  # Keep for gethostbyname/getaddrinfo in SSRF check
from typing import List, Optional
from urllib.parse import urlparse, urlunparse

# Config will be imported inside the function that needs it (is_url_private_or_internal)
# to handle both direct execution and module import scenarios.
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

        # Import config here for SSRF override flag check
        try:
            from mcp_doc_retriever import config
            allow_test_urls = getattr(config, "ALLOW_TEST_INTERNAL_URLS", False)
        except ImportError:
            # Fallback if run in a context where absolute import fails (shouldn't happen with __main__ setup)
            logger.warning("Could not import config for SSRF check. Assuming default behavior (ALLOW_TEST_INTERNAL_URLS=False).")
            allow_test_urls = False
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
            except socket.gaierror:
                pass  # Fall through if resolution fails
            except ValueError:
                pass  # Fall through if IP is invalid format
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
            # Use getaddrinfo for potentially resolving both IPv4 and IPv6
            # Use socket.AF_UNSPEC to allow both families
            addr_info_list = socket.getaddrinfo(
                hostname, parsed.port, family=socket.AF_UNSPEC, proto=socket.IPPROTO_TCP
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
    # Normalize keywords: lowercase and remove empty/whitespace-only strings
    lowered_keywords = [kw.lower() for kw in keywords if kw and kw.strip()]
    if not lowered_keywords:
        return False  # No valid keywords to check
    # Perform case-insensitive check
    text_lower = text.lower()
    return all(keyword in text_lower for keyword in lowered_keywords)


# --- Helper functions _is_json_like and _find_block_lines have been moved to searcher/helpers.py ---


# --- Example Usage (if run directly) ---
if __name__ == "__main__":
    # --- Setup for direct execution ---
    import sys
    import os
    import logging # Ensure logging is imported here too if used before setup

    # Add the 'src' directory to sys.path to allow absolute imports
    current_dir = os.path.dirname(os.path.abspath(__file__))
    # Go up two levels from src/mcp_doc_retriever/utils.py to reach the project root
    project_root = os.path.abspath(os.path.join(current_dir, "..", ".."))
    src_dir = os.path.join(project_root, "src") # Explicitly point to src directory
    if src_dir not in sys.path:
        sys.path.insert(0, src_dir)
        # print(f"Added {src_dir} to sys.path for direct execution.") # Optional debug print

    # No need to re-import config here anymore, it's handled within the function.
    # The sys.path modification above ensures the import inside is_url_private_or_internal works.
    pass # Placeholder if no other setup needed here
    # --- End Setup ---

    print("--- Top-Level Utility Function Examples ---")
    # Ensure logging is configured *after* potential sys.exit
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
            try:
                dl_id = generate_download_id(url)
                print(f"'{url}' -> Canon='{canon_url}', ID='{dl_id}'")
            except ValueError as id_e:
                print(f"'{url}' -> Canon='{canon_url}', ID ERROR: {id_e}")
        except ValueError as e:
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
    ]
    try:
        google_ip = socket.gethostbyname("google.com")
        ssrf_urls_to_test.append(f"http://{google_ip}")
        print(f"(Using resolved google.com IP for testing: {google_ip})")
    except socket.gaierror as e:
        print(f"Warning: Could not resolve google.com for SSRF test: {e}")
    except Exception as e:
        print(f"Warning: Unexpected error resolving google.com: {e}")

    for url in ssrf_urls_to_test:
        try:
            is_internal = is_url_private_or_internal(url)
            print(f"'{url}' -> Internal/Private: {is_internal}")
        except Exception as e:
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
    )  # False
    print(
        f"'{text_sample}' contains [' ']: {contains_all_keywords(text_sample, [' '])}"
    )  # False
    print(f"None contains ['test']: {contains_all_keywords(None, ['test'])}")  # False

    print("\n--- Top-Level Utils Examples Finished ---")
