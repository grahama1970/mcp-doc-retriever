"""
HTML Content Extractor Module (Pandoc Pipeline with Selector-Based Extraction).

This module fetches HTML (using playwright_fetch), extracts specific sections
via CSS selectors, converts them to Markdown using Pandoc, and then uses
markdown_extractor to get structured code/description pairs.

Dependencies:
- beautifulsoup4>=4.12.0
- requests>=2.31.0
- loguru>=0.7.0
- lxml
- playwright (via playwright_fetch.py)
- pandoc (External binary, must be installed)
- markdown_extractor.py (Local module)
"""

import os
import subprocess
import tempfile
import json
from pathlib import Path
from typing import Dict, List, Optional, Union, Any
from bs4 import BeautifulSoup, Tag
from loguru import logger

# Use absolute imports
from mcp_doc_retriever.context7.playwright_fetch import get_html_content
from mcp_doc_retriever.context7.markdown_extractor import extract_from_markdown


class HTMLExtractorError(Exception):
    """Base exception for HTMLExtractor errors."""

    pass


class HTMLParsingError(HTMLExtractorError):
    """Raised when HTML parsing fails."""

    pass


class PandocError(HTMLExtractorError):
    """Raised when Pandoc conversion fails."""

    pass


class HTMLExtractor:
    """
    Extracts structured data from HTML using CSS selectors, followed by Pandoc
    and MarkdownExtractor.
    """

    def __init__(
        self,
        content_selectors: Optional[List[str]] = None,
    ):
        """
        Initialize the HTML extractor.

        Args:
            content_selectors: List of CSS selectors to use.
                             Defaults to ['article.default', 'div[class*="highlight"]'].
        """
        self.content_selectors = content_selectors or [
            "article.default",
            'div[class*="highlight"]',
        ]
        logger.info(
            f"Initialized HTMLExtractor with content_selectors: {self.content_selectors}"
        )

    def _html_to_markdown_pandoc(self, html_snippet: str) -> str:
        """Converts an HTML snippet to Markdown using Pandoc."""
        try:
            soup = BeautifulSoup(html_snippet, "lxml")

            # Process code blocks
            for highlight in soup.find_all(
                "div", class_=lambda x: x and "highlight" in x
            ):
                code = highlight.find("code")
                if code:
                    lang = "text"
                    if code.get("class"):
                        for cls in code["class"]:
                            if cls.startswith("language-"):
                                lang = cls.replace("language-", "")
                                break

                    code_lines = []
                    for line in code.find_all("span", class_="line"):
                        text = "".join(
                            span.get_text() for span in line.find_all("span")
                        )
                        code_lines.append(text)
                    if not code_lines:
                        code_lines = [code.get_text()]

                    code_content = "\n".join(code_lines)
                    new_pre = soup.new_tag("pre")
                    new_code = soup.new_tag("code", **{"class": f"language-{lang}"})
                    new_code.string = code_content
                    new_pre.append(new_code)
                    highlight.replace_with(new_pre)

            # Preserve header hierarchy with section numbers
            section_counts = [0] * 6  # Track counts for h1-h6
            headers_found = False
            for level in range(1, 7):
                headers = soup.find_all(f"h{level}")
                logger.debug(f"Found {len(headers)} h{level} headers in snippet")
                for header in headers:
                    text = header.get_text().strip()
                    if not text:
                        text = "Unnamed Section"
                        logger.debug(f"Empty h{level} header, using fallback: {text}")
                    headers_found = True

                    # Reset counts for deeper levels
                    for j in range(level, len(section_counts)):
                        section_counts[j] = 0
                    # Increment count for current level
                    section_counts[level - 1] += 1
                    # Build section number
                    section_number_parts = [
                        str(section_counts[j])
                        for j in range(level)
                        if section_counts[j] > 0
                    ]
                    section_number = (
                        ".".join(section_number_parts)
                        if section_number_parts
                        else str(level)
                    )
                    # Prepend section number to header text
                    header.replace_with(f"{'#' * level} {section_number} {text}\n\n")
                    logger.debug(
                        f"Converted h{level} '{text}' to Markdown: {'#' * level} {section_number} {text}"
                    )

            # Add fallback heading if no headers were found
            if not headers_found:
                logger.debug(
                    "No headers found in HTML snippet, adding fallback heading"
                )
                html_snippet = f"<h2>Unnamed Section</h2>\n{html_snippet}"
                soup = BeautifulSoup(html_snippet, "lxml")
                section_counts[1] = 1  # h2 level
                soup.h2.replace_with("## 1.1 Unnamed Section\n\n")
                logger.debug("Added fallback Markdown heading: ## 1.1 Unnamed Section")

            # Convert cleaned HTML using Pandoc
            logger.debug(f"Pandoc input HTML (first 500 chars):\n{str(soup)[:500]}...")
            process = subprocess.run(
                [
                    "pandoc",
                    "--from=html",
                    "--to=gfm",
                    "--wrap=none",
                    "--strip-comments",
                    "--no-highlight",
                ],
                input=str(soup),
                capture_output=True,
                text=True,
                encoding="utf-8",
                check=True,
            )
            markdown_output = process.stdout
            if not any(line.startswith("#") for line in markdown_output.splitlines()):
                logger.warning(
                    "Pandoc output contains no headings, section_path may be empty"
                )
            logger.debug(
                f"Pandoc conversion successful. Output (first 500 chars):\n{markdown_output[:500]}..."
            )
            return markdown_output

        except FileNotFoundError:
            logger.error(
                "Pandoc not found. Please ensure Pandoc is installed and in the system PATH."
            )
            raise PandocError("Pandoc executable not found.")
        except subprocess.CalledProcessError as e:
            logger.error(f"Pandoc conversion failed. Stderr:\n{e.stderr}")
            raise PandocError(f"Pandoc conversion failed: {e.stderr}")
        except Exception as e:
            logger.error(f"Unexpected error during Pandoc conversion: {e}")
            raise PandocError(f"Unexpected Pandoc error: {e}")

    def extract_from_url(
        self,
        url: str,
        repo_link: Optional[str] = None,
        cache_dir: Optional[str] = None,
        ignore_robots: bool = False,
    ) -> Dict[str, List[Dict[str, Any]]]:
        """Extract structured data from a URL."""
        repo_link = repo_link or url
        try:
            cache_dir = cache_dir or "src/mcp_doc_retriever/context7/data"
            os.makedirs(cache_dir, exist_ok=True)
            safe_filename = "".join(c if c.isalnum() else "_" for c in url) + ".html"
            output_file = os.path.join(cache_dir, safe_filename)

            logger.info(f"Fetching content from {url} (ignore_robots={ignore_robots})")
            html_content = get_html_content(
                url, output_file, ignore_robots=ignore_robots
            )

            if not html_content:
                if not ignore_robots:
                    logger.warning(
                        f"Fetching disallowed by robots.txt for {url}. Cannot extract."
                    )
                    return {}
                else:
                    raise HTMLExtractorError(
                        f"Failed to fetch content from {url} even though robots.txt was ignored."
                    )

            source_identifier = output_file if os.path.exists(output_file) else url
            return self.extract_from_string(
                html_content, repo_link=repo_link, source_identifier=source_identifier
            )

        except Exception as e:
            logger.error(
                f"Error extracting from URL {url} ({type(e).__name__}): {str(e)}"
            )
            raise

    def extract_from_file(
        self, file_path: Union[str, Path], repo_link: str
    ) -> Dict[str, List[Dict[str, Any]]]:
        """Extract structured data from a local HTML file."""
        try:
            file_path_obj = Path(file_path)
            logger.debug(f"Reading file: {file_path_obj}")
            if not file_path_obj.exists():
                raise FileNotFoundError(f"File not found: {file_path_obj}")
            with open(file_path_obj, "r", encoding="utf-8") as f:
                html_content = f.read()
            if not html_content.strip():
                raise ValueError(f"Empty file: {file_path_obj}")
            return self.extract_from_string(
                html_content, repo_link=repo_link, source_identifier=str(file_path_obj)
            )
        except Exception as e:
            logger.error(f"Error extracting from file {file_path}: {str(e)}")
            raise

    def _process_html_snippet(
        self,
        html_snippet: str,
        repo_link: str,
        source_identifier: str,
        selector_info: str,
    ) -> List[Dict[str, Any]]:
        """Helper function to convert HTML snippet to MD and extract data."""
        extracted_data = []
        logger.debug(f"Converting HTML snippet ({selector_info}) to Markdown...")
        markdown_text = self._html_to_markdown_pandoc(html_snippet)

        if markdown_text.strip():
            with tempfile.NamedTemporaryFile(
                mode="w+", delete=False, suffix=".md", encoding="utf-8"
            ) as md_file:
                md_file.write(markdown_text)
                temp_md_path = md_file.name
            logger.debug(f"Saved temporary Markdown to: {temp_md_path}")
            logger.debug(
                f"Markdown content ({selector_info}, first 500 chars):\n{markdown_text[:500]}..."
            )
            try:
                extracted = extract_from_markdown(temp_md_path, repo_link)
                if extracted:
                    for item in extracted:
                        item["original_source"] = source_identifier
                        item["html_selector"] = selector_info
                    logger.info(
                        f"Extracted {len(extracted)} item(s) from Markdown snippet ({selector_info})"
                    )
                    extracted_data.extend(extracted)
                else:
                    logger.warning(
                        f"Markdown extractor returned no data for snippet ({selector_info})"
                    )
            finally:
                os.unlink(temp_md_path)
        else:
            logger.warning(
                f"Pandoc conversion resulted in empty Markdown for snippet ({selector_info})"
            )
        return extracted_data

    def _build_element_snippet(self, element: Tag, headers: List[Tag]) -> str:
        """
        Build an HTML snippet including all <h1>-<h6> headers that appear before
        this element in the original document order, followed by the element itself.
        """
        # 1) Find the true root of this element
        root = element
        while root.parent and isinstance(root.parent, Tag):
            root = root.parent

        # 2) Proof: list every header in the document
        all_headers = root.find_all(["h1","h2","h3","h4","h5","h6"])
        logger.debug(f"[proof] total headers: {len(all_headers)} -> " +
                    "; ".join(f"<{h.name}>{h.get_text(strip=True)}" for h in all_headers))

        # 3) Get all tags in document order
        all_tags = root.find_all(True)
        logger.debug(f"[proof] total tags in doc: {len(all_tags)}")

        # 4) Find index of our element
        try:
            elem_idx = all_tags.index(element)
        except ValueError:
            logger.warning("[proof] element not found in tag list; using full headers anyway")
            elem_idx = len(all_tags)

        # 5) Collect headers whose index < elem_idx
        relevant = []
        for hdr in all_headers:
            try:
                idx = all_tags.index(hdr)
                if idx < elem_idx:
                    relevant.append((idx, hdr))
            except ValueError:
                continue

        # 6) Sort them into document order
        relevant.sort(key=lambda x: x[0])
        logger.debug(f"[proof] relevant headers count: {len(relevant)}")

        # 7) Build a fresh snippet
        new_soup = BeautifulSoup("<html><head></head><body></body></html>", "lxml")
        body = new_soup.body

        # 8) Clone & append each header, then the element
        for _, hdr in relevant:
            body.append(BeautifulSoup(str(hdr), "lxml").find(hdr.name))
        body.append(BeautifulSoup(str(element), "lxml").find(True))

        return str(new_soup)
    
    def extract_from_string(
        self, html_content: str, repo_link: str, source_identifier: str
    ) -> Dict[str, List[Dict[str, Any]]]:
        """Extracts structured data from an HTML string."""
        try:
            if not html_content.strip():
                raise ValueError("Empty HTML content provided")

            all_extracted_data: Dict[str, List[Dict[str, Any]]] = {}
            soup = None

            # Parse HTML
            parsers = ["lxml", "html.parser"]
            for parser in parsers:
                try:
                    soup = BeautifulSoup(html_content, parser)
                    logger.debug(f"Successfully parsed HTML using '{parser}' parser.")
                    break
                except ImportError:
                    logger.warning(f"Parser '{parser}' not found. Trying next parser.")
                    continue
                except Exception as e:
                    logger.warning(
                        f"Failed to parse HTML with '{parser}': {e}. Trying next parser."
                    )

            if soup is None:
                logger.error("HTML parsing failed with all available parsers.")
                raise HTMLParsingError("Failed to parse HTML with available parsers.")

            # Collect all headers upfront (so we know their sourcelines)
            headers = soup.find_all(["h1", "h2", "h3", "h4", "h5", "h6"])
            logger.info(f"Found {len(headers)} headers in document")
            for header in headers:
                text = header.get_text().strip() or "Unnamed Section"
                logger.debug(
                    f"Header {header.name}: '{text}' at line {getattr(header, 'sourceline', 'unknown')}"
                )

            # Process elements using selectors
            for selector in self.content_selectors:
                logger.debug(f"Processing selector: {selector}")
                elements = soup.select(selector)
                selector_data = []

                if elements:
                    logger.info(
                        f"Found {len(elements)} element(s) for selector '{selector}'"
                    )
                    for i, element in enumerate(elements):
                        if isinstance(element, Tag):
                            snippet = self._build_element_snippet(element, headers)
                            logger.debug(
                                f"Processing element {i + 1} for selector '{selector}' with snippet (first 500 chars):\n{snippet[:500]}..."
                            )
                            snippet_data = self._process_html_snippet(
                                snippet,
                                repo_link,
                                source_identifier,
                                f"selector: {selector} [{i + 1}]",
                            )
                            selector_data.extend(snippet_data)
                        else:
                            logger.warning(
                                f"Selected element {i + 1} for selector '{selector}' is not a Tag, skipping."
                            )
                    if selector_data:
                        all_extracted_data[selector] = selector_data
                else:
                    logger.warning(f"No elements found for selector: {selector}")

            return all_extracted_data

        except (HTMLParsingError, PandocError):
            raise
        except Exception as e:
            logger.error(f"Unexpected error during extraction: {e}")
            raise


def main():
    import pyperclip
    """Run tests for HTMLExtractor functionality."""
    url = "https://docs.arangodb.com/3.12/aql/fundamentals/subqueries/"
    repo_link = url

    # Test: Selector Mode
    logger.info("\n--- Running Test: Selector Mode ---")
    try:
        extractor = HTMLExtractor(
            content_selectors=["article.default", 'div[class*="highlight"]']
        )
        logger.info(f"Testing extraction from URL: {url} (Ignoring robots.txt)")
        result = extractor.extract_from_url(
            url, repo_link=repo_link, ignore_robots=True
        )

        if not result:
            logger.warning("Extraction returned no results.")
        else:
            logger.info(f"Extraction Result (JSON):\n{json.dumps(result, indent=2)}")
        logger.info("--- Test Completed ---")

    except Exception as e:
        logger.error(f"Test failed: {str(e)}", exc_info=True)


if __name__ == "__main__":
    main()
