# tests/test_mcp_retriever_e2e_loguru.py (New file or modify existing)
import pytest
import requests
import subprocess
import time
import os
import shutil
import uuid
import json
import sys
from pathlib import Path

# import logging # Remove standard logging
import traceback
from loguru import logger  # Import Loguru

# --- Loguru Configuration ---
# Remove default handler, configure custom stderr handler
logger.remove()
logger.add(
    sys.stderr,
    level="DEBUG",  # Capture everything from DEBUG upwards
    format="<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> | <level>{level: <8}</level> | <cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - <level>{message}</level>",
    colorize=True,
)

# --- Configuration (Keep the rest as before) ---
BASE_URL = os.environ.get(
    "MCP_TEST_BASE_URL", "http://localhost:8001"
)  # Will change for local tests
# Project paths
PROJECT_ROOT = Path(__file__).parent.parent
HOST_CLI_TEST_DIR = PROJECT_ROOT / "test_cli_dir"

# Docker container configuration
CONTAINER_NAME = "mcp-doc-retriever-app"
CONTAINER_DOWNLOADS_BASE = "/app/downloads"
CONTAINER_CONTENT_DIR = f"{CONTAINER_DOWNLOADS_BASE}/content"
CONTAINER_INDEX_DIR = f"{CONTAINER_DOWNLOADS_BASE}/index"

# Test behavior configuration
REQUEST_TIMEOUT = 30  # seconds - Default timeout for API requests
KEEP_CONTAINER = False  # Set to True to keep container running after tests
KEEP_DOWNLOADS = False  # Set to True to preserve downloaded files after tests

SHARED_STATE = {}  # For sharing data between test cases if needed
SHARED_STATE = {}

# --- NEW Loguru-based Helper Functions ---
# We don't need separate _print_* functions now, just use logger directly


def run_subprocess(command, cwd=None, timeout=60, check=True, description="subprocess"):
    """Runs subprocess with Loguru logging."""
    logger.info(f"Starting STEP: Running {description}")
    cmd_str = " ".join(map(str, command))
    logger.debug(f"Executing command: {cmd_str}")
    if cwd:
        logger.debug(f"Working Directory: {cwd}")
    # VSCODE BREAKPOINT: Before running the command. Inspect `command`, `cwd`.
    start_time = time.time()
    result = None
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            cwd=cwd if cwd else PROJECT_ROOT,
            errors="replace",
        )
        duration = time.time() - start_time
        logger.info(
            f"{description} finished in {duration:.2f}s. RC: {result.returncode}"
        )
        # Log stdout/stderr only if they contain content
        if result.stdout and result.stdout.strip():
            logger.debug(f"{description} STDOUT:\n---\n{result.stdout.strip()}\n---")
        if result.stderr and result.stderr.strip():
            logger.warning(
                f"{description} STDERR:\n---\n{result.stderr.strip()}\n---"
            )  # Log stderr as warning

        # VSCODE BREAKPOINT: After command execution. Inspect `result`.
        if check and result.returncode != 0:
            logger.error(
                f"{description} FAILED with non-zero exit code {result.returncode}."
            )
            # VSCODE BREAKPOINT: When check=True fails.
            raise subprocess.CalledProcessError(
                result.returncode, command, output=result.stdout, stderr=result.stderr
            )

        logger.info(
            f"{description} check PASSED (RC={result.returncode}, check={check})."
        )
        return result
    # ... (keep except blocks, but use logger.error/logger.exception) ...
    except subprocess.CalledProcessError as e:
        logger.error(f"{description} raised CalledProcessError. RC: {e.returncode}")
        # VSCODE BREAKPOINT: In CalledProcessError handler.
        raise
    except subprocess.TimeoutExpired as e:
        logger.error(f"{description} TIMED OUT after {timeout}s.")
        # VSCODE BREAKPOINT: In TimeoutExpired handler.
        if e.stdout and e.stdout.strip():
            logger.debug(f"Timeout STDOUT:\n---\n{e.stdout.strip()}\n---")
        if e.stderr and e.stderr.strip():
            logger.warning(f"Timeout STDERR:\n---\n{e.stderr.strip()}\n---")
        raise
    except Exception as e:
        logger.exception(
            f"{description} FAILED with unexpected error!"
        )  # logger.exception includes traceback
        # VSCODE BREAKPOINT: In generic exception handler.
        raise


