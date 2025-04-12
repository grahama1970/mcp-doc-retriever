"""
Module: utils.py

Description:
Provides shared, general-purpose utility functions for the MCP Document Retriever,
including URL canonicalization, ID generation, security checks (SSRF), and basic
keyword matching helpers used across different components like the downloader,
searcher, and potentially an API layer.

Third-Party Documentation:
- hashlib: https://docs.python.org/3/library/hashlib.html
- ipaddress: https://docs.python.org/3/library/ipaddress.html
- logging: https://docs.python.org/3/library/logging.html
- re: https://docs.python.org/3/library/re.html
- socket: https://docs.python.org/3/library/socket.html
- pathlib: https://docs.python.org/3/library/pathlib.html
- urllib.parse: https://docs.python.org/3/library/urllib.parse.html

Sample Input/Output:

Function: canonicalize_url()
Input: "http://Example.Com:80/Path/?query=1#frag"
Output: "http://example.com/Path"

Function: is_url_private_or_internal()
Input: "http://192.168.1.1/admin"
Output: True
Input: "https://google.com"
Output: False
"""

import hashlib
import ipaddress
import logging
import re
import socket
from pathlib import Path  # Keep if still needed by any remaining utils
from typing import List, Optional
from urllib.parse import urlparse, urlunparse, unquote

# Assuming config is importable for SSRF override flag
# Use relative import consistent with package structure
try:
    from . import config
except ImportError:
    # Provide fallback or re-raise if config is essential and structure wrong
    print("Warning: Could not import config. Using default values for SSRF check.")

    # Define a simple mock object that mimics the expected attribute
    class MockConfig:
        ALLOW_TEST_INTERNAL_URLS = False  # Default conservative behavior

    config = MockConfig()


logger = logging.getLogger(__name__)

# --- Constants ---
# Default timeouts can reside here or in config.py
TIMEOUT_REQUESTS = 30
TIMEOUT_PLAYWRIGHT = 60


# --- URL Utilities ---


def canonicalize_url(url: str) -> str:
    """
    Normalize URL for consistent identification and processing.
    - Converts scheme and netloc to lowercase.
    - Removes default ports (80 for http, 443 for https).
    - Ensures path starts with '/'.
    - Removes trailing slash from path unless it's just '/'.
    - Removes fragment (#...) and query string (?...).
    - Adds 'http://' if scheme is missing.
    - Handles '//' shorthand for scheme.
    - Decodes percent-encoded characters in the path.

    Args:
        url: The input URL string.
    Returns:
        The canonicalized URL string.
    Raises:
        ValueError: If the URL cannot be parsed or canonicalized.
    """
    if not isinstance(url, str):
        raise ValueError("URL must be a string.")
    url = url.strip()
    if not url:
        raise ValueError("URL cannot be empty.")

    try:
        # Handle scheme-relative URLs like //example.com/path
        if url.startswith("//"):
            url = (
                "http:" + url
            )  # Assume http for canonicalization purpose if scheme missing

        parsed = urlparse(url)

        # Add scheme if missing (default to http)
        scheme = parsed.scheme.lower()
        if not scheme:
            # If still no scheme after handling '//', prepend http://
            if not url.startswith("http://") and not url.startswith("https://"):
                url = "http://" + url
                parsed = urlparse(url)  # Re-parse with the added scheme
                scheme = parsed.scheme.lower()
            else:
                # Should not happen if logic above is correct, but as a safeguard
                raise ValueError("URL scheme could not be determined.")

        # Normalize netloc (lowercase, remove default port)
        netloc = parsed.netloc.lower()
        if ":" in netloc:
            host, port_str = netloc.split(":", 1)
            try:
                port = int(port_str)
                # Remove port if it's the default for the scheme
                if (scheme == "http" and port == 80) or (
                    scheme == "https" and port == 443
                ):
                    netloc = host  # Use only host if default port
            except ValueError:
                # Invalid port number, keep netloc as is? Or raise error?
                # Keeping it might be more robust to oddly formed URLs.
                logger.debug(
                    f"Invalid port '{port_str}' in URL '{url}', keeping netloc as is."
                )
                pass  # Keep netloc as host:port_str

        # Normalize path (ensure leading '/', remove trailing '/', decode)
        path = parsed.path if parsed.path else "/"
        # Decode percent-encoded characters (e.g., %20 -> space)
        # It's generally safer to normalize these for consistency
        path = unquote(path)
        # Ensure path starts with a slash
        if not path.startswith("/"):
            path = "/" + path
        # Remove trailing slash unless path is just "/"
        if path != "/" and path.endswith("/"):
            path = path.rstrip("/")

        # Reconstruct the canonical URL without query, params, or fragment
        # urlunparse expects a 6-tuple: (scheme, netloc, path, params, query, fragment)
        return urlunparse((scheme, netloc, path, "", "", ""))

    except Exception as e:
        # Catch potential errors during parsing or reconstruction
        logger.error(f"Failed to canonicalize URL '{url}': {e}", exc_info=True)
        raise ValueError(f"Could not canonicalize URL: '{url}' - Error: {e}") from e


