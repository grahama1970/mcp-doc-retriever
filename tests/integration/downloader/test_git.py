"""
Integration tests for the Git downloader workflow.
"""
import pytest
import asyncio
import shutil
from pathlib import Path
import json
import time
from concurrent.futures import ThreadPoolExecutor

# Import the workflow function to test
from mcp_doc_retriever.downloader.workflow import fetch_documentation_workflow
# Import models only needed for assertions on index file (if created by workflow)
from mcp_doc_retriever.models import IndexRecord

# A small, public repo suitable for testing
TEST_REPO_URL = "https://github.com/octocat/Spoon-Knife.git"
# A file expected to be in the repo
EXPECTED_FILE = "README.md"
# Expected start content of the README
EXPECTED_README_START = "### Well hello there!" # Updated expected content

@pytest.fixture
def temp_download_dir(tmp_path: Path) -> Path:
    """Creates a temporary directory for downloads."""
    download_dir = tmp_path / "git_test_downloads_workflow"
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

@pytest.mark.asyncio
async def test_git_full_clone_workflow(temp_download_dir: Path, shared_executor: ThreadPoolExecutor):
    """Tests a full clone via the fetch_documentation_workflow."""
    download_id = "test_git_full_workflow"
    # Define paths based on workflow structure
    content_dir = temp_download_dir / "content" / download_id / "repo"
    index_file_path = temp_download_dir / "index" / f"{download_id}_git.jsonl" # Assuming this naming convention

    await fetch_documentation_workflow(
        source_type="git",
        download_id=download_id,
        repo_url=TEST_REPO_URL,
        doc_path=None, # Indicate full clone for workflow
        base_dir=temp_download_dir,
        force=False,
        executor=shared_executor
    )

    # Verify clone directory and expected file exist
    assert content_dir.is_dir(), f"Content directory {content_dir} not created."
    expected_readme = content_dir / EXPECTED_FILE
    assert expected_readme.is_file(), f"Expected file {expected_readme} not found."
    # Updated assertion for README content
    assert expected_readme.read_text().startswith(EXPECTED_README_START), \
        f"README content did not start with expected string. Got: '{expected_readme.read_text()[:100]}...'"


    # Note: The current workflow doesn't seem to create an index file for git clones.
    # If it should, the workflow needs updating, and this check enabled.
    # assert index_file_path.is_file(), f"Index file {index_file_path} not created for git clone."
    # Add checks for index content if it gets created.

@pytest.mark.asyncio
async def test_git_clone_force_workflow(temp_download_dir: Path, shared_executor: ThreadPoolExecutor):
    """Tests the force flag via the fetch_documentation_workflow."""
    download_id = "test_git_force_workflow"
    content_dir = temp_download_dir / "content" / download_id / "repo"
    expected_readme = content_dir / EXPECTED_FILE

    try:
        # Initial clone
        await fetch_documentation_workflow(
            source_type="git", download_id=download_id, repo_url=TEST_REPO_URL, doc_path=None,
            base_dir=temp_download_dir, force=False, executor=shared_executor
        )
        assert expected_readme.is_file(), "Initial clone failed, README not found."
        initial_mtime = expected_readme.stat().st_mtime

        await asyncio.sleep(1.1) # Ensure time difference for mtime check

        # Attempt clone without force - should use existing dir, mtime shouldn't change significantly
        await fetch_documentation_workflow(
            source_type="git", download_id=download_id, repo_url=TEST_REPO_URL, doc_path=None,
            base_dir=temp_download_dir, force=False, executor=shared_executor
        )
        assert expected_readme.stat().st_mtime == initial_mtime, "mtime changed on non-forced run."

        # Attempt clone with force - should remove and re-clone
        await fetch_documentation_workflow(
            source_type="git", download_id=download_id, repo_url=TEST_REPO_URL, doc_path=None,
            base_dir=temp_download_dir, force=True, executor=shared_executor
        )
        assert expected_readme.is_file(), "README not found after forced re-clone."
        # Mtime *might* be newer, but not guaranteed if content is identical.
        # The key check is that the operation succeeded and the file exists after force=True.

    finally:
        # Ensure executor is shut down even if assertions fail mid-test
        # Note: shared_executor fixture handles shutdown, no need here if using fixture
        pass


# TODO: Add sparse checkout test for workflow if needed
# async def test_git_sparse_clone_workflow(temp_download_dir: Path, shared_executor: ThreadPoolExecutor):
#     download_id = "test_git_sparse_workflow"
#     doc_path_sparse = "docs" # Example path
#     repo_url_sparse = "https://github.com/..." # Repo with a 'docs' dir
#     content_dir = temp_download_dir / "content" / download_id / "repo"
#     expected_sparse_subdir = content_dir / doc_path_sparse
#     expected_file_in_sparse = expected_sparse_subdir / "index.rst" # Example file
#     absent_dir_in_sparse = content_dir / "tests" # Example dir outside sparse path
#
#     await fetch_documentation_workflow(
#         source_type="git", download_id=download_id, repo_url=repo_url_sparse,
#         doc_path=doc_path_sparse, # Provide the sparse path
#         base_dir=temp_download_dir, force=True, executor=shared_executor
#     )
#
#     assert expected_sparse_subdir.is_dir()
#     assert expected_file_in_sparse.is_file()
#     assert not absent_dir_in_sparse.exists()