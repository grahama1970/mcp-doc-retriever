"""
Markdown extraction and processing module for PDF extraction workflow.

This module extracts structured content from Markdown files, including:
- Text blocks with section hierarchy tracking
- Tables with parsing and validation
- Images with metadata
- Code blocks with language detection and metadata

It integrates with the Marker PDF extraction pipeline by supporting
marker_json format for enhanced table and image extraction from PDFs.

Third-party package documentation:
- markdown-it-py: https://github.com/executablebooks/markdown-it-py
- tiktoken: https://github.com/openai/tiktoken
- ftfy: https://github.com/rspeer/ftfy

Example usage:
    >>> from mcp_doc_retriever.context7.pdf_extractor.markdown_extractor import extract_from_markdown
    >>> markdown_file = "example.md"
    >>> repo_link = "https://github.com/example/repo"
    >>> extracted_data = extract_from_markdown(markdown_file, repo_link)
    >>> for item in extracted_data:
    ...     print(f"Type: {item['type']}, Token count: {item['token_count']}")
"""

import os
import sys
import re
import json
import datetime
import unicodedata
from pathlib import Path
from typing import List, Dict, Tuple, Optional, Any, cast
from loguru import logger

# Import dependencies 
try:
    from markdown_it import MarkdownIt
except ImportError:
    logger.warning("markdown-it-py not found. Install with: uv add markdown-it-py")
    MarkdownIt = None

try:
    import tiktoken
except ImportError:
    logger.warning("tiktoken not found. Install with: uv add tiktoken")
    tiktoken = None

try:
    import ftfy
except ImportError:
    logger.warning("ftfy not found. Install with: uv add ftfy")
    ftfy = None

# Handle imports for both standalone and module usage
if __name__ == "__main__":
    # When run as a script, configure system path
    current_dir = Path(__file__).resolve().parent
    project_root = current_dir
    while not (project_root / 'pyproject.toml').exists() and project_root != project_root.parent:
        project_root = project_root.parent
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root / "src"))
    
    # Import after path setup for standalone usage
    try:
        from mcp_doc_retriever.context7.tree_sitter_utils import extract_code_metadata
        from mcp_doc_retriever.context7.text_chunker import SectionHierarchy, hash_string
        from mcp_doc_retriever.context7.pdf_extractor.utils import calculate_iou
    except ImportError:
        logger.warning("Could not import required modules for standalone execution")
        
        # Define minimal versions for standalone testing
        def extract_code_metadata(code: str, language: str) -> Dict[str, Any]:
            """Minimal version for standalone testing."""
            return {"language": language, "length": len(code)}
        
        def hash_string(text: str) -> str:
            """Minimal version for standalone testing."""
            import hashlib
            return hashlib.md5(text.encode()).hexdigest()
        
        class SectionHierarchy:
            """Minimal version for standalone testing."""
            def __init__(self):
                self.titles = []
                self.hashes = []
                
            def update(self, number: str, title: str, content: str) -> None:
                self.titles.append(f"{number}. {title}")
                self.hashes.append(hash_string(title))
                
            def get_titles(self) -> List[str]:
                return self.titles
                
            def get_hashes(self) -> List[str]:
                return self.hashes
        
        def calculate_iou(box1: List[float], box2: List[float]) -> float:
            """Minimal version for standalone testing."""
            return 0.5  # Always return 0.5 for testing
else:
    # When imported as a module, use relative imports
    try:
        from ..tree_sitter_utils import extract_code_metadata
        from ..text_chunker import SectionHierarchy, hash_string
        from .utils import calculate_iou
    except ImportError:
        # Fallback to absolute imports
        from mcp_doc_retriever.context7.tree_sitter_utils import extract_code_metadata
        from mcp_doc_retriever.context7.text_chunker import SectionHierarchy, hash_string
        from mcp_doc_retriever.context7.pdf_extractor.utils import calculate_iou


def clean_section_title(title: str, to_ascii: bool = False) -> str:
    """
    Cleans a section title by normalizing Unicode characters and removing unprintable characters.

    Args:
        title: The raw section title.
        to_ascii: If True, converts non-ASCII characters to ASCII equivalents.

    Returns:
        The cleaned section title.
    """
    try:
        cleaned = ftfy.fix_text(title, normalization="NFC")
        cleaned = "".join(c for c in cleaned if unicodedata.category(c)[0] != "C")
        if to_ascii:
            cleaned = (
                unicodedata.normalize("NFKD", cleaned)
                .encode("ascii", "ignore")
                .decode("ascii")
            )
        cleaned = cleaned.strip()
        return cleaned if cleaned else "Unnamed Section"
    except Exception as e:
        logger.warning(f"Error cleaning section title '{title}': {e}")
        return "Unnamed Section"


