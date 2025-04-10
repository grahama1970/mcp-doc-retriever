# Explanation and How to Use:
Placement: Save this code as tests/test_integration_api.py.
Prerequisites: Make sure pytest, pytest-asyncio, and httpx are installed in your development environment (.venv).
Fixture (docker_service):
This pytest fixture uses scope="module" and autouse=True. This means it will run automatically once before any tests in this file (module) start and once after they all finish.
Setup: It runs docker compose down -v to ensure a clean state, then docker compose up --build -d to build (if needed) and start the service in the background. It then polls the /health endpoint for up to HEALTH_TIMEOUT seconds to ensure the service is ready before tests begin. If it fails to start or become healthy, it fails the entire test suite early.
Teardown: After all tests in the file run, it executes docker compose down -v again to stop the container and remove the volume, cleaning up.
HTTP Client Fixture (http_client): Provides a shared httpx.AsyncClient for all tests within the module, configured with the base URL and timeout.
Test Class (TestApiIntegration): Groups the tests. Using a class allows sharing state if needed (like download_ids). The @pytest.mark.asyncio decorator marks the class so all test methods within it can be async.
Test Naming (test_01_..., test_02_...): Using numbered prefixes ensures pytest runs the tests in a predictable order, which is important here since some tests rely on downloads initiated by previous tests (e.g., search tests rely on example_download_id).
Polling (poll_for_index_status): Since downloads happen in the background, tests that need to verify download results use this helper function to poll the index file (via docker exec cat ...) until the entry for the target URL reaches a final state (success or skipped) or a timeout occurs.
Assertions: Uses standard assert statements to check status codes, response JSON content, file existence (docker exec test -f), and index file content (docker exec cat ... + parsing).
Running: Execute from your project root:
pytest tests/integration/test_api_e2e.py -v -s
Use code with caution.
Bash
This Python/pytest approach provides a much more robust, readable, and maintainable way to test your API's integration points compared to the Bash script.