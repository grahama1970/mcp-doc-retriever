"""
Module: git_downloader.py

Description:
Contains functions specifically related to handling 'git' source types.
This includes checking for the 'git' command dependency, cloning repositories
(including sparse checkout) using subprocess, and scanning local directories
for documentation files asynchronously.
"""

import asyncio
import logging
import os
import subprocess
import shutil
from pathlib import Path
from typing import Optional, List, Set
from concurrent.futures import ThreadPoolExecutor
import aiofiles  # For async file writing (sparse-checkout file)

logger = logging.getLogger(__name__)


def check_git_dependency() -> bool:
    """Checks if the 'git' command is available in the system PATH."""
    try:
        # Use --version as a simple, non-intrusive check
        result = subprocess.run(
            ["git", "--version"], check=True, capture_output=True, text=True, timeout=5
        )
        logger.info(f"Git command found: {result.stdout.strip()}")
        return True
    except FileNotFoundError:
        logger.error(
            "Git command ('git') not found. Please ensure Git is installed and accessible in your system's PATH."
        )
        return False
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
        logger.error(f"Error while checking for git command: {e}")
        return False


async def run_git_clone(
    repo_url: str,
    target_dir: Path,
    doc_path: Optional[str],
    executor: ThreadPoolExecutor,
):
    """
    Clones a git repository using subprocess running in the provided executor.
    Handles both full clones and sparse checkouts if doc_path is provided.

    Args:
        repo_url: The URL of the git repository.
        target_dir: The local Path where the repository should be cloned.
        doc_path: Optional path within the repo for sparse checkout.
        executor: The ThreadPoolExecutor to run sync subprocess calls.
    """
    loop = asyncio.get_running_loop()
    target_dir_str = str(target_dir)  # subprocess usually needs string paths
    logger.info(f"Attempting to clone '{repo_url}' into '{target_dir_str}'...")

    # Define helper for running subprocess in executor
    async def run_subprocess(cmd: List[str], cwd: Optional[str] = None):
        logger.debug(
            f"Running command in executor: {' '.join(cmd)} (cwd: {cwd or 'default'})"
        )
        try:
            # Run the synchronous subprocess call within the executor thread
            result = await loop.run_in_executor(
                executor,
                lambda: subprocess.run(
                    cmd,
                    cwd=cwd,
                    check=True,
                    capture_output=True,
                    text=True,
                    timeout=300,  # 5 min timeout for git ops
                ),
            )
            logger.debug(
                f"Command successful: {' '.join(cmd)}. Output:\n{result.stdout[:500]}..."
            )  # Log truncated stdout
            return result
        except FileNotFoundError:
            logger.error(
                f"Command failed: '{cmd[0]}' not found. Ensure it's installed and in PATH."
            )
            raise  # Re-raise FileNotFoundError
        except subprocess.TimeoutExpired:
            logger.error(f"Command timed out: {' '.join(cmd)}")
            raise RuntimeError(f"Git command timed out: {' '.join(cmd)}")
        except subprocess.CalledProcessError as e:
            # Log stderr for detailed error info from git
            logger.error(f"Command failed: {' '.join(cmd)}. Stderr:\n{e.stderr}")
            raise RuntimeError(f"Git command failed: {' '.join(cmd)}") from e
        except Exception as e:
            # Catch other potential errors during executor run
            logger.error(
                f"Unexpected error running command {' '.join(cmd)}: {e}", exc_info=True
            )
            raise RuntimeError(
                f"Unexpected error during git operation: {' '.join(cmd)}"
            ) from e

    if doc_path:
        # --- Sparse Checkout ---
        logger.info(f"Performing sparse checkout for path: '{doc_path}'")
        try:
            # Ensure target directory exists for sparse checkout initialization
            await loop.run_in_executor(
                executor, target_dir.mkdir, {"parents": True, "exist_ok": True}
            )

            # 1. Init repo, add remote, set sparse checkout config
            await run_subprocess(["git", "init"], cwd=target_dir_str)
            await run_subprocess(
                ["git", "remote", "add", "origin", repo_url], cwd=target_dir_str
            )
            await run_subprocess(
                ["git", "config", "core.sparseCheckout", "true"], cwd=target_dir_str
            )

            # 2. Define the sparse-checkout path(s) in .git/info/sparse-checkout
            sparse_checkout_file = target_dir / ".git" / "info" / "sparse-checkout"
            # Ensure parent directory exists asynchronously (though git init likely creates .git)
            await loop.run_in_executor(
                executor,
                sparse_checkout_file.parent.mkdir,
                {"parents": True, "exist_ok": True},
            )

            # Prepare pattern: use forward slashes, ensure ends with '/' for directory
            sparse_path_pattern = doc_path.strip().replace("\\", "/")
            if not sparse_path_pattern.endswith("/"):
                sparse_path_pattern += (
                    "/"  # Treat as directory pattern unless it's a specific file
                )

            async with aiofiles.open(sparse_checkout_file, "w", encoding="utf-8") as f:
                await f.write(f"{sparse_path_pattern}\n")
                # Optionally add specific root files: await f.write("README.md\n")
            logger.debug(
                f"Wrote sparse checkout pattern '{sparse_path_pattern}' to {sparse_checkout_file}"
            )

            # 3. Pull the required data (try common default branches)
            # Consider adding --depth 1 for faster initial checkout
            common_branches = ["main", "master"]  # Add more if needed
            pull_successful = False
            for branch in common_branches:
                try:
                    logger.info(
                        f"Attempting to pull sparse data from branch '{branch}'..."
                    )
                    await run_subprocess(
                        ["git", "pull", "--depth", "1", "origin", branch],
                        cwd=target_dir_str,
                    )
                    pull_successful = True
                    logger.info(
                        f"Successfully sparse-cloned '{doc_path}' from branch '{branch}' of '{repo_url}'"
                    )
                    break  # Exit loop on success
                except RuntimeError as e:
                    logger.warning(
                        f"Failed to pull sparse data from branch '{branch}': {e}. Trying next branch..."
                    )
                    # If the error is specific to the branch not existing, that's expected.
                    # Other errors might indicate bigger problems.

            if not pull_successful:
                logger.error(
                    f"Failed to pull sparse data from any common branch ({common_branches}) for '{repo_url}'."
                )
                raise RuntimeError(
                    f"Could not pull sparse data for {doc_path} from {repo_url}"
                )

        except Exception as e:
            logger.error(
                f"Error during sparse checkout process for {repo_url}: {e}",
                exc_info=True,
            )
            # Clean up partially created directory on failure? Optional.
            # shutil.rmtree(target_dir, ignore_errors=True)
            raise RuntimeError(
                f"Git sparse checkout process failed for {repo_url}"
            ) from e

    else:
        # --- Full Clone ---
        logger.info("Performing full clone (depth 1)...")
        try:
            await run_subprocess(
                ["git", "clone", "--depth", "1", repo_url, target_dir_str]
            )
            logger.info(f"Successfully cloned '{repo_url}' to '{target_dir_str}'")
        except Exception as e:
            # Error already logged by run_subprocess
            # Clean up partially created directory on failure? Optional.
            # shutil.rmtree(target_dir, ignore_errors=True)
            raise RuntimeError(f"Git clone failed for {repo_url}") from e


