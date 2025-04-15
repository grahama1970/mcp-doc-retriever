# tests/local/test_03_local_cli.py
import pytest
import subprocess
import sys
from pathlib import Path

# Test constants
EXAMPLE_URL = "https://httpbin.org/html"
TEST_GIT_REPO_URL = (
    "https://github.com/octocat/Spoon-Knife.git"  # Use a different repo for CLI test
)
CLI_TIMEOUT = 120  # Seconds


@pytest.mark.skipif(
    sys.platform == "win32",
    reason="CLI tests using uv run might behave differently on Windows pathing",
)
def test_local_cli_web_download(temp_cli_dirs):
    """Test the CLI web download command locally."""
    web_dir = temp_cli_dirs["web"]
    download_id = "cli_local_web"
    index_file = web_dir / "index" / f"{download_id}.jsonl"

    cmd = [
        "uv",
        "run",  # Use uv run if tests are run via uv
        "python",
        "-m",
        "mcp_doc_retriever.cli",
        "download",
        "website",
        EXAMPLE_URL,
        download_id,
        "--base-dir",
        str(web_dir),
        "--force",
        "--depth",
        "0",  # Keep it small
        "-v",  # Add verbosity
    ]
    print(f"\nRunning CLI command: {' '.join(cmd)}")
    # Run the command, capturing output and checking return code
    result = subprocess.run(
        cmd, capture_output=True, text=True, timeout=CLI_TIMEOUT, check=False
    )  # check=False allows us to assert code

    print("CLI STDOUT:")
    print(result.stdout[-1000:])  # Print last 1000 chars
    print("CLI STDERR:")
    print(result.stderr[-1000:])
    print(f"CLI Exit Code: {result.returncode}")

    assert result.returncode == 0, "CLI web download command failed"
    assert index_file.is_file(), f"Expected index file not found: {index_file}"
    print("CLI web download successful and index file created.")


@pytest.mark.skipif(
    sys.platform == "win32",
    reason="CLI tests using uv run might behave differently on Windows pathing",
)
def test_local_cli_git_download(temp_cli_dirs):
    """Test the CLI git download command locally."""
    git_dir = temp_cli_dirs["git"]
    download_id = "cli_local_git"
    index_file = git_dir / "index" / f"{download_id}.jsonl"

    cmd = [
        "uv",
        "run",  # Use uv run if tests are run via uv
        "python",
        "-m",
        "mcp_doc_retriever.cli",
        "download",
        "git",
        TEST_GIT_REPO_URL,
        download_id,
        "--base-dir",
        str(git_dir),
        "--doc-path",
        ".",  # Clone all
        "--force",
        "-v",  # Add verbosity
    ]
    print(f"\nRunning CLI command: {' '.join(cmd)}")
    result = subprocess.run(
        cmd, capture_output=True, text=True, timeout=CLI_TIMEOUT, check=False
    )

    print("CLI STDOUT:")
    print(result.stdout[-1000:])  # Print last 1000 chars
    print("CLI STDERR:")
    print(result.stderr[-1000:])
    print(f"CLI Exit Code: {result.returncode}")

    assert result.returncode == 0, "CLI git download command failed"
    assert index_file.is_file(), f"Expected index file not found: {index_file}"
    print("CLI git download successful and index file created.")
