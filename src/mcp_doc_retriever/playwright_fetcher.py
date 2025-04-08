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

            # Skip if exists and not force
            if os.path.exists(norm_target) and not force:
                result['status'] = 'skipped'
                return result

            # Create target directory
            os.makedirs(os.path.dirname(norm_target), exist_ok=True)

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

            # Save sanitized content
            async with aiofiles.open(norm_target, 'w', encoding='utf-8') as f:
                await f.write(content)

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
async def fetch_single_url_playwright(*args, **kwargs):
    raise NotImplementedError("fetch_single_url_playwright must be patched in tests or implemented")