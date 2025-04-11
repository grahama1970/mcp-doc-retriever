"""
Module: downloader.py

Description:
Orchestrates robust documentation fetching for a given documentation URL:
1. Checks (via Perplexity API) if the documentation is statically generated and available for direct download (e.g., on GitHub as Markdown).
2. If a static site exists, downloads all Markdown source files using git sparse-checkout (prefer) or full clone, enforcing directory/file restrictions.
3. If no static site is available, falls back to downloading all documentation pages using Playwright/browser automation or httpx.
4. Adds deep contextual logging, error propagation, and security best practices per lessons_learned.json.

Third-party packages:
- httpx: https://www.python-httpx.org/
- aiofiles: https://github.com/Tinche/aiofiles
- Playwright: https://playwright.dev/python/
- git: https://git-scm.com/
- Perplexity API (via MCP): https://www.perplexity.ai/

Internal Modules:
- mcp_doc_retriever.utils: Helper functions (timeouts, semaphores, path/URL manipulation).
- mcp_doc_retriever.models: Pydantic models (IndexRecord).
- mcp_doc_retriever.robots: Robots.txt checking logic.
- mcp_doc_retriever.fetchers: URL fetching implementations (requests, playwright).

Sample input (programmatic call):
await fetch_documentation_workflow(
    docs_url="https://docs.arangodb.com/stable/",
    download_id="arangodb_docs",
    base_dir="./downloads",
    depth=2,
    force=True,
    use_playwright=False,
    max_file_size=10485760
)

Expected output:
- If static docs are detected, Markdown sources are downloaded to ./downloads/content/arangodb_docs/site/content/
- If not, all documentation pages are recursively downloaded and indexed under ./downloads/content/ and ./downloads/index/
- After download, all nested JSON examples are extracted from Markdown/HTML and indexed in ./downloads/index/arangodb_docs_examples.jsonl (or similar).
- All actions are logged and errors are propagated to the orchestration layer.
"""
import subprocess
from typing import Optional

async def detect_site_type(
    docs_url: str,
    timeout: int = 10,
    logger_override=None,
) -> str:
    """
    Detects if a documentation site is static (direct HTML/Markdown, minimal JS) or JS-driven.
    Returns: "static", "js-driven", or "unknown"
    """
    _logger = logger_override or logger
    import httpx
    import re

    _logger.info(f"[SiteTypeDetection] Fetching {docs_url} to analyze site type...")
    try:
        headers = {
            "User-Agent": "MCPBot/1.0 (site-type-detector)"
        }
        with httpx.Client(timeout=timeout, headers=headers, follow_redirects=True) as client:
            resp = client.get(docs_url)
            content = resp.text
            _logger.debug(f"[SiteTypeDetection] HTTP status: {resp.status_code}, Content length: {len(content)}")
            # Heuristics for static site:
            # - Main content present (look for <main>, <article>, or lots of <p>)
            # - Few <script> tags, or scripts are analytics only
            # - No "Please enable JavaScript" or "Loading..." placeholders
            # - No large inline JS blocks
            script_count = len(re.findall(r"<script\b", content, re.I))
            main_count = len(re.findall(r"<main\b", content, re.I))
            article_count = len(re.findall(r"<article\b", content, re.I))
            p_count = len(re.findall(r"<p\b", content, re.I))
            noscript = re.search(r"<noscript>.*?</noscript>", content, re.I | re.S)
            js_warning = re.search(r"enable javascript|please enable javascript|requires javascript", content, re.I)
            loading = re.search(r"loading\.\.\.|please wait", content, re.I)
            # If main content is present and script count is low, likely static
            if (main_count > 0 or article_count > 0 or p_count > 10) and script_count < 5 and not js_warning and not loading:
                _logger.info(f"[SiteTypeDetection] Site appears STATIC (main content present, minimal JS).")
                return "static"
            # If lots of scripts, JS warnings, or loading placeholders, likely JS-driven
            if script_count > 10 or js_warning or loading or (main_count == 0 and article_count == 0 and p_count < 5):
                _logger.info(f"[SiteTypeDetection] Site appears JS-DRIVEN (many scripts, JS warnings, or missing main content).")
                return "js-driven"
            # If <noscript> present with warning, likely JS-driven
            if noscript and ("javascript" in noscript.group(0).lower()):
                _logger.info(f"[SiteTypeDetection] Site appears JS-DRIVEN (noscript warning detected).")
                return "js-driven"
            _logger.info(f"[SiteTypeDetection] Site type UNKNOWN (heuristics inconclusive).")
            return "unknown"
    except Exception as e:
        _logger.error(f"[SiteTypeDetection] Error fetching/analyzing {docs_url}: {e}", exc_info=True)
        return "unknown"

