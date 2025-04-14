"""
Description:
    This module provides individual URL fetching functions used by the web_downloader
    module. It includes two main functions:
    - fetch_single_url_requests: Uses httpx for standard HTTP requests
    - fetch_single_url_playwright: Uses Playwright for JavaScript-rendered pages

Third-Party Documentation:
    - httpx: https://www.python-httpx.org/
    - playwright: https://playwright.dev/python/

Sample Input/Output:
    url = "https://example.com"
    target_path = "./downloads/content/example.com/page.html"
    result = await fetch_single_url_requests(
        url=url,
        target_local_path=target_path,
        force=False,
        allowed_base_dir="./downloads/content",
        timeout=30,
        client=httpx.AsyncClient(),
        max_size=10_000_000
    )
    # Returns: {
    #     "status": "success",
    #     "target_path": "./downloads/content/example.com/page.html",
    #     "content_md5": "d41d8cd98f00b204e9800998ecf8427e",
    #     "http_status": 200,
    #     "detected_links": ["http://example.com/other.html"],
    # }
"""

import asyncio
import hashlib
import logging
import os
from pathlib import Path
from typing import Dict, Any, Optional, List
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup
from playwright.async_api import async_playwright

logger = logging.getLogger(__name__)

async def fetch_single_url_requests(
    url: str,
    target_local_path: str,
    force: bool = False,
    allowed_base_dir: str = "",
    timeout: int = 30,
    client: Optional[httpx.AsyncClient] = None,  # Accepts optional client
    max_size: Optional[int] = None,
) -> Dict[str, Any]:
    """
    Fetches a single URL using httpx and saves it to the target path.
    Correctly handles optionally provided httpx.AsyncClient instances.

    Args:
        url: The URL to fetch
        target_local_path: Where to save the downloaded content (string path)
        force: Whether to overwrite existing files
        allowed_base_dir: Base directory path (string) that target_local_path must be under
        timeout: Request timeout in seconds
        client: Optional pre-configured and managed httpx.AsyncClient instance.
                If provided, this function will *not* close it.
                If None, a temporary client will be created and closed.
        max_size: Maximum file size to download (in bytes)

    Returns:
        Dictionary with status information and results
    """
    target_path = Path(target_local_path)
    allowed_base = Path(allowed_base_dir).resolve() if allowed_base_dir else None
    http_status_code = None  # Initialize

    # --- Path and Pre-check Validation ---
    if allowed_base:
        try:
            # Ensure target path resolves and is within the allowed base
            resolved_target = target_path.resolve()
            if not str(resolved_target).startswith(str(allowed_base)):
                raise ValueError(
                    f"Target path {resolved_target} is outside allowed base {allowed_base}"
                )
        except Exception as path_val_e:
            logger.error(
                f"Path validation failed for '{target_local_path}': {path_val_e}"
            )
            return {
                "status": "failed",
                "error_message": f"Target path validation failed: {path_val_e}",
            }

    if target_path.exists() and not force:
        logger.info(f"Skipping existing file (force=False): {target_path}")
        return {
            "status": "skipped",
            "target_path": str(target_path),
            "error_message": "File exists and force=False",
        }

    try:
        target_path.parent.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        logger.error(
            f"Failed to create directory {target_path.parent}: {e}", exc_info=True
        )
        return {
            "status": "failed",
            "error_message": f"Failed to create directory {target_path.parent}: {e}",
        }

    # --- Client Handling and Download ---
    local_client = None  # To hold a client if we create one locally
    try:
        if client is None:
            # No client provided, create and manage one locally
            logger.debug(
                f"No shared client provided for {url}, creating temporary client."
            )
            # Note: follow_redirects=True is the default in AsyncClient
            local_client = httpx.AsyncClient(timeout=timeout, follow_redirects=True)
            client_to_use = local_client
        else:
            # Use the provided shared client
            logger.debug(f"Using shared client for {url}.")
            client_to_use = client

        # *** CORE FIX: Use the selected client directly for the request ***
        # *** Do NOT use 'async with client_to_use:' here ***
        logger.debug(
            f"Sending GET stream request to {url} using {'local' if local_client else 'shared'} client."
        )
        async with client_to_use.stream("GET", url) as response:
            http_status_code = response.status_code  # Store status code early
            logger.debug(f"Received response for {url}: Status {http_status_code}")

            # Check for non-success status codes (e.g., 404, 500)
            if not response.is_success:
                error_msg = f"HTTP error {http_status_code} for URL: {url}"
                logger.warning(error_msg)
                # Attempt to read body for more info, but don't fail if it raises error
                try:
                    body_preview = (await response.aread())[:500].decode(
                        "utf-8", errors="replace"
                    )
                    error_msg += f" - Body Preview: {body_preview}"
                except Exception:
                    pass  # Ignore if reading body fails on error status
                return {
                    "status": "failed",
                    "error_message": error_msg,
                    "http_status": http_status_code,
                }

            # Check content length before reading if max_size specified
            content_length_header = response.headers.get("content-length")
            if max_size and content_length_header:
                try:
                    content_length = int(content_length_header)
                    if content_length > max_size:
                        logger.warning(
                            f"Content-Length {content_length} exceeds max_size {max_size} for {url}"
                        )
                        return {
                            "status": "failed",
                            "error_message": f"Content-Length {content_length} exceeds max_size {max_size}",
                            "http_status": http_status_code,
                        }
                except ValueError:
                    logger.warning(
                        f"Invalid Content-Length header '{content_length_header}' for {url}"
                    )

            # Read the response body content
            logger.debug(f"Reading content for {url}")
            content = await response.aread()
            logger.debug(f"Read {len(content)} bytes for {url}")

            # Check actual size if max_size specified (in case Content-Length was missing/wrong)
            if max_size and len(content) > max_size:
                logger.warning(
                    f"Downloaded content size {len(content)} exceeds max_size {max_size} for {url}"
                )
                return {
                    "status": "failed",
                    "error_message": f"Downloaded content size {len(content)} exceeds max_size {max_size}",
                    "http_status": http_status_code,
                }

            # --- Process Content (Inside Response Context) ---
            logger.debug(f"Processing content for {url} inside response context.")
            content_md5 = hashlib.md5(content).hexdigest()
            detected_links = []
            # Access headers *inside* the context block
            content_type = response.headers.get("content-type", "").lower()

            if "html" in content_type:
                logger.debug(f"Content type for {url} is HTML, extracting links.")
                try:
                    # Use html.parser for resilience, pass the bytes directly
                    soup = BeautifulSoup(content, "html.parser")
                    for a_tag in soup.find_all("a", href=True):
                        href = a_tag["href"].strip()
                        # Basic filtering of non-navigational links
                        if href and not href.startswith(
                            ("#", "javascript:", "mailto:", "tel:")
                        ):
                            try:
                                # Resolve relative URLs against the fetched URL
                                abs_url = urljoin(url, href)
                                detected_links.append(abs_url)
                            except ValueError:
                                logger.warning(
                                    f"Could not resolve relative link '{href}' from base '{url}'"
                                )
                    logger.debug(f"Extracted {len(detected_links)} links from {url}")
                except Exception as e:
                    logger.warning(f"Error extracting links from {url}: {e}", exc_info=True)
            else:
                logger.debug(
                    f"Content type '{content_type}' for {url} is not HTML, skipping link extraction."
                )

            # --- Write Content to File (Revised Error Handling) ---
            file_written_successfully = False # Flag to track success
            try:
                logger.debug(f"Attempting to write {len(content)} bytes to {target_path}")
                target_path.write_bytes(content)
                # If write_bytes completes without error, set the flag
                file_written_successfully = True
                logger.info(f"Successfully saved {url} to {target_path}")
            except OSError as e: # Catch specific OS-level errors like permissions, disk full, etc.
                error_msg = f"Failed to write file {target_path} due to OSError: {e}"
                logger.error(error_msg, exc_info=True)
                return {
                    "status": "failed",
                    "error_message": error_msg,
                    "http_status": http_status_code,
                    "content_md5": content_md5,
                }
            except Exception as e: # Catch any other unexpected errors during write
                error_msg = f"Unexpected error writing file {target_path}: {e}"
                logger.error(error_msg, exc_info=True)
                return {
                    "status": "failed",
                    "error_message": error_msg,
                    "http_status": http_status_code,
                    "content_md5": content_md5,
                }

            # --- Success (Only if file writing flag is True) ---
            if file_written_successfully:
                return {
                    "status": "success",
                    "target_path": str(target_path),
                    "content_md5": content_md5,
                    "http_status": http_status_code,
                    "detected_links": detected_links,
                }
            else:
                # This case should ideally not be reached if exceptions are caught,
                # but acts as a safeguard.
                logger.error(f"File writing did not succeed for {target_path}, but no specific exception was caught.")
                return {
                    "status": "failed",
                    "error_message": "File writing failed for unknown reason after download.",
                    "http_status": http_status_code,
                    "content_md5": content_md5,
                }
            # --- End processing inside response context ---

    # --- Exception Handling ---
    except httpx.TimeoutException as e:
        logger.warning(f"Request timed out for {url}: {e}")
        return {
            "status": "failed",
            "error_message": f"Request timed out: {e}",
            "http_status": http_status_code,
        }
    except httpx.RequestError as e:
        # Covers connection errors, DNS errors, etc.
        logger.warning(f"HTTP request error for {url}: {e}")
        return {
            "status": "failed",
            "error_message": f"Request error: {e}",
            "http_status": http_status_code,
        }
    except Exception as e:
        # Catch-all for other unexpected errors during the process
        logger.error(f"Unexpected error fetching {url}: {e}", exc_info=True)
        return {
            "status": "failed",
            "error_message": f"Unexpected error: {e}",
            "http_status": http_status_code,  # Include status if available
        }
    finally:
        # --- Explicitly close the client *only if* it was created locally ---
        if local_client:
            try:
                logger.debug(f"Closing locally created client for {url}.")
                await local_client.aclose()
            except Exception as close_e:
                logger.warning(f"Error closing locally created httpx client: {close_e}")

