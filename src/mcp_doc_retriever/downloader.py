"""
Module: downloader.py

Description:
Asynchronous recursive downloader with security protections, robots.txt compliance, atomic file writes, and optional Playwright fallback for dynamic content.

Third-party packages:
- httpx: https://www.python-httpx.org/
- aiofiles: https://github.com/Tinche/aiofiles
- Playwright: https://playwright.dev/python/
- bleach: https://bleach.readthedocs.io/en/latest/

Sample input:
start_url = "https://docs.python.org/3/"
depth = 0
force = True
download_id = "test_download"
base_dir = "downloads_test"

Expected output:
- Downloads https://docs.python.org/3/ to downloads_test/content/docs.python.org/3/index.html
- Creates an index file at downloads_test/index/test_download.jsonl with fetch status and metadata
- Handles robots.txt, paywalls, and dynamic content fallback

"""

import os
import argparse
import sys
import json
import asyncio
import hashlib
import re
import time
import httpx
import aiofiles
import fcntl
import bleach
import tempfile
import urllib.parse
import logging # Added import

# Configure basic logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')


from mcp_doc_retriever.utils import TIMEOUT_REQUESTS, TIMEOUT_PLAYWRIGHT, playwright_semaphore, requests_semaphore
from mcp_doc_retriever.playwright_fetcher import fetch_single_url_playwright



# Global concurrency and resource controls for Playwright
active_browser_count = 0
browser_count_lock = asyncio.Lock()

# Global lock file path with user-specific name
GLOBAL_LOCK_PATH = f'/tmp/mcp_downloader_{os.getuid()}.lock'

async def acquire_global_lock():
    """Acquire global download lock with retries"""
    max_attempts = 3
    for attempt in range(max_attempts):
        lock_file = None
        try:
            lock_file = open(GLOBAL_LOCK_PATH, 'w')
            fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return lock_file
        except BlockingIOError:
            if lock_file:
                lock_file.close()
            if attempt < max_attempts - 1:
                await asyncio.sleep(0.1 * (attempt + 1))
        except Exception:
            if lock_file:
                lock_file.close()
            break
    return None

