"""
Integration tests for the Web downloader workflow using httpbin.org.
Verifies basic fetch, recursion, Playwright, robots, 404s, force flag,
and crucially, the flat/hashed file path structure and index records.
"""
import pytest
import asyncio
import shutil
from pathlib import Path
import json
from urllib.parse import urlparse
import os
import time
from concurrent.futures import ThreadPoolExecutor

# Import the workflow function to test
from mcp_doc_retriever.downloader.workflow import fetch_documentation_workflow
# Import helpers and models needed for verification
from mcp_doc_retriever.downloader.helpers import url_to_local_path
from mcp_doc_retriever.models import IndexRecord # Only IndexRecord needed from models

# Base URL for testing
HTTPBIN_BASE = "https://httpbin.org"

@pytest.fixture
def temp_download_dir(tmp_path: Path) -> Path:
    """Creates a temporary directory for downloads."""
    download_dir = tmp_path / "web_test_downloads_workflow"
    download_dir.mkdir()
    yield download_dir
    # Clean up unless tests fail? For now, keep it.
    # shutil.rmtree(download_dir)

@pytest.fixture(scope="module")
def shared_executor():
    """Provides a shared ThreadPoolExecutor for the module."""
    executor = ThreadPoolExecutor(max_workers=2)
    yield executor
    executor.shutdown(wait=True)

def check_index_record(index_file: Path, url: str, expected_status: str, expected_local_path: Path | None = None, check_error: bool = False):
    """Checks the index file for a specific URL record."""
    assert index_file.is_file(), f"Index file {index_file} not found or is not a file."
    found = False
    with open(index_file, "r") as f:
        for line in f:
            try:
                record_data = json.loads(line)
            except json.JSONDecodeError:
                pytest.fail(f"Failed to decode JSON line in {index_file}: {line.strip()}")

            # Check canonical_url first, fallback to original_url if needed
            record_url = record_data.get("canonical_url", record_data.get("original_url"))
            if record_url == url:
                found = True
                # Compare string status values directly
                assert record_data.get("fetch_status") == expected_status, f"Expected status '{expected_status}' but got '{record_data.get('fetch_status')}' for {url}"
                if expected_local_path:
                    # Normalize paths for comparison (resolve potentially relative paths)
                    assert record_data.get("local_path"), f"Expected local_path {expected_local_path.resolve()} but got None or empty for {url}"
                    assert Path(record_data["local_path"]).resolve() == expected_local_path.resolve(), f"Expected local_path {expected_local_path.resolve()} but got {Path(record_data['local_path']).resolve()} for {url}"
                else:
                    assert record_data.get("local_path") is None or record_data["local_path"] == "", f"Expected null or empty local_path but got '{record_data.get('local_path')}' for {url}"
                if check_error:
                     assert record_data.get("error_message") is not None and record_data["error_message"] != "", f"Expected an error message for {url}"
                else:
                     assert record_data.get("error_message") is None or record_data["error_message"] == "", f"Expected no error message for {url}, got '{record_data.get('error_message')}'"
                break
    assert found, f"Record for URL {url} not found in index file {index_file}."

@pytest.mark.asyncio
async def test_web_basic_fetch_workflow(temp_download_dir: Path, shared_executor: ThreadPoolExecutor):
    """Tests fetching a single HTML page via workflow and verifies path/index."""
    url = f"{HTTPBIN_BASE}/html"
    download_id = "test_basic_fetch_workflow"
    index_file_path = temp_download_dir / "index" / f"{download_id}.jsonl"
    content_base_dir = temp_download_dir / "content" / download_id

    # Run workflow first
    await fetch_documentation_workflow(
        source_type="website",
        download_id=download_id,
        url=url,
        base_dir=temp_download_dir,
        depth=0, # Only fetch the root URL
        force=False,
        max_concurrent_requests=2,
        executor=shared_executor
    )

    # Calculate expected path *after* workflow ensures directory exists
    expected_file_path = url_to_local_path(content_base_dir, url)

    # Verify content file exists at the expected path
    assert expected_file_path.is_file(), f"Expected file not found at {expected_file_path}"
    content = expected_file_path.read_text()
    assert "<!DOCTYPE html>" in content
    assert "<h1>Herman Melville - Moby Dick</h1>" in content

    # Verify index file record
    check_index_record(index_file_path, url, "success", expected_file_path)