def generate_download_id(url: str) -> str:
    """
    Generate a unique download ID (MD5 hash) based on the canonical URL.
    Uses MD5 for a stable, reasonably unique identifier based on the normalized URL.

    Args:
        url: The input URL string.
    Returns:
        A string representing the MD5 hash of the canonical URL.
    Raises:
        ValueError: If the URL is invalid and cannot be canonicalized.
    """
    try:
        # First, canonicalize the URL to ensure consistency
        canonical_url_str = canonicalize_url(url)
        # Encode the canonical URL string to bytes (UTF-8 recommended)
        url_bytes = canonical_url_str.encode("utf-8")
        # Calculate the MD5 hash
        hasher = hashlib.md5()
        hasher.update(url_bytes)
        # Return the hexadecimal representation of the hash
        return hasher.hexdigest()
    except ValueError as e:
        # Propagate the error if canonicalization failed
        raise ValueError(
            f"Could not generate download ID for invalid URL: {url}"
        ) from e
    except Exception as e:
        # Catch other potential errors (e.g., during hashing)
        logger.error(f"Error generating download ID for '{url}': {e}", exc_info=True)
        # Re-raise as a ValueError or a custom exception if needed
        raise ValueError(f"Could not generate download ID for URL: {url}") from e


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
            logger.warning("SSRF check: Received non-string URL input.")
            return True  # Treat non-strings as unsafe

        parsed = urlparse(url)
        hostname = (
            parsed.hostname
        )  # Extracts host part (e.g., 'example.com' from 'http://example.com:80')

        if not hostname:
            logger.debug(f"SSRF check: Blocked URL with no hostname: {url}")
            return True  # URLs without a host are typically invalid or local file paths

        # --- Test URL Override Check ---
        # Check if the configuration allows bypassing checks for specific test URLs
        allow_test = getattr(config, "ALLOW_TEST_INTERNAL_URLS", False)
        if allow_test:
            # Define hosts/IPs commonly used in testing environments
            test_hosts = {"host.docker.internal", "localhost", "127.0.0.1"}
            test_ips = {"172.17.0.1"}  # Example Docker bridge IP

            host_lower = hostname.lower().split(":")[0]  # Get host part without port
            if host_lower in test_hosts:
                logger.debug(f"SSRF: Allowed test host (config override): {hostname}")
                return False  # Allow if hostname matches test list

            # Try resolving the hostname to see if it matches a test IP
            try:
                # Use getaddrinfo for potentially better IPv6 support than gethostbyname
                addr_info = socket.getaddrinfo(hostname, None, family=socket.AF_UNSPEC)
                resolved_ips = {info[4][0] for info in addr_info}  # Get unique IPs
                if any(ip in test_ips for ip in resolved_ips):
                    logger.debug(
                        f"SSRF: Allowed test IP (config override): {resolved_ips}"
                    )
                    return False  # Allow if any resolved IP matches test list
            except (socket.gaierror, ValueError):
                # Ignore errors during test IP check (e.g., DNS resolution failure)
                pass
            except Exception as e:
                # Log unexpected errors during the test resolve check
                logger.warning(f"SSRF test resolve check error for {hostname}: {e}")

        # --- Standard Internal/Private Checks ---
        host_lower = hostname.lower()
        # Check for common internal/test TLDs and hostnames
        if host_lower == "localhost" or host_lower.endswith(
            (".localhost", ".local", ".internal", ".test", ".example", ".invalid")
        ):
            logger.debug(f"SSRF: Blocked internal host pattern: {hostname}")
            return True

        # Resolve hostname to IP addresses
        ips = []
        try:
            # Use getaddrinfo for better IPv4/IPv6 handling
            # Use SOCK_STREAM hint for TCP-based services usually targeted by SSRF
            addr_info = socket.getaddrinfo(
                hostname,
                parsed.port or 0,
                family=socket.AF_UNSPEC,
                type=socket.SOCK_STREAM,
            )
            # Get unique IP addresses from the results
            ips = list(set(info[4][0] for info in addr_info))
            if not ips:
                # Should not happen if getaddrinfo succeeds, but as a safeguard
                logger.debug(
                    f"SSRF: Blocked due to no IP addresses resolved for {hostname}"
                )
                return True
        except socket.gaierror:
            # DNS resolution failed
            logger.debug(f"SSRF: Blocked due to DNS resolution failure for {hostname}")
            return True  # Treat resolution failures as potentially unsafe
        except Exception as e:
            # Catch other potential errors during DNS lookup
            logger.warning(f"SSRF DNS resolution error for {hostname}: {e}")
            return True  # Treat other DNS errors as unsafe

        # Check each resolved IP address
        for ip_str in ips:
            try:
                ip = ipaddress.ip_address(ip_str)
                # Check against various private/internal/reserved ranges
                if (
                    ip.is_private  # e.g., 10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16
                    or ip.is_loopback  # e.g., 127.0.0.1, ::1
                    or ip.is_link_local  # e.g., 169.254.0.0/16, fe80::/10
                    or ip.is_reserved  # Other IANA reserved ranges
                    or ip.is_multicast  # Multicast addresses
                    or ip.is_unspecified  # e.g., 0.0.0.0, ::
                ):
                    logger.debug(
                        f"SSRF: Blocked private/reserved IP {ip_str} resolved for {hostname}"
                    )
                    return True  # Found an internal/private IP, block the URL
            except ValueError:
                # Handle cases where the resolved string is not a valid IP address
                logger.warning(
                    f"SSRF: Invalid IP address format '{ip_str}' resolved for {hostname}. Blocking."
                )
                return True  # Treat invalid IP formats as unsafe

        # If all resolved IPs are public and valid
        logger.debug(f"SSRF: Allowed public host/IPs: {hostname} resolved to {ips}")
        return False  # URL is considered safe

    except Exception as e:
        # Catch-all for any unexpected errors during the check process
        logger.error(
            f"Unexpected error during SSRF check for '{url}': {e}", exc_info=True
        )
        return True  # Default to blocking in case of unexpected errors


