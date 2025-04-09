"""
MCP Document Retriever FastAPI server.

- Provides `/download` endpoint to start recursive downloads.
- Provides `/search` endpoint to search downloaded content.
- Health check at `/health`.

Links:
- FastAPI: https://fastapi.tiangolo.com/
- Pydantic: https://docs.pydantic.dev/
- Uvicorn: https://www.uvicorn.org/

Sample `/download` input:
{
  "url": "https://docs.python.org/3/",
  "use_playwright": false,
  "force": false,
  "depth": 1
}

Sample `/download` output:
{
  "status": "started",
  "message": "Download initiated for https://docs.python.org/3/",
  "download_id": "uuid-string"
}

Sample `/search` input:
{
  "download_id": "uuid-string",
  "scan_keywords": ["Python"],
  "extract_selector": "title",
  "extract_keywords": null
}

Sample `/search` output:
[
  {
    "original_url": "https://docs.python.org/3/",
    "extracted_content": "Welcome to Python 3.x documentation",
    "selector_matched": "title"
  }
]
"""

from fastapi import FastAPI, BackgroundTasks, HTTPException
from .models import DownloadRequest, DownloadStatus, SearchRequest, SearchResultItem
from .searcher import perform_search
from typing import List
import os
from .downloader import start_recursive_download
from urllib.parse import urlparse, urlunparse
import uuid
import time

app = FastAPI(title="MCP Document Retriever")

@app.post("/download", response_model=DownloadStatus)
async def download(request: DownloadRequest, background_tasks: BackgroundTasks):
    # Validate URL
    parsed = urlparse(request.url)
    if not parsed.scheme or not parsed.netloc:
        # Try to add scheme if missing
        parsed = urlparse("http://" + request.url)
        if not parsed.scheme or not parsed.netloc:
            raise HTTPException(status_code=400, detail="Invalid URL format")

    # Canonicalize URL (ensure scheme, normalize)
    canonical_url = urlunparse(parsed._replace(path=parsed.path or "/"))

    # Use default depth=1 if not provided or invalid
    depth = request.depth if request.depth and request.depth > 0 else 1

    # Generate unique download_id
    download_id = str(uuid.uuid4())

    # Start background download
    background_tasks.add_task(
        start_recursive_download,
        canonical_url,
        depth,
        request.force,
        download_id
    )

    return DownloadStatus(
        status="started",
        message=f"Download initiated for {canonical_url}",
        download_id=download_id
    )

@app.post("/search", response_model=List[SearchResultItem])
async def search(request: SearchRequest):
    index_path = f"/app/downloads/index/{request.download_id}.jsonl"
    if not os.path.exists(index_path):
        raise HTTPException(status_code=404, detail="Index file not found")
    results = perform_search(
        request.download_id,
        request.scan_keywords,
        request.extract_selector,
        request.extract_keywords
    )
    return results

@app.get("/health")
async def health():
    """Health check endpoint"""
    return {"status": "healthy"}

if __name__ == "__main__":
    import uvicorn
    import uuid as uuid_lib
    import time
    import os
    import asyncio
    from .downloader import start_recursive_download
    from .searcher import perform_search

    async def minimal_test():
        # Minimal real-world usage test
        test_url = "https://docs.python.org/3/"
        test_depth = 1
        test_force = False
        test_download_id = str(uuid_lib.uuid4())

        print(f"Starting minimal real-world download test for {test_url} with download_id {test_download_id}")
        await start_recursive_download(test_url, test_depth, test_force, test_download_id, base_dir="downloads")

        # Wait a bit for download to complete (adjust as needed)
        print("Waiting 10 seconds for download to complete...")
        await asyncio.sleep(10)

        # Verify content saved
        index_path = f"downloads/index/{test_download_id}.jsonl"
        if os.path.exists(index_path):
            print(f"Download index file created: {index_path}")
        else:
            print(f"ERROR: Download index file not found: {index_path}")

        # Perform a simple search
        try:
            results = perform_search(
                test_download_id,
                ["Python"],
                "title",
                None
            )
            print(f"Search results ({len(results)} hits):")
            for r in results:
                print(r)
        except Exception as e:
            print(f"Search failed: {e}")

    # Run the async minimal test
    asyncio.run(minimal_test())