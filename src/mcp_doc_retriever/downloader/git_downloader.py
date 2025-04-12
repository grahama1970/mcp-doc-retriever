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
      allowed_exts = {".py", ".rst", ".html", ".md"}
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

import asyncio
import logging
import os
import subprocess
import shutil
import sys  # Added sys import for exiting on failure
from pathlib import Path
from typing import Optional, List, Set, Dict  # Added Dict for type hints
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
    except Exception as e:
        logger.error(f"Unexpected error checking for git command: {e}", exc_info=True)
        return False


async def run_git_clone(
    repo_url: str,
    target_dir: Path,
    doc_path: Optional[str],
    executor: ThreadPoolExecutor,  # This argument name is fine, matches the calls below
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
                executor,  # Use the passed executor argument
                lambda: subprocess.run(
                    cmd,
                    cwd=cwd,
                    check=True,  # Raises CalledProcessError on non-zero exit
                    capture_output=True,  # Capture stdout/stderr
                    text=True,  # Decode stdout/stderr as text
                    timeout=300,  # 5 min timeout for git ops
                ),
            )
            # Log truncated stdout for successful commands at debug level
            stdout_log = result.stdout.strip()
            if stdout_log:
                logger.debug(
                    f"Command successful: {' '.join(cmd)}. Output:\n{stdout_log[:500]}{'...' if len(stdout_log) > 500 else ''}"
                )
            else:
                logger.debug(f"Command successful: {' '.join(cmd)}. (No stdout)")

            return result
        except FileNotFoundError:
            # Error if the command itself (e.g., 'git') isn't found
            logger.error(
                f"Command failed: '{cmd[0]}' not found. Ensure it's installed and in PATH."
            )
            raise  # Re-raise FileNotFoundError for higher-level handling
        except subprocess.TimeoutExpired:
            logger.error(f"Command timed out after 300s: {' '.join(cmd)}")
            raise RuntimeError(f"Git command timed out: {' '.join(cmd)}")
        except subprocess.CalledProcessError as e:
            # Error if the command runs but returns a non-zero exit code
            stderr_lower = e.stderr.lower().strip() if e.stderr else ""
            # Check for specific network errors for better user feedback
            error_hint = ""
            if (
                "could not resolve host" in stderr_lower
                or "unable to access" in stderr_lower
            ):
                error_hint = " This often indicates a network connectivity or DNS resolution issue."

            # Log detailed error info from stderr
            stderr_log = e.stderr.strip() if e.stderr else "(No stderr)"
            logger.error(
                f"Command failed (exit code {e.returncode}): {' '.join(cmd)}. Stderr:\n{stderr_log}"
            )
            # Raise a runtime error including the hint
            raise RuntimeError(
                f"Git command failed: {' '.join(cmd)}.{error_hint}"
            ) from e
        except Exception as e:
            # Catch other potential errors during executor run or subprocess interaction
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
            # Running synchronous mkdir in executor
            await loop.run_in_executor(
                executor, lambda: target_dir.mkdir(parents=True, exist_ok=True)
            )

            # 1. Init repo, add remote, set sparse checkout config
            # Specify initial branch name (e.g., main) for git init if known/desired
            # Using '-b main' might avoid issues if default branch name differs later
            await run_subprocess(["git", "init", "-b", "main"], cwd=target_dir_str)
            await run_subprocess(
                ["git", "remote", "add", "origin", repo_url], cwd=target_dir_str
            )
            # Enable sparse checkout functionality (core config is less common now)
            # await run_subprocess(
            #    ["git", "config", "core.sparseCheckout", "true"], cwd=target_dir_str
            # )
            # Recommended: Use cone mode via sparse-checkout command
            await run_subprocess(
                ["git", "sparse-checkout", "init", "--cone"], cwd=target_dir_str
            )

            # 2. Define the sparse-checkout path(s) using sparse-checkout set
            # Prepare pattern: use forward slashes, no trailing slash needed for cone mode dirs
            sparse_path_pattern = doc_path.strip().replace("\\", "/")
            # Remove leading/trailing slashes for 'set' command consistency
            sparse_path_pattern = sparse_path_pattern.strip("/")

            # Set the desired path(s) - overwrites previous set
            await run_subprocess(
                ["git", "sparse-checkout", "set", sparse_path_pattern],
                cwd=target_dir_str,
            )
            logger.debug(
                f"Set sparse checkout pattern (cone mode) to '{sparse_path_pattern}'"
            )

            # 3. Pull the required data (try common default branches)
            common_branches = [
                "main",
                "master",
            ]  # Add more if needed ('dev', 'develop', etc.)
            pull_successful = False
            last_pull_error = None
            for branch in common_branches:
                try:
                    logger.info(
                        f"Attempting to pull sparse data from branch '{branch}'..."
                    )
                    # Pull only the specified branch, depth 1 for speed
                    # --no-tags might speed up slightly if tags aren't needed
                    await run_subprocess(
                        [
                            "git",
                            "pull",
                            "--depth",
                            "1",
                            "origin",
                            branch,
                        ],  # Removed --no-tags unless needed
                        cwd=target_dir_str,
                    )
                    pull_successful = True
                    logger.info(
                        f"Successfully pulled sparse data for '{doc_path}' from branch '{branch}' of '{repo_url}'"
                    )
                    # Set upstream tracking for the checked out branch (optional but good practice)
                    try:
                        await run_subprocess(
                            [
                                "git",
                                "branch",
                                f"--set-upstream-to=origin/{branch}",
                                branch,
                            ],
                            cwd=target_dir_str,
                        )
                    except Exception as track_err:
                        logger.warning(
                            f"Could not set upstream tracking for branch {branch}: {track_err}"
                        )

                    break  # Exit loop on success
                except RuntimeError as e:
                    last_pull_error = e  # Store the last error encountered
                    # Check if error is specifically about the branch not existing
                    err_str_lower = str(e).lower()
                    if (
                        "couldn't find remote ref" in err_str_lower
                        or "couldn't find remote object" in err_str_lower
                        or "repository not found" in err_str_lower
                        or "does not appear to be a git repository" in err_str_lower
                    ):
                        logger.warning(
                            f"Branch '{branch}' not found or issue accessing remote. Trying next..."
                        )
                    else:
                        # Log other pull errors more prominently
                        logger.error(
                            f"Failed to pull sparse data from branch '{branch}': {e}."
                        )
                    continue  # Try next branch

            if not pull_successful:
                logger.error(
                    f"Failed to pull sparse data from any common branch ({common_branches}) for '{repo_url}'. Last error: {last_pull_error}"
                )
                # Add check if target dir is empty or just .git before raising fatal error
                is_empty_or_just_git = (
                    not any(p.name != ".git" for p in target_dir.iterdir())
                    if target_dir.is_dir()
                    else True
                )
                if is_empty_or_just_git:
                    raise RuntimeError(
                        f"Could not pull sparse data for {doc_path} from {repo_url}"
                    ) from last_pull_error
                else:
                    # If some files exist (maybe previous pull worked partially?) log warning but don't fail
                    logger.warning(
                        f"Sparse pull failed, but target directory {target_dir} contains some files. Proceeding with existing content."
                    )

        except Exception as e:
            logger.error(
                f"Error during sparse checkout process for {repo_url}: {e}",
                exc_info=True,
            )
            # Consider cleaning up partially created directory on failure for robustness
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
            # Clone with depth 1 for speed
            await run_subprocess(
                [
                    "git",
                    "clone",
                    "--depth",
                    "1",
                    "--branch",
                    "main",
                    repo_url,
                    target_dir_str,
                ]  # Try main branch first
            )
            logger.info(
                f"Successfully cloned (branch main) '{repo_url}' to '{target_dir_str}'"
            )
        except RuntimeError as e_main:
            # If main branch fails, try master (common fallback)
            err_str_lower = str(e_main).lower()
            if (
                "remote branch main not found" in err_str_lower
                or "couldn't find remote ref main" in err_str_lower
            ):
                logger.warning(
                    "Branch 'main' not found, attempting 'master' branch for full clone..."
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
                        ]
                    )
                    logger.info(
                        f"Successfully cloned (branch master) '{repo_url}' to '{target_dir_str}'"
                    )
                except Exception as e_master:
                    logger.info(
                        f"Attempting cleanup of failed full clone directory (master attempt): {target_dir}"
                    )
                    await loop.run_in_executor(
                        executor, lambda: shutil.rmtree(target_dir, ignore_errors=True)
                    )
                    raise RuntimeError(
                        f"Git clone failed for {repo_url} on both main and master branches"
                    ) from e_master
            else:
                # If the error wasn't about the branch, re-raise the original error
                logger.info(
                    f"Attempting cleanup of failed full clone directory (main attempt): {target_dir}"
                )
                await loop.run_in_executor(
                    executor, lambda: shutil.rmtree(target_dir, ignore_errors=True)
                )
                raise RuntimeError(f"Git clone failed for {repo_url}") from e_main
        except Exception as e:
            # Catch other unexpected errors during clone
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
    executor: ThreadPoolExecutor,  # Argument name matches usage
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
            # topdown=True allows pruning directories (though not used here)
            # followlinks=False is generally safer to avoid loops and unintended scans
            for root, dirs, files in os.walk(
                target_path, topdown=True, onerror=None, followlinks=False
            ):
                # Skip common VCS directories
                dirs[:] = [d for d in dirs if d not in [".git", ".hg", ".svn"]]

                root_path = Path(root)
                for file in files:
                    try:
                        file_path = root_path / file
                        # Check suffix efficiently (lower case for case-insensitivity)
                        if file_path.suffix.lower() in allowed_extensions:
                            # Optional: Check if it's actually a file (not a broken symlink pointing nowhere)
                            # Add check for file size maybe?
                            try:
                                if (
                                    file_path.is_file()
                                ):  # Resolves symlinks, checks target exists
                                    found_files.append(file_path)
                                # else: logger.debug(f"Skipping non-file path: {file_path}") # Too verbose
                            except OSError as stat_err:
                                logger.warning(
                                    f"Could not stat file {file_path}: {stat_err}"
                                )

                    except OSError as path_err:
                        # Handle potential errors constructing the Path object (unlikely but possible)
                        logger.warning(
                            f"Could not form path for file '{file}' in '{root}': {path_err}"
                        )
                        continue
                    except Exception as inner_e:
                        # Catch other unexpected errors processing a single file
                        logger.warning(
                            f"Error processing file '{file}' in '{root}': {inner_e}"
                        )

        except Exception as e:
            # Catch errors during the walk setup or top-level iteration (e.g., permission denied on root)
            logger.error(
                f"Error during directory scan setup or iteration for {target_path}: {e}",
                exc_info=True,
            )
            # May return partially found files or an empty list depending on when error occurred
            return found_files  # Return what was found so far

        logger.info(
            f"Scan complete. Found {len(found_files)} files with allowed extensions in {target_path}"
        )
        return found_files

    # Run the synchronous function in the executor
    try:
        # Use the passed executor argument here
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
    """Example of using the git functions directly, with basic tests."""
    print("Running direct git downloader example...")
    logging.basicConfig(
        level=logging.INFO,  # Set level to INFO for cleaner test output
        format="%(asctime)s - %(levelname)-8s [%(name)s:%(funcName)s:%(lineno)d] %(message)s",
    )
    # Ensure the module's logger also adheres to this level if set differently elsewhere
    logger.setLevel(logging.INFO)

    # --- Test Tracking ---
    test_results: Dict[
        str, str
    ] = {}  # Dictionary to store results: test_name -> "PASS" / "FAIL: Reason" / "SKIPPED" / "WARNING"
    all_passed = True  # Overall status flag, set to False on FAIL/ERROR/UNKNOWN

    # --- Prerequisite Test: Git Dependency ---
    print("\n--- Testing Prerequisites ---")
    test_name = "Git Dependency Check"
    try:
        git_ok = check_git_dependency()
        assert git_ok, "'git' command not found or not working."
        test_results[test_name] = "PASS"
    except Exception as e:
        # Catch assertion error or unexpected errors from check_git_dependency
        print(f"FAIL: {test_name} failed: {e}")
        logger.error(f"{test_name} Exception", exc_info=False)
        test_results[test_name] = f"FAIL: {e}"
        all_passed = False
        # Print summary here before exiting as subsequent tests depend on Git
        print("\n--- Test Summary ---")
        for name, result in test_results.items():
            print(f"- {name}: {result}")
        print("\n✗ Prerequisite tests failed. Aborting.")
        sys.exit(1)  # Exit with error status

    # --- Test Setup ---
    # Define executor early, handle potential errors during creation
    test_executor = None
    try:
        test_executor = ThreadPoolExecutor(
            max_workers=2
        )  # Define the executor instance
    except Exception as exec_e:
        print(f"FAIL: Could not create ThreadPoolExecutor: {exec_e}")
        test_results["Executor Creation"] = f"FAIL: {exec_e}"
        all_passed = False
        print("\n--- Test Summary ---")
        for name, result in test_results.items():
            print(f"- {name}: {result}")
        print("\n✗ Setup tests failed. Aborting.")
        sys.exit(1)
    else:
        test_results["Executor Creation"] = "PASS"

    base_test_dir = Path("./git_downloader_test").resolve()  # Use absolute path

    # Clean up previous runs robustly
    cleanup_status = "PASS"  # Assume pass unless warning/error
    if base_test_dir.exists():
        print(f"Removing previous test directory: {base_test_dir}")
        # Run rmtree in executor in case of large dirs/permission issues
        try:
            # Use the correct variable name 'test_executor'
            await asyncio.get_running_loop().run_in_executor(
                test_executor,
                lambda: shutil.rmtree(
                    base_test_dir, ignore_errors=False
                ),  # Set ignore_errors=False to catch issues
            )
        except Exception as e:
            print(
                f"Warning: Failed to remove previous test directory {base_test_dir}: {e}"
            )
            # Decide if this is fatal - for tests, maybe not, but record it.
            cleanup_status = f"WARNING: {e}"
    test_results["Cleanup Previous Run"] = cleanup_status

    # Create base test directory
    try:
        base_test_dir.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        print(f"FAIL: Could not create base test directory {base_test_dir}: {e}")
        test_results["Create Test Directory"] = f"FAIL: {e}"
        all_passed = False
        # Exit if base dir cannot be created
        print("\n--- Test Summary ---")
        for name, result in test_results.items():
            print(f"- {name}: {result}")
        print("\n✗ Setup tests failed. Aborting.")
        if test_executor:
            test_executor.shutdown()  # Ensure executor is shut down
        sys.exit(1)
    else:
        test_results["Create Test Directory"] = "PASS"

    # --- Test Parameters ---
    # Use python-arango repo known to have .rst files (but root README is .md)
    repo_url = "https://github.com/ArangoDB-Community/python-arango.git"
    clone_path_full = base_test_dir / "arango_full"
    clone_path_sparse = base_test_dir / "arango_sparse"
    doc_path_sparse = "docs"  # Directory containing .rst files
    # Ensure .rst is included, add others if needed from python-arango docs
    allowed_exts = {
        ".py",
        ".rst",
        ".html",
        ".js",
        ".css",
        ".txt",
        ".md",
    }  # Added .md as it exists too

    # Variables to store results between tests
    found_files_full = []
    found_files_sparse = []

    try:
        # --- Test 1: Full Clone ---
        print("\n--- Testing Full Clone ---")
        test_name = "Full Clone Execution"
        try:
            # Pass the defined 'test_executor' to the function
            await run_git_clone(repo_url, clone_path_full, None, test_executor)
            # Assertion: Check if the target directory exists after cloning
            assert clone_path_full.is_dir(), (
                f"Target directory '{clone_path_full}' not created."
            )

            # Check for either README.md OR README.rst
            readme_md_path = clone_path_full / "README.md"
            readme_rst_path = clone_path_full / "README.rst"
            assert readme_md_path.is_file() or readme_rst_path.is_file(), (
                f"Neither 'README.md' nor 'README.rst' found as a file in {clone_path_full}"
            )

            # --- FIX: Use correct directory name 'arango' ---
            # Check for the main __init__ file of the library as stronger proof
            core_lib_file = clone_path_full / "arango" / "__init__.py"
            assert core_lib_file.is_file(), (
                f"Core library file '{core_lib_file}' not found in full clone."
            )
            # --- End Fix ---

            print(f"Full clone completed successfully in: {clone_path_full}")
            test_results[test_name] = "PASS"
        except Exception as e:
            print(f"FAIL: {test_name} failed: {e}")
            # Debugging: List directory contents on failure
            if clone_path_full.is_dir():
                print(
                    f"DEBUG: Contents of {clone_path_full}: {os.listdir(clone_path_full)}"
                )
            # End Debugging
            logger.error(f"{test_name} Exception", exc_info=False)
            test_results[test_name] = f"FAIL: {e}"
            all_passed = False

        # --- Test 2: File Scan (Full Clone) ---
        print("\n--- Testing File Scan (Full Clone) ---")
        test_name = "File Scan (Full Clone)"
        # Only run scan if previous step passed AND directory exists
        prereq_passed = test_results.get("Full Clone Execution") == "PASS"
        if prereq_passed and clone_path_full.is_dir():
            try:
                # Pass the defined 'test_executor'
                found_files_full = await scan_local_files_async(
                    clone_path_full, allowed_exts, test_executor
                )
                print(f"Found {len(found_files_full)} files in full clone.")
                # Adjust expected count for python-arango (may vary slightly)
                assert len(found_files_full) > 100, (
                    f"Expected roughly > 100 files in full clone scan, found {len(found_files_full)}."
                )
                test_results[test_name] = "PASS"
            except Exception as e:
                print(f"FAIL: {test_name} failed: {e}")
                logger.error(f"{test_name} Exception", exc_info=False)
                test_results[test_name] = f"FAIL: {e}"
                all_passed = False
        else:
            # Provide more specific reason for skipping
            skip_reason = (
                "(Prerequisite Failed)" if not prereq_passed else "(Directory Missing)"
            )
            test_results[test_name] = f"SKIPPED {skip_reason}"
            # Ensure found_files_full is empty if skipped, for later comparisons
            found_files_full = []

        # --- Test 3: Sparse Clone ---
        print("\n--- Testing Sparse Clone ---")
        test_name = "Sparse Clone Execution"
        # This test can run even if the full clone failed, as it uses a different directory
        # We just need the base directory setup to have passed.
        if test_results.get("Create Test Directory") == "PASS":
            try:
                # Use updated doc_path_sparse
                await run_git_clone(
                    repo_url, clone_path_sparse, doc_path_sparse, test_executor
                )
                # Assertion: Check if the target directory exists
                assert clone_path_sparse.is_dir(), (
                    f"Target directory '{clone_path_sparse}' not created."
                )
                # Assertions for python-arango sparse 'docs' checkout
                expected_sparse_subdir = (
                    clone_path_sparse / doc_path_sparse
                )  # Check for 'docs' dir
                assert expected_sparse_subdir.is_dir(), (
                    f"Expected sparse directory '{expected_sparse_subdir}' not found."
                )
                # Check for a known file within 'docs'
                assert (expected_sparse_subdir / "index.rst").is_file(), (
                    "'docs/index.rst' not found in sparse clone."
                )

                # Removed assertion checking for absence of README.md as root files are expected

                # Keep checks for absence of other *directories*
                # --- FIX: Check for absence of correct dir name 'arango' ---
                assert not (clone_path_sparse / "arango").exists(), (
                    "'arango' directory should NOT exist in sparse clone."
                )
                # --- End Fix ---
                print(f"Sparse clone completed successfully in: {clone_path_sparse}")
                test_results[test_name] = "PASS"
            except Exception as e:
                print(f"FAIL: {test_name} failed: {e}")
                # Debugging: List directory contents on failure
                if clone_path_sparse.is_dir():
                    print(
                        f"DEBUG: Contents of {clone_path_sparse}: {os.listdir(clone_path_sparse)}"
                    )
                # End Debugging
                logger.error(f"{test_name} Exception", exc_info=False)
                test_results[test_name] = f"FAIL: {e}"
                all_passed = False
        else:
            test_results[test_name] = "SKIPPED (Setup Failed)"

        # --- Test 4: File Scan (Sparse Clone) ---
        print("\n--- Testing File Scan (Sparse Clone) ---")
        test_name = "File Scan (Sparse Clone)"
        # Only run scan if previous step passed AND directory exists
        prereq_passed = test_results.get("Sparse Clone Execution") == "PASS"
        if prereq_passed and clone_path_sparse.is_dir():
            try:
                # Scan the correct sparse target ('docs')
                scan_target_sparse = clone_path_sparse / doc_path_sparse
                # Pass the defined 'test_executor'
                found_files_sparse = await scan_local_files_async(
                    scan_target_sparse, allowed_exts, test_executor
                )
                print(
                    f"Found {len(found_files_sparse)} files in sparse clone path '{doc_path_sparse}'."
                )
                # Adjust counts/checks for python-arango 'docs' (likely dozens of .rst/.py files)
                assert len(found_files_sparse) > 20, (
                    f"Expected > 20 files in sparse clone path '{doc_path_sparse}', found {len(found_files_sparse)}."
                )
                # Ensure at least one .rst file was found
                assert any(f.suffix == ".rst" for f in found_files_sparse), (
                    "No .rst files found in sparse docs scan."
                )
                # Assertion: Check if number is much smaller than full scan (only if full scan ran AND succeeded)
                full_scan_passed = test_results.get("File Scan (Full Clone)") == "PASS"
                if (
                    full_scan_passed and found_files_full
                ):  # Avoid comparison if full scan was skipped or failed
                    assert len(found_files_sparse) < len(found_files_full), (
                        f"Sparse scan found {len(found_files_sparse)} files, not less than full scan ({len(found_files_full)})."
                    )
                test_results[test_name] = "PASS"
            except Exception as e:
                print(f"FAIL: {test_name} failed: {e}")
                logger.error(f"{test_name} Exception", exc_info=False)
                test_results[test_name] = f"FAIL: {e}"
                all_passed = False
        else:
            # Provide more specific reason for skipping
            skip_reason = (
                "(Prerequisite Failed)" if not prereq_passed else "(Directory Missing)"
            )
            test_results[test_name] = f"SKIPPED {skip_reason}"

    except Exception as e:
        # Catch unexpected errors in the overall example structure
        print(f"\nGit downloader example failed unexpectedly: {e}")
        logger.error("Example structure failed", exc_info=True)
        # Mark any tests not yet run as errored
        tests_to_mark = [
            "Full Clone Execution",
            "File Scan (Full Clone)",
            "Sparse Clone Execution",
            "File Scan (Sparse Clone)",
        ]
        for t_name in tests_to_mark:
            if t_name not in test_results:
                test_results[t_name] = f"ERROR: Outer execution failed ({e})"
        all_passed = False
    finally:
        if (
            test_executor
        ):  # Check if executor was successfully created before shutting down
            print("\nShutting down executor...")
            # Wait for pending tasks? shutdown(wait=True) is often safer
            test_executor.shutdown(wait=True)
            print("Executor shut down.")
        else:
            print("\nExecutor was not created, skipping shutdown.")

        # --- Final Summary ---
        print("\n--- Test Summary ---")
        # Ensure all potential test keys are present, marking as UNKNOWN if missing
        # Added Executor Creation to the summary list
        all_test_names = [
            "Git Dependency Check",
            "Executor Creation",
            "Cleanup Previous Run",
            "Create Test Directory",
            "Full Clone Execution",
            "File Scan (Full Clone)",
            "Sparse Clone Execution",
            "File Scan (Sparse Clone)",
        ]
        summary_all_passed = (
            True  # Recalculate based on final results, ignoring warnings
        )
        for name in all_test_names:
            result = test_results.get(
                name, "UNKNOWN (Test did not run or record result)"
            )
            print(f"- {name}: {result}")
            # Determine overall pass/fail based on FAIL/ERROR/UNKNOWN, ignore WARNING
            if "FAIL" in result or "ERROR" in result or "UNKNOWN" in result:
                summary_all_passed = False

        print("\n--------------------")
        if summary_all_passed:
            print("✓ All core Git Downloader tests passed!") # Already has print
        else:
            print( # Already has print
                "✗ Some Git Downloader tests FAILED, were SKIPPED, or had WARNINGS/ERRORS."
            )
            # Optional: Exit with non-zero status code on failure
            # sys.exit(1)
        print("--------------------")

        # Optional: Clean up test directory at the very end
        # print(f"Removing test directory: {base_test_dir}")
        # try:
        #     # Use simple rmtree here, executor is already shut down
        #     shutil.rmtree(base_test_dir, ignore_errors=True)
        # except Exception as e:
        #     print(f"Warning: Final cleanup failed for {base_test_dir}: {e}")

    print("\nDirect git downloader example finished.")

if __name__ == "__main__":
    import sys
    import asyncio

    asyncio.run(_git_example())

# Ensure the rest of the file (check_git_dependency, run_git_clone, scan_local_files_async definitions)
# remains the same as the previous complete version.