def parse_markdown_table(content: str) -> Optional[Dict[str, Any]]:
    """
    Parses a Markdown table into a dictionary with headers and rows.

    Args:
        content: The Markdown table content (e.g., "| Header1 | Header2 |\n|---|---|\n| Row1 | Row2 |").

    Returns:
        Dictionary with 'headers' (list) and 'rows' (list of lists), or None if parsing fails.
    """
    try:
        lines = [line.strip() for line in content.strip().split("\n") if line.strip()]
        if len(lines) < 2:
            return None

        headers = [h.strip() for h in lines[0].strip("|").split("|") if h.strip()]
        if not headers:
            return None

        separator = lines[1].strip("|").split("|")
        if not all(re.match(r"[-: ]+", s.strip()) for s in separator):
            return None

        rows = []
        for line in lines[2:]:
            cells = [
                c.strip() for c in line.strip("|").split("|") if c.strip() or c == ""
            ]
            if len(cells) == len(headers):
                rows.append(cells)

        return {"headers": headers, "rows": rows}
    except Exception as e:
        logger.warning(f"Error parsing Markdown table: {e}")
        return None


def parse_markdown_image(content: str) -> Optional[Dict[str, str]]:
    """
    Parses a Markdown image (e.g., ![alt](src)) into a dictionary with alt text and source.

    Args:
        content: The Markdown image content.

    Returns:
        Dictionary with 'alt' and 'src', or None if parsing fails.
    """
    try:
        match = re.match(r"!\[(.*?)\]\((.*?)\)", content.strip())
        if match:
            return {"alt": match.group(1).strip(), "src": match.group(2).strip()}
        return None
    except Exception as e:
        logger.warning(f"Error parsing Markdown image: {e}")
        return None


