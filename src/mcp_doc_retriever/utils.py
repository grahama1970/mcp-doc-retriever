from urllib.parse import urlparse, urlunparse
import hashlib
import os
import asyncio
import re

TIMEOUT_REQUESTS = 30
TIMEOUT_PLAYWRIGHT = 60

# Limit concurrent operations to prevent resource exhaustion or rate limiting
playwright_semaphore = asyncio.Semaphore(
    3
)  # Limit concurrent Playwright browser instances/contexts
requests_semaphore = asyncio.Semaphore(
    10
)  # Limit concurrent outgoing HTTP requests via httpx
import html
from typing import List, Dict, Any, Optional
from .models import ContentBlock
import socket
import ipaddress
import json
from . import config
from bs4 import BeautifulSoup



def canonicalize_url(url: str) -> str:
    """Normalize URL by:
    - Lowercasing scheme and host
    - Removing default ports (80 for http, 443 for https)
    - Removing fragments (#...)
    - Removing query parameters (?...)
    - Ensuring path starts with '/' if not empty
    """
    try:
        parsed = urlparse(url)

        # Lowercase scheme and netloc (host)
        scheme = parsed.scheme.lower()
        netloc = parsed.netloc.lower()

        # Remove default ports
        if ":" in netloc:
            host, port_str = netloc.split(":", 1)
            try:
                port = int(port_str)
                if (scheme == "http" and port == 80) or (
                    scheme == "https" and port == 443
                ):
                    netloc = host
            except ValueError:
                # Keep netloc as is if port is not a valid integer
                pass

        # Ensure path starts with '/' if it exists, otherwise it's empty
        path = parsed.path if parsed.path else ""
        if path and not path.startswith("/"):
            path = "/" + path  # Should not happen with valid urlparse, but defensive

        # Remove params, query, and fragment
        # path parameter in urlunparse handles the actual path component
        return urlunparse((scheme, netloc, path, "", "", ""))
    except Exception as e:
        # If urlparse fails or any other error occurs, maybe return original or raise?
        # Raising might be better to signal invalid input upstream.
        raise ValueError(f"Could not canonicalize URL: {url} - Error: {e}") from e


def generate_download_id(url: str) -> str:
    """Generate unique download ID as MD5 hash of canonical URL."""
    try:
        canonical_url_str = canonicalize_url(url)
        # Use utf-8 encoding for consistency
        return hashlib.md5(canonical_url_str.encode("utf-8")).hexdigest()
    except ValueError as e:
        # Handle cases where canonicalization fails
        raise ValueError(
            f"Could not generate download ID for invalid URL: {url} - {e}"
        ) from e


# --- Code Block Extraction Utilities ---

def extract_code_blocks_from_html(html_content: str, source_url: Optional[str] = None) -> List[Dict[str, Any]]:
    """
    Extract code blocks from HTML content.
    Returns a list of dicts with code, language, block_type, and metadata.
    """
    soup = BeautifulSoup(html_content, "html.parser")
    code_blocks = []

    # <pre><code>...</code></pre> and <pre>...</pre>
    for pre in soup.find_all("pre"):
        code_tag = pre.find("code")
        if code_tag:
            code = code_tag.get_text()
            language = code_tag.get("class", [None])[0]
            block_type = "pre>code"
        else:
            code = pre.get_text()
            language = None
            block_type = "pre"
        code_blocks.append({
            "code": code,
            "language": language,
            "block_type": block_type,
            "source_url": source_url,
        })

    # Standalone <code> (not inside <pre>)
    for code_tag in soup.find_all("code"):
        if code_tag.find_parent("pre"):
            continue  # Already handled
        code = code_tag.get_text()
        language = code_tag.get("class", [None])[0]
        code_blocks.append({
            "code": code,
            "language": language,
            "block_type": "code",
            "source_url": source_url,
        })

    return code_blocks

