#!/bin/bash

# End-to-End Test Script for MCP Document Retriever Service

# --- Configuration ---
BASE_URL="http://localhost:8001" # Adjust if your host/port differs
CONTAINER_NAME="mcp-doc-retriever"
CLI_SCRIPT_PATH="src/mcp_doc_retriever/cli.py"

# Test URLs
EXAMPLE_URL="https://example.com/"
PYTHON_DOCS_URL="https://docs.python.org/3/"
# Using a smaller, faster-cloning repo for Git test
# TEST_GIT_REPO_URL="https://github.com/pallets/flask.git"
# Let's use the local test repo for speed and reliability
TEST_GIT_REPO_URL="file:///app/git_downloader_test/arango_sparse" # Path inside the container
TEST_GIT_REPO_NAME="arango_sparse" # Expected top-level dir name after clone

# Timeouts and Intervals
CURL_TIMEOUT=60         # seconds for curl requests
POLL_INTERVAL=3         # seconds between status/file checks
STATUS_POLL_TIMEOUT=90  # seconds max wait for status 'completed'/'failed'
FILE_POLL_TIMEOUT=60    # seconds max wait for a specific file (index/content)
CLI_TIMEOUT=120         # seconds max wait for CLI command execution

# Base directories inside the container
CONTAINER_DOWNLOADS_BASE="/app/downloads"
CONTAINER_INDEX_DIR="${CONTAINER_DOWNLOADS_BASE}/index"
CONTAINER_CONTENT_DIR="${CONTAINER_DOWNLOADS_BASE}/content"

# Host directory for CLI test outputs
HOST_CLI_TEST_DIR="./cli_test_downloads"

# --- Helper Functions ---
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Function to print PASS/FAIL messages
# Usage: report_status $? "Test Description"
report_status() {
  local exit_code=$1
  local description=$2
  if [ $exit_code -eq 0 ]; then
    echo -e "${GREEN}[PASS]${NC} ${description}"
  else
    echo -e "${RED}[FAIL]${NC} ${description} (Exit code: ${exit_code})"
  fi
  echo "--------------------------------------------------"
}

# Function to poll for file existence inside container
# Usage: poll_for_file FILE_PATH TIMEOUT "File Description"
poll_for_file() {
    local file_path=$1
    local timeout=$2
    local desc=$3
    local start_time=$(date +%s)
    echo -n "Polling for ${desc}: ${file_path} (max ${timeout}s)..." >&2 # Log to stderr, no newline
    while true; do
        # Use docker exec to check for file existence
        if docker exec "${CONTAINER_NAME}" test -e "${file_path}"; then # Use -e to check for files OR directories
            echo -e " Found." >&2
            return 0 # Success
        fi

        local current_time=$(date +%s)
        local elapsed=$((current_time - start_time))
        if [ $elapsed -ge $timeout ]; then
            echo -e " Timeout!" >&2
            return 1 # Failure
        fi
        # Print progress without newline
        echo -n "." >&2
        sleep ${POLL_INTERVAL}
    done
}

