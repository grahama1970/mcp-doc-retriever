import hashlib
import re
from src.mcp_doc_retriever.utils import playwright_semaphore, TIMEOUT_PLAYWRIGHT

async def fetch_single_url_playwright(url, target_local_path, force=False, allowed_base_dir=".", timeout=None):
    """
    Minimal Playwright fetcher with concurrency control and basic protections.
    """
    import os
    import urllib.parse
    import aiofiles
    import tempfile # Added import
    from playwright.async_api import async_playwright

    result = {
        'status': None,
        'content_md5': None,
        'detected_links': [],
        'error_message': None
    }

    async with playwright_semaphore:
        try:
            # Path sanitization
            decoded_path = target_local_path
            while '%' in decoded_path:
                decoded_path = urllib.parse.unquote(decoded_path)
            decoded_path = decoded_path.replace("\\", "/")
            norm_base = os.path.abspath(allowed_base_dir)
            norm_target = os.path.abspath(os.path.normpath(decoded_path))
            if not norm_target.startswith(norm_base):
                result['status'] = 'failed'
                result['error_message'] = f"Target path outside allowed directory: {norm_target}"
                return result

            # Create target directory if needed BEFORE atomic check
            target_dir = os.path.dirname(norm_target)
            os.makedirs(target_dir, exist_ok=True)

            # Atomic existence check (similar to requests_fetcher, but simpler for Playwright context)
            # If file exists and force is False, skip.
            if not force and os.path.exists(norm_target):
                 result['status'] = 'skipped'
                 return result
            # If file exists and force is True, we'll overwrite via os.replace later.
            # If file doesn't exist, we proceed.

            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=True)
                context = await browser.new_context(
                    java_script_enabled=True,
                    bypass_csp=True,
                    ignore_https_errors=True,
                    viewport={'width': 1280, 'height': 800},
                    record_har_path=None,
                    record_video_dir=None,
                    accept_downloads=False,
                    user_agent="Mozilla/5.0 (compatible; MCPBot/1.0)",
                    base_url=None,
                    locale="en-US"
                )
                # Block images, media, fonts to reduce resource use
                await context.route("**/*", lambda route, request: (
                    route.abort() if request.resource_type in ["image", "media", "font"] else route.continue_()
                ))

                page = await context.new_page()
                try:
                    await page.goto(url, timeout=(timeout or TIMEOUT_PLAYWRIGHT) * 1000)
                    # Strip scripts to avoid DOM-based attacks
                    await page.evaluate("""
                        for (const script of document.querySelectorAll('script')) {
                            script.remove();
                        }
                    """)
                    content = await page.content()
                finally:
                    await context.close()
                    await browser.close()

            # Write to temp file first
            fd, temp_path = tempfile.mkstemp(dir=target_dir, suffix=".html")
            os.close(fd) # Close file descriptor from mkstemp

            try:
                async with aiofiles.open(temp_path, 'w', encoding='utf-8') as f:
                    await f.write(content)

                # Atomic rename/replace
                # Check again before replacing for TOCTOU protection if not forcing
                if not force and os.path.exists(norm_target):
                    os.remove(temp_path) # Clean up temp file
                    result['status'] = 'skipped' # File appeared during download
                    return result

                os.replace(temp_path, norm_target) # Atomically move temp file to final location

            except Exception as e:
                # Ensure temp file cleanup on error during write/replace
                if os.path.exists(temp_path):
                    os.remove(temp_path)
                raise e # Re-raise the exception to be caught by the outer handler

            # Compute hash
            md5 = hashlib.md5(content.encode('utf-8')).hexdigest()
            result['content_md5'] = md5

            # Extract links
            links = re.findall(r'''(?:href|src)=["'](.*?)["']''', content, re.IGNORECASE)
            result['detected_links'] = links

            result['status'] = 'success'
            return result

        except Exception as e:
            result['status'] = 'failed'
            result['error_message'] = str(e)
            return result