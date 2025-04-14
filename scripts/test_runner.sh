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
TEST_GIT_REPO_URL="$(pwd)/git_downloader_test/arango_sparse" # Use absolute path
TEST_GIT_REPO_NAME="arango_sparse" # Expected top-level dir name after clone

TEST_API_GIT_REPO_URL="https://github.com/git-fixtures/basic.git" # Valid HTTPS URL for API test
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

# Function to construct expected content path for web downloads
get_content_path() {
    local download_id=$1
    local url=$2
    # Extract host and path from URL
    local host=$(echo "$url" | sed -E 's#^https?://([^/]+).*#\1#')
    # Get the base name of the URL (without protocol and host)
    local url_base=$(echo "$url" | sed -E 's#^https?://[^/]+/?##' | sed 's#/#-#g')
    if [ -z "$url_base" ]; then
        url_base="index"
    fi
    # Note: We don't know the exact hash, so we'll use a wildcard
    echo "${CONTAINER_CONTENT_DIR}/${download_id}/${host}/${url_base}-*"
}

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
        # *** THE ONLY CHANGE IS HERE: Removed the extraneous text ***
        # Corrected line:
        if [ $elapsed -ge $timeout ]; then echo -e " Timeout!" >&2; return 1; fi
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
        payload=$(cat <<EOF
{
  "download_id": "${unique_download_id}",
  "source_type": "${source_type}",
  "repo_url": "${source_location}",
  "doc_path": ".",
  "force": ${force}
}
EOF
)
    elif [ "$source_type" == "website" ]; then
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
        echo -e "${RED}ERROR:${NC} curl command failed with exit code ${curl_exit_code}." >&2
        echo "" >&2
        return 1 # Signal failure
    fi

    local status
    # Use jq if available, otherwise grep/cut
    if command -v jq &> /dev/null; then
        status=$(echo "${response_json}" | jq -r '.status')
    else
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
        local_path=$(docker exec "${CONTAINER_NAME}" sh -c "head -n 1 '${index_file_path}' | jq -r '.local_path // empty'")
    else
        local_path=$(docker exec "${CONTAINER_NAME}" sh -c "head -n 1 '${index_file_path}' | grep -o '\"local_path\": *\"[^\"]*\"' | cut -d'\"' -f4")
    fi

    if [ -z "$local_path" ] || [ "$local_path" == "null" ]; then
        echo "Error extracting local_path from ${index_file_path}" >&2
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

# Cleanup function for test artifacts
cleanup_test_artifacts() {
    echo -e "\n${BLUE}Cleaning up test artifacts...${NC}"
    if [ -d "${HOST_CLI_TEST_DIR}" ]; then
        echo "Removing previous host CLI test directory: ${HOST_CLI_TEST_DIR}" >&2
        rm -rf "${HOST_CLI_TEST_DIR}"
    fi
    mkdir -p "${HOST_CLI_TEST_DIR}"
    cleanup_exit_code=$?
    if [ $cleanup_exit_code -ne 0 ]; then echo -e "${RED}ERROR:${NC} Failed to create host CLI test directory." >&2; return 1; fi
    if docker ps -q -f name="${CONTAINER_NAME}" >/dev/null 2>&1; then
        echo "Cleaning container download directories" >&2
        docker exec "${CONTAINER_NAME}" sh -c "rm -rf ${CONTAINER_DOWNLOADS_BASE}/* || true"
    fi
    return 0
}

# Initial cleanup
cleanup_test_artifacts
if [ $? -ne 0 ]; then echo -e "${RED}ERROR:${NC} Initial cleanup failed. Cannot continue." >&2; exit 1; fi

# Ensure clean Docker environment
echo "Starting/Restarting Docker Compose service..." >&2
docker compose down -v --timeout 10 || echo "Docker down failed (or no volumes to remove), maybe not running." >&2
docker compose up -d --build --force-recreate
up_exit_code=$?
if [ $up_exit_code -ne 0 ]; then echo -e "${RED}ERROR:${NC} docker compose up failed with exit code ${up_exit_code}. Cannot continue." >&2; exit 1; fi
echo "Waiting 20 seconds for service initialization..." >&2
sleep 20


