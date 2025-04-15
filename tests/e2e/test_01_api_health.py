# tests/e2e/test_01_api_health.py
import requests
from .conftest import BASE_URL  # Import from conftest


def test_health_check(mcp_service):  # Depends on service being up
    """Verify the /health endpoint returns 200 and status healthy."""
    url = f"{mcp_service}/health"  # Use fixture providing the URL
    response = requests.get(url, timeout=10)
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "healthy"
    assert "timestamp" in data
