"""
Module: fetchers.py

Description:
Provides asynchronous functions to fetch content from URLs using different methods:
- fetch_single_url_requests: Uses the httpx library for standard HTTP requests.
- fetch_single_url_playwright: Uses Playwright for fetching dynamic/JS-heavy content.

Handles path sanitization, atomic writes, size limits, link detection, and error reporting.

Third-party packages:
- httpx: https://www.python-httpx.org/
- aiofiles: https://github.com/Tinche/aiofiles
- Playwright: https://playwright.dev/python/ (via fetch_single_url_playwright)

Sample input (fetch_single_url_requests):
url = "https://example.com"
target_local_path = "./downloads_test/requests_test.html"
result = await fetch_single_url_requests(url, target_local_path, force=True)

Sample output (fetch_single_url_requests):
{
  'status': 'success',
  'content_md5': '...',
  'detected_links': [...],
  'error_message': None,
  'http_status': 200
}

Sample input (fetch_single_url_playwright):
url = "https://docs.python.org/3/"
target_local_path = "./downloads_test/playwright_test.html"
result = await fetch_single_url_playwright(url, target_local_path, force=True)

Sample output (fetch_single_url_playwright):
{
  'status': 'success',
  'content_md5': '...',
  'detected_links': [...],
  'error_message': None,
  'http_status': 200 # Now includes http_status
}
"""

import httpx
import aiofiles
import tempfile
import os
import urllib.parse
import traceback
import logging
import hashlib
import re
from contextlib import nullcontext
from typing import Tuple, Optional, Literal

# Playwright imports moved inside function to keep dependency optional
# from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeoutError, Error as PlaywrightError

from mcp_doc_retriever.utils import (
    TIMEOUT_REQUESTS,
    TIMEOUT_PLAYWRIGHT,
    playwright_semaphore,
)

logger = logging.getLogger(__name__)


# --- Helper Function for Path Preparation ---
async def _prepare_target_path(
    target_local_path: str, allowed_base_dir: str, force: bool
) -> Tuple[Optional[str], Optional[Literal["skipped", "failed"]], Optional[str]]:
    """
    Sanitizes path, ensures target directory exists, and checks for existing file.

    Returns:
        Tuple: (normalized_target_path, status, error_message)
               - norm_target: Absolute, sanitized path if checks pass.
               - status: 'skipped' if file exists and force=False, 'failed' on error, None otherwise.
               - error_message: Description if status is 'failed'.
    """
    norm_target = None
    try:
        # --- Path Sanitization ---
        decoded_path = target_local_path
        for _ in range(5):  # Limit unquoting depth
            if "%" not in decoded_path:
                break
            decoded_path = urllib.parse.unquote(decoded_path)
        decoded_path = decoded_path.replace("\\", "/")

        norm_base = os.path.abspath(allowed_base_dir)
        # If target path already contains base dir, use it directly
        if decoded_path.startswith(norm_base):
            norm_target = os.path.abspath(decoded_path)
        else:
            # Treat target_local_path as relative to base_dir
            # First normalize the path to remove any '..' or '.'
            normalized_path = os.path.normpath(decoded_path)
            # Split into components and rejoin to ensure clean path
            path_parts = []
            for part in normalized_path.split(os.sep):
                if part == '..':
                    if path_parts:
                        path_parts.pop()
                elif part and part != '.':
                    path_parts.append(part)
            clean_path = os.path.join(*path_parts)
            
            # Join with base dir and make absolute
            norm_target = os.path.abspath(os.path.join(norm_base, clean_path))

        # Final check: Does the resolved absolute target path start with the resolved absolute base path?
        # Add os.sep to prevent partial matches (e.g., /base/dir matching /base/directory)
        if not norm_target.startswith(norm_base + os.sep) and norm_target != norm_base:
            error_message = f"Security risk: Target path '{target_local_path}' resolves to '{norm_target}', which is outside allowed base directory '{norm_base}'"
            logger.error(error_message)
            return None, "failed", error_message
        logger.debug(f"Sanitized path: {norm_target}")

        # --- Ensure Target Directory Exists ---
        target_dir = os.path.dirname(norm_target)
        if target_dir:
            try:
                os.makedirs(target_dir, exist_ok=True)
                logger.debug(f"Ensured directory exists: {target_dir}")
            except OSError as mkdir_e:
                error_message = (
                    f"Directory creation failed for {target_dir}: {str(mkdir_e)}"
                )
                logger.error(error_message)
                return None, "failed", error_message

        # --- Atomic Existence Check ---
        if not force and os.path.exists(norm_target):
            logger.info(f"File exists and force=False, skipping: {norm_target}")
            # Return norm_target even if skipped, useful for context
            return norm_target, "skipped", None

        # If all checks pass
        return norm_target, None, None

    except Exception as e:
        error_message = f"Path preparation failed for '{target_local_path}': {e}"
        logger.error(error_message, exc_info=True)
        return None, "failed", error_message