# Function to poll for download status via API
# Usage: poll_for_status DOWNLOAD_ID TIMEOUT
# Returns 0 if status becomes 'completed'
# Returns 1 if status becomes 'failed'
# Returns 2 if timeout occurs before completion/failure
poll_for_status() {
    local download_id=$1
    local timeout=$2
    local start_time=$(date +%s)
    echo -n "Polling for status of ${download_id} (max ${timeout}s)..." >&2
    while true; do
        local response_json
        local http_code
        # Use -w to get http code separately, handle curl errors
        response_json=$(curl -s -w "\n%{http_code}" --max-time 10 "${BASE_URL}/status/${download_id}")
        local curl_exit_code=$?

        if [ $curl_exit_code -ne 0 ]; then
            echo -n "!" >&2 # Indicate curl error
        else
            http_code=$(echo "$response_json" | tail -n1)
            response_json=$(echo "$response_json" | sed '$d') # Remove last line (http_code)

            if [ "$http_code" -eq 200 ]; then
                local status
                # Use jq if available, otherwise grep/cut
                if command -v jq &> /dev/null; then
                    status=$(echo "${response_json}" | jq -r '.status')
                else
                    status=$(echo "${response_json}" | grep -o '"status": *"[^"]*"' | cut -d'"' -f4)
                fi

                if [ "$status" == "completed" ]; then
                    echo -e " Completed." >&2
                    return 0 # Success
                elif [ "$status" == "failed" ]; then
                    echo -e " Failed." >&2
                    # Optionally print error message from response
                    local error_msg
                    if command -v jq &> /dev/null; then
                       error_msg=$(echo "${response_json}" | jq -r '.error_details // "N/A"')
                    else
                       error_msg=$(echo "${response_json}" | grep -o '"error_details": *"[^"]*"' | cut -d'"' -f4 || echo "N/A")
                    fi
                    echo "Error details: ${error_msg:0:150}..." >&2
                    return 1 # Failure
                elif [ "$status" == "running" ] || [ "$status" == "pending" ] ; then
                     echo -n "." >&2 # Still working
                else
                    echo -n "?(${status})" >&2 # Unknown status
                fi
            elif [ "$http_code" -eq 404 ]; then
                echo -n "X" >&2 # Task ID not found yet (might happen briefly)
            else
                echo -n "E(${http_code})" >&2 # Other HTTP error
            fi
        fi

        local current_time=$(date +%s)
        local elapsed=$((current_time - start_time))
        if [ $elapsed -ge $timeout ]; then
            echo -e " Timeout!" >&2
            return 2 # Timeout
        fi
        sleep ${POLL_INTERVAL}
    done
}

# Function to run download via API and capture ID
# Usage: download_id=$(run_api_download "SOURCE_TYPE" "SOURCE_LOCATION" DEPTH FORCE_FLAG "Description" [USE_PLAYWRIGHT])
run_api_download() {
    local source_type=$1
    local source_location=$2 # Renamed from 'url' for clarity (can be git url or web url)
    local depth=$3
    local force=$4
    local desc=$5
    local use_pw=${6:-false} # Optional playwright flag, default false - Note: This flag is now superseded by explicit 'playwright' source_type

    # Print status messages to stderr
    echo -e "${BLUE}Initiating API Download:${NC} ${desc}" >&2
    echo "Source Type: ${source_type}, Location: ${source_location}, Depth: ${depth}, Force: ${force}, Use Playwright: ${use_pw}" >&2

    local payload
    local unique_download_id
    # Generate a unique download ID
    if command -v uuidgen &> /dev/null; then
        unique_download_id=$(uuidgen)
    else
        # Fallback to timestamp if uuidgen is not available
        unique_download_id="ts_$(date +%s%N)"
    fi
    echo "Generated Download ID: ${unique_download_id}" >&2

    # Construct payload based on source type
    if [ "$source_type" == "git" ]; then
        # --- FIX: Use `repo_url` and `doc_path` based on `models.DocDownloadRequest` ---
        payload=$(cat <<EOF
{
  "download_id": "${unique_download_id}",
  "source_type": "${source_type}",
  "repo_url": "${source_location}",
  "doc_path": null,
  "force": ${force}
}
EOF
)
    elif [ "$source_type" == "website" ]; then
        # --- FIX: Use `url` based on `models.DocDownloadRequest` ---
        payload=$(cat <<EOF
{
  "download_id": "${unique_download_id}",
  "source_type": "${source_type}",
  "url": "${source_location}",
  "depth": ${depth},
  "force": ${force}
}
EOF
)
    elif [ "$source_type" == "playwright" ]; then # Explicitly handle playwright type
        # --- FIX: Use `url` based on `models.DocDownloadRequest` ---
        payload=$(cat <<EOF
{
  "download_id": "${unique_download_id}",
  "source_type": "${source_type}",
  "url": "${source_location}",
  "depth": ${depth},
  "force": ${force}
}
EOF
)
    else
        echo -e "${RED}ERROR:${NC} Unsupported source_type '${source_type}' in run_api_download." >&2
        return 1
    fi

    local response_json
    response_json=$(curl -s -X POST "${BASE_URL}/download" \
                      -H "Content-Type: application/json" \
                      --max-time ${CURL_TIMEOUT} \
                      -d "${payload}")
    local curl_exit_code=$?

    if [ $curl_exit_code -ne 0 ]; then
        # Print error messages to stderr
        echo -e "${RED}ERROR:${NC} curl command failed with exit code ${curl_exit_code}." >&2
        echo "" >&2
        return 1 # Signal failure
    fi

    # local download_id # No longer needed here, use unique_download_id generated earlier
    local status
    # Use jq if available, otherwise grep/cut
    if command -v jq &> /dev/null; then
        # download_id=$(echo "${response_json}" | jq -r '.download_id') # Don't extract from response
        status=$(echo "${response_json}" | jq -r '.status')
    else
        # download_id=$(echo "${response_json}" | grep -o '"download_id": *"[^"]*"' | cut -d'"' -f4) # Don't extract from response
        status=$(echo "${response_json}" | grep -o '"status": *"[^"]*"' | cut -d'"' -f4)
    fi

    # Check for 'pending' status. The `download_id` is the one we generated.
    if [ "$status" == "pending" ] && [ -n "$unique_download_id" ]; then
        echo "Download Task Pending. ID: ${unique_download_id}" >&2
        echo "${unique_download_id}" # Print ONLY the generated download_id to stdout for capture
        echo "" >&2
        return 0 # Signal success
    elif [ "$status" == "failed_validation" ]; then
         echo -e "${YELLOW}WARN:${NC} Download request failed validation." >&2
         local message
         if command -v jq &> /dev/null; then
            message=$(echo "${response_json}" | jq -r '.message // "N/A"')
         else
            message=$(echo "${response_json}" | grep -o '"message": *"[^"]*"' | cut -d'"' -f4 || echo "N/A")
         fi
         echo "Validation message: ${message}" >&2
         echo "" >&2
         return 1 # Signal failure (validation failure)
    else
        echo -e "${RED}ERROR:${NC} Did not receive 'started'/'pending' status or valid download_id." >&2
        echo "Response: ${response_json}" >&2
        echo "" >&2
        return 1 # Signal failure
    fi
}

