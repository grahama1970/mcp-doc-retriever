#!/bin/bash

# Sanity Check Script for MCP Document Retriever Service

# --- Configuration ---
BASE_URL="http://localhost:8001" # Adjust if your host/port differs
CONTAINER_NAME="mcp-doc-retriever"
PYTHON_DOCS_URL="https://docs.python.org/3/"
EXAMPLE_URL="https://example.com/"
# Timeout for curl requests (seconds)
CURL_TIMEOUT=60
POLL_INTERVAL=2 # seconds between status checks
STATUS_POLL_TIMEOUT=60 # seconds max wait for status to become completed/failed
FILE_POLL_TIMEOUT=30 # seconds max wait for a specific file (index/content)


# --- Helper Functions ---
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
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
# Usage: poll_for_file FILE_PATH TIMEOUT
poll_for_file() {
    local file_path=$1
    local timeout=$2
    local start_time=$(date +%s)
    echo -n "Polling for file: ${file_path} (max ${timeout}s)..." >&2 # Log to stderr, no newline
    while true; do
        if docker exec "${CONTAINER_NAME}" test -f "${file_path}"; then
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

# --- NEW Function to poll for download status ---
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
                    local error_msg=$(echo "${response_json}" | jq -r '.error_details // "N/A"')
                    echo "Error details: ${error_msg:0:100}..." >&2
                    return 1 # Failure
                elif [ "$status" == "running" ] || [ "$status" == "pending" ] ; then
                     echo -n "." >&2 # Still working
                else
                    echo -n "?" >&2 # Unknown status
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


# Function to run download and capture ID
# Usage: download_id=$(run_download "URL" DEPTH FORCE_FLAG "Description")
run_download() {
    local url=$1
    local depth=$2
    local force=$3
    local desc=$4
    local use_pw=${5:-false} # Optional playwright flag, default false

    # Print status messages to stderr
    echo "Initiating Download: ${desc}" >&2
    echo "URL: ${url}, Depth: ${depth}, Force: ${force}, Use Playwright: ${use_pw}" >&2

    local payload
    payload=$(cat <<EOF
{
  "url": "${url}",
  "depth": ${depth},
  "force": ${force},
  "use_playwright": ${use_pw}
}
EOF
)

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

    local download_id
    local status
    # Use jq if available, otherwise grep/cut
    if command -v jq &> /dev/null; then
        download_id=$(echo "${response_json}" | jq -r '.download_id')
        status=$(echo "${response_json}" | jq -r '.status')
    else
        download_id=$(echo "${response_json}" | grep -o '"download_id": *"[^"]*"' | cut -d'"' -f4)
        status=$(echo "${response_json}" | grep -o '"status": *"[^"]*"' | cut -d'"' -f4)
    fi

    # Check for 'started' or 'failed_validation'
    if [ "$status" == "started" ] && [ -n "$download_id" ] && [ "$download_id" != "null" ]; then
        echo "Download Task Started. ID: ${download_id}" >&2
        echo "${download_id}" # Print ONLY the download_id to stdout for capture
        echo "" >&2
        return 0 # Signal success
    elif [ "$status" == "failed_validation" ]; then
         echo -e "${YELLOW}WARN:${NC} Download request failed validation." >&2
         local message=$(echo "${response_json}" | jq -r '.message // "N/A"')
         echo "Validation message: ${message}" >&2
         echo "" >&2
         return 1 # Signal failure (validation failure)
    else
        echo -e "${RED}ERROR:${NC} Did not receive 'started' status or valid download_id." >&2
        echo "Response: ${response_json}" >&2
        echo "" >&2
        return 1 # Signal failure
    fi
}


# --- Test Execution ---
# Force clean slate
# echo "Ensuring clean Docker environment (down -v --rmi all)..." >&2
# docker compose down -v --rmi all --timeout 5 || echo "Docker down failed, continuing..." >&2
echo "Starting/Restarting Docker Compose service..." >&2
docker compose down --timeout 5 || echo "Docker down failed, maybe not running." >&2
docker compose up -d --build --force-recreate # Build and start fresh
up_exit_code=$?
if [ $up_exit_code -ne 0 ]; then
    echo -e "${RED}ERROR:${NC} docker compose up failed with exit code ${up_exit_code}. Cannot continue." >&2
    exit 1
fi
# Add a longer delay to allow the service to initialize before the first health check, especially after a build
echo "Waiting 15 seconds for service initialization..." >&2
sleep 15


echo "=================================================="
echo "Starting MCP Document Retriever Sanity Checks..."
echo "Target: ${BASE_URL}"
echo "Container: ${CONTAINER_NAME}"
echo "=================================================="

# --- Phase 1: Basic API Health ---
echo "Phase 1: Basic API Health"
health_status_code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 "${BASE_URL}/health")
curl_exit_code=$?
health_body=""
if [ $curl_exit_code -eq 0 ] && [ "$health_status_code" -eq 200 ]; then
    health_body=$(curl -s --max-time 10 "${BASE_URL}/health")
    curl_body_exit_code=$?
    if [ $curl_body_exit_code -ne 0 ]; then health_body="(Failed to get response body)"; fi
