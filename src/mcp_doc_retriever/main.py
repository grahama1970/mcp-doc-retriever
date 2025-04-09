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
        download_id,
        use_playwright=request.use_playwright # Pass the flag
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