# Function to extract the first local_path from an index file inside the container
# Usage: extracted_path=$(extract_first_local_path CONTAINER_INDEX_FILE_PATH)
extract_first_local_path() {
    local index_file_path=$1
    local local_path=""

    # Check if jq is available inside the container
    if docker exec "${CONTAINER_NAME}" command -v jq &> /dev/null; then
        # Use jq to extract the local_path from the first line (JSON object)
        local_path=$(docker exec "${CONTAINER_NAME}" sh -c "head -n 1 '${index_file_path}' | jq -r '.local_path // empty'")
    else
        # Fallback to grep/cut if jq is not available
        local_path=$(docker exec "${CONTAINER_NAME}" sh -c "head -n 1 '${index_file_path}' | grep -o '\"local_path\": *\"[^\"]*\"' | cut -d'\"' -f4")
    fi

    if [ -z "$local_path" ] || [ "$local_path" == "null" ]; then
        echo "Error extracting local_path from ${index_file_path}" >&2
        # Attempt to show the first line for debugging
        docker exec "${CONTAINER_NAME}" sh -c "head -n 1 '${index_file_path}'" >&2
        return 1
    else
        echo "$local_path" # Return the extracted path
        return 0
    fi
}

# --- Test Setup ---
echo "=================================================="
echo "Starting MCP Document Retriever E2E Tests..."
echo "Target API: ${BASE_URL}"
echo "Container: ${CONTAINER_NAME}"
echo "CLI Script: ${CLI_SCRIPT_PATH}"
echo "=================================================="

# Clean up previous CLI test runs
echo "Cleaning up previous host CLI test directory: ${HOST_CLI_TEST_DIR}" >&2
rm -rf "${HOST_CLI_TEST_DIR}"
mkdir -p "${HOST_CLI_TEST_DIR}"
cleanup_exit_code=$?
if [ $cleanup_exit_code -ne 0 ]; then
    echo -e "${RED}ERROR:${NC} Failed to create host CLI test directory. Check permissions." >&2
    exit 1
fi