def extract_code_blocks_from_markdown(md_content: str, source_url: Optional[str] = None) -> List[Dict[str, Any]]:
    """
    Extract code blocks from Markdown content (fenced and indented).
    Returns a list of dicts with code, language, block_type, and metadata.
    """
    code_blocks = []
    # Fenced code blocks: ```lang\ncode\n```
    fenced = re.findall(r"```(\w+)?\n(.*?)```", md_content, re.DOTALL)
    for lang, code in fenced:
        code_blocks.append({
            "code": code,
            "language": lang if lang else None,
            "block_type": "fenced",
            "source_url": source_url,
        })
    # Indented code blocks (4 spaces or tab)
    indented = re.findall(r"(?:^|\n)((?:    |\t).+(\n(?:    |\t).+)*)", md_content)
    for block, _ in indented:
        code_blocks.append({
            "code": block,
            "language": None,
            "block_type": "indented",
            "source_url": source_url,
        })
    return code_blocks

def extract_json_from_code_block(code: str) -> Optional[Any]:
    """
    If the code block is JSON, parse and return the structure, else None.
    """
    try:
        import json
        return json.loads(code)
    except Exception:
        return None

# --- Enhanced Content Extraction Utilities ---
def clean_html_for_search(
    html_content: str,
    allowed_tags: Optional[List[str]] = None,
    remove_selectors: Optional[List[str]] = None
) -> str:
    """
    Remove common noise elements (sidebars, nav, footer, ads) and sanitize HTML for search.

    Args:
        html_content: Raw HTML string.
        allowed_tags: List of tags to keep (default: main, article, pre, code, section, div, p, span, h1-h6).
        remove_selectors: List of CSS selectors to remove (default: nav, aside, footer, header, .sidebar, .nav, .ads, etc.).

    Returns:
        Cleaned HTML string.
    """
    from bs4 import BeautifulSoup
    import bleach

    if allowed_tags is None:
        allowed_tags = [
            "main", "article", "pre", "code", "section", "div", "p", "span",
            "h1", "h2", "h3", "h4", "h5", "h6"
        ]
    if remove_selectors is None:
        remove_selectors = [
            "nav", "aside", "footer", "header", ".sidebar", ".nav", ".ads", ".advert", ".cookie", ".banner"
        ]

    soup = BeautifulSoup(html_content, "html.parser")
    # Remove noise elements by selector
    for selector in remove_selectors:
        for el in soup.select(selector):
            el.decompose()
    # Optionally, remove comments
    for comment in soup.find_all(string=lambda text: isinstance(text, type(soup.Comment))):
        comment.extract()
    # Convert back to string
    cleaned_html = str(soup)
    # Sanitize with bleach
    sanitized = bleach.clean(
        cleaned_html,
        tags=allowed_tags,
        attributes=bleach.sanitizer.ALLOWED_ATTRIBUTES,
        strip=True
    )
    return sanitized
def code_block_relevance_score(
    code: str,
    keywords: List[str],
    language: Optional[str] = None
) -> float:
    """
    Compute a relevance score for a code block based on keyword density and optional language match.

    Args:
        code: The code block content.
        keywords: List of keywords to match.
        language: Programming language of the code block (optional).

    Returns:
        A float score (0.0–1.0) indicating relevance.
    """
    if not code or not keywords:
        return 0.0
    code_lower = code.lower()
    total = len(keywords)
    matched = sum(1 for kw in keywords if kw.lower() in code_lower)
    density = matched / total if total else 0.0
    # Optionally boost score if language is specified and matches
    lang_boost = 1.1 if language and language.lower() in code_lower else 1.0
    return min(density * lang_boost, 1.0)
