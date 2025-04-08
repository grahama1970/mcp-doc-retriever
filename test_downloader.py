import subprocess
import sys

result = subprocess.run([sys.executable, "scripts/check_deps.py"])
if result.returncode != 0:
    print("Dependency check failed. Exiting tests.")
    sys.exit(1)

import asyncio
import httpx
from unittest.mock import patch
import os
import hashlib
import tempfile
import shutil
from src.mcp_doc_retriever.downloader import fetch_single_url_requests

TEST_URL = "https://example.com"  # small, reliable test URL
INVALID_URL = "http://nonexistent.invalid"

def print_result(test_name, passed, error=None, result=None):
    print(f"\n=== {test_name} ===")
    if passed:
        print("PASS")
    else:
        print("FAIL")
        if error:
            print(f"Error: {error}")
        if result:
            print(f"Result: {result}")

async def test_successful_download_force_true(tmpdir):
    test_name = "Successful download with force=True"
    target_file = os.path.join(tmpdir, "download.html")
    try:
        async def mock_send(request, **kwargs):
            content = b"<html><body>Test Content</body></html>"
            return httpx.Response(200, content=content, headers={"Content-Length": str(len(content))})
        transport = httpx.MockTransport(mock_send)
        client = httpx.AsyncClient(transport=transport)
        with patch("httpx.AsyncClient", return_value=client):
            result = await fetch_single_url_requests(TEST_URL, target_file, force=True, allowed_base_dir=tmpdir)
        assert result['status'] == 'success', f"Unexpected status: {result['status']}"
        assert os.path.exists(target_file), "File was not created"
        with open(target_file, 'rb') as f:
            content = f.read()
        md5_manual = hashlib.md5(content).hexdigest()
        assert result['content_md5'] == md5_manual, "MD5 mismatch"
        assert isinstance(result['detected_links'], list), "Links not a list"
        print_result(test_name, True)
    except Exception as e:
        print_result(test_name, False, str(e), result)

async def test_no_clobber_force_false(tmpdir):
    test_name = "No-clobber behavior with force=False"
    target_file = os.path.join(tmpdir, "download.html")
    try:
        # Pre-create file
        with open(target_file, 'w') as f:
            f.write("existing content")
        result = await fetch_single_url_requests(TEST_URL, target_file, force=False, allowed_base_dir=tmpdir)
        assert result['status'] == 'skipped', f"Unexpected status: {result['status']}"
        with open(target_file, 'r') as f:
            content = f.read()
        assert content == "existing content", "File was overwritten unexpectedly"
        print_result(test_name, True)
    except Exception as e:
        print_result(test_name, False, str(e))

async def test_invalid_url(tmpdir):
    test_name = "Error handling for invalid URLs"
    target_file = os.path.join(tmpdir, "bad.html")
    try:
        result = await fetch_single_url_requests(INVALID_URL, target_file, force=True, allowed_base_dir=tmpdir)
        assert result['status'] == 'failed', f"Unexpected status: {result['status']}"
        assert result['error_message'] is not None, "No error message provided"
        print_result(test_name, True)
    except Exception as e:
        print_result(test_name, False, str(e))

async def test_directory_creation(tmpdir):
    test_name = "Directory creation"
    nested_dir = os.path.join(tmpdir, "nested", "dir", "structure")
    target_file = os.path.join(nested_dir, "file.html")
    try:
        result = await fetch_single_url_requests(TEST_URL, target_file, force=True, allowed_base_dir=tmpdir)
        assert result['status'] == 'success', f"Unexpected status: {result['status']}"
        assert os.path.exists(target_file), "File was not created in nested directory"
        print_result(test_name, True)
    except Exception as e:
        print_result(test_name, False, str(e))

async def test_md5_hash(tmpdir):
    test_name = "MD5 hash calculation"
    target_file = os.path.join(tmpdir, "md5test.html")
    try:
        result = await fetch_single_url_requests(TEST_URL, target_file, force=True, allowed_base_dir=tmpdir)
        assert result['status'] == 'success', f"Unexpected status: {result['status']}"
        with open(target_file, 'rb') as f:
            content = f.read()
        md5_manual = hashlib.md5(content).hexdigest()
        assert result['content_md5'] == md5_manual, "MD5 mismatch"
        print_result(test_name, True)
    except Exception as e:
        print_result(test_name, False, str(e))

async def main():
    with tempfile.TemporaryDirectory() as tmpdir:
        await test_successful_download_force_true(tmpdir)
        await test_no_clobber_force_false(tmpdir)
        await test_invalid_url(tmpdir)
        await test_directory_creation(tmpdir)
        await test_md5_hash(tmpdir)

if __name__ == "__main__":
    asyncio.run(main())