# --- Phase 1: Basic API Health ---
echo -e "\n${BLUE}Phase 1: Basic API Health${NC}"
health_status_code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 15 "${BASE_URL}/health")
curl_exit_code=$?
health_body=""
if [ $curl_exit_code -eq 0 ] && [ "$health_status_code" -eq 200 ]; then health_body=$(curl -s --max-time 10 "${BASE_URL}/health"); curl_body_exit_code=$?; if [ $curl_body_exit_code -ne 0 ]; then health_body="(Failed to get response body)"; fi; fi
if [ $curl_exit_code -eq 0 ] && [ "$health_status_code" -eq 200 ] && echo "$health_body" | grep -q '"status":"healthy"'; then report_status 0 "Health check endpoint (/health)"; else
    if [ $curl_exit_code -ne 0 ]; then report_status $curl_exit_code "Health check endpoint (/health) - curl command failed"; elif [ "$health_status_code" -ne 200 ]; then report_status 1 "Health check endpoint (/health) - Expected status 200, got ${health_status_code}"; else report_status 1 "Health check endpoint (/health) - Status 200, but body unexpected: ${health_body}"; fi
    echo "Attempting to fetch logs on health check failure..." >&2; docker logs "${CONTAINER_NAME}" || echo "Failed to get logs." >&2; exit 1
fi


# --- Phase 2: API - Simple Web Download (example.com) ---
echo -e "\n${BLUE}Phase 2: API - Simple Web Download (example.com, depth 0)${NC}"
EXAMPLE_DOWNLOAD_ID=$(run_api_download "website" "${EXAMPLE_URL}" 0 true "Download example.com (depth 0, force=true)")
init_exit_code=$?
report_status $init_exit_code "Initiate API download for example.com"
if [ $init_exit_code -ne 0 ]; then exit 1; fi

poll_for_status "${EXAMPLE_DOWNLOAD_ID}" ${STATUS_POLL_TIMEOUT}
status_exit_code=$?
if [ $status_exit_code -eq 0 ]; then report_status 0 "Polling status check completed successfully for example.com"; elif [ $status_exit_code -eq 1 ]; then report_status 1 "Polling status check indicates download FAILED for example.com"; exit 1; else report_status 1 "Polling status check TIMED OUT for example.com"; exit 1; fi

content_exists_code=1 # Default to failure for content check
index_exists_code=1 # Default to failure for index check
if [ $status_exit_code -eq 0 ]; then
    index_file_path="${CONTAINER_INDEX_DIR}/${EXAMPLE_DOWNLOAD_ID}.jsonl"
    poll_for_file "${index_file_path}" ${FILE_POLL_TIMEOUT} "Index file (${EXAMPLE_DOWNLOAD_ID}.jsonl)"; index_exists_code=$?
    report_status $index_exists_code "Index file exists for example.com download"

    if [ $index_exists_code -eq 0 ]; then
        target_content_dir_inside_container="${CONTAINER_CONTENT_DIR}/${EXAMPLE_DOWNLOAD_ID}/example.com"
        echo "Looking for content files in directory: ${target_content_dir_inside_container}" >&2

        echo "Adding 1s sleep before checking files..." >&2; sleep 1
        echo "Checking permissions and contents of target directory inside container:" >&2
        docker exec "${CONTAINER_NAME}" ls -ld "${target_content_dir_inside_container}" || echo "  (Failed to ls -ld target dir)" >&2
        docker exec "${CONTAINER_NAME}" ls -al "${target_content_dir_inside_container}" || echo "  (Failed to ls -al target dir)" >&2

        # Check for *any* .html file
        html_files=$(docker exec "${CONTAINER_NAME}" sh -c "find \"${target_content_dir_inside_container}\" -maxdepth 1 -name '*.html' -type f 2>/dev/null")
        if [ -n "$html_files" ]; then
             report_status 0 "Found expected HTML content file(s) in location"
             content_exists_code=0 # Set success code
             echo "Found file(s): ${html_files}" >&2
             if docker exec "${CONTAINER_NAME}" sh -c "head -n 1 '${index_file_path}' | grep -q '\"fetch_status\":\"success\"'"; then report_status 0 "Index file first record shows fetch_status: success"; else report_status 1 "Index file first record DOES NOT show fetch_status: success"; docker exec "${CONTAINER_NAME}" sh -c "head -n 3 '${index_file_path}'" >&2; fi
             if docker exec "${CONTAINER_NAME}" test -d "${target_content_dir_inside_container}"; then report_status 0 "Content directory structure is correct"; else report_status 1 "Content directory structure is not correct"; fi
        else
            report_status 1 "No HTML content files found in expected location (${target_content_dir_inside_container})"
            content_exists_code=1 # Ensure failure code is set
        fi
    fi
