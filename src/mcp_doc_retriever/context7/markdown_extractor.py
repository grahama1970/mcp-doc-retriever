# src/mcp_doc_retriever/extractors/markdown_extractor.py
"""
Module: markdown_extractor.py
Description: This module extracts code blocks and descriptions from Markdown files using the `markdown-it-py` library.

Third-party package documentation:
- markdown-it-py: https://github.com/executablebooks/markdown-it-py
- os: https://docs.python.org/3/library/os.html
- pathlib: https://docs.python.org/3/library/pathlib.html
- file_discovery: mcp_doc_retriever/context7/file_discovery.py
- tiktoken: https://github.com/openai/tiktoken
- tree_sitter: https://tree-sitter.github.io/tree-sitter/
- tree_sitter_languages: https://github.com/grantjenks/tree-sitter-languages

Sample Input:
file_path = "path/to/async.md" (Path to a markdown file)

Expected Output:
A list of dictionaries, where each dictionary represents a code block and its description,
extracted from the Markdown file, formatted as a JSON string, including repo link, extraction date, token counts, line number spans, and code type.
If tree-sitter is available, additional code metadata is included.
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

from mcp_doc_retriever.context7.tree_sitter_utils import extract_code_metadata


def extract_from_markdown(file_path: str, repo_link: str) -> List[Dict]:
    """
    Extracts code blocks and descriptions from a Markdown file.

    Args:
        file_path (str): The path to the Markdown file.
        repo_link (str): The URL of the repository.

    Returns:
        List[Dict]: A list of dictionaries, each containing code and description.
    """
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

        for i, token in enumerate(tokens):
            if token.type == "paragraph_open":
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


def usage_function():
    """
    Demonstrates basic usage of the extract_from_markdown function.
    It uses file_discovery to locate 'async.md' and extracts code and descriptions.
    The output is formatted as a JSON string, including repo link, extraction date, token counts, line number spans, code type, and tree-sitter metadata (if available).
    """
    repo_dir = "/tmp/fastapi_sparse"
    repo_link = "https://github.com/fastapi/fastapi.git"  # Define the repo link
    exclude_patterns = []  # No exclusion for this test

    relevant_files = find_relevant_files(repo_dir, exclude_patterns)

    if not relevant_files:
        logger.error(
            f"No relevant files found in {repo_dir}. Ensure sparse checkout was successful."
        )
        raise FileNotFoundError(f"No relevant files found in {repo_dir}")

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
        usage_function()
        logger.info("Markdown extraction usage example completed successfully.")
    except AssertionError as e:
        logger.error(f"Markdown extraction usage example failed: {e}")
    except FileNotFoundError as e:
        logger.error(f"Markdown extraction usage example failed: {e}")
    except Exception as e:
        logger.error(f"An unexpected error occurred: {e}")
