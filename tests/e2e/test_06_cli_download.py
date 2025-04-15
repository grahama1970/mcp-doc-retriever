# tests/e2e/test_06_cli_download.py
import pytest
import time

# Removed initial_cleanup from import
from .conftest import CONTAINER_CLI_TEST_DIR, CONTAINER_NAME, container
from .helpers import run_cli_command_in_container, poll_for_file_in_container

# Constants
EXAMPLE_URL = "https://example.com/"
TEST_GIT_REPO_URL = "https://github.com/git-fixtures/basic.git"
CLI_TIMEOUT = 150
FILE_POLL_TIMEOUT_INDEX = 90
FILE_POLL_TIMEOUT_REPO_DIR = 90
FILE_POLL_TIMEOUT_README = 150


# --- Register marker ---
def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "dependency(name=None, depends=None, scope='session'): mark test to run after specified dependencies",
    )


# --- End marker ---


# ---> REMOVED cleanup_downloads <---
def test_cli_web_download(container):  # Removed initial_cleanup dependency too
    """Test running the web download CLI command inside the container."""
    # 'initial_cleanup' (session-scoped) will have run before this
    cli_web_out_dir = f"{CONTAINER_CLI_TEST_DIR}/web"
    download_id = "cli_web_pytest"
    args = [
        "download",         # Command
        "website",          # Positional SOURCE_TYPE
        EXAMPLE_URL,        # Positional URL_OR_REPO
        download_id,        # Positional DOWNLOAD_ID
        # Options follow
        "--base-dir",
        cli_web_out_dir,
        "--depth",
        "0",
        "--force",
    ]
    exit_code, output = run_cli_command_in_container(container, args, CLI_TIMEOUT)
    assert exit_code == 0, f"CLI web download failed. Output:\n{output}"
    index_file_path = f"{cli_web_out_dir}/index/{download_id}.jsonl"
    assert poll_for_file_in_container(
        container, index_file_path, FILE_POLL_TIMEOUT_INDEX
    ), f"CLI web index file not found in container: {index_file_path}"
    content_dir_path = f"{cli_web_out_dir}/content/{download_id}/example.com"
    assert poll_for_file_in_container(
        container, content_dir_path, FILE_POLL_TIMEOUT_REPO_DIR
    ), f"CLI web content directory not found in container: {content_dir_path}"
    exit_code_find, _ = container.exec_run(
        f'sh -c \'find "{content_dir_path}" -maxdepth 1 -name "*.html" -type f | grep -q .\''
    )
    assert exit_code_find == 0, (
        f"No HTML files found in CLI web content directory: {content_dir_path}"
    )


# ---> REMOVED cleanup_downloads <---
def test_cli_git_download(container):  # Removed initial_cleanup dependency too
    """Test running the git download CLI command inside the container."""
    # 'initial_cleanup' (session-scoped) will have run before this
    cli_git_out_dir = f"{CONTAINER_CLI_TEST_DIR}/git"
    download_id = "cli_git_pytest"

    args = [
        "download",         # Command
        "git",              # Positional SOURCE_TYPE
        TEST_GIT_REPO_URL,  # Positional URL_OR_REPO
        download_id,        # Positional DOWNLOAD_ID
        # Options follow
        "--base-dir",
        cli_git_out_dir,
        "--doc-path",
        ".",
        "--force",
        "-v",
    ]
    exit_code, output = run_cli_command_in_container(container, args, CLI_TIMEOUT)
    print("\n--- Full CLI Git Download Output ---")
    print(output)
    print("--- End Full CLI Output ---")
    assert exit_code == 0, f"CLI git download failed."

    # Check Index File
    index_file_path = f"{cli_git_out_dir}/index/{download_id}.jsonl"
    print(f"\nDEBUG: Checking for index file: {index_file_path}")
    assert poll_for_file_in_container(
        container, index_file_path, FILE_POLL_TIMEOUT_INDEX
    ), f"CLI git index file not found in container: {index_file_path}"
    print("DEBUG: Index file found.")

    # Check Repo Directory
    repo_dir_path = f"{cli_git_out_dir}/content/{download_id}/repo"
    print(f"DEBUG: Checking for repo directory: {repo_dir_path}")
    assert poll_for_file_in_container(
        container, repo_dir_path, FILE_POLL_TIMEOUT_REPO_DIR
    ), f"CLI git repo directory not found in container: {repo_dir_path}"
    print("DEBUG: Repo directory found.")

    # DEBUG: List contents of repo directory
    print(f"DEBUG: Listing contents of {repo_dir_path}...")
    time.sleep(2)
    ls_cmd = f"ls -la {repo_dir_path}"
    ls_exit_code, ls_output = container.exec_run(ls_cmd)
    ls_output_str = ls_output.decode().strip() if ls_output else "(no output)"
    print(f"DEBUG: ls -la Output (Exit: {ls_exit_code}):\n{ls_output_str}")

    # Check CHANGELOG File (as README doesn't exist in this test repo)
    changelog_path = f"{repo_dir_path}/CHANGELOG"
    print(
        f"DEBUG: Checking for CHANGELOG file: {changelog_path} (Timeout: {FILE_POLL_TIMEOUT_README}s)"
    )
    assert poll_for_file_in_container(
        container, changelog_path, FILE_POLL_TIMEOUT_README # Use updated timeout var name if needed
    ), (
        f"CLI git CHANGELOG file not found in container: {changelog_path}. Contents of repo dir listed above."
    )
    print("DEBUG: CHANGELOG file found.")