fi


# --- Phase 3: API - Search Functionality ---
echo -e "\n${BLUE}Phase 3: API - Search Functionality (using example.com download)${NC}"
# Check prerequisites carefully
if [ "$status_exit_code" -ne 0 ] || [ "$index_exists_code" -ne 0 ] || [ "$content_exists_code" -ne 0 ]; then
     echo -e "${YELLOW}WARN:${NC} Skipping Search tests as prerequisite download/file checks failed (Status=${status_exit_code}, Index=${index_exists_code}, Content=${content_exists_code})." >&2
else
    echo "Testing search for existing content (title)..."; search_payload_1=$(cat <<EOF
{ "download_id": "${EXAMPLE_DOWNLOAD_ID}", "scan_keywords": ["Example", "Domain"], "extract_selector": "title", "extract_keywords": null }
EOF
); search_response_1=$(curl -s -X POST "${BASE_URL}/search" -H "Content-Type: application/json" --max-time ${CURL_TIMEOUT} -d "${search_payload_1}"); search_1_exit_code=$?
    if command -v jq &> /dev/null; then if [ $search_1_exit_code -eq 0 ] && echo "${search_response_1}" | jq -e '. | length > 0 and .[0].match_details == "Example Domain"' > /dev/null; then report_status 0 "Search found expected content ('Example Domain' in title via match_details)"; else report_status 1 "Search did not find expected content via match_details. Exit: ${search_1_exit_code}, Response: ${search_response_1}"; fi
    else if [ $search_1_exit_code -eq 0 ] && echo "${search_response_1}" | grep -q '"match_details": *"Example Domain"'; then report_status 0 "Search found expected content ('Example Domain' in title via match_details) (grep)"; else report_status 1 "Search did not find expected content via match_details (grep). Exit: ${search_1_exit_code}, Response: ${search_response_1}"; fi; fi

    echo "Testing search for non-existent keyword..."; search_payload_2=$(cat <<EOF
{ "download_id": "${EXAMPLE_DOWNLOAD_ID}", "scan_keywords": ["nonexistentkeywordxyz123"], "extract_selector": "p", "extract_keywords": null }
EOF
); search_response_2=$(curl -s -X POST "${BASE_URL}/search" -H "Content-Type: application/json" --max-time ${CURL_TIMEOUT} -d "${search_payload_2}"); search_2_exit_code=$?
    if [ $search_2_exit_code -eq 0 ] && [ "${search_response_2}" == "[]" ]; then report_status 0 "Search correctly returned empty array for non-existent keyword"; else report_status 1 "Search did not return empty array. Exit: ${search_2_exit_code}, Response: ${search_response_2}"; fi
fi

# --- Search Test for Invalid ID (always run) ---
echo "Testing search with invalid download ID..."; search_payload_3=$(cat <<EOF
{ "download_id": "invalid-id-does-not-exist", "scan_keywords": ["test"], "extract_selector": "title", "extract_keywords": null }
EOF
); search_response_3_body=$(curl -s -w "\n%{http_code}" -X POST "${BASE_URL}/search" -H "Content-Type: application/json" --max-time ${CURL_TIMEOUT} -d "${search_payload_3}")
search_status_3=$(echo "$search_response_3_body" | tail -n1); search_response_3_body=$(echo "$search_response_3_body" | sed '$d')
if [ "$search_status_3" -eq 404 ]; then report_status 0 "Search correctly returned 404 for invalid download ID"; else report_status 1 "Search did not return 404 for invalid download ID (Got: ${search_status_3}). Body: ${search_response_3_body}"; fi


