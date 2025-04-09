"""
Pydantic models for MCP Document Retriever.

Defines:
- DownloadRequest
- DownloadStatus
- SearchRequest
- SearchResultItem
- SearchResponse
- IndexRecord

Links:
- Pydantic: https://docs.pydantic.dev/
- Python typing: https://docs.python.org/3/library/typing.html

Sample DownloadRequest input:
{
  "url": "https://docs.python.org/3/",
  "use_playwright": false,
  "force": false,
  "depth": 1
}

Sample DownloadStatus output:
{
  "status": "started",
  "message": "Download initiated for https://docs.python.org/3/",
  "download_id": "uuid-string"
}

Sample SearchRequest input:
{
  "download_id": "uuid-string",
  "scan_keywords": ["Python"],
  "extract_selector": "title",
  "extract_keywords": null
}

Sample SearchResponse output:
{
  "results": [
    {
      "original_url": "https://docs.python.org/3/",
      "extracted_content": "Welcome to Python 3.x documentation",
      "selector_matched": "title"
    }
  ]
}
"""

from pydantic import BaseModel
from typing import List, Optional, Literal

class DownloadRequest(BaseModel):
    """Request model for initiating a download"""
    url: str
    force: bool = False
    depth: int = 1

class DownloadStatus(BaseModel):
    """Response model for download status"""
    status: Literal["started", "failed"]
    message: str
    download_id: str

class SearchRequest(BaseModel):
    """Request model for searching downloaded content"""
    download_id: str
    scan_keywords: List[str]
    extract_selector: str
    extract_keywords: Optional[List[str]] = None

class SearchResultItem(BaseModel):
    """Model for individual search results"""
    original_url: str
    extracted_content: str
    selector_matched: str

class SearchResponse(BaseModel):
    """Response model for search results"""
    results: List[SearchResultItem]

class IndexRecord(BaseModel):
    """Internal model for tracking download attempts"""
    original_url: str
    canonical_url: str
    local_path: str
    content_md5: Optional[str] = None
    fetch_status: Literal["success", "failed_request", "failed_robotstxt", "failed_paywall"]
    http_status: Optional[int] = None
    error_message: Optional[str] = None

if __name__ == "__main__":
    # Minimal usage verification
    req = DownloadRequest(url="https://docs.python.org/3/")
    print("DownloadRequest:", req.json())

    status = DownloadStatus(status="started", message="Download initiated", download_id="uuid-string")
    print("DownloadStatus:", status.json())

    search_req = SearchRequest(
        download_id="uuid-string",
        scan_keywords=["Python"],
        extract_selector="title",
        extract_keywords=None
    )
    print("SearchRequest:", search_req.json())

    search_resp = SearchResponse(results=[
        SearchResultItem(
            original_url="https://docs.python.org/3/",
            extracted_content="Welcome to Python 3.x documentation",
            selector_matched="title"
        )
    ])
    print("SearchResponse:", search_resp.json())