fi
if [ $curl_exit_code -eq 0 ] && [ "$health_status_code" -eq 200 ] && [[ "$health_body" == *"\"status\":\"healthy\""* ]]; then
  report_status 0 "Health check endpoint (/health)"
else
  if [ $curl_exit_code -ne 0 ]; then
      report_status $curl_exit_code "Health check endpoint (/health) - curl command failed"
  elif [ "$health_status_code" -ne 200 ]; then
      report_status 1 "Health check endpoint (/health) - Expected status 200, got ${health_status_code}"
  else
      report_status 1 "Health check endpoint (/health) - Status 200, but body unexpected: ${health_body}"
  fi
  # Check logs if health fails after startup attempt
  echo "Attempting to fetch logs on health check failure..." >&2
  docker logs "${CONTAINER_NAME}" || echo "Failed to get logs." >&2
  exit 1 # Exit if health check fails
fi


# --- Phase 2: Simple Download and Verification ---
echo "Phase 2: Simple Download (example.com, depth 0)"
EXAMPLE_DOWNLOAD_ID=$(run_download "${EXAMPLE_URL}" 0 true "Download example.com (depth 0, force=true)")
test_exit_code=$?
report_status $test_exit_code "Initiate download for example.com"
if [ $test_exit_code -ne 0 ]; then exit 1; fi

# --- ADDED: Poll for status completion ---
poll_for_status "${EXAMPLE_DOWNLOAD_ID}" ${STATUS_POLL_TIMEOUT}
status_exit_code=$?
if [ $status_exit_code -eq 0 ]; then
    report_status 0 "Polling status check completed successfully for example.com"
elif [ $status_exit_code -eq 1 ]; then
    report_status 1 "Polling status check indicates download FAILED for example.com"
    # Decide whether to exit or continue
    # exit 1
else # Timeout
    report_status 1 "Polling status check TIMED OUT for example.com"
    exit 1 # Exit if status doesn't resolve
fi

