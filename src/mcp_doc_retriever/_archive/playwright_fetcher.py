"""
Playwright-based async fetcher for MCP Document Retriever.

- Fetches a single URL using Playwright with concurrency control.
- Saves HTML content atomically.
- Extracts links from the page.

Links:
- Playwright Python: https://playwright.dev/python/
- aiofiles: https://github.com/Tinche/aiofiles
- asyncio: https://docs.python.org/3/library/asyncio.html

Sample input:
url = "https://docs.python.org/3/"
target_local_path = "./downloads/content/docs.python.org/index.html"
force = False

Sample output:
{
  'status': 'success',
  'content_md5': 'md5hash',
  'detected_links': ['https://docs.python.org/3/tutorial/', ...],
  'error_message': None
}
"""

import hashlib
import re
from mcp_doc_retriever.utils import playwright_semaphore, TIMEOUT_PLAYWRIGHT

async def fetch_single_url_playwright(url, target_local_path, force=False, allowed_base_dir=".", timeout=None):
    """
    Minimal Playwright fetcher with concurrency control and basic protections.
    """
    import os
    import urllib.parse
    import aiofiles
    import tempfile
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

            # Atomic existence check
            if not force and os.path.exists(norm_target):
                 result['status'] = 'skipped'
                 return result

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
                await context.route("**/*", lambda route, request: (
                    route.abort() if request.resource_type in ["image", "media", "font"] else route.continue_()
                ))

                page = await context.new_page()
                try:
                    await page.goto(url, timeout=(timeout or TIMEOUT_PLAYWRIGHT) * 1000)
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
            os.close(fd)

            try:
                async with aiofiles.open(temp_path, 'w', encoding='utf-8') as f:
                    await f.write(content)

                if not force and os.path.exists(norm_target):
                    os.remove(temp_path)
                    result['status'] = 'skipped'
                    return result

                os.replace(temp_path, norm_target)

            except Exception as e:
                if os.path.exists(temp_path):
                    os.remove(temp_path)
                raise e

            md5 = hashlib.md5(content.encode('utf-8')).hexdigest()
            result['content_md5'] = md5

            links = re.findall(r'''(?:href|src)=["'](.*?)["']''', content, re.IGNORECASE)
            result['detected_links'] = links

            result['status'] = 'success'
            return result

        except Exception as e:
            result['status'] = 'failed'
            result['error_message'] = str(e)
            return result

if __name__ == "__main__":
    import asyncio
    url = "https://docs.python.org/3/"
    target_path = "./downloads/content/docs.python.org/index_playwright.html"
    result = asyncio.run(fetch_single_url_playwright(url, target_path, force=True))
    print("Playwright fetch result:", result)