# Ensure clean Docker environment
echo "Starting/Restarting Docker Compose service..." >&2
docker compose down -v --timeout 10 || echo "Docker down failed (or no volumes to remove), maybe not running." >&2
docker compose up -d --build --force-recreate # Build and start fresh
up_exit_code=$?
if [ $up_exit_code -ne 0 ]; then
    echo -e "${RED}ERROR:${NC} docker compose up failed with exit code ${up_exit_code}. Cannot continue." >&2
    exit 1
fi
# Add a delay to allow the service to initialize, especially after a build
echo "Waiting 20 seconds for service initialization..." >&2
sleep 20


# --- Phase 1: Basic API Health ---
echo -e "\n${BLUE}Phase 1: Basic API Health${NC}"
health_status_code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 15 "${BASE_URL}/health")
curl_exit_code=$?
health_body=""
if [ $curl_exit_code -eq 0 ] && [ "$health_status_code" -eq 200 ]; then
    health_body=$(curl -s --max-time 10 "${BASE_URL}/health")
    curl_body_exit_code=$?
    if [ $curl_body_exit_code -ne 0 ]; then health_body="(Failed to get response body)"; fi
fi
if [ $curl_exit_code -eq 0 ] && [ "$health_status_code" -eq 200 ] && echo "$health_body" | grep -q '"status":"healthy"'; then
  report_status 0 "Health check endpoint (/health)"
else
  if [ $curl_exit_code -ne 0 ]; then
      report_status $curl_exit_code "Health check endpoint (/health) - curl command failed"
  elif [ "$health_status_code" -ne 200 ]; then
      report_status 1 "Health check endpoint (/health) - Expected status 200, got ${health_status_code}"
  else
      report_status 1 "Health check endpoint (/health) - Status 200, but body unexpected: ${health_body}"
  fi
  # Check logs if health fails
  echo "Attempting to fetch logs on health check failure..." >&2
  docker logs "${CONTAINER_NAME}" || echo "Failed to get logs." >&2
  exit 1 # Exit if health check fails
fi


# --- Phase 2: API - Simple Web Download (example.com) ---
echo -e "\n${BLUE}Phase 2: API - Simple Web Download (example.com, depth 0)${NC}"
EXAMPLE_DOWNLOAD_ID=$(run_api_download "website" "${EXAMPLE_URL}" 0 true "Download example.com (depth 0, force=true)")
init_exit_code=$?
report_status $init_exit_code "Initiate API download for example.com"
if [ $init_exit_code -ne 0 ]; then exit 1; fi

# Poll for status completion
poll_for_status "${EXAMPLE_DOWNLOAD_ID}" ${STATUS_POLL_TIMEOUT}
status_exit_code=$?
if [ $status_exit_code -eq 0 ]; then
    report_status 0 "Polling status check completed successfully for example.com"
elif [ $status_exit_code -eq 1 ]; then
    report_status 1 "Polling status check indicates download FAILED for example.com"
    exit 1 # Fail fast if core download fails
else # Timeout
    report_status 1 "Polling status check TIMED OUT for example.com"
    exit 1
fi

# Verify files only if status is completed
if [ $status_exit_code -eq 0 ]; then
    index_file_path="${CONTAINER_INDEX_DIR}/${EXAMPLE_DOWNLOAD_ID}.jsonl"
    poll_for_file "${index_file_path}" ${FILE_POLL_TIMEOUT} "Index file (${EXAMPLE_DOWNLOAD_ID}.jsonl)"
    index_exists_code=$?
    report_status $index_exists_code "Index file exists for example.com download"

    if [ $index_exists_code -eq 0 ]; then
        # Extract the local_path from the index file
        extracted_content_path=$(extract_first_local_path "${index_file_path}")
        extract_code=$?
        if [ $extract_code -eq 0 ] && [ -n "$extracted_content_path" ]; then
            report_status 0 "Extracted content path from index: ${extracted_content_path}"
            # Poll for the extracted content file path
            poll_for_file "${extracted_content_path}" ${FILE_POLL_TIMEOUT} "Content file (from index)"
            content_exists_code=$?
            report_status $content_exists_code "Content file exists at path specified in index"

            # Basic check on index content (success status)
            if docker exec "${CONTAINER_NAME}" sh -c "head -n 1 '${index_file_path}' | grep -q '\"fetch_status\":\"success\"'"; then
                 report_status 0 "Index file first record shows fetch_status: success"
            else
                 report_status 1 "Index file first record DOES NOT show fetch_status: success"
                 docker exec "${CONTAINER_NAME}" sh -c "head -n 3 '${index_file_path}'" >&2
            fi
        else
            report_status 1 "Failed to extract valid content path from index file"
        fi
    fi