def check_file_in_container(file_path):
    """Checks file/dir existence inside container with Loguru."""
    logger.info(f"Starting STEP: Checking container path: '{file_path}'")
    command = ["docker", "exec", CONTAINER_NAME, "test", "-e", file_path]
    # VSCODE BREAKPOINT: Before running `docker exec test -e`.
    try:
        result = run_subprocess(
            command, check=False, description=f"container path check ({file_path})"
        )
        exists = result.returncode == 0
        logger.info(
            f"Container path '{file_path}' {'exists' if exists else 'does NOT exist'}."
        )
        # VSCODE BREAKPOINT: After `docker exec test -e`. Inspect `result`, `exists`.
        return exists
    except Exception as e:
        logger.error(f"Error during container path check for '{file_path}': {e}")
        # VSCODE BREAKPOINT: If the docker exec command itself fails.
        return False


# ... Adapt list_files_in_container similarly using logger ...


def api_request(
    method,
    endpoint,
    base_url_override=None,
    data=None,
    expected_status=None,
    timeout=REQUEST_TIMEOUT,
):
    """Makes API request with Loguru logging. Allows overriding BASE_URL."""
    target_base_url = base_url_override if base_url_override else BASE_URL
    url = f"{target_base_url}{endpoint}"
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    logger.info(f"Starting STEP: Sending API Request: {method} {url}")
    if data:
        # VSCODE BREAKPOINT: Before request with data. Inspect `data`.
        logger.debug(f"Request Payload:\n{json.dumps(data, indent=2)}")
    else:
        # VSCODE BREAKPOINT: Before request without data.
        logger.debug("Request Body: [NONE]")

    response = None
    try:
        start_time = time.time()
        response = requests.request(
            method, url, json=data, headers=headers, timeout=timeout
        )
        duration = time.time() - start_time
        logger.info(
            f"API Response received in {duration:.2f}s. Status: {response.status_code}"
        )
        # VSCODE BREAKPOINT: Immediately after receiving response. Inspect `response`.
        # Log response body preview (limited)
        try:
            response_text = response.text
            limit = 500
            body_preview = response_text[:limit] + (
                "..." if len(response_text) > limit else ""
            )
            logger.debug(f"Response Body Preview:\n---\n{body_preview}\n---")
            # Attempt to parse and print JSON nicely if possible
            # response_json = response.json() # Don't assume it's JSON here
        except Exception as e:
            logger.warning(f"(Error processing response body for logging preview: {e})")

        if expected_status is not None:
            logger.debug(
                f"Asserting Status Code: Expected={expected_status}, Actual={response.status_code}"
            )
            if response.status_code != expected_status:
                logger.error(
                    f"Status code mismatch! Expected {expected_status}, got {response.status_code}"
                )
                # VSCODE BREAKPOINT: On status code mismatch.
            assert response.status_code == expected_status, (
                f"Assertion Failed: Expected status {expected_status}, got {response.status_code}. Response: {response.text}"
            )
            logger.info("Status code assertion PASSED.")

        logger.debug("Checking for HTTP errors (4xx/5xx) to raise exception...")
        response.raise_for_status()
        logger.info("HTTP status check passed (no 4xx/5xx).")

        try:
            # Return JSON if possible, otherwise raw response
            return response.json()
        except requests.exceptions.JSONDecodeError:
            logger.debug("(Response was not JSON, returning raw response object)")
            return response

    # ... (adapt except blocks using logger.error/logger.exception) ...
    except requests.exceptions.HTTPError as e:
        logger.error(f"API Request FAILED (HTTPError {e.response.status_code})!")
        # VSCODE BREAKPOINT: In HTTPError handler.
        pytest.fail(
            f"API request failed with HTTPError: {e}. Response: {getattr(e.response, 'text', 'N/A')}"
        )
    except requests.exceptions.RequestException as e:
        logger.error(f"API Request FAILED (RequestException)! Error: {e}")
        # VSCODE BREAKPOINT: In RequestException handler.
        pytest.fail(f"API request failed with RequestException: {e}")
    except Exception as e:
        logger.exception("API Request FAILED (Unexpected Error)!")
        # VSCODE BREAKPOINT: In generic exception handler for API request.
        pytest.fail(f"API request failed with unexpected error: {e}")


