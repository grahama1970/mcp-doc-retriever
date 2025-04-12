"""
Module: fetchers.py

Description:
Provides asynchronous functions to fetch content from URLs using different methods:
- fetch_single_url_requests: Uses the httpx library for standard HTTP requests.
- fetch_single_url_playwright: Uses Playwright for fetching dynamic/JS-heavy content.

Handles path preparation, atomic writes, size limits, basic link detection,
and reports status, metadata (MD5, HTTP status), and errors. It does NOT perform
deep content analysis (like code block extraction) - that should happen after fetching.

Third-party packages:
- httpx: https://www.python-httpx.org/
- aiofiles: https://github.com/Tinche/aiofiles
- Playwright: https://playwright.dev/python/ (Optional, via fetch_single_url_playwright)
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
import asyncio  # Keep for Semaphore and loop operations
import sys  # Added for exit status in example
import shutil  # Added for example cleanup
from pathlib import Path  # Use pathlib
from contextlib import nullcontext
from typing import Tuple, Optional, Literal, Dict, Any, Set  # Added Set for type hints

# Use relative imports for utils from parent directory
# This might fail on direct execution if not installed correctly (-e)
try:
    # Assuming utils.py defines these constants
    from mcp_doc_retriever.utils import TIMEOUT_REQUESTS, TIMEOUT_PLAYWRIGHT
except ImportError as e:
    # Provide fallback values if import fails (e.g., during direct execution testing)
    # Log a warning, as this indicates a potential environment issue
    logging.warning(f"Could not import utils for timeouts, using defaults: {e}")
    TIMEOUT_REQUESTS = 30  # Default timeout for httpx requests in seconds
    TIMEOUT_PLAYWRIGHT = 60  # Default timeout for Playwright operations in seconds

# --- Semaphores for Concurrency Control ---
# Define semaphores used by fetchers directly within this module
# Limit concurrent Playwright browser instances/contexts to avoid overwhelming resources
playwright_semaphore = asyncio.Semaphore(3)
# Optional: Limit concurrent requests globally if needed, though httpx pooling helps
# requests_semaphore = asyncio.Semaphore(50)

logger = logging.getLogger(__name__)


# --- Helper Function for Path Preparation (Using Pathlib) ---
async def _prepare_target_path(
    target_local_path: str,  # Input path can still be string
    allowed_base_dir: str,  # Input path can still be string
    force: bool,
) -> Tuple[Optional[Path], Optional[Literal["skipped", "failed"]], Optional[str]]:
    """
    Validates path, ensures target directory exists, checks for existing file.
    Returns a validated absolute Path object if successful.

    Args:
        target_local_path: The requested save path (relative or absolute string).
        allowed_base_dir: The absolute base directory string downloads are confined to.
        force: If True, allows overwriting existing files.

    Returns:
        Tuple (absolute_target_path | None, status | None, error_message | None):
         - absolute_target_path: Validated absolute Path object, or None on failure.
         - status: 'skipped' or 'failed' if applicable, else None.
         - error_message: Description if status is 'failed'.
    """
    absolute_target: Optional[Path] = None
    try:
        # Ensure allowed_base_dir is an absolute Path and exists
        base_path = Path(allowed_base_dir).resolve(strict=True)

        # --- Path Sanitization & Resolution ---
        # Decode URL-encoded characters first
        decoded_path_str = target_local_path
        try:
            # Limit unquoting depth as a safety measure against recursion bombs
            depth = 0
            while "%" in decoded_path_str and depth < 5:  # Arbitrary limit
                decoded_path_str = urllib.parse.unquote(decoded_path_str)
                depth += 1
        except Exception as ue:
            logger.warning(
                f"URL unquoting failed for '{target_local_path}': {ue}. Using original."
            )
            # Fallback to original if unquoting fails badly
            decoded_path_str = target_local_path

        # Create Path object relative to base_dir and resolve it.
        # Path() handles joining intelligently based on OS.
        # Resolving makes it absolute and cleans '..' etc.
        # `strict=False` because the file/dir itself might not exist yet.
        absolute_target = (base_path / decoded_path_str).resolve(strict=False)

        # --- Security Check: Path Traversal ---
        # Check if the resolved path is still within the allowed base directory
        # Ensure both paths are resolved before comparison
        # Using startswith on string representation is a common robust way
        if not (
            absolute_target == base_path
            or str(absolute_target).startswith(str(base_path) + os.sep)
        ):
            error_message = f"Security risk: Target path '{target_local_path}' resolves to '{absolute_target}', which is outside allowed base '{base_path}'"
            logger.error(error_message)
            return None, "failed", error_message
        logger.debug(f"Validated target path: {absolute_target}")

        # --- Ensure Target Directory Exists ---
        # Get the parent directory of the intended file path
        target_dir_path = absolute_target.parent
        try:
            # Creates parent directories as needed (parents=True)
            # Doesn't raise error if the directory already exists (exist_ok=True)
            target_dir_path.mkdir(parents=True, exist_ok=True)
            logger.debug(f"Ensured directory exists: {target_dir_path}")
        except OSError as mkdir_e:
            error_message = (
                f"Directory creation failed for {target_dir_path}: {mkdir_e}"
            )
            logger.error(error_message)
            return None, "failed", error_message

        # --- Atomic Existence Check ---
        # Check if the final target path already exists
        if absolute_target.exists():
            # Check if it's actually a file, not a directory
            if absolute_target.is_file():
                if not force:
                    logger.info(
                        f"File exists and force=False, skipping: {absolute_target}"
                    )
                    return (
                        absolute_target,
                        "skipped",
                        None,
                    )  # Return path even if skipped
                else:
                    logger.debug(
                        f"File exists but force=True, will overwrite: {absolute_target}"
                    )
            elif absolute_target.is_dir():
                # Cannot overwrite a directory with a file download
                error_message = (
                    f"Target path exists but is a directory: {absolute_target}"
                )
                logger.error(error_message)
                return None, "failed", error_message
            else:
                # Handle other cases like broken symlinks - treat as non-existent for overwrite logic
                logger.warning(
                    f"Target path exists but is not a regular file or directory: {absolute_target}. Proceeding."
                )

        # If all checks pass, return the validated absolute path
        return absolute_target, None, None

    except FileNotFoundError:
        # Raised by base_path.resolve(strict=True) if allowed_base_dir doesn't exist
        error_message = f"Configuration error: Allowed base directory '{allowed_base_dir}' not found."
        logger.critical(error_message)  # Critical because it's a config issue
        return None, "failed", error_message
    except Exception as e:
        error_message = f"Path preparation failed for '{target_local_path}' relative to '{allowed_base_dir}': {e}"
        logger.error(error_message, exc_info=True)
        return None, "failed", error_message


# --- Helper Function for Basic Link Extraction ---
def _extract_links(content_sample: str) -> list[str]:
    """Extracts potential relative/absolute links (href, src) from HTML sample."""
    try:
        # Improved regex: handles whitespace better, captures content more reliably
        # Looks for href= or src=, optional whitespace, quotes (' or "), captures non-quote content
        links_found = re.findall(
            r"""(?:href|src)\s*=\s*['"]([^'"]+)['"]""", content_sample, re.IGNORECASE
        )
        # Filter links: non-empty, strip whitespace, avoid common non-http(s) schemes
        valid_links = {
            link.strip()
            for link in links_found
            if link.strip()
            and not link.startswith(("#", "javascript:", "mailto:", "data:", "tel:"))
        }
        # Further filtering could be added here (e.g., require http/https or relative paths)
        return sorted(list(valid_links))
    except Exception as e:
        logger.warning(
            f"Link extraction failed: {e}", exc_info=False
        )  # Less verbose logging for link extract fail
        return []


# --- Fetcher Implementations ---


async def fetch_single_url_requests(
    url: str,
    target_local_path: str,  # Input path string
    force: bool = False,
    max_size: int | None = None,
    allowed_base_dir: str = ".",  # Input path string
    timeout: int | None = None,
    client: httpx.AsyncClient | None = None,
) -> Dict[str, Any]:
    """
    Downloads a single URL using httpx, saves to target path.

    Returns a dictionary containing:
      'status': 'success', 'skipped', 'failed', or 'failed_request'.
      'content_md5': MD5 hash if successful.
      'detected_links': List of potential links found in the content.
      'error_message': Description if status indicates failure.
      'http_status': Integer HTTP status code received.
      'target_path': The validated absolute Path object where the file should be/is.
    """
    result: Dict[str, Any] = {
        "status": None,
        "content_md5": None,
        "detected_links": [],
        "error_message": None,
        "http_status": None,
        "target_path": None,
    }
    validated_target_path: Optional[Path] = None
    temp_file_path: Optional[Path] = None
    # Track if we created the client to ensure closure
    created_client = False
    active_client = client
    tf_fd = -1  # Initialize file descriptor to invalid value

    try:
        # --- Prepare Path ---
        validated_target_path, prep_status, prep_error = await _prepare_target_path(
            target_local_path, allowed_base_dir, force
        )
        result["target_path"] = validated_target_path  # Store path regardless of status
        if prep_status:
            result["status"] = prep_status
            result["error_message"] = prep_error
            return result
        if not validated_target_path:  # Safety check
            raise RuntimeError(
                "Path preparation failed without explicit status or path."
            )

        # --- Setup Client ---
        client_context = nullcontext()  # Default no-op context manager
        if not active_client:
            request_timeout = timeout if timeout is not None else TIMEOUT_REQUESTS
            logger.debug(
                f"Creating temporary httpx client for {url} (timeout={request_timeout})"
            )
            # Set reasonable limits for the temporary client
            limits = httpx.Limits(max_keepalive_connections=5, max_connections=10)
            active_client = httpx.AsyncClient(
                follow_redirects=True, timeout=request_timeout, limits=limits
            )
            client_context = (
                active_client  # Use client as its own async context manager
            )
            created_client = True
        else:
            logger.debug(f"Using provided httpx client for {url}")

        # --- Perform Request ---
        async with client_context:
            response: Optional[httpx.Response] = None
            try:
                logger.debug(f"Making GET request to: {url}")
                # Use timeout from client if not overridden
                # Check if active_client has timeout configured, handle potential AttributeError
                client_timeout = getattr(active_client, "timeout", None)
                # Provide a default value if client_timeout or client_timeout.read is None
                default_read_timeout = TIMEOUT_REQUESTS
                read_timeout = (
                    client_timeout.read
                    if client_timeout and client_timeout.read is not None
                    else default_read_timeout
                )
                req_timeout_obj = timeout if timeout is not None else read_timeout

                response = await active_client.get(url, timeout=req_timeout_obj)
                result["http_status"] = response.status_code
                logger.debug(f"Received status {response.status_code} for: {url}")
                response.raise_for_status()  # Check for 4xx/5xx

                # Header Size Check
                content_length_header = response.headers.get("Content-Length")
                if max_size is not None and content_length_header:
                    try:
                        if int(content_length_header) > max_size:
                            raise ValueError(
                                f"Content-Length ({content_length_header}) exceeds max_size ({max_size})"
                            )
                    except (ValueError, TypeError) as header_e:
                        logger.warning(
                            f"Invalid Content-Length header '{content_length_header}': {header_e}. Proceeding with streaming check."
                        )

                # --- File Writing (Atomic) ---
                # Create temp file in the same directory as the target
                tf_fd, temp_file_name = tempfile.mkstemp(
                    suffix=".tmp",
                    prefix=f"mcpdl_{validated_target_path.stem}_",
                    dir=validated_target_path.parent,
                )
                temp_file_path = Path(temp_file_name)
                logger.debug(f"Created temp file: {temp_file_path} with fd: {tf_fd}")

                total_bytes_read = 0
                md5_hash = hashlib.md5()
                file_op_error = None
                try:
                    async with aiofiles.open(temp_file_path, mode="wb") as afp:
                        async for chunk in response.aiter_bytes(chunk_size=8192):
                            if not chunk:
                                continue  # Skip empty chunks
                            total_bytes_read += len(chunk)
                            # Streaming Size Check
                            if max_size is not None and total_bytes_read > max_size:
                                # Need to break loop cleanly before raising
                                file_op_error = ValueError(
                                    f"File exceeds max_size during download ({total_bytes_read} > {max_size})"
                                )
                                break  # Stop writing
                            await afp.write(chunk)
                            md5_hash.update(chunk)
                        # Ensure data is flushed from aiofiles buffer
                        await afp.flush()

                    # --- FIX: Run os.fsync in executor AFTER closing aiofiles handle ---
                    if (
                        file_op_error is None
                    ):  # Only fsync if no error occurred during write/size check
                        loop = asyncio.get_running_loop()
                        logger.debug(
                            f"Attempting fsync for temp file descriptor {tf_fd}"
                        )
                        await loop.run_in_executor(
                            None, os.fsync, tf_fd
                        )  # Use default executor
                        logger.debug(
                            f"fsync completed for temp file descriptor {tf_fd}"
                        )
                    # --- End Fix ---

                except ValueError as ve:  # Catch the size limit error specifically
                    file_op_error = ve  # Store error to raise after closing fd
                except Exception as e:
                    file_op_error = e  # Store other file operation errors
                finally:
                    # Ensure the low-level file descriptor is closed
                    if tf_fd != -1:
                        try:
                            os.close(tf_fd)
                            logger.debug(f"Closed temp file descriptor {tf_fd}")
                            tf_fd = -1  # Mark as closed
                        except OSError as close_e:
                            logger.warning(
                                f"Error closing temp file descriptor {tf_fd}: {close_e}"
                            )

                # Raise any error caught during file writing/size check/fsync
                if file_op_error:
                    raise file_op_error

                logger.debug(
                    f"Finished writing {total_bytes_read} bytes to temp: {temp_file_path}"
                )

                # Atomic Rename (os.replace is generally atomic)
                logger.debug(
                    f"Attempting rename {temp_file_path} to {validated_target_path}"
                )
                # Check TOCTOU again right before replace
                if validated_target_path.exists():
                    if not force:
                        logger.warning(
                            f"Target file appeared during download (TOCTOU): {validated_target_path}. Skipping."
                        )
                        result["status"] = "skipped"
                    else:
                        logger.debug(
                            f"Target file appeared during download (TOCTOU) but force=True: {validated_target_path}. Overwriting."
                        )
                        os.replace(temp_file_path, validated_target_path)
                        if not validated_target_path.is_file():
                            raise IOError(
                                f"File {validated_target_path} not found after rename."
                            )
                        logger.info(
                            f"Successfully downloaded (overwrote) {url} to {validated_target_path}"
                        )
                        result["content_md5"] = md5_hash.hexdigest()
                        result["status"] = "success"
                        temp_file_path = None  # Prevent cleanup as rename succeeded
                else:
                    # Target does not exist, perform rename
                    os.replace(temp_file_path, validated_target_path)
                    if not validated_target_path.is_file():
                        raise IOError(
                            f"File {validated_target_path} not found after rename."
                        )
                    logger.info(
                        f"Successfully downloaded {url} to {validated_target_path}"
                    )
                    result["content_md5"] = md5_hash.hexdigest()
                    result["status"] = "success"
                    temp_file_path = None  # Prevent cleanup

                # --- Link Detection (on success) ---
                if result["status"] == "success":
                    try:
                        # Read beginning of file for links using aiofiles for async read
                        read_limit = 1 * 1024 * 1024  # 1MB sample
                        async with aiofiles.open(
                            validated_target_path,
                            mode="r",
                            encoding="utf-8",
                            errors="ignore",
                        ) as f:
                            content_sample = await f.read(read_limit)
                        result["detected_links"] = _extract_links(content_sample)
                        logger.debug(
                            f"Detected {len(result['detected_links'])} links in {validated_target_path.name}"
                        )
                    except Exception as link_e:
                        logger.warning(
                            f"Link detection failed for {validated_target_path}: {link_e}"
                        )

            # --- Specific Error Handling ---
            except (
                ValueError
            ) as e:  # Catch size limit errors (from header or streaming)
                result["status"] = "failed_request"
                result["error_message"] = str(e)
                logger.warning(f"{result['error_message']} for {url}")
            except httpx.TimeoutException as e:
                result["status"] = "failed_request"
                result["error_message"] = f"Timeout: {e}"
                logger.warning(f"Timeout fetching {url}: {e}")
            except httpx.HTTPStatusError as e:
                result["status"] = "failed_request"
                result["http_status"] = e.response.status_code
                result["error_message"] = (
                    f"HTTP Error {e.response.status_code}: {e.request.url}"
                )
                logger.warning(f"HTTP error for {url}: {e.response.status_code}")
            except httpx.RequestError as e:  # Includes TransportError etc.
                result["status"] = "failed_request"
                result["error_message"] = f"{type(e).__name__}: {e}"
                logger.warning(f"{type(e).__name__} for {url}: {e}")
            except (
                IOError
            ) as e:  # Catch file operation errors (size, rename fail, read fail)
                result["status"] = "failed"
                result["error_message"] = f"File operation failed: {e}"
                logger.error(
                    f"File operation failed for {url} -> {validated_target_path}: {e}",
                    exc_info=True,
                )
            except (
                Exception
            ) as e:  # Catch-all for unexpected errors during request/write
                result["status"] = "failed"
                result["error_message"] = (
                    f"Unexpected Download Error: {type(e).__name__}: {e}"
                )
                logger.error(
                    f"Unexpected error during download for {url}: {e}", exc_info=True
                )

    except Exception as outer_e:
        # Catch errors during client setup or path prep
        result["status"] = "failed"
        result["error_message"] = f"Setup error: {type(outer_e).__name__}: {outer_e}"
        logger.error(f"Outer setup error for {url}: {outer_e}", exc_info=True)

    finally:
        # Ensure fd is closed if loop exited early before finally block in write section
        if tf_fd != -1:
            try:
                os.close(tf_fd)
                logger.debug(f"Closed temp file descriptor {tf_fd} in outer finally")
            except OSError as close_e:
                logger.warning(
                    f"Error closing temp fd {tf_fd} in outer finally: {close_e}"
                )
        # Cleanup temp file if it exists (i.e., if rename didn't happen or failed)
        if temp_file_path and temp_file_path.exists():
            logger.debug(f"Cleaning up leftover temp file: {temp_file_path}")
            try:
                temp_file_path.unlink()
            except OSError as remove_e:
                logger.warning(
                    f"Failed to remove temp file {temp_file_path}: {remove_e}"
                )
        # Close the temporary client if we created one
        if created_client and isinstance(active_client, httpx.AsyncClient):
            try:
                await active_client.aclose()
                logger.debug(f"Closed temporary client for {url}")
            except Exception as close_e:
                logger.warning(f"Error closing temporary client: {close_e}")

    # Final status check and cleanup
    if result["status"] is None:
        result["status"] = "failed"
        result["error_message"] = (
            result["error_message"] or "Unknown error: fetch status remained None."
        )
        logger.error(f"{result['error_message']} for {url}")
    if result["status"] == "success":
        result["error_message"] = None  # Ensure no error message on success

    return result


async def fetch_single_url_playwright(
    url: str,
    target_local_path: str,  # Input path string
    force: bool = False,
    allowed_base_dir: str = ".",  # Input path string
    timeout: int | None = None,
    # Note: max_size is not easily implementable with Playwright page.content()
) -> Dict[str, Any]:
    """
    Downloads a single URL using Playwright, saves rendered HTML.
    Limits concurrent Playwright instances using a semaphore.

    Returns a dictionary containing:
      'status': 'success', 'skipped', 'failed', or 'failed_request'.
      'content_md5': MD5 hash if successful.
      'detected_links': List of potential links found in the rendered content.
      'error_message': Description if status indicates failure.
      'http_status': Integer HTTP status code of the main navigation response.
      'target_path': The validated absolute Path object where the file should be/is.
    """
    # --- Optional Dependency Import ---
    try:
        from playwright.async_api import (
            async_playwright,
            TimeoutError as PlaywrightTimeoutError,
            Error as PlaywrightError,
        )
    except ImportError:
        logger.error(
            "Playwright dependency not installed. Cannot use Playwright fetcher."
        )
        return {
            "status": "failed",
            "error_message": "Playwright not installed.",
            "content_md5": None,
            "detected_links": [],
            "http_status": None,
            "target_path": None,
        }

    result: Dict[str, Any] = {
        "status": None,
        "content_md5": None,
        "detected_links": [],
        "error_message": None,
        "http_status": None,
        "target_path": None,
    }
    validated_target_path: Optional[Path] = None
    temp_file_path: Optional[Path] = None
    context = None
    browser = None
    tf_fd = -1  # Initialize file descriptor

    # --- Use Semaphore to Limit Concurrency ---
    logger.debug(f"Waiting for Playwright semaphore for {url}...")
    async with playwright_semaphore:
        logger.debug(f"Acquired Playwright semaphore for {url}")
        try:
            # --- Prepare Path ---
            validated_target_path, prep_status, prep_error = await _prepare_target_path(
                target_local_path, allowed_base_dir, force
            )
            result["target_path"] = (
                validated_target_path  # Store path regardless of status
            )
            if prep_status:
                result["status"] = prep_status
                result["error_message"] = prep_error
                return result
            if not validated_target_path:  # Safety check
                raise RuntimeError(
                    "Path preparation failed without explicit status or path."
                )

            # --- Playwright Execution ---
            effective_timeout_ms = (timeout or TIMEOUT_PLAYWRIGHT) * 1000
            content: Optional[str] = None
            page_status: Optional[int] = None

            async with async_playwright() as p:
                try:
                    logger.debug(f"Launching Playwright browser for {url}...")
                    # Consider adding browser launch options if needed (e.g., proxy)
                    browser = await p.chromium.launch(headless=True)
                    logger.debug(f"Creating Playwright context for {url}...")
                    context = await browser.new_context(
                        java_script_enabled=True,
                        bypass_csp=True,  # Can help with some sites loading resources
                        ignore_https_errors=True,  # Useful for sites with cert issues, use with caution
                        viewport={"width": 1280, "height": 800},  # Common desktop size
                        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",  # Realistic UA
                    )
                    # Optional: Block non-essential resources to speed up loading
                    try:
                        # Block images, media, fonts, stylesheets etc. Adjust list as needed.
                        await context.route(
                            "**/*",
                            lambda route: route.abort()
                            if route.request.resource_type
                            in ["image", "media", "font", "stylesheet", "other"]
                            else route.continue_(),
                        )
                        logger.debug(f"Applied resource blocking rule for {url}")
                    except Exception as route_e:
                        logger.warning(
                            f"Failed to set resource blocking for {url}: {route_e}"
                        )

                    page = await context.new_page()
                    # Capture console errors/warnings for debugging site issues
                    page.on(
                        "console",
                        lambda msg: logger.warning(
                            f"[{url}] Browser Console ({msg.type}): {msg.text}"
                        )
                        if msg.type in ["error", "warning"]
                        else None,
                    )
                    # Optional: Capture page errors
                    # page.on("pageerror", lambda exc: logger.error(f"[{url}] Page Error: {exc}"))

                    logger.info(
                        f"Playwright navigating to: {url} (timeout: {effective_timeout_ms}ms)"
                    )
                    # wait_until options: 'load', 'domcontentloaded', 'networkidle', 'commit'
                    # 'domcontentloaded' is often a good balance. 'load' waits for images etc. 'networkidle' can be slow.
                    response = await page.goto(
                        url, wait_until="domcontentloaded", timeout=effective_timeout_ms
                    )

                    if response:
                        page_status = response.status
                        result["http_status"] = page_status
                        logger.debug(
                            f"Playwright navigation response status: {page_status} for {url}"
                        )
                        # Check if status indicates a client or server error (4xx or 5xx)
                        if not response.ok:  # response.ok checks for status 2xx or 3xx
                            raise PlaywrightError(
                                f"Navigation failed with HTTP status {page_status}"
                            )
                    else:
                        # This case might occur if navigation is interrupted very early
                        raise PlaywrightError(
                            "Playwright navigation returned no response object."
                        )

                    # Optional: Wait a bit for JS rendering after DOM load
                    try:
                        # Example: Wait for a common indicator or just a fixed time
                        await page.wait_for_timeout(
                            2000
                        )  # Wait 2 seconds for scripts to potentially run
                        logger.debug(f"Post-navigation wait completed for {url}")
                    except PlaywrightTimeoutError:
                        logger.warning(
                            f"Post-navigation wait timed out for {url}, getting content anyway."
                        )

                    content = await page.content()  # Get final rendered HTML source
                    logger.debug(
                        f"Playwright retrieved content (length {len(content or '')}) for: {url}"
                    )

                except PlaywrightTimeoutError as e:
                    result["status"] = "failed_request"
                    result["error_message"] = f"Playwright Timeout: {e}"
                    logger.warning(f"Playwright timeout for {url}: {e}")
                except PlaywrightError as e:  # Catch Playwright-specific errors
                    result["status"] = "failed_request"
                    result["error_message"] = f"Playwright Error: {e}"
                    # Include page status if available and seems relevant
                    if page_status and not (200 <= page_status < 400):
                        result["error_message"] = (
                            f"HTTP {page_status} | {result['error_message']}"
                        )
                    logger.warning(
                        f"Playwright error for {url} (HTTP: {page_status}): {e}"
                    )
                except Exception as e:  # Catch other unexpected errors
                    result["status"] = "failed_request"
                    result["error_message"] = (
                        f"Unexpected Playwright Error: {type(e).__name__}: {e}"
                    )
                    logger.error(
                        f"Unexpected error during Playwright operation for {url}: {e}",
                        exc_info=True,
                    )
                finally:
                    # Ensure resources are closed robustly even if errors occurred
                    if context:
                        try:
                            await context.close()
                            logger.debug(f"Closed Playwright context for {url}")
                        except Exception as ce:
                            logger.warning(f"Error closing Playwright context: {ce}")
                    if browser:
                        try:
                            await browser.close()
                            logger.debug(f"Closed Playwright browser for {url}")
                        except Exception as be:
                            logger.warning(f"Error closing Playwright browser: {be}")

            # --- File Writing (Atomic, if content retrieved successfully) ---
            if (
                content is not None and result["status"] is None
            ):  # Only proceed if PW succeeded
                try:
                    # Create temp file and write using aiofiles
                    tf_fd, temp_file_name = tempfile.mkstemp(
                        suffix=".tmp",
                        prefix=f"mcpdl_pw_{validated_target_path.stem}_",
                        dir=validated_target_path.parent,
                    )
                    temp_file_path = Path(temp_file_name)
                    logger.debug(
                        f"Created Playwright temp file: {temp_file_path} with fd: {tf_fd}"
                    )

                    content_bytes = content.encode("utf-8")
                    md5_hash = hashlib.md5(content_bytes)

                    file_op_error = None
                    try:
                        async with aiofiles.open(temp_file_path, mode="wb") as afp:
                            await afp.write(content_bytes)
                            # --- REMOVED: await afp.fsync() ---

                        # --- FIX: Run os.fsync in executor AFTER closing aiofiles handle ---
                        loop = asyncio.get_running_loop()
                        logger.debug(
                            f"Attempting fsync for temp file descriptor {tf_fd}"
                        )
                        await loop.run_in_executor(
                            None, os.fsync, tf_fd
                        )  # Use default executor
                        logger.debug(
                            f"fsync completed for temp file descriptor {tf_fd}"
                        )
                        # --- End Fix ---

                    except Exception as e:
                        file_op_error = e
                    finally:
                        # Ensure the low-level file descriptor is closed
                        if tf_fd != -1:
                            try:
                                os.close(tf_fd)
                                logger.debug(f"Closed temp file descriptor {tf_fd}")
                                tf_fd = -1  # Mark as closed
                            except OSError as close_e:
                                logger.warning(
                                    f"Error closing temp file descriptor {tf_fd}: {close_e}"
                                )

                    if file_op_error:
                        raise file_op_error  # Raise error caught during write/fsync

                    logger.debug(
                        f"Finished writing Playwright content ({len(content_bytes)} bytes) to temp: {temp_file_path}"
                    )

                    # Atomic Rename
                    if validated_target_path.exists():
                        if not force:
                            logger.warning(
                                f"Target file appeared (TOCTOU): {validated_target_path}. Skipping."
                            )
                            result["status"] = "skipped"
                        else:
                            logger.debug(
                                f"Target file appeared (TOCTOU) but force=True: {validated_target_path}. Overwriting."
                            )
                            os.replace(temp_file_path, validated_target_path)
                            if not validated_target_path.is_file():
                                raise IOError(
                                    f"File not found after rename: {validated_target_path}"
                                )
                            logger.info(
                                f"Successfully saved (overwrote) Playwright content for {url} to {validated_target_path}"
                            )
                            result["content_md5"] = md5_hash.hexdigest()
                            result["status"] = "success"
                            temp_file_path = None  # Prevent cleanup
                    else:
                        os.replace(temp_file_path, validated_target_path)
                        if not validated_target_path.is_file():
                            raise IOError(
                                f"File not found after rename: {validated_target_path}"
                            )
                        logger.info(
                            f"Successfully saved Playwright content for {url} to {validated_target_path}"
                        )
                        result["content_md5"] = md5_hash.hexdigest()
                        result["status"] = "success"
                        temp_file_path = None  # Prevent cleanup

                    # Link Detection (on success)
                    if result["status"] == "success":
                        try:
                            content_sample = content[
                                : 1 * 1024 * 1024
                            ]  # Use in-memory content
                            result["detected_links"] = _extract_links(content_sample)
                            logger.debug(
                                f"Detected {len(result['detected_links'])} links (Playwright) in {validated_target_path.name}"
                            )
                        except Exception as link_e:
                            logger.warning(
                                f"Link detection failed (Playwright) for {validated_target_path}: {link_e}"
                            )

                except IOError as e:
                    result["status"] = "failed"
                    result["error_message"] = f"File operation failed: {e}"
                    logger.error(
                        f"File operation failed (Playwright) for {url}: {e}",
                        exc_info=True,
                    )
                except Exception as file_e:
                    result["status"] = "failed"
                    result["error_message"] = (
                        f"File saving error (Playwright): {type(file_e).__name__}: {file_e}"
                    )
                    logger.error(
                        f"Unexpected file saving error (Playwright) for {url}: {file_e}",
                        exc_info=True,
                    )
                finally:
                    # Ensure fd is closed if loop exited early before finally block in write section
                    if tf_fd != -1:
                        try:
                            os.close(tf_fd)
                            logger.debug(
                                f"Closed temp file descriptor {tf_fd} in outer finally"
                            )
                        except OSError as close_e:
                            logger.warning(
                                f"Error closing temp fd {tf_fd} in outer finally: {close_e}"
                            )
                    # Cleanup temp file on failure/skip during rename
                    if temp_file_path and temp_file_path.exists():
                        logger.debug(
                            f"Cleaning up leftover Playwright temp file: {temp_file_path}"
                        )
                        try:
                            temp_file_path.unlink()
                        except OSError as rem_e:
                            logger.warning(
                                f"Failed to remove temp file {temp_file_path}: {rem_e}"
                            )

            # If status is still None after potential PW errors or if content was None
            elif result["status"] is None:
                result["status"] = (
                    "failed_request"  # Default to request failure if no success/specific error
                )
                result["error_message"] = (
                    result["error_message"]
                    or "Playwright fetch did not complete successfully."
                )
                logger.warning(f"{result['error_message']} for {url}")

        except Exception as outer_e:
            # Catch errors during path prep, semaphore, async_playwright startup etc.
            result["status"] = "failed"
            result["error_message"] = (
                f"Outer Playwright error: {type(outer_e).__name__}: {outer_e}"
            )
            logger.error(
                f"Outer error during Playwright processing for {url}: {outer_e}",
                exc_info=True,
            )

        finally:
            logger.debug(f"Released Playwright semaphore for {url}")

    # Final status check and cleanup
    if result["status"] is None:
        result["status"] = "failed"
        result["error_message"] = result["error_message"] or "Unknown Playwright error."
        logger.error(f"{result['error_message']} for {url}")
    if result["status"] == "success":
        result["error_message"] = None  # Clear error on success

    # Remove analysis keys that no longer belong here (if they were ever added)
    result.pop("code_snippets", None)
    result.pop("content_blocks", None)

    return result


# --- Standalone Usage Example ---
async def usage_example():
    """Demonstrates programmatic usage of both fetchers with test tally."""
    # --- Test Tracking ---
    test_results: Dict[str, str] = {}
    all_passed = True

    # Ensure the test directory exists
    test_base_dir_obj = Path("./fetchers_example_output").resolve()
    # --- Setup: Create/Clean Directory ---
    test_name_setup = "Create/Clean Test Directory"
    try:
        if test_base_dir_obj.exists():
            logger.info(f"Removing previous test directory: {test_base_dir_obj}")
            shutil.rmtree(test_base_dir_obj)
        test_base_dir_obj.mkdir(parents=True, exist_ok=True)
        test_base_dir = str(test_base_dir_obj)  # Keep string for function args
        test_results[test_name_setup] = "PASS"
    except Exception as e:
        print(f"FAIL: Error setting up test directory {test_base_dir_obj}: {e}")
        test_results[test_name_setup] = f"FAIL: {e}"
        all_passed = False
        # Cannot proceed if setup fails
        print("\n--- Test Summary ---")
        for name, result in test_results.items():
            print(f"- {name}: {result}")
        print("\n✗ Setup tests failed. Aborting.")
        sys.exit(1)

    # Configure logging for the example - Use basicConfig if no handlers are set
    if not logging.getLogger().handlers:
        logging.basicConfig(
            level=logging.INFO, format="[%(levelname)-8s] %(name)s: %(message)s"
        )
    else:
        # If handlers exist, just ensure the level is appropriate for the example
        logging.getLogger().setLevel(logging.INFO)

    logger.info(f"Fetcher usage examples outputting to: {test_base_dir}")

    # --- Test 1: Requests Fetch ---
    test_name_req = "Requests Fetch (httpbin.org/html)"
    print(f"\n--- Testing {test_name_req} ---")
    req_target_path = test_base_dir_obj / "requests_example.html"
    try:
        req_result = await fetch_single_url_requests(
            url="https://httpbin.org/html",
            target_local_path=str(req_target_path),
            force=True,
            allowed_base_dir=test_base_dir,
        )
        req_result_summary = {
            k: v for k, v in req_result.items() if k != "detected_links"
        }
        req_result_summary["num_links"] = len(req_result.get("detected_links", []))
        logger.info(f"Requests fetch result: {req_result_summary}")

        # Assertions
        assert req_result.get("status") == "success", (
            f"Expected status 'success', got '{req_result.get('status')}'"
        )
        assert req_target_path.is_file(), (
            f"Output file {req_target_path} was not created."
        )
        assert req_result.get("content_md5"), "Content MD5 hash was not generated."
        # Check for a specific known MD5 for httpbin.org/html if stable
        assert req_result.get("content_md5") == "3405f5d4c9ada070d09b84cfd77ba22c", (
            "MD5 hash mismatch for httpbin.org/html"
        )
        test_results[test_name_req] = "PASS"
    except Exception as e:
        print(f"FAIL: {test_name_req} failed: {e}")
        logger.error(f"{test_name_req} Exception", exc_info=False)
        test_results[test_name_req] = f"FAIL: {e}"
        all_passed = False

    # --- Test 2: Playwright Fetch ---
    test_name_pw = "Playwright Fetch (httpbin.org/html)"
    print(f"\n--- Testing {test_name_pw} ---")
    # Check if Playwright is likely installed
    try:
        from playwright.async_api import Error as PlaywrightError  # Check import works

        pw_available = True
    except ImportError:
        pw_available = False
        logger.warning("Playwright not installed, skipping Playwright fetcher example.")
        test_results[test_name_pw] = "SKIPPED (Playwright not installed)"

    if pw_available:
        pw_target_path = test_base_dir_obj / "playwright_example.html"
        try:
            pw_result = await fetch_single_url_playwright(
                url="https://httpbin.org/html",
                target_local_path=str(pw_target_path),
                force=True,
                allowed_base_dir=test_base_dir,
                timeout=45,  # Slightly longer timeout for PW
            )
            pw_result_summary = {
                k: v for k, v in pw_result.items() if k != "detected_links"
            }
            pw_result_summary["num_links"] = len(pw_result.get("detected_links", []))
            logger.info(f"Playwright fetch result: {pw_result_summary}")

            # Assertions
            assert pw_result.get("status") == "success", (
                f"Expected status 'success', got '{pw_result.get('status')}'"
            )
            assert pw_target_path.is_file(), (
                f"Output file {pw_target_path} was not created."
            )
            assert pw_result.get("content_md5"), "Content MD5 hash was not generated."
            # Note: Playwright MD5 might differ from raw requests due to minor rendering differences
            # print(f"DEBUG: Playwright MD5: {pw_result.get('content_md5')}")
            test_results[test_name_pw] = "PASS"
        except Exception as e:
            print(f"FAIL: {test_name_pw} failed: {e}")
            logger.error(f"{test_name_pw} Exception", exc_info=False)
            test_results[test_name_pw] = f"FAIL: {e}"
            all_passed = False

    # --- Final Summary ---
    print("\n--- Fetcher Test Summary ---")
    all_test_names = [test_name_setup, test_name_req, test_name_pw]
    summary_all_passed = True
    for name in all_test_names:
        result = test_results.get(name, "UNKNOWN (Test did not run)")
        print(f"- {name}: {result}")
        if "FAIL" in result or "ERROR" in result or "UNKNOWN" in result:
            summary_all_passed = False

    print("\n--------------------")
    if summary_all_passed:
        print("✓ All Fetcher tests passed!") # Already has print
    else:
        print("✗ Some Fetcher tests FAILED or were SKIPPED.")
        # sys.exit(1) # Optional: Exit with error status
    print("--------------------")

    # Clean up test directory? Usually good practice after tests run.
    logger.info(f"Attempting to clean up test directory: {test_base_dir_obj}")
    try:
        shutil.rmtree(test_base_dir_obj, ignore_errors=True)
        logger.info("Test directory cleaned up.")
    except Exception as clean_e:
        logger.warning(
            f"Failed to clean up test directory {test_base_dir_obj}: {clean_e}"
        )

    logger.info("\nFetcher examples finished.")


if __name__ == "__main__":
    # --- Cleaned up Standalone Execution ---
    # No need for sys.path manipulation if installed with -e
    # Just call the example function directly.
    # The try/except around the top-level import handles the case
    # where utils might not be found during development if not installed.
    print("Running fetchers.py example...")
    # Setup basic logging for the example run if not configured externally
    if not logging.getLogger().handlers:
        logging.basicConfig(
            level=logging.INFO, format="[%(levelname)-8s] %(name)s: %(message)s"
        )
    asyncio.run(usage_example())