fi


# --- Phase 3: API - Search Functionality ---
echo -e "\n${BLUE}Phase 3: API - Search Functionality (using example.com download)${NC}"
if [ $status_exit_code -ne 0 ] || [ $index_exists_code -ne 0 ] || [ $content_exists_code -ne 0 ]; then
     echo -e "${YELLOW}WARN:${NC} Skipping Search tests as prerequisite download/file checks failed." >&2
else
    echo "Testing search for existing content (title)..."
    search_payload_1=$(cat <<EOF
{
  "download_id": "${EXAMPLE_DOWNLOAD_ID}",
  "scan_keywords": ["Example", "Domain"],
  "extract_selector": "title",
  "extract_keywords": null
}
EOF
)
    search_response_1=$(curl -s -X POST "${BASE_URL}/search" -H "Content-Type: application/json" --max-time ${CURL_TIMEOUT} -d "${search_payload_1}")
    search_1_exit_code=$?
    # --- FIX: Use `match_details` from SearchResultItem model ---
    # Use jq for robust check if available
    if command -v jq &> /dev/null; then
        if [ $search_1_exit_code -eq 0 ] && echo "${search_response_1}" | jq -e '. | length > 0 and .[0].match_details == "Example Domain"' > /dev/null; then
            report_status 0 "Search found expected content ('Example Domain' in title via match_details)"
        else
            report_status 1 "Search did not find expected content via match_details. Exit: ${search_1_exit_code}, Response: ${search_response_1}"
        fi
    else # Fallback to grep (less precise - checks if string exists anywhere)
         if [ $search_1_exit_code -eq 0 ] && echo "${search_response_1}" | grep -q '"match_details": *"Example Domain"'; then
            report_status 0 "Search found expected content ('Example Domain' in title via match_details) (grep)"
        else
            report_status 1 "Search did not find expected content via match_details (grep). Exit: ${search_1_exit_code}, Response: ${search_response_1}"
        fi
    fi
    # --- END FIX ---

    echo "Testing search for non-existent keyword..."
    search_payload_2=$(cat <<EOF
{
  "download_id": "${EXAMPLE_DOWNLOAD_ID}",
  "scan_keywords": ["nonexistentkeywordxyz123"],
  "extract_selector": "p",
  "extract_keywords": null
}
EOF
)
    search_response_2=$(curl -s -X POST "${BASE_URL}/search" -H "Content-Type: application/json" --max-time ${CURL_TIMEOUT} -d "${search_payload_2}")
    search_2_exit_code=$?
    if [ $search_2_exit_code -eq 0 ] && [ "${search_response_2}" == "[]" ]; then
        report_status 0 "Search correctly returned empty array for non-existent keyword"
    else
        report_status 1 "Search did not return empty array. Exit: ${search_2_exit_code}, Response: ${search_response_2}"
    fi
fi # End search tests block

# --- Search Test for Invalid ID (always run) ---
echo "Testing search with invalid download ID..."
search_payload_3=$(cat <<EOF
{
  "download_id": "invalid-id-does-not-exist",
  "scan_keywords": ["test"],
  "extract_selector": "title",
  "extract_keywords": null
}
EOF
)
# --- FIX: The API endpoint returns 404, check the body for detail (optional) ---
search_response_3_body=$(curl -s -w "\n%{http_code}" -X POST "${BASE_URL}/search" -H "Content-Type: application/json" --max-time ${CURL_TIMEOUT} -d "${search_payload_3}")
search_status_3=$(echo "$search_response_3_body" | tail -n1)
search_response_3_body=$(echo "$search_response_3_body" | sed '$d')

if [ "$search_status_3" -eq 404 ]; then
    report_status 0 "Search correctly returned 404 for invalid download ID"
