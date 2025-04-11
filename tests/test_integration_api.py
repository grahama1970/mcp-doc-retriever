"""
Module: test_integration_api_e2e.py

Description:
Performs end-to-end integration tests for the MCP Document Retriever FastAPI API.
These tests interact with a *running* instance of the service (expected to be
managed by docker compose) by making HTTP requests to its endpoints (/health,
/download, /search).

It verifies:
- Basic service health.
- Initiation of download tasks.
- Correct creation of index and content files within the container volume
  (using `docker exec` for verification).
- Correct status and path information in the index file.
- Basic search functionality against downloaded content.
- Handling of invalid search requests.
- Correct path generation (checking against known bugs like nested 'content').
- (Optional) Invocation of Playwright downloads.

Prerequisites:
- Docker and Docker Compose installed.
- The MCP Document Retriever service running in a container named 'mcp-doc-retriever'
  (typically started with `docker compose up -d --build`).
- Python environment with `pytest`, `pytest-asyncio`, and `httpx` installed
  (usually via `uv pip install -r requirements-dev.txt` or `uv sync --dev`).

How to Run:
Ensure the Docker container is running first. Then, from the project root directory:
pytest tests/integration/test_api_e2e.py -v -s
"""

import pytest
import pytest_asyncio  # Ensures asyncio event loop handling by pytest
import httpx
import asyncio
import time
import subprocess
import os
import json

@pytest.mark.asyncio
async def test_download_ssrf_block(http_client: httpx.AsyncClient):
    """
    Test that /download blocks internal/private/SSRF-prone URLs and allows legitimate external URLs.
    """
    ssrf_urls = [
        "http://localhost:8000",
        "http://127.0.0.1",
        "http://10.0.0.5",
        "http://192.168.1.1",
        "http://my-internal-service.local",
        "http://[::1]",
        "http://169.254.169.254",  # AWS metadata
    ]
    for url in ssrf_urls:
        payload = {"url": url, "depth": 0, "force": True}
        response = await http_client.post("/download", json=payload)
        assert response.status_code == 200, f"API call failed: {response.text}"
        data = response.json()
        assert data["status"] == "failed_validation", f"Should block SSRF URL: {url}"
        assert "internal" in data["message"].lower() or "ssrf" in data["message"].lower(), f"Message should indicate SSRF block: {data['message']}"
    # Control: external URL should be allowed
    payload = {"url": "http://example.com", "depth": 0, "force": True}
    response = await http_client.post("/download", json=payload)
    assert response.status_code == 200, f"API call failed: {response.text}"
    data = response.json()
    assert data["status"] == "started", "External URL should be allowed"

import uuid  # For generating unique invalid IDs
from typing import Dict, Any, Set, Optional  # For type hinting
from urllib.parse import (
    urlparse,
)  # Though not directly used here, reflects project context

# --- Test Configuration ---
BASE_URL = "http://localhost:8001"  # URL where the service is accessible via docker compose port mapping
CONTAINER_NAME = (
    "mcp-doc-retriever"  # Should match container_name in docker-compose.yml
)
HEALTH_TIMEOUT = 300  # 5 minutes to wait for initial health check (build may be slow)
DOWNLOAD_WAIT_SHORT = 8  # seconds for simple example.com download
DOWNLOAD_WAIT_LONG = 45  # seconds for deeper python docs download (adjust as needed)
API_TIMEOUT_SECONDS = 60  # Timeout for individual API calls via httpx
POLL_TIMEOUT_SECONDS = 60  # Max time to wait for download index entry to appear/update
POLL_INTERVAL_SECONDS = 3  # How often to check index file while polling

# URLs for testing
EXAMPLE_URL = "https://example.com/"
PYTHON_DOCS_URL = "https://docs.python.org/3/"


# --- Helper Functions ---