@pytest.mark.asyncio
async def test_web_recursive_fetch_depth_1_workflow(temp_download_dir: Path, shared_executor: ThreadPoolExecutor):
    """Tests recursive fetch (depth 1) via workflow and verifies paths/index."""
    start_url = f"{HTTPBIN_BASE}/links/2/0"
    linked_url = f"{HTTPBIN_BASE}/links/1/0" # Expected linked URL
    download_id = "test_recursive_fetch_workflow"
    index_file_path = temp_download_dir / "index" / f"{download_id}.jsonl"
    content_base_dir = temp_download_dir / "content" / download_id

    # Run workflow first
    await fetch_documentation_workflow(
        source_type="website",
        download_id=download_id,
        url=start_url,
        base_dir=temp_download_dir,
        depth=1, # Fetch start URL + 1 level deep
        force=False,
        max_concurrent_requests=2,
        executor=shared_executor
    )

    # Calculate expected paths *after* workflow ensures directory exists
    expected_start_path = url_to_local_path(content_base_dir, start_url)
    expected_linked_path = url_to_local_path(content_base_dir, linked_url)

    # Verify start URL download
    assert expected_start_path.is_file(), f"Start URL file not found at {expected_start_path}"
    check_index_record(index_file_path, start_url, "success", expected_start_path)

    # Verify linked URL download (depth 1)
    assert expected_linked_path.is_file(), f"Linked URL file not found at {expected_linked_path}"
    check_index_record(index_file_path, linked_url, "success", expected_linked_path)


@pytest.mark.asyncio
@pytest.mark.skipif(os.environ.get("CI") == "true", reason="Playwright tests often flaky in CI without proper setup")
async def test_web_playwright_fetch_workflow(temp_download_dir: Path, shared_executor: ThreadPoolExecutor):
    """Tests fetching using Playwright via workflow."""
    url = f"{HTTPBIN_BASE}/html"
    download_id = "test_playwright_fetch_workflow"
    index_file_path = temp_download_dir / "index" / f"{download_id}.jsonl"
    content_base_dir = temp_download_dir / "content" / download_id

    # Run workflow first
    await fetch_documentation_workflow(
        source_type="playwright", # Use playwright source type
        download_id=download_id,
        url=url,
        base_dir=temp_download_dir,
        depth=0,
        force=True, # Force to ensure playwright runs
        max_concurrent_requests=1, # Playwright often better with lower concurrency
        timeout_playwright=30, # Give playwright a bit more time
        executor=shared_executor
    )

    # Calculate expected path *after* workflow ensures directory exists
    expected_file_path = url_to_local_path(content_base_dir, url)

    # Verification via index file and content check
    assert expected_file_path.is_file(), f"Expected file not found at {expected_file_path}"
    content = expected_file_path.read_text()
    assert "<!DOCTYPE html>" in content
    assert "<h1>Herman Melville - Moby Dick</h1>" in content
    check_index_record(index_file_path, url, "success", expected_file_path)

@pytest.mark.asyncio
async def test_web_robots_txt_blocking_workflow(temp_download_dir: Path, shared_executor: ThreadPoolExecutor):
    """Tests that robots.txt prevents download via workflow."""
    url = f"{HTTPBIN_BASE}/deny"
    download_id = "test_robots_blocking_workflow"
    index_file_path = temp_download_dir / "index" / f"{download_id}.jsonl"
    content_base_dir = temp_download_dir / "content" / download_id

    # Run workflow first
    await fetch_documentation_workflow(
        source_type="website",
        download_id=download_id,
        url=url,
        base_dir=temp_download_dir,
        depth=0,
        force=False,
        max_concurrent_requests=1,
        executor=shared_executor
    )

    # Calculate expected path *after* workflow (even though file shouldn't exist)
    expected_file_path = url_to_local_path(content_base_dir, url)

    # Verify content file does NOT exist
    assert not expected_file_path.exists(), f"File should NOT exist due to robots.txt: {expected_file_path}"

    # Verify index file record status
    check_index_record(index_file_path, url, "failed_robotstxt", expected_local_path=None)


