# src/mcp_doc_retriever/context7/ipynb_extractor.py
"""
Module: ipynb_extractor.py
Description: This module extracts code blocks and descriptions from Jupyter Notebook (.ipynb) files.

Third-party package documentation:
- json: https://docs.python.org/3/library/json.html
- os: https://docs.python.org/3/library/os.html
- pathlib: https://docs.python.org/3/library/pathlib.html
- file_discovery: mcp_doc_retriever/context7/file_discovery.py
- tiktoken: https://github.com/openai/tiktoken
- tree_sitter: https://tree-sitter.github.io/tree-sitter/
- tree_sitter_languages: https://github.com/grantjenks/tree-sitter-languages

Sample Input:
file_path = "path/to/my_notebook.ipynb" (Path to an IPython Notebook file)

Expected Output:
A list of dictionaries, where each dictionary represents a code block and its description,
extracted from the IPython Notebook file, formatted as a JSON string, including repo link, extraction date, token counts, line number spans, and code type.
If tree-sitter is available, additional code metadata is included.
"""

import os
from pathlib import Path
from typing import List, Dict
import json
import datetime
import tiktoken
from loguru import logger

from mcp_doc_retriever.context7.file_discovery import find_relevant_files
from mcp_doc_retriever.context7.tree_sitter_utils import extract_code_metadata
from mcp_doc_retriever.context7.sparse_checkout import sparse_checkout


def extract_from_ipynb(file_path: str, repo_link: str) -> List[Dict]:
    """
    Extracts code blocks and descriptions from a Jupyter Notebook file.

    Args:
        file_path (str): The path to the Jupyter Notebook file.
        repo_link (str): The URL of the repository.

    Returns:
        List[Dict]: A list of dictionaries, each containing code and description.
    """
    try:
        notebook_content = Path(file_path).read_text(encoding="utf-8")
        notebook_data = json.loads(notebook_content)

        extracted_data: List[Dict] = []
        encoding = tiktoken.encoding_for_model("gpt-4")

        line_number = 1  # Track line numbers across cells
        for cell in notebook_data["cells"]:
            if cell["cell_type"] == "code":
                code_block = "".join(cell["source"]).strip()
                code_start_line = line_number
                code_end_line = code_start_line + code_block.count("\n")
                code_type = "python"  # Jupyter notebooks are usually Python
                description = ""
                description_start_line = None

                # Find preceding markdown cell for description
                cell_index = notebook_data["cells"].index(cell)
                if (
                    cell_index > 0
                    and notebook_data["cells"][cell_index - 1]["cell_type"]
                    == "markdown"
                ):
                    description = "".join(
                        notebook_data["cells"][cell_index - 1]["source"]
                    ).strip()
                    description_start_line = code_start_line - len(
                        notebook_data["cells"][cell_index - 1]["source"]
                    )

                code_token_count = len(encoding.encode(code_block))
                description_token_count = len(encoding.encode(description))

                code_metadata = extract_code_metadata(code_block, code_type)

                extracted_data.append(
                    {
                        "file_path": file_path,
                        "repo_link": repo_link,
                        "extraction_date": datetime.datetime.now().isoformat(),
                        "code_line_span": (code_start_line, code_end_line),
                        "description_line_span": (
                            description_start_line,
                            description_start_line if description_start_line else None,
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

                line_number += code_block.count("\n") + 1  # Account for code cell lines

            elif cell["cell_type"] == "markdown":
                # Update line number for markdown cells
                line_number += len(cell["source"])

        return extracted_data

    except Exception as e:
        logger.error(f"Error extracting from IPython Notebook file {file_path}: {e}")
        return []


def usage_function():
    """
    Demonstrates basic usage of the extract_from_ipynb function.
    """
    repo_url = "https://github.com/ronitmalvi/fine-tune.git"
    repo_dir = "/tmp/fine_tune_sparse"
    repo_link = (
        "https://github.com/ronitmalvi/fine-tune/blob/main/finetune-llama-vision.ipynb"
    )
    exclude_patterns = []
    patterns = ["*.ipynb"]

    success = sparse_checkout(repo_url, repo_dir, patterns)
    if not success:
        logger.error("Sparse checkout failed.")
        raise RuntimeError("Sparse checkout failed.")

    relevant_files = find_relevant_files(repo_dir, exclude_patterns)

    if not relevant_files:
        logger.error(
            f"No relevant files found in {repo_dir}. Ensure sparse checkout was successful."
        )
        raise FileNotFoundError(f"No relevant files found in {repo_dir}")

    ipynb_file = None
    for file_path in relevant_files:
        if file_path.endswith(".ipynb"):
            ipynb_file = file_path
            break

    if not ipynb_file:
        logger.error("No IPYNB file found in the repository.")
        raise FileNotFoundError("No IPYNB file found in the repository.")

    extracted_data = extract_from_ipynb(ipynb_file, repo_link)

    if extracted_data:
        logger.info("Extracted data from IPYNB file:")
        json_output = json.dumps(extracted_data, indent=4)
        logger.info(f"\n{json_output}")

        assert len(extracted_data) > 0, (
            "No data extracted despite code blocks being present."
        )
        logger.info("Extraction test passed: Data extracted as expected.")

    else:
        logger.error(
            "No data extracted from IPYNB file, but code blocks were expected."
        )
        raise AssertionError(
            "No data extracted from IPYNB file, but code blocks were expected."
        )


if __name__ == "__main__":
    # Basic usage demonstration
    logger.info("Running IPYNB extraction usage example...")
    try:
        usage_function()
        logger.info("IPYNB extraction usage example completed successfully.")
    except AssertionError as e:
        logger.error(f"IPYNB extraction usage example failed: {e}")
    except FileNotFoundError as e:
        logger.error(f"IPYNB extraction usage example failed: {e}")
    except Exception as e:
        logger.error(f"An unexpected error occurred: {e}")