else
    report_status 1 "Search did not return 404 for invalid download ID (Got: ${search_status_3}). Body: ${search_response_3_body}"
fi


# --- Phase 4: API - Git Download ---
echo -e "\n${BLUE}Phase 4: API - Git Download (${TEST_GIT_REPO_URL})${NC}"
# --- FIX: Use correct source_type "git" ---
GIT_DOWNLOAD_ID=$(run_api_download "git" "${TEST_GIT_REPO_URL}" 0 true "Download Git repo (${TEST_GIT_REPO_NAME})")
init_exit_code=$?
report_status $init_exit_code "Initiate API download for Git repo"
if [ $init_exit_code -ne 0 ]; then exit 1; fi

# Poll for status completion
poll_for_status "${GIT_DOWNLOAD_ID}" ${STATUS_POLL_TIMEOUT}
status_exit_code=$?
if [ $status_exit_code -eq 0 ]; then
    report_status 0 "Polling status check completed successfully for Git repo"
elif [ $status_exit_code -eq 1 ]; then
    report_status 1 "Polling status check indicates download FAILED for Git repo"
    exit 1
else # Timeout
    report_status 1 "Polling status check TIMED OUT for Git repo"
    exit 1
fi

# Verify files only if status is completed
if [ $status_exit_code -eq 0 ]; then
    git_index_file_path="${CONTAINER_INDEX_DIR}/${GIT_DOWNLOAD_ID}.jsonl"
    poll_for_file "${git_index_file_path}" ${FILE_POLL_TIMEOUT} "Index file (${GIT_DOWNLOAD_ID}.jsonl)"
    index_exists_code=$?
    report_status $index_exists_code "Index file exists for Git download"

    if [ $index_exists_code -eq 0 ]; then
        # Extract the local_path (should be the repo root dir)
        # For git, the index might have many files. We expect the *directory* to exist.
        # Let's check for a known file within the expected structure.
        # --- FIX: Check for correct repo path inside content dir ---
        expected_repo_dir="${CONTAINER_CONTENT_DIR}/${GIT_DOWNLOAD_ID}/repo" # Adjusted path based on workflow
        expected_readme_path="${expected_repo_dir}/README.md" # Assuming a standard README

        poll_for_file "${expected_repo_dir}" ${FILE_POLL_TIMEOUT} "Git repo content directory (repo/)"
        repo_dir_exists_code=$?
        report_status $repo_dir_exists_code "Git repo content directory exists"

        if [ $repo_dir_exists_code -eq 0 ]; then
             poll_for_file "${expected_readme_path}" ${FILE_POLL_TIMEOUT} "Known file (README.md) in Git repo"
             readme_exists_code=$?
             report_status $readme_exists_code "Known file (README.md) exists in Git repo"
        fi

        # Check index content for success status (check first line)
        if docker exec "${CONTAINER_NAME}" sh -c "head -n 1 '${git_index_file_path}' | grep -q '\"fetch_status\":\"success\"'"; then
             report_status 0 "Git index file first record shows fetch_status: success"
        else
             report_status 1 "Git index file first record DOES NOT show fetch_status: success"
             docker exec "${CONTAINER_NAME}" sh -c "head -n 3 '${git_index_file_path}'" >&2
        fi
    fi
fi


# --- Phase 5: API - Playwright Download ---
echo -e "\n${BLUE}Phase 5: API - Playwright Download (example.com)${NC}"
# --- FIX: Use correct source_type "playwright" ---
PW_DOWNLOAD_ID=$(run_api_download "playwright" "${EXAMPLE_URL}" 0 true "Download example.com via Playwright")
init_exit_code=$?
report_status $init_exit_code "Initiate Playwright download for example.com"