def run_docker_exec(command_args: list[str]) -> subprocess.CompletedProcess:
    """Runs a command inside the container using docker exec. Returns CompletedProcess."""
    base_cmd = ["docker", "exec", CONTAINER_NAME]
    full_cmd = base_cmd + command_args
    # print(f"\nDEBUG: Running docker exec: {' '.join(full_cmd)}") # Uncomment for debug
    try:
        # Using CompletedProcess allows access to returncode, stdout, stderr
        result = subprocess.run(
            full_cmd, capture_output=True, text=True, check=False, timeout=30
        )
        return result
    except subprocess.TimeoutExpired:
        print(f"\nERROR: Docker exec command timed out: {' '.join(full_cmd)}")
        # Return a dummy process object indicating failure
        return subprocess.CompletedProcess(
            full_cmd, returncode=-1, stdout="", stderr="Docker exec command timed out"
        )
    except Exception as e:
        print(f"\nERROR: Error running docker exec {' '.join(full_cmd)}: {e}")
        return subprocess.CompletedProcess(
            full_cmd, returncode=-1, stdout="", stderr=str(e)
        )


async def check_health(client: httpx.AsyncClient, max_wait: int) -> bool:
    """Polls the health endpoint until it's healthy or timeout."""
    print(f"Polling health endpoint {BASE_URL}/health for {max_wait}s...")
    start_time = time.monotonic()
    while time.monotonic() - start_time < max_wait:
        try:
            response = await client.get(f"{BASE_URL}/health", timeout=5)  # Use absolute URL
            if (
                response.status_code == 200
                and response.json().get("status") == "healthy"
            ):
                print("\nHealth check PASSED.")
                return True
            else:
                print(f". (Status: {response.status_code})", end="", flush=True)
        except (httpx.ConnectError, httpx.TimeoutException):
            print(".", end="", flush=True)  # Service might not be ready yet
            pass
        except Exception as e:
            print(f"\nHealth check error: {e}")  # Log other errors
        await asyncio.sleep(1)  # Wait 1 second between checks
    print("\nHealth check FAILED (timeout).")
    return False


async def poll_for_index_status(
    download_id: str,
    target_url: str,
    expected_statuses: Set[str],
    timeout: int = POLL_TIMEOUT_SECONDS,
    interval: int = POLL_INTERVAL_SECONDS,
) -> Optional[Dict[str, Any]]:
    """Polls the index file inside the container until target_url has an expected status."""
    start_time = time.monotonic()
    index_file_path = f"/app/downloads/index/{download_id}.jsonl"
    print(
        f"Polling index {index_file_path} for URL {target_url} (expecting {expected_statuses})...",
        end="",
        flush=True,
    )

    last_error = None
    while time.monotonic() - start_time < timeout:
        proc = run_docker_exec(["cat", index_file_path])

        if proc.returncode == 0:
            lines = proc.stdout.strip().split("\n")
            for line in reversed(lines):  # Check recent entries first
                try:
                    record = json.loads(line)
                    # Match canonical URL
                    if record.get("canonical_url") == target_url:
                        status = record.get("fetch_status")
                        if status in expected_statuses:
                            print(
                                f"\nFound final status '{status}' for {target_url} in index."
                            )
                            return record  # Return the full record
                        else:
                            # Found the URL, but status not final yet (or unexpected)
                            # Keep polling but maybe log intermediate status?
                            # print(f" Found intermediate status '{status}'...", end="", flush=True)
                            pass
                except json.JSONDecodeError:
                    # Log once, might be okay if file is being written
                    if last_error != "json_decode":
                        print(
                            f"\nWarning: Skipping invalid JSON line in index: {line[:100]}..."
                        )
                        last_error = "json_decode"
                    continue  # Ignore invalid lines
                except Exception as e:
                    if last_error != "json_parse_other":
                        print(
                            f"\nWarning: Error processing index line '{line[:100]}...': {e}"
                        )
                        last_error = "json_parse_other"

        elif "No such file or directory" in proc.stderr:
            # File might not exist yet, this is okay early on
            if last_error != "not_found":
                print(" Index file not found yet...", end="", flush=True)
                last_error = "not_found"
        else:
            # Other docker exec error
            if last_error != "exec_error":
                print(f"\nError reading index via docker exec: {proc.stderr}")
                last_error = "exec_error"

        print(f".", end="", flush=True)  # Progress indicator
        await asyncio.sleep(interval)

    print(
        f"\nTimeout ({timeout}s) waiting for {target_url} to reach status {expected_statuses} in {index_file_path}"
    )
    # Try one last read after timeout
    proc = run_docker_exec(["cat", index_file_path])
    if proc.returncode == 0:
        print(f"Final index content sample:\n{proc.stdout[:500]}...")
    else:
        print(f"Final attempt to read index failed: {proc.stderr}")
    return None  # Timeout


