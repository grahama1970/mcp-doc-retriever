# src/mcp_doc_retriever/context7/rst_extractor_pandoc.py
import subprocess
from pathlib import Path
from typing import List, Dict
from mcp_doc_retriever.context7.markdown_extractor import extract_from_markdown
from mcp_doc_retriever.context7.file_discovery import find_relevant_files
from mcp_doc_retriever.context7.sparse_checkout import sparse_checkout
from loguru import logger
import os
import json
from docutils.core import publish_doctree
from docutils import nodes
import re
import datetime
import tiktoken


def preprocess_markdown(markdown_file: str) -> str:
    """
    Preprocesses a Markdown file to replace Pandoc's `::: testcode` fences with standard ```python fences.

    Args:
        markdown_file (str): Path to the Markdown file.

    Returns:
        str: Path to the preprocessed Markdown file.
    """
    try:
        markdown_path = Path(markdown_file)
        content = markdown_path.read_text(encoding="utf-8")

        # Log the original Markdown content for debugging
        logger.debug(
            f"Original Markdown content:\n{content[:500]}..."
        )  # Truncate for brevity

        # Replace `::: testcode` with ```python and `:::` with ```
        content = content.replace("::: testcode", "```python").replace(":::", "```")

        # Write the preprocessed content back to the same file
        markdown_path.write_text(content, encoding="utf-8")

        logger.info(f"Preprocessed Markdown file: {markdown_file}")
        logger.debug(
            f"Preprocessed Markdown content:\n{content[:500]}..."
        )  # Truncate for brevity

        return str(markdown_path)
    except Exception as e:
        logger.error(f"Error preprocessing Markdown file {markdown_file}: {e}")
        raise


def convert_rst_to_markdown(rst_file: str, output_dir: str) -> str:
    """
    Converts an RST file to Markdown using Pandoc inside a Docker container.

    Args:
        rst_file (str): The path to the RST file.
        output_dir (str): The directory to save the Markdown file.

    Returns:
        str: The path to the converted Markdown file.
    """
    try:
        rst_path = Path(rst_file)
        markdown_file = Path(output_dir) / f"{rst_path.stem}.md"
        markdown_file.parent.mkdir(parents=True, exist_ok=True)

        # Construct the Docker command
        command = [
            "docker",
            "run",
            "--rm",
            "-v",
            f"{os.path.dirname(rst_path)}:/data",
            "-v",
            f"{os.path.dirname(markdown_file)}:/output",
            "pandoc/core:latest",
            "/data/" + os.path.basename(rst_path),
            "-s",
            "-t",
            "markdown",
            "-o",
            "/output/" + os.path.basename(markdown_file),
        ]

        logger.info(f"Executing command: {' '.join(command)}")

        process = subprocess.run(command, capture_output=True, text=True, check=True)

        logger.info(f"Command completed with return code: {process.returncode}")
        logger.debug(f"Standard Output:\n{process.stdout}")
        logger.debug(f"Standard Error:\n{process.stderr}")

        # Preprocess the Markdown file to fix code block fences
        preprocessed_file = preprocess_markdown(str(markdown_file))

        return preprocessed_file
    except subprocess.CalledProcessError as e:
        logger.error(f"Error converting RST to Markdown: {e}")
        logger.error(f"Command: {' '.join(e.cmd)}")
        logger.error(f"Return Code: {e.returncode}")
        logger.error(f"Standard Output:\n{e.stdout}")
        logger.error(f"Standard Error:\n{e.stderr}")
        raise
    except FileNotFoundError:
        logger.error("Docker is not installed or Pandoc Docker image not found.")
        raise
    except Exception as e:
        logger.error(f"An unexpected error occurred: {e}")
        raise


def extract_code_blocks_regex(rst_text: str) -> List[str]:
    """
    Extracts code blocks from RST text using regular expressions.

    Args:
        rst_text (str): The RST text to extract code blocks from.

    Returns:
        List[str]: A list of extracted code blocks.
    """
    try:
        code_blocks = re.findall(
            r".. code-block:: \w+\n\s+(.*?)\n(?=\S)", rst_text, re.DOTALL
        )
        testcode_blocks = re.findall(
            r".. testcode:: \w*\n\s+(.*?)\n(?=\S)", rst_text, re.DOTALL
        )
        all_blocks = code_blocks + testcode_blocks
        return all_blocks
    except Exception as e:
        logger.error(f"Error extracting code blocks using regex: {e}")
        return []