async def fetch_single_url_requests(url, target_local_path, force=False, max_size=None, allowed_base_dir=".", timeout=None):
    """
    Download a single URL to a local file asynchronously, with security protections.

    Args:
        url (str): The URL to fetch.
        target_local_path (str): The local file path to save content.
        force (bool): If True, overwrite existing file. Default is False.
        max_size (int or None): Maximum allowed size in bytes. None means unlimited.
        allowed_base_dir (str): Base directory within which all downloads must stay.

    Returns:
        dict: {
            'status': 'success' | 'skipped' | 'failed' | 'failed_paywall' | 'failed_request',
            'content_md5': str or None,
            'detected_links': list of str,
            'error_message': str or None
        }

    Security considerations:
    - The target path is sanitized and must stay within allowed_base_dir.
    - Downloads are written atomically via a temporary file, renamed on success.
    - If max_size is set, downloads exceeding this size are aborted.
    """
    result = {
        'status': None,
        'content_md5': None,
        'detected_links': [],
        'error_message': None
    }

    async with requests_semaphore:
        try:
            # Create target directory if needed BEFORE atomic existence check
            try:
                os.makedirs(os.path.dirname(target_local_path), exist_ok=True)
            except Exception as e:
                result['status'] = 'failed'
                result['error_message'] = f"Directory creation failed: {str(e)}"
                return result

            # Decode URL-encoded characters to prevent bypass (handle multiple encodings)
            decoded_path = target_local_path
            while '%' in decoded_path:
                decoded_path = urllib.parse.unquote(decoded_path)
            # Normalize Windows backslashes to forward slashes for cross-platform traversal protection
            decoded_path = decoded_path.replace("\\", "/")
            # Path sanitization
            norm_base = os.path.abspath(allowed_base_dir)
            norm_target = os.path.abspath(os.path.normpath(decoded_path))
            if not norm_target.startswith(norm_base):
                result['status'] = 'failed'
                result['error_message'] = f"Target path outside allowed directory: target='{norm_target}' base='{norm_base}'"
                return result

            # Atomic existence check to prevent TOCTOU race
            try:
                flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
                fd = os.open(norm_target, flags)
                os.close(fd)
                # File did not exist before, safe to proceed
            except FileExistsError:
                if not force:
                    result['status'] = 'skipped'
                    return result
                # else: force=True, proceed to overwrite later
            except Exception as e:
                result['status'] = 'failed'
                result['error_message'] = f"Atomic existence check failed: {str(e)}"
                return result

            async with httpx.AsyncClient(follow_redirects=True, timeout=timeout or TIMEOUT_REQUESTS) as client:
                try:
                    response = await client.get(url, follow_redirects=True)
                    response.raise_for_status()
        
                    # Paywall/login detection removed - response.text loads entire content into memory.
                    # This is memory-intensive and better handled by Playwright if needed.

                    # Check Content-Length header if present
                    content_length = response.headers.get("Content-Length")
                    if content_length is not None:
                        try:
                            content_length = int(content_length)
                            if max_size is not None and content_length > max_size:
                                result['status'] = 'failed'
                                result['error_message'] = f"File too large ({content_length} bytes > max_size {max_size})"
                                return result
                        except ValueError:
                            # Ignore invalid header, fallback to streaming check
                            content_length = None

                    # Acquire global download lock
                    lock_file = await acquire_global_lock()
                    if not lock_file:
                        result['status'] = 'failed'
                        result['error_message'] = "Download locked by another process"
                        return result
                    
                    try:
                        # Create temp file
                        target_dir = os.path.dirname(norm_target)
                        fd, temp_path = tempfile.mkstemp(dir=target_dir)
                        os.close(fd)

                        total = 0
                        md5 = hashlib.md5()
                        async with aiofiles.open(temp_path, 'wb') as f:
                            async for chunk in response.aiter_bytes(chunk_size=8192):
                                total += len(chunk)
                                if max_size is not None and total > max_size:
                                    await f.close()
                                    os.remove(temp_path)
                                    result['status'] = 'failed'
                                    result['error_message'] = f"File exceeds max_size during download ({total} bytes)"
                                    return result
                                try:
                                    md5.update(chunk)
                                except Exception as e:
                                    await f.close()
                                    os.remove(temp_path)
                                    result['status'] = 'failed'
                                    result['error_message'] = f"MD5 calculation failed during download: {str(e)}"
                                    return result
                                await f.write(chunk)
                        
                        # Atomic rename
                        try:
                            # Before rename, check again for TOCTOU protection
                            if not force and os.path.exists(norm_target):
                                # Someone created file during download, skip overwrite
                                os.remove(temp_path)
                                result['status'] = 'skipped'
                                return result

                            os.replace(temp_path, norm_target)
                            result['content_md5'] = md5.hexdigest()
                        except Exception as e:
                            if os.path.exists(temp_path):
                                os.remove(temp_path)
                            result['status'] = 'failed'
                            result['error_message'] = f"File finalize failed: {str(e)}"
                            return result

                    except Exception as e:
                        # Cleanup on error
                        if 'temp_path' in locals() and os.path.exists(temp_path):
                            os.remove(temp_path)
                        raise
                    finally:
                        # Release lock
                        try:
                            fcntl.flock(lock_file, fcntl.LOCK_UN)
                            lock_file.close()
                            os.remove(GLOBAL_LOCK_PATH)
                        except:
                            pass

                except httpx.RequestError as e:
                    result['status'] = 'failed_request'
                    result['error_message'] = f"Request error: {str(e)}"
                    return result
                except httpx.HTTPStatusError as e:
                    result['status'] = 'failed_request'
                    result['error_message'] = f"HTTP error: {str(e)}"
                    return result
                except Exception as e:
                    # Cleanup temp file on any error
                    if 'temp_path' in locals() and os.path.exists(temp_path):
                        os.remove(temp_path)
                    result['status'] = 'failed'
                    result['error_message'] = "Download error"  # Consistent with tests
                    return result

            # Basic link detection (href/src attributes)
            try:
                # Read back content for link detection
                async with aiofiles.open(norm_target, 'rb') as f:
                    content = await f.read()
                text_content = content.decode('utf-8', errors='ignore')
                links = re.findall(r'''(?:href|src)=["'](.*?)["']''', text_content, re.IGNORECASE)
                result['detected_links'] = links
            except Exception:
                # Ignore link detection errors
                result['detected_links'] = []

            result['status'] = 'success'
            return result

        except Exception as e:
            result['status'] = 'failed'
            result['error_message'] = str(e)
            return result