# --- Pytest Fixtures ---


# Automatically run docker compose up/down for the entire test session (module)
@pytest.fixture(scope="module", autouse=True)
def docker_service(request):
    """Starts and stops the docker compose service for the test module."""
    print("\n--- Setting up Docker Compose service for tests ---")
    try:
        # Clean up potentially running containers and old volumes first
        print("Stopping existing services and removing volumes (if any)...")
        down_cmd = [
            "docker",
            "compose",
            "down",
            "-v",
            "--remove-orphans",
            "--timeout",
            "5",
        ]
        subprocess.run(down_cmd, check=False, capture_output=True)

        # Build and start detached
        print("Building and starting service detached...")
        up_cmd = ["docker", "compose", "up", "--build", "-d", "--force-recreate"]
        print(f"Running docker compose build (timeout: {HEALTH_TIMEOUT}s)...")
        start_result = subprocess.run(
            up_cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False
        )
        if start_result.stdout:
            print(f"[DOCKER BUILD STDOUT]: {start_result.stdout}")
        if start_result.stderr:
            print(f"[DOCKER BUILD STDERR]: {start_result.stderr}")
        if start_result.returncode != 0:
            pytest.fail(
                f"docker compose up failed:\nSTDOUT:\n{start_result.stdout}\nSTDERR:\n{start_result.stderr}",
                pytrace=False,
            )
        print("`docker compose up` command finished.")

        # Wait for the service to become healthy
        print(
            f"Waiting up to {HEALTH_TIMEOUT}s for service health at {BASE_URL}/health ..."
        )
        # Use an async context to run the async health check
        healthy = asyncio.run(
            check_health(httpx.AsyncClient(timeout=5), HEALTH_TIMEOUT)
        )
        if not healthy:
            # Grab logs if health check fails
            logs_result = subprocess.run(
                ["docker", "compose", "logs", CONTAINER_NAME],
                capture_output=True,
                text=True,
            )
            pytest.fail(
                f"Service {CONTAINER_NAME} did not become healthy within {HEALTH_TIMEOUT}s.\nLogs:\n{logs_result.stdout}\n{logs_result.stderr}",
                pytrace=False,
            )

        yield  # Tests run after this point

    finally:
        # Teardown: Stop and remove container/volume after tests complete
        print("\n--- Tearing down Docker Compose service ---")
        down_cmd = [
            "docker",
            "compose",
            "down",
            "-v",
            "--remove-orphans",
            "--timeout",
            "5",
        ]
        subprocess.run(down_cmd, check=False, capture_output=True)
        print("--- Docker Compose service stopped and volume removed ---")


@pytest.fixture(scope="module")
async def http_client():
    """Provides an async httpx client scoped to the test module."""
    # Base URL and timeout set here apply to all requests using this client
    async with httpx.AsyncClient(
        base_url=BASE_URL, timeout=API_TIMEOUT_SECONDS
    ) as client:
        yield client
# Fixture: Start a local HTTP server to serve test_data/ for custom download tests
import socket
import sys
import threading
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler

