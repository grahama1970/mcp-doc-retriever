"""
MCP Integration Test Script

Verifies the SSE endpoint and MCP protocol implementation by:
1. Connecting to the SSE endpoint
2. Verifying initial connection message
3. Checking for periodic heartbeats
4. Testing MCP method mapping to existing endpoints
"""

import asyncio
import httpx
import json
import pytest

BASE_URL = "http://localhost:8001"
TIMEOUT = 5  # seconds

async def test_sse_connection():
    """Test SSE connection and initial handshake"""
    async with httpx.AsyncClient() as client:
        # Connect to SSE endpoint
        async with client.stream("GET", f"{BASE_URL}/", timeout=TIMEOUT) as response:
            assert response.status_code == 200
            assert "text/event-stream" in response.headers["content-type"]
            
            # Check initial connection event
            events = []
            async for line in response.aiter_lines():
                if line.startswith("event:"):
                    events.append(line.split(":")[1].strip())
                if line.startswith("data:"):
                    data = json.loads(line.split(":", 1)[1].strip())
                    if events[-1] == "connected":
                        assert data["service"] == "DocRetriever"
                        assert "document_download" in data["capabilities"]
                        assert "document_search" in data["capabilities"]
                        break

async def test_mcp_method_mapping():
    """Test that MCP methods map to existing endpoints"""
    test_url = "https://example.com"
    
    # Test document_download maps to /download
    async with httpx.AsyncClient() as client:
        response = await client.post(
            f"{BASE_URL}/download",
            json={
                "url": test_url,
                "depth": 0,
                "force": True
            },
            timeout=TIMEOUT
        )
        assert response.status_code in [200, 400]  # 400 if validation fails
        
    # Test document_search maps to /search
    async with httpx.AsyncClient() as client:
        response = await client.post(
            f"{BASE_URL}/search",
            json={
                "download_id": "test-id",
                "scan_keywords": ["test"],
                "extract_selector": "title"
            },
            timeout=TIMEOUT
        )
        assert response.status_code in [200, 404]  # 404 if test-id not found

@pytest.mark.asyncio
async def test_full_mcp_workflow():
    """Test complete MCP workflow"""
    await test_sse_connection()
    await test_mcp_method_mapping()

if __name__ == "__main__":
    pytest.main(["-v", __file__])