# Poll for Playwright status completion (allow more time)
if [ $init_exit_code -eq 0 ]; then
    poll_for_status "${PW_DOWNLOAD_ID}" $((STATUS_POLL_TIMEOUT * 2))
    status_exit_code=$?
    if [ $status_exit_code -eq 0 ]; then
        report_status 0 "Polling status check completed successfully for Playwright download"
    elif [ $status_exit_code -eq 1 ]; then
        report_status 1 "Polling status check indicates Playwright download FAILED"
        # Don't exit, continue to CLI tests
    else # Timeout
        report_status 1 "Polling status check TIMED OUT for Playwright download"
        # Don't exit, continue to CLI tests
    fi

    # Verify files only if status is completed
    if [ $status_exit_code -eq 0 ]; then
        pw_index_file_path="${CONTAINER_INDEX_DIR}/${PW_DOWNLOAD_ID}.jsonl"
        poll_for_file "${pw_index_file_path}" ${FILE_POLL_TIMEOUT} "Index file (${PW_DOWNLOAD_ID}.jsonl)"
        index_exists_code=$?
        report_status $index_exists_code "Index file exists for Playwright download"

        if [ $index_exists_code -eq 0 ]; then
            # Extract the local_path from the index file
            extracted_content_path=$(extract_first_local_path "${pw_index_file_path}")
            extract_code=$?
            if [ $extract_code -eq 0 ] && [ -n "$extracted_content_path" ]; then
                report_status 0 "Extracted content path from Playwright index: ${extracted_content_path}"
                # Poll for the extracted content file path
                poll_for_file "${extracted_content_path}" ${FILE_POLL_TIMEOUT} "Playwright Content file (from index)"
                content_exists_code=$?
                report_status $content_exists_code "Playwright Content file exists at path specified in index"
            else
                 report_status 1 "Failed to extract valid content path from Playwright index file"
            fi
        fi
    fi
else
    echo -e "${YELLOW}WARN:${NC} Skipping Playwright download checks as initiation failed." >&2
fi


# --- Phase 6: CLI - Web Download ---
echo -e "\n${BLUE}Phase 6: CLI - Web Download (example.com)${NC}"
CLI_WEB_OUT_DIR="${HOST_CLI_TEST_DIR}/web"
mkdir -p "${CLI_WEB_OUT_DIR}"
echo "Running CLI web download..." >&2
# Execute using uv run directly on the host
# --- FIX: Use the correct CLI command structure `python -m mcp_doc_retriever download ...` ---
cli_web_cmd="uv run python -m mcp_doc_retriever download website ${EXAMPLE_URL} cli_web_example --base-dir ${CLI_WEB_OUT_DIR} --depth 0 --force"
echo "Executing: ${cli_web_cmd}" >&2
cli_web_output=$(timeout ${CLI_TIMEOUT} ${cli_web_cmd} 2>&1)
cli_web_exit_code=$?

report_status $cli_web_exit_code "CLI web download command execution"
echo "CLI Output (Web):" >&2
echo "${cli_web_output}" >&2
echo "--- End CLI Output ---" >&2

if [ $cli_web_exit_code -eq 0 ]; then
    # Extract download ID from CLI output (assuming it prints 'Download ID: ...')
    # --- FIX: Use the download_id provided to the command ---
    cli_web_download_id="cli_web_example"
    # cli_web_download_id=$(echo "${cli_web_output}" | grep -o 'Download ID: [a-zA-Z0-9_-]*' | cut -d' ' -f3)

    if [ -n "$cli_web_download_id" ]; then
        report_status 0 "Using CLI web download ID: ${cli_web_download_id}"
        cli_web_index_path="${CLI_WEB_OUT_DIR}/index/${cli_web_download_id}.jsonl"
        cli_web_content_base_path="${CLI_WEB_OUT_DIR}/content/${cli_web_download_id}"

        # Check for index file on host
        if [ -f "$cli_web_index_path" ]; then
            report_status 0 "CLI web index file exists on host: ${cli_web_index_path}"

            # Extract local path from host index file
            host_local_path=$(head -n 1 "$cli_web_index_path" | grep -o '\"local_path\": *\"[^\"]*\"' | cut -d'\"' -f4)
            if [ -n "$host_local_path" ]; then
                 report_status 0 "Extracted host local path from CLI index: ${host_local_path}"
                 # Check if the content file exists at that path on the host
                 if [ -f "$host_local_path" ]; then
                     report_status 0 "CLI web content file exists on host at path from index"
                 else
                     report_status 1 "CLI web content file NOT found on host at path from index: ${host_local_path}"
                 fi
            else
                 report_status 1 "Failed to extract local_path from CLI web index file on host"
                 head -n 1 "$cli_web_index_path" >&2
            fi
        else
            report_status 1 "CLI web index file NOT found on host: ${cli_web_index_path}"
        fi
    else
        report_status 1 "Failed to identify download ID for CLI web output"
    fi