@pytest.fixture(scope="module")
async def test_data_server():
    """
    Starts a local HTTP server serving the test_data/ directory on a random port.
    Yields the port number for use in tests.
    """
    # Find a free port
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("", 0))
    port = sock.getsockname()[1]
    sock.close()

    # Change working directory to test_data/
    orig_cwd = os.getcwd()
    test_data_dir = os.path.join(orig_cwd, "test_data")
    os.chdir(test_data_dir)

    server = ThreadingHTTPServer(("0.0.0.0", port), SimpleHTTPRequestHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    print(f"[TEST FIXTURE] Started test_data HTTP server on port {port}")

    try:
        yield port
    finally:
        server.shutdown()
        thread.join()
        os.chdir(orig_cwd)
        print(f"[TEST FIXTURE] Stopped test_data HTTP server on port {port}")


# Use a class to share state (like download IDs) between tests if needed
# Mark the class so all methods inherit asyncio marker
@pytest.mark.asyncio
class TestApiIntegration:
    # Class variable to store download IDs across test methods
    # Note: relies on pytest default ordered execution or explicit ordering marks
    download_ids: Dict[str, str] = {}

# Test methods as top-level async functions

async def test_01_health_check(http_client: httpx.AsyncClient):
    """Phase 1: Verify the /health endpoint."""
    print("\n--- Test: Health Check ---")
    response = await http_client.get("/health")  # Relative URL uses base_url
    assert response.status_code == 200
    assert response.json() == {"status": "healthy"}
    print("[PASS] Health check successful.")

@pytest.fixture
async def example_com_download_id(http_client: httpx.AsyncClient):
    """Fixture: Download example.com and return the download_id."""
    print("\n--- Fixture: Download example.com (depth 0) ---")
    payload = {"url": EXAMPLE_URL, "depth": 0, "force": True}
    response = await http_client.post("/download", json=payload)
    assert response.status_code == 200, f"API call failed: {response.text}"
    data = response.json()
    assert data["status"] == "started"
    download_id = data.get("download_id")
    assert download_id, "Did not receive download_id"
    print(f"Download initiated, ID: {download_id}")

    print(
        f"Waiting up to {POLL_TIMEOUT_SECONDS}s for example.com download completion..."
    )
    final_record = await poll_for_index_status(
        download_id, EXAMPLE_URL, {"success", "skipped"}
    )
    assert final_record is not None, (
        f"Download for {EXAMPLE_URL} did not complete successfully within timeout."
    )
    assert final_record["fetch_status"] in ["success", "skipped"], (
        f"Unexpected final status: {final_record['fetch_status']}"
    )
    print(
        f"Download task for {EXAMPLE_URL} completed with status: {final_record['fetch_status']}"
    )
    return download_id
    assert final_record.get("canonical_url") == EXAMPLE_URL
    if final_record["fetch_status"] == "success":
        assert final_record.get("http_status") == 200
        assert final_record.get("content_md5") is not None
        # Check path structure (relative to /app/downloads)
        expected_rel_path = "content/example.com/index.html"
        # Get the path from the record and make it absolute *within the container context*
        recorded_path = final_record.get("local_path")
        assert recorded_path, "Local path missing from successful record"
        assert recorded_path == os.path.join("/app/downloads", expected_rel_path), (
            f"Incorrect local_path in index: {recorded_path}"
        )
    elif final_record["fetch_status"] == "skipped":
        # For skipped, path might still be present from previous run if not forced properly
        assert final_record.get("error_message") is not None

    # Verify content file existence using docker exec if successful
    content_path = final_record.get("local_path")
    if content_path and final_record["fetch_status"] == "success":
        proc = run_docker_exec(["test", "-f", content_path])
        assert proc.returncode == 0, (
            f"Content file {content_path} not found inside container. Stderr: {proc.stderr}"
        )
        print(f"[PASS] Content file {content_path} exists.")
    elif final_record["fetch_status"] == "skipped":
        print(
            "[INFO] Download skipped, content file existence not verified in this state."
        )
    elif not content_path:
        pytest.fail(
            f"Download status was {final_record['fetch_status']} but no local_path recorded."
        )

    print("[PASS] Simple Download test complete.")

    async def test_03_search_example_com_success(self, http_client: httpx.AsyncClient):
        """Phase 3a: Search example.com download for expected content."""
        print("\n--- Test: Search Simple (Success Case) ---")
        download_id = TestApiIntegration.download_ids.get("example")
        assert download_id, (
            "Prerequisite test_download_simple_and_verify did not set download_id"
        )

        payload = {
            "download_id": download_id,
            "scan_keywords": ["Example", "Domain"],  # Scan is case-insensitive
            "extract_selector": "title",
            "extract_keywords": None,  # No keyword filter on extracted text
        }
        response = await http_client.post("/search", json=payload)
        assert response.status_code == 200, f"API call failed: {response.text}"
        results = response.json()
        assert isinstance(results, list), "Search response should be a list"
        assert len(results) >= 1, "Expected at least one search result for 'title'"

        # Check content of the first result (assuming title is unique)
        found = False
        for item in results:
            # Normalize whitespace just in case
            extracted = " ".join(item.get("extracted_content", "").split())
            if extracted == "Example Domain":
                assert item.get("selector_matched") == "title"
                found = True
                break
        assert found, (
            f"Expected 'Example Domain' in search results, got: {[item.get('extracted_content') for item in results]}"
        )
        print("[PASS] Search found expected content.")

    async def test_04_search_example_com_no_results(
        self, http_client: httpx.AsyncClient
    ):
        """Phase 3b: Search example.com download for non-existent keyword."""
        print("\n--- Test: Search Simple (No Results Case) ---")
        download_id = TestApiIntegration.download_ids.get("example")
        assert download_id, (
            "Prerequisite test_download_simple_and_verify did not set download_id"
        )

        payload = {
            "download_id": download_id,
            "scan_keywords": ["nonexistentkeywordxyz123"],  # Should not exist
            "extract_selector": "p",  # Selector that would normally match
            "extract_keywords": None,
        }
        response = await http_client.post("/search", json=payload)
        assert response.status_code == 200, f"API call failed: {response.text}"
        results = response.json()
        assert isinstance(results, list), "Search response should be a list"
        assert len(results) == 0, f"Expected empty search results, got: {results}"
        print("[PASS] Search correctly returned no results.")

    async def test_05_search_invalid_id(self, http_client: httpx.AsyncClient):
        """Phase 3c: Tests searching with a non-existent download ID."""
        print("\n--- Test: Search Invalid ID ---")
        payload = {
            "download_id": f"invalid-id-{uuid.uuid4()}",  # Generate unique invalid ID
            "scan_keywords": ["test"],
            "extract_selector": "title",
            "extract_keywords": None,
        }
        response = await http_client.post("/search", json=payload)
        assert response.status_code == 404, (
            f"Expected 404, got {response.status_code}: {response.text}"
        )
        # Check detail message for clarity
        assert "Index file not found" in response.json().get("detail", "")
        print("[PASS] Search correctly returned 404 for invalid ID.")

    async def test_06_download_deeper_and_verify(self, http_client: httpx.AsyncClient):
        """Phase 4: Tests downloading a more complex site (depth 1) and verifies paths."""
        print("\n--- Test: Deeper Download (Python Docs, depth 1) ---")
        payload = {"url": PYTHON_DOCS_URL, "depth": 1, "force": True}
        response = await http_client.post("/download", json=payload)
        assert response.status_code == 200, f"API call failed: {response.text}"
        data = response.json()
        assert data["status"] == "started"
        download_id = data.get("download_id")
        assert download_id, "Did not receive download_id"
        TestApiIntegration.download_ids["python"] = download_id  # Store for search test
        print(f"Download initiated, ID: {download_id}")

        # Longer timeout for root page check
        print(
            f"Waiting up to {POLL_TIMEOUT_SECONDS * 2}s for root page ({PYTHON_DOCS_URL}) download completion..."
        )
        final_record = await poll_for_index_status(
            download_id,
            PYTHON_DOCS_URL,
            {"success", "skipped"},
            timeout=POLL_TIMEOUT_SECONDS * 2,
        )
        assert final_record is not None, (
            f"Download for {PYTHON_DOCS_URL} root did not complete successfully within timeout."
        )
        assert final_record["fetch_status"] in ["success", "skipped"], (
            f"Unexpected final status for root: {final_record['fetch_status']}"
        )
        print(
            f"Root page download task for {PYTHON_DOCS_URL} completed with status: {final_record['fetch_status']}"
        )

        # Verify path structure in the specific record fetched
        expected_rel_path = "content/docs.python.org/3/index.html"
        expected_abs_path = os.path.join(
            "/app/downloads", expected_rel_path
        )  # Path inside container
        assert final_record.get("local_path") == expected_abs_path, (
            f"Incorrect path in index. Expected: '{expected_abs_path}', Got: '{final_record.get('local_path')}'"
        )
        print(
            f"[PASS] Root page path structure is correct: {final_record.get('local_path')}"
        )

        # Verify content file existence for the root page if it was successful
        content_path = final_record.get("local_path")
        if content_path and final_record["fetch_status"] == "success":
            proc = run_docker_exec(["test", "-f", content_path])
            assert proc.returncode == 0, (
                f"Root content file {content_path} not found inside container. Stderr: {proc.stderr}"
            )
            print(f"[PASS] Root content file {content_path} exists.")

        print("[PASS] Deeper Download initiated and root page verified.")
        # Note: We don't wait for *all* depth 1 pages here, just verify the process started
        # and the root page entry/path is correct. Further checks could poll specific depth 1 URLs.

    async def test_07_search_deeper(self, http_client: httpx.AsyncClient):
        """Phase 4b: Tests searching the downloaded python docs content."""
        print("\n--- Test: Search Deeper (Python Docs) ---")
        download_id = TestApiIntegration.download_ids.get("python")
        assert download_id, (
            "Prerequisite test_download_deeper_and_verify did not set download_id"
        )

        # Allow some time for depth 1 pages to be processed *before* searching
        print(
            "Waiting extra 15s for potential depth 1 page completion before search..."
        )
        await asyncio.sleep(15)


        payload = {
            "download_id": download_id,
            "scan_keywords": [
                "asyncio",
                "await",
            ],  # Keywords likely on index or linked pages
            "extract_selector": "p",  # Extract paragraphs
            "extract_keywords": [
                "task",
                "event loop",
            ],  # Find paragraphs mentioning task/event loop
        }
        search_resp = await http_client.post("/search", json=payload)
        assert search_resp.status_code == 200
        results = search_resp.json().get("results", [])
        assert any("asyncio" in str(r) or "await" in str(r) for r in results), "Did not find expected keywords in search results"
        print("[PASS] Search deeper (python docs) verified.")
    # --- New E2E Tests for Complex Scenarios ---

async def test_08_complex_json_extraction_and_search(http_client: httpx.AsyncClient, test_data_server):
    """Test: Complex nested JSON extraction and search (ArangoDB-style)."""
    print("\n--- Test: Complex Nested JSON Extraction and Search ---")
    port = test_data_server
    url = f"http://host.docker.internal:{port}/complex_doc.json"
    payload = {"url": url, "depth": 0, "force": True}
    response = await http_client.post("/download", json=payload)
    assert response.status_code == 200, f"API call failed: {response.text}"
    data = response.json()
    assert data["status"] == "started"
    download_id = data.get("download_id")
    assert download_id, "Did not receive download_id"
    print(f"Download initiated, ID: {download_id}")

    # Wait for download to complete
    record = await poll_for_index_status(download_id, url, {"success"})
    assert record is not None, "Download did not complete successfully"

    # Search for a deeply nested value
    search_payload = {
        "download_id": download_id,
        "scan_keywords": ["Deeply nested value", "target_field"],
        "extract_selector": None,
        "extract_keywords": ["target_field", "Deeply nested value"]
    }
    search_resp = await http_client.post("/search", json=search_payload)
    assert search_resp.status_code == 200
    results = search_resp.json().get("results", [])
    assert any("Deeply nested value" in str(r) for r in results), "Did not find deeply nested value in search results"
    print("[PASS] Complex nested JSON extraction and search verified.")

async def test_09_mixed_markdown_extraction_and_search(http_client: httpx.AsyncClient, test_data_server):
    """Test: Mixed content extraction and search from Markdown (text and code blocks)."""
    print("\n--- Test: Mixed Markdown Content Extraction and Search ---")
    port = test_data_server
    url = f"http://host.docker.internal:{port}/mixed_content.md"
    payload = {"url": url, "depth": 0, "force": True}
    response = await http_client.post("/download", json=payload)
    assert response.status_code == 200, f"API call failed: {response.text}"
    data = response.json()
    assert data["status"] == "started"
    download_id = data.get("download_id")
    assert download_id, "Did not receive download_id"
    print(f"Download initiated, ID: {download_id}")

    record = await poll_for_index_status(download_id, url, {"success"})
    assert record is not None, "Download did not complete successfully"

    # Search for text and code block content
    search_payload = {
        "download_id": download_id,
        "scan_keywords": ["Python", "hello_world", "print", "key", "number"],
        "extract_selector": None,
        "extract_keywords": ["hello_world", "print", "Python", "key", "number"]
    }
    search_resp = await http_client.post("/search", json=search_payload)
    assert search_resp.status_code == 200
    results = search_resp.json().get("results", [])
    # Ensure both text and code block content are indexed and searchable
    assert any("hello_world" in str(r) for r in results), "Code block (hello_world) not found in search results"
    assert any("Python" in str(r) for r in results), "Text content (Python) not found in search results"
    assert any('"key": "value"' in str(r) or "number" in str(r) for r in results), "JSON code block not found"
    print("[PASS] Mixed Markdown content extraction and search verified.")

async def test_10_mixed_html_extraction_and_search(http_client: httpx.AsyncClient, test_data_server):
    """Test: Mixed content extraction and search from HTML (text and code blocks)."""
    print("\n--- Test: Mixed HTML Content Extraction and Search ---")
    port = test_data_server
    url = f"http://host.docker.internal:{port}/mixed_content.html"
    payload = {"url": url, "depth": 0, "force": True}
    response = await http_client.post("/download", json=payload)
    assert response.status_code == 200, f"API call failed: {response.text}"
    data = response.json()
    assert data["status"] == "started"
    download_id = data.get("download_id")
    assert download_id, "Did not receive download_id"
    print(f"Download initiated, ID: {download_id}")

    record = await poll_for_index_status(download_id, url, {"success"})
    assert record is not None, "Download did not complete successfully"

    # Search for text and code block content
    search_payload = {
        "download_id": download_id,
        "scan_keywords": ["JavaScript", "greet", "console.log", "key", "number"],
        "extract_selector": None,
        "extract_keywords": ["greet", "console.log", "JavaScript", "key", "number"]
    }
    search_resp = await http_client.post("/search", json=search_payload)
    assert search_resp.status_code == 200
    results = search_resp.json().get("results", [])
    # Ensure both text and code block content are indexed and searchable
    assert any("greet" in str(r) for r in results), "Code block (greet) not found in search results"
    assert any("JavaScript" in str(r) for r in results), "Text content (JavaScript) not found in search results"
    assert any('"key": "value"' in str(r) or "number" in str(r) for r in results), "JSON code block not found"
    print("[PASS] Mixed HTML content extraction and search verified.")

async def test_11_crawler_depth_limit(http_client: httpx.AsyncClient, test_data_server):
    """Test: Documentation crawling depth limit and link following."""
    print("\n--- Test: Documentation Crawler Depth Limit ---")
    port = test_data_server
    root_url = f"http://host.docker.internal:{port}/depth_root.html"
    payload = {"url": root_url, "depth": 1, "force": True}
    response = await http_client.post("/download", json=payload)
    assert response.status_code == 200, f"API call failed: {response.text}"
    data = response.json()
    assert data["status"] == "started"
    download_id = data.get("download_id")
    assert download_id, "Did not receive download_id"
    print(f"Download initiated, ID: {download_id}")

    # Wait for root and depth 1 children to be downloaded
    record = await poll_for_index_status(download_id, root_url, {"success"})
    assert record is not None, "Root page download did not complete successfully"

    # Check that depth_child1.html and depth_child2.html are present in the index (depth 1)
    child1_url = f"http://host.docker.internal:{port}/depth_child1.html"
    child2_url = f"http://host.docker.internal:{port}/depth_child2.html"
    child1_record = await poll_for_index_status(download_id, child1_url, {"success", "skipped", "error"})
    child2_record = await poll_for_index_status(download_id, child2_url, {"success", "skipped", "error"})
    assert child1_record is not None, "Child 1 page not found in index at depth 1"
    assert child2_record is not None, "Child 2 page not found in index at depth 1"

    # Now, set depth=0 and verify only root is downloaded
    payload = {"url": root_url, "depth": 0, "force": True}
    response = await http_client.post("/download", json=payload)
    assert response.status_code == 200, f"API call failed: {response.text}"
    data = response.json()
    assert data["status"] == "started"
    download_id_0 = data.get("download_id")
    assert download_id_0, "Did not receive download_id for depth=0"
    print(f"Download (depth=0) initiated, ID: {download_id_0}")

    record_0 = await poll_for_index_status(download_id_0, root_url, {"success"})
    assert record_0 is not None, "Root page download (depth=0) did not complete successfully"
    # Should not find children at depth=0
    child1_record_0 = await poll_for_index_status(download_id_0, child1_url, {"success", "skipped", "error"})
    child2_record_0 = await poll_for_index_status(download_id_0, child2_url, {"success", "skipped", "error"})
    assert child1_record_0 is None, "Child 1 should not be downloaded at depth=0"
    assert child2_record_0 is None, "Child 2 should not be downloaded at depth=0"
    print("[PASS] Crawler depth limit and link following verified.")

async def test_12_code_block_prioritization(http_client: httpx.AsyncClient, test_data_server):
    """Test: Search results prioritize matches found within code blocks."""
    print("\n--- Test: Code Block Prioritization in Search Results ---")
    port = test_data_server
    url = f"http://host.docker.internal:{port}/mixed_content.md"
    payload = {"url": url, "depth": 0, "force": True}
    response = await http_client.post("/download", json=payload)
    assert response.status_code == 200, f"API call failed: {response.text}"
    data = response.json()
    assert data["status"] == "started"
    download_id = data.get("download_id")
    assert download_id, "Did not receive download_id"
    print(f"Download initiated, ID: {download_id}")

    record = await poll_for_index_status(download_id, url, {"success"})
    assert record is not None, "Download did not complete successfully"

    # Search for a term present in both text and code block ("print")
    search_payload = {
        "download_id": download_id,
        "scan_keywords": ["print"],
        "extract_selector": None,
        "extract_keywords": ["print"]
    }
    search_resp = await http_client.post("/search", json=search_payload)
    assert search_resp.status_code == 200
    results = search_resp.json().get("results", [])
    # Check that the first result(s) prioritize code block matches
    found_code_block = False
    for r in results[:3]:  # Check top 3 results
        if "def hello_world" in str(r) or "print(" in str(r):
            found_code_block = True
            break
    assert found_code_block, "Code block match for 'print' not prioritized in search results"
    print("[PASS] Code block prioritization in search results verified.")
    assert response.status_code == 200, f"API call failed: {response.text}"
    results = response.json()
    assert isinstance(results, list), "Search response should be a list"
    # We expect *some* results, hard to predict exact number/content reliably
    assert len(results) > 0, (
        f"Expected some search results for 'asyncio/await/task/event loop', got none."
    )
    print(
        f"[PASS] Search on deeper download found {len(results)} results (at least 1 expected)."
    )

    # --- Optional Playwright Test ---
    # Uncomment and potentially adjust if Playwright functionality needs specific testing
    # @pytest.mark.asyncio
    # async def test_08_download_playwright(self, http_client: httpx.AsyncClient):
    #     """Phase 5: Tests forcing playwright download."""
    #     print("\n--- Test: Playwright Download (example.com, depth 0) ---")
    #     # Optional: Check if Playwright seems installed in container first
    #     proc_check = run_docker_exec(["python", "-m", "playwright", "--version"])
    #     if proc_check.returncode != 0:
    #         pytest.skip(f"Playwright command check failed in container: {proc_check.stderr}")

    #     payload = {"url": EXAMPLE_URL, "depth": 0, "force": True, "use_playwright": True}
    #     response = await http_client.post("/download", json=payload)
    #     assert response.status_code == 200, f"API call failed: {response.text}"
    #     data = response.json()
    #     assert data["status"] == "started"
    #     download_id = data.get("download_id")
    #     assert download_id, "Did not receive download_id"
    #     print(f"Playwright Download initiated, ID: {download_id}")

    #     print(f"Waiting up to {POLL_TIMEOUT_SECONDS}s for Playwright download completion...")
    #     final_record = await poll_for_index_status(download_id, EXAMPLE_URL, {"success", "skipped"})
    #     assert final_record is not None, f"Playwright download for {EXAMPLE_URL} did not complete successfully."
    #     assert final_record["fetch_status"] in ["success", "skipped"]

    #     # Verify http_status is populated for Playwright downloads
    #     assert final_record.get("http_status") is not None, "Playwright result should include http_status"
    #     if final_record["fetch_status"] == "success":
    #          assert final_record.get("http_status") == 200
    #          assert final_record.get("content_md5") is not None

    #     print("[PASS] Playwright Download test complete (basic checks passed).")