def json_structure_search(
    json_obj: Any,
    query: List[str],
    match_mode: str = "keys"
) -> dict:
    """
    Perform a structure-aware search on a JSON object.

    Args:
        json_obj: The JSON object to search.
        query: List of keys, values, or structure patterns to match.
        match_mode: "keys", "values", or "structure".

    Returns:
        dict with:
            - "matched_items": List of matched keys/values/paths.
            - "score": Fraction of query items matched (0.0–1.0).
            - "mode": The match mode used.
    """
    matched = []
    total = len(query)
    if total == 0:
        return {"matched_items": [], "score": 0.0, "mode": match_mode}

    def walk(obj, path=""):
        if isinstance(obj, dict):
            for k, v in obj.items():
                yield (path + "." + k if path else k, v)
                yield from walk(v, path + "." + k if path else k)
        elif isinstance(obj, list):
            for i, v in enumerate(obj):
                yield (f"{path}[{i}]", v)
                yield from walk(v, f"{path}[{i}]")
        else:
            yield (path, obj)

    if match_mode == "keys":
        keys = set()
        for p, v in walk(json_obj):
            if isinstance(v, (dict, list)):
                continue
            if "." in p:
                keys.add(p.split(".")[-1].split("[")[0])
            else:
                keys.add(p)
        for q in query:
            if q in keys:
                matched.append(q)
    elif match_mode == "values":
        values = []
        for p, v in walk(json_obj):
            if not isinstance(v, (dict, list)):
                values.append(str(v))
        for q in query:
            # Check if query term is contained in any value (case insensitive)
            if any(q.lower() in val.lower() for val in values):
                matched.append(q)
    elif match_mode == "structure":
        # Structure match: check if query path components appear in order in any path
        paths = set()
        for p, v in walk(json_obj):
            paths.add(p)
        
        # Check each path for containing all query components in order
        for path in paths:
            path_parts = path.split('.')
            query_idx = 0
            path_idx = 0
            
            # Match each query component to path components in order
            while query_idx < len(query) and path_idx < len(path_parts):
                if query[query_idx] in path_parts[path_idx]:
                    query_idx += 1
                path_idx += 1
            
            # If we matched all query components
            if query_idx == len(query):
                matched.append(path)
    else:
        raise ValueError(f"Unknown match_mode: {match_mode}")

    score = len(matched) / total if total else 0.0
    return {"matched_items": matched, "score": score, "mode": match_mode}

def extract_content_blocks_from_html(
    html_content: str, source_url: Optional[str] = None
) -> List[ContentBlock]:
    """
    Extracts code, json, and text blocks from HTML content.
    Returns a list of ContentBlock instances with metadata and (approximate) line numbers.
    """
    soup = BeautifulSoup(html_content, "html.parser")
    content_blocks: List[ContentBlock] = []

    # Track which lines each block starts/ends on (approximate: split by lines, search for block text)
    lines = html_content.splitlines()
    used_spans = set()

    # Extract <pre><code>...</code></pre> and <pre>...</pre>
    for pre in soup.find_all("pre"):
        code_tag = pre.find("code")
        if code_tag:
            code = code_tag.get_text()
            language = None
            classes = code_tag.get("class", [])
            if classes:
                language = classes[0]
            block_type = "pre>code"
        else:
            code = pre.get_text()
            language = None
            block_type = "pre"
        # Try to detect JSON
        parsed_json = None
        block_type_final = "json" if _is_json_like(code) else "code"
        if block_type_final == "json":
            try:
                parsed_json = json.loads(code)
            except Exception:
                parsed_json = None
        # Approximate line numbers
        start_line, end_line = _find_block_lines(code, lines, used_spans)
        content_blocks.append(
            ContentBlock(
                type="json" if parsed_json else "code",
                content=code,
                language=language if not parsed_json else "json",
                block_type=block_type,
                start_line=start_line,
                end_line=end_line,
                source_url=source_url,
                metadata={"parsed_json": parsed_json} if parsed_json else None,
            )
        )

    # Standalone <code> (not inside <pre>)
    for code_tag in soup.find_all("code"):
        if code_tag.find_parent("pre"):
            continue  # Already handled
        code = code_tag.get_text()
        language = None
        classes = code_tag.get("class", [])
        if classes:
            language = classes[0]
        block_type = "code"
        parsed_json = None
        block_type_final = "json" if _is_json_like(code) else "code"
        if block_type_final == "json":
            try:
                parsed_json = json.loads(code)
            except Exception:
                parsed_json = None
        start_line, end_line = _find_block_lines(code, lines, used_spans)
        content_blocks.append(
            ContentBlock(
                type="json" if parsed_json else "code",
                content=code,
                language=language if not parsed_json else "json",
                block_type=block_type,
                start_line=start_line,
                end_line=end_line,
                source_url=source_url,
                metadata={"parsed_json": parsed_json} if parsed_json else None,
            )
        )

    # Extract <p> blocks as text blocks (robust paragraph extraction)
    for p in soup.find_all("p"):
        text = p.get_text(separator=" ", strip=True)
        if text:
            start_line, end_line = _find_block_lines(text, lines, used_spans)
            content_blocks.append(
                ContentBlock(
                    type="text",
                    content=text,
                    block_type="text",
                    start_line=start_line,
                    end_line=end_line,
                    source_url=source_url,
                )
            )
    # Optionally, extract other visible text nodes (excluding code/pre/script/style) as fallback
    # for elem in soup.find_all(text=True):
    #     parent = elem.parent
    #     if parent.name in ["pre", "code", "script", "style", "p"]:
    #         continue
    #     text = elem.strip()
    #     if text:
    #         start_line, end_line = _find_block_lines(text, lines, used_spans)
    #         content_blocks.append(
    #             ContentBlock(
    #                 type="text",
    #                 content=text,
    #                 block_type="text",
    #                 start_line=start_line,
    #                 end_line=end_line,
    #                 source_url=source_url,
    #             )
    #         )

    return content_blocks

