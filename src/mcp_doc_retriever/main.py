"""
Module: utils.py

Description:
Provides shared, general-purpose utility functions for the MCP Document Retriever,
including URL canonicalization, ID generation, security checks (SSRF), basic
keyword matching helpers, and datetime helpers used across different components
like the downloader, searcher, and potentially an API layer.

Third-Party Documentation:
- hashlib: https://docs.python.org/3/library/hashlib.html
- ipaddress: https://docs.python.org/3/library/ipaddress.html
- logging: https://docs.python.org/3/library/logging.html
- re: https://docs.python.org/3/library/re.html
- socket: https://docs.python.org/3/library/socket.html
- pathlib: https://docs.python.org/3/library/pathlib.html
- urllib.parse: https://docs.python.org/3/library/urllib.parse.html
- aiofiles: https://github.com/Tinche/aiofiles

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
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional
from urllib.parse import urlparse, urlunparse, unquote
import asyncio  # Added for async md5
import aiofiles  # Added for async md5


# Assuming config is importable for SSRF override flag
try:
    # Use relative import consistent with package structure
    from . import config
except ImportError:
    print("Warning: Could not import config. Using default values for SSRF check.")

    class MockConfig:
        ALLOW_TEST_INTERNAL_URLS = False

    config = MockConfig()


logger = logging.getLogger(__name__)

# --- Constants ---
TIMEOUT_REQUESTS = 30
TIMEOUT_PLAYWRIGHT = 60


# --- URL Utilities ---
def canonicalize_url(url: str) -> str:
    """Normalize URL for consistent identification and processing."""
    if not isinstance(url, str):
        raise ValueError("URL must be a string.")
    url = url.strip()
    if not url:
        raise ValueError("URL cannot be empty.")
    try:
        if url.startswith("//"):
            url = "http:" + url
        parsed = urlparse(url)
        scheme = parsed.scheme.lower()
        if not scheme:
            if not url.startswith("http://") and not url.startswith("https://"):
                url = "http://" + url
                parsed = urlparse(url)
                scheme = parsed.scheme.lower()
            else:
                raise ValueError("URL scheme could not be determined.")
        netloc = parsed.netloc.lower()
        if ":" in netloc:
            host, port_str = netloc.split(":", 1)
            try:
                port = int(port_str)
                if (scheme == "http" and port == 80) or (
                    scheme == "https" and port == 443
                ):
                    netloc = host
            except ValueError:
                logger.debug(
                    f"Invalid port '{port_str}' in URL '{url}', keeping netloc as is."
                )
                pass
        path = parsed.path if parsed.path else "/"
        path = unquote(path)
        if not path.startswith("/"):
            path = "/" + path
        if path != "/" and path.endswith("/"):
            path = path.rstrip("/")
        return urlunparse((scheme, netloc, path, "", "", ""))
    except Exception as e:
        logger.error(f"Failed to canonicalize URL '{url}': {e}", exc_info=True)
        raise ValueError(f"Could not canonicalize URL: '{url}' - Error: {e}") from e


def generate_download_id(url: str) -> str:
    """Generate a unique download ID (MD5 hash) based on the canonical URL."""
    try:
        canonical_url_str = canonicalize_url(url)
        url_bytes = canonical_url_str.encode("utf-8")
        hasher = hashlib.md5()
        hasher.update(url_bytes)
        return hasher.hexdigest()
    except ValueError as e:
        raise ValueError(
            f"Could not generate download ID for invalid URL: {url}"
        ) from e
    except Exception as e:
        logger.error(f"Error generating download ID for '{url}': {e}", exc_info=True)
        raise ValueError(f"Could not generate download ID for URL: {url}") from e


# --- Security Utilities ---
def is_url_private_or_internal(url: str) -> bool:
    """Checks if a URL resolves to an internal, private, loopback, or reserved IP."""
    try:
        if not isinstance(url, str):
            logger.warning("SSRF check: Received non-string URL input.")
            return True
        parsed = urlparse(url)
        hostname = parsed.hostname
        if not hostname:
            logger.debug(f"SSRF check: Blocked URL with no hostname: {url}")
            return True

        allow_test = getattr(config, "ALLOW_TEST_INTERNAL_URLS", False)
        if allow_test:
            test_hosts = {"host.docker.internal", "localhost", "127.0.0.1"}
            test_ips = {"172.17.0.1"}
            host_lower = hostname.lower().split(":")[0]
            if host_lower in test_hosts:
                logger.debug(f"SSRF: Allowed test host (config override): {hostname}")
                return False
            try:
                addr_info = socket.getaddrinfo(hostname, None, family=socket.AF_UNSPEC)
                resolved_ips = {info[4][0] for info in addr_info}
                if any(ip in test_ips for ip in resolved_ips):
                    logger.debug(
                        f"SSRF: Allowed test IP (config override): {resolved_ips}"
                    )
                    return False
            except (socket.gaierror, ValueError):
                pass
            except Exception as e:
                logger.warning(f"SSRF test resolve check error for {hostname}: {e}")

        host_lower = hostname.lower()
        if host_lower == "localhost" or host_lower.endswith(
            (".localhost", ".local", ".internal", ".test", ".example", ".invalid")
        ):
            logger.debug(f"SSRF: Blocked internal host pattern: {hostname}")
            return True

        ips = []
        try:
            addr_info = socket.getaddrinfo(
                hostname,
                parsed.port or 0,
                family=socket.AF_UNSPEC,
                type=socket.SOCK_STREAM,
            )
            ips = list(set(info[4][0] for info in addr_info))
            if not ips:
                logger.debug(
                    f"SSRF: Blocked due to no IP addresses resolved for {hostname}"
                )
                return True
        except socket.gaierror:
            logger.debug(f"SSRF: Blocked due to DNS resolution failure for {hostname}")
            return True
        except Exception as e:
            logger.warning(f"SSRF DNS resolution error for {hostname}: {e}")
            return True

        for ip_str in ips:
            try:
                ip = ipaddress.ip_address(ip_str)
                if (
                    ip.is_private
                    or ip.is_loopback
                    or ip.is_link_local
                    or ip.is_reserved
                    or ip.is_multicast
                    or ip.is_unspecified
                ):
                    logger.debug(
                        f"SSRF: Blocked private/reserved IP {ip_str} resolved for {hostname}"
                    )
                    return True
            except ValueError:
                logger.warning(
                    f"SSRF: Invalid IP address format '{ip_str}' resolved for {hostname}. Blocking."
                )
                return True

        logger.debug(f"SSRF: Allowed public host/IPs: {hostname} resolved to {ips}")
        return False
    except Exception as e:
        logger.error(
            f"Unexpected error during SSRF check for '{url}': {e}", exc_info=True
        )
        return True


# --- Datetime Helpers ---
def _datetime_to_iso(dt: Optional[datetime]) -> Optional[str]:
    """Converts datetime object to timezone-aware ISO 8601 string (UTC)."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()


