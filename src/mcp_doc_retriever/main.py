from fastapi import FastAPI
from .models import DownloadRequest, DownloadStatus, SearchRequest, SearchResponse

app = FastAPI(title="MCP Document Retriever")

@app.post("/download", response_model=DownloadStatus)
async def download(request: DownloadRequest):
    """Placeholder endpoint for initiating downloads"""
    return DownloadStatus(
        status="started",
        message=f"Download initiated for {request.url}",
        download_id="placeholder_id"
    )

@app.post("/search", response_model=SearchResponse)
async def search(request: SearchRequest):
    """Placeholder endpoint for searching downloaded content"""
    return SearchResponse(results=[])

@app.get("/health")
async def health():
    """Health check endpoint"""
    return {"status": "healthy"}