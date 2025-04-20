"""
Marker-based PDF processing for the PDF extraction pipeline.

This module uses Marker to extract content in JSON or Markdown format and parses
Markdown output into structured data using markdown-it.

Dependencies:
- marker: For PDF extraction.
- markdown-it-py: For Markdown parsing.
- loguru: For logging.
"""
import json
from typing import List, Dict, Union, Tuple
from loguru import logger
from markdown_it import MarkdownIt
from markdown_it.tree import SyntaxTreeNode

from .config import DEFAULT_OUTPUT_DIR
from .utils import _get_encoder

try:
    from marker.convert import convert_single_pdf
    from marker.models import load_all_models
except ImportError:
    logger.warning("Marker not installed. Functionality limited.")
    convert_single_pdf, load_all_models = None, None


def _run_marker(
    pdf_path: str, output_format: str = "json", force_ocr: bool = False
) -> Tuple[Union[List[Dict], str], Dict]:
    """Runs Marker PDF conversion."""
    if not convert_single_pdf or not load_all_models:
        logger.error("Marker not available.")
        return [] if output_format == "json" else "", {}

    try:
        model_lst = load_all_models()
        data, _, meta = convert_single_pdf(
            pdf_path,
            model_lst=model_lst,
            output_format=output_format,
            force_ocr=force_ocr,
        )
        logger.info(
            f"Marker extracted {len(data) if isinstance(data, list) else 'Markdown'} content."
        )
        return data, meta
    except Exception as e:
        logger.error(f"Marker conversion failed: {e}")
        return [] if output_format == "json" else "", {}


def parse_markdown(markdown_content: str, pdf_path: str, repo_link: str) -> List[Dict]:
    """Parses Markdown into structured data."""
    logger.debug("Parsing Marker Markdown.")
    elements = []
    encoding = _get_encoder()
    md = MarkdownIt("commonmark", {"html": False})

    try:
        tokens = md.parse(markdown_content)
        tree = SyntaxTreeNode(tokens)
        current_text = []

        for node in tree.children:
            if node.type == "heading":
                if current_text:
                    text = " ".join(current_text).strip()
                    if text:
                        elements.append(
                            {
                                "type": "paragraph",
                                "text": text,
                                "token_count": len(encoding.encode(text)),
                                "source": "marker_md",
                            }
                        )
                    current_text = []
                level = int(node.tag[1])
                text = "".join(
                    c.content for c in node.children if c.type == "text"
                ).strip()
                elements.append(
                    {
                        "type": "heading",
                        "level": level,
                        "text": text,
                        "token_count": len(encoding.encode(text)),
                        "source": "marker_md",
                    }
                )
            elif node.type == "paragraph":
                text = "".join(
                    c.content for c in node.children if c.type == "text"
                ).strip()
                if text:
                    current_text.append(text)
            elif node.type in ["fence", "code_block"]:
                if current_text:
                    text = " ".join(current_text).strip()
                    if text:
                        elements.append(
                            {
                                "type": "paragraph",
                                "text": text,
                                "token_count": len(encoding.encode(text)),
                                "source": "marker_md",
                            }
                        )
                    current_text = []
                code = node.content.strip()
                elements.append(
                    {
                        "type": "code",
                        "language": node.info if node.type == "fence" else None,
                        "text": code,
                        "token_count": len(encoding.encode(code)),
                        "source": "marker_md",
                    }
                )
            elif node.type in ["bullet_list", "ordered_list"]:
                if current_text:
                    text = " ".join(current_text).strip()
                    if text:
                        elements.append(
                            {
                                "type": "paragraph",
                                "text": text,
                                "token_count": len(encoding.encode(text)),
                                "source": "marker_md",
                            }
                        )
                    current_text = []
                items = [
                    "".join(n.content for n in item.walk() if n.type == "text").strip()
                    for item in node.children
                ]
                if items:
                    elements.append(
                        {
                            "type": "list",
                            "items": items,
                            "ordered": node.type == "ordered_list",
                            "token_count": len(encoding.encode("\n".join(items))),
                            "source": "marker_md",
                        }
                    )
            elif node.type == "table":
                if current_text:
                    text = " ".join(current_text).strip()
                    if text:
                        elements.append(
                            {
                                "type": "paragraph",
                                "text": text,
                                "token_count": len(encoding.encode(text)),
                                "source": "marker_md",
                            }
                        )
                    current_text = []
                try:
                    header = [
                        "".join(
                            c.content for c in th.children if c.type == "text"
                        ).strip()
                        for th in node.children[0].children[0].children
                    ]
                    body = [
                        [
                            "".join(
                                c.content for c in td.children if c.type == "text"
                            ).strip()
                            for td in row.children
                        ]
                        for row in node.children[1].children
                    ]
                    elements.append(
                        {
                            "type": "table",
                            "header": header,
                            "body": body,
                            "page": 0,
                            "bbox": None,
                            "needs_review": True,
                            "source": "marker_md",
                        }
                    )
                except Exception as e:
                    logger.warning(f"Failed to parse table: {e}")

        if current_text:
            text = " ".join(current_text).strip()
            if text:
                elements.append(
                    {
                        "type": "paragraph",
                        "text": text,
                        "token_count": len(encoding.encode(text)),
                        "source": "marker_md",
                    }
                )

        return elements
    except Exception as e:
        logger.error(f"Markdown parsing failed: {e}")
        return []


def process_marker(pdf_path: str, repo_link: str, use_markdown: bool) -> List[Dict]:
    """Processes PDF with Marker."""
    output_format = "markdown" if use_markdown else "json"
    data, _ = _run_marker(pdf_path, output_format=output_format)

    if use_markdown and isinstance(data, str):
        return parse_markdown(data, pdf_path, repo_link)
    elif isinstance(data, list):
        elements = []
        encoding = _get_encoder()
        for item in data:
            element = {
                "file_path": pdf_path,
                "repo_link": repo_link,
                "source": "marker_json",
            }
            if item.get("type") == "heading":
                element.update(
                    {
                        "type": "heading",
                        "level": item.get("level", 0),
                        "text": item.get("text", ""),
                        "token_count": len(encoding.encode(item.get("text", ""))),
                    }
                )
                elements.append(element)
            elif item.get("type") == "paragraph":
                element.update(
                    {
                        "type": "paragraph",
                        "text": item.get("text", ""),
                        "token_count": len(encoding.encode(item.get("text", ""))),
                    }
                )
                elements.append(element)
            elif item.get("type") == "list":
                element.update(
                    {
                        "type": "list",
                        "items": item.get("items", []),
                        "ordered": item.get("ordered", False),
                        "token_count": len(
                            encoding.encode("\n".join(item.get("items", [])))
                        ),
                    }
                )
                elements.append(element)
            elif item.get("type") == "table":
                element.update(
                    {
                        "type": "table",
                        "header": item.get("header", []),
                        "body": item.get("rows", []),
                        "page": item.get("page", 0),
                        "bbox": item.get("bbox"),
                        "needs_review": item.get("needs_review", True),
                        "source": "marker_json",
                    }
                )
                elements.append(element)
        return _assign_unique_table_ids(
            [e for e in elements if e["type"] == "table"], "marker_json"
        ) + [e for e in elements if e["type"] != "table"]
    return []


def usage_function():
    """Demonstrates Marker processing."""
    sample_md = "# Heading\n```python\nprint('Hello')\n```"
    elements = parse_markdown(sample_md, "sample.pdf", "https://repo")
    return elements


if __name__ == "__main__":
    result = usage_function()
    print("Marker Processing Result:")
    print(json.dumps(result, indent=2))
