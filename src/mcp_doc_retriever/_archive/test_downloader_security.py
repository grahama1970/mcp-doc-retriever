import asyncio
import os
import tempfile
import aiofiles
import pytest
from src.mcp_doc_retriever.downloader import fetch_single_url_requests, fetch_single_url_playwright
import unittest.mock
import httpx

# Existing test cases (keep all current tests)
# ... [previous test content remains unchanged] ...

# New security tests for concurrency control
@pytest.mark.asyncio
async def test_semaphore_race_condition(tmpdir):
    """Test race condition in semaphore acquisition"""
    target_file = os.path.join(tmpdir, "race_test.bin")
    
    # Simulate high contention for semaphores
    async def mock_get(*args, **kwargs):
        await asyncio.sleep(0.1)  # Simulate network delay
        class FakeResponse:
            def __init__(self):
                self.text = "test"
                self.headers = {}
            def raise_for_status(self):
                pass
            async def aiter_bytes(self, chunk_size=8192):
                yield b"test"
        return FakeResponse()

    # Create many concurrent download tasks
    tasks = []
    with unittest.mock.patch("httpx.AsyncClient.get", side_effect=mock_get):
        for i in range(20):  # Exceeds semaphore limit of 10
            tasks.append(
                fetch_single_url_requests(
                    f"https://example.com/race_{i}",
                    target_file,
                    force=True,
                    allowed_base_dir=str(tmpdir)
                )
            )
        
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        # Verify semaphore limit was respected (allow some failures due to contention)
        success_count = sum(1 for r in results if isinstance(r, dict) and r['status'] == 'success')
        failure_count = sum(1 for r in results if isinstance(r, dict) and r['status'] != 'success')
        assert success_count <= 10, "Semaphore limit exceeded"
        assert success_count + failure_count == 20, "Missing results"
        assert success_count >= 8, "Too many failures (expected most to succeed)"

@pytest.mark.asyncio
async def test_deadlock_scenario(tmpdir):
    """Test potential deadlock between semaphores and locks"""
    target_file = os.path.join(tmpdir, "deadlock_test.html")
    
    # Simulate deadlock-prone scenario
    class FakePage:
        async def goto(self, *args, **kwargs):
            await asyncio.sleep(0.2)  # Simulate delay that could cause deadlock
            return None
        async def content(self):
            return "<html></html>"
        async def close(self):
            pass

    class FakeContext:
        async def new_page(self):
            return FakePage()
        async def close(self):
            pass

    class FakeBrowser:
        async def new_context(self):
            return FakeContext()
        async def close(self):
            pass

    class FakePlaywright:
        def __init__(self):
            self.chromium = self
        async def launch(self, **kwargs):
            return FakeBrowser()
        async def __aenter__(self):
            return self
        async def __aexit__(self, exc_type, exc, tb):
            pass

    class FakeAsyncPlaywrightCtxMgr:
        async def __aenter__(self):
            return FakePlaywright()
        async def __aexit__(self, exc_type, exc, tb):
            pass

    def fake_async_playwright():
        return FakeAsyncPlaywrightCtxMgr()

    import src.mcp_doc_retriever.downloader as downloader_module
    with unittest.mock.patch("playwright.async_api.async_playwright", new=fake_async_playwright):
        # Create many concurrent playwright tasks
        tasks = []
        for i in range(5):  # Exceeds semaphore limit of 3
            tasks.append(
                fetch_single_url_playwright(
                    f"https://example.com/deadlock_{i}",
                    target_file,
                    force=True,
                    allowed_base_dir=str(tmpdir)
                )
            )
        
        # Add timeout to prevent hanging
        try:
            results = await asyncio.wait_for(
                asyncio.gather(*tasks, return_exceptions=True),
                timeout=5.0
            )
            # Verify all tasks completed (no deadlock)
            assert len(results) == 5
        except asyncio.TimeoutError:
            pytest.fail("Potential deadlock detected - tasks did not complete")

@pytest.mark.asyncio
async def test_browser_count_exhaustion(tmpdir):
    """Test resource exhaustion via browser count tracking"""
    target_file = os.path.join(tmpdir, "exhaustion_test.html")
    
    # Simulate many concurrent browsers
    class FakePage:
        async def goto(self, *args, **kwargs):
            return None
        async def content(self):
            return "<html></html>"
        async def close(self):
            pass

    class FakeContext:
        async def new_page(self):
            return FakePage()
        async def close(self):
            pass

    class FakeBrowser:
        async def new_context(self):
            return FakeContext()
        async def close(self):
            pass

    class FakePlaywright:
        def __init__(self):
            self.chromium = self
        async def launch(self, **kwargs):
            return FakeBrowser()
        async def __aenter__(self):
            return self
        async def __aexit__(self, exc_type, exc, tb):
            pass

    class FakeAsyncPlaywrightCtxMgr:
        async def __aenter__(self):
            return FakePlaywright()
        async def __aexit__(self, exc_type, exc, tb):
            pass

    def fake_async_playwright():
        return FakeAsyncPlaywrightCtxMgr()

    import src.mcp_doc_retriever.downloader as downloader_module
    with unittest.mock.patch("playwright.async_api.async_playwright", new=fake_async_playwright):
        # Reset counters for clean test
        downloader_module.active_browser_count = 0
        
        # Create many concurrent playwright tasks
        tasks = []
        for i in range(15):  # Exceeds warning threshold of 10
            tasks.append(
                fetch_single_url_playwright(
                    f"https://example.com/exhaustion_{i}",
                    target_file,
                    force=True,
                    allowed_base_dir=str(tmpdir)
                )
            )
        
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        # Verify browser count was properly tracked
        assert downloader_module.active_browser_count == 0, "Browser count leak detected"
        success_count = sum(1 for r in results if isinstance(r, dict) and r['status'] == 'success')
        assert success_count > 0, "All requests failed unexpectedly"

@pytest.mark.asyncio
async def test_semaphore_leak_on_error(tmpdir):
    """Test semaphores are properly released on error"""
    target_file = os.path.join(tmpdir, "leak_test.bin")
    
    # Simulate error after semaphore acquisition
    async def mock_get(*args, **kwargs):
        raise httpx.RequestError("Simulated error")

    import src.mcp_doc_retriever.downloader as downloader_module
    initial_semaphore_value = downloader_module.requests_semaphore._value
    
    with unittest.mock.patch("httpx.AsyncClient.get", side_effect=mock_get):
        result = await fetch_single_url_requests(
            "https://example.com/leak",
            target_file,
            force=True,
            allowed_base_dir=str(tmpdir)
        )
        
        # Verify semaphore was released
        assert downloader_module.requests_semaphore._value == initial_semaphore_value, "Semaphore leak detected"
        assert result['status'] == 'failed_request'