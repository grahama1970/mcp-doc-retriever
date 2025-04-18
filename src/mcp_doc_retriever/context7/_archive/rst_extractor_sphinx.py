# src/mcp_doc_retriever/context7/rst_extractor_sphinx.py
"""
Module: rst_extractor_sphinx.py
Description: This module extracts code blocks from RST files in a sparse checkout directory using Sphinx.
"""

import os
import sys
from pathlib import Path
from typing import List, Dict
import datetime
import tiktoken
import json
from loguru import logger

from sphinx.application import Sphinx
from sphinx.util.docutils import docutils
from docutils import nodes
from docutils.parsers.rst import directives

from mcp_doc_retriever.context7.file_discovery import find_relevant_files
from mcp_doc_retriever.context7.tree_sitter_utils import extract_code_metadata
from mcp_doc_retriever.context7.sparse_checkout import sparse_checkout


# src/mcp_doc_retriever/context7/rst_extractor_sphinx.py
"""
Module: rst_extractor_sphinx.py
Description: This module extracts code blocks and descriptions from reStructuredText (.rst) files using Sphinx.
"""

import os
import sys
from pathlib import Path
from typing import List, Dict
import datetime
import tiktoken
import json
from loguru import logger

from sphinx.application import Sphinx
from sphinx.util.docutils import docutils
from docutils import nodes
from docutils.parsers.rst import directives

from mcp_doc_retriever.context7.file_discovery import find_relevant_files
from mcp_doc_retriever.context7.tree_sitter_utils import extract_code_metadata
from mcp_doc_retriever.context7.sparse_checkout import sparse_checkout


def extract_from_rst_sphinx(
    file_path: str, repo_link: str, sparse_checkout_dir: str
) -> List[Dict]:
    """
    Extracts code blocks and descriptions from an RST file using Sphinx.

    Args:
        file_path (str): The path to the RST file.
        repo_link (str): The URL of the repository.
        sparse_checkout_dir (str): The directory where the sparse checkout is located.

    Returns:
        List[Dict]: A list of dictionaries, each containing code and description.
    """
    try:
        logger.info(f"Parsing RST file with Sphinx: {file_path}")

        # Construct paths relative to the sparse checkout directory
        srcdir = os.path.join(sparse_checkout_dir, "docs")
        confdir = os.path.join(sparse_checkout_dir, "docs")

        # Create a Sphinx application
        doctreedir = os.path.join(sparse_checkout_dir, "docs", "_build", "doctrees")
        outdir = os.path.join(sparse_checkout_dir, "docs", "_build", "html")

        # Check for existence of conf.py file.
        conf_py_path = os.path.join(confdir, "conf.py")
        if not os.path.exists(conf_py_path):
            logger.error(f"conf.py not found in sparse checkout directory: {confdir}")
            raise FileNotFoundError(f"conf.py not found at {conf_py_path}")

        # Initialize Sphinx app
        app = Sphinx(srcdir, confdir, outdir, doctreedir, "html")
        app.add_source_suffix(
            ".rst", "restructuredtext"
        )  # Ensure .rst files are correctly parsed
        app.config.nitpicky = True  # Report any inconsistencies

        # Read the RST file content
        with open(file_path, "r", encoding="utf-8") as f:
            rst_text = f.read()

        # Parse the RST file
        document = docutils.core.publish_doctree(
            rst_text,
            settings_overrides={
                "input_encoding": "utf-8",
                "env": app.env,
                "doctitle_xform": False,
            },
        )

        extracted_data: List[Dict] = []
        encoding = tiktoken.encoding_for_model("gpt-4")

        for node in document.traverse():
            logger.debug(f"Visiting node: {type(node)}, text: {node.astext().strip()}")

            if isinstance(node, nodes.literal_block):
                # Check the parent node for the 'testcode' class or 'code-block'
                if isinstance(node.parent, nodes.container) and (
                    "testcode" in node.parent.get("classes", [])
                    or "code-block" in node.parent.get("classes", [])
                ):
                    logger.info(
                        f"Found a code block directive: {node.parent.get('classes')}"
                    )
                    try:
                        code_block = node.astext().strip()
                        logger.debug(f"Extracted code block: {code_block}")

                        code_start_line = node.line if node.line else 1
                        code_end_line = code_start_line + code_block.count("\n")
                        code_type = "python"  # Default to Python

                        # Extract description from preceding paragraph, if any
                        description = get_description(node)
                        description_start_line = description["line"]
                        description_text = description["text"]

                        process_code_block(
                            extracted_data,
                            file_path,
                            repo_link,
                            code_block,
                            code_start_line,
                            code_end_line,
                            code_type,
                            description_text,
                            description_start_line,
                        )
                    except Exception as e:
                        logger.error(f"Error processing code block: {e}", exc_info=True)

        logger.info(f"Extracted {len(extracted_data)} code blocks.")

        return extracted_data

    except Exception as e:
        logger.error(f"Error parsing RST file with Sphinx: {e}", exc_info=True)
        return []


