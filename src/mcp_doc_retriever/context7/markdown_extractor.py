# src/mcp_doc_retriever/extractors/markdown_extractor.py
"""
Module: markdown_extractor.py
Description: This module extracts code blocks and descriptions from Markdown files using the `markdown-it-py` library.
It includes section hierarchy tracking and cleans section titles using `ftfy` to normalize Unicode characters.

Third-party package documentation:
- markdown-it-py: https://github.com/executablebooks/markdown-it-py
- os: https://docs.python.org/3/library/os.html
- pathlib: https://docs.python.org/3/library/pathlib.html
- file_discovery: mcp_doc_retriever/context7/file_discovery.py
- tiktoken: https://github.com/openai/tiktoken
- tree_sitter: https://tree-sitter.github.io/tree-sitter/
- tree_sitter_languages: https://github.com/grantjenks/tree-sitter-languages
- ftfy: https://github.com/rspeer/ftfy

Sample Input:
file_path = "path/to/async.md" (Path to a markdown file)

Expected Output:
A list of dictionaries, where each dictionary represents a code block and its description,
extracted from the Markdown file, formatted as a JSON string, including repo link, extraction date,
token counts, line number spans, code type, cleaned section path, section hash path, and code metadata (if tree-sitter is available).
"""

import os
import re
from pathlib import Path
from typing import List, Dict, Tuple, Optional
from markdown_it import MarkdownIt
from loguru import logger
import json
import datetime
import tiktoken
import ftfy
import unicodedata

from mcp_doc_retriever.context7.file_discovery import find_relevant_files
from mcp_doc_retriever.context7.tree_sitter_utils import extract_code_metadata
from mcp_doc_retriever.context7.text_chunker import (
    SectionHierarchy,
    hash_string,
)