# --- Helper Function for Link Extraction ---
def _extract_links(content_sample: str) -> list[str]:
    """Extracts potential links from a string sample."""
    try:
        detected_links_set = set()
        # Simple regex for href and src attributes
        # Ignores case, finds content within single or double quotes
        links_found = re.findall(
            r"""(?:href|src)\s*=\s*["']([^"']+)["']""", content_sample, re.IGNORECASE
        )
        # Basic filtering
        for link in links_found:
            link = link.strip()
            # Filter out empty links, fragments, javascript, mailto, data URIs
            if link and not link.startswith(("#", "javascript:", "mailto:", "data:")):
                detected_links_set.add(link)
        return list(detected_links_set)
    except Exception as e:
        logger.warning(f"Link extraction failed: {e}", exc_info=True)
        return []


# --- Fetcher Implementations ---


async def fetch_single_url_requests(
    url: str,
    target_local_path: str,
    force: bool = False,
    max_size: int | None = None,
    allowed_base_dir: str = ".",
    timeout: int | None = None,
    client: httpx.AsyncClient | None = None,
) -> dict:
    """
    Download a single URL using httpx.
    """
    result = {
        "status": None,
        "content_md5": None,
        "detected_links": [],
        "error_message": None,
        "http_status": None,
    }
    temp_path = None  # Ensure defined for potential cleanup
    norm_target = None

    # Prepare and validate target path using helper
    norm_target, prep_status, prep_error = await _prepare_target_path(
        target_local_path, allowed_base_dir, force
    )
    if prep_status:
        result["status"] = prep_status
        result["error_message"] = prep_error
        # If skipped, result['status'] is 'skipped', norm_target is valid
        # If failed, result['status'] is 'failed', norm_target is None
        return result
    if not norm_target:  # Should not happen if status is None, but safety check
        result["status"] = "failed"
        result["error_message"] = "Path preparation failed unexpectedly."
        return result

    # Setup client context (shared or temporary)
    if client:
        client_context = nullcontext(client)
    else:
        request_timeout = timeout if timeout is not None else TIMEOUT_REQUESTS
        logger.warning(
            f"Creating temporary httpx client for {url}. Consider passing a shared client."
        )
        client_context = httpx.AsyncClient(
            follow_redirects=True, timeout=request_timeout
        )

    try:  # Outer try block for client and request errors
        async with client_context as active_client:
            try:  # Inner try for request execution and response processing
                logger.debug(f"Making GET request to: {url}")
                request_specific_timeout = (
                    timeout if timeout is not None else active_client.timeout.read
                )
                response = await active_client.get(
                    url, follow_redirects=True, timeout=request_specific_timeout
                )
                result["http_status"] = response.status_code
                logger.debug(
                    f"Received response status {response.status_code} for: {url}"
                )
                response.raise_for_status()  # Check for 4xx/5xx errors

                # --- Header Size Check ---
                content_length_header = response.headers.get("Content-Length")
                if max_size is not None and content_length_header:
                    try:
                        content_length = int(content_length_header)
                        if content_length > max_size:
                            result["status"] = "failed_request"
                            result["error_message"] = (
                                f"File too large based on Content-Length ({content_length} > {max_size})"
                            )
                            logger.warning(f"{result['error_message']} for {url}")
                            return result  # Exit early
                    except ValueError:
                        logger.warning(
                            f"Invalid Content-Length header '{content_length_header}'. Proceeding with streaming check."
                        )

                # --- File Writing ---
                temp_path_to_clean = (
                    None  # Variable to track temp file for finally block
                )
                try:
                    target_dir = os.path.dirname(
                        norm_target
                    )  # Already created by _prepare_target_path
                    fd, temp_path = tempfile.mkstemp(
                        dir=target_dir, suffix=".tmp", prefix="mcpdl_"
                    )
                    os.close(fd)
                    temp_path_to_clean = temp_path  # Mark for potential cleanup
                    logger.debug(f"Created temp file: {temp_path}")

                    total_bytes_read = 0
                    md5_hash = hashlib.md5()
                    async with aiofiles.open(temp_path, "wb") as f:
                        async for chunk in response.aiter_bytes(chunk_size=8192):
                            if not chunk:
                                continue
                            total_bytes_read += len(chunk)
                            # --- Streaming Size Check ---
                            if max_size is not None and total_bytes_read > max_size:
                                await f.flush()
                                result["status"] = "failed_request"
                                result["error_message"] = (
                                    f"File exceeds max_size during download ({total_bytes_read} > {max_size})"
                                )
                                logger.warning(f"{result['error_message']} for {url}")
                                raise IOError(
                                    result["error_message"]
                                )  # Trigger cleanup in finally

                            # --- MD5 Hashing ---
                            try:
                                md5_hash.update(chunk)
                            except Exception as md5_e:
                                await f.flush()
                                result["status"] = "failed"
                                result["error_message"] = (
                                    f"MD5 calculation failed: {str(md5_e)}"
                                )
                                logger.error(
                                    f"MD5 calculation failed for {url}: {md5_e}"
                                )
                                raise IOError(
                                    result["error_message"]
                                )  # Trigger cleanup

                            await f.write(chunk)
                        await f.flush()  # Ensure buffer written before rename

                    logger.debug(
                        f"Finished writing {total_bytes_read} bytes to temp file: {temp_path}"
                    )

                    # --- Atomic Rename ---
                    logger.debug(f"Attempting to rename {temp_path} to {norm_target}")
                    # TOCTOU check
                    if not force and os.path.exists(norm_target):
                        logger.warning(
                            f"Target file appeared during download (TOCTOU): {norm_target}. Skipping rename."
                        )
                        result["status"] = "skipped"
                        # Keep temp_path_to_clean set so finally block removes it
                    else:
                        try:
                            os.replace(temp_path, norm_target)
                            # Verify file was actually written
                            if not os.path.exists(norm_target):
                                raise IOError(f"File {norm_target} not found after rename")
                            logger.info(
                                f"Successfully downloaded and verified {url} to {norm_target}"
                            )
                            result["content_md5"] = md5_hash.hexdigest()
                            result["status"] = "success"
                            temp_path_to_clean = (
                                None  # File renamed, don't clean up the original path
                            )
                        except OSError as replace_e:
                            result["status"] = "failed"
                            result["error_message"] = (
                                f"File rename failed: {str(replace_e)}"
                            )
                            logger.error(
                                f"Atomic rename failed {temp_path} -> {norm_target}: {replace_e}"
                            )
                            # Keep temp_path_to_clean set for cleanup

                except Exception as file_e:
                    # Catch errors during file writing (IOError raised above, or other issues)
                    if (
                        result["status"] is None or result["status"] == "success"
                    ):  # Avoid overwriting specific failures
                        result["status"] = "failed"
                    err_msg = f"File operation failed: {str(file_e)}"
                    # Append if error already exists (e.g., size limit reached)
                    result["error_message"] = (
                        f"{result['error_message']} | {err_msg}"
                        if result["error_message"]
                        else err_msg
                    )
                    logger.error(
                        f"File operation failed processing {url} -> {norm_target}: {file_e}",
                        exc_info=True,
                    )
                finally:
                    # Cleanup temp file IF it still exists and wasn't successfully renamed
                    if temp_path_to_clean and os.path.exists(temp_path_to_clean):
                        logger.debug(f"Cleaning up temp file: {temp_path_to_clean}")
                        try:
                            os.remove(temp_path_to_clean)
                        except OSError as remove_e:
                            logger.warning(
                                f"Failed to remove temp file {temp_path_to_clean}: {remove_e}"
                            )

            # --- HTTP/Request Error Handling ---
            except httpx.TimeoutException as e:
                tb = traceback.format_exc()
                logger.warning(f"Timeout error fetching {url}: {e}")
                result["status"] = "failed_request"
                result["error_message"] = f"Timeout: {str(e)} | Traceback: {tb}"
            except httpx.HTTPStatusError as e:
                tb = traceback.format_exc()
                logger.warning(
                    f"HTTP status error for {url}: {e.response.status_code} - {e}"
                )
                result["status"] = "failed_request"
                result["http_status"] = e.response.status_code
                result["error_message"] = (
                    f"HTTP {e.response.status_code}: {str(e)} | Traceback: {tb}"
                )
            except httpx.TransportError as e:
                tb = traceback.format_exc()
                logger.warning(f"Transport error for {url}: {e}")
                result["status"] = "failed_request"
                result["error_message"] = f"Transport: {str(e)} | Traceback: {tb}"
            except httpx.RequestError as e:
                tb = traceback.format_exc()
                logger.warning(f"Request error for {url}: {e}")
                result["status"] = "failed_request"
                result["error_message"] = f"Request: {str(e)} | Traceback: {tb}"
            except Exception as e:  # Catch-all for unexpected client/request errors
                tb = traceback.format_exc()
                logger.error(
                    f"Unexpected error during HTTP request/response handling for {url}: {e}",
                    exc_info=True,
                )
                result["status"] = "failed_request"
                result["error_message"] = (
                    f"Unexpected HTTP Error: {str(e)} | Traceback: {tb}"
                )

            # --- Link Detection (only if successful download) ---
            if result["status"] == "success":
                logger.debug(f"Attempting link detection in {norm_target}")
                try:
                    read_limit = 1 * 1024 * 1024  # 1 MB limit
                    async with aiofiles.open(
                        norm_target, "r", encoding="utf-8", errors="ignore"
                    ) as f:
                        content_sample = await f.read(read_limit)
                    result["detected_links"] = _extract_links(
                        content_sample
                    )  # Use helper
                    logger.debug(
                        f"Detected {len(result['detected_links'])} unique candidate links in {norm_target}"
                    )
                except Exception as link_e:
                    logger.warning(
                        f"Link detection failed for {norm_target}: {link_e}",
                        exc_info=True,
                    )
                    result["detected_links"] = []

    except Exception as outer_e:
        # Catch errors during setup (e.g., client creation, initial checks)
        tb = traceback.format_exc()
        logger.error(f"Outer setup error for {url}: {outer_e}", exc_info=True)
        result["status"] = "failed"
        result["error_message"] = f"Setup error: {str(outer_e)} | Traceback: {tb}"

    # Final check: Ensure status is set if it somehow remained None
    if result["status"] is None:
        logger.error(
            f"Result status was None for {url} at function end. Setting to failed."
        )
        result["status"] = "failed"
        if result["error_message"] is None:
            result["error_message"] = "Unknown error: download status was None."

    # Ensure error message is None if status is success
    if result["status"] == "success":
        result["error_message"] = None

    return result