# --- Basic Content Matching Utilities ---


# *** CHANGE: Kept this function here, improved implementation ***
def contains_all_keywords(text: Optional[str], keywords: List[str]) -> bool:
    """
    Check if a given text string contains all specified keywords (case-insensitive).

    Args:
        text: The text content to search within. Can be None.
        keywords: A list of keywords that must all be present.

    Returns:
        True if text is not None and contains all non-empty keywords, False otherwise.
    """
    # If there's no text to search in, keywords cannot be contained.
    if text is None:
        return False

    # Normalize and filter keywords: lowercase and remove empty/None entries.
    lowered_keywords = [kw.lower() for kw in keywords if kw and kw.strip()]

    # If, after filtering, the list of keywords is empty, what should happen?
    # Option 1: Return True (vacuously true, no keywords needed to be found).
    # Option 2: Return False (intent is usually to find *something*).
    # Choosing Option 2 as it aligns better with typical search intent.
    if not lowered_keywords:
        return True  # Vacuously true: contains all keywords from an empty list

    # Normalize the text to lowercase for case-insensitive comparison.
    text_lower = text.lower()

    # Check if *all* the normalized keywords are present in the lowercased text.
    # The `all()` function returns True only if the condition is met for every keyword.
    return all(keyword in text_lower for keyword in lowered_keywords)


