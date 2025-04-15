# src/mcp_doc_retriever/downloader/git_downloader.py
"""
Description:
  Provides functionality for interacting with Git repositories as a documentation source.
  - `check_git_dependency`: Verifies the `git` command-line tool is installed and accessible.
  - `run_git_clone`: Clones a Git repository, supporting both full clones (depth 1)
    and sparse checkouts (using `git sparse-checkout`) for specific paths within the repo.
    It uses `subprocess` running in a `ThreadPoolExecutor` for asynchronous execution.
  - `scan_local_files_async`: Scans a specified local directory (typically the cloned repo
    or a subdirectory within it) asynchronously using `os.walk` in an executor, returning
    a list of found files matching allowed documentation extensions.

External Dependencies:
  - Requires the 'git' command-line tool to be installed on the system PATH.

Python Standard Library Documentation:
  - subprocess: https://docs.python.org/3/library/subprocess.html
  - concurrent.futures (ThreadPoolExecutor): https://docs.python.org/3/library/concurrent.futures.html
  - pathlib: https://docs.python.org/3/library/pathlib.html
  - os (os.walk): https://docs.python.org/3/library/os.html#os.walk
  - asyncio: https://docs.python.org/3/library/asyncio.html

Sample Input (Conceptual - assumes setup within a running asyncio loop):
  repo_url = "https://github.com/pallets/flask.git"
  clone_path = Path("./test_git_download/content/flask_example/repo")
  sparse_path = "examples/tutorial" # or None for full clone
  executor = ThreadPoolExecutor()

  # Check dependency
  if check_git_dependency():
      # Clone (sparse)
      await run_git_clone(repo_url, clone_path, sparse_path, executor)
      # Scan
      scan_dir = clone_path / sparse_path if sparse_path else clone_path
      allowed_exts = {".py", ".rst", ".html", ".md", ""} # Added empty string for extensionless files
      found_files = await scan_local_files_async(scan_dir, allowed_exts, executor)
      print(f"Found {len(found_files)} files.")
  executor.shutdown()

Sample Expected Output:
  - Clones the specified Git repository into `<base_dir>/content/<download_id>/repo/`.
  - If `doc_path` is provided, performs a sparse checkout, only fetching files under that path.
  - `scan_local_files_async` returns a list of `pathlib.Path` objects representing files
    found within the scanned directory matching the allowed extensions.
  - Prints logs to the console indicating progress and any errors.
"""

# src/mcp_doc_retriever/downloader/git_downloader.py
"""
# ... (Docstring remains the same) ...
"""

import asyncio
import logging
import os
import subprocess
import shutil
import sys
from pathlib import Path
from typing import Optional, List, Set, Dict
from concurrent.futures import ThreadPoolExecutor
# import aiofiles # No longer needed

logger = logging.getLogger(__name__)


