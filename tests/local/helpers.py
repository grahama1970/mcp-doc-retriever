# tests/local/helpers.py
import time
import requests
from requests.exceptions import RequestException

POLL_INTERVAL = 3  # Seconds


def poll_status(base_url: str, download_id: str, timeout: int) -> dict:
    """Polls the local /status endpoint until completed or failed, or timeout."""
    start_time = time.time()
    print(f"Polling status for {download_id} @ {base_url} (max {timeout}s)... ", end="")
    while time.time() - start_time < timeout:
        try:
            response = requests.get(f"{base_url}/status/{download_id}", timeout=10)
            if response.status_code == 200:
                data = response.json()
                status = data.get("status")
                if status == "completed":
                    print("Completed.")
                    return data
                elif status == "failed":
                    print("Failed.")
                    return data
                elif status in ["running", "pending"]:
                    print(".", end="", flush=True)
                else:
                    print(f"?({status})", end="", flush=True)  # Unknown status
            elif response.status_code == 404:
                # This might happen briefly if polling starts before task is registered
                print("X", end="", flush=True)  # Not found yet
            else:
                print(
                    f"E({response.status_code})", end="", flush=True
                )  # Other HTTP error

        except RequestException as e:
            print(f"!({e})", end="", flush=True)  # Request error

        time.sleep(POLL_INTERVAL)

    print("Timeout!")
    raise TimeoutError(f"Polling status for {download_id} timed out after {timeout}s")
