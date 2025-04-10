"""
Pydantic models for MCP Document Retriever.

... (existing docstring) ...
"""

from pydantic import BaseModel, Field, validator, AnyHttpUrl # Added AnyHttpUrl if needed
from typing import List, Optional, Literal, Dict # Added Dict
from datetime import datetime, timedelta # Added datetime, timedelta for example

# --- Existing Models ---

class DownloadRequest(BaseModel):
    """Request model for initiating a download"""
    # Ensure url validation if desired (optional)
    # url: AnyHttpUrl # Use this for stricter validation
    url: str
    force: bool = False
    depth: int = Field(default=1, ge=0)
    use_playwright: Optional[bool] = False
    timeout: Optional[int] = None
    max_file_size: Optional[int] = Field(
        default=None, alias="max_size"
    )

    @validator("timeout", "max_file_size")
    def check_positive_optional(cls, value):
        if value is not None and value <= 0:
            raise ValueError("Value must be positive if provided")
        return value

class DownloadStatus(BaseModel):
    """Response model for download status AFTER initiation"""
    status: Literal["started", "failed_validation"] # Renamed from "failed" which is ambiguous now
    message: str
    download_id: Optional[str] = None # download_id is None if validation fails early


class SearchRequest(BaseModel):
    """Request model for searching downloaded content"""
    download_id: str
    scan_keywords: List[str] = Field(..., min_items=1)
    extract_selector: str
    extract_keywords: Optional[List[str]] = None

    @validator("extract_selector")
    def check_selector_non_empty(cls, value):
        if not value or not value.strip():
            raise ValueError("extract_selector cannot be empty")
        return value

class SearchResultItem(BaseModel):
    """Model for individual search results"""
    original_url: str
    extracted_content: str
    selector_matched: str

class IndexRecord(BaseModel):
    """Internal model for tracking download attempts and results in the index file."""
    original_url: str
    canonical_url: str
    local_path: str
    content_md5: Optional[str] = None
    fetch_status: Literal[
        "success", "failed_request", "failed_robotstxt", "failed_paywall", "skipped"
    ]
    http_status: Optional[int] = None
    error_message: Optional[str] = None

# --- NEW Model for Task Status ---

class TaskStatus(BaseModel):
    """Response model for querying the status of a background download task."""
    status: Literal["pending", "running", "completed", "failed"]
    message: Optional[str] = None # Optional message for progress or final status
    start_time: Optional[datetime] = None
    end_time: Optional[datetime] = None
    error_details: Optional[str] = None # Store error details if failed

# --- Example Usage (Optional - If running models.py directly) ---
if __name__ == "__main__":
    # Example verification for existing models (assuming they were here)
    print("--- Model Verification Start ---")
    # Add verification for DownloadRequest, DownloadStatus, etc. if needed

    print("\nTaskStatus (Running):")
    task_running = TaskStatus(
        status="running",
        message="Processing URL 10/50",
        start_time=datetime.now()
    )
    try:
        print(task_running.model_dump_json(indent=2))
    except AttributeError:
        print(task_running.json(indent=2)) # V1 fallback

    print("\nTaskStatus (Completed):")
    task_completed = TaskStatus(
        status="completed",
        message="Download finished successfully.",
        start_time=datetime.now() - timedelta(minutes=5), # Example past time
        end_time=datetime.now()
    )
    try:
        print(task_completed.model_dump_json(indent=2))
    except AttributeError:
        print(task_completed.json(indent=2))

    print("\nTaskStatus (Failed):")
    task_failed = TaskStatus(
        status="failed",
        message="Download failed critically.",
        start_time=datetime.now() - timedelta(minutes=2),
        end_time=datetime.now(),
        error_details="Timeout connecting to host."
    )
    try:
        print(task_failed.model_dump_json(indent=2, exclude_none=True)) # Exclude None example
    except AttributeError:
        print(task_failed.json(indent=2))

    print("\n--- Model Verification End ---")