async def fetch_documentation_workflow(
    docs_url: str,
    download_id: str,
    base_dir: str = "./downloads",
    depth: int = 3,
    force: bool = False,
    use_playwright: bool = False,
    max_file_size: Optional[int] = 10 * 1024 * 1024,
    timeout_requests: Optional[int] = None,
    timeout_playwright: Optional[int] = None,
    logger_override=None,
) -> None:
    """
    Orchestrates documentation fetching:
    1. Detects if the site is static or JS-driven.
    2. If static, downloads recursively.
    3. If JS-driven, uses Perplexity to check for a static version elsewhere.
    4. If static site found, downloads it; else, falls back to dynamic scraping.
    Adds deep contextual logging and enforces directory/file restrictions.

    Args:
        docs_url: The documentation site URL.
        download_id: Unique identifier for this download batch.
        base_dir: Root directory for downloads.
        depth: Max recursion depth for fallback crawling.
        force: Overwrite existing files.
        use_playwright: Use Playwright for fallback crawling.
        max_file_size: Max file size for downloads.
        timeout_requests: Timeout for httpx requests.
        timeout_playwright: Timeout for Playwright.
        logger_override: Optional logger to use.

    Returns:
        None
    """
    _logger = logger_override or logger

    from mcp_doc_retriever.utils import TIMEOUT_REQUESTS, TIMEOUT_PLAYWRIGHT
    if timeout_requests is None:
        timeout_requests = TIMEOUT_REQUESTS
    if timeout_playwright is None:
        timeout_playwright = TIMEOUT_PLAYWRIGHT
    _logger.info(f"Starting documentation fetch workflow for {docs_url} (ID: {download_id})")

    # Step 1: Detect site type
    site_type = await detect_site_type(docs_url, timeout=timeout_requests, logger_override=_logger)
    _logger.info(f"[Workflow] Site type detected: {site_type}")

    if site_type == "static":
        _logger.info(f"[Workflow] Detected static site. Proceeding with recursive download (requests/Playwright).")
        await start_recursive_download(
            start_url=docs_url,
            depth=depth,
            force=force,
            download_id=download_id,
            base_dir=base_dir,
            use_playwright=use_playwright,
            max_file_size=max_file_size,
            timeout_requests=timeout_requests,
            timeout_playwright=timeout_playwright,
        )
        # After download, extract and index JSON examples from HTML/Markdown
        try:
            from mcp_doc_retriever.example_extractor import extract_and_index_examples
            abs_base_dir = os.path.abspath(base_dir)
            content_dir = os.path.join(abs_base_dir, "content", download_id)
            if not os.path.exists(content_dir):
                content_dir = os.path.join(abs_base_dir, "content")
            example_index_path = os.path.join(abs_base_dir, "index", f"{download_id}_examples.jsonl")
            n_examples = extract_and_index_examples(
                content_dir, example_index_path, file_types=[".md", ".markdown", ".html", ".htm"]
            )
            _logger.info(f"Extracted and indexed {n_examples} JSON examples from content to {example_index_path}")
        except Exception as ex:
            _logger.error(f"Example extraction/indexing failed: {ex}", exc_info=True)
        return

    # If JS-driven or unknown, try Perplexity to find a static version elsewhere
    _logger.info(f"[Workflow] Site appears JS-driven or unknown. Querying Perplexity for alternate static documentation source.")
    from mcp_sdk import use_mcp_tool  # type: ignore
    try:
        import asyncio
        import json as _json
        perplexity_args = {
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "You are a documentation analysis assistant. Given a documentation website URL, "
                        "determine if the documentation is statically generated and available for direct download "
                        "(such as on GitHub, in Markdown or similar source format). If so, provide the direct repository URL "
                        "and the path to the documentation sources. If not, state that only browser-based crawling is possible."
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        f"Is the documentation at {docs_url} statically generated and available for direct download (e.g., on GitHub as Markdown)? "
                        "If so, what is the repository URL and the path to the documentation sources?"
                    ),
                },
            ]
        }
        from mcp_sdk import use_mcp_tool as mcp_use_tool
        result = await mcp_use_tool(
            server_name="perplexity-ask-fresh",
            tool_name="perplexity_ask",
            arguments=perplexity_args,
        )
        answer = result.get("result") or result.get("output") or result
        _logger.info(f"[Workflow] Perplexity response: {answer}")
        import re
        repo_url = None
        doc_path = None
        if isinstance(answer, str):
            repo_match = re.search(r"(https://github\.com/[^\s\)]+)", answer)
            path_match = re.search(r"(site/content[^\s\)]*)", answer)
            if repo_match:
                repo_url = repo_match.group(1)
            if path_match:
                doc_path = path_match.group(1)
        if repo_url and doc_path:
            _logger.info(f"[Workflow] Static documentation source detected by Perplexity: {repo_url} (path: {doc_path})")
            # Download Markdown sources using git sparse-checkout
            import os
            abs_base_dir = os.path.abspath(base_dir)
            content_dir = os.path.join(abs_base_dir, "content", download_id)
            os.makedirs(content_dir, exist_ok=True)
            if not os.path.commonpath([content_dir, abs_base_dir]) == abs_base_dir:
                raise RuntimeError("Resolved content_dir escapes allowed base_dir!")
            try:
                _logger.info(f"[Workflow] Cloning {repo_url} (sparse-checkout: {doc_path}) into {content_dir}")
                subprocess.run(
                    ["git", "init"], cwd=content_dir, check=True
                )
                subprocess.run(
                    ["git", "remote", "add", "origin", repo_url], cwd=content_dir, check=True
                )
                subprocess.run(
                    ["git", "config", "core.sparseCheckout", "true"], cwd=content_dir, check=True
                )
                sparse_path = doc_path.rstrip("/") + "/"
                with open(os.path.join(content_dir, ".git", "info", "sparse-checkout"), "w") as f:
                    f.write(f"{sparse_path}*\n")
                subprocess.run(
                    ["git", "pull", "origin", "main"], cwd=content_dir, check=True
                )
                _logger.info(f"[Workflow] Successfully performed sparse-checkout of {repo_url} ({sparse_path})")
            except Exception as git_e:
                _logger.error(f"[Workflow] Git sparse-checkout failed: {git_e}. Falling back to full clone.")
                try:
                    subprocess.run(
                        ["git", "clone", repo_url, content_dir], check=True
                    )
                except Exception as clone_e:
                    _logger.critical(f"[Workflow] Full git clone also failed: {clone_e}")
                    raise
            _logger.info(f"[Workflow] Documentation sources downloaded to {content_dir}")
            try:
                from mcp_doc_retriever.example_extractor import extract_and_index_examples
                example_index_path = os.path.join(abs_base_dir, "index", f"{download_id}_examples.jsonl")
                n_examples = extract_and_index_examples(content_dir, example_index_path, file_types=[".md", ".markdown"])
                _logger.info(f"[Workflow] Extracted and indexed {n_examples} JSON examples from Markdown to {example_index_path}")
            except Exception as ex:
                _logger.error(f"[Workflow] Example extraction/indexing failed: {ex}", exc_info=True)
            return
        else:
            _logger.info("[Workflow] No static documentation source detected by Perplexity. Falling back to dynamic scraping.")
    except Exception as perplexity_e:
        _logger.error(f"[Workflow] Perplexity/static doc detection failed: {perplexity_e}. Falling back to dynamic scraping.")

    # Fallback to dynamic scraping (Playwright or requests)
    await start_recursive_download(
        start_url=docs_url,
        depth=depth,
        force=force,
        download_id=download_id,
        base_dir=base_dir,
        use_playwright=use_playwright,
        max_file_size=max_file_size,
        timeout_requests=timeout_requests,
        timeout_playwright=timeout_playwright,
    )
    try:
        from mcp_doc_retriever.example_extractor import extract_and_index_examples
        abs_base_dir = os.path.abspath(base_dir)
        content_dir = os.path.join(abs_base_dir, "content", download_id)
        if not os.path.exists(content_dir):
            content_dir = os.path.join(abs_base_dir, "content")
        example_index_path = os.path.join(abs_base_dir, "index", f"{download_id}_examples.jsonl")
        n_examples = extract_and_index_examples(content_dir, example_index_path, file_types=[".html", ".htm"])
        _logger.info(f"[Workflow] Extracted and indexed {n_examples} JSON examples from HTML to {example_index_path}")
    except Exception as ex:
        _logger.error(f"[Workflow] Example extraction/indexing failed: {ex}", exc_info=True)