def extract_from_rst(file_path: str, repo_link: str) -> List[Dict]:
    """
    Extracts code blocks and descriptions from an RST file, handling different code block types.

    Args:
        file_path (str): The path to the RST file.
        repo_link (str): The URL of the repository.

    Returns:
        List[Dict]: A list of dictionaries, each containing code and description.
    """
    try:
        logger.info(f"Parsing RST file: {file_path}")
        rst_text = Path(file_path).read_text(encoding="utf-8")
        document = publish_doctree(
            rst_text, settings_overrides={"output_encoding": "utf-8"}
        )

        extracted_data: List[Dict] = []
        encoding = tiktoken.encoding_for_model("gpt-4")

        for node in document.traverse():
            if isinstance(node, nodes.literal_block):
                code_type = "python"  # Default to Python

                if isinstance(
                    node.parent, nodes.container
                ) and "code-block" in node.parent.get("classes", []):
                    logger.info("Found code-block directive.")
                    try:
                        code_block = node.astext().strip()
                        code_start_line = node.line if node.line else 1
                        code_end_line = code_start_line + code_block.count("\n")
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
                        logger.error(f"Error processing code-block block: {e}")

                elif isinstance(
                    node.parent, nodes.container
                ) and "testcode" in node.parent.get("classes", []):
                    logger.info("Found testcode directive.")
                    try:
                        code_block = node.astext().strip()
                        code_start_line = node.line if node.line else 1
                        code_end_line = code_start_line + code_block.count("\n")
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
                        logger.error(f"Error processing testcode block: {e}")

        regex_code_blocks = extract_code_blocks_regex(rst_text)
        for code_block in regex_code_blocks:
            if not any(d["code"] == code_block for d in extracted_data):
                logger.info("Found code block using regex.")
                try:
                    code_start_line = 1
                    code_end_line = code_start_line + code_block.count("\n")
                    code_type = "python"
                    description_text = ""
                    description_start_line = 1

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
                    logger.error(f"Error processing regex-extracted block: {e}")

        logger.info(f"Extracted {len(extracted_data)} code blocks.")
        return extracted_data
    except Exception as e:
        logger.error(f"Error parsing RST file: {e}")
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
        logger.error(f"Error getting description: {e}")
        return {"text": "", "line": 1}


def process_code_block(
    extracted_data: List[Dict],
    file_path: str,
    repo_link: str,
    code_block: str,
    code_start_line: int,
    code_end_line: int,
    code_type: str,
    description: str,
    description_start_line: int,
):
    """Processes a code block and appends its metadata to extracted_data."""
    try:
        encoding = tiktoken.encoding_for_model("gpt-4")
        code_token_count = len(encoding.encode(code_block))
        description_token_count = len(encoding.encode(description))
        code_metadata = {"language": code_type}  # Simplified for brevity

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
        logger.error(f"Error processing code block: {e}")


def usage_function():
    """
    Main function to convert RST files to Markdown and extract code blocks.
    """
    repo_url = "https://github.com/arangodb/python-arango.git"
    repo_dir = "/tmp/python_arango_sparse"
    repo_link = "https://github.com/arangodb/python-arango/blob/main/docs/aql.rst"
    exclude_patterns = []
    patterns = ["docs/*"]
    output_dir = "/tmp/markdown_output"

    success = sparse_checkout(repo_url, repo_dir, patterns)
    if not success:
        logger.error("Sparse checkout failed.")
        raise RuntimeError("Sparse checkout failed.")

    relevant_files = find_relevant_files(repo_dir, exclude_patterns)

    if not relevant_files:
        logger.error(f"No relevant files found in {repo_dir}.")
        raise FileNotFoundError(f"No relevant files found in {repo_dir}")

    rst_file = None
    for file_path in relevant_files:
        if "docs/aql.rst" in file_path:
            rst_file = file_path
            break

    if not rst_file:
        logger.error("aql.rst not found in the repository.")
        raise FileNotFoundError("aql.rst not found in the repository.")

    try:
        markdown_file = convert_rst_to_markdown(rst_file, output_dir)
    except Exception as e:
        logger.error(f"Conversion failed: {e}")
        raise

    try:
        extracted_data = extract_from_markdown(markdown_file, repo_link)
    except ImportError as e:
        logger.error(f"Error importing extract_from_markdown: {e}")
        raise
    except Exception as e:
        logger.error(f"Error extracting from Markdown: {e}")
        raise

    if extracted_data:
        logger.info("Extracted data from Markdown file:")
        json_output = json.dumps(extracted_data, indent=4)
        logger.info(f"\n{json_output}")
        logger.info("Extraction test passed: Data extracted as expected.")
    else:
        logger.error(
            "No data extracted from Markdown file, but code blocks were expected."
        )
        raise AssertionError(
            "No data extracted from Markdown file, but code blocks were expected."
        )


if __name__ == "__main__":
    try:
        usage_function()
    except Exception as e:
        logger.error(f"An unexpected error occurred: {e}")