# Only proceed with file/search checks if status is completed
if [ $status_exit_code -eq 0 ]; then
    index_path="/app/downloads/index/${EXAMPLE_DOWNLOAD_ID}.jsonl"
    content_path="/app/downloads/content/example.com/index.html"

    # Check file existence (use shorter timeout now, should be quick after completion)
    poll_for_file "${index_path}" ${FILE_POLL_TIMEOUT}
    test_exit_code=$?
    report_status $test_exit_code "Index file exists for example.com download"

    if [ $test_exit_code -eq 0 ]; then
        # Check index content
        echo "Checking example.com index file content for success..."
        # Use jq for a more robust check if available, otherwise fallback to less strict grep
        if docker exec "${CONTAINER_NAME}" sh -c "command -v jq >/dev/null && jq -e '.fetch_status == \"success\" and .canonical_url == \"${EXAMPLE_URL}\"' '${index_path}' >/dev/null 2>&1 || grep -q '\"fetch_status\":\"success\"' '${index_path}'"; then
           # Also verify the path within the index record matches the expected content_path
           if docker exec "${CONTAINER_NAME}" sh -c "command -v jq >/dev/null && jq -e '.local_path == \"${content_path}\"' '${index_path}' >/dev/null 2>&1 || grep -q '\"local_path\":\"${content_path}\"' '${index_path}'"; then
               report_status 0 "Index file shows success status and correct path for example.com"
           else
               report_status 1 "Index file shows success status BUT incorrect path for example.com"
               docker exec "${CONTAINER_NAME}" cat "${index_path}" || echo "(Failed to read index)" >&2
           fi
        else
           report_status 1 "Index file DOES NOT show success status for example.com"
           docker exec "${CONTAINER_NAME}" cat "${index_path}" || echo "(Failed to read index)" >&2
        fi
    fi

    poll_for_file "${content_path}" ${FILE_POLL_TIMEOUT}
    report_status $? "Content file (index.html) exists for example.com download"

    # --- Phase 3: Search Functionality (moved inside status check) ---
    echo "Phase 3: Search Functionality (using example.com download)"

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
    if [ $search_1_exit_code -eq 0 ] && echo "${search_response_1}" | grep -q '"Example Domain"'; then
        report_status 0 "Search found expected content ('Example Domain' in title)"
    else
        report_status 1 "Search did not find expected content. Exit: ${search_1_exit_code}, Response: ${search_response_1}"
    fi

    echo "Testing search for non-existent keyword..."
    search_payload_2=$(cat <<EOF
{
  "download_id": "${EXAMPLE_DOWNLOAD_ID}",
  "scan_keywords": ["nonexistentkeywordxyz"],
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
else
    echo -e "${YELLOW}WARN:${NC} Skipping file and search tests for example.com as download did not complete successfully." >&2
fi # End of status check block

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
search_status_3=$(curl -s -o /dev/null -w '%{http_code}' -X POST "${BASE_URL}/search" -H "Content-Type: application/json" --max-time ${CURL_TIMEOUT} -d "${search_payload_3}")
if [ "$search_status_3" -eq 404 ]; then
    report_status 0 "Search correctly returned 404 for invalid download ID"
else
    report_status 1 "Search did not return 404 for invalid download ID (Got: ${search_status_3})"
fi


# --- Phase 4: Deeper Download & Path Verification ---
echo "Phase 4: Deeper Download (Python Docs, depth 1)"
PYTHON_DOCS_ID=$(run_download "${PYTHON_DOCS_URL}" 1 true "Download Python Docs (depth 1, force=true)")
test_exit_code=$?
report_status $test_exit_code "Initiate download for Python Docs"
if [ $test_exit_code -ne 0 ]; then exit 1; fi

# --- ADDED: Poll for Python Docs status completion ---
poll_for_status "${PYTHON_DOCS_ID}" $((STATUS_POLL_TIMEOUT * 2)) # Longer timeout for deeper crawl
status_exit_code=$?
if [ $status_exit_code -eq 0 ]; then
    report_status 0 "Polling status check completed successfully for Python Docs"
elif [ $status_exit_code -eq 1 ]; then
    report_status 1 "Polling status check indicates download FAILED for Python Docs"
    # exit 1
else # Timeout
    report_status 1 "Polling status check TIMED OUT for Python Docs"
    exit 1
fi

if [ $status_exit_code -eq 0 ]; then
    # POLL for the root index file and content file (shorter timeout now)
    py_index_path="/app/downloads/index/${PYTHON_DOCS_ID}.jsonl"
    py_content_path="/app/downloads/content/docs.python.org/3/index.html"

    poll_for_file "${py_index_path}" ${FILE_POLL_TIMEOUT}
    index_exists=$?
    poll_for_file "${py_content_path}" ${FILE_POLL_TIMEOUT}
    content_exists=$?

    report_status $index_exists "Python Docs index file exists"
    report_status $content_exists "Python Docs root content file exists"

    if [ $index_exists -eq 0 ]; then
        echo "Checking Python Docs index file content for CORRECT path..."
        # Using grep fallback here for simplicity in the example
        if docker exec "${CONTAINER_NAME}" grep -q "\"canonical_url\":\"${PYTHON_DOCS_URL}\"" "${py_index_path}" && \
           docker exec "${CONTAINER_NAME}" grep -q "\"local_path\":\"${py_content_path}\"" "${py_index_path}"; then
           report_status 0 "Index file contains correct root URL and path structure"
        else
           report_status 1 "Index file DOES NOT contain correct root URL/path structure"
           echo "Index content sample:" >&2
           docker exec "${CONTAINER_NAME}" head -n 5 "${py_index_path}" || echo "(Failed to read index)" >&2
        fi
    fi
else
     echo -e "${YELLOW}WARN:${NC} Skipping file checks for Python Docs as download did not complete successfully." >&2
fi # End Python Docs status check


# --- Phase 5: Playwright Download Test (Automated) ---
echo "Phase 5: Playwright Download Test (Automated)"
PW_DOWNLOAD_ID=$(run_download "${EXAMPLE_URL}" 0 true "Download example.com via Playwright" true)
test_exit_code=$?
report_status $test_exit_code "Initiate Playwright download for example.com"

if [ $test_exit_code -eq 0 ]; then
    # Poll for Playwright status completion
    poll_for_status "${PW_DOWNLOAD_ID}" $((STATUS_POLL_TIMEOUT * 2)) # Longer timeout for playwright
    status_exit_code=$?
    if [ $status_exit_code -eq 0 ]; then
        report_status 0 "Polling status check completed successfully for Playwright download"
    elif [ $status_exit_code -eq 1 ]; then
        report_status 1 "Polling status check indicates Playwright download FAILED"
        # exit 1
    else # Timeout
        report_status 1 "Polling status check TIMED OUT for Playwright download"
        exit 1
    fi

    if [ $status_exit_code -eq 0 ]; then
        # Poll for files (shorter timeout now)
        pw_index_path="/app/downloads/index/${PW_DOWNLOAD_ID}.jsonl"
        pw_content_path="/app/downloads/content/example.com/index.html"

        poll_for_file "${pw_index_path}" ${FILE_POLL_TIMEOUT}
        pw_index_exists=$?
        poll_for_file "${pw_content_path}" ${FILE_POLL_TIMEOUT}
        pw_content_exists=$?

        report_status $pw_index_exists "Playwright index file exists"
        report_status $pw_content_exists "Playwright content file exists"

        if [ $pw_index_exists -eq 0 ]; then
            echo "Verifying Playwright index file content for success..."
            # Use jq for a more robust check if available, otherwise fallback to less strict grep
            if docker exec "${CONTAINER_NAME}" sh -c "command -v jq >/dev/null && jq -e '.fetch_status == \"success\" and .canonical_url == \"${EXAMPLE_URL}\"' '${pw_index_path}' >/dev/null 2>&1 || grep -q '\"fetch_status\":\"success\"' '${pw_index_path}'"; then
               report_status 0 "Index file shows success status for Playwright download"
            else
               report_status 1 "Index file DOES NOT show success status for Playwright download"
               docker exec "${CONTAINER_NAME}" cat "${pw_index_path}" || echo "(Failed to read index)" >&2
            fi
        fi
    else
        echo -e "${YELLOW}WARN:${NC} Skipping file checks for Playwright download as it did not complete successfully." >&2
    fi # End Playwright status check
fi
echo "--------------------------------------------------"


# --- Script Footer ---
echo ""
echo "=================================================="
echo "Sanity Check Script Finished."
echo "Review output above for PASS/FAIL details."
echo "=================================================="
