import os
import hashlib
import re
import httpx
import aiofiles


async def fetch_single_url_requests(url, target_local_path, force=False):
    """
    Download a single URL to a local file asynchronously.

    Args:
        url (str): The URL to fetch.
        target_local_path (str): The local file path to save content.
        force (bool): If True, overwrite existing file. Default is False.

    Returns:
        dict: {
            'status': 'success' | 'skipped' | 'failed',
            'content_md5': str or None,
            'detected_links': list of str,
            'error_message': str or None
        }
    """
    result = {
        'status': None,
        'content_md5': None,
        'detected_links': [],
        'error_message': None
    }

    try:
        # Check if file exists and handle force/no-clobber logic
        if os.path.exists(target_local_path) and not force:
            result['status'] = 'skipped'
            return result

        # Create target directory if needed
        os.makedirs(os.path.dirname(target_local_path), exist_ok=True)

        async with httpx.AsyncClient(follow_redirects=True, timeout=30) as client:
            try:
                response = await client.get(url)
                response.raise_for_status()
                content = response.content
            except httpx.RequestError as e:
                result['status'] = 'failed'
                result['error_message'] = f"Request error: {str(e)}"
                return result
            except httpx.HTTPStatusError as e:
                result['status'] = 'failed'
                result['error_message'] = f"HTTP error: {str(e)}"
                return result

        # Save content asynchronously
        async with aiofiles.open(target_local_path, 'wb') as f:
            await f.write(content)

        # Calculate MD5 hash
        md5_hash = hashlib.md5(content).hexdigest()
        result['content_md5'] = md5_hash

        # Basic link detection (href/src attributes)
        try:
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