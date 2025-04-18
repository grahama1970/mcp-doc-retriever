# src/mcp_doc_retriever/file_discovery.py
"""
Module: file_discovery.py
Description: This module implements recursive file discovery within a specified directory,
excluding files matching certain patterns.

Third-party package documentation:
- pathlib: https://docs.python.org/3/library/pathlib.html
- loguru: https://github.com/Delgan/loguru
- fnmatch: https://docs.python.org/3/library/fnmatch.html

Sample Input:
repo_dir = "/tmp/fastapi_sparse"
exclude_patterns = ["*archive*", "*archived*", "old", "docs/old", "*deprecated*"]

Expected Output:
A list of file paths within the repo_dir, excluding those matching the exclude_patterns.
A log message indicates whether the discovery was successful and if the expected file is present.

"""

import os
import fnmatch
from pathlib import Path
from typing import List
from loguru import logger


def find_relevant_files(repo_dir: str, exclude_patterns: List[str]) -> List[str]:
    """
    Recursively finds relevant files within a specified directory, excluding those matching
    the provided exclude patterns.

    Args:
        repo_dir (str): The directory to search for files.
        exclude_patterns (List[str]): A list of glob-style patterns to exclude.

    Returns:
        List[str]: A list of file paths that are considered relevant (not excluded).
    """
    relevant_files = []
    for root, _, filenames in os.walk(repo_dir):
        for filename in filenames:
            file_path = Path(root) / filename
            relative_path = file_path.relative_to(
                repo_dir
            )  # Get relative path for matching

            # Check if the file should be excluded
            exclude = False
            for pattern in exclude_patterns:
                if fnmatch.fnmatch(str(relative_path), pattern):
                    logger.debug(
                        f"Excluding file: {file_path} due to pattern: {pattern}"
                    )
                    exclude = True
                    break

            if not exclude:
                relevant_files.append(str(file_path))
            else:
                logger.debug(f"Skipping file: {file_path}")

    return relevant_files


def usage_function():
    """
    Demonstrates basic usage of the find_relevant_files function and includes a simple test.
    """
    repo_dir = "/tmp/fastapi_sparse"  # Replace with a valid directory
    exclude_patterns = ["*archive*", "*archived*", "old", "docs/old", "*deprecated*"]

    relevant_files = find_relevant_files(repo_dir, exclude_patterns)

    if relevant_files:
        logger.info("Relevant files found.")

        # Simple Test: Check if a known file is present in the results
        expected_file = str(
            Path(repo_dir) / "README.md"
        )  # Adjust based on what files you expect
        file_found = expected_file in relevant_files

        if file_found:
            logger.info(f"Test passed: Expected file '{expected_file}' found.")
        else:
            logger.error(f"Test failed: Expected file '{expected_file}' not found.")
            raise AssertionError(f"Expected file '{expected_file}' not found.")
    else:
        logger.info("No relevant files found.")
        raise AssertionError("No relevant files found.")


if __name__ == "__main__":
    # Basic usage demonstration
    logger.info("Running file discovery usage example...")
    try:
        usage_function()
        logger.info("File discovery usage example completed successfully.")
    except AssertionError as e:
        logger.error(f"File discovery usage example failed: {e}")
