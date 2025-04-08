import os
import json

# Load configuration
try:
    with open("config.json", "r") as f:
        _config = json.load(f)
except Exception:
    _config = {}

TIMEOUT_REQUESTS = _config.get("timeout_requests", 30)
TIMEOUT_PLAYWRIGHT = _config.get("timeout_playwright", 30)
import asyncio
import hashlib
import re
import time
import httpx
import aiofiles
import fcntl
import bleach

# Global concurrency and resource controls for Playwright
playwright_semaphore = asyncio.Semaphore(5)  # Limit concurrent Playwright sessions
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

async def fetch_single_url_playwright(url, target_local_path, force=False, max_size=None, allowed_base_dir=".", timeout=None):
    """
    Download a single URL using Playwright to a local file asynchronously, with security protections.

    Returns:
        dict: {
            'status': 'success' | 'skipped' | 'failed',
            'content_md5': str or None,
            'detected_links': list of str,
            'error_message': str or None
        }
    """
    import tempfile
    result = {
        'status': None,
        'content_md5': None,
        'detected_links': [],
        'error_message': None
    }

    try:
        # Create target directory if needed BEFORE atomic existence check
        try:
            os.makedirs(os.path.dirname(target_local_path), exist_ok=True)
        except Exception as e:
            result['status'] = 'failed'
            result['error_message'] = f"Directory creation failed: {str(e)}"
            return result

        import urllib.parse
        # Decode URL-encoded characters to prevent bypass (handle multiple encodings)
        decoded_path = target_local_path
        while '%' in decoded_path:
            decoded_path = urllib.parse.unquote(decoded_path)
        decoded_path = decoded_path.replace("\\", "/")
        norm_base = os.path.abspath(allowed_base_dir)
        norm_target = os.path.abspath(os.path.normpath(decoded_path))
        if not norm_target.startswith(norm_base):
            result['status'] = 'failed'
            result['error_message'] = f"Target path outside allowed directory: target='{norm_target}' base='{norm_base}'"
            return result

        # Atomic existence check
        try:
            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
            fd = os.open(norm_target, flags)
            os.close(fd)
        except FileExistsError:
            if not force:
                result['status'] = 'skipped'
                return result
            # else: force=True, proceed to overwrite later
        except Exception as e:
            result['status'] = 'failed'
            result['error_message'] = f"Atomic existence check failed: {str(e)}"
            return result

        # Acquire global download lock
        lock_file = await acquire_global_lock()
        if not lock_file:
            result['status'] = 'failed'
            result['error_message'] = "Download locked by another process"
            return result

        try:
            # Acquire concurrency semaphore to limit resource exhaustion
            await playwright_semaphore.acquire()
            global active_browser_count
            try:
                # Track active browser count for leak/resource monitoring
                async with browser_count_lock:
                    active_browser_count += 1
                    if active_browser_count > 10:
                        print(f"[WARN] High number of active Playwright browsers: {active_browser_count}")

                from playwright.async_api import async_playwright, Error as PlaywrightError

                async with async_playwright() as p:
                    browser = await p.chromium.launch(headless=True)
                    context = await browser.new_context()
                    page = await context.new_page()
                    try:
                        await page.goto(url, wait_until="networkidle", timeout=(timeout or TIMEOUT_PLAYWRIGHT)*1000)
                        original_content = await page.content()
                        # Sanitize HTML content to prevent XSS
                        content = bleach.clean(
                            original_content,
                            tags=bleach.sanitizer.ALLOWED_TAGS,
                            attributes=bleach.sanitizer.ALLOWED_ATTRIBUTES
                        )

                        # Paywall/login detection
                        lowered = original_content.lower()
                        if any(k in lowered for k in ["login", "sign in", "password", "subscribe"]) or \
                            ("input" in lowered and ("type=\"password\"" in lowered or "name=\"password\"" in lowered)):
                            result['status'] = 'failed_paywall'
                            result['error_message'] = "Paywall or login detected"
                            await page.close()
                            await context.close()
                            await browser.close()
                            return result

                    except PlaywrightError as e:
                        await page.close()
                        await context.close()
                        await browser.close()
                        result['status'] = 'failed'
                        result['error_message'] = f"Playwright navigation error: {str(e)}"
                        return result
                    except Exception as e:
                        await page.close()
                        await context.close()
                        await browser.close()
                        result['status'] = 'failed'
                        result['error_message'] = f"Playwright error: {str(e)}"
                        return result

                    await page.close()
                    await context.close()
                    await browser.close()

            finally:
                # Decrement active browser count and release semaphore
                async with browser_count_lock:
                    active_browser_count -= 1
                playwright_semaphore.release()

            # Enforce max_size
            content_bytes = content.encode('utf-8', errors='ignore')
            if max_size is not None and len(content_bytes) > max_size:
                result['status'] = 'failed'
                result['error_message'] = f"File too large ({len(content_bytes)} bytes > max_size {max_size})"
                return result

            # Write atomically
            target_dir = os.path.dirname(norm_target)
            fd, temp_path = tempfile.mkstemp(dir=target_dir)
            os.close(fd)

            md5 = hashlib.md5()
            try:
                async with aiofiles.open(temp_path, 'wb') as f:
                    md5.update(content_bytes)
                    await f.write(content_bytes)

                # Before rename, check again for TOCTOU protection
                if not force and os.path.exists(norm_target):
                    os.remove(temp_path)
                    result['status'] = 'skipped'
                    return result

                os.replace(temp_path, norm_target)
                result['content_md5'] = md5.hexdigest()

            except Exception as e:
                if os.path.exists(temp_path):
                    os.remove(temp_path)
                result['status'] = 'failed'
                result['error_message'] = f"File write failed: {str(e)}"
                return result

            # Link detection
            try:
                text_content = content
                links = re.findall(r'''(?:href|src)=["'](.*?)["']''', text_content, re.IGNORECASE)
                result['detected_links'] = links
            except Exception:
                result['detected_links'] = []

            result['status'] = 'success'
            return result

        finally:
            try:
                fcntl.flock(lock_file, fcntl.LOCK_UN)
                lock_file.close()
                os.remove(GLOBAL_LOCK_PATH)
            except:
                pass

    except Exception as e:
        result['status'] = 'failed'
        result['error_message'] = str(e)
        return result

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
            'status': 'success' | 'skipped' | 'failed',
            'content_md5': str or None,
            'detected_links': list of str,
            'error_message': str or None
        }

    Security considerations:
    - The target path is sanitized and must stay within allowed_base_dir.
    - Downloads are written atomically via a temporary file, renamed on success.
    - If max_size is set, downloads exceeding this size are aborted.
    """
    import tempfile
    result = {
        'status': None,
        'content_md5': None,
        'detected_links': [],
        'error_message': None
    }

    try:
        # Create target directory if needed BEFORE atomic existence check
        try:
            os.makedirs(os.path.dirname(target_local_path), exist_ok=True)
        except Exception as e:
            result['status'] = 'failed'
            result['error_message'] = f"Directory creation failed: {str(e)}"
            return result
        import urllib.parse
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
    
                # Paywall/login detection
                lowered = response.text.lower()
                if any(k in lowered for k in ["login", "sign in", "password", "subscribe", "pwd", "pass_word"]) or \
                    ("input" in lowered and ("type=\"password\"" in lowered or
                    "name=\"password\"" in lowered or
                    "type=\"hidden\" name=\"pwd\"" in lowered or
                    "type=&#34;password&#34;" in lowered)):
                    result['status'] = 'failed_paywall'
                    result['error_message'] = "Paywall or login detected"
                    return result

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
    """
    Basic robots.txt check with caching.
    """
    from urllib.parse import urlparse

    parsed = urlparse(url)
    base_url = f"{parsed.scheme}://{parsed.netloc}"
    robots_url = f"{base_url}/robots.txt"

    if base_url in robots_cache:
        disallow_list = robots_cache[base_url]
    else:
        disallow_list = []
        try:
            resp = await client.get(robots_url, timeout=10)
            if resp.status_code == 200:
                lines = resp.text.splitlines()
                user_agent = None
                for line in lines:
                    line = line.strip()
                    if not line or line.startswith('#'):
                        continue
                    if line.lower().startswith('user-agent:'):
                        user_agent = line.split(':',1)[1].strip()
                    elif line.lower().startswith('disallow:') and (user_agent == '*' or user_agent is None):
                        path = line.split(':',1)[1].strip()
                        if path:
                            disallow_list.append(path)
            # Cache regardless of success to avoid repeated fetches
            robots_cache[base_url] = disallow_list
        except Exception:
            robots_cache[base_url] = disallow_list  # treat as allowed on error

    # Check if URL path is disallowed
    for disallowed in disallow_list:
        if parsed.path.startswith(disallowed):
            return False
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

    from src.mcp_doc_retriever.utils import canonicalize_url, url_to_local_path
    from src.mcp_doc_retriever.models import IndexRecord

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
            if not parsed_url.netloc.endswith(start_domain):
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
            except Exception as e:
                record = IndexRecord(
                    original_url=current_url,
                    canonical_url=canonical_url,
                    local_path=local_path,
                    content_md5=None,
                    fetch_status="failed_request",
                    http_status=None,
                    error_message=str(e)
                )
                async with aiofiles.open(index_path, 'a') as f:
                    await f.write(record.json() + "\n")
                continue

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
                error_message=error_message
            )
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