async def fetch_single_url_playwright(
    url: str,
    target_local_path: str,
    force: bool = False,
    allowed_base_dir: str = ".",
    timeout: int | None = None,
    # Note: max_size is not implemented here
) -> dict:
    """
    Download a single URL using Playwright. Captures HTTP status.
    """
    # Import Playwright here to make it an optional dependency
    try:
        from playwright.async_api import (
            async_playwright,
            TimeoutError as PlaywrightTimeoutError,
            Error as PlaywrightError,
        )
    except ImportError:
        logger.error(
            "Playwright not installed. Cannot use fetch_single_url_playwright."
        )
        return {
            "status": "failed",
            "error_message": "Playwright not installed.",
            "content_md5": None,
            "detected_links": [],
            "http_status": None,
        }

    result = {
        "status": None,
        "content_md5": None,
        "detected_links": [],
        "error_message": None,
        "http_status": None,  # Added http_status field
    }
    temp_path = None
    norm_target = None
    context = None  # Define context and browser outside try for finally block
    browser = None

    async with playwright_semaphore:  # Control concurrency
        try:
            # Prepare and validate target path using helper
            norm_target, prep_status, prep_error = await _prepare_target_path(
                target_local_path, allowed_base_dir, force
            )
            if prep_status:
                result["status"] = prep_status
                result["error_message"] = prep_error
                return result
            if not norm_target:
                result["status"] = "failed"
                result["error_message"] = "Path preparation failed unexpectedly."
                return result

            effective_timeout_ms = (timeout or TIMEOUT_PLAYWRIGHT) * 1000
            content = None  # Ensure content is defined

            async with async_playwright() as p:
                try:
                    # --- Launch Browser and Context ---
                    browser = await p.chromium.launch(headless=True)
                    context = await browser.new_context(
                        java_script_enabled=True,
                        bypass_csp=True,
                        ignore_https_errors=True,
                        viewport={"width": 1280, "height": 800},
                        user_agent="MCPBot/1.0 (compatible; Playwright)",
                        locale="en-US",
                    )
                    # Block non-essential resources
                    await context.route(
                        "**/*",
                        lambda route: route.abort()
                        if route.request.resource_type
                        in ["image", "media", "font", "stylesheet"]
                        else route.continue_(),
                    )

                    page = await context.new_page()

                    # --- Navigation and Content Retrieval ---
                    logger.debug(f"Playwright navigating to: {url}")
                    # Use wait_until='domcontentloaded' for faster interaction start
                    response = await page.goto(
                        url,
                        wait_until="domcontentloaded",
                        timeout=effective_timeout_ms
                    )

                    # --- Capture HTTP Status ---
                    if response:
                        result["http_status"] = response.status
                        logger.debug(
                            f"Playwright navigation response status: {response.status}"
                        )
                        if (
                            not 200 <= response.status < 400
                        ):  # Treat redirects (3xx) as potentially okay for getting content, but flag non-2xx/3xx as errors
                            result["status"] = (
                                "failed_request"  # Or map specific codes?
                            )
                            result["error_message"] = (
                                f"HTTP error {response.status} during navigation."
                            )
                            logger.warning(f"{result['error_message']} for {url}")
                            # Don't necessarily raise here, let finally close, but prevent success status later
                    else:
                        logger.warning(
                            f"Playwright page.goto did not return a response object for {url}."
                        )
                        result["status"] = (
                            "failed_request"  # Treat as failure if no response
                        )
                        result["error_message"] = (
                            "Playwright navigation yielded no response."
                        )

                    # Proceed only if navigation seemed okay (status might still be non-200, handled later)
                    if result["status"] != "failed_request":
                        # Optional: Add waits if content loads dynamically after DOMContentLoaded
                        # await page.wait_for_selector('#main-content', timeout=effective_timeout_ms/2)
                        # await page.wait_for_load_state('networkidle', timeout=effective_timeout_ms/2)

                        # Optional: Remove scripts (consider if needed)
                        # await page.evaluate("() => { document.querySelectorAll('script').forEach(s => s.remove()); }")

                        content = await page.content()
                        logger.debug(
                            f"Playwright successfully retrieved content frame for: {url}"
                        )

                # --- Playwright Specific Error Handling ---
                except PlaywrightTimeoutError as e:
                    tb = traceback.format_exc()
                    logger.warning(f"Playwright timeout error for {url}: {e}")
                    result["status"] = "failed_request"
                    result["error_message"] = (
                        f"Playwright Timeout: {str(e)} | Traceback: {tb}"
                    )
                except PlaywrightError as e:  # Catch other Playwright-specific errors
                    tb = traceback.format_exc()
                    if result["status"] is None:
                        result["status"] = "failed_request"
                    pw_err_msg = f"Playwright Error: {str(e)} | Traceback: {tb}"
                    result["error_message"] = (
                        f"{result['error_message']} | {pw_err_msg}"
                        if result["error_message"]
                        else pw_err_msg
                    )
                    logger.warning(f"Playwright error for {url}: {e}")
                except Exception as e:  # Catch unexpected errors during Playwright ops
                    tb = traceback.format_exc()
                    if result["status"] is None:
                        result["status"] = "failed_request"
                    gen_err_msg = (
                        f"Unexpected Playwright Ops Error: {str(e)} | Traceback: {tb}"
                    )
                    result["error_message"] = (
                        f"{result['error_message']} | {gen_err_msg}"
                        if result["error_message"]
                        else gen_err_msg
                    )
                    logger.error(
                        f"Unexpected error during Playwright operation for {url}: {e}",
                        exc_info=True,
                    )
                finally:
                    # Ensure browser and context are closed
                    if context:
                        try:
                            await context.close()
                        except Exception as close_e:
                            logger.warning(
                                f"Error closing Playwright context: {close_e}"
                            )
                    if browser:
                        try:
                            await browser.close()
                        except Exception as close_e:
                            logger.warning(
                                f"Error closing Playwright browser: {close_e}"
                            )

            # --- File Writing & Post-processing (only if content was retrieved and no critical error occurred) ---
            if (
                content is not None
                and result["status"] != "failed_request"
                and result["status"] != "failed"
            ):
                temp_path_to_clean = None
                try:
                    target_dir = os.path.dirname(norm_target)
                    fd, temp_path = tempfile.mkstemp(
                        dir=target_dir, suffix=".tmp", prefix="mcpdl_pw_"
                    )
                    os.close(fd)
                    temp_path_to_clean = temp_path
                    logger.debug(f"Created Playwright temp file: {temp_path}")

                    content_bytes = content.encode("utf-8")
                    md5_hash = hashlib.md5(content_bytes)

                    async with aiofiles.open(temp_path, "wb") as f:
                        await f.write(content_bytes)
                    logger.debug(
                        f"Finished writing {len(content_bytes)} bytes from Playwright to temp file: {temp_path}"
                    )

                    # --- Atomic Rename ---
                    logger.debug(
                        f"Attempting Playwright rename {temp_path} to {norm_target}"
                    )
                    if not force and os.path.exists(norm_target):
                        logger.warning(
                            f"Target file appeared during Playwright processing (TOCTOU): {norm_target}. Skipping."
                        )
                        result["status"] = "skipped"  # Set status to skipped
                        # Keep temp_path_to_clean set for cleanup
                    else:
                        try:
                            os.replace(temp_path, norm_target)
                            logger.info(
                                f"Successfully saved Playwright content for {url} to {norm_target}"
                            )
                            result["content_md5"] = md5_hash.hexdigest()
                            result["status"] = (
                                "success"  # Set status to success *only* after successful write & rename
                            )
                            temp_path_to_clean = (
                                None  # Don't clean up after successful rename
                            )
                        except OSError as replace_e:
                            result["status"] = "failed"
                            result["error_message"] = (
                                f"Playwright file rename failed: {str(replace_e)}"
                            )
                            logger.error(
                                f"Playwright atomic rename failed {temp_path} -> {norm_target}: {replace_e}"
                            )
                            # Keep temp_path_to_clean set for cleanup

                except Exception as file_e:
                    if result["status"] is None or result["status"] == "success":
                        result["status"] = "failed"
                    file_err_msg = f"Playwright file operation failed: {str(file_e)}"
                    result["error_message"] = (
                        f"{result['error_message']} | {file_err_msg}"
                        if result["error_message"]
                        else file_err_msg
                    )
                    logger.error(
                        f"Playwright file operation failed processing {url} -> {norm_target}: {file_e}",
                        exc_info=True,
                    )
                finally:
                    if temp_path_to_clean and os.path.exists(temp_path_to_clean):
                        logger.debug(
                            f"Cleaning up Playwright temp file: {temp_path_to_clean}"
                        )
                        try:
                            os.remove(temp_path_to_clean)
                        except OSError as remove_e:
                            logger.warning(
                                f"Failed to remove Playwright temp file {temp_path_to_clean}: {remove_e}"
                            )

                # --- Link Detection ---
                if result["status"] == "success":
                    logger.debug(
                        f"Attempting Playwright link detection in {norm_target}"
                    )
                    content_sample = content[: 1 * 1024 * 1024]  # Limit sample size
                    result["detected_links"] = _extract_links(
                        content_sample
                    )  # Use helper
                    logger.debug(
                        f"Detected {len(result['detected_links'])} unique candidate links via Playwright for {url}"
                    )
            elif (
                result["status"] is None
            ):  # If no specific error occurred but content is None or status is still None
                logger.warning(
                    f"Playwright fetch for {url} did not result in success or specific failure, marking as failed."
                )
                result["status"] = "failed_request"
                if result["error_message"] is None:
                    result["error_message"] = (
                        "Playwright fetch did not complete successfully (check logs for navigation errors)."
                    )

        except Exception as outer_e:
            # Catch errors during path prep or semaphore acquisition etc.
            if result["status"] is None:
                result["status"] = "failed"  # Ensure status is set
            outer_err_msg = f"Outer Playwright Error: {str(outer_e)}"
            result["error_message"] = (
                f"{result['error_message']} | {outer_err_msg}"
                if result["error_message"]
                else outer_err_msg
            )
            logger.error(
                f"Outer error during Playwright fetch for {url}: {outer_e}",
                exc_info=True,
            )

    # Final status check
    if result["status"] is None:
        logger.error(f"Playwright result status None for {url}. Setting to failed.")
        result["status"] = "failed"
        if result["error_message"] is None:
            result["error_message"] = "Unknown Playwright error."

    # Ensure error message is None if status is success
    if result["status"] == "success":
        result["error_message"] = None

    return result


