# tests/e2e/conftest.py
import pytest
import requests
import docker
import subprocess
import time
import os
from pathlib import Path
from docker.errors import NotFound, APIError
from requests.exceptions import ConnectionError

# --- Constants ---
BASE_URL = os.environ.get("MCP_TEST_BASE_URL", "http://localhost:8001")
CONTAINER_NAME = "mcp-doc-retriever"
COMPOSE_FILE_PATH = Path(__file__).resolve().parent.parent.parent / "docker-compose.yml"
CONTAINER_DOWNLOADS_BASE = "/app/downloads"
CONTAINER_INDEX_DIR = "/app/downloads/index"
CONTAINER_CLI_TEST_DIR = "/app/cli_test_downloads_e2e"
HEALTH_CHECK_TIMEOUT = 60
HEALTH_CHECK_INTERVAL = 2


# --- Helper Function ---
def is_service_healthy(base_url):
    """Checks if the /health endpoint returns 200 OK."""
    try:
        response = requests.get(f"{base_url}/health", timeout=5)
        return (
            response.status_code == 200 and response.json().get("status") == "healthy"
        )
    except ConnectionError:
        return False
    except Exception:
        return False


# --- Fixtures ---


@pytest.fixture(scope="session")
def docker_client():
    """Provides a Docker client."""
    try:
        client = docker.from_env()
        client.ping()
        return client
    except Exception as e:
        pytest.fail(f"Failed to connect to Docker daemon: {e}")


@pytest.fixture(scope="session")
def mcp_service(docker_client):
    """Manages the Docker Compose service lifecycle for the test session."""
    compose_file = str(COMPOSE_FILE_PATH)
    if not COMPOSE_FILE_PATH.exists():
        pytest.fail(f"docker-compose.yml not found at: {compose_file}")

    print("\n--- E2E Setup: Starting Docker Compose (Removed initial down -v) ---")
    # Removed initial `down -v` which was deleting the volume before `up`
    # subprocess.run(
    #     ["docker", "compose", "-f", compose_file, "down", "-v", "--remove-orphans"],
    #     check=False,
    #     capture_output=True,
    # )
    start_cmd = [
        "docker",
        "compose",
        "-f",
        compose_file,
        "up",
        "-d",
        # "--build",  # Removed: Rely on cached/pre-built image for speed
        # "--force-recreate", # Removed: `up -d` handles existing containers
    ]
    start_result = subprocess.run(start_cmd, check=True, capture_output=True, text=True)
    print(start_result.stdout)
    if start_result.stderr:
        print("Compose Up Stderr:\n", start_result.stderr)

    print(
        f"Waiting up to {HEALTH_CHECK_TIMEOUT}s for service to become healthy at {BASE_URL}..."
    )
    start_time = time.time()
    healthy = False
    container = None
    while time.time() - start_time < HEALTH_CHECK_TIMEOUT:
        try:
            container = docker_client.containers.get(CONTAINER_NAME)
            if container.status != "running":
                print(f"Container status: {container.status}. Waiting...")
                time.sleep(HEALTH_CHECK_INTERVAL)
                continue
            if is_service_healthy(BASE_URL):
                healthy = True
                print("Service is healthy!")
                break
            else:
                print("Service not healthy yet. Retrying...")
        except NotFound:
            print(f"Container '{CONTAINER_NAME}' not found yet. Waiting...")
        except APIError as e:
            print(f"Docker API error: {e}. Retrying...")
        except Exception as e:
            print(f"Unexpected error checking health: {e}. Retrying...")
        time.sleep(HEALTH_CHECK_INTERVAL)

    if not healthy:
        print(
            f"\nERROR: Service did not become healthy within {HEALTH_CHECK_TIMEOUT}s."
        )
        try:
            if container:
                print(
                    "\n--- Container Logs (on failure) ---\n",
                    container.logs().decode(),
                    "\n-----------------------------------",
                )
        except Exception as log_e:
            print(f"Could not retrieve container logs: {log_e}")
        subprocess.run(
            ["docker", "compose", "-f", compose_file, "down", "-v", "--remove-orphans"],
            check=False,
            capture_output=True,
        )
        pytest.fail("Service failed to start or become healthy.")

    yield BASE_URL

    print("\n--- E2E Teardown: Stopping Docker Compose ---")
    subprocess.run(
        ["docker", "compose", "-f", compose_file, "down", "--remove-orphans"], # Removed -v to preserve volume during session
        check=False,
        capture_output=True,
    )
    print("Docker Compose stopped.")


@pytest.fixture(scope="session")
def container(
    docker_client, mcp_service
):  # Depends on mcp_service to ensure container exists
    """Provides the running service container object."""
    try:
        return docker_client.containers.get(CONTAINER_NAME)
    except NotFound:
        pytest.fail(f"Container '{CONTAINER_NAME}' not found after service startup.")
    except Exception as e:
        pytest.fail(f"Error getting container '{CONTAINER_NAME}': {e}")


# ---> CHANGE: Scope changed to "session", renamed, depends on container <---
@pytest.fixture(scope="session")
def initial_cleanup(container):
    """Fixture to clean download directories inside the container ONCE at the start."""
    print(
        f"\n--- SESSION SETUP: Cleaning container dirs: {CONTAINER_DOWNLOADS_BASE}, {CONTAINER_CLI_TEST_DIR} ---"
    )
    # Ensure the base directories exist before trying to remove contents
    container.exec_run(f"mkdir -p {CONTAINER_DOWNLOADS_BASE} {CONTAINER_CLI_TEST_DIR}")
    # Use find | xargs rm for better handling of many files/errors or if dir is initially empty
    # Exclude the database file from cleanup
    db_file_path = f"{CONTAINER_DOWNLOADS_BASE}/task_status.db"
    cmd_cleanup_downloads = f"sh -c 'find {CONTAINER_DOWNLOADS_BASE} -mindepth 1 -maxdepth 1 -not -path {db_file_path} -print0 | xargs -0 rm -rf'"
    cmd_cleanup_cli = f"sh -c 'find {CONTAINER_CLI_TEST_DIR} -mindepth 1 -maxdepth 1 -print0 | xargs -0 rm -rf'"

    exit_code_dl, output_dl = container.exec_run(cmd_cleanup_downloads)
    if exit_code_dl != 0:
        print(
            f"Warning: Non-zero exit code ({exit_code_dl}) during {CONTAINER_DOWNLOADS_BASE} cleanup. Output:\n{output_dl.decode()}"
        )

    exit_code_cli, output_cli = container.exec_run(cmd_cleanup_cli)
    if exit_code_cli != 0:
        print(
            f"Warning: Non-zero exit code ({exit_code_cli}) during {CONTAINER_CLI_TEST_DIR} cleanup. Output:\n{output_cli.decode()}"
        )

    # Recreate CLI test dir base just in case
    container.exec_run(f"mkdir -p {CONTAINER_CLI_TEST_DIR}")
    print("--- SESSION SETUP: Initial cleanup attempt complete ---")
    # No yield needed, just runs once at the start of the session.
