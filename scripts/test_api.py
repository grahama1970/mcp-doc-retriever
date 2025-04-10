import time
import requests
import pytest

BASE_URL = "http://localhost:8001"
TEST_URL = "https://example.com"
INVALID_URL = "https://invalid.example.nonexistent"
DEEP_URL = "https://example.com/with/links"  # Assumes this page contains links

def test_basic_download_and_search():
    """Test basic download and search functionality"""
    # Test download
    download_resp = requests.post(f"{BASE_URL}/download", json={"url": TEST_URL})
    assert download_resp.status_code == 200, "Download request failed"
    
    download_id = download_resp.json().get("download_id")
    assert download_id, "No download_id returned"
    
    # Wait for processing
    time.sleep(2)
    
    # Test search
    search_resp = requests.post(
        f"{BASE_URL}/search",
        json={
            "download_id": download_id,
            "scan_keywords": ["example"],
            "extract_selector": "body"
        }
    )
    assert search_resp.status_code == 200, "Search request failed"
    assert "example" in search_resp.text.lower(), "Expected keyword not found"

def test_recursive_download():
    """Test recursive download functionality with depth"""
    download_resp = requests.post(
        f"{BASE_URL}/download",
        json={"url": DEEP_URL, "depth": 2}  # Assumes depth parameter is supported
    )
    assert download_resp.status_code == 200, "Recursive download request failed"
    
    download_id = download_resp.json().get("download_id")
    assert download_id, "No download_id returned for recursive download"
    
    # Wait for processing
    time.sleep(5)  # Longer wait for recursive processing
    
    # Verify we can search the downloaded content
    search_resp = requests.post(
        f"{BASE_URL}/search",
        json={"download_id": download_id, "scan_keywords": ["example"]}
    )
    assert search_resp.status_code == 200, "Search after recursive download failed"

def test_search_misses():
    """Test search with no matches"""
    download_resp = requests.post(f"{BASE_URL}/download", json={"url": TEST_URL})
    download_id = download_resp.json().get("download_id")
    time.sleep(2)
    
    search_resp = requests.post(
        f"{BASE_URL}/search",
        json={
            "download_id": download_id,
            "scan_keywords": ["nonexistentkeyword123"],
            "extract_selector": "body"
        }
    )
    assert search_resp.status_code == 200, "Search request failed"
    assert not search_resp.json().get("matches"), "Expected no matches but found some"

def test_search_with_selectors():
    """Test search with specific selectors"""
    download_resp = requests.post(f"{BASE_URL}/download", json={"url": TEST_URL})
    download_id = download_resp.json().get("download_id")
    time.sleep(2)
    
    search_resp = requests.post(
        f"{BASE_URL}/search",
        json={
            "download_id": download_id,
            "scan_keywords": ["example"],
            "extract_selector": "title"  # More specific selector
        }
    )
    assert search_resp.status_code == 200, "Search with selector failed"
    results = search_resp.json()
    assert results.get("matches"), "Expected matches but found none"
    assert all("title" in match.get("context", "") for match in results["matches"]), "Selector not respected"

def test_download_failure():
    """Test handling of invalid URLs"""
    download_resp = requests.post(f"{BASE_URL}/download", json={"url": INVALID_URL})
    assert download_resp.status_code != 200, "Invalid URL should fail"
    assert "error" in download_resp.json(), "Error response expected for invalid URL"

def test_force_download():
    """Test force download overwrite functionality"""
    # First download
    download_resp = requests.post(f"{BASE_URL}/download", json={"url": TEST_URL})
    download_id = download_resp.json().get("download_id")
    time.sleep(2)
    
    # Force re-download
    force_resp = requests.post(
        f"{BASE_URL}/download",
        json={"url": TEST_URL, "force": True}
    )
    assert force_resp.status_code == 200, "Force download request failed"
    assert force_resp.json().get("download_id") != download_id, "Expected new download_id for forced download"

if __name__ == "__main__":
    # Maintain backward compatibility with direct script execution
    test_basic_download_and_search()
    print("All basic tests passed")