async def scan_local_files_async(
    target_path: Path, allowed_extensions: Set[str], executor: ThreadPoolExecutor
) -> List[Path]:
    """
    Asynchronously scans a directory for files with allowed extensions using os.walk
    run in the provided ThreadPoolExecutor.

    Args:
        target_path: The directory Path to scan.
        allowed_extensions: A set of lower-case file extensions (e.g., {'.md', '.html'}).
        executor: The ThreadPoolExecutor to run the synchronous scan.

    Returns:
        A list of Path objects for the found files.
    """
    loop = asyncio.get_running_loop()

    def _scan_sync():
        """The synchronous scanning function to run in the executor."""
        found_files = []
        if not target_path.is_dir():
            logger.warning(
                f"Scan target path is not a directory or does not exist: {target_path}"
            )
            return []  # Return empty list if path is invalid

        logger.info(f"Scanning directory for documentation files: {target_path}...")
        try:
            # os.walk is generally efficient for traversing large directory trees
            for root, _, files in os.walk(target_path):
                root_path = Path(root)
                for file in files:
                    file_path = root_path / file
                    # Check suffix efficiently
                    if file_path.suffix.lower() in allowed_extensions:
                        found_files.append(file_path)
        except Exception as e:
            # Catch errors during the walk itself (e.g., permission errors)
            logger.error(
                f"Error during directory scan of {target_path}: {e}", exc_info=True
            )
            # May return partially found files or an empty list depending on when error occurred
            return found_files

        logger.info(
            f"Scan complete. Found {len(found_files)} files with allowed extensions in {target_path}"
        )
        return found_files

    # Run the synchronous function in the executor
    try:
        files_to_process = await loop.run_in_executor(executor, _scan_sync)
        return files_to_process
    except Exception as e:
        # Catch errors related to running the task in the executor itself
        logger.error(
            f"Failed to execute file scan task for {target_path}: {e}", exc_info=True
        )
        return []  # Return empty list on executor failure


