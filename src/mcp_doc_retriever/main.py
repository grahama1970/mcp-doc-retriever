from fastapi import FastAPI, BackgroundTasks, HTTPException
from .models import DownloadRequest, DownloadStatus, SearchRequest, SearchResponse
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

@app.post("/search", response_model=SearchResponse)
async def search(request: SearchRequest):
    """Placeholder endpoint for searching downloaded content"""
    return SearchResponse(results=[])

@app.get("/health")
async def health():
    """Health check endpoint"""
    return {"status": "healthy"}