def extract_content_blocks_from_markdown(
    md_content: str, source_url: Optional[str] = None
) -> List[ContentBlock]:
    """
    Extracts code, json, and text blocks from Markdown content (fenced and indented).
    Returns a list of ContentBlock instances with metadata and line numbers.
    """
    content_blocks: List[ContentBlock] = []
    lines = md_content.splitlines()
    used_spans = set()

    # Fenced code blocks: ```lang\ncode\n```
    for match in re.finditer(r"^```(\w+)?\n(.*?)(?<=\n)```", md_content, re.DOTALL | re.MULTILINE):
        lang = match.group(1)
        code = match.group(2)
        block_type = "fenced"
        parsed_json = None
        block_type_final = "json" if lang and lang.lower() == "json" and _is_json_like(code) else "code"
        if block_type_final == "json":
            try:
                parsed_json = json.loads(code)
            except Exception:
                parsed_json = None
        # Line numbers
        start_line, end_line = _find_block_lines(code, lines, used_spans)
        content_blocks.append(
            ContentBlock(
                type="json" if parsed_json else "code",
                content=code,
                language=lang if not parsed_json else "json",
                block_type=block_type,
                start_line=start_line,
                end_line=end_line,
                source_url=source_url,
                metadata={"parsed_json": parsed_json} if parsed_json else None,
            )
        )

    # Indented code blocks (4 spaces or tab)
    for match in re.finditer(r"(?:^|\n)((?:    |\t).+(\n(?:    |\t).+)*)", md_content):
        block = match.group(1)
        code = "\n".join([line[4:] if line.startswith("    ") else line.lstrip("\t") for line in block.splitlines()])
        block_type = "indented"
        parsed_json = None
        block_type_final = "json" if _is_json_like(code) else "code"
        if block_type_final == "json":
            try:
                parsed_json = json.loads(code)
            except Exception:
                parsed_json = None
        start_line, end_line = _find_block_lines(code, lines, used_spans)
        content_blocks.append(
            ContentBlock(
                type="json" if parsed_json else "code",
                content=code,
                language="json" if parsed_json else None,
                block_type=block_type,
                start_line=start_line,
                end_line=end_line,
                source_url=source_url,
                metadata={"parsed_json": parsed_json} if parsed_json else None,
            )
        )

    # Extract text blocks (non-code)
    # Remove all code blocks from text for text extraction
    text_content = md_content
    text_content = re.sub(r"^```(\w+)?\n(.*?)(?<=\n)```", "", text_content, flags=re.DOTALL | re.MULTILINE)
    text_content = re.sub(r"(?:^|\n)((?:    |\t).+(\n(?:    |\t).+)*)", "", text_content)
    for para in re.split(r"\n\s*\n", text_content):
        text = para.strip()
        if text:
            start_line, end_line = _find_block_lines(text, lines, used_spans)
            content_blocks.append(
                ContentBlock(
                    type="text",
                    content=text,
                    block_type="text",
                    start_line=start_line,
                    end_line=end_line,
                    source_url=source_url,
                )
            )

    return content_blocks