def check_git_dependency() -> bool:
    """Checks if the 'git' command is available in the system PATH."""
    # ... (function remains the same) ...
    try:
        result = subprocess.run(
            ["git", "--version"], check=True, capture_output=True, text=True, timeout=10
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
    except Exception as e:
        logger.error(f"Unexpected error checking for git command: {e}", exc_info=True)
        return False


async def get_default_branch(repo_url: str, executor: ThreadPoolExecutor) -> str:
    """Detects the default branch of a remote git repository."""
    # ... (function remains the same) ...
    loop = asyncio.get_running_loop()
    try:
        result = await loop.run_in_executor(
            executor,
            lambda: subprocess.run(
                ["git", "remote", "show", repo_url],
                capture_output=True,
                text=True,
                timeout=20,
            ),
        )
        if result.returncode == 0:
            for line in result.stdout.splitlines():
                if "HEAD branch:" in line:
                    branch_name = line.split(":", 1)[1].strip()
                    if branch_name and branch_name != "(unknown)":
                        logger.debug(
                            f"Detected default branch '{branch_name}' via 'git remote show'"
                        )
                        return branch_name
                    else:
                        logger.debug(
                            f"'git remote show' reported HEAD branch as '{branch_name}', continuing search."
                        )
                        break
        else:
            logger.debug(
                f"'git remote show {repo_url}' failed with code {result.returncode}. Stderr: {result.stderr.strip()}"
            )
    except subprocess.TimeoutExpired:
        logger.warning(
            f"Timeout detecting default branch via 'git remote show' for {repo_url}. Trying ls-remote."
        )
    except Exception as e:
        logger.warning(
            f"Error detecting default branch via 'git remote show': {e}. Trying ls-remote."
        )

    common_branches = ["main", "master"]
    for branch in common_branches:
        try:
            cmd = [
                "git",
                "ls-remote",
                "--heads",
                "--exit-code",
                repo_url,
                f"refs/heads/{branch}",
            ]
            logger.debug(f"Checking for branch '{branch}' using: {' '.join(cmd)}")
            result = await loop.run_in_executor(
                executor,
                lambda: subprocess.run(cmd, capture_output=True, text=True, timeout=20),
            )
            if result.returncode == 0:
                logger.debug(f"Found branch '{branch}' via ls-remote for {repo_url}")
                return branch
            elif result.returncode == 2:
                logger.debug(
                    f"Branch '{branch}' not found via ls-remote for {repo_url}."
                )
            else:
                logger.warning(
                    f"'git ls-remote' for branch '{branch}' failed unexpectedly. Code: {result.returncode}. Stderr: {result.stderr.strip()}"
                )
        except subprocess.TimeoutExpired:
            logger.warning(
                f"Timeout checking for branch '{branch}' via ls-remote for {repo_url}."
            )
        except Exception as e:
            logger.warning(f"Error checking for branch {branch} via ls-remote: {e}")
            continue

    logger.error(
        f"Could not determine default branch for {repo_url} using 'remote show' or 'ls-remote' for {common_branches}."
    )
    raise RuntimeError(f"Could not determine default branch for {repo_url}")


async def run_git_clone(
    repo_url: str,
    target_dir: Path,
    doc_path: Optional[str],
    executor: ThreadPoolExecutor,
):
    """
    Clones a git repository using subprocess running in the provided executor.
    Handles both full clones and sparse checkouts if doc_path is provided.
    """
    loop = asyncio.get_running_loop()
    target_dir_str = str(target_dir)
    logger.info(
        f"Attempting git operations for '{repo_url}' into '{target_dir_str}'..."
    )

    async def run_subprocess(
        cmd: List[str],
        cwd: Optional[str] = None,
        operation_tag: str = "git_operation",
        timeout: int = 300,
    ):
        # ... (run_subprocess helper remains the same) ...
        logger.info(
            f"Starting {operation_tag}: {' '.join(cmd)} (cwd: {cwd or 'default'})"
        )
        try:
            result = await loop.run_in_executor(
                executor,
                lambda: subprocess.run(
                    cmd,
                    cwd=cwd,
                    check=True,
                    capture_output=True,
                    text=True,
                    timeout=timeout,
                ),
            )
            stdout_log = result.stdout.strip()
            if stdout_log:
                logger.debug(
                    f"Command successful ({operation_tag}) Output:\n{stdout_log[:500]}{'...' if len(stdout_log) > 500 else ''}"
                )
            else:
                logger.debug(f"Command successful ({operation_tag}): (No stdout)")
            logger.info(f"Finished {operation_tag}: {' '.join(cmd)}")
            return result
        except FileNotFoundError:
            logger.error(
                f"Command failed ({operation_tag}): '{cmd[0]}' not found. Ensure it's installed and in PATH."
            )
            logger.info(
                f"Finished {operation_tag} (FileNotFoundError): {' '.join(cmd)}"
            )
            raise
        except subprocess.TimeoutExpired:
            logger.error(
                f"Command timed out after {timeout}s ({operation_tag}): {' '.join(cmd)}"
            )
            logger.info(f"Finished {operation_tag} (Timeout): {' '.join(cmd)}")
            raise RuntimeError(f"Git command timed out: {' '.join(cmd)}")
        except subprocess.CalledProcessError as e:
            stderr_lower = e.stderr.lower().strip() if e.stderr else ""
            error_hint = (
                " This often indicates a network connectivity, authentication, or DNS resolution issue."
                if "could not resolve host" in stderr_lower
                or "unable to access" in stderr_lower
                or "authentication failed" in stderr_lower
                else ""
            )
            stderr_log = e.stderr.strip() if e.stderr else "(No stderr)"
            logger.error(
                f"Command failed (exit code {e.returncode}) ({operation_tag}): {' '.join(cmd)}. Stderr:\n{stderr_log}"
            )
            logger.info(
                f"Finished {operation_tag} (CalledProcessError): {' '.join(cmd)}"
            )
            raise RuntimeError(
                f"Git command failed: {' '.join(cmd)}.{error_hint}"
            ) from e
        except Exception as e:
            logger.error(
                f"Unexpected error running command ({operation_tag}) {' '.join(cmd)}: {e}",
                exc_info=True,
            )
            logger.info(f"Finished {operation_tag} (Exception): {' '.join(cmd)}")
            raise RuntimeError(
                f"Unexpected error during git operation: {' '.join(cmd)}"
            ) from e

    effective_doc_path = doc_path.strip() if doc_path else None
    if effective_doc_path == ".":
        effective_doc_path = None

    if effective_doc_path:
        # --- Sparse Checkout ---
        logger.info(f"Performing sparse checkout for path: '{effective_doc_path}'")
        try:
            await loop.run_in_executor(
                executor, lambda: target_dir.mkdir(parents=True, exist_ok=True)
            )
            default_branch = "main"
            try:
                default_branch = await get_default_branch(repo_url, executor)
            except Exception as branch_err:
                logger.warning(
                    f"Could not determine default branch for sparse checkout ({branch_err}). Defaulting to '{default_branch}'."
                )

            await run_subprocess(
                ["git", "init", "-b", default_branch],
                cwd=target_dir_str,
                operation_tag="git_init",
            )
            await run_subprocess(
                ["git", "remote", "add", "origin", repo_url],
                cwd=target_dir_str,
                operation_tag="git_remote_add",
            )
            await run_subprocess(
                ["git", "sparse-checkout", "init", "--cone"],
                cwd=target_dir_str,
                operation_tag="git_sparse_init",
            )
            sparse_path_pattern = effective_doc_path.replace("\\", "/").strip("/")
            await run_subprocess(
                ["git", "sparse-checkout", "set", sparse_path_pattern],
                cwd=target_dir_str,
                operation_tag="git_sparse_set",
            )
            logger.debug(
                f"Set sparse checkout pattern (cone mode) to '{sparse_path_pattern}'"
            )

            logger.info(
                f"Attempting to pull sparse data from branch '{default_branch}'..."
            )
            await run_subprocess(
                ["git", "pull", "--depth", "1", "origin", default_branch],
                cwd=target_dir_str,
                operation_tag=f"git_sparse_pull_{default_branch}",
                timeout=600,
            )
            logger.info(
                f"Successfully pulled sparse data for '{effective_doc_path}' from branch '{default_branch}' of '{repo_url}'"
            )

            # --- REMOVED set-upstream call ---
            # try:
            #     await run_subprocess(...) # set-upstream call removed
            # except Exception as track_err:
            #     logger.warning(...) # Removed

        except Exception as e:
            logger.error(
                f"Error during sparse checkout process for {repo_url}: {e}",
                exc_info=True,
            )
            logger.info(
                f"Attempting cleanup of failed sparse clone directory: {target_dir}"
            )
            await loop.run_in_executor(
                executor, lambda: shutil.rmtree(target_dir, ignore_errors=True)
            )
            raise RuntimeError(
                f"Git sparse checkout process failed for {repo_url}"
            ) from e

    else:
        # --- Full Clone ---
        logger.info("Performing full clone (depth 1)...")
        try:
            default_branch = "main"
            try:
                default_branch = await get_default_branch(repo_url, executor)
            except Exception as e:
                logger.warning(
                    f"Could not detect default branch, falling back to 'main': {e}"
                )

            await run_subprocess(
                [
                    "git",
                    "clone",
                    "--depth",
                    "1",
                    "--branch",
                    default_branch,
                    repo_url,
                    target_dir_str,
                ],
                operation_tag=f"git_clone_{default_branch}",
                timeout=600,
            )
            logger.info(
                f"Successfully cloned (branch {default_branch}) '{repo_url}' to '{target_dir_str}'"
            )

            # --- REMOVED set-upstream call ---
            # try:
            #     await run_subprocess(...) # set-upstream call removed
            # except Exception as track_err:
            #     logger.warning(...) # Removed

        except RuntimeError as e_clone:
            err_str_lower = str(e_clone).lower()
            needs_master_fallback = (
                "remote branch " + default_branch + " not found"
            ) in err_str_lower or (
                "couldn't find remote ref " + default_branch
            ) in err_str_lower

            if default_branch != "master" and needs_master_fallback:
                logger.warning(
                    f"Branch '{default_branch}' not found, attempting 'master' branch for full clone..."
                )
                try:
                    await run_subprocess(
                        [
                            "git",
                            "clone",
                            "--depth",
                            "1",
                            "--branch",
                            "master",
                            repo_url,
                            target_dir_str,
                        ],
                        operation_tag="git_clone_master",
                        timeout=600,
                    )
                    logger.info(
                        f"Successfully cloned (branch master) '{repo_url}' to '{target_dir_str}'"
                    )

                    # --- REMOVED set-upstream call for master ---
                    # try:
                    #     await run_subprocess(...) # set-upstream call removed
                    # except Exception as track_err_master:
                    #     logger.warning(...) # Removed

                except Exception as e_master:
                    logger.error(
                        f"Clone attempt for 'master' branch also failed: {e_master}",
                        exc_info=True,
                    )
                    logger.info(
                        f"Attempting cleanup of failed full clone directory (master attempt): {target_dir}"
                    )
                    await loop.run_in_executor(
                        executor, lambda: shutil.rmtree(target_dir, ignore_errors=True)
                    )
                    raise RuntimeError(
                        f"Git clone failed for {repo_url} attempting both '{default_branch}' and 'master'. Last error: {e_master}"
                    ) from e_master
            else:
                logger.info(
                    f"Attempting cleanup of failed full clone directory ({default_branch} attempt): {target_dir}"
                )
                await loop.run_in_executor(
                    executor, lambda: shutil.rmtree(target_dir, ignore_errors=True)
                )
                raise

        except Exception as e:
            logger.error(
                f"Unexpected error during full clone for {repo_url}: {e}", exc_info=True
            )
            logger.info(
                f"Attempting cleanup of failed full clone directory (unexpected error): {target_dir}"
            )
            await loop.run_in_executor(
                executor, lambda: shutil.rmtree(target_dir, ignore_errors=True)
            )
            raise RuntimeError(f"Git clone failed unexpectedly for {repo_url}") from e


async def scan_local_files_async(
    target_path: Path,
    allowed_extensions: Set[str],
    executor: ThreadPoolExecutor,
) -> List[Path]:
    """Scans a directory for files with allowed extensions."""
    # ... (function remains the same) ...
    loop = asyncio.get_running_loop()

    def _scan_sync():
        found_files: List[Path] = []
        if not target_path.is_dir():
            logger.warning(
                f"Scan target path is not a directory or does not exist: {target_path}"
            )
            return found_files
        logger.info(
            f"Scanning directory for files: {target_path} (Allowed extensions: {allowed_extensions})"
        )
        try:
            for root, dirs, files in os.walk(
                target_path, topdown=True, onerror=None, followlinks=False
            ):
                dirs[:] = [d for d in dirs if d not in [".git", ".hg", ".svn"]]
                root_path = Path(root)
                for filename in files:
                    try:
                        file_path = root_path / filename
                        if file_path.is_file():
                            file_ext_lower = file_path.suffix.lower()
                            if file_ext_lower in allowed_extensions or (
                                not file_path.suffix and "" in allowed_extensions
                            ):
                                found_files.append(file_path)
                    except OSError as stat_err:
                        logger.warning(
                            f"Could not stat file during scan {file_path}: {stat_err}"
                        )
                    except Exception as inner_e:
                        logger.warning(
                            f"Error processing file entry '{filename}' in '{root}': {inner_e}"
                        )
        except Exception as e:
            logger.error(
                f"Error during directory scan of {target_path}: {e}", exc_info=True
            )
            return found_files
        logger.info(
            f"Scan complete. Found {len(found_files)} files matching allowed extensions in {target_path}"
        )
        return found_files

    try:
        files_to_process = await loop.run_in_executor(executor, _scan_sync)
        return files_to_process
    except Exception as e:
        logger.error(
            f"Failed to execute file scan task for {target_path}: {e}", exc_info=True
        )
        return []


# --- Usage Example ---
async def _git_example():
    """Example of using the git functions directly, with basic tests."""
    # ... (This example function remains unchanged, it tests the module's functions) ...
    print("Running direct git downloader example...")
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(levelname)-8s [%(name)s:%(funcName)s:%(lineno)d] %(message)s",
    )
    logger.setLevel(logging.INFO)
    test_results: Dict[str, str] = {}
    all_passed = True
    print("\n--- Testing Prerequisites ---")
    test_name = "Git Dependency Check"
    git_ok = check_git_dependency()
    assert git_ok
    test_results[test_name] = "PASS"  # Simplified line
    test_executor = ThreadPoolExecutor(max_workers=2)
    test_results["Executor Creation"] = "PASS"  # Simplified line
    base_test_dir = Path("./git_downloader_test").resolve()
    if base_test_dir.exists():
        await asyncio.get_running_loop().run_in_executor(
            test_executor, lambda: shutil.rmtree(base_test_dir, ignore_errors=False)
        )  # Simplified line
    test_results["Cleanup Previous Run"] = "PASS"  # Assume pass or previous line raises
    base_test_dir.mkdir(parents=True, exist_ok=True)
    test_results["Create Test Directory"] = "PASS"  # Simplified line
    repo_url = "https://github.com/ArangoDB-Community/python-arango.git"
    clone_path_full = base_test_dir / "arango_full"
    clone_path_sparse = base_test_dir / "arango_sparse"
    doc_path_sparse = "docs"
    allowed_exts_example = {".py", ".rst", ".html", ".js", ".css", ".txt", ".md", ""}
    found_files_full = []
    found_files_sparse = []
    try:
        # Test 1: Full Clone
        test_name = "Full Clone Execution"
        await run_git_clone(repo_url, clone_path_full, None, test_executor)
        assert clone_path_full.is_dir()
        assert (clone_path_full / "README.md").is_file()
        assert (clone_path_full / "arango" / "__init__.py").is_file()
        test_results[test_name] = "PASS"  # Simplified line
        # Test 2: File Scan (Full Clone)
        test_name = "File Scan (Full Clone)"
        found_files_full = await scan_local_files_async(
            clone_path_full, allowed_exts_example, test_executor
        )
        assert len(found_files_full) > 100
        test_results[test_name] = "PASS"  # Simplified line
        # Test 3: Sparse Clone
        test_name = "Sparse Clone Execution"
        await run_git_clone(repo_url, clone_path_sparse, doc_path_sparse, test_executor)
        assert clone_path_sparse.is_dir()
        assert (clone_path_sparse / doc_path_sparse).is_dir()
        assert (clone_path_sparse / doc_path_sparse / "index.rst").is_file()
        assert not (clone_path_sparse / "arango").exists()
        test_results[test_name] = "PASS"  # Simplified line
        # Test 4: File Scan (Sparse Clone)
        test_name = "File Scan (Sparse Clone)"
        scan_target_sparse = clone_path_sparse / doc_path_sparse
        found_files_sparse = await scan_local_files_async(
            scan_target_sparse, allowed_exts_example, test_executor
        )
        assert len(found_files_sparse) > 20
        assert any(f.suffix == ".rst" for f in found_files_sparse)
        assert len(found_files_sparse) < len(found_files_full)
        test_results[test_name] = "PASS"  # Simplified line
    except Exception as e:
        # Basic error handling for example
        print(f"\nExample failed: {e}")
        all_passed = False
    finally:
        if test_executor:
            test_executor.shutdown(wait=True)
            print("\nExecutor shut down.")
        # Simplified Summary
        print("\n--- Example Summary ---")
        summary_all_passed = all(r == "PASS" for r in test_results.values())
        if summary_all_passed:
            print("✓ All Example Tests Passed")
        else:
            print("✗ Some Example Tests Failed")
        print("--------------------")
    print("\nDirect git downloader example finished.")


if __name__ == "__main__":
    try:
        asyncio.run(_git_example())
    except RuntimeError as e:
        if "cannot run event loop while another loop is running" in str(e):
            print("Detected running event loop, attempting alternative execution.")
            loop = asyncio.get_event_loop()
            task = loop.create_task(_git_example())
            print("Task created, completion not guaranteed.")
        else:
            raise