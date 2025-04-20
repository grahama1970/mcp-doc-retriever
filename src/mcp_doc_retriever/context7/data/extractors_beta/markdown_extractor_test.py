# src/mcp_doc_retriever/context7/markdown_extractor.py
"""
Module: markdown_extractor.py
Description: This module extracts code blocks and descriptions from Markdown files using the `markdown-it-py` library.
"""

import os
from pathlib import Path
from typing import List, Dict, Tuple, Optional
from markdown_it import MarkdownIt
from loguru import logger
from mcp_doc_retriever.context7.file_discovery import find_relevant_files
import json
import datetime
import tiktoken
import spacy

from mcp_doc_retriever.context7.tree_sitter_utils import extract_code_metadata
from mcp_doc_retriever.context7.text_chunker import (
    SectionHierarchy,
    hash_string,
)  # Import SectionHierarchy and hash_string


def extract_from_markdown(file_path: str, repo_link: str) -> List[Dict]:
    """
    Extracts code blocks and descriptions from a Markdown file.

    Args:
        file_path (str): The path to the Markdown file.
        repo_link (str): The URL of the repository.

    Returns:
        List[Dict]: A list of dictionaries, each containing code and description.
    """
    section_hierarchy = SectionHierarchy()  # Initialize SectionHierarchy
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
        section_number = ""
        section_title = ""
        section_level = 0
        section_number_list: List[int] = []

        for i, token in enumerate(tokens):
            # Handle headings to update section hierarchy
            if token.type == "heading_open":
                section_level = int(
                    token.tag[1:]
                )  # e.g., h1, h2, h3, convert 'h1' to 1
                section_title = ""  # Reset title for this heading

            elif token.type == "inline" and tokens[i - 1].type == "heading_open":
                section_title += token.content  # Capture the heading content

            elif token.type == "heading_close":
                # Update section hierarchy when the heading closes
                # Update section number list based on the current heading level
                if len(section_number_list) >= section_level:
                    section_number_list = section_number_list[
                        : section_level - 1
                    ]  # Truncate the list
                while len(section_number_list) < section_level:
                    section_number_list.append(1)  # Add a new level, start at 1
                if section_number_list:
                    section_number_list[-1] += 1  # Increment the last number

                section_number = ".".join(map(str, section_number_list))
                section_hierarchy.update(
                    section_number, section_title, markdown_content
                )

            elif token.type == "paragraph_open":
                # Reset description for a new paragraph
                description = ""
                description_start_line = (
                    token.map[0] + 1 if token.map else 1
                )  # Start line of paragraph

            elif token.type == "paragraph_close":
                # Paragraph is done, reset values
                description = description.strip()

            elif token.type == "code_block" or token.type == "fence":
                code_block = token.content.strip()
                code_start_line = token.map[0] + 1 if token.map else 1
                code_end_line = token.map[1] if token.map else code_start_line

                # Determine code type
                code_type = (
                    token.info.split()[0].lower()
                    if token.info
                    else Path(file_path).suffix[1:]
                )  # Language tag or file extension
                code_token_count = len(encoding.encode(code_block))
                description_token_count = len(encoding.encode(description))

                # Extract code metadata using tree-sitter (if available)
                code_metadata = extract_code_metadata(code_block, code_type)
                section_titles = section_hierarchy.get_titles()  # Get section titles
                section_hash_path = section_hierarchy.get_hashes()  # Get section hashes
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
                        "section_path": section_titles,  # Added section titles to the extracted data
                        "section_hash_path": section_hash_path,  # Added section hash path
                    }
                )
                code_block = None

            elif token.type == "inline" and description is not None:
                # Append the inline text to the current description
                description += token.content

        return extracted_data

    except Exception as e:
        logger.error(f"Error extracting from Markdown file {file_path}: {e}")
        return []


def usage_function(file_path: str, repo_link: str):
    """
    Demonstrates basic usage of the extract_from_markdown function.
    """

    extracted_data = extract_from_markdown(file_path, repo_link)

    if extracted_data:
        logger.info("Extracted data from Markdown file:")
        # Format extracted data as JSON
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
    # Basic usage demonstration
    logger.info("Running Markdown extraction usage example...")
    try:
        # Use the correct file path and repo link
        file_path = "src/mcp_doc_retriever/context7/data/extractors_beta/async.md"  # Correct file path
        repo_link = "https://github.com/grahama1970/mcp-doc-retriever-test-repo/tree/main/async_extractor_test"  # Correct repo link
        usage_function(file_path, repo_link)
        logger.info("Markdown extraction usage example completed successfully.")
    except AssertionError as e:
        logger.error(f"Markdown extraction usage example failed: {e}")
    except FileNotFoundError as e:
        logger.error(f"Markdown extraction usage example failed: {e}")
    except Exception as e:
        logger.error(f"An unexpected error occurred: {e}")
