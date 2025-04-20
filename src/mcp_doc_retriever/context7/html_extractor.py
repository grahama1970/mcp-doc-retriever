"""
HTML Content Extractor Module (Selector-Based Extraction with markdownify).

This module fetches HTML using playwright_fetch, extracts specific sections
via CSS selectors, builds minimal HTML snippets with context headers, converts
them to Markdown using markdownify, and then uses markdown_extractor to
get structured code/description pairs.

Dependencies:
- beautifulsoup4>=4.12.0
- requests>=2.31.0
- loguru>=0.7.0
- lxml
- playwright (via playwright_fetch.py)
- markdownify>=0.9.2
- markdown_extractor.py (Local module)
"""

import os
import tempfile
import json
from pathlib import Path
from typing import Dict, List, Optional, Union, Any, Callable
import requests
from bs4 import BeautifulSoup, Tag
from markdownify import markdownify as md
from loguru import logger

# Use absolute imports for local modules
from mcp_doc_retriever.context7.playwright_fetch import get_html_content
from mcp_doc_retriever.context7.markdown_extractor import extract_from_markdown


class HTMLExtractorError(Exception):
    """Base exception for HTMLExtractor errors."""

    pass


class HTMLParsingError(HTMLExtractorError):
    """Raised when HTML parsing fails."""

    pass


class HTMLExtractor:
    """
    Extracts structured data from HTML using CSS selectors,
    context header extraction, markdownify, and MarkdownExtractor.
    """

    def __init__(
        self,
        content_selectors: Optional[List[str]] = None,
        fetcher: Callable[[str, str, bool], str] = get_html_content,
    ):
        """
        Initialize the HTML extractor.

        Args:
            content_selectors: List of CSS selectors to use.
            fetcher: Function to fetch HTML (url, output_path, ignore_robots) -> html string.
        """
        self.content_selectors = content_selectors or [
            "article.default",
            'div[class*="highlight"]',
        ]
        self.fetcher = fetcher
        # Reusable HTTP session
        self.session = requests.Session()
        logger.info(
            f"Initialized HTMLExtractor with selectors: {self.content_selectors}"
        )

    def _html_to_markdown(self, html_snippet: str) -> str:
        """Converts an HTML snippet to Markdown using markdownify."""
        # strip script/style tags
        return md(html_snippet, strip=["script", "style"])

    def _build_element_snippet(self, element: Tag) -> str:
        """
        Build an HTML snippet including all preceding <h1>-<h6> headers
        (in document order) and the element itself.
        """
        # Find document root
        root: Tag = element
        while root.parent and isinstance(root.parent, Tag):
            root = root.parent

        # Extract preceding headers
        preceding = element.find_all_previous(["h1", "h2", "h3", "h4", "h5", "h6"])
        preceding.reverse()  # document order
        logger.debug(
            f"Context headers for element <{element.name}>: "
            + ", ".join(h.get_text(strip=True) for h in preceding)
        )

        # Assemble minimal snippet
        soup = BeautifulSoup("<html><head></head><body></body></html>", "lxml")
        body = soup.body
        for hdr in preceding:
            body.append(BeautifulSoup(str(hdr), "lxml").find(hdr.name))
        body.append(BeautifulSoup(str(element), "lxml").find(True))
        return str(soup)

    def _process_html_snippet(
        self,
        html_snippet: str,
        repo_link: str,
        source_identifier: str,
        selector_info: str,
    ) -> List[Dict[str, Any]]:
        """Convert HTML snippet to Markdown and extract structured data."""
        extracted_data: List[Dict[str, Any]] = []
        markdown_text = self._html_to_markdown(html_snippet)
        if markdown_text.strip():
            # Write to temp .md for extractor
            with tempfile.NamedTemporaryFile(
                mode="w+", delete=False, suffix=".md", encoding="utf-8"
            ) as md_file:
                md_file.write(markdown_text)
                temp_md_path = md_file.name
            try:
                items = extract_from_markdown(temp_md_path, repo_link) or []
                for item in items:
                    item["original_source"] = source_identifier
                    item["html_selector"] = selector_info
                extracted_data.extend(items)
            finally:
                os.unlink(temp_md_path)
        else:
            logger.warning(f"Empty Markdown for snippet ({selector_info})")
        return extracted_data

    def extract_from_string(
        self, html_content: str, repo_link: str, source_identifier: str
    ) -> Dict[str, List[Dict[str, Any]]]:
        """Extract structured data from raw HTML string."""
        # Parse HTML
        soup = None
        for parser in ("lxml", "html.parser"):
            try:
                soup = BeautifulSoup(html_content, parser)
                logger.debug(f"Parsed HTML with {parser}")
                break
            except Exception:
                continue
        if soup is None:
            raise HTMLParsingError("Failed to parse HTML content.")

        # Log headers
        headers = soup.find_all(["h1", "h2", "h3", "h4", "h5", "h6"])
        logger.info(f"Found {len(headers)} headers in document.")

        results: Dict[str, List[Dict[str, Any]]] = {}
        for selector in self.content_selectors:
            elements = soup.select(selector)
            logger.info(f"Selector '{selector}' matched {len(elements)} elements")
            snippets: List[Dict[str, Any]] = []
            for idx, element in enumerate(elements, start=1):
                snippet_html = self._build_element_snippet(element)
                snippet_data = self._process_html_snippet(
                    snippet_html,
                    repo_link,
                    source_identifier,
                    f"selector: {selector} [{idx}]",
                )
                snippets.extend(snippet_data)
            if snippets:
                results[selector] = snippets
        return results

    def extract_from_url(
        self,
        url: str,
        repo_link: Optional[str] = None,
        cache_dir: Optional[str] = None,
        ignore_robots: bool = False,
    ) -> Dict[str, List[Dict[str, Any]]]:
        """Extract structured data from a URL."""
        repo_link = repo_link or url
        cache_dir = cache_dir or "src/mcp_doc_retriever/context7/data"
        os.makedirs(cache_dir, exist_ok=True)
        safe_name = "".join(c if c.isalnum() else "_" for c in url) + ".html"
        out_file = os.path.join(cache_dir, safe_name)

        logger.info(f"Fetching URL {url} (ignore_robots={ignore_robots})")
        html = self.fetcher(url, out_file, ignore_robots)
        if not html:
            if not ignore_robots:
                return {}
            raise HTMLExtractorError(
                f"Failed to fetch {url} despite ignoring robots.txt"
            )
        return self.extract_from_string(html, repo_link, out_file)

    def extract_from_file(
        self, file_path: Union[str, Path], repo_link: str
    ) -> Dict[str, List[Dict[str, Any]]]:
        """Extract structured data from a local HTML file."""
        path = Path(file_path)
        if not path.exists():
            raise FileNotFoundError(f"File not found: {path}")
        html = path.read_text(encoding="utf-8")
        if not html.strip():
            raise HTMLExtractorError(f"Empty file: {path}")
        return self.extract_from_string(html, repo_link, str(path))


def main():
    url = "https://docs.arangodb.com/3.12/aql/fundamentals/subqueries/"
    extractor = HTMLExtractor(
        content_selectors=["article.default", 'div[class*="highlight"]']
    )
    result = extractor.extract_from_url(url, ignore_robots=True)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