import os
import json
import asyncio
import re
import httpx
import aiofiles
from urllib.parse import urlparse, urljoin
import logging
import traceback

# Configure logging (obtained from the application's central config)
logger = logging.getLogger(__name__)

# Import constants and utilities
from mcp_doc_retriever.utils import (
    TIMEOUT_REQUESTS,
    TIMEOUT_PLAYWRIGHT,
    canonicalize_url,
    url_to_local_path,
)
from mcp_doc_retriever.utils import is_url_private_or_internal

# Import data models
from mcp_doc_retriever.models import IndexRecord

# Import specialized functions
from mcp_doc_retriever.robots import _is_allowed_by_robots
from mcp_doc_retriever.fetchers import (
    fetch_single_url_requests,
    fetch_single_url_playwright,
)


async def start_recursive_download(
    start_url: str,
    depth: int,
    force: bool,
    download_id: str,
    base_dir: str = "/app/downloads",  # Sensible default for container environment
    use_playwright: bool = False,
    timeout_requests: int = TIMEOUT_REQUESTS,
    timeout_playwright: int = TIMEOUT_PLAYWRIGHT,
    max_file_size: int | None = None,
) -> None:
    """
    Starts the asynchronous recursive download process.

    Args:
        start_url: The initial URL to begin downloading from.
        depth: Maximum recursion depth (0 means only the start_url).
        force: Whether to overwrite existing files.
        download_id: A unique identifier for this download batch.
        base_dir: The root directory for all downloads (content and index).
        use_playwright: Whether to use Playwright (True) or httpx (False) by default.
        timeout_requests: Timeout in seconds for httpx requests.
        timeout_playwright: Timeout in seconds for Playwright operations.
        max_file_size: Maximum size in bytes for individual downloaded files.
    """
    logger.debug(f"!!! Entering start_recursive_download for ID: {download_id}, URL: {start_url}") # ADDED FOR DEBUG
    # Ensure base_dir is absolute for reliable path comparisons later
    abs_base_dir = os.path.abspath(base_dir)
    logger.info(f"Download starting. ID: {download_id}, Base Dir: {abs_base_dir}")

    # Prepare directories and index file path safely
    index_dir = os.path.join(abs_base_dir, "index")
    content_base_dir = os.path.join(abs_base_dir, "content")
    try:
        os.makedirs(index_dir, exist_ok=True)
        os.makedirs(content_base_dir, exist_ok=True)
        logger.debug(f"Ensured directories exist: {index_dir}, {content_base_dir}")
    except OSError as e:
        logger.critical(
            f"Cannot create base directories ({index_dir}, {content_base_dir}): {e}. Aborting download."
        )
        # Consider raising an exception here or returning a status
        return

    # Sanitize download_id to prevent path traversal in index filename
    # Allow letters, numbers, underscore, hyphen, dot
    safe_download_id = re.sub(r"[^a-zA-Z0-9_\-\.]", "_", download_id)
    if not safe_download_id:
        safe_download_id = "default_download"
        logger.warning(
            f"Original download_id '{download_id}' was invalid or empty, using '{safe_download_id}'"
        )
    index_path = os.path.join(index_dir, f"{safe_download_id}.jsonl")
    logger.info(f"Using index file: {index_path}")

    # Initialize queue and visited set
    queue = asyncio.Queue()
    visited = set()  # Store canonical URLs

    # Canonicalize start URL and domain
    try:
        start_canonical = canonicalize_url(start_url)
        await queue.put((start_canonical, 0))  # Add canonical URL tuple (url, depth)
        visited.add(start_canonical)  # Add start URL to visited immediately
        start_domain = urlparse(start_canonical).netloc
        if not start_domain:
            raise ValueError("Could not extract domain from start URL")
        logger.info(
            f"Starting domain: {start_domain}, Canonical start URL: {start_canonical}, Depth: {depth}"
        )
    except Exception as e:
        logger.critical(f"Invalid start URL '{start_url}': {e}. Aborting download.")
        # Optionally write a failure record to index?
        return

    robots_cache = {}

    # Use a single shared httpx client for the entire download process
    # Configure reasonable timeouts
    client_timeout = httpx.Timeout(timeout_requests, read=timeout_requests, connect=15)
    # Define standard headers
    headers = {
        "User-Agent": "MCPBot/1.0 (compatible; httpx; +https://example.com/botinfo)"
    }  # Example informative UA

    async with httpx.AsyncClient(
        follow_redirects=True, timeout=client_timeout, headers=headers
    ) as client:
        while not queue.empty():
            # Dequeue the next URL to process
            current_canonical_url, current_depth = await queue.get()
            logger.debug(
                f"Processing URL: {current_canonical_url} at depth {current_depth}"
            )

            # --- Pre-download Checks ---
            try:
                # SSRF protection: block internal/private URLs early
                if is_url_private_or_internal(current_canonical_url):
                    logger.warning(f"Blocked potential SSRF/internal URL: {current_canonical_url}")
                    record = IndexRecord(
                        original_url=current_canonical_url,
                        canonical_url=current_canonical_url,
                        local_path="",
                        content_md5=None,
                        fetch_status="failed_ssrf",
                        http_status=None,
                        error_message="Blocked by SSRF protection (internal/private address)",
                    )
                    try:
                        async with aiofiles.open(index_path, "a", encoding="utf-8") as f:
                            await f.write(record.model_dump_json(exclude_none=True) + "\n")
                    except Exception as write_e:
                        logger.error(f"Failed to write SSRF block index record for {current_canonical_url}: {write_e}")
                    queue.task_done()
                    continue

                # Domain restriction check (using canonical URL)
                parsed_url = urlparse(current_canonical_url)
                if parsed_url.netloc != start_domain:
                    logger.debug(
                        f"Skipping URL outside start domain {start_domain}: {current_canonical_url}"
                    )
                    queue.task_done()
                    continue

                # Robots.txt check
                allowed = await _is_allowed_by_robots(current_canonical_url, client, robots_cache)
                if not allowed:
                    logger.info(f"Blocked by robots.txt: {current_canonical_url}")
                    record = IndexRecord(
                        original_url=current_canonical_url,
                        canonical_url=current_canonical_url,
                        local_path="",
                        content_md5=None,
                        fetch_status="failed_robotstxt",
                        http_status=None,
                        error_message="Blocked by robots.txt",
                    )
                    try:
                        async with aiofiles.open(index_path, "a", encoding="utf-8") as f:
                            await f.write(record.model_dump_json(exclude_none=True) + "\n")
                    except Exception as write_e:
                        logger.error(f"Failed to write robots.txt index record for {current_canonical_url}: {write_e}")
                    queue.task_done()
                    continue

                # Map URL to local path *after* robots check and domain check pass
                local_path = url_to_local_path(content_base_dir, current_canonical_url)
                logger.debug(f"Mapped {current_canonical_url} to local path: {local_path}")

            except Exception as pre_check_e:
                logger.error(
                    f"Error during pre-download checks for {current_canonical_url}: {pre_check_e}",
                    exc_info=True,
                )
                queue.task_done()
                continue

            # --- Main Download Attempt ---
            result = None
            fetch_status = (
                "failed_request"  # Default to failure unless explicitly successful
            )
            error_message = "Download did not complete successfully."  # Default error
            content_md5 = None
            http_status = None
            detected_links = []
            final_local_path = ""  # Store path only if successfully saved

            try:
                logger.info(
                    f"Attempting download: {current_canonical_url} (Depth {current_depth}) -> {local_path}"
                )
                fetcher_kwargs = {
                    "url": current_canonical_url,  # Pass canonical URL to fetchers
                    "target_local_path": local_path,
                    "force": force,
                    "allowed_base_dir": content_base_dir,
                }

                # Choose the fetcher based on the flag
                if use_playwright:
                    logger.debug("Using Playwright fetcher")
                    result = await fetch_single_url_playwright(
                        **fetcher_kwargs, timeout=timeout_playwright
                    )
                else:
                    logger.debug("Using Requests (httpx) fetcher")
                    result = await fetch_single_url_requests(
                        **fetcher_kwargs,
                        timeout=timeout_requests,
                        client=client,
                        max_size=max_file_size,
                    )

                # --- Log the raw result from the fetcher ---
                logger.info(f"Fetcher raw result for {current_canonical_url}: {result!r}") # Use !r for detailed repr

                # --- Process the result dictionary ---
                if result:
                    status_from_result = result.get("status")
                    error_message_from_result = result.get("error_message")
                    content_md5 = result.get("content_md5")
                    http_status = result.get("http_status")
                    detected_links = result.get("detected_links", [])

                    # Map fetcher status to IndexRecord status
                    if status_from_result == "success":
                        fetch_status = "success"
                        final_local_path = local_path  # Store path on success
                        error_message = None
                    elif status_from_result == "skipped":
                        fetch_status = "skipped"
                        error_message = (
                            error_message_from_result or "Skipped (exists or TOCTOU)"
                        )
                        logger.info(
                            f"Skipped download for {current_canonical_url}: {error_message}"
                        )
                    elif (
                        status_from_result == "failed_paywall"
                    ):  # Handle specific failure types
                        fetch_status = "failed_paywall"
                        error_message = (
                            error_message_from_result
                            or "Failed due to potential paywall"
                        )
                    else:  # Consolidate other failures ('failed', 'failed_request')
                        fetch_status = "failed_request"
                        # Use error from result if available, otherwise keep default
                        error_message = (
                            error_message_from_result
                            or f"Fetcher failed with status '{status_from_result}'."
                        )

                    # Truncate error message if it exists
                    if error_message:
                        error_message = str(error_message)[:2000]  # Limit length

                else:
                    # This case should ideally not happen if fetchers always return a dict
                    logger.error(
                        f"Fetcher returned None for {current_canonical_url}, treating as failed_request."
                    )
                    error_message = "Fetcher returned None result."
                    fetch_status = "failed_request"

            except (
                Exception
            ) as fetch_exception:  # Catch ALL exceptions during the fetcher call itself
                tb = traceback.format_exc()
                error_msg_str = f"Exception during fetcher execution: {str(fetch_exception)} | Traceback: {tb}"
                logger.error(
                    f"Exception caught processing {current_canonical_url}: {error_msg_str}"
                )
                fetch_status = "failed_request"
                error_message = error_msg_str[:2000]  # Truncate

            # --- Create and Write Index Record ---
            try:
                record = IndexRecord(
                    original_url=current_canonical_url,  # Store the canonical URL fetched
                    canonical_url=current_canonical_url,
                    local_path=final_local_path
                    if fetch_status == "success"
                    else "",  # Store path only on success
                    content_md5=content_md5,
                    fetch_status=fetch_status,
                    http_status=http_status,
                    error_message=error_message,
                    code_snippets=result.get("code_snippets"),
                )
                logger.info(f"Preparing to write index record object: {record!r}")
                record_json = record.model_dump_json(exclude_none=True)
                logger.info(f"Writing index record JSON: {record_json}")
                logger.debug(f"Attempting to open index file for append: {index_path}") # Log before open
                async with aiofiles.open(index_path, "a", encoding="utf-8") as f:
                    await f.write(record_json + "\n")
                    logger.debug(f"Successfully wrote index record for {current_canonical_url}") # Log after write

            except Exception as write_e:
                logger.critical(
                    f"CRITICAL: Failed to create or write index record for {current_canonical_url}: {write_e}",
                    exc_info=True,
                )
                logger.error(
                    f"Failed Record Data Hint: status={fetch_status}, error='{error_message}'"
                )

            # --- Recurse if successful and within depth limit ---
            if fetch_status == "success" and current_depth < depth:
                logger.debug(
                    f"Successfully downloaded {current_canonical_url}. Found {len(detected_links)} potential links. Max depth {depth}, current {current_depth}."
                )
                if not detected_links:
                    logger.info(
                        f"No links detected or extracted from {current_canonical_url}"
                    )

                for link in detected_links:
                    abs_link = None
                    try:
                        # Construct absolute URL relative to the *current* page's canonical URL
                        abs_link = urljoin(current_canonical_url, link.strip())

                        # Basic filter: only http/https
                        parsed_abs_link = urlparse(abs_link)
                        if parsed_abs_link.scheme not in ["http", "https"]:
                            logger.debug(f"Skipping non-http(s) link: {abs_link}")
                            continue

                        # Canonicalize the potential next link
                        canon_link = canonicalize_url(abs_link)
                        canon_domain = urlparse(canon_link).netloc

                        # Check domain and visited status BEFORE putting in queue
                        if canon_domain == start_domain and canon_link not in visited:
                            visited.add(
                                canon_link
                            )  # Add to visited *before* adding to queue
                            logger.debug(
                                f"Adding link to queue: {canon_link} (from {link} on {current_canonical_url})"
                            )
                            await queue.put((canon_link, current_depth + 1))
                        # Optional: Log reasons for skipping links
                        # elif canon_domain != start_domain:
                        #     logger.debug(f"Skipping link to different domain: {canon_link}")
                        # elif canon_link in visited:
                        #     logger.debug(f"Skipping link already processed or in queue: {canon_link}")

                    except Exception as link_processing_exception:
                        logger.warning(
                            f"Failed to process or queue link '{link}' found in {current_canonical_url} (Absolute: {abs_link}): {link_processing_exception}",
                            exc_info=True,
                        )
                        # Continue to the next link even if one fails
            elif fetch_status == "success" and current_depth >= depth:
                logger.info(
                    f"Reached max depth {depth}, not recursing further from {current_canonical_url}"
                )

            # Mark the current task from the queue as done
            queue.task_done()

        # Wait for all tasks in the queue to be processed (optional, good practice)
        await queue.join()
        logger.info(
            f"Download queue processed. Download finished for ID: {download_id}"
        )


