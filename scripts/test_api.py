import time
import requests

BASE_URL = "http://localhost:8001"
TEST_URL = "https://example.com"

def main():
    # Call /download endpoint
    print("Requesting download...")
    download_resp = requests.post(f"{BASE_URL}/download", json={"url": TEST_URL})
    print("Download response status:", download_resp.status_code)
    print("Download response body:", download_resp.text)

    try:
        download_id = download_resp.json().get("download_id")
    except Exception:
        download_id = None

    if not download_id:
        print("No download_id returned, aborting.")
        return

    # Wait briefly
    time.sleep(2)

    # Call /search endpoint
    print("Requesting search...")
    search_resp = requests.post(
        f"{BASE_URL}/search",
        json={
            "download_id": download_id,
            "scan_keywords": ["example"],
            "extract_selector": "body"
        }
    )
    print("Search response status:", search_resp.status_code)
    print("Search response body:", search_resp.text)

if __name__ == "__main__":
    main()