# MCP Document Retriever Service 🌐💾🔎

## Overview 🌟

`mcp-doc-retriever` is a Dockerized FastAPI application designed to act as a Model Context Protocol (MCP) server for agents like Roo Code. Its primary function is to download website content recursively, storing it locally in a mirrored site structure, and provide a powerful two-phase search capability across the downloaded documents.

The service first attempts downloads using efficient methods (Python `requests`). It automatically detects likely JavaScript-heavy pages (based on simple heuristics) and retries fetching them using Playwright for full browser rendering. Downloads are stored locally preserving the site hierarchy (`hostname/path/file.html`), and the service avoids re-downloading existing files by *path* unless explicitly forced (`force=true`). An index file is maintained for each download job, mapping original URLs to local paths and content hashes. The search function allows agents to first quickly scan relevant files for keywords, then perform precise text extraction using CSS selectors on the identified candidate pages.

This project is intended to be built and potentially maintained using an agentic workflow, specifically following the Roomodes framework described below.

## ✨ Features

*   ✅ **Recursive Website Download:** Downloads HTML content starting from a URL, following links within the same domain and its subdomains (configurable depth).
*   ✅ **Mirrored Storage:** Saves downloaded files locally preserving the original site's directory structure (e.g., `hostname/path/file.html`) within a persistent volume.
*   ✅ **Smart Fetching with Auto-Fallback:** Uses `requests` primarily. Automatically detects and retries likely JS-heavy pages using Playwright. Supports forcing Playwright via API flag.
*   ✅ **Efficient Re-fetching:** Avoids re-downloading files if they already exist at the target *path* (`--no-clobber` behavior) unless overridden by a `force=true` flag.
*   ✅ **Download Indexing:** Maintains a JSON Lines index file per download job, mapping original URLs to canonical URLs, local file paths, content MD5 hashes, and fetch status.
*   ✅ **Error Handling:** Reports basic issues like invalid start URLs, connection errors, detected logins/paywalls back to the agent (where appropriate). Logs page-level errors during recursion. Basic `robots.txt` respect.
*   ✅ **Two-Phase Search (Job-Scoped):**
    1.  **Fast Scan:** Uses the index file to identify relevant local files for a download job, then quickly scans the *decoded text content* of those files for keywords.
    2.  **Precise Extraction:** Parses candidate pages (identified by scan) using BeautifulSoup and applies CSS selectors to extract specific *text content* (`.get_text()`). Can further filter results by keywords within the extracted text.
*   ✅ **Concurrency Control:** Uses `asyncio` Semaphores to limit concurrent `requests` and Playwright operations.
*   ✅ **Structured I/O:** Uses Pydantic models for API requests/responses.
*   ✅ **MCP Compatible:** Designed for easy integration as an MCP server.
*   ✅ **Dockerized & Self-Contained:** Packaged with `docker-compose`, includes Playwright browser dependencies in the image, uses a named volume for persistent storage.
*   ✅ **Standard Packaging:** Uses `pyproject.toml` and `uv`.

## 🏗️ Runtime Architecture Diagram

