# tests/e2e/helpers.py
import time
import requests
import uuid
import json
from requests.exceptions import RequestException
from docker.models.containers import Container
from docker.errors import NotFound, APIError

# Constants from conftest or defined here
POLL_INTERVAL = 3
CONTAINER_DOWNLOADS_BASE = "/app/downloads"


def poll_for_status(base_url: str, download_id: str, timeout: int) -> dict:
    """Polls the /status endpoint until completed or failed, or timeout."""
    start_time = time.time()
    print(f"Polling status for {download_id} (max {timeout}s)... ", end="")
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


def poll_for_file_in_container(
    container: Container, file_path: str, timeout: int
) -> bool:
    """Polls for file/directory existence inside the container."""
    start_time = time.time()
    print(f"Polling for file {file_path} in container (max {timeout}s)... ", end="")
    while time.time() - start_time < timeout:
        try:
            # Use exec_run to check if the file exists inside the container
            # 'test -e' returns 0 if the file exists, non-zero otherwise
            exit_code, _ = container.exec_run(cmd=f"test -e {file_path}")

            if exit_code == 0:
                print("Found.")
                return True
            else:
                # File not found yet, print dot and continue polling
                print(".", end="", flush=True)
        except APIError as e:
            # Handle potential Docker API errors during exec_run
            print(f"E({e})", end="", flush=True)
        except Exception as e:
            # Catch any other unexpected errors
            print(f"!({e})", end="", flush=True)

        time.sleep(POLL_INTERVAL)

    print("Timeout!")
    return False


def run_api_download(
    base_url: str, source_type: str, source_location: str, depth: int, force: bool
) -> str:
    """Submits a download request and returns the download_id."""
    unique_id = f"test_{uuid.uuid4().hex[:8]}"
    payload = {
        "download_id": unique_id,
        "source_type": source_type,
        "force": force,
    }
    if source_type == "git":
        payload["repo_url"] = source_location
        payload["doc_path"] = "."  # Clone everything
    elif source_type in ["website", "playwright"]:
        payload["url"] = source_location
        payload["depth"] = depth
    else:
        raise ValueError(f"Unsupported source_type: {source_type}")

    print(
        f"Submitting API download: {source_type} - {source_location} (ID: {unique_id})"
    )
    response = requests.post(f"{base_url}/download", json=payload, timeout=15)
    response.raise_for_status()  # Raise exception for 4xx/5xx

    data = response.json()
    assert data["status"] == "pending"
    assert data["download_id"] == unique_id
    print(f"API Download submitted successfully. ID: {unique_id}")
    return unique_id


def run_cli_command_in_container(
    container: Container, command_args: list, timeout: int
) -> tuple[int, str]:
    """Runs a command using 'docker exec' and returns exit code and output."""
    # Construct the full command to run inside the container
    full_command = ["uv", "run", "python", "-m", "mcp_doc_retriever.cli"] + command_args
    print(f"Executing in container: {' '.join(full_command)}")
    try:
        # Use timeout for the exec call
        # Note: Docker SDK's timeout isn't directly supported in exec_run in older versions.
        # We rely on the test framework's timeout or a wrapper if needed.
        # This simple version doesn't enforce the timeout strictly within exec_run.
        exit_code, output = container.exec_run(
            cmd=full_command, stream=False, demux=False
        )
        output_str = output.decode("utf-8") if output else ""
        print(f"CLI Exit Code: {exit_code}")
        print(
            f"CLI Output:\n{output_str[:1000]}{'...' if len(output_str) > 1000 else ''}"
        )
        return exit_code, output_str
    except APIError as e:
        print(f"Docker API Error running command: {e}")
        return -1, str(e)
    except Exception as e:
        print(f"Unexpected Error running command: {e}")
        return -1, str(e)


def extract_first_local_path(
    container: Container, index_file_path_in_container: str
) -> str | None:
    """Reads the first line of the index file in container and extracts local_path."""
    print(f"Extracting local_path from: {index_file_path_in_container}")
    exit_code, output = container.exec_run(
        f"sh -c 'head -n 1 {index_file_path_in_container}'"
    )
    if exit_code != 0:
        print(f"Failed to read index file head. Output:\n{output.decode()}")
        return None
    first_line = output.decode().strip()
    if not first_line:
        print("Index file first line is empty.")
        return None
    try:
        data = json.loads(first_line)
        local_path = data.get("local_path")
        if local_path:
            print(f"Extracted local_path: {local_path}")
            return local_path
        else:
            print("No 'local_path' found in first line JSON.")
            return None
    except json.JSONDecodeError:
        print("Failed to parse first line as JSON.")
        return None