def extract_from_markdown(
    file_path: str,
    repo_link: str,
    marker_json: Optional[Dict[str, Any]] = None,
    table_cache: Optional[List[Dict[str, Any]]] = None,
) -> List[Dict[str, Any]]:
    """
    Extracts text, images, tables, and code blocks from a Markdown file as separate JSON objects,
    with section hierarchy tracking. Concatenates paragraphs and lists into text nodes, splitting
    on two or more line breaks. Cleans section titles with ftfy and parses code with tree-sitter.
    Integrates Marker's JSON for tables and images, with validation against table_cache.

    Args:
        file_path: Path to the Markdown file.
        repo_link: URL of the repository.
        marker_json: Optional Marker JSON output for tables and images.
        table_cache: Optional list of tables from table_extractor.py for validation.

    Returns:
        List of dictionaries containing extracted content (text, image, table, code).
    """
    section_hierarchy = SectionHierarchy()
    try:
        md = MarkdownIt("commonmark", {"html": False, "typographer": True})
        markdown_content = Path(file_path).read_text(encoding="utf-8")
        tokens = md.parse(markdown_content)
        logger.debug(f"Parsed Markdown file: {file_path}, {len(tokens)} tokens")

        extracted_data: List[Dict[str, Any]] = []
        text_content = []
        text_start_line = None
        last_line = 0
        image_content = ""
        table_content = ""
        code_block = None
        code_start_line = None
        table_start_line = None
        encoding = tiktoken.encoding_for_model("gpt-4")
        section_title = ""
        section_level = 0
        section_counts = [0] * 6
        current_page = 1

        # Extract tables and images from Marker JSON
        marker_tables = []
        marker_images = []
        if marker_json:
            for page in marker_json.get("pages", []):
                page_number = page.get("page_number", 1)
                for block in page.get("blocks", []):
                    if block.get("type") == "table":
                        marker_tables.append(
                            {
                                "page": page_number,
                                "bbox": block.get("bbox", [0, 0, 0, 0]),
                                "table_data": {
                                    "cells": [
                                        {
                                            "row": c["row"],
                                            "col": c["col"],
                                            "text": c["text"],
                                        }
                                        for c in block.get("cells", [])
                                    ]
                                },
                            }
                        )
                    elif block.get("type") == "image":
                        marker_images.append(
                            {
                                "page": page_number,
                                "bbox": block.get("bbox", [0, 0, 0, 0]),
                                "alt": block.get("alt", ""),
                                "src": block.get("src", ""),
                            }
                        )

        table_lines = []
        in_table = False

        def flush_text():
            nonlocal text_content, text_start_line, last_line
            if text_content:
                content = "\n".join(text_content).strip()
                if content:
                    section_titles = section_hierarchy.get_titles()
                    section_hash_path = section_hierarchy.get_hashes()
                    text_token_count = len(encoding.encode(content))
                    text_end_line = last_line
                    extracted_data.append(
                        {
                            "file_path": file_path,
                            "repo_link": repo_link,
                            "extraction_date": datetime.datetime.now().isoformat(),
                            "type": "text",
                            "content": content,
                            "line_span": (text_start_line, text_end_line),
                            "token_count": text_token_count,
                            "section_id": hash_string(content),
                            "section_path": section_titles,
                            "section_hash_path": section_hash_path,
                            "page": current_page,
                            "metadata": {},
                        }
                    )
                    logger.debug(
                        f"Added text at lines {text_start_line}-{text_end_line}, "
                        f"section_path: {section_titles}"
                    )
                text_content = []
                text_start_line = None

        for i, token in enumerate(tokens):
            # Track current line for line_span
            current_line = token.map[0] + 1 if token.map else last_line + 1

            # Check for double line breaks
            if token.map and token.map[0] > last_line + 1:
                flush_text()
                text_start_line = current_line

            if token.type == "heading_open":
                flush_text()
                section_level = int(token.tag[1:])
                section_title = ""
                logger.debug(f"Found heading level {section_level}")

            elif (
                token.type == "inline"
                and i > 0
                and tokens[i - 1].type == "heading_open"
            ):
                section_title = token.content.strip()
                logger.debug(f"Captured raw section title: {section_title}")

            elif token.type == "heading_close":
                cleaned_title = clean_section_title(section_title, to_ascii=True)
                section_number = ""
                number_match = re.match(r"(\d+(?:\.\d+)*\.?)\s*(.*)", section_title)
                if number_match:
                    section_number = number_match.group(1).rstrip(".")
                    cleaned_title = clean_section_title(
                        number_match.group(2) or "Unnamed Section", to_ascii=True
                    )
                else:
                    for j in range(section_level, len(section_counts)):
                        section_counts[j] = 0
                    section_counts[section_level - 1] += 1
                    section_number_parts = [
                        str(section_counts[j])
                        for j in range(section_level)
                        if section_counts[j] > 0
                    ]
                    section_number = (
                        ".".join(section_number_parts)
                        if section_number_parts
                        else str(section_level)
                    )

                section_hierarchy.update(
                    section_number, cleaned_title, markdown_content
                )
                logger.debug(f"Updated hierarchy: {section_hierarchy.get_titles()}")

            elif token.type in (
                "paragraph_open",
                "bullet_list_open",
                "ordered_list_open",
            ):
                if not text_start_line:
                    text_start_line = current_line
                logger.debug(f"Started text block at line {text_start_line}")

            elif token.type == "inline":
                content = token.content
                page_match = re.match(r"<!--\s*page:\s*(\d+)\s*-->", content)
                if page_match:
                    flush_text()
                    current_page = int(page_match.group(1))
                    logger.debug(f"Updated current page to {current_page}")
                    continue

                # Handle images
                image_match = re.match(r"!\[(.*?)\]\((.*?)\)", content)
                if image_match:
                    flush_text()
                    image_content = content
                    image_start_line = current_line
                    image_end_line = current_line
                    image_data = parse_markdown_image(image_content)
                    section_titles = section_hierarchy.get_titles()
                    section_hash_path = section_hierarchy.get_hashes()
                    image_token_count = len(encoding.encode(image_content))

                    image_metadata = {
                        "alt": image_data["alt"] if image_data else "",
                        "src": image_data["src"] if image_data else "",
                    }
                    if marker_images:
                        matching_image = next(
                            (
                                img
                                for img in marker_images
                                if img["page"] == current_page
                            ),
                            None,
                        )
                        if matching_image:
                            image_metadata["bbox"] = matching_image["bbox"]
                            image_metadata["source"] = "marker_json"
                        else:
                            image_metadata["source"] = "markdown"
                    else:
                        image_metadata["source"] = "markdown"

                    extracted_data.append(
                        {
                            "file_path": file_path,
                            "repo_link": repo_link,
                            "extraction_date": datetime.datetime.now().isoformat(),
                            "type": "image",
                            "content": image_content,
                            "line_span": (image_start_line, image_end_line),
                            "token_count": image_token_count,
                            "section_id": hash_string(image_content),
                            "section_path": section_titles,
                            "section_hash_path": section_hash_path,
                            "page": current_page,
                            "metadata": image_metadata,
                        }
                    )
                    logger.debug(
                        f"Added image at lines {image_start_line}-{image_end_line}, "
                        f"section_path: {section_titles}"
                    )
                    continue

                # Handle tables
                if re.match(r"\|.*\|", content):
                    flush_text()
                    table_lines.append(content)
                    if not in_table:
                        in_table = True
                        table_start_line = current_line
                else:
                    if in_table:
                        table_content = "\n".join(table_lines)
                        table_data = parse_markdown_table(table_content)
                        if table_data:
                            table_end_line = current_line - 1
                            table_token_count = len(encoding.encode(table_content))
                            section_titles = section_hierarchy.get_titles()
                            section_hash_path = section_hierarchy.get_hashes()
                            table_id = hash_string(table_content)

                            table_metadata = {
                                "valid": True,
                                "issues": [],
                                "source": "markdown",
                            }
                            if marker_tables:
                                matching_table = next(
                                    (
                                        t
                                        for t in marker_tables
                                        if t["page"] == current_page
                                    ),
                                    None,
                                )
                                if matching_table:
                                    table_data = matching_table["table_data"]
                                    table_metadata["source"] = "marker_json"
                                    table_metadata["bbox"] = matching_table["bbox"]

                            if table_cache:
                                table_valid = any(
                                    t["page"] == current_page
                                    and (
                                        not table_metadata.get("bbox")
                                        or calculate_iou(
                                            t["bbox"],
                                            table_metadata.get(
                                                "bbox", [0, 0, 100, 100]
                                            ),
                                        )
                                        > 0.5
                                    )
                                    for t in table_cache
                                )
                                if not table_valid:
                                    table_metadata["issues"].append(
                                        "No matching table in table_cache"
                                    )
                                    table_metadata["valid"] = False

                            extracted_data.append(
                                {
                                    "file_path": file_path,
                                    "repo_link": repo_link,
                                    "extraction_date": datetime.datetime.now().isoformat(),
                                    "type": "table",
                                    "content": table_content,
                                    "line_span": (table_start_line, table_end_line),
                                    "token_count": table_token_count,
                                    "section_id": table_id,
                                    "section_path": section_titles,
                                    "section_hash_path": section_hash_path,
                                    "page": current_page,
                                    "metadata": {
                                        "table": table_data,
                                        "validation": table_metadata,
                                    },
                                }
                            )
                            logger.debug(
                                f"Added table at lines {table_start_line}-{table_end_line}, "
                                f"source: {table_metadata['source']}, section_path: {section_titles}"
                            )
                        table_lines = []
                        in_table = False

                if not in_table and not image_match:
                    text_content.append(content)

            elif token.type == "code_block" or token.type == "fence":
                flush_text()
                code_block = token.content.strip()
                code_start_line = current_line
                code_end_line = token.map[1] if token.map else code_start_line
                code_type = (
                    token.info.split()[0].lower()
                    if token.info
                    else Path(file_path).suffix[1:]
                )
                code_token_count = len(encoding.encode(code_block))
                code_metadata = extract_code_metadata(code_block, code_type)
                section_titles = section_hierarchy.get_titles()
                section_hash_path = section_hierarchy.get_hashes()

                extracted_data.append(
                    {
                        "file_path": file_path,
                        "repo_link": repo_link,
                        "extraction_date": datetime.datetime.now().isoformat(),
                        "type": "code",
                        "content": code_block,
                        "line_span": (code_start_line, code_end_line),
                        "token_count": code_token_count,
                        "section_id": hash_string(code_block),
                        "section_path": section_titles,
                        "section_hash_path": section_hash_path,
                        "page": current_page,
                        "metadata": {
                            "code_type": code_type,
                            "code_metadata": code_metadata,
                        },
                    }
                )
                logger.debug(
                    f"Added code block at lines {code_start_line}-{code_end_line}, "
                    f"section_path: {section_titles}"
                )
                code_block = None

            last_line = current_line if token.map else last_line

        # Flush any remaining content
        flush_text()
        if in_table and table_lines:
            table_content = "\n".join(table_lines)
            table_data = parse_markdown_table(table_content)
            if table_data:
                table_end_line = last_line
                table_token_count = len(encoding.encode(table_content))
                section_titles = section_hierarchy.get_titles()
                section_hash_path = section_hierarchy.get_hashes()
                table_id = hash_string(table_content)

                table_metadata = {"valid": True, "issues": [], "source": "markdown"}
                if marker_tables:
                    matching_table = next(
                        (t for t in marker_tables if t["page"] == current_page), None
                    )
                    if matching_table:
                        table_data = matching_table["table_data"]
                        table_metadata["source"] = "marker_json"
                        table_metadata["bbox"] = matching_table["bbox"]

                if table_cache:
                    table_valid = any(
                        t["page"] == current_page
                        and (
                            not table_metadata.get("bbox")
                            or calculate_iou(
                                t["bbox"], table_metadata.get("bbox", [0, 0, 100, 100])
                            )
                            > 0.5
                        )
                        for t in table_cache
                    )
                    if not table_valid:
                        table_metadata["issues"].append(
                            "No matching table in table_cache"
                        )
                        table_metadata["valid"] = False

                extracted_data.append(
                    {
                        "file_path": file_path,
                        "repo_link": repo_link,
                        "extraction_date": datetime.datetime.now().isoformat(),
                        "type": "table",
                        "content": table_content,
                        "line_span": (table_start_line, table_end_line),
                        "token_count": table_token_count,
                        "section_id": table_id,
                        "section_path": section_titles,
                        "section_hash_path": section_hash_path,
                        "page": current_page,
                        "metadata": {"table": table_data, "validation": table_metadata},
                    }
                )
                logger.debug(
                    f"Added final table at lines {table_start_line}-{table_end_line}, "
                    f"source: {table_metadata['source']}, section_path: {section_titles}"
                )

        # Batch tree-sitter processing for efficiency
        code_items = [
            (item["content"], item["metadata"]["code_type"])
            for item in extracted_data
            if item["type"] == "code"
        ]
        for (code, code_type), item in zip(
            code_items, [item for item in extracted_data if item["type"] == "code"]
        ):
            item["metadata"]["code_metadata"] = extract_code_metadata(code, code_type)

        return extracted_data

    except Exception as e:
        logger.error(f"Error extracting from Markdown file {file_path}: {e}")
        return []