def usage_example():
    """Demonstrates programmatic usage of both fetchers."""
    import asyncio

    # Ensure the test directory exists
    test_base_dir = "./fetchers_usage_example"
    try:
        os.makedirs(test_base_dir, exist_ok=True)
    except OSError as e:
        print(f"Error creating test directory {test_base_dir}: {e}")
        return  # Cannot run example if dir fails

    # Basic logging setup for example
    logging.basicConfig(
        level=logging.INFO, format="[%(levelname)s] %(name)s: %(message)s"
    )
    logger.info(
        f"Fetcher usage examples will output to: {os.path.abspath(test_base_dir)}"
    )

    async def run_examples():
        logger.info(f"\n--- Testing fetch_single_url_requests ---")
        req_target = os.path.join(test_base_dir, "requests_example.html")
        req_result = await fetch_single_url_requests(
            url="https://example.com",
            target_local_path=req_target,
            force=True,
            allowed_base_dir=test_base_dir,
        )
        logger.info(f"Requests fetch result: {req_result}")
        if os.path.exists(req_target):
            logger.info(f"Requests file created: {req_target}")

        logger.info(f"\n--- Testing fetch_single_url_playwright ---")
        # Check if Playwright is likely installed before running its example
        try:
            from playwright.async_api import async_playwright

            pw_available = True
        except ImportError:
            pw_available = False
            logger.warning(
                "Playwright not installed, skipping Playwright fetcher example."
            )

        if pw_available:
            pw_target = os.path.join(test_base_dir, "playwright_example.html")
            pw_result = await fetch_single_url_playwright(
                url="https://example.com",  # Use simple site for example
                target_local_path=pw_target,
                force=True,
                allowed_base_dir=test_base_dir,
                timeout=30,  # Use a reasonable timeout for the example
            )
            logger.info(f"Playwright fetch result: {pw_result}")
            if os.path.exists(pw_target):
                logger.info(f"Playwright file created: {pw_target}")

    asyncio.run(run_examples())
    logger.info(f"\nFetcher examples finished.")


if __name__ == "__main__":
    usage_example()
