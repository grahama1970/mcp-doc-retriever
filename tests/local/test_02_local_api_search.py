# tests/local/test_02_local_api_search.py
import pytest
import requests
from .test_01_local_api_download import (
    web_download_id,
)  # Import ID from previous test module


@pytest.mark.dependency(
    depends=["test_api_web_download_submit"]
)  # Depends on web download finishing
def test_local_api_search_success(local_mcp_server):
    """Test successful search using the completed web download."""
    base_url = local_mcp_server
    assert web_download_id is not None, "Web download ID is needed from previous test"

    search_url = f"{base_url}/search"  # Use the main /search endpoint
    payload = {
        "download_id": web_download_id,
        "scan_keywords": ["example"],  # Search for 'example'
        "extract_selector": "p",  # Extract paragraphs
    }
    print(f"\nSending search request: {payload}")
    response = requests.post(search_url, json=payload, timeout=30)

    assert response.status_code == 200, (
        f"Search failed with status {response.status_code}. Body: {response.text}"
    )
    results = response.json()
    assert isinstance(results, list)
    # Example.com has one <p> tag
    assert len(results) == 1, f"Expected 1 search result, got {len(results)}"
    first_result = results[0]
    assert (
        "example" in first_result["match_details"].lower()
    )  # Check if keyword is in details
    assert first_result["selector_matched"] == "p"
    print("Search successful, found expected content.")


def test_local_api_search_invalid_id(local_mcp_server):
    """Test search with a download ID that doesn't exist."""
    base_url = local_mcp_server
    search_url = f"{base_url}/search"
    payload = {
        "download_id": "non_existent_id_local_check",
        "scan_keywords": ["test"],
        "extract_selector": "p",
    }
    print(f"\nSending search request for non-existent ID: {payload}")
    response = requests.post(search_url, json=payload, timeout=30)
    assert response.status_code == 404, (
        f"Expected 404 for invalid ID, got {response.status_code}"
    )
    print("Search correctly returned 404 for invalid ID.")
