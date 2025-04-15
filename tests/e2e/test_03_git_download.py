# tests/e2e/test_03_api_git_download.py
import pytest
import requests
import json
import time

# Removed initial_cleanup from import
from .conftest import (
    CONTAINER_INDEX_DIR,
    CONTAINER_DOWNLOADS_BASE,
    mcp_service,
    container,
)
from .helpers import (
    poll_for_status,
    poll_for_file_in_container,
    run_api_download,
    extract_first_local_path,
)

TEST_GIT_REPO_URL = "https://github.com/psf/requests.git"
EXPECTED_DOC_PATH_SEGMENT = "docs/index.rst"
STATUS_POLL_TIMEOUT = 180
FILE_POLL_TIMEOUT = 60


# --- Register marker ---
def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "dependency(name=None, depends=None, scope='session'): mark test to run after specified dependencies",
    )


# --- End marker ---


@pytest.mark.dependency(name="git_download_submit")  # Keep name marker
# ---> REMOVED cleanup_downloads <---
def test_api_git_download_submit(mcp_service):  # Removed initial_cleanup dependency too
    """Test submitting a git download via API (requests repo)."""
    payload = {
        "source_type": "git",
        "repo_url": TEST_GIT_REPO_URL,
        "doc_path": "docs",
        "force": True,
    }
    print(f"\nSubmitting Git download (sparse): {payload}")
    response = requests.post(f"{mcp_service}/download", json=payload, timeout=30)
    assert response.status_code == 202, (
        f"Expected 202 Accepted, got {response.status_code}"
    )
    data = response.json()
    assert data["status"] == "pending"
    assert "download_id" in data
    download_id = data["download_id"]
    print(f"Git Download submitted. ID: {download_id}")
    assert download_id is not None
    pytest.git_download_id = download_id


@pytest.mark.dependency(depends=["git_download_submit"])  # Depends on the named marker
def test_api_git_download_status_and_files(mcp_service, container):
    """Test polling status and checking files for the git download."""
    download_id = getattr(pytest, "git_download_id", None)
    assert download_id, "Git Download ID not found from previous test step"

    # 1. Poll Status
    status_result = poll_for_status(mcp_service, download_id, STATUS_POLL_TIMEOUT)
    assert status_result["status"] == "completed", (
        f"Git download failed. Error: {status_result.get('error_details')}"
    )

    # 2. Check Index File Existence
    index_file_path_in_container = f"{CONTAINER_INDEX_DIR}/{download_id}.jsonl"
    assert poll_for_file_in_container(
        container, index_file_path_in_container, FILE_POLL_TIMEOUT
    ), f"Git index file not found at {index_file_path_in_container}"

    # 3. Check Index File Permissions and Content
    print(
        f"\nDEBUG: Checking details for Git index file: {index_file_path_in_container}"
    )
    print("DEBUG: Waiting 2s for potential filesystem sync...")
    time.sleep(2)
    ls_cmd = f"ls -ln {index_file_path_in_container}"
    print(f"DEBUG: Running command (as root): {ls_cmd}")
    ls_exit_code, ls_output = container.exec_run(ls_cmd)
    ls_output_str = ls_output.decode().strip() if ls_output else "(no output)"
    print(f"DEBUG: ls -ln Output (Exit: {ls_exit_code}):\n{ls_output_str}")
    assert ls_exit_code == 0, (
        f"Failed to list Git index file details ({index_file_path_in_container})"
    )
    cat_cmd = f"cat {index_file_path_in_container}"
    print(f"DEBUG: Running command (as appuser): {cat_cmd}")
    cat_exit_code, cat_output = container.exec_run(cat_cmd, user="appuser")
    cat_output_str = cat_output.decode().strip() if cat_output else "(no output)"
    print(
        f"DEBUG: cat Output (as appuser) (Exit: {cat_exit_code}):\n'{cat_output_str}'"
    )
    assert cat_exit_code == 0, (
        f"Failed to 'cat' Git index file as appuser ({index_file_path_in_container}). 'ls' output was:\n{ls_output_str}"
    )
    assert cat_output_str, (
        f"Git index file appears to be empty when read by appuser ({index_file_path_in_container}). 'ls' output was:\n{ls_output_str}"
    )
    first_line_content = cat_output_str.splitlines()[0] if cat_output_str else ""
    assert first_line_content, (
        "First line of Git index file content is empty after cat."
    )
    try:
        record_data = json.loads(first_line_content)
        actual_status = record_data.get("fetch_status")
        assert actual_status == "success", (
            f"Expected fetch_status 'success' in first Git index record, got '{actual_status}'. Record: {record_data}"
        )
        print(
            f"DEBUG: Git index first line parsed successfully. Status: '{actual_status}'"
        )
    except json.JSONDecodeError:
        pytest.fail(
            f"Failed to parse first line of Git index file as JSON: '{first_line_content}'"
        )
    except Exception as e:
        pytest.fail(
            f"Error checking parsed Git index record data: {e}. Record Data: {record_data if 'record_data' in locals() else 'N/A'}"
        )

    # 4. Extract path to a KNOWN doc file and check existence
    print(
        f"\nDEBUG: Checking for '{EXPECTED_DOC_PATH_SEGMENT}' entry in Git index file..."
    )
    grep_cmd = f"grep '{EXPECTED_DOC_PATH_SEGMENT}' {index_file_path_in_container}"
    print(f"DEBUG: Running command (as appuser): {grep_cmd}")
    exit_code, output = container.exec_run(grep_cmd, user="appuser")
    output_str = output.decode().strip() if output else "(no output)"
    print(f"DEBUG: grep output (Exit: {exit_code}):\n{output_str}")
    assert exit_code == 0, (
        f"Could not find '{EXPECTED_DOC_PATH_SEGMENT}' entry in Git index file. Full index content was:\n'{cat_output_str}'"
    )
    doc_file_line = output_str.splitlines()[0]
    try:
        doc_file_data = json.loads(doc_file_line)
        relative_doc_file_path = doc_file_data.get("local_path")
        assert (
            relative_doc_file_path
            and EXPECTED_DOC_PATH_SEGMENT in relative_doc_file_path
        ), (
            f"Invalid relative path for '{EXPECTED_DOC_PATH_SEGMENT}' found in index line: {doc_file_line}"
        )
        print(
            f"DEBUG: Found '{EXPECTED_DOC_PATH_SEGMENT}' relative path: {relative_doc_file_path}"
        )
    except Exception as e:
        pytest.fail(
            f"Failed to parse index line or get path for '{EXPECTED_DOC_PATH_SEGMENT}': {e}\nLine: {doc_file_line}"
        )
    repo_dir_path = (
        f"{CONTAINER_DOWNLOADS_BASE}/{relative_doc_file_path.split('/repo/')[0]}/repo"
    )
    print(f"DEBUG: Checking repo directory: {repo_dir_path}")
    assert poll_for_file_in_container(container, repo_dir_path, FILE_POLL_TIMEOUT), (
        f"Repo directory not found at {repo_dir_path}"
    )
    absolute_doc_file_path = f"{CONTAINER_DOWNLOADS_BASE}/{relative_doc_file_path}"
    print(f"DEBUG: Checking specific doc file: {absolute_doc_file_path}")
    assert poll_for_file_in_container(
        container, absolute_doc_file_path, FILE_POLL_TIMEOUT
    ), (
        f"Specific doc file '{EXPECTED_DOC_PATH_SEGMENT}' not found at {absolute_doc_file_path}"
    )
    print("DEBUG: Git content directory and specific doc file checks passed.")