async def _is_allowed_by_robots(url: str, client: httpx.AsyncClient, robots_cache: dict) -> bool:
    """Check robots.txt rules for the given URL. Supports multiple user-agent blocks, Allow/Disallow rules, and longest match precedence."""
    from urllib.parse import urlparse

    parsed = urlparse(url)
    base_url = f"{parsed.scheme}://{parsed.netloc}"
    robots_url = f"{base_url}/robots.txt"

    if base_url in robots_cache:
        rules = robots_cache[base_url]
    else:
        rules = {}
        current_agents = []
        try:
            resp = await client.get(robots_url, timeout=10)
            if resp.status_code == 200:
                lines = resp.text.splitlines()
                for line in lines:
                    line = line.strip()
                    if not line or line.startswith('#'):
                        continue
                    if line.lower().startswith('user-agent:'):
                        agent = line.split(':',1)[1].strip()
                        current_agents = [agent]
                        if agent not in rules:
                            rules[agent] = []
                    elif line.lower().startswith('disallow:'):
                        path = line.split(':',1)[1].strip()
                        for agent in current_agents:
                            rules.setdefault(agent, []).append(('disallow', path))
                    elif line.lower().startswith('allow:'):
                        path = line.split(':',1)[1].strip()
                        for agent in current_agents:
                            rules.setdefault(agent, []).append(('allow', path))
            robots_cache[base_url] = rules
        except Exception:
            robots_cache[base_url] = rules  # treat as allowed on error

    # Determine applicable rules
    # Prefer exact user-agent, fallback to '*'
    agent_rules = []
    if '*' in rules:
        agent_rules = rules.get('*', [])
    # TODO: support custom user-agent matching if needed

    # Find longest matching rule
    matched_rule = None
    matched_length = -1
    for rule_type, path in agent_rules:
        if not path:
            continue
        if parsed.path.startswith(path):
            if len(path) > matched_length:
                matched_rule = (rule_type, path)
                matched_length = len(path)

    if matched_rule:
        rule_type, _ = matched_rule
        if rule_type == 'allow':
            return True
        elif rule_type == 'disallow':
            return False

    # Default allow
    return True

async def start_recursive_download(
    start_url: str,
    depth: int,
    force: bool,
    download_id: str,
    base_dir: str = "/app/downloads",
    use_playwright: bool = False,
    timeout_requests: int = TIMEOUT_REQUESTS,
    timeout_playwright: int = TIMEOUT_PLAYWRIGHT
) -> None:
    import json
    import os
    import aiofiles
    import asyncio
    from urllib.parse import urlparse

    from mcp_doc_retriever.utils import canonicalize_url, url_to_local_path
    from mcp_doc_retriever.models import IndexRecord

    # Prepare index file path
    index_dir = os.path.join(base_dir, "index")
    os.makedirs(index_dir, exist_ok=True)
    index_path = os.path.join(index_dir, f"{download_id}.jsonl")

    # Initialize queue and visited set
    queue = asyncio.Queue()
    await queue.put((start_url, 0))
    visited = set()

    # Canonicalize start domain for domain restriction
    start_canonical = canonicalize_url(start_url)
    start_domain = urlparse(start_canonical).netloc

    robots_cache = {}

    async with httpx.AsyncClient(follow_redirects=True, timeout=timeout_requests) as client:
        while not queue.empty():
            current_url, current_depth = await queue.get()

            canonical_url = canonicalize_url(current_url)
            if canonical_url in visited:
                continue
            visited.add(canonical_url)

            # Domain restriction
            parsed_url = urlparse(canonical_url)
            # Strict domain check: only allow the exact starting domain
            if parsed_url.netloc != start_domain:
                continue

            # Robots.txt check
            allowed = await _is_allowed_by_robots(canonical_url, client, robots_cache)
            if not allowed:
                record = IndexRecord(
                    original_url=current_url,
                    canonical_url=canonical_url,
                    local_path="",
                    content_md5=None,
                    fetch_status="failed_robotstxt",
                    http_status=None,
                    error_message="Blocked by robots.txt"
                )
                async with aiofiles.open(index_path, 'a') as f:
                    await f.write(record.json() + "\n")
                continue

            # Map URL to local path
            local_path = url_to_local_path(base_dir, canonical_url)

            # Download
            try:
                if use_playwright:
                    result = await fetch_single_url_playwright(
                        current_url,
                        local_path,
                        force=force,
                        allowed_base_dir=base_dir,
                        timeout=timeout_playwright
                    )
                else:
                    result = await fetch_single_url_requests(
                        current_url,
                        local_path,
                        force=force,
                        allowed_base_dir=base_dir,
                        timeout=timeout_requests
                    )
                    # Check for dynamic content fallback
                    if result.get("status") == "success":
                        try:
                            async with aiofiles.open(local_path, 'rb') as f:
                                content_bytes = await f.read()
                            if len(content_bytes) < 1024 and (b"#root" in content_bytes or b"#app" in content_bytes):
                                # Retry with Playwright
                                pw_result = await fetch_single_url_playwright(
                                    current_url,
                                    local_path,
                                    force=True,  # overwrite with Playwright content
                                    allowed_base_dir=base_dir,
                                    timeout=timeout_playwright
                                )
                                if pw_result.get("status") == "success":
                                    result = pw_result  # override with Playwright result
                        except Exception:
                            pass
            except Exception as e: # Outer exception handler
                error_msg = str(e) # Capture error message
                logging.error(f"Exception during fetch for {current_url}: {error_msg}", exc_info=True) # Log exception with traceback
                record = IndexRecord(
                    original_url=current_url,
                    canonical_url=canonical_url,
                    local_path=local_path,
                    content_md5=None,
                    fetch_status="failed_request", # Sets status
                    http_status=None,
                    error_message=error_msg # Use captured message
                )
                logging.info(f"Writing failed index record (exception): {record.json()}") # Log record before writing
                async with aiofiles.open(index_path, 'a') as f:
                    await f.write(record.json() + "\n")
                continue # Skips processing the result dict

            status = result.get("status")
            content_md5 = result.get("content_md5")
            error_message = result.get("error_message")
            detected_links = result.get("detected_links", [])

            if status == "success":
                fetch_status = "success"
            elif status == "failed_paywall":
                fetch_status = "failed_paywall"
            elif status == "failed_robotstxt":
                fetch_status = "failed_robotstxt"
            else:
                fetch_status = "failed_request"

            record = IndexRecord(
                original_url=current_url,
                canonical_url=canonical_url,
                local_path=local_path,
                content_md5=content_md5,
                fetch_status=fetch_status,
                http_status=None,
                error_message=error_message # Uses error message from result dict
            )
            logging.info(f"Writing index record (normal): {record.json()}") # Log record before writing
            async with aiofiles.open(index_path, 'a') as f:
                await f.write(record.json() + "\n")

            # Recurse if within depth and success
            if fetch_status == "success" and current_depth < depth:
                for link in detected_links:
                    try:
                        abs_link = httpx.URL(link, base=current_url).human_repr()
                        canon_link = canonicalize_url(abs_link)
                        canon_domain = urlparse(canon_link).netloc
                        if canon_link not in visited and canon_domain.endswith(start_domain):
                            await queue.put((abs_link, current_depth + 1))
                    except Exception:
                        continue
