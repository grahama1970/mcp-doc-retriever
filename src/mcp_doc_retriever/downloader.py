import os
import asyncio
import hashlib
import re
import time
import httpx
import aiofiles
import fcntl

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


async def fetch_single_url_requests(url, target_local_path, force=False, max_size=None, allowed_base_dir="."):
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

        # Check if file exists and handle force/no-clobber logic
        if os.path.exists(norm_target) and not force:
            result['status'] = 'skipped'
            return result

        # Create target directory if needed
        try:
            os.makedirs(os.path.dirname(norm_target), exist_ok=True)
        except Exception as e:
            result['status'] = 'failed'
            result['error_message'] = f"Directory creation failed: {str(e)}"
            return result

        async with httpx.AsyncClient(follow_redirects=True, timeout=30) as client:
            try:
                response = await client.get(url, follow_redirects=True)
                response.raise_for_status()

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
                result['status'] = 'failed'
                result['error_message'] = f"Request error: {str(e)}"
                return result
            except httpx.HTTPStatusError as e:
                result['status'] = 'failed'
                result['error_message'] = f"HTTP error: {str(e)}"
                return result
            except Exception as e:
                # Cleanup temp file on any error
                if 'temp_path' in locals() and os.path.exists(temp_path):
                    os.remove(temp_path)
                result['status'] = 'failed'
                result['error_message'] = f"Download error: {str(e)}"
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