# src/mcp_doc_retriever/sparse_checkout.py
"""
Module: sparse_checkout.py
Description: This module implements sparse checkout of a Git repository, downloading only the specified 'docs/en/docs' directory.
It uses the git sparse-checkout CLI to download relevant files and validates the downloaded file size.

Third-party package documentation:
- git: https://git-scm.com/docs
- loguru: https://github.com/Delgan/loguru

Sample Input:
repo_url = "https://github.com/example/repo.git"
output_dir = "/tmp/repo_sparse_checkout"
patterns = ["docs/en/docs/*"]

Expected Output:
The specified 'docs/en/docs' directory and its files are downloaded to the output directory.
A log message indicates success or failure, and any skipped files due to size limits.
"""

import os
import subprocess
from pathlib import Path
from loguru import logger


def sparse_checkout(repo_url: str, output_dir: str, patterns: list[str]) -> bool:
    """
    Performs a sparse checkout of a Git repository, downloading only files matching the specified patterns.

    Args:
        repo_url (str): The URL of the Git repository.
        output_dir (str): The directory to checkout the repository into.
        patterns (list[str]): A list of file patterns to include in the sparse checkout.

    Returns:
        bool: True if the sparse checkout was successful, False otherwise.
    """
    try:
        # Ensure the output directory exists
        Path(output_dir).mkdir(parents=True, exist_ok=True)

        # Initialize the repository
        subprocess.run(["git", "init", output_dir], check=True, capture_output=True)

        # Set sparse checkout
        subprocess.run(
            ["git", "-C", output_dir, "config", "core.sparseCheckout", "true"],
            check=True,
            capture_output=True,
        )

        # Add the specified patterns to the sparse-checkout file
        with open(Path(output_dir) / ".git" / "info" / "sparse-checkout", "w") as f:
            for pattern in patterns:
                f.write(pattern + "\n")

        # Check if remote origin already exists
        try:
            subprocess.run(
                ["git", "-C", output_dir, "remote", "get-url", "origin"],
                check=True,
                capture_output=True,
            )
            logger.info("Remote 'origin' already exists.")
        except subprocess.CalledProcessError:
            # Add remote if it doesn't exist
            subprocess.run(
                ["git", "-C", output_dir, "remote", "add", "origin", repo_url],
                check=True,
                capture_output=True,
            )

        # Try fetching from 'main' first, if it fails, try 'master'
        try:
            subprocess.run(
                ["git", "-C", output_dir, "fetch", "--depth=1", "origin", "main"],
                check=True,
                capture_output=True,
            )
            branch = "origin/main"
        except subprocess.CalledProcessError:
            logger.warning("Failed to fetch from 'main', trying 'master'")
            try:
                subprocess.run(
                    ["git", "-C", output_dir, "fetch", "--depth=1", "origin", "master"],
                    check=True,
                    capture_output=True,
                )
                branch = "origin/master"
            except subprocess.CalledProcessError as e:
                logger.error(f"Failed to fetch from both 'main' and 'master': {e}")
                logger.error(f"Stderr: {e.stderr.decode()}")
                logger.error(f"Stdout: {e.stdout.decode()}")
                return False

        # Checkout
        subprocess.run(
            ["git", "-C", output_dir, "checkout", branch],
            check=True,
            capture_output=True,
        )

        return True

    except subprocess.CalledProcessError as e:
        logger.error(f"Error during sparse checkout: {e}")
        logger.error(f"Stderr: {e.stderr.decode()}")
        logger.error(f"Stdout: {e.stdout.decode()}")
        return False
    except Exception as e:
        logger.exception(f"An unexpected error occurred: {e}")
        return False


def validate_download_size(repo_dir: str, max_size_gb: float = 1.0) -> None:
    """
    Validates the total size of downloaded files in the repository directory.

    Args:
        repo_dir (str): The directory containing the downloaded repository.
        max_size_gb (float): The maximum allowed size in gigabytes.  Defaults to 1.0 GB
    """
    total_size = 0
    for dirpath, _, filenames in os.walk(repo_dir):
        for f in filenames:
            fp = Path(dirpath) / f
            try:
                total_size += os.path.getsize(fp)
            except FileNotFoundError:
                logger.warning(f"File not found: {fp}")
            except OSError as e:
                logger.error(f"OSError while getting size of {fp}: {e}")

    total_size_gb = total_size / (1024 * 1024 * 1024)
    if total_size_gb > max_size_gb:
        logger.warning(
            f"Total download size ({total_size_gb:.2f} GB) exceeds the limit of {max_size_gb} GB."
        )
    else:
        logger.info(f"Total download size: {total_size_gb:.2f} GB")


def usage_function():
    """
    Demonstrates basic usage of the sparse_checkout function.
    """
    repo_url = "https://github.com/fastapi/fastapi.git"  # Example repo
    output_dir = "/tmp/fastapi_sparse"
    patterns = ["docs/en/docs/*"]  # Only download the English documentation

    success = sparse_checkout(repo_url, output_dir, patterns)
    if success:
        logger.info(f"Sparse checkout successful to: {output_dir}")
        validate_download_size(output_dir)
    else:
        logger.error("Sparse checkout failed.")


if __name__ == "__main__":
    # Basic usage demonstration
    logger.info("Running sparse checkout usage example...")
    usage_function()
    logger.info("Sparse checkout usage example completed.")