# --- Phase 4: API - Git Download ---
echo -e "\n${BLUE}Phase 4: API - Git Download (${TEST_API_GIT_REPO_URL})${NC}" # Use correct var
GIT_DOWNLOAD_ID=$(run_api_download "git" "${TEST_API_GIT_REPO_URL}" 0 true "Download Git repo (basic fixture)") # Use correct var
init_git_exit_code=$? # Capture exit code specifically for git init
report_status $init_git_exit_code "Initiate API download for Git repo"
# Decide whether to exit based on init_git_exit_code
if [ $init_git_exit_code -ne 0 ]; then
    echo -e "${YELLOW}WARN:${NC} Git download initiation failed. Skipping subsequent Git checks." >&2
    # exit 1 # Optional: exit here if Git download is critical path
else
    # Poll for status only if initiation seemed ok
    poll_for_status "${GIT_DOWNLOAD_ID}" ${STATUS_POLL_TIMEOUT}; status_git_exit_code=$?
    if [ $status_git_exit_code -eq 0 ]; then report_status 0 "Polling status check completed successfully for Git repo"; elif [ $status_git_exit_code -eq 1 ]; then report_status 1 "Polling status check indicates download FAILED for Git repo"; else report_status 1 "Polling status check TIMED OUT for Git repo"; fi

    # Verify files only if status is completed
    if [ $status_git_exit_code -eq 0 ]; then
        git_index_file_path="${CONTAINER_INDEX_DIR}/${GIT_DOWNLOAD_ID}.jsonl"
        poll_for_file "${git_index_file_path}" ${FILE_POLL_TIMEOUT} "Index file (${GIT_DOWNLOAD_ID}.jsonl)"; index_git_exists_code=$?
        report_status $index_git_exists_code "Index file exists for Git download"
        if [ $index_git_exists_code -eq 0 ]; then
            expected_repo_dir="${CONTAINER_CONTENT_DIR}/${GIT_DOWNLOAD_ID}/repo"
            expected_readme_path="${expected_repo_dir}/README" # Git fixture has README without extension
            poll_for_file "${expected_repo_dir}" ${FILE_POLL_TIMEOUT} "Git repo content directory (repo/)"; repo_dir_exists_code=$?
            report_status $repo_dir_exists_code "Git repo content directory exists"
            if [ $repo_dir_exists_code -eq 0 ]; then poll_for_file "${expected_readme_path}" ${FILE_POLL_TIMEOUT} "Known file (README) in Git repo"; readme_exists_code=$?; report_status $readme_exists_code "Known file (README) exists in Git repo"; fi
            if docker exec "${CONTAINER_NAME}" sh -c "head -n 1 '${git_index_file_path}' | grep -q '\"fetch_status\":\"success\"'"; then report_status 0 "Git index file first record shows fetch_status: success"; else report_status 1 "Git index file first record DOES NOT show fetch_status: success"; docker exec "${CONTAINER_NAME}" sh -c "head -n 3 '${git_index_file_path}'" >&2; fi
        fi
    fi
fi


# --- Phase 5: API - Playwright Download ---
echo -e "\n${BLUE}Phase 5: API - Playwright Download (example.com)${NC}"
PW_DOWNLOAD_ID=$(run_api_download "playwright" "${EXAMPLE_URL}" 0 true "Download example.com via Playwright")
init_pw_exit_code=$? # Capture exit code
report_status $init_pw_exit_code "Initiate Playwright download for example.com"
if [ $init_pw_exit_code -eq 0 ]; then
    poll_for_status "${PW_DOWNLOAD_ID}" $((STATUS_POLL_TIMEOUT * 2)); status_pw_exit_code=$?
    if [ $status_pw_exit_code -eq 0 ]; then report_status 0 "Polling status check completed successfully for Playwright download"; elif [ $status_pw_exit_code -eq 1 ]; then report_status 1 "Polling status check indicates Playwright download FAILED"; else report_status 1 "Polling status check TIMED OUT for Playwright download"; fi

    if [ $status_pw_exit_code -eq 0 ]; then # Only check files if status completed successfully
        pw_index_file_path="${CONTAINER_INDEX_DIR}/${PW_DOWNLOAD_ID}.jsonl"
        poll_for_file "${pw_index_file_path}" ${FILE_POLL_TIMEOUT} "Index file (${PW_DOWNLOAD_ID}.jsonl)"; index_pw_exists_code=$?
        report_status $index_pw_exists_code "Index file exists for Playwright download"
        if [ $index_pw_exists_code -eq 0 ]; then
            extracted_content_path=$(extract_first_local_path "${pw_index_file_path}")
            extract_code=$?
            if [ $extract_code -eq 0 ] && [ -n "$extracted_content_path" ]; then
                report_status 0 "Extracted content path from Playwright index: ${extracted_content_path}"
                poll_for_file "${extracted_content_path}" ${FILE_POLL_TIMEOUT} "Playwright Content file (from index)"; content_pw_exists_code=$?
                report_status $content_pw_exists_code "Playwright Content file exists at path specified in index"
            else report_status 1 "Failed to extract valid content path from Playwright index file"; fi
        fi
    fi