if __name__ == "__main__":
    print("Markdown Extractor Module Verification")
    print("====================================")
    
    # Ensure required dependencies are installed
    missing_deps = []
    if MarkdownIt is None:
        missing_deps.append("markdown-it-py")
    if tiktoken is None:
        missing_deps.append("tiktoken")
    if ftfy is None:
        missing_deps.append("ftfy")
    
    if missing_deps:
        print(f"❌ Missing required dependencies: {', '.join(missing_deps)}")
        print("Install them with:")
        for dep in missing_deps:
            print(f"   uv add {dep}")
        sys.exit(1)
    
    # Create a test markdown file
    test_md_path = Path(__file__).parent / "test_markdown.md"
    
    # Check if we need to create the test file
    if not test_md_path.exists():
        print(f"Creating test markdown file at: {test_md_path}")
        test_markdown_content = """# Test Markdown Document

This is a test markdown document created for testing the markdown extractor.

## Section 1: Tables

Here's a sample table:

| Header 1 | Header 2 | Header 3 |
|----------|----------|----------|
| Cell 1   | Cell 2   | Cell 3   |
| Cell 4   | Cell 5   | Cell 6   |

## Section 2: Code Blocks

Here's a sample code block:

```python
def hello_world():
    print("Hello, world!")
    return 42
```

## Section 3: Images

Here's a sample image:

![Test Image](test_image.png)

## Section 4: Text Content

This is some regular text content that should be extracted as a text node.
It contains multiple sentences and should be treated as a single block.

* This is a list item
* This is another list item
"""
        try:
            with open(test_md_path, "w") as f:
                f.write(test_markdown_content)
            print(f"✅ Test file created successfully")
        except Exception as e:
            print(f"❌ Failed to create test file: {e}")
            # Use a string-based test as fallback
            print("Using in-memory test content instead")
            test_md_path = None
            test_markdown_content = "# Test\n\nTest content."
    else:
        print(f"Using existing test file: {test_md_path}")
        with open(test_md_path, "r") as f:
            test_markdown_content = f.read()
    
    # Mock Marker JSON for testing
    marker_json = {
        "pages": [
            {
                "page_number": 1,
                "blocks": [
                    {
                        "type": "table",
                        "bbox": [100, 160, 300, 200],
                        "cells": [
                            {"row": 0, "col": 0, "text": "Header 1"},
                            {"row": 0, "col": 1, "text": "Header 2"},
                            {"row": 0, "col": 2, "text": "Header 3"},
                            {"row": 1, "col": 0, "text": "Cell 1"},
                            {"row": 1, "col": 1, "text": "Cell 2"},
                            {"row": 1, "col": 2, "text": "Cell 3"},
                            {"row": 2, "col": 0, "text": "Cell 4"},
                            {"row": 2, "col": 1, "text": "Cell 5"},
                            {"row": 2, "col": 2, "text": "Cell 6"},
                        ],
                    },
                    {
                        "type": "image",
                        "bbox": [100, 50, 200, 100],
                        "alt": "Test Image",
                        "src": "test_image.png",
                    },
                ],
            }
        ]
    }
    
    # Mock table cache for testing
    table_cache = [{"page": 1, "bbox": [100, 150, 300, 210], "data": [], "accuracy": 95.0}]
    
    print("\n1. Testing basic extraction:")
    print("---------------------------")
    
    try:
        # Test with file if available, otherwise use memory content
        if test_md_path:
            result = extract_from_markdown(str(test_md_path), "https://github.com/test/repo")
        else:
            # Create a temporary file for testing
            temp_md_path = Path(__file__).parent / "temp_test.md"
            with open(temp_md_path, "w") as f:
                f.write(test_markdown_content)
            result = extract_from_markdown(str(temp_md_path), "https://github.com/test/repo")
            # Clean up
            os.unlink(temp_md_path)
        
        if result:
            print(f"✅ Successfully extracted {len(result)} nodes from Markdown")
            # Count node types
            node_types = {}
            for node in result:
                node_type = node.get("type", "unknown")
                node_types[node_type] = node_types.get(node_type, 0) + 1
            
            for node_type, count in node_types.items():
                print(f"  - {node_type}: {count} nodes")
            
            # Show a sample of each type
            for node_type in node_types.keys():
                sample = next((node for node in result if node.get("type") == node_type), None)
                if sample:
                    content = sample.get("content", "")
                    print(f"\n  {node_type.upper()} SAMPLE:")
                    print(f"  - Token count: {sample.get('token_count', 0)}")
                    print(f"  - Content preview: {content[:50]}..." if len(content) > 50 else content)
        else:
            print("❌ No data extracted from Markdown file")
    except Exception as e:
        print(f"❌ Error during basic extraction: {e}")
    
    print("\n2. Testing with marker JSON and table cache:")
    print("------------------------------------------")
    
    try:
        # Test with marker JSON and table cache
        if test_md_path:
            result_with_marker = extract_from_markdown(
                str(test_md_path), 
                "https://github.com/test/repo",
                marker_json,
                table_cache
            )
        else:
            # Create a temporary file for testing
            temp_md_path = Path(__file__).parent / "temp_test.md"
            with open(temp_md_path, "w") as f:
                f.write(test_markdown_content)
            result_with_marker = extract_from_markdown(
                str(temp_md_path), 
                "https://github.com/test/repo",
                marker_json,
                table_cache
            )
            # Clean up
            os.unlink(temp_md_path)
        
        if result_with_marker:
            print(f"✅ Successfully extracted {len(result_with_marker)} nodes from Markdown with marker JSON")
            
            # Look for marker-enhanced nodes
            marker_enhanced = 0
            for node in result_with_marker:
                if node.get("type") in ["table", "image"]:
                    metadata = node.get("metadata", {})
                    if isinstance(metadata, dict):
                        if metadata.get("source") == "marker_json" or (
                            isinstance(metadata.get("validation"), dict) and 
                            metadata.get("validation", {}).get("source") == "marker_json"
                        ):
                            marker_enhanced += 1
            
            print(f"  - Found {marker_enhanced} nodes enhanced with marker JSON data")
        else:
            print("❌ No data extracted from Markdown file with marker JSON")
    except Exception as e:
        print(f"❌ Error during marker JSON extraction: {e}")
    
    print("\n✅ Markdown extractor verification complete!")