def clean_section_title(title: str, to_ascii: bool = False) -> str:
    """
    Cleans a section title by normalizing Unicode characters and removing unprintable characters.

    Args:
        title (str): The raw section title.
        to_ascii (bool): If True, converts non-ASCII characters to their closest ASCII equivalents.
                        If False, preserves Unicode characters (e.g., Chinese characters).

    Returns:
        str: The cleaned section title.
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


def extract_from_markdown(file_path: str, repo_link: str) -> List[Dict]:
    """
    Extracts code blocks and descriptions from a Markdown file, including section hierarchy tracking.
    Cleans section titles using `ftfy` to normalize Unicode characters.

    Args:
        file_path (str): The path to the Markdown file.
        repo_link (str): The URL of the repository.

    Returns:
        List[Dict]: A list of dictionaries, each containing code, description, and section information.
    """
    section_hierarchy = SectionHierarchy()
    try:
        md = MarkdownIt("commonmark", {"html": False, "typographer": True})
        markdown_content = Path(file_path).read_text(encoding="utf-8")
        tokens = md.parse(markdown_content)

        extracted_data: List[Dict] = []
        code_block = None
        description = ""
        code_start_line = None
        description_start_line = None
        encoding = tiktoken.encoding_for_model("gpt-4")
        section_title = ""
        section_level = 0
        # Track section counts at each level (e.g., [0, 2, 1] for level 1: 2, level 2: 1)
        section_counts: List[int] = [
            0
        ] * 6  # Support up to 6 heading levels (# to ######)

        for i, token in enumerate(tokens):
            if token.type == "heading_open":
                section_level = int(token.tag[1:])  # e.g., h1 -> 1, h2 -> 2
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
                # Clean the section title
                cleaned_title = clean_section_title(section_title, to_ascii=True)
                logger.debug(f"Cleaned section title: {cleaned_title}")

                # Extract section number from title (e.g., "2.2 Usage" -> "2.2")
                section_number = ""
                number_match = re.match(r"(\d+(?:\.\d+)*\.?)\s*(.*)", section_title)
                if number_match:
                    section_number = number_match.group(1).rstrip(".")
                    cleaned_title = clean_section_title(
                        number_match.group(2) or "Unnamed Section", to_ascii=True
                    )
                    logger.debug(
                        f"Extracted section number: {section_number}, title: {cleaned_title}"
                    )
                else:
                    # Generate section number dynamically
                    # Reset counts for deeper levels
                    for j in range(section_level, len(section_counts)):
                        section_counts[j] = 0
                    # Increment count for current level
                    section_counts[section_level - 1] += 1
                    # Build section number (e.g., "2.2")
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
                    logger.debug(f"Generated section number: {section_number}")

                # Update section hierarchy
                section_hierarchy.update(
                    section_number, cleaned_title, markdown_content
                )
                logger.debug(
                    f"Current section hierarchy: {section_hierarchy.get_titles()}"
                )

            elif token.type == "paragraph_open":
                description = ""
                description_start_line = token.map[0] + 1 if token.map else 1
                logger.debug(f"Started paragraph at line {description_start_line}")

            elif token.type == "paragraph_close":
                description = description.strip()
                logger.debug(f"Closed paragraph, description: {description[:50]}...")

            elif token.type == "code_block" or token.type == "fence":
                code_block = token.content.strip()
                code_start_line = token.map[0] + 1 if token.map else 1
                code_end_line = token.map[1] if token.map else code_start_line

                code_type = (
                    token.info.split()[0].lower()
                    if token.info
                    else Path(file_path).suffix[1:]
                )
                code_token_count = len(encoding.encode(code_block))
                description_token_count = len(encoding.encode(description))

                code_metadata = extract_code_metadata(code_block, code_type)
                section_titles = section_hierarchy.get_titles()
                section_hash_path = section_hierarchy.get_hashes()

                extracted_data.append(
                    {
                        "file_path": file_path,
                        "repo_link": repo_link,
                        "extraction_date": datetime.datetime.now().isoformat(),
                        "code_line_span": (code_start_line, code_end_line),
                        "description_line_span": (
                            description_start_line,
                            description_start_line,
                        ),
                        "code": code_block,
                        "code_type": code_type,
                        "description": description,
                        "code_token_count": code_token_count,
                        "description_token_count": description_token_count,
                        "embedding_code": None,
                        "embedding_description": None,
                        "code_metadata": code_metadata,
                        "section_path": section_titles,
                        "section_hash_path": section_hash_path,
                    }
                )
                logger.debug(
                    f"Added code block at lines {code_start_line}-{code_end_line}, "
                    f"section_path: {section_titles}"
                )
                code_block = None

            elif token.type == "inline" and description is not None:
                description += token.content

        return extracted_data

    except Exception as e:
        logger.error(f"Error extracting from Markdown file {file_path}: {e}")
        return []


def usage_function():
    """
    Demonstrates basic usage of the extract_from_markdown function.
    """
    repo_dir = "/tmp/fastapi_sparse"
    repo_link = "https://github.com/fastapi/fastapi.git"
    exclude_patterns = ['*.yml', '*.yaml', '*.json', '*.txt', '*.csv']

    relevant_files = find_relevant_files(repo_dir, exclude_patterns)

    if not relevant_files:
        logger.error(
            f"No relevant files found in {repo_dir}. Ensure sparse checkout was successful."
        )
        raise FileNotFoundError(f"No relevant files found in {repo_dir}")

    # We are just goning to test the async.md file
    # In a real-world scenario, you would want to test all relevant files
    markdown_file = None
    for file_path in relevant_files:
        if "async.md" in file_path:
            markdown_file = file_path
            break

    if not markdown_file:
        logger.error("No async.md file found in the repository.")
        raise FileNotFoundError("No async.md file found in the repository.")

    extracted_data = extract_from_markdown(markdown_file, repo_link)

    if extracted_data:
        logger.info("Extracted data from Markdown file:")
        json_output = json.dumps(extracted_data, indent=4)
        logger.info(f"\n{json_output}")

        assert len(extracted_data) > 0, (
            "No data extracted despite code blocks being present."
        )
        logger.info("Extraction test passed: Data extracted as expected.")

    else:
        logger.error(
            "No data extracted from Markdown file, but code blocks were expected."
        )
        raise AssertionError(
            "No data extracted from Markdown file, but code blocks were expected."
        )


if __name__ == "__main__":
    logger.info("Running Markdown extraction usage example...")
    try:
        usage_function()
        logger.info("Markdown extraction usage example completed successfully.")
    except AssertionError as e:
        logger.error(f"Markdown extraction usage example failed: {e}")
    except FileNotFoundError as e:
        logger.error(f"Markdown extraction usage example failed: {e}")
    except Exception as e:
        logger.error(f"An unexpected error occurred: {e}")