def _is_json_like(text: str) -> bool:
    """Heuristic: is this block likely to be JSON?"""
    text = text.strip()
    if (text.startswith("{") and text.endswith("}")) or (text.startswith("[") and text.endswith("]")):
        try:
            json.loads(text)
            return True
        except Exception:
            return False
    return False

def detect_arangosearch_json_example(content_block, nearby_texts=None):
    """
    Heuristically determine if a JSON code block is an ArangoSearch link properties or analyzer example.
    Args:
        content_block: ContentBlock or dict with at least 'content', 'language', and 'metadata' (with 'parsed_json').
        nearby_texts: Optional list of nearby text (headings, paragraphs) for context.
    Returns:
        (is_arangosearch: bool, example_type: str|None)
    """
    # Only consider JSON blocks
    if not content_block:
        return False, None
    lang = getattr(content_block, 'language', None) or content_block.get('language')
    if lang and 'json' not in str(lang).lower():
        return False, None
    parsed = None
    if hasattr(content_block, 'metadata') and content_block.metadata:
        parsed = content_block.metadata.get('parsed_json')
    elif isinstance(content_block, dict) and content_block.get('parsed_json'):
        parsed = content_block['parsed_json']
    elif isinstance(content_block, dict) and content_block.get('metadata') and 'parsed_json' in content_block['metadata']:
        parsed = content_block['metadata']['parsed_json']
    if not parsed or not isinstance(parsed, dict):
        return False, None
    # Heuristic 1: Key-based detection
    keys = set(parsed.keys())
    # Link properties: look for 'links', 'fields', 'analyzers', 'includeAllFields', 'storeValues', 'trackListPositions', 'type' == 'arangosearch'
    if 'links' in keys or 'fields' in keys or 'analyzers' in keys or 'includeAllFields' in keys or 'storeValues' in keys or 'trackListPositions' in keys:
        if parsed.get('type', '').lower() == 'arangosearch' or 'links' in keys or 'analyzers' in keys:
            return True, 'link_properties' if 'links' in keys or 'fields' in keys else 'analyzer'
    # Heuristic 2: Contextual detection from nearby text
    if nearby_texts:
        joined = ' '.join(nearby_texts).lower()
        if 'arangosearch' in joined:
            if 'analyzer' in joined:
                return True, 'analyzer'
            if 'link' in joined or 'property' in joined:
                return True, 'link_properties'
    # Heuristic 3: Look for nested 'analyzers' or 'links' in the structure
    def walk(obj):
        if isinstance(obj, dict):
            for k, v in obj.items():
                yield k, v
                yield from walk(v)
        elif isinstance(obj, list):
            for v in obj:
                yield from walk(v)
    found_analyzers = False
    found_links = False
    for k, v in walk(parsed):
        if k == 'analyzers':
            found_analyzers = True
        if k == 'links':
            found_links = True
    if found_analyzers:
        return True, 'analyzer'
    if found_links:
        return True, 'link_properties'
    return False, None


def _find_block_lines(block: str, lines: List[str], used_spans: set) -> (Optional[int], Optional[int]):
    """
    Approximate the start and end line numbers of a block within the source lines.
    Avoids overlapping with previously used spans.
    """
    block_lines = block.strip().splitlines()
    if not block_lines:
        return None, None
    for i in range(len(lines) - len(block_lines) + 1):
        if (i, i + len(block_lines) - 1) in used_spans:
            continue
        window = lines[i : i + len(block_lines)]
        if [l.strip() for l in window] == [l.strip() for l in block_lines]:
            used_spans.add((i, i + len(block_lines) - 1))
            return i + 1, i + len(block_lines)
    return None, None

# Inside src/mcp_doc_retriever/utils.py