import sys

def usage_example():
    """
    Real-world usage: Download a single page or all documentation pages recursively, with optional Playwright support.
    Usage:
        python -m src.mcp_doc_retriever.downloader [single|recursive] [requests|playwright]
    """
    # Default: recursive download, requests fetcher
    mode = "recursive"
    fetcher = "requests"
    if len(sys.argv) > 1:
        mode = sys.argv[1].lower()
    if len(sys.argv) > 2:
        fetcher = sys.argv[2].lower()

    use_playwright = fetcher == "playwright"

    # Set parameters based on mode
    if mode == "single":
        start_url = "https://docs.arangodb.com/stable/index-and-search/arangosearch/arangosearch-views-reference/"
        depth = 0
        download_id = "arangodb_single"
        base_dir = "./downloads"
        log_msg = f"Downloading single ArangoDB documentation page using {'Playwright' if use_playwright else 'requests'}."
    else:
        start_url = "https://docs.arangodb.com/stable/"
        depth = 5
        download_id = "arangodb_full"
        base_dir = "./downloads"
        log_msg = f"Recursively downloading all ArangoDB documentation pages using {'Playwright' if use_playwright else 'requests'}."

    # Logging setup
    log_level = logging.INFO
    log_format = (
        "%(asctime)s - %(levelname)s - [%(name)s:%(funcName)s:%(lineno)d] - %(message)s"
    )
    date_format = "%Y-%m-%d %H:%M:%S"
    logging.basicConfig(level=log_level, format=log_format, datefmt=date_format)
    logging.getLogger("httpx").setLevel(logging.WARNING)

    async def run_real_world():
        logger.info(log_msg)
        try:
            await start_recursive_download(
                start_url=start_url,
                depth=depth,
                force=True,
                download_id=download_id,
                base_dir=base_dir,
                use_playwright=use_playwright,
                max_file_size=10 * 1024 * 1024,
            )
            logger.info("Download completed.")
            # Verify output
            index_file = os.path.join(base_dir, "index", f"{download_id}.jsonl")
            if os.path.exists(index_file):
                logger.info(f"Index file found: {index_file}")
            else:
                logger.error(f"Index file NOT found: {index_file}")

            # After download, extract and index JSON examples
            try:
                from mcp_doc_retriever.example_extractor import extract_and_index_examples
                content_dir = os.path.join(base_dir, "content", download_id)
                if not os.path.exists(content_dir):
                    content_dir = os.path.join(base_dir, "content")
                example_index_path = os.path.join(base_dir, "index", f"{download_id}_examples.jsonl")
                n_examples = extract_and_index_examples(
                    content_dir,
                    example_index_path,
                    file_types=[".md", ".markdown", ".html", ".htm"]
                )
                logger.info(f"Extracted and indexed {n_examples} JSON examples to {example_index_path}")
            except Exception as ex:
                logger.error(f"Example extraction/indexing failed: {ex}", exc_info=True)
        except Exception as e:
            logger.error(
                f"An error occurred during the download: {e}", exc_info=True
            )
        finally:
            pass

    asyncio.run(run_real_world())
    # No stray calls; usage_example() is the correct entrypoint.


if __name__ == "__main__":
    # This block executes when the script is run directly
    # Usage: python -m src.mcp_doc_retriever.downloader [single|recursive]
    usage_example()
