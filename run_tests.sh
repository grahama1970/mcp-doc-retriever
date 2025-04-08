#!/bin/bash
# Warn if run as 'bash run_tests.sh' or 'sh run_tests.sh' instead of './run_tests.sh'
if [[ "$0" == "bash" || "$0" == "sh" ]]; then
  echo "Warning: It's recommended to run this script as './run_tests.sh' after making it executable with 'chmod +x run_tests.sh'."
fi
set -e

echo "=== Test Harness: Environment Verification and Test Runner ==="

if [ -z "$VIRTUAL_ENV" ] && [ -d ".venv" ]; then
  echo "Virtual environment not active. Activating .venv..."
  source .venv/bin/activate
fi

echo "Verifying dependencies and environment..."
uv run scripts/check_deps.py

echo "Running tests..."
uv run test_downloader.py