# --- Usage Example (if run directly) ---
async def _git_example():
    """Example of using the git functions directly."""
    print("Running direct git downloader example...")
    logging.basicConfig(
        level=logging.DEBUG,
        format="%(asctime)s - %(levelname)s - [%(name)s:%(funcName)s:%(lineno)d] - %(message)s",
    )

    if not check_git_dependency():
        print("Git dependency check failed. Aborting example.")
        return

    test_executor = ThreadPoolExecutor(max_workers=2)
    base_test_dir = Path("./git_downloader_test")
    # Clean up previous runs
    if base_test_dir.exists():
        print(f"Removing previous test directory: {base_test_dir}")
        shutil.rmtree(base_test_dir)
    base_test_dir.mkdir()

    repo_url = "https://github.com/pallets/flask.git"  # Example repo
    clone_path_full = base_test_dir / "flask_full"
    clone_path_sparse = base_test_dir / "flask_sparse"
    doc_path_sparse = "examples/tutorial"  # Example path for sparse checkout

    try:
        print("\n--- Testing Full Clone ---")
        await run_git_clone(repo_url, clone_path_full, None, test_executor)
        print(f"Full clone completed in: {clone_path_full}")

        print("\n--- Testing File Scan (Full Clone) ---")
        allowed_exts = {".py", ".rst", ".html"}  # Example extensions
        found_files_full = await scan_local_files_async(
            clone_path_full, allowed_exts, test_executor
        )
        print(f"Found {len(found_files_full)} files in full clone.")
        # print("Sample files:", found_files_full[:5])

        print("\n--- Testing Sparse Clone ---")
        await run_git_clone(repo_url, clone_path_sparse, doc_path_sparse, test_executor)
        print(f"Sparse clone completed in: {clone_path_sparse}")

        print("\n--- Testing File Scan (Sparse Clone) ---")
        # Scan the specific doc_path within the sparse clone
        scan_target_sparse = clone_path_sparse / doc_path_sparse
        found_files_sparse = await scan_local_files_async(
            scan_target_sparse, allowed_exts, test_executor
        )
        print(
            f"Found {len(found_files_sparse)} files in sparse clone path '{doc_path_sparse}'."
        )
        # print("Sample files:", found_files_sparse[:5])

    except Exception as e:
        print(f"Git downloader example failed: {e}")
        logger.error("Example failed", exc_info=True)
    finally:
        print("Shutting down executor...")
        test_executor.shutdown()
        print("Executor shut down.")
        # Optionally leave the test dir for inspection or remove it:
        # print(f"Removing test directory: {base_test_dir}")
        # shutil.rmtree(base_test_dir, ignore_errors=True)
    print("Direct git downloader example finished.")


if __name__ == "__main__":
    # Example of how to run the git functions directly (mainly for testing)
    asyncio.run(_git_example())