else echo -e "${YELLOW}WARN:${NC} Skipping Playwright download checks as initiation failed." >&2; fi


# --- Phase 6: CLI - Web Download ---
echo -e "\n${BLUE}Phase 6: CLI - Web Download (example.com)${NC}" >&2
CLI_WEB_OUT_DIR="${HOST_CLI_TEST_DIR}/web"; mkdir -p "${CLI_WEB_OUT_DIR}"; echo "Running CLI web download..." >&2
# Use full module path for execution
cli_web_cmd="uv run python -m mcp_doc_retriever.cli download run --source-type website --url ${EXAMPLE_URL} --download-id cli_web_example --downloads-dir ${CLI_WEB_OUT_DIR} --depth 0 --force"
echo "Executing: ${cli_web_cmd}" >&2
cli_web_output=$(timeout ${CLI_TIMEOUT} ${cli_web_cmd} 2>&1); cli_web_exit_code=$?
report_status $cli_web_exit_code "CLI web download command execution"; echo "CLI Output (Web):"; echo "${cli_web_output}"; echo "--- End CLI Output ---" >&2
if [ $cli_web_exit_code -eq 0 ]; then
    cli_web_download_id="cli_web_example"; report_status 0 "Using CLI web download ID: ${cli_web_download_id}"
    cli_web_index_path="${CLI_WEB_OUT_DIR}/index/${cli_web_download_id}.jsonl"
    expected_host_dir="${CLI_WEB_OUT_DIR}/content/cli_web_example/example.com"
    if [ -f "$cli_web_index_path" ]; then report_status 0 "CLI web index file exists on host: ${cli_web_index_path}"; else report_status 1 "CLI web index file NOT found on host: ${cli_web_index_path}"; fi
    if [ -d "$expected_host_dir" ]; then report_status 0 "CLI web download directory exists with correct structure"; html_files=$(find "$expected_host_dir" -maxdepth 1 -name "*.html" -type f); if [ -n "$html_files" ]; then report_status 0 "Found downloaded HTML files in expected location"; echo "Found files:\n${html_files}" >&2; else report_status 1 "No HTML files found in expected location (${expected_host_dir})"; fi; else report_status 1 "Expected CLI web download directory not found: ${expected_host_dir}"; find "${CLI_WEB_OUT_DIR}" -type d >&2; fi
else echo "CLI web command failed. Last few lines of output:"; echo "${cli_web_output}" | tail -n 10 >&2; fi