```mermaid
graph TD
    subgraph "External Agent (e.g., Roo Code)"
        Agent -- "POST /download\n(DownloadRequest JSON)" --> FAPI
        Agent -- "POST /search\n(SearchRequest JSON)" --> FAPI
    end

    subgraph "Docker Container: mcp-doc-retriever"
        FAPI(🌐 FastAPI - main.py)

        subgraph "Download Flow"
            direction TB
            FAPI -- Parse Request --> Utils(🔧 Utils - utils.py)
            Utils -- Canonical URL & Download ID --> BGTask{🚀 Start Background Task}
            BGTask -- Orchestrate --> Downloader(⚙️ Async Downloader - downloader.py)

            Downloader -- URL --> Utils # Canonicalize for Visited Check
            Downloader -- Need Fetch? (Path Exists?) --> ContentFS[(📁 Content Volume - /app/downloads/content)]
            Downloader -- Fetch --> FetchChoice{Auto-Detect JS? Use Playwright Flag?}
            FetchChoice -- Requests --> RequestsLib(🐍 Requests)
            FetchChoice -- Playwright --> PlaywrightLib(🎭 Playwright)
            RequestsLib -- Fetch --> TargetSite[🌍 Target Website]
            PlaywrightLib -- Render & Fetch --> TargetSite
            TargetSite -- HTML --> FetchResult{Content + Status}
            FetchResult -- Calculate MD5 --> Downloader
            FetchResult -- Save Content --> ContentFS # Save to hostname/path/file.html
            Downloader -- Log Attempt --> IndexFS[(💾 Index Volume - /app/downloads/index)] # Write IndexRecord to {download_id}.jsonl
            FetchResult -- Extract Links (BS4) --> LinkQueue[/Links/]
            LinkQueue -- Next URL --> Downloader # Recursive Loop (Check Domain/Depth/Visited/Robots)

            FAPI -- "Immediate Response\n(DownloadStatus JSON)" --> AgentResp(HTTP Response) # Contains download_id
        end

        subgraph "Search Flow"
            direction TB
            FAPI -- Parse SearchRequest --> Searcher(🔎 Searcher - searcher.py)
            Searcher -- Read Index File (download_id) --> IndexFS
            IndexFS -- Relevant Local Paths --> Searcher
            Searcher -- Phase 1: Scan Keywords --> ContentFS # Read content from relevant paths
            ContentFS -- Content --> ScanFunc(📜 Decode & Scan Text)
            ScanFunc -- Candidate Paths --> Searcher
            Searcher -- Phase 2: Parse & Extract --> ContentFS # Read content from candidate paths
            ContentFS -- Content --> ExtractFunc(🌳 BS4 Parse & Select Text)
            ExtractFunc -- Extracted Text --> Searcher
            Searcher -- Lookup Original URL --> IndexFS # Use index to map path back to URL
            Searcher -- Formatted Results --> FAPI
            FAPI -- "SearchResponse JSON" --> AgentResp
        end

    end

    AgentResp --> Agent


    %% Styling
    classDef default fill:#f9f,stroke:#333,stroke-width:2px
    classDef agent fill:#ccf,stroke:#333
    classDef fastapi fill:#9cf,stroke:#333
    classDef logic fill:#9fc,stroke:#333
    classDef io fill:#ff9,stroke:#333
    classDef external fill:#ccc,stroke:#333
    classDef data fill:#eee,stroke:#666,stroke-dasharray: 5 5
    classDef util fill:#fdf,stroke:#333

    class Agent,AgentResp agent;
    class FAPI fastapi;
    class Utils util;
    class BGTask,Downloader,Searcher,ScanFunc,ExtractFunc logic;
    class FetchChoice,RequestsLib,PlaywrightLib,LinkQueue,FetchResult io;
    class ContentFS,IndexFS data;
    class TargetSite external;
Use code with caution.
Markdown
🛠️ Technology Stack
Language: Python 3.10+

Web Framework: FastAPI

HTTP Client: Requests

Browser Automation: Playwright

HTML Parsing: BeautifulSoup4, lxml

Data Validation: Pydantic

Concurrency: Asyncio (with Semaphores)

Containerization: Docker, Docker Compose

Dependency Management: uv, pyproject.toml

🤖 Roomodes Workflow (Project Construction)
This project is designed to be built and maintained using the Roomodes framework, leveraging specialized AI agents for different tasks as defined in the .roomodes configuration file. The primary flow for building features or fixing bugs involves:

📝 Planner: Reads the task.md document for this project. It identifies the next incomplete task ([ ]) and delegates it via new_task to Boomerang Mode. After Boomerang reports success, Planner performs Git actions (add, commit, tag) and updates task.md.

🪃 Boomerang Mode: Receives the task from Planner. Analyzes it, breaks it down if necessary, and delegates functional sub-tasks (like implementing downloader logic or search logic) sequentially to the appropriate Coder agent (Intern, Junior, or Senior). After coding, it mandates Presenter to demonstrate functionality and Hacker to check security (if applicable for code changes), managing remediation loops with Coders if needed. Optionally coordinates with Refactorer. Reports overall success or failure back to Planner. It may prompt specialists to log lessons.

🧑‍💻 Coder Agents (Intern/Junior/Senior): Receive specific coding tasks from Boomerang Mode.

Mandatory Documentation Step: Before coding, Coders must download relevant documentation for required libraries (e.g., FastAPI, Requests, Playwright, BeautifulSoup) into the repo_docs/ directory (using git sparse-checkout or similar via command tool if needed).

Implementation: They implement the code, adhering to standards (using uv for dependencies via pyproject.toml, adding standard header comments, keeping file sizes manageable, creating inline examples or test scripts).

Mandatory Doc Reference: Coders must grep or search (search_files) the downloaded documentation in repo_docs/ first when encountering errors or needing to understand library usage before escalating or trying external search. They also consult src/mcp_doc_retriever/docs/lessons_learned.json.

Reporting: Report completion/results back to Boomerang Mode. Junior/Senior coders log lessons if prompted and applicable.

(Other Roles): Researcher, Librarian, Hacker, Presenter, Refactorer perform their specialized functions as orchestrated by Boomerang Mode, following their specific instructions from the .roomodes configuration, including consulting/updating lessons_learned.json and using repo_docs/ where applicable.

📁 Project Structure
mcp-doc-retriever/
├── .git/
├── .gitignore
├── .env.example        # Example environment variables (if needed)
├── .venv/              # Virtual environment (managed by uv)
├── .roomodes           # (If project-specific modes are defined here)
├── docker-compose.yml  # Docker Compose configuration
├── Dockerfile          # Docker build instructions
├── pyproject.toml      # Project metadata and dependencies (for uv)
├── uv.lock             # Locked dependency file
├── README.md           # This file
├── task.md             # Detailed development task plan for Planner agent
├── repo_docs/          # Downloaded third-party documentation (managed by Coder Agent)
│   └── ...
├── scripts/            # Utility and testing scripts
│   ├── test_api.py
│   ├── test_download.py # (Optional direct module test)
│   └── test_search.py   # (Optional direct module test)
└── src/
    └── mcp_doc_retriever/ # Main application source code
        ├── __init__.py
        ├── main.py           # FastAPI application entrypoint
        ├── models.py         # Pydantic models for API and data structures
        ├── downloader.py     # Core download logic (requests, playwright, recursion, indexing)
        ├── searcher.py       # Two-phase search logic (scan, selector extraction)
        ├── config.py         # (Optional: Configuration like concurrency limits)
        ├── utils.py          # Helper functions (URL canonicalization, path gen, ID gen, robots)
        └── docs/             # Internal project documentation
            └── lessons_learned.json # Database for agent lessons learned

# NOTE: The 'download_data' directory containing index/ and content/ subdirs
# is created ON THE HOST by Docker when the volume is mounted if it doesn't exist.
# It's mapped to /app/downloads inside the container.
Use code with caution.
⚙️ Setup & Installation
Prerequisites:

Docker Desktop (or Docker Engine)

Docker Compose

Git

Python 3.10+ (for running uv locally if needed)

uv (Install via pip install uv or preferred method)

Steps:

Clone the Repository:

git clone <repository-url>
cd mcp-doc-retriever
Use code with caution.
Bash
(Optional) Configure Environment Variables:

Copy .env.example to .env if specific runtime configurations are needed.

(Optional) Local Development Setup:

# Coder agents would typically run these commands when tasked
uv venv # Create virtual environment
uv sync # Install dependencies
# playwright install # Install browsers locally if needed for non-Docker tests
Use code with caution.
Bash
🚀 Running the Service
Build the Docker Images: (This will install dependencies and Playwright browsers inside the image)

docker compose build
Use code with caution.
Bash
Start the Services (FastAPI App):

docker compose up -d
Use code with caution.
Bash
This starts the mcp-doc-retriever container.

It maps a host port (e.g., 8001 - check docker-compose.yml) to the container's port 8000.

It creates/mounts the named volume download_data (defined in docker-compose.yml) to /app/downloads inside the container. Docker manages this volume's location on the host. The index/ and content/ subdirectories will be created within this volume by the application as needed.

Verify Service is Running:

docker compose logs mcp_doc_retriever
# Check logs for Uvicorn startup messages.
# You can inspect the volume content using Docker commands if needed.
Use code with caution.
Bash
Stopping the Services:

docker compose down
# To remove the persistent download data volume:
# docker compose down -v
Use code with caution.
Bash
💻 API Usage
1. Download Endpoint
Endpoint: POST /download

Purpose: Initiates the recursive download process for a given URL. Returns immediately, download runs in background.

Request Body: (DownloadRequest model)

{
  "url": "https://docs.python.org/3/library/asyncio.html",
  "use_playwright": false,
  "force": false,
  "depth": 2
}
Use code with caution.
Json
url (str, required): The starting URL.

use_playwright (bool, optional, default: false): Force Playwright use for the initial URL and potentially subsequent ones (heuristic still applies).

force (bool, optional, default: false): If true, re-download files even if they exist at the target local path.

depth (int, optional, default: 2): Max recursion depth (0=start URL only, 1=start+links, etc.).

Response Body (On Success): (DownloadStatus model)

{
  "status": "started",
  "message": "Download initiated for https://docs.python.org/3/library/asyncio.html",
  "download_id": "c5e1aEXAMPLEHASHa3b2c4d5e6f7a8b9" // MD5 hash of canonical start URL
}
Use code with caution.
Json
Response Body (On Error): (HTTP 4xx for bad request, potentially 5xx internal)

2. Search Endpoint
Endpoint: POST /search

Purpose: Searches through downloaded content associated with a download_id.

Request Body: (SearchRequest model)

{
  "download_id": "c5e1aEXAMPLEHASHa3b2c4d5e6f7a8b9",
  "scan_keywords": ["event loop", "coroutine"],
  "extract_selector": "div.section > pre.highlight-python",
  "extract_keywords": ["async def", "await"]
}
Use code with caution.
Json
download_id (str, required): Identifier from a previous /download call.

scan_keywords (List[str], required): Keywords for fast scan on decoded text of relevant files (from index).

extract_selector (str, required): CSS selector for precise extraction on candidate pages.

extract_keywords (List[str], optional): Further filter selected elements based on their text content.

Response Body (On Success): (SearchResponse model)

{
  "results": [
    {
      "original_url": "https://docs.python.org/3/library/asyncio-task.html",
      "extracted_content": "async def main():\n    print('hello')\n    await asyncio.sleep(1)\n    print('world')",
      "selector_matched": "div.section > pre.highlight-python"
      // "local_path": "/app/downloads/content/docs.python.org/3/library/asyncio-task.html" // Optional debug info
    },
    // ... other results
  ]
}
Use code with caution.
Json
results: List of SearchResultItem.

original_url: The URL from which this content was originally downloaded.

extracted_content: The extracted text matching the criteria.

selector_matched: The CSS selector used.

Response Body (On Error): (HTTP 404 if download_id index not found, 5xx internal)

Example curl Usage
# 1. Initiate Download (Replace URL and potentially adjust port)
DOWNLOAD_ID=$(curl -s -X POST http://localhost:8001/download \
-H "Content-Type: application/json" \
-d '{
  "url": "https://docs.python.org/3/library/functions.html",
  "depth": 0
}' | grep -o '"download_id": *"[^"]*"' | cut -d'"' -f4)

echo "Download initiated. ID: $DOWNLOAD_ID"
echo "Waiting a bit for download to potentially finish..."
sleep 5 # Adjust wait time as needed for simple pages

# 2. Search the downloaded content (Use the captured DOWNLOAD_ID)
curl -X POST http://localhost:8001/search \
-H "Content-Type: application/json" \
-d "{
  \"download_id\": \"$DOWNLOAD_ID\",
  \"scan_keywords\": [\"print\", \"function\"],
  \"extract_selector\": \"dl.py.function > dt#print\",
  \"extract_keywords\": [\"sep\", \"end\", \"file\"]
}"
Use code with caution.
Bash
🤔 Key Concepts Explained
Mirrored Download: Saves files locally mimicking the site's path structure (hostname/path/file.html).

Index File: A crucial .jsonl file per download job (/app/downloads/index/{download_id}.jsonl) tracks every URL attempted, its canonical form, success/failure status, local path (if successful), and content MD5. This links URLs to files for searching.

Playwright Auto-Fallback: The service attempts fetching with requests. If the response looks like a shell page needing JavaScript (based on a simple heuristic like short content + common JS root element IDs), it automatically retries that single page with Playwright.

--no-clobber/force: Default prevents re-downloading if a file exists at the target path. force=true overrides this path check.

Two-Phase Search (Job-Scoped): The search operates only on files logged in the index file for the specified download_id. Phase 1 quickly scans the text content of successfully downloaded files for keywords. Phase 2 parses only the candidate files from phase 1 with BeautifulSoup, applies the CSS selector, and extracts the matching text content.

Download ID: An MD5 hash of the canonicalized starting URL for a download job. Used to link requests to the correct index file and downloaded content scope.

Concurrency: Semaphores limit simultaneous requests (~10) and Playwright (~2-4) operations to prevent overwhelming the service or the target site.

URL Canonicalization: URLs are normalized (lowercase scheme/host, remove fragments/default ports) before processing to ensure consistency in visited checks, indexing, and ID generation.

🔌 MCP Integration
Build/Push Image: docker compose build (and optionally push).

Configure MCP: Add to mcp_settings.json.

Example mcp_settings.json entry:

{
  "mcpServers": {
    "mcp-doc-retriever": { // Unique name
      "command": "docker",
      "args": [
        "run", "--rm",
        "-p", "8001:8000", // Map correct host:container port
        // Use a named volume for persistence
        "-v", "mcp_doc_downloads:/app/downloads", // Mount named volume
        "your-dockerhub-username/mcp-doc-retriever:latest" // Your image
        // Add env vars if needed, e.g., for proxy settings
      ],
      "disabled": false,
      "alwaysAllow": [
        "doc_download", // Tool for /download
        "doc_search"    // Tool for /search
      ]
    }
  }
}
Use code with caution.
Json
Volume: Using a named volume (mcp_doc_downloads in this example, managed by Docker) is generally preferred over host paths for MCP servers unless specific host access is needed. Ensure the volume name is consistent.

Tool Definition: Define doc_download and doc_search tools in Roo, mapping agent parameters to the respective API JSON request formats.

🧪 Testing
Module Tests: Optional scripts/test_*.py for direct testing during development.

API/Integration Tests: Use scripts/test_api.py or curl against the running containerized service (docker compose up -d).

📚 Documentation Standards
repo_docs/: Coders must download & reference relevant 3rd party docs here first.

File Headers: Standard headers required in src/mcp_doc_retriever/.

File Size: Aim for < 500 lines per .py file.

lessons_learned.json: Central KB for agent insights located at src/mcp_doc_retriever/docs/lessons_learned.json.