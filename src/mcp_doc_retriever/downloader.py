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

TIMEOUT_REQUESTS = 30
TIMEOUT_PLAYWRIGHT = 60
from src.mcp_doc_retriever.playwright_fetcher import fetch_single_url_playwright



# Global concurrency and resource controls for Playwright
playwright_semaphore = asyncio.Semaphore(3)  # Limit concurrent Playwright sessions
requests_semaphore = asyncio.Semaphore(10)  # Limit concurrent HTTP requests
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


async def _is_allowed_by_robots(url: str, client: httpx.AsyncClient, robots_cache: dict) -> bool:
    """
    from urllib.parse import urlparse
async def _is_allowed_by_robots(url: str, client: httpx.AsyncClient, robots_cache: dict) -> bool:
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
def parse_args():
    """
    Parse command-line arguments for the recursive downloader CLI.
    """
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
    """
    CLI entry point for the recursive downloader.
    """
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