def get_description(node):
    """Extracts description from the preceding paragraph node."""
    try:
        description = ""
        description_start_line = 1

        preceding_element = node.previous_node(siblings=True)
        if isinstance(preceding_element, nodes.paragraph):
            description = preceding_element.astext().strip()
            description_start_line = (
                preceding_element.line if preceding_element.line else 1
            )

        return {"text": description, "line": description_start_line}
    except Exception as e:
        logger.error(f"Error getting description: {e}", exc_info=True)
        return {"text": "", "line": 1}


def process_code_block(
    extracted_data,
    file_path,
    repo_link,
    code_block,
    code_start_line,
    code_end_line,
    code_type,
    description,
    description_start_line,
):
    """Processes a code block and appends its metadata to extracted_data."""
    try:
        encoding = tiktoken.encoding_for_model("gpt-4")
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
    except Exception as e:
        logger.error(f"Error processing code block: {e}", exc_info=True)


def usage_function_sphinx():
    """
    Demonstrates usage of extract_from_rst_sphinx with a sparse checkout directory.
    """
    logger.remove()  # Remove the default handler
    logger.add(
        sys.stderr, level="DEBUG", colorize=True
    )  # Add a handler for the terminal at DEBUG level

    repo_url = "https://github.com/arangodb/python-arango.git"
    repo_dir = "/tmp/python_arango_sparse"  # The sparse checkout directory
    repo_link = "https://github.com/arangodb/python-arango/blob/main/docs/aql.rst"  # Correct repo link
    exclude_patterns = []
    patterns = ["docs/*"]  # Sparse checkout pattern

    # Perform sparse checkout
    success = sparse_checkout(repo_url, repo_dir, patterns)
    if not success:
        logger.error("Sparse checkout failed.")
        raise RuntimeError("Sparse checkout failed.")

    # Explicitly check for aql.rst *AFTER* sparse checkout
    aql_rst_path = os.path.join(repo_dir, "docs", "aql.rst")
    if os.path.exists(aql_rst_path):
        logger.info(f"aql.rst found at: {aql_rst_path}")
    else:
        logger.error("aql.rst NOT found.  Sparse checkout may have failed.")
        raise FileNotFoundError("aql.rst not found after sparse checkout.")

    relevant_files = find_relevant_files(repo_dir, exclude_patterns)

    if not relevant_files:
        logger.error(
            f"No relevant files found in {repo_dir}. Ensure sparse checkout was successful."
        )
        raise FileNotFoundError(f"No relevant files found in {repo_dir}")

    rst_file = None
    for file_path in relevant_files:
        if "docs/aql.rst" in file_path:  # Find the specific aql.rst file
            rst_file = file_path
            break

    if not rst_file:
        logger.error("aql.rst not found in the repository.")
        raise FileNotFoundError("aql.rst not found in the repository.")

    # Pass the sparse checkout directory to extract_from_rst_sphinx
    extracted_data = extract_from_rst_sphinx(rst_file, repo_link, repo_dir)

    if extracted_data:
        logger.info("Extracted data from RST file (Sphinx):")
        json_output = json.dumps(extracted_data, indent=4)
        logger.info(f"\n{json_output}")

        assert len(extracted_data) > 0, (
            "No data extracted despite code blocks being present."
        )
        logger.info("Extraction test passed: Data extracted as expected.")

    else:
        logger.error(
            "No data extracted from RST file (Sphinx), but code blocks were expected."
        )
        raise AssertionError(
            "No data extracted from RST file (Sphinx), but code blocks were expected."
        )


