# tests/local/conftest.py
import pytest
import requests
import subprocess
import time
import os
import signal
import sys
import shutil
from pathlib import Path
from requests.exceptions import ConnectionError
import uuid

# --- Configuration ---
# Use the port defined in verify_local.sh
LOCAL_BASE_URL = os.environ.get("MCP_LOCAL_TEST_BASE_URL", "http://localhost:8005")
LOCAL_PORT = LOCAL_BASE_URL.split(":")[-1]  # Extract port
HEALTH_CHECK_TIMEOUT = 30  # Seconds to wait for local server health
HEALTH_CHECK_INTERVAL = 1
CLI_TEST_BASE_DIR_NAME = "local_pytest_cli_downloads"  # Subdir in tmp_path


# --- Helper ---
def is_local_service_healthy(base_url):
    """Checks the local /health endpoint."""
    try:
        response = requests.get(f"{base_url}/health", timeout=2)
        return (
            response.status_code == 200 and response.json().get("status") == "healthy"
        )
    except ConnectionError:
        return False
    except Exception as e:
        print(f"Health check error: {e}")
        return False


# --- Fixtures ---


@pytest.fixture(scope="session")
def local_mcp_server():
    """Starts and stops the local uvicorn server for the test session."""
    server_process = None
    log_file_path = Path("local_server_test.log")
    print(f"\n--- Local Test Setup: Starting uvicorn on port {LOCAL_PORT} ---")
    print(f"--- Server logs will be in: {log_file_path.resolve()} ---")

    # Command to run uvicorn - assumes running pytest from project root
    # Adjust path to main:app if necessary
    cmd = [
        sys.executable,  # Use the same python interpreter running pytest
        "-m",
        "uvicorn",
        "src.mcp_doc_retriever.main:app",
        "--host",
        "127.0.0.1",  # Bind to localhost only for local tests
        "--port",
        LOCAL_PORT,
        # Optional: "--reload" if needed, but usually not for automated tests
    ]

    try:
        # Open log file for server output
        with open(log_file_path, "wb") as log_file:
            # Start the server process
            server_process = subprocess.Popen(
                cmd, stdout=log_file, stderr=subprocess.STDOUT
            )

            # Wait for the server to become healthy
            print(
                f"Waiting up to {HEALTH_CHECK_TIMEOUT}s for local server to become healthy..."
            )
            start_time = time.time()
            healthy = False
            while time.time() - start_time < HEALTH_CHECK_TIMEOUT:
                if is_local_service_healthy(LOCAL_BASE_URL):
                    healthy = True
                    print("Local server is healthy!")
                    break
                time.sleep(HEALTH_CHECK_INTERVAL)

            if not healthy:
                # Try to terminate if still running
                if server_process.poll() is None:  # Check if process is running
                    server_process.terminate()
                    server_process.wait(timeout=5)
                pytest.fail(
                    f"Local server failed to start or become healthy within {HEALTH_CHECK_TIMEOUT}s. Check {log_file_path}."
                )

            yield LOCAL_BASE_URL  # Provide the URL to the tests

    except FileNotFoundError:
        pytest.fail(
            "Failed to start server: 'uvicorn' command not found. Is it installed in the venv?"
        )
    except Exception as e:
        pytest.fail(f"Failed to start local server: {e}")
    finally:
        # --- Teardown ---
        print("\n--- Local Test Teardown: Stopping uvicorn server ---")
        if server_process and server_process.poll() is None:
            try:
                print(f"Terminating server process (PID: {server_process.pid})...")
                # Send SIGTERM first for graceful shutdown
                server_process.terminate()
                server_process.wait(timeout=10)  # Wait for graceful shutdown
                print("Server terminated gracefully.")
            except subprocess.TimeoutExpired:
                print("Server did not terminate gracefully, sending SIGKILL...")
                server_process.kill()
                server_process.wait(timeout=5)  # Wait for kill
                print("Server killed.")
            except Exception as e:
                print(f"Error stopping server: {e}")
        else:
            print("Server process already stopped or not started.")


@pytest.fixture(scope="function")
def temp_cli_dirs(tmp_path):
    """Creates unique temporary directories for CLI tests for each function."""
    # tmp_path is a built-in pytest fixture providing a Path object
    # unique to each test function run.
    base_cli_dir = tmp_path / CLI_TEST_BASE_DIR_NAME
    web_dir = base_cli_dir / "web"
    git_dir = base_cli_dir / "git"
    web_dir.mkdir(parents=True, exist_ok=True)
    git_dir.mkdir(parents=True, exist_ok=True)
    print(f"\nCreated temp CLI dirs:\n  Web: {web_dir}\n  Git: {git_dir}")
    # Yield a dictionary or tuple of paths for tests to use
    yield {"web": web_dir, "git": git_dir}
    # Cleanup is handled automatically by pytest's tmp_path fixture


# Fixture to generate unique IDs for tests that need them
@pytest.fixture
def unique_id():
    """Generates a unique ID string."""
    return f"local_test_{uuid.uuid4().hex[:8]}"