else
    # If CLI command failed, try to show relevant part of output
    echo "CLI web command failed. Last few lines of output:" >&2
    echo "${cli_web_output}" | tail -n 10 >&2
fi


# --- Phase 7: CLI - Git Download ---
echo -e "\n${BLUE}Phase 7: CLI - Git Download (Local Test Repo)${NC}"
CLI_GIT_OUT_DIR="${HOST_CLI_TEST_DIR}/git"
mkdir -p "${CLI_GIT_OUT_DIR}"
# Need to use the *host* path equivalent for the file URL
HOST_TEST_GIT_REPO_PATH="$(pwd)/git_downloader_test/arango_sparse" # Assumes script run from project root
HOST_TEST_GIT_REPO_URL="file://${HOST_TEST_GIT_REPO_PATH}"

echo "Running CLI git download..." >&2
# --- FIX: Use the correct CLI command structure and args ---
# Use --repo-url (or whatever cli.py expects), download_id, --base-dir, --force
# Assuming --doc-path is not used for this test based on API test pattern
cli_git_download_id="cli_git_example"
cli_git_cmd="uv run python -m mcp_doc_retriever download git ${HOST_TEST_GIT_REPO_URL} ${cli_git_download_id} --base-dir ${CLI_GIT_OUT_DIR} --force"
echo "Executing: ${cli_git_cmd}" >&2
cli_git_output=$(timeout ${CLI_TIMEOUT} ${cli_git_cmd} 2>&1)
cli_git_exit_code=$?

report_status $cli_git_exit_code "CLI git download command execution"
echo "CLI Output (Git):" >&2
echo "${cli_git_output}" >&2
echo "--- End CLI Output ---" >&2

if [ $cli_git_exit_code -eq 0 ]; then
    # Extract download ID
    # Use the download ID provided to the command
    report_status 0 "Using CLI git download ID: ${cli_git_download_id}"
    cli_git_index_path="${CLI_GIT_OUT_DIR}/index/${cli_git_download_id}.jsonl"
    # --- FIX: Use correct content path based on workflow ---
    cli_git_content_repo_path="${CLI_GIT_OUT_DIR}/content/${cli_git_download_id}/repo" # Check if '/repo' is added by workflow
    cli_git_readme_path="${cli_git_content_repo_path}/README.md"

        # Check for index file on host
        if [ -f "$cli_git_index_path" ]; then
            report_status 0 "CLI git index file exists on host: ${cli_git_index_path}"
        else
            report_status 1 "CLI git index file NOT found on host: ${cli_git_index_path}"
        fi

        # Check for content directory on host
        if [ -d "$cli_git_content_repo_path" ]; then
            report_status 0 "CLI git content directory exists on host: ${cli_git_content_repo_path}"
            # Check for known file within repo
            if [ -f "$cli_git_readme_path" ]; then
                report_status 0 "CLI git known file (README.md) exists on host"
            else
                report_status 1 "CLI git known file (README.md) NOT found on host: ${cli_git_readme_path}"
            fi
        else
            report_status 1 "CLI git content directory NOT found on host: ${cli_git_content_repo_path}"
        fi
    else
        report_status 1 "Failed to identify download ID for CLI git output"
    fi
else
    echo "CLI git command failed. Last few lines of output:" >&2
    echo "${cli_git_output}" | tail -n 10 >&2
fi


# --- Script Footer ---
echo ""
echo "=================================================="
echo "E2E Test Script Finished."
echo "Review output above for PASS/FAIL details."
echo "Host CLI outputs are in: ${HOST_CLI_TEST_DIR}"
echo "=================================================="

# Optional: Add a command to keep the container running for inspection
# echo "Container left running. Use 'docker compose down' to stop it."
# exit 0 # Explicitly exit with 0 if all tests passed or were handled

# Exit with a non-zero code if any major failures occurred (e.g., health check, core downloads)
# This requires tracking failures throughout the script. For simplicity, we rely on manual review for now.