# ... Adapt poll_status similarly using logger ...


# --- Pytest Fixture for Setup/Teardown ---
# Keep the fixture, but replace _print_* calls with logger calls
# REMOVE THE wait_time FIX FROM PREVIOUS STEP - WE WILL RE-ADD IT CORRECTLY
@pytest.fixture(scope="session", autouse=True)
def docker_setup_teardown(request):
    """Manages Docker container start/stop and cleanup for the test session with Loguru."""
    logger.info("--- Starting E2E Test Session Setup ---")

    # For local API testing, we don't need Docker setup
    run_docker_tests = False  # Skip Docker setup when testing local API
    
    if run_docker_tests:  # This block won't execute for local testing
        logger.info("Docker tests enabled: Performing Docker setup.")

        logger.info("Starting STEP: Cleaning up host directories")
        # ... (use logger.info/error for host dir cleanup) ...
        if HOST_CLI_TEST_DIR.exists():
            logger.info(
                f"Removing previous host CLI test directory: {HOST_CLI_TEST_DIR}"
            )
            try:
                shutil.rmtree(HOST_CLI_TEST_DIR)
            except Exception as e:
                logger.error(
                    f"Could not remove host CLI directory {HOST_CLI_TEST_DIR}: {e}"
                )
        try:
            HOST_CLI_TEST_DIR.mkdir(parents=True, exist_ok=True)
            logger.info(f"Created host CLI test directory: {HOST_CLI_TEST_DIR}")
        except Exception as e:
            logger.error(
                f"Could not create host CLI directory {HOST_CLI_TEST_DIR}: {e}"
            )
            pytest.fail(f"Failed to create host CLI directory: {e}")

        logger.info("Starting STEP: Ensuring clean Docker state (compose down)")
        # VSCODE BREAKPOINT: Before initial compose down.
        run_subprocess(
            ["docker", "compose", "down", "-v", "--remove-orphans", "--timeout", "15"],
            check=False,
            description="initial docker compose down",
        )

        logger.info("Starting STEP: Starting Docker services (compose up)")
        # VSCODE BREAKPOINT: Before compose up.
        try:
            run_subprocess(
                [
                    "docker",
                    "compose",
                    "up",
                    "-d",
                    "--build",
                    "--force-recreate",
                    "--remove-orphans",
                ],
                description="docker compose up",
            )
        except Exception as e:
            logger.exception("Docker compose up FAILED!")
            # VSCODE BREAKPOINT: If compose up fails critically.
            pytest.fail(f"Docker compose up failed: {e}")

        # --- FIX: Define wait_time correctly ---
        wait_time = 25
        logger.info(
            f"Starting STEP: Waiting {wait_time} seconds for service initialization..."
        )
        # ---------------------------------------
        time.sleep(wait_time)

        logger.info("Starting STEP: Verifying Docker container and API health")
        # VSCODE BREAKPOINT: Before container/API verification.
        # ... (use logger.info/error/exception for verification steps) ...
        container_verified = False
        api_verified = False
        try:
            ps_result = run_subprocess(
                [
                    "docker",
                    "ps",
                    "-f",
                    f"name=^/{CONTAINER_NAME}$",
                    "--format",
                    "{{.Names}}",
                ],
                description="docker ps check",
            )
            assert CONTAINER_NAME in ps_result.stdout
            logger.info(f"Container '{CONTAINER_NAME}' confirmed running.")
            container_verified = True

            logger.info(f"Verifying API health endpoint ({BASE_URL}/health)...")
            api_request(
                "GET", "/health", expected_status=200, timeout=20
            )  # Uses BASE_URL (docker)
            logger.info("Docker API health endpoint is responsive.")
            api_verified = True
        except Exception as e:
            logger.exception("Container/API verification FAILED!")
            if container_verified and not api_verified:
                logger.error("Container running, but Docker API health check failed.")
            elif not container_verified:
                logger.error("Container did not start/run correctly.")
            try:
                logger.info("Attempting to fetch container logs...")
                run_subprocess(
                    ["docker", "logs", CONTAINER_NAME],
                    check=False,
                    description="docker logs on failure",
                )
            except Exception as log_e:
                logger.error(f"Could not fetch logs: {log_e}")
            pytest.fail(f"Container/API verification failed: {e}")

        logger.info("Starting STEP: Cleaning container downloads directory")
        # VSCODE BREAKPOINT: Before cleaning container downloads.
        try:
            run_subprocess(
                [
                    "docker",
                    "exec",
                    CONTAINER_NAME,
                    "sh",
                    "-c",
                    f"rm -rf {CONTAINER_DOWNLOADS_BASE}/* && mkdir -p {CONTAINER_INDEX_DIR} {CONTAINER_CONTENT_DIR} && chmod -R 777 {CONTAINER_DOWNLOADS_BASE}",
                ],
                description="clean container downloads",
            )
            logger.info("Container downloads directory cleaned.")
        except Exception as e:
            logger.warning(
                f"Failed to clean container downloads dir: {e}. May cause test issues."
            )
            # VSCODE BREAKPOINT: If cleaning fails.

        logger.info("--- Docker Setup Complete ---")

    else:
        logger.warning("Skipping Docker setup as Docker tests seem disabled.")

    # --- Yield to run tests ---
    yield
    # --- Tests Finished ---

    logger.info("--- Starting E2E Test Session Teardown ---")
    if run_docker_tests:
        # Teardown Docker
        if KEEP_CONTAINER:
            logger.info("KEEP_CONTAINER=true, leaving container running.")
        else:
            logger.info("Starting STEP: Stopping Docker services (compose down)")
            # VSCODE BREAKPOINT: Before final compose down.
            run_subprocess(
                [
                    "docker",
                    "compose",
                    "down",
                    "-v",
                    "--remove-orphans",
                    "--timeout",
                    "15",
                ],
                check=False,
                description="final docker compose down",
            )

    # Optional Host Cleanup
    if KEEP_DOWNLOADS:
        logger.info(
            f"KEEP_DOWNLOADS=true, preserving host outputs in {HOST_CLI_TEST_DIR}"
        )
    else:
        logger.info("Starting STEP: Cleaning up host directories on teardown")
        # ... (use logger for host dir cleanup) ...
        if HOST_CLI_TEST_DIR.exists():
            logger.info(f"Removing host CLI test directory: {HOST_CLI_TEST_DIR}")
            try:
                shutil.rmtree(HOST_CLI_TEST_DIR)
            except Exception as e:
                logger.error(f"Could not remove host CLI dir on teardown: {e}")

    logger.info("--- E2E Test Session Teardown Complete ---")


