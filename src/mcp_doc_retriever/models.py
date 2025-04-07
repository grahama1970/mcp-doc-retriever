from pydantic import BaseModel
from typing import List, Optional, Literal

class DownloadRequest(BaseModel):
    """Request model for initiating a download"""
    url: str
    use_playwright: bool = False
    force: bool = False
    depth: int = 2

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