def _iso_to_datetime(iso_str: Optional[str]) -> Optional[datetime]:
    """Converts ISO 8601 string back to datetime object."""
    if iso_str is None:
        return None
    try:
        if iso_str.endswith("Z"):
            iso_str = iso_str[:-1] + "+00:00"
        return datetime.fromisoformat(iso_str)
    except ValueError:
        logger.warning(f"Could not parse ISO date string: {iso_str}")
        return None


# --- Basic Content Matching Utilities ---
def contains_all_keywords(text: Optional[str], keywords: List[str]) -> bool:
    """Checks if a given text string contains ALL provided keywords (case-insensitive)."""
    if text is None:
        return False
    lowered_keywords = [kw.lower() for kw in keywords if kw and kw.strip()]
    if not lowered_keywords:
        return True  # Vacuously true if no valid keywords
    text_lower = text.lower()
    return all(keyword in text_lower for keyword in lowered_keywords)


# --- Path Utilities ---
def get_relative_path(full_path: Path, base_path: Path) -> Optional[str]:
    """Calculates the relative path of a file with respect to a base directory."""
    try:
        abs_full_path = full_path.resolve()
        abs_base_path = base_path.resolve()
        relative = abs_full_path.relative_to(abs_base_path)
        return relative.as_posix()  # Use forward slashes
    except ValueError:
        logger.warning(f"Path {full_path} is not inside base path {base_path}")
        return None
    except Exception as e:
        logger.error(
            f"Error calculating relative path for {full_path} from {base_path}: {e}",
            exc_info=True,
        )
        return None


# --- Async File Hashing ---
async def calculate_md5_async(file_path: Path, chunk_size: int = 8192) -> str:
    """Asynchronously calculates the MD5 hash of a file."""
    hasher = hashlib.md5()
    try:
        async with aiofiles.open(file_path, "rb") as f:
            while True:
                chunk = await f.read(chunk_size)
                if not chunk:
                    break
                hasher.update(chunk)
        return hasher.hexdigest()
    except FileNotFoundError:
        logger.error(f"File not found for MD5 calculation: {file_path}")
        raise
    except IOError as e:
        logger.error(f"IOError calculating MD5 for {file_path}: {e}")
        raise
    except Exception as e:
        logger.error(
            f"Unexpected error calculating MD5 for {file_path}: {e}", exc_info=True
        )
        raise


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
        "http://example.com/%7Euser/",
        "http://example.com",
        "example.com",
        "",
    ]
    for url in urls_to_canon:
        try:
            print(f"'{url}' -> '{canonicalize_url(url)}'")
        except ValueError as e:
            print(f"'{url}' -> ERROR: {e}")

    print("\n--- SSRF Checks ---")
    ssrf_urls_to_test = [
        "http://google.com",
        "http://localhost:8000",
        "http://127.0.0.1",
        "http://192.168.1.1",
        "http://10.0.0.5",
        "http://172.16.10.1",
        "http://[::1]",
        "http://example.local",
        "ftp://example.com",
        "http://169.254.1.1",
    ]
    try:
        public_host = "one.one.one.one"
        addr_info = socket.getaddrinfo(public_host, None, family=socket.AF_UNSPEC)
        public_ip = addr_info[0][4][0]
        ssrf_urls_to_test.append(f"http://{public_ip}")
        print(f"(Checking against public IP: {public_ip} for {public_host})")
    except socket.gaierror as e:
        print(f"Warning: Cannot resolve {public_host} for SSRF test: {e}")
    for url in ssrf_urls_to_test:
        print(f"'{url}' -> Internal/Private: {is_url_private_or_internal(url)}")

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
    )
    print(f"... contains []: {contains_all_keywords(text_sample, [])}")
    print(f"... contains ['']: {contains_all_keywords(text_sample, [''])}")
    print(f"Testing text: None")
    print(f"... contains ['keyword']: {contains_all_keywords(None, ['keyword'])}")

    print("\n------------------------------------")
    print("✓ Utils usage examples executed successfully.")
    print("------------------------------------")
    print("\n--- Top-Level Utils Examples Finished ---")
