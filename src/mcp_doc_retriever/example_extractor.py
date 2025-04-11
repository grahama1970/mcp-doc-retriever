"""
Module: example_extractor.py

Description:
Extracts nested JSON examples from Markdown and HTML files in a given directory and indexes them for agent queries.
Intended for use after documentation download (static repo or recursive HTML crawl).

Third-party packages:
- BeautifulSoup: https://www.crummy.com/software/BeautifulSoup/bs4/doc/
- json: https://docs.python.org/3/library/json.html

Sample input:
    extract_and_index_examples(
        content_dir="./downloads/content/arangodb_docs/site/content/",
        index_path="./downloads/index/arangodb_docs_examples.jsonl"
    )

Expected output:
- For each Markdown or HTML file, all JSON code blocks are extracted and written to the index file as ContentBlock records.

"""
import os
# import json
import logging
from typing import Optional, List
from mcp_doc_retriever.utils import (
    extract_content_blocks_from_html,
    extract_content_blocks_from_markdown,
)
# from mcp_doc_retriever.models import ContentBlock

logger = logging.getLogger(__name__)

def extract_and_index_examples(
    content_dir: str,
    index_path: str,
    file_types: Optional[List[str]] = None,
    logger_override=None,
) -> int:
    """
    Recursively extracts JSON examples from Markdown and HTML files in content_dir and writes them to index_path.

    Args:
        content_dir: Directory containing downloaded documentation files.
        index_path: Path to the output index file (JSONL).
        file_types: List of file extensions to process (default: [".md", ".markdown", ".html", ".htm"]).
        logger_override: Optional logger to use.

    Returns:
        Number of JSON examples indexed.
    """
    _logger = logger_override or logger
    if file_types is None:
        file_types = [".md", ".markdown", ".html", ".htm"]

    count = 0
    _logger.info(f"Starting example extraction in {content_dir} (index: {index_path})")
    with open(index_path, "w", encoding="utf-8") as index_file:
        for root, _, files in os.walk(content_dir):
            for fname in files:
                ext = os.path.splitext(fname)[1].lower()
                if ext not in file_types:
                    continue
                fpath = os.path.join(root, fname)
                try:
                    with open(fpath, "r", encoding="utf-8", errors="ignore") as f:
                        content = f.read()
                    if ext in [".md", ".markdown"]:
                        blocks = extract_content_blocks_from_markdown(content, source_url=fpath)
                    else:
                        blocks = extract_content_blocks_from_html(content, source_url=fpath)
                    for block in blocks:
                        if block.type == "json" and block.metadata and "parsed_json" in block.metadata:
                            # Write ContentBlock as JSONL
                            index_file.write(block.model_dump_json(exclude_none=True) + "\n")
                            count += 1
                except Exception as e:
                    _logger.warning(f"Failed to extract examples from {fpath}: {e}", exc_info=True)
    _logger.info(f"Extraction complete. {count} JSON examples indexed in {index_path}")
    return count

if __name__ == "__main__":
    # Minimal real-world usage: extract examples from a test directory
    import sys
    logging.basicConfig(level=logging.INFO)
    if len(sys.argv) < 3:
        print("Usage: python -m src.mcp_doc_retriever.example_extractor <content_dir> <index_path>")
        sys.exit(1)
    content_dir = sys.argv[1]
    index_path = sys.argv[2]
    n = extract_and_index_examples(content_dir, index_path)
    print(f"Extracted {n} JSON examples to {index_path}")