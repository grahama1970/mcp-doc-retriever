**Phase 0: Basic Module Verification**

*   **Action:** As outlined in `task.md`, run `uv run python src/mcp_doc_retriever/....py` for each file with a `if __name__ == "__main__":` block. Fix any errors that arise *before* proceeding. This ensures basic syntax and import correctness.

**Phase 1: Unit & Integration Testing (Local, No API/Docker)**

*   **Action:** Write and run the unit tests (`tests/unit/...`) and integration tests (`tests/integration/...`) as described in `task.md` tasks 1.1 - 1.6.
*   **Focus:** These tests should *not* require the FastAPI server or Docker. They test Python functions directly, perhaps using mock data, `tmp_path`, `httpbin.org`, or local Git repos.
*   **Key:** **Ensure tests in `tests/integration/downloader/test_web.py` and `tests/integration/searcher/test_searcher.py` specifically validate the new flat/hashed path structure.** For example, the web integration test should call the download function, then assert that a file exists at `<tmp_path>/downloads/content/<id>/example.com/http_example.com_-<some_hash>.html` and that the corresponding index record points to this path.
*   **Command:** `uv run pytest tests/unit tests/integration`

**Phase 2a: Local API & CLI Testing**

*   **Action 1: Start Local FastAPI Server:**
    *   Open a separate terminal.
    *   Navigate to your project root.
    *   Run: `uvicorn src.mcp_doc_retriever.main:app --reload --port 8000` (using port 8000 to avoid conflict with Docker's 8001). Watch for any startup errors.
*   **Action 2: Run API Tests Against Local Server:**
    *   We need a way to tell the `test_mcp_retriever_e2e_loguru.py` script to target `http://localhost:8000`. We can use an environment variable or add specific test functions. Let's modify `api_request` slightly to allow overriding the base URL easily. (Done in the Loguru example above).
    *   Run specific tests targeting the local server:
        ```bash
        # Example: Test only health against local server
        MCP_TEST_BASE_URL="http://localhost:8000" uv run pytest -v -s -k test_phase1_api_health tests/test_mcp_retriever_e2e_loguru.py

        # Example: Test only web download against local server
        MCP_TEST_BASE_URL="http://localhost:8000" uv run pytest -v -s -k test_phase2_api_web_download tests/test_mcp_retriever_e2e_loguru.py
        ```
        *Note:* The `docker_setup_teardown` fixture will still run but won't be strictly necessary for *these specific* local API tests (though cleanup of the host dir might still be useful). The key is overriding `BASE_URL`. The Docker-specific parts like `check_file_in_container` won't work here, so tests relying on those will need adaptation or separate local versions.
*   **Action 3: Run CLI Tests Locally:**
    *   Run the CLI tests (Phase 6, 7) *without* relying on Docker setup. These tests already use `run_subprocess` to execute `uv run python -m ...` locally. They should create files in the `HOST_CLI_TEST_DIR`.
    *   Command: `uv run pytest -v -s -k "test_phase6 or test_phase7" tests/test_mcp_retriever_e2e_loguru.py`
*   **Goal:** Ensure the API endpoints (`/health`, `/download`, `/status`, `/search`) and the CLI commands work correctly in your local environment, interacting with your local filesystem. Fix any `TypeError` in `/search` or other bugs found here.

**Phase 2b: Docker E2E Testing**

*   **Action:** *Only after* Phase 2a (Local API/CLI) is passing reliably.
    *   Stop the local `uvicorn` server.
    *   Run the full E2E test suite using the Docker fixture. Set the `BASE_URL` back to the Docker port (or let it default).
    *   Command: `uv run pytest -v -s tests/test_mcp_retriever_e2e_loguru.py` (or use `-m phaseN` / `-k test_name` to run specific ones against Docker).
*   **Goal:** Verify the application works correctly *when containerized*. Debug issues specific to the Docker environment (volumes, networking, permissions, dependencies missing in Dockerfile). The Git index timeout and any remaining search errors specific to the container environment would be tackled here.

**Phase 3 & 4: Docs, Deployment Prep, Review**

*   **Action:** Proceed with these tasks once all automated tests (local and Docker) are passing.

**Summary of Changes:**

1.  **Adopt Loguru:** Replaced standard logging and print helpers with Loguru.
2.  **Fix `wait_time` Bug:** Corrected the `UnboundLocalError` in the fixture.
3.  **Prioritize Local Testing:** Clearly defined the steps to test locally first (standalone modules -> unit/integration -> local API/CLI) before moving to Docker E2E.
4.  **Target Local API:** Showed how to run tests against the local `uvicorn` server by overriding the `BASE_URL`.
5.  **Clarify Test Scope:** Differentiated between tests that run purely locally and those that interact with the API (local or Docker).

This revised plan tackles the complexity step-by-step, making debugging much more manageable. Start with Phase 0 and 1, then Phase 2a (local API/CLI), and only then Phase 2b (Docker E2E).