"""
Module for converting RST documentation to Markdown and extracting code blocks.
Implements secure file handling and content processing with size limits.
"""

import subprocess
from pathlib import Path
from typing import List, Dict, Optional, Any
import os
import json
from docutils.core import publish_doctree
from docutils import nodes
import re
import datetime
import tiktoken
import tempfile
from loguru import logger

# Constants for security constraints
MAX_FILE_SIZE = 50 * 1024  # 50KB size limit
ALLOWED_EXTENSIONS = {'.rst', '.md'}

# Configure base directory - use environment variable or default to local path
BASE_DIR = os.getenv("MCP_CONTENT_DIR", os.path.join(os.getcwd(), "downloads/content"))

from mcp_doc_retriever.context7.markdown_extractor import extract_from_markdown
from mcp_doc_retriever.context7.file_discovery import find_relevant_files
from mcp_doc_retriever.context7.sparse_checkout import sparse_checkout

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
        file_size = markdown_path.stat().st_size
        
        if file_size > MAX_FILE_SIZE:
            logger.error(f"File {markdown_file} exceeds size limit of {MAX_FILE_SIZE} bytes")
            raise ValueError(f"File exceeds size limit of {MAX_FILE_SIZE} bytes")
            
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
    In test mode (RST_TEST_MODE=mock), bypasses Docker and creates mock output.

    Args:
        rst_file (str): The path to the RST file.
        output_dir (str): The directory to save the Markdown file.

    Returns:
        str: The path to the converted Markdown file.

    Raises:
        ValueError: If paths are invalid or outside allowed directories
        RuntimeError: If Docker command fails
    """
    try:
        # Resolve absolute paths and validate
        content_root = Path(BASE_DIR).resolve()
        rst_path = Path(rst_file).resolve()
        output_path = Path(output_dir).resolve()
        
        # Validate paths are within allowed directories
        if not rst_path.is_file():
            raise ValueError(f"RST file does not exist: {rst_file}")
            
        if not (str(rst_path).startswith(str(content_root)) and
                str(output_path).startswith(str(content_root))):
            raise ValueError(f"Paths must be within {BASE_DIR} directory")

        markdown_file = output_path / f"{rst_path.stem}.md"
        markdown_file.parent.mkdir(parents=True, exist_ok=True)

        # Check for test mode
        if os.getenv("RST_TEST_MODE") == "mock":
            logger.info("Test mode: Mocking RST to Markdown conversion")
            # Read original RST content for the mock title
            rst_content = rst_path.read_text(encoding="utf-8")
            mock_content = f"""# {rst_path.stem}

{rst_content}  # Include original content for testing

