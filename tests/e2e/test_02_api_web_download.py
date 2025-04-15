# tests/e2e/test_02_api_web_download.py
import pytest
import json
import time

# Removed initial_cleanup from import as it's session-scoped now
from .conftest import (
    CONTAINER_NAME,
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

# Constants
EXAMPLE_URL = "https://example.com/"
STATUS_POLL_TIMEOUT = 90
FILE_POLL_TIMEOUT = 60


# --- Register custom markers ---
def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "dependency(name=None, depends=None, scope='session'): mark test to run after specified dependencies",
    )


# --- End marker registration ---


@pytest.mark.dependency(name="web_download_submit")
# ---> REMOVED cleanup_downloads (now initial_cleanup with session scope) <---
def test_api_web_download_submit(
    mcp_service,
):  # Removed initial_cleanup argument as well
    """Test submitting a simple web download via API."""
    # 'initial_cleanup' runs automatically once per session due to its scope and dependency
    download_id = run_api_download(
        mcp_service, "website", EXAMPLE_URL, depth=0, force=True
    )
    assert download_id is not None
    pytest.example_download_id = download_id


@pytest.mark.dependency(depends=["web_download_submit"])
def test_api_web_download_status_and_files(mcp_service, container):
    """Test polling status and checking files for the web download."""
    download_id = getattr(pytest, "example_download_id", None)
    assert download_id, "Download ID not found from previous test step"

    # 1. Poll Status
    status_result = poll_for_status(mcp_service, download_id, STATUS_POLL_TIMEOUT)
    assert status_result["status"] == "completed", (
        f"Download failed. Error: {status_result.get('error_details')}"
    )

    # 2. Check Index File Existence
    index_file_path_in_container = f"{CONTAINER_INDEX_DIR}/{download_id}.jsonl"
    assert poll_for_file_in_container(
        container, index_file_path_in_container, FILE_POLL_TIMEOUT
    ), f"Index file not found at {index_file_path_in_container}"

    # 3. DEBUG: Check Index File Permissions and Content (as appuser)
    print(f"\nDEBUG: Checking details for index file: {index_file_path_in_container}")
    print("DEBUG: Waiting 2s for potential filesystem sync...")
    time.sleep(2)
    ls_cmd = f"ls -ln {index_file_path_in_container}"
    print(f"DEBUG: Running command (as root): {ls_cmd}")
    ls_exit_code, ls_output = container.exec_run(ls_cmd)
    ls_output_str = ls_output.decode().strip() if ls_output else "(no output)"
    print(f"DEBUG: ls -ln Output (Exit: {ls_exit_code}):\n{ls_output_str}")
    assert ls_exit_code == 0, (
        f"Failed to list index file details ({index_file_path_in_container})"
    )

    cat_cmd = f"cat {index_file_path_in_container}"
    print(f"DEBUG: Running command (as appuser): {cat_cmd}")
    cat_exit_code, cat_output = container.exec_run(cat_cmd, user="appuser")
    cat_output_str = cat_output.decode().strip() if cat_output else "(no output)"
    print(
        f"DEBUG: cat Output (as appuser) (Exit: {cat_exit_code}):\n'{cat_output_str}'"
    )
    assert cat_exit_code == 0, (
        f"Failed to 'cat' index file as appuser ({index_file_path_in_container}). 'ls' output was:\n{ls_output_str}"
    )
    assert cat_output_str, (
        f"Index file appears to be empty when read by appuser ({index_file_path_in_container}). 'ls' output was:\n{ls_output_str}"
    )
    first_line_content = cat_output_str.splitlines()[0] if cat_output_str else ""
    assert first_line_content, "First line of index file content is empty after cat."
    try:
        record_data = json.loads(first_line_content)
        actual_status = record_data.get("fetch_status")
        assert actual_status == "success", (
            f"Expected fetch_status 'success' in first record, got '{actual_status}'. Record: {record_data}"
        )
        print(f"DEBUG: Index first line parsed successfully. Status: '{actual_status}'")
    except json.JSONDecodeError:
        pytest.fail(
            f"Failed to parse first line of index file as JSON: '{first_line_content}'"
        )
    except Exception as e:
        pytest.fail(
            f"Error checking parsed index record data: {e}. Record Data: {record_data if 'record_data' in locals() else 'N/A'}"
        )

    # 4. Extract path and check Content File
    relative_content_path = extract_first_local_path(
        container, index_file_path_in_container
    )
    assert relative_content_path, (
        f"Could not extract a non-empty local_path from index. Index content:\n'{cat_output_str}'"
    )
    absolute_content_path = f"{CONTAINER_DOWNLOADS_BASE}/{relative_content_path}"
    assert poll_for_file_in_container(
        container, absolute_content_path, FILE_POLL_TIMEOUT
    ), f"Content file not found at expected absolute path: {absolute_content_path}"
    print("DEBUG: Content file existence check passed.")