def url_to_local_path(base_dir: str, url: str) -> str:
    """Generate local file path from URL in mirrored structure:
    base_dir/{hostname}/{path}/[index.html|filename]

    Assumes base_dir is the root where the hostname directory should be created (e.g., /app/downloads/content).
    """
    try:
        parsed = urlparse(canonicalize_url(url))
        if not parsed.netloc:
            raise ValueError("URL must have a valid hostname (netloc).")

        safe_hostname = re.sub(r"[^a-zA-Z0-9\.\-]", "_", parsed.netloc)
        safe_hostname = safe_hostname.replace(":", "_")

        path_segment = parsed.path.lstrip("/")

        if not path_segment or path_segment.endswith("/"):
            filename = "index.html"
            dir_path = os.path.dirname(path_segment)
        else:
            filename = os.path.basename(path_segment)
            dir_path = os.path.dirname(path_segment)
            if not filename:
                filename = "index.html"

        safe_dir_parts = []
        for part in dir_path.split(os.sep):
            safe_part = re.sub(r'[<>:"/\\|?*]', "_", part)
            if safe_part or not safe_dir_parts:
                safe_dir_parts.append(safe_part)
        safe_dir_path = os.path.join(*safe_dir_parts)

        safe_filename = re.sub(r'[<>:"/\\|?*]', "_", filename)
        max_filename_len = 200
        if len(safe_filename) > max_filename_len:
            name, ext = os.path.splitext(safe_filename)
            safe_filename = name[: max_filename_len - len(ext) - 1] + ext

        # *** CORRECTED PATH CONSTRUCTION ***
        # Construct path relative to base_dir, NO extra 'content' prepended here.
        relative_path = os.path.join(safe_hostname, safe_dir_path, safe_filename)
        full_path = os.path.join(
            base_dir, relative_path
        )  # Join base_dir with hostname/path/file structure

        norm_path = os.path.normpath(full_path)

        # Validation against base_dir
        abs_base_dir = os.path.abspath(base_dir)
        abs_norm_path = os.path.abspath(norm_path)
        # Check against base_dir directly now
        if (
            not abs_norm_path.startswith(abs_base_dir + os.sep)
            and abs_norm_path != abs_base_dir
        ):
            raise ValueError(
                f"Constructed path '{norm_path}' escapes base directory '{base_dir}' for URL '{url}'"
            )

        max_total_path = 400
        if len(norm_path) > max_total_path:
            raise ValueError(
                f"Constructed path exceeds maximum length ({max_total_path} chars): '{norm_path}'"
            )

        return norm_path

    except ValueError as e:
        raise ValueError(f"Could not generate local path for URL: {url} - {e}") from e
    except Exception as e:
        raise RuntimeError(f"Error generating local path for URL: {url} - {e}") from e

