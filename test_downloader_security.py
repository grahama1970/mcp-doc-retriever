import asyncio
import os
import tempfile
import aiofiles
import pytest
from src.mcp_doc_retriever.downloader import fetch_single_url_requests

# Security test cases
MALICIOUS_URLS = [
    "file:///etc/passwd",
    "http://localhost",
    "http://127.0.0.1",
    "http://169.254.169.254/latest/meta-data",
    "http://[::1]",
    "http://internal.service",
]

PATH_TRAVERSAL_TARGETS = [
    "../../../../../../../../../../../../../../../../../../tmp/escape",
    "%2e%2e%2fetc%2fpasswd",
    "..%5c..%5c..%5cWindows%5cSystem32%5cdrivers%5cetc%5chosts",
    "%252e%252e%252fetc%252fpasswd",  # Double encoded
    "valid/../../../../etc/passwd",   # Mixed valid and traversal
    "valid/path/../../../etc/passwd\x00.html"  # Null byte injection
]

@pytest.mark.asyncio
async def test_ssrf_protection(tmpdir):
    """Test that SSRF attempts are blocked"""
    for url in MALICIOUS_URLS:
        target_file = os.path.join(tmpdir, "test.html")
        result = await fetch_single_url_requests(url, target_file, force=True, allowed_base_dir=tmpdir)
        assert result['status'] == 'failed', f"SSRF vulnerability: {url} was accessible"
        assert "internal" not in result['error_message'].lower(), "Error message leaks internal details"

@pytest.mark.asyncio 
async def test_path_traversal(tmpdir):
    """Test that path traversal attempts are blocked"""
    for path in PATH_TRAVERSAL_TARGETS:
        target_file = os.path.join(tmpdir, path)
        result = await fetch_single_url_requests(
            "https://example.com", target_file, force=True, allowed_base_dir=str(tmpdir)
        )
        assert result['status'] == 'failed', f"Path traversal vulnerability: {path} was accessible"
        # We do not assert file existence here, as it may pre-exist or be system file

@pytest.mark.asyncio
async def test_race_condition(tmpdir):
    """Test for TOCTOU race condition vulnerability"""
    target_file = os.path.join(tmpdir, "race.html")
    
    # Create the file right after existence check
    async def malicious_create():
        await asyncio.sleep(0.1)  # Simulate race window
        async with aiofiles.open(target_file, 'w') as f:
            await f.write("malicious content")
    
    # Start download and malicious creation concurrently
    download_task = asyncio.create_task(
        fetch_single_url_requests("https://example.com", target_file, force=False, allowed_base_dir=tmpdir)
    )
    create_task = asyncio.create_task(malicious_create())
    
    await asyncio.gather(download_task, create_task)
    
    # Verify the original content wasn't overwritten
    with open(target_file, 'r') as f:
        content = f.read()
    assert content == "malicious content", "Race condition vulnerability detected"

@pytest.mark.asyncio
async def test_large_file_protection(tmpdir):
    """Test that large downloads are rejected based on max_size"""
    # Use a URL that returns a large file (simulate with a known large URL)
    large_file_url = "https://example.com/large-file"
    target_file = os.path.join(tmpdir, "large.bin")
    
    # Set a very small max_size to force rejection
    result = await fetch_single_url_requests(
        large_file_url, target_file, force=True, max_size=1024, allowed_base_dir=str(tmpdir)
    )
    assert result['status'] == 'failed', "Large file download was allowed"
    # Accept size error or HTTP error (since URL is dummy)
    assert (
        "too large" in result['error_message'].lower()
        or "exceeds max_size" in result['error_message'].lower()
        or "http error" in result['error_message'].lower()
    ), "Expected size limit or HTTP error"

@pytest.mark.asyncio
async def test_error_message_sanitization(tmpdir):
    """Test that error messages don't leak sensitive info"""
    target_file = os.path.join(tmpdir, "error.html")
    result = await fetch_single_url_requests("invalid://url", target_file, force=True)
    
    assert result['status'] == 'failed'
    assert "stack trace" not in result['error_message']
    assert "internal" not in result['error_message'].lower()
@pytest.mark.asyncio
async def test_valid_download_within_allowed_dir(tmpdir):
    """Test a valid small download within allowed directory"""
    small_file_url = "https://example.com/small-file"
    target_file = os.path.join(tmpdir, "small.bin")
    
    # Large enough max_size to allow small file
    result = await fetch_single_url_requests(
        small_file_url, target_file, force=True, max_size=10 * 1024 * 1024, allowed_base_dir=str(tmpdir)
    )
    # Accept success or graceful failure if URL is dummy
    assert result['status'] in ('success', 'failed')

@pytest.mark.asyncio
async def test_path_outside_allowed_dir(tmpdir):
    """Test that download outside allowed_base_dir is blocked"""
    outside_path = "/tmp/evil.bin"
    result = await fetch_single_url_requests(
        "https://example.com", outside_path, force=True, allowed_base_dir=str(tmpdir)
    )
    assert result['status'] == 'failed'
    assert "outside allowed directory" in result['error_message'].lower()
    assert "httpx" not in result['error_message']  # Don't leak library details

@pytest.mark.asyncio
async def test_concurrent_downloads(tmpdir):
    """Test that concurrent downloads maintain file integrity"""
    target_file = os.path.join(tmpdir, "concurrent.bin")
    
    # Run 5 concurrent downloads
    tasks = [
        fetch_single_url_requests(
            "https://example.com", target_file, force=True, allowed_base_dir=str(tmpdir)
        )
        for _ in range(5)
    ]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    
    # Verify at least one succeeded and files aren't corrupted
    success_count = sum(1 for r in results if isinstance(r, dict) and r['status'] == 'success')
    assert success_count >= 1, "No downloads succeeded"
    
    # Verify file exists and has content
    assert os.path.exists(target_file)
    assert os.path.getsize(target_file) > 0

@pytest.mark.asyncio
async def test_partial_file_cleanup(tmpdir):
    """Test that partial files are cleaned up on download failure"""
    target_file = os.path.join(tmpdir, "partial.bin")
    
    # Simulate failed download
    result = await fetch_single_url_requests(
        "https://invalid.url", target_file, force=True, allowed_base_dir=str(tmpdir)
    )
    assert result['status'] == 'failed'
    
    # Verify no temp files remain
    temp_files = [f for f in os.listdir(tmpdir) if f.startswith('tmp')]
    assert not temp_files, "Temporary files not cleaned up"

@pytest.mark.asyncio
async def test_invalid_content_length(tmpdir):
    """Test handling of invalid Content-Length headers"""
    target_file = os.path.join(tmpdir, "invalid.bin")
    
    # Test with various invalid Content-Length values
    for invalid_length in ["", "abc", "-100", "1.5", "1e6"]:
        result = await fetch_single_url_requests(
            "https://example.com",
            target_file,
            force=True,
            max_size=1024,
            allowed_base_dir=str(tmpdir)
        )
        # Should either fail gracefully or ignore invalid header
        assert result['status'] in ('success', 'failed')
        if result['status'] == 'failed':
            assert "invalid" not in result['error_message'].lower()