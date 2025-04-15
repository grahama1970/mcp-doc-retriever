# tests/e2e/test_05_api_search.py
import pytest
import requests
import json
from .conftest import BASE_URL
from .helpers import poll_for_status  # Keep this import

# Constants
EXAMPLE_URL = "https://example.com/"
STATUS_POLL_TIMEOUT = 90


# ---> REMOVE Dependency Marker <---
# @pytest.mark.dependency(depends=['web_download_submit']) # REMOVED
def test_api_search_success(mcp_service):
    """Test successful search using the completed web download."""
    # We still need the ID saved by test_02
    download_id = getattr(pytest, "example_download_id", None)
    if not download_id:
        pytest.fail(
            "Prerequisite web download ID (pytest.example_download_id) was not set."
        )

    # Poll for status *within* this test to ensure completion
    print(f"\nEnsuring download {download_id} is complete before searching...")
    status_result = poll_for_status(mcp_service, download_id, STATUS_POLL_TIMEOUT)
    if status_result.get("status") != "completed":
        pytest.fail(
            f"Prerequisite download {download_id} did not complete successfully. Status: {status_result.get('status')}, Error: {status_result.get('error_details')}"
        )
    print("Download confirmed complete.")

    # Proceed with search logic
    search_url = f"{mcp_service}/search"
    payload = {
        "download_id": download_id,
        "scan_keywords": ["Example", "Domain"],
        "extract_selector": "title",
    }
    print(f"\nSending search request: {payload}")
    response = requests.post(search_url, json=payload, timeout=30)

    assert response.status_code == 200, (
        f"Search failed with status {response.status_code}. Body: {response.text}"
    )
    results = response.json()
    assert isinstance(results, list)
    assert len(results) > 0, "Expected at least one search result"
    first_result = results[0]
    assert first_result["original_url"] == EXAMPLE_URL
    assert first_result["selector_matched"] == "title"
    assert "Example Domain" in first_result["match_details"], (
        f"Expected title not found in match_details: {first_result['match_details']}"
    )
    print(f"Search successful. Found {len(results)} result(s).")


# ---> REMOVE Dependency Marker <---
# @pytest.mark.dependency(depends=['web_download_submit']) # REMOVED
def test_api_search_no_results(mcp_service):
    """Test search that should yield no results."""
    download_id = getattr(pytest, "example_download_id", None)
    if not download_id:
        pytest.fail(
            "Prerequisite web download ID (pytest.example_download_id) was not set."
        )

    # Poll for status *within* this test
    print(f"\nEnsuring download {download_id} is complete before searching...")
    status_result = poll_for_status(mcp_service, download_id, STATUS_POLL_TIMEOUT)
    if status_result.get("status") != "completed":
        pytest.fail(
            f"Prerequisite download {download_id} did not complete successfully. Status: {status_result.get('status')}, Error: {status_result.get('error_details')}"
        )
    print("Download confirmed complete.")

    # Proceed with search logic
    search_url = f"{mcp_service}/search"
    payload = {
        "download_id": download_id,
        "scan_keywords": ["nonexistentkeywordxyz123"],
        "extract_selector": "p",
    }
    print(f"\nSending search for non-existent keyword: {payload}")
    response = requests.post(search_url, json=payload, timeout=30)
    assert response.status_code == 200
    results = response.json()
    assert isinstance(results, list)
    assert len(results) == 0, (
        f"Expected empty list for non-matching search, got {len(results)}"
    )
    print("Search correctly returned no results.")


def test_api_search_invalid_id(mcp_service):
    """Test search with a download ID that doesn't exist."""
    search_url = f"{mcp_service}/search"
    payload = {
        "download_id": "invalid-id-does-not-exist-pytest",
        "scan_keywords": ["test"],
        "extract_selector": "p",
    }
    print(f"\nSending search request for non-existent ID: {payload}")
    response = requests.post(search_url, json=payload, timeout=30)
    assert response.status_code == 404, (
        f"Expected 404 for invalid ID, got {response.status_code}"
    )
    print("Search correctly returned 404 for invalid ID.")