if __name__ == "__main__":
    # Basic usage demonstration
    logger.info("Running RST extraction usage example with Sphinx...")
    try:
        usage_function_sphinx()
        logger.info("RST extraction usage example with Sphinx completed successfully.")
    except AssertionError as e:
        logger.error(f"RST extraction usage example failed: {e}")
    except FileNotFoundError as e:
        logger.error(f"RST extraction usage example failed: {e}")
    except Exception as e:
        logger.error(f"An unexpected error occurred: {e}")


def get_description(node):
    """Extracts description from the preceding paragraph node."""
    try:
        description = ""
        description_start_line = 1

        preceding_element = node.previous_node(siblings=True)
        if isinstance(preceding_element, nodes.paragraph):
            description = preceding_element.astext().strip()
            description_start_line = (
                preceding_element.line if preceding_element.line else 1
            )

        return {"text": description, "line": description_start_line}
    except Exception as e:
        logger.error(f"Error getting description: {e}", exc_info=True)
        return {"text": "", "line": 1}


def process_code_block(
    extracted_data,
    file_path,
    repo_link,
    code_block,
    code_start_line,
    code_end_line,
    code_type,
    description,
    description_start_line,
):
    """Processes a code block and appends its metadata to extracted_data."""
    try:
        encoding = tiktoken.encoding_for_model("gpt-4")
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
    except Exception as e:
        logger.error(f"Error processing code block: {e}", exc_info=True)


def usage_function_sphinx():
    """
    Demonstrates usage of extract_from_rst_sphinx with a sparse checkout directory.
    """
    logger.remove()  # Remove the default handler
    logger.add(
        sys.stderr, level="DEBUG", colorize=True
    )  # Add a handler for the terminal at DEBUG level

    repo_url = "https://github.com/arangodb/python-arango.git"
    repo_dir = "/tmp/python_arango_sparse"  # The sparse checkout directory
    repo_link = "https://github.com/arangodb/python-arango/blob/main/docs/aql.rst"  # Correct repo link
    exclude_patterns = []
    patterns = ["docs/*"]  # Sparse checkout pattern

    # Perform sparse checkout
    success = sparse_checkout(repo_url, repo_dir, patterns)
    if not success:
        logger.error("Sparse checkout failed.")
        raise RuntimeError("Sparse checkout failed.")

    # Explicitly check for aql.rst *AFTER* sparse checkout
    aql_rst_path = os.path.join(repo_dir, "docs", "aql.rst")
    if os.path.exists(aql_rst_path):
        logger.info(f"aql.rst found at: {aql_rst_path}")
    else:
        logger.error("aql.rst NOT found.  Sparse checkout may have failed.")
        raise FileNotFoundError("aql.rst not found after sparse checkout.")

    relevant_files = find_relevant_files(repo_dir, exclude_patterns)

    if not relevant_files:
        logger.error(
            f"No relevant files found in {repo_dir}. Ensure sparse checkout was successful."
        )
        raise FileNotFoundError(f"No relevant files found in {repo_dir}")

    rst_file = None
    for file_path in relevant_files:
        if "docs/aql.rst" in file_path:  # Find the specific aql.rst file
            rst_file = file_path
            break

    if not rst_file:
        logger.error("aql.rst not found in the repository.")
        raise FileNotFoundError("aql.rst not found in the repository.")

    # Pass the sparse checkout directory to extract_from_rst_sphinx
    extracted_data = extract_from_rst_sphinx(rst_file, repo_link, repo_dir)

    if extracted_data:
        logger.info("Extracted data from RST file (Sphinx):")
        json_output = json.dumps(extracted_data, indent=4)
        logger.info(f"\n{json_output}")

        assert len(extracted_data) > 0, (
            "No data extracted despite code blocks being present."
        )
        logger.info("Extraction test passed: Data extracted as expected.")

    else:
        logger.error(
            "No data extracted from RST file (Sphinx), but code blocks were expected."
        )
        raise AssertionError(
            "No data extracted from RST file (Sphinx), but code blocks were expected."
        )


if __name__ == "__main__":
    # Basic usage demonstration
    logger.info("Running RST extraction usage example with Sphinx...")
    try:
        usage_function_sphinx()
        logger.info("RST extraction usage example with Sphinx completed successfully.")
    except AssertionError as e:
        logger.error(f"RST extraction usage example failed: {e}")
    except FileNotFoundError as e:
        logger.error(f"RST extraction usage example failed: {e}")
    except Exception as e:
        logger.error(f"An unexpected error occurred: {e}")
