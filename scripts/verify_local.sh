#!/bin/bash

# Exit immediately if a command exits with a non-zero status.
set -e
# Treat unset variables as an error.
set -u
# Ensure pipeline failures are caught.
set -o pipefail

# --- Configuration ---
BASE_URL="http://localhost:8005"
CLI_WEB_DIR="./manual_cli_test/web_verify_script"
CLI_GIT_DIR="./manual_cli_test/git_verify_script"
POLL_TIMEOUT_SECONDS=60
POLL_INTERVAL_SECONDS=3

# --- Helper Functions ---
function fail {
    echo "ERROR: $1" >&2
    exit 1
}

function poll_status {
    local download_id=$1
    local start_time=$(date +%s)
    local end_time=$((start_time + POLL_TIMEOUT_SECONDS))
    local current_status=""

    echo "Polling status for download ID: $download_id (Timeout: ${POLL_TIMEOUT_SECONDS}s)"

    while [[ $(date +%s) -lt $end_time ]]; do
        current_status=$(curl -s "$BASE_URL/status/$download_id" | jq .status -r)

        if [[ "$current_status" == "completed" ]]; then
            echo "Status for $download_id: completed"
            return 0
        elif [[ "$current_status" == "failed" ]]; then
            echo "Status for $download_id: failed"
            return 1
        elif [[ "$current_status" == "null" ]] || [[ -z "$current_status" ]]; then
             # Handle cases where the ID might not be immediately available or jq fails
             echo "Status for $download_id: pending or invalid response, retrying..."
        else
            echo "Status for $download_id: $current_status, retrying..."
        fi
        sleep $POLL_INTERVAL_SECONDS
    done

    echo "Polling timed out for download ID: $download_id after ${POLL_TIMEOUT_SECONDS} seconds."
    return 1
}

# --- Cleanup and Setup ---
echo "Cleaning up previous test directories..."
rm -rf "$CLI_WEB_DIR" "$CLI_GIT_DIR"
echo "Creating test directories..."
mkdir -p "$CLI_WEB_DIR" "$CLI_GIT_DIR"

# --- API Tests ---
echo "--- Running API Tests ---"

# Generate unique IDs for API tests
WEB_ID="verify_web_$(uuidgen)"
GIT_ID="verify_git_$(uuidgen)"
echo "Generated Web ID: $WEB_ID"
echo "Generated Git ID: $GIT_ID"

# 1. API - Website Download
echo "Step 1: Requesting website download (https://example.com)..."
echo "Requesting website download (https://example.com) with ID: $WEB_ID"
curl -s -f -X POST -H "Content-Type: application/json" \
     -d '{"source_type": "website", "url": "https://example.com", "download_id": "'"$WEB_ID"'"}' \
     "$BASE_URL/download" || fail "Failed to submit website download request for ID $WEB_ID"
echo "Website download request submitted for ID: $WEB_ID"

# 2. API - Git Download
echo "Step 2: Requesting git download (https://github.com/git-fixtures/basic.git)..."
echo "Requesting git download (https://github.com/git-fixtures/basic.git) with ID: $GIT_ID"
curl -s -f -X POST -H "Content-Type: application/json" \
     -d '{"source_type": "git", "repo_url": "https://github.com/git-fixtures/basic.git", "download_id": "'"$GIT_ID"'"}' \
     "$BASE_URL/download" || fail "Failed to submit git download request for ID $GIT_ID"
echo "Git download request submitted for ID: $GIT_ID"

# 3. API - Status Polling
echo "Step 3: Polling download statuses..."
poll_status "$WEB_ID" || fail "Website download polling failed or timed out."
poll_status "$GIT_ID" || fail "Git download polling failed or timed out."
echo "Both downloads completed successfully."

# 4. API - Search (Website)
echo "Step 4: Testing search endpoint for website download ($WEB_ID)..."
SEARCH_WEB_STATUS=$(curl -s -w "%{http_code}" -o /dev/null -X POST -H "Content-Type: application/json" -d '{"scan_keywords": ["example"], "extract_selector": "p"}' "$BASE_URL/search/$WEB_ID")
if [[ "$SEARCH_WEB_STATUS" -ne 200 ]]; then
    fail "Search request for $WEB_ID failed. Expected status 200, got $SEARCH_WEB_STATUS"
fi
echo "Search request for $WEB_ID successful (Status: $SEARCH_WEB_STATUS)."

# 5. API - Search Non-Existent ID
echo "Step 5: Testing search endpoint for non-existent ID..."
SEARCH_NONEXISTENT_STATUS=$(curl -s -w "%{http_code}" -o /dev/null -X POST -H "Content-Type: application/json" -d '{"scan_keywords": ["test"], "extract_selector": "p"}' "$BASE_URL/search/non_existent_id_script_check")
if [[ "$SEARCH_NONEXISTENT_STATUS" -ne 404 ]]; then
    fail "Search request for non-existent ID failed. Expected status 404, got $SEARCH_NONEXISTENT_STATUS"
fi
echo "Search request for non-existent ID correctly returned 404."

# --- CLI Tests ---
echo "--- Running CLI Tests ---"

# 6. CLI - Website Download
echo "Step 6: Running CLI website download (https://httpbin.org/html)..."
mcp-doc-retriever download website https://httpbin.org/html web_test_script --base-dir "$CLI_WEB_DIR" --force -v
if [[ $? -ne 0 ]]; then
    fail "CLI website download command failed."
fi
echo "CLI website download successful."

# 7. CLI - Git Download
echo "Step 7: Running CLI git download (https://github.com/octocat/Spoon-Knife.git)..."
mcp-doc-retriever download git https://github.com/octocat/Spoon-Knife.git git_test_script --base-dir "$CLI_GIT_DIR" --force -v
if [[ $? -ne 0 ]]; then
    fail "CLI git download command failed."
fi
echo "CLI git download successful."

# --- Filesystem Checks ---
echo "--- Running Filesystem Checks ---"

# 8. Filesystem Check - Web
echo "Step 8: Checking for CLI web download output file..."
WEB_OUTPUT_FILE="$CLI_WEB_DIR/index/web_test_script.jsonl"
if [[ ! -f "$WEB_OUTPUT_FILE" ]]; then
    fail "CLI web download output file not found: $WEB_OUTPUT_FILE"
fi
echo "CLI web download output file found: $WEB_OUTPUT_FILE"

# 9. Filesystem Check - Git
echo "Step 9: Checking for CLI git download output file..."
GIT_OUTPUT_FILE="$CLI_GIT_DIR/index/git_test_script.jsonl"
if [[ ! -f "$GIT_OUTPUT_FILE" ]]; then
    fail "CLI git download output file not found: $GIT_OUTPUT_FILE"
fi
echo "CLI git download output file found: $GIT_OUTPUT_FILE"

# --- Success ---
echo "---"
echo "Verification Script PASSED"
exit 0