# --- _is_json_like and _find_block_lines were moved to searcher/helpers.py ---
# Ensure they are NOT present here anymore.


# --- Example Usage (if run directly) ---
if __name__ == "__main__":
    print("--- Top-Level Utility Function Examples ---")
    logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")

    print("\n--- Canonicalization ---")
    urls_to_canon = [
        "http://Example.Com:80/Path/",
        "https://example.com:443/path/../other/",
        "example.com/test?query=1#frag",
        "//cdn.com/lib",
        "http://example.com/%7Euser/",  # Test percent decoding
        "http://example.com",
        "example.com",
        "",  # Test empty
    ]
    for url in urls_to_canon:
        try:
            print(f"'{url}' -> '{canonicalize_url(url)}'")
        except ValueError as e:
            print(f"'{url}' -> ERROR: {e}")

    print("\n--- SSRF Checks ---")
    ssrf_urls_to_test = [
        "http://google.com",  # Public
        "http://localhost:8000",  # Loopback host
        "http://127.0.0.1",  # Loopback IP
        "http://192.168.1.1",  # Private IP
        "http://10.0.0.5",  # Private IP
        "http://172.16.10.1",  # Private IP
        "http://[::1]",  # Loopback IPv6
        "http://example.local",  # Internal TLD pattern
        "ftp://example.com",  # Different scheme (check behavior)
        "http://169.254.1.1",  # Link-local IP
    ]
    # Try resolving a known public host to test against its resolved IP
    try:
        # Use a common, likely stable public domain
        public_host = "one.one.one.one"  # Cloudflare DNS
        addr_info = socket.getaddrinfo(public_host, None, family=socket.AF_UNSPEC)
        public_ip = addr_info[0][4][0]  # Get the first resolved IP
        ssrf_urls_to_test.append(f"http://{public_ip}")
        print(f"(Checking against public IP: {public_ip} for {public_host})")
    except socket.gaierror as e:
        print(f"Warning: Cannot resolve {public_host} for SSRF test: {e}")

    for url in ssrf_urls_to_test:
        print(f"'{url}' -> Internal/Private: {is_url_private_or_internal(url)}")

    # --- CHANGE: Added more keyword test cases ---
    print("\n--- Keyword Check ---")
    text_sample = "Sample document with KEYWORDS and Text."
    print(f"Testing text: '{text_sample}'")
    print(
        f"... contains ['sample', 'keywords']: {contains_all_keywords(text_sample, ['sample', 'keywords'])}"
    )
    print(
        f"... contains ['Sample', 'Keywords', 'TEXT']: {contains_all_keywords(text_sample, ['Sample', 'Keywords', 'TEXT'])}"
    )
    print(
        f"... contains ['sample', 'missing']: {contains_all_keywords(text_sample, ['sample', 'missing'])}"
    )
    print(
        f"... contains ['sample', None, 'text']: {contains_all_keywords(text_sample, ['sample', None, 'text'])}"
    )  # Test filtering None
    print(
        f"... contains []: {contains_all_keywords(text_sample, [])}"
    )  # Test empty list
    print(
        f"... contains ['']: {contains_all_keywords(text_sample, [''])}"
    )  # Test list with empty string
    print(f"Testing text: None")
    print(
        f"... contains ['keyword']: {contains_all_keywords(None, ['keyword'])}"
    )  # Test None text
# Corrected indentation for the success message block
print("\n------------------------------------")
print("✓ Utils usage examples executed successfully.")
print("------------------------------------")
print("\n--- Top-Level Utils Examples Finished ---")