async def fetch_single_url_playwright(
    url: str,
    target_local_path: str,
    force: bool = False,
    allowed_base_dir: str = "",
    timeout: int = 30,
) -> Dict[str, Any]:
    """
    Fetches a single URL using Playwright and saves it to the target path.
    This function handles JavaScript-rendered pages.

    Args:
        url: The URL to fetch
        target_local_path: Where to save the downloaded content
        force: Whether to overwrite existing files
        allowed_base_dir: Base directory that target_local_path must be under
        timeout: Maximum time to wait for page load in seconds

    Returns:
        Dictionary with status information and results
    """
    target_path = Path(target_local_path)
    allowed_base = Path(allowed_base_dir).resolve() if allowed_base_dir else None

    # Validate target path is under allowed base directory
    if allowed_base and not str(target_path.resolve()).startswith(str(allowed_base)):
        return {
            "status": "failed",
            "error_message": f"Target path {target_path} not under allowed base directory {allowed_base}",
        }

    # Check if file exists and force is False
    if target_path.exists() and not force:
        return {
            "status": "skipped",
            "target_path": str(target_path),
            "error_message": "File exists and force=False",
        }

    # Ensure parent directory exists
    try:
        target_path.parent.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        return {
            "status": "failed",
            "error_message": f"Failed to create directory {target_path.parent}: {e}",
        }

    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch()
            page = await browser.new_page()
            
            try:
                response = await page.goto(url, timeout=timeout * 1000, wait_until='networkidle')
                if not response:
                    return {
                        "status": "failed",
                        "error_message": "No response received from page",
                    }
                
                if not response.ok:
                    return {
                        "status": "failed",
                        "error_message": f"HTTP {response.status}",
                        "http_status": response.status,
                    }

                # Get rendered HTML
                content = await page.content()
                content_bytes = content.encode('utf-8')

                # Calculate MD5
                content_md5 = hashlib.md5(content_bytes).hexdigest()

                # Extract links
                detected_links = []
                try:
                    for a in await page.query_selector_all('a[href]'):
                        href = await a.get_attribute('href')
                        if href and not href.startswith(('#', 'javascript:', 'mailto:')):
                            abs_url = urljoin(url, href)
                            detected_links.append(abs_url)
                except Exception as e:
                    logger.warning(f"Error extracting links from {url}: {e}")

                # Write content
                file_written_successfully = False # Flag to track success
                try:
                    logger.debug(f"Attempting to write {len(content_bytes)} bytes (Playwright) to {target_path}")
                    target_path.write_bytes(content_bytes)
                    file_written_successfully = True
                    logger.info(f"Successfully saved (Playwright) {url} to {target_path}")
                except OSError as e:
                    error_msg = f"Failed to write file (Playwright) {target_path} due to OSError: {e}"
                    logger.error(error_msg, exc_info=True)
                    return {
                        "status": "failed",
                        "error_message": error_msg,
                        "http_status": response.status if response else None,
                    }
                except Exception as e:
                    error_msg = f"Unexpected error writing file (Playwright) {target_path}: {e}"
                    logger.error(error_msg, exc_info=True)
                    return {
                        "status": "failed",
                        "error_message": error_msg,
                        "http_status": response.status if response else None,
                    }

                if file_written_successfully:
                    # Note: The original success return block starting at line 447 is kept,
                    # this diff only replaces the try/except block for writing.
                    pass # Placeholder, the actual success return is handled later
                else:
                    logger.error(f"File writing did not succeed (Playwright) for {target_path}, returning failure.")
                    return {
                        "status": "failed",
                        "error_message": "File writing failed for unknown reason after download (Playwright).",
                        "http_status": response.status if response else None,
                    }

                # --- Success/Failure Return (Based on Flag) ---
                if file_written_successfully:
                    # This return was already here, just moved after the flag check
                    return {
                        "status": "success",
                        "target_path": str(target_path),
                        "content_md5": content_md5,
                        "http_status": response.status,
                        "detected_links": detected_links,
                    }
                else:
                    logger.error(f"File writing did not succeed (Playwright) for {target_path}, returning failure.")
                    return {
                        "status": "failed",
                        "error_message": "File writing failed for unknown reason after download (Playwright).",
                        "http_status": response.status if response else None,
                    }

                return {
                    "status": "success",
                    "target_path": str(target_path),
                    "content_md5": content_md5,
                    "http_status": response.status,
                    "detected_links": detected_links,
                }

            except Exception as e:
                if 'ERR_BLOCKED_BY_CLIENT' in str(e):
                    return {
                        "status": "failed_paywall",
                        "error_message": "Blocked by paywall/client-side protection",
                    }
                return {
                    "status": "failed",
                    "error_message": f"Navigation failed: {str(e)}",
                }
            finally:
                await page.close()
                await browser.close()

    except Exception as e:
        return {
            "status": "failed",
            "error_message": f"Playwright error: {str(e)}",
        }

# Standalone test
if __name__ == "__main__":
    import sys
    import tempfile
    
    async def test_fetchers():
        logging.basicConfig(level=logging.INFO)
        
        with tempfile.TemporaryDirectory() as tmpdir:
            test_url = "https://httpbin.org/html"
            target_path = os.path.join(tmpdir, "test.html")
            
            print("\nTesting fetch_single_url_requests...")
            result1 = await fetch_single_url_requests(
                url=test_url,
                target_local_path=target_path,
                force=True,
                allowed_base_dir=tmpdir,
            )
            print(f"Result: {result1}")
            assert result1["status"] == "success", "requests fetch failed"
            
            print("\nTesting fetch_single_url_playwright...")
            result2 = await fetch_single_url_playwright(
                url=test_url,
                target_local_path=target_path.replace(".html", "_pw.html"),
                force=True,
                allowed_base_dir=tmpdir,
            )
            print(f"Result: {result2}")
            assert result2["status"] == "success", "playwright fetch failed"
            
            print("\nAll tests passed!")

    if os.environ.get("PYTEST_CURRENT_TEST"):
        print("Skipping standalone tests when running under pytest")
    else:
        asyncio.run(test_fetchers())