# --- Phase 7: CLI - Git Download ---
echo -e "\n${BLUE}Phase 7: CLI - Git Download (Local Test Repo)${NC}" >&2
CLI_GIT_OUT_DIR="${HOST_CLI_TEST_DIR}/git"; mkdir -p "${CLI_GIT_OUT_DIR}";
# Use the public repo URL for CLI test too
HOST_TEST_GIT_REPO_URL="https://github.com/git-fixtures/basic.git"
echo "Running CLI git download..." >&2
cli_git_download_id="cli_git_example"
# Use full module path and correct argument name (--repo-url) and add --doc-path
cli_git_cmd="uv run python -m mcp_doc_retriever.cli download run --source-type git --repo-url ${HOST_TEST_GIT_REPO_URL} --doc-path . --download-id ${cli_git_download_id} --downloads-dir ${CLI_GIT_OUT_DIR} --force"
echo "Executing: ${cli_git_cmd}" >&2
cli_git_output=$(timeout ${CLI_TIMEOUT} ${cli_git_cmd} 2>&1); cli_git_exit_code=$?
report_status $cli_git_exit_code "CLI git download command execution"; echo "CLI Output (Git):"; echo "${cli_git_output}"; echo "--- End CLI Output ---" >&2
if [ $cli_git_exit_code -eq 0 ]; then
    report_status 0 "Using CLI git download ID: ${cli_git_download_id}"; cli_git_index_path="${CLI_GIT_OUT_DIR}/index/${cli_git_download_id}.jsonl"; cli_git_content_repo_path="${CLI_GIT_OUT_DIR}/content/${cli_git_download_id}/repo"; cli_git_readme_path="${cli_git_content_repo_path}/README"; checks_passed=0; total_checks=3
    if [ -f "$cli_git_index_path" ]; then report_status 0 "CLI git index file exists on host: ${cli_git_index_path}"; ((checks_passed++)); else report_status 1 "CLI git index file NOT found on host: ${cli_git_index_path}"; fi
    if [ -d "$cli_git_content_repo_path" ]; then report_status 0 "CLI git content directory exists with correct structure"; ((checks_passed++)); else report_status 1 "CLI git content directory NOT found: ${cli_git_content_repo_path}"; find "${CLI_GIT_OUT_DIR}" -type d >&2; fi
    if [ -f "$cli_git_readme_path" ]; then report_status 0 "CLI git critical files (README) exist in expected location"; ((checks_passed++)); else report_status 1 "CLI git critical files NOT found in expected location"; echo "Missing: ${cli_git_readme_path}" >&2; fi
    if [ $checks_passed -eq $total_checks ]; then report_status 0 "All CLI git download structure checks passed ($checks_passed/$total_checks)"; else report_status 1 "Some CLI git download structure checks failed ($checks_passed/$total_checks)"; fi
else echo "CLI git command failed. Last few lines of output:"; echo "${cli_git_output}" | tail -n 10 >&2; fi

# --- Script Footer ---
exec 3>&- # Close fd 3
echo -e "\n${BLUE}Script Completion:${NC}"
echo "=================================================="
echo -e "\n--- Test Summary ---"
cat "$TEST_RESULTS_FILE" # Print collected results
fail_count=$(grep -c "\[FAIL\]" "$TEST_RESULTS_FILE")
pass_count=$(grep -c "\[PASS\]" "$TEST_RESULTS_FILE")
echo "--------------------"
echo -e "Total Checks Reported: $((pass_count + fail_count))"
echo -e "${GREEN}Passed: ${pass_count}${NC}"
echo -e "${RED}Failed: ${fail_count}${NC}"
echo "=================================================="

# Final cleanup
if [ "${KEEP_DOWNLOADS:-false}" != "true" ]; then
    echo -e "\n${BLUE}Performing final cleanup...${NC}"; cleanup_test_artifacts
    if [ "${KEEP_CONTAINER:-false}" != "true" ]; then echo "Stopping container..." >&2; docker compose down -v --timeout 10; else echo "Container left running for inspection."; fi
else
    echo -e "\n${YELLOW}Note:${NC} Downloads preserved (KEEP_DOWNLOADS=true)"
    if [ "${KEEP_CONTAINER:-false}" == "true" ]; then echo "Container left running (KEEP_CONTAINER=true)"; else echo "Stopping container..."; docker compose down -v --timeout 10; fi
fi

echo -e "\nTest artifacts location (host): ${HOST_CLI_TEST_DIR}"
echo "=================================================="

# Exit with overall status
rm "$TEST_RESULTS_FILE" # Clean up temp file
if [ "$fail_count" -gt 0 ]; then
    echo "Exiting with status 1 due to FAIL results." >&2
    exit 1
else
    echo "Exiting with status 0 (all checks passed)." >&2
    exit 0
fi