@pytest.mark.asyncio
async def test_web_404_handling_workflow(temp_download_dir: Path, shared_executor: ThreadPoolExecutor):
    """Tests handling of a 404 Not Found error via workflow."""
    url = f"{HTTPBIN_BASE}/status/404"
    download_id = "test_404_handling_workflow"
    index_file_path = temp_download_dir / "index" / f"{download_id}.jsonl"
    content_base_dir = temp_download_dir / "content" / download_id

    # Run workflow first
    await fetch_documentation_workflow(
        source_type="website",
        download_id=download_id,
        url=url,
        base_dir=temp_download_dir,
        depth=0,
        force=False,
        max_concurrent_requests=1,
        executor=shared_executor
    )

    # Calculate expected path *after* workflow (even though file shouldn't exist)
    expected_file_path = url_to_local_path(content_base_dir, url)

    # Verify content file does NOT exist
    assert not expected_file_path.exists(), f"File should NOT exist due to 404: {expected_file_path}"

    # Verify index file record status and error
    check_index_record(index_file_path, url, "failed_request", expected_local_path=None, check_error=True)

@pytest.mark.asyncio
async def test_web_force_flag_workflow(temp_download_dir: Path, shared_executor: ThreadPoolExecutor):
    """Tests the force flag for re-downloading web content via workflow."""
    url = f"{HTTPBIN_BASE}/html"
    download_id = "test_force_flag_workflow"
    index_file_path = temp_download_dir / "index" / f"{download_id}.jsonl"
    content_base_dir = temp_download_dir / "content" / download_id

    # Initial download (force=False)
    await fetch_documentation_workflow(
        source_type="website", download_id=download_id, url=url, base_dir=temp_download_dir,
        depth=0, force=False, max_concurrent_requests=1, executor=shared_executor
    )
    # Calculate path *after* first run
    expected_file_path = url_to_local_path(content_base_dir, url)
    check_index_record(index_file_path, url, "success", expected_file_path)
    assert expected_file_path.is_file()
    initial_mtime = expected_file_path.stat().st_mtime
    initial_index_mtime = index_file_path.stat().st_mtime

    await asyncio.sleep(1.1) # Ensure time difference

    # Attempt download without force - should skip
    await fetch_documentation_workflow(
        source_type="website", download_id=download_id, url=url, base_dir=temp_download_dir,
        depth=0, force=False, max_concurrent_requests=1, executor=shared_executor
    )
    # Re-calculate path (should be same)
    expected_file_path_run2 = url_to_local_path(content_base_dir, url)
    assert expected_file_path_run2 == expected_file_path # Sanity check path calculation
    # Check skipped status via index
    check_index_record(index_file_path, url, "skipped", expected_file_path) # Expect skipped status now
    assert expected_file_path.stat().st_mtime == initial_mtime
    assert index_file_path.stat().st_mtime == initial_index_mtime # Index not updated

    # Attempt download with force - should re-download
    await fetch_documentation_workflow(
        source_type="website", download_id=download_id, url=url, base_dir=temp_download_dir,
        depth=0, force=True, max_concurrent_requests=1, executor=shared_executor
    )
    # Re-calculate path (should be same)
    expected_file_path_run3 = url_to_local_path(content_base_dir, url)
    assert expected_file_path_run3 == expected_file_path # Sanity check path calculation
    # Check completed status via index after force
    check_index_record(index_file_path, url, "success", expected_file_path) # Expect success status again
    assert expected_file_path.is_file()
    # Mtime might not change if content is identical, but index should update
    assert index_file_path.stat().st_mtime > initial_index_mtime

    # Final check (redundant but ensures state)
    check_index_record(index_file_path, url, "success", expected_file_path)

# TODO: Add test for concurrency limits if specific behavior needs verification beyond basic parallel fetches.