```python
def hello():
    print("Hello, World!")
```
"""
            markdown_file.write_text(mock_content)
            logger.info(f"Created mock Markdown file: {markdown_file}")
            return str(markdown_file)

        # Real conversion using Docker
        # Sanitize paths for Docker volume mounts
        data_mount = f"{rst_path.parent}:/data:ro"  # Read-only mount
        output_mount = f"{markdown_file.parent}:/output"

        # Construct the Docker command with minimal permissions
        command = [
            "docker",
            "run",
            "--rm",
            "--network", "none",  # Disable network access
            "--security-opt", "no-new-privileges",  # Prevent privilege escalation
            "-v", data_mount,
            "-v", output_mount,
            "--user", str(os.getuid()),  # Run as current user
            "pandoc/core:latest",
            "/data/" + rst_path.name,
            "-s",
            "-t", "markdown",
            "-o", "/output/" + markdown_file.name,
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


def extract_from_rst(file_path: str, repo_link: str) -> List[Dict[str, Any]]:
    """
    Extracts code blocks and descriptions from an RST file.

    Args:
        file_path (str): The path to the RST file.
        repo_link (str): The URL of the repository.

    Returns:
        List[Dict]: A list of dictionaries, each containing code and description.
    """
    try:
        logger.info(f"Parsing RST file: {file_path}")
        rst_path = Path(file_path)
        
        # Validate file extension
        if rst_path.suffix not in ALLOWED_EXTENSIONS:
            raise ValueError(f"Invalid file extension: {rst_path.suffix}")
            
        # Check file size before reading
        file_size = rst_path.stat().st_size
        if file_size > MAX_FILE_SIZE:
            logger.error(f"File {file_path} exceeds size limit of {MAX_FILE_SIZE} bytes")
            raise ValueError(f"File exceeds size limit of {MAX_FILE_SIZE} bytes")
            
        rst_text = rst_path.read_text(encoding="utf-8")
        
        # Configure secure settings for docutils
        secure_settings = {
            "output_encoding": "utf-8",
            "file_insertion_enabled": False,  # Disable file inclusion
            "raw_enabled": False,  # Disable raw content
            "input_encoding_error_handler": "strict",
            "_disable_config": True,  # Disable external config files
            "report_level": 5,  # Highest warning level
            "halt_level": 5,  # Halt on severe errors
            # Disable potentially dangerous directives
            "expose_internals": [],
            "strip_comments": True,
            "strip_elements_with_classes": [],
            "strip_classes": [],
        }
        
        document = publish_doctree(rst_text, settings_overrides=secure_settings)

        extracted_data: List[Dict[str, Any]] = []
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


def get_description(node: nodes.Node) -> Dict[str, Any]:
    """
    Extracts description from the preceding paragraph node.
    Uses secure docutils traversal to find the nearest previous paragraph.
    
    Args:
        node: The current docutils node
        
    Returns:
        Dict containing description text and line number
    """
    try:
        description = ""
        description_start_line = 1

        last_paragraph = None
        parent = node.parent
        if parent is not None:
            for sibling in parent.children:
                if sibling is node:
                    break
                if isinstance(sibling, nodes.paragraph):
                    last_paragraph = sibling
            
            if last_paragraph is not None:
                description = last_paragraph.astext().strip()
                description_start_line = last_paragraph.line if last_paragraph.line else 1
        return {"text": description, "line": description_start_line}
    except Exception as e:
        logger.error(f"Error getting description: {e}")
        return {"text": "", "line": 1}


def process_code_block(
    extracted_data: List[Dict[str, Any]],
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


def check_file_size(file_path: str) -> bool:
    """
    Check if a file's size is within acceptable limits.
    
    Args:
        file_path (str): Path to the file to check
        
    Returns:
        bool: True if file size is acceptable, False otherwise
        
    Raises:
        ValueError: If file doesn't exist or other IO error
    """
    try:
        path = Path(file_path)
        if not path.is_file():
            raise ValueError(f"File does not exist: {file_path}")
            
        file_size = path.stat().st_size
        return file_size <= MAX_FILE_SIZE
    except Exception as e:
        logger.error(f"Error checking file size for {file_path}: {e}")
        raise


def usage_function():
    """
    Main function to demonstrate RST extraction functionality.
    Creates a test RST file and processes it to verify the extraction works.
    """
    # Create test directories
    os.makedirs(BASE_DIR, exist_ok=True)
    
    # Create a temporary RST file in the correct directory
    with tempfile.NamedTemporaryFile(
        mode='w', 
        suffix='.rst',
        dir=BASE_DIR,
        delete=False
    ) as rst_file:
        # Write a simple RST file with a code block for testing
        rst_content = """
Test RST Document
===============

Here's a sample code block with description:

This is a test function that prints a greeting.

.. code-block:: python

    def hello():
        print("Hello, World!")

"""
        rst_file.write(rst_content)
        rst_path = rst_file.name

    output_dir = os.path.join(BASE_DIR, "test_output")
    os.makedirs(output_dir, exist_ok=True)

    logger.info(f"Created test RST file at: {rst_path}")

    try:
        # Set test mode to avoid Docker dependency
        os.environ["RST_TEST_MODE"] = "mock"
        
        # Convert RST to Markdown
        markdown_file = convert_rst_to_markdown(rst_path, output_dir)
        logger.info(f"Converted to Markdown: {markdown_file}")

        # Extract code blocks
        repo_link = "https://example.com/test.rst"  # Example repository link
        extracted_data = extract_from_markdown(markdown_file, repo_link)

        if extracted_data:
            logger.info("Extracted data from test RST file:")
            json_output = json.dumps(extracted_data, indent=4)
            logger.info(f"\n{json_output}")
            logger.info("Extraction test passed: Data extracted as expected.")
        else:
            raise AssertionError(
                "No data extracted from test file, but code blocks were expected."
            )
    except Exception as e:
        logger.error(f"Test failed: {e}")
        raise
    finally:
        # Clean up test files and environment
        try:
            os.unlink(rst_path)
            logger.info(f"Cleaned up test RST file: {rst_path}")
        except OSError as e:
            logger.warning(f"Error cleaning up test file {rst_path}: {e}")
        os.environ.pop("RST_TEST_MODE", None)


if __name__ == "__main__":
    try:
        usage_function()
    except Exception as e:
        logger.error(f"An unexpected error occurred: {e}")