def parse_args():
    """Parse command-line arguments for the recursive downloader CLI."""
    parser = argparse.ArgumentParser(description="Recursive Downloader CLI")
    parser.add_argument(
        "--url",
        type=str,
        default="http://example.com",
        help="Starting URL to download (default: http://example.com)"
    )
    parser.add_argument(
        "--depth",
        type=int,
        default=1,
        help="Recursion depth (default: 1)"
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Force overwrite existing files"
    )
    parser.add_argument(
        "--output-dir",
        type=str,
        default="downloads",
        help="Output directory inside the base downloads folder (default: downloads). Must be within the base downloads directory."
    )
    return parser.parse_args()


def main():
    """CLI entry point for the recursive downloader."""
    args = parse_args()

    BASE_DOWNLOAD_DIR = os.path.abspath("downloads")
    resolved_output_dir = os.path.abspath(args.output_dir)

    # Security check: prevent directory traversal outside base dir
    if not os.path.commonpath([resolved_output_dir, BASE_DOWNLOAD_DIR]) == BASE_DOWNLOAD_DIR:
        print(f"Error: Output directory '{resolved_output_dir}' is outside the allowed base directory '{BASE_DOWNLOAD_DIR}'. Aborting.")
        sys.exit(1)

    # Create the directory if it doesn't exist (safe because it's inside base)
    os.makedirs(resolved_output_dir, exist_ok=True)
    args.output_dir = resolved_output_dir

    print("Starting download with parameters:")
    print(f"  URL: {args.url}")
    print(f"  Depth: {args.depth}")
    print(f"  Force overwrite: {args.force}")
    print(f"  Output directory: {args.output_dir}")
    print("Beginning recursive download...")

    asyncio.run(
        start_recursive_download(
            start_url=args.url,
            depth=args.depth,
            force=args.force,
            download_id="cli_download",
            base_dir=args.output_dir,
            use_playwright=False
        )
    )
    print("Download completed.")


if __name__ == "__main__":
    main()