# --- Test Cases (Adapt to use logger instead of print) ---
# Keep the @pytest.mark.phaseN markers


@pytest.mark.phase1
def test_phase1_api_health():
    """Tests the /health endpoint of the Docker API."""
    logger.info(">>> Test: Phase 1: API Health Check <<<")
    logger.info("Starting STEP: Checking /health endpoint (Docker API)")
    # VSCODE BREAKPOINT: Start health check.
    response = api_request(
        "GET", "/health", expected_status=200
    )  # Targets BASE_URL (Docker)
    # VSCODE BREAKPOINT: After health check response. Inspect `response`.
    assert isinstance(response, dict), (
        f"Health check response not dict. Got: {type(response)}"
    )
    assert response.get("status") == "healthy", (
        f"Health check status not 'healthy'. Response: {response}"
    )
    logger.info("<<< Test Phase 1: PASSED >>>")


# ... Adapt ALL other test cases (test_phase2_api_web_download etc.) to use logger ...
# e.g., replace _print_step -> logger.info("Starting STEP: ...")
#         replace _print_info -> logger.info(...)
#         replace _print_error -> logger.error(...)
#         replace _print_debug -> logger.debug(...)

# IMPORTANT: For local testing later, we will override the BASE_URL
# or add specific local test functions. The fixture handles Docker setup/teardown.