def is_url_private_or_internal(url: str) -> bool:
    """
    Determines if a URL resolves to an internal/private/loopback/reserved address or suspicious hostname.
    Blocks SSRF vectors by checking:
      - Hostnames like 'localhost', ending with '.local', '.internal', etc.
      - Any resolved IP (IPv4/IPv6) in private, loopback, link-local, or reserved ranges.
      - Allows test hostnames (e.g., host.docker.internal) if config.ALLOW_TEST_INTERNAL_URLS is True.

    Args:
        url: The URL to check (must include scheme).
    Returns:
        True if the URL is internal/private, False if external.

    Security rationale:
        Prevents SSRF by blocking requests to internal resources (cloud metadata, local network, etc).
        Allows test infrastructure when ALLOW_TEST_INTERNAL_URLS is enabled.

    Sample input/output:
        >>> is_url_private_or_internal('http://localhost:8000')
        True
        >>> is_url_private_or_internal('http://127.0.0.1')
        True
        >>> is_url_private_or_internal('http://10.0.0.5')
        True
        >>> is_url_private_or_internal('http://192.168.1.1')
        True
        >>> is_url_private_or_internal('http://example.com')
        False
        >>> is_url_private_or_internal('http://my-internal-service.local')
        True
        >>> is_url_private_or_internal('http://host.docker.internal')  # Allowed if config.ALLOW_TEST_INTERNAL_URLS
        False

    """
    try:
        parsed = urlparse(url)
        hostname = parsed.hostname
        # DEBUG: Log SSRF check details
        print(f"[SSRF DEBUG] ALLOW_TEST_INTERNAL_URLS={getattr(config, 'ALLOW_TEST_INTERNAL_URLS', False)}, hostname checked: {hostname}")
        if not hostname:
            return True  # Block if no hostname

        # Allow test hostnames if override is enabled
        if getattr(config, "ALLOW_TEST_INTERNAL_URLS", False):
            # Allow any hostname that matches or ends with 'host.docker.internal' (for Docker test infra)
            base_host = hostname.lower().split(':')[0]
            # Allow any hostname containing 'host.docker.internal' or 'testserver'
            if (
                "host.docker.internal" in base_host
                or "testserver" in base_host
                or base_host == "host.docker"
                or base_host == "localhost"
                or base_host == "127.0.0.1"
            ):
                # If test override allows this hostname, allow without further IP checks
                return False

        # Block obvious internal hostnames
        lowered = hostname.lower()
        if (
            lowered == 'localhost' or
            lowered.endswith('.localhost') or
            lowered.endswith('.local') or
            lowered.endswith('.internal') or
            lowered.endswith('.test') or
            lowered.endswith('.example')
        ):
            return True
        # Block IPv4/IPv6 loopback, private, reserved, link-local
        try:
            # gethostbyname_ex returns (hostname, aliaslist, ipaddrlist)
            ip_addrs = []
            try:
                ip_addrs = socket.gethostbyname_ex(hostname)[2]
            except Exception:
                # Try IPv6
                try:
                    infos = socket.getaddrinfo(hostname, None)
                    ip_addrs = [info[4][0] for info in infos]
                except Exception:
                    return True  # Block if cannot resolve
            for ip in ip_addrs:
                try:
                    ip_obj = ipaddress.ip_address(ip)
                    # Allow test IPs if override is enabled (e.g., 172.17.0.1 for Docker bridge)
                    if getattr(config, "ALLOW_TEST_INTERNAL_URLS", False):
                        allowed_test_ips = {"172.17.0.1"}
                        if ip in allowed_test_ips:
                            continue
                    if (
                        ip_obj.is_private or
                        ip_obj.is_loopback or
                        ip_obj.is_link_local or
                        ip_obj.is_reserved or
                        ip_obj.is_multicast or
                        ip_obj.is_unspecified
                    ):
                        return True
                except Exception:
                    return True  # Block if cannot parse IP
        except Exception:
            return True  # Block on DNS errors
        return False
    except Exception:
        return True  # Block on any parsing error

# --- Example Usage ---
if __name__ == "__main__":
    import re  # Need re for url_to_local_path changes above

    print("--- Utility Function Examples ---")

    urls_to_test = [
        "http://example.com",
        "http://example.com/",
        "https://example.com:443/path/to/page.html?query=1#fragment",
        "HTTP://Example.com/Another_Path/",
        "http://example.com:8080/ différent /path.aspx",  # Non-standard port, encoding needed
        "http://example.com/..%2f../etc/passwd",  # Path traversal attempt
        "ftp://example.com/resource",  # Different scheme
        "invalid-url",
    ]

    print("\nCanonicalization & Download ID:")
    for url in urls_to_test:
        try:
            canon = canonicalize_url(url)
            dl_id = generate_download_id(url)
            print(f"'{url}' -> Canon='{canon}', ID='{dl_id}'")
        except ValueError as e:
            print(f"'{url}' -> ERROR: {e}")

    print("\nURL to Local Path (Base Dir: '/tmp/downloads_test'):")
    base = "/tmp/downloads_test"
    # Ensure content dir exists for realistic path generation examples
    try:
        os.makedirs(os.path.join(base, "content"), exist_ok=True)
    except Exception:
        pass

    for url in urls_to_test:
        # Filter out invalid ones handled above
        if "ERROR" in locals().get("canon", "") and url in locals().get("url", ""):
            continue
        try:
            local_path = url_to_local_path(base, url)
            print(f"'{url}' -> Local Path='{local_path}'")
        except (ValueError, RuntimeError) as e:
            print(f"'{url}' -> ERROR generating path: {e}")

    print("\n--- End Examples ---")
