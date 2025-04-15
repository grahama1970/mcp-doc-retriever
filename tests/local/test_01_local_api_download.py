# tests/local/test_01_local_api_download.py
import pytest
import requests
from .helpers import poll_status

# Test constants
EXAMPLE_URL = "https://example.com/"
TEST_GIT_REPO_URL = "https://github.com/git-fixtures/basic.git"
POLL_TIMEOUT = 60  # Seconds

# Store IDs globally within the module for dependency
web_download_id = None
git_download_id = None


@pytest.mark.dependency()
def test_api_web_download_submit(local_mcp_server):
    """Test submitting example.com download and polling status."""
    global web_download_id
    base_url = local_mcp_server
    payload = {
        "source_type": "website",
        "url": EXAMPLE_URL,
        "depth": 0,
        "force": True,
        # download_id is optional, let server generate
    }
    print(f"\nSubmitting web download: {payload}")
    response = requests.post(f"{base_url}/download", json=payload, timeout=15)
    assert response.status_code == 202, (
        f"Expected 202 Accepted, got {response.status_code}"
    )
    data = response.json()
    assert data["status"] == "pending"
    assert "download_id" in data
    web_download_id = data["download_id"]  # Store the generated ID
    print(f"Web Download submitted. ID: {web_download_id}")
    assert web_download_id is not None

    # Poll for completion
    status_result = poll_status(base_url, web_download_id, POLL_TIMEOUT)
    assert status_result["status"] == "completed", (
        f"Web download failed. Error: {status_result.get('error_details')}"
    )
    print("Web download polling successful.")


@pytest.mark.dependency()
def test_api_git_download_submit(local_mcp_server):
    """Test submitting git download and polling status."""
    global git_download_id
    base_url = local_mcp_server
    payload = {
        "source_type": "git",
        "repo_url": TEST_GIT_REPO_URL,
        "doc_path": ".",  # Clone all
        "force": True,
    }
    print(f"\nSubmitting git download: {payload}")
    response = requests.post(f"{base_url}/download", json=payload, timeout=15)
    assert response.status_code == 202, (
        f"Expected 202 Accepted, got {response.status_code}"
    )
    data = response.json()
    assert data["status"] == "pending"
    assert "download_id" in data
    git_download_id = data["download_id"]  # Store the generated ID
    print(f"Git Download submitted. ID: {git_download_id}")
    assert git_download_id is not None

    # Poll for completion
    status_result = poll_status(
        base_url, git_download_id, POLL_TIMEOUT * 2
    )  # Allow longer for git
    assert status_result["status"] == "completed", (
        f"Git download failed. Error: {status_result.get('error_details')}"
    )
    print("Git download polling successful.")
