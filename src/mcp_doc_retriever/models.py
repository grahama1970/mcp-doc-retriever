"""
Pydantic models for MCP Document Retriever.

Includes models for download, search, and status requests, as well as conditional validation for DocDownloadRequest.

Third-party documentation:
- Pydantic: https://docs.pydantic.dev
- Pydantic GitHub: https://github.com/pydantic/pydantic

Sample input/output for DocDownloadRequest:

    # Git source
    DocDownloadRequest(
        source_type='git',
        repo_url='https://github.com/pydantic/pydantic',
        doc_path='docs/',
        download_id='abc123'
    )
    # Website source
    DocDownloadRequest(
        source_type='website',
        url='https://docs.pydantic.dev',
        download_id='def456',
        depth=1
    )
    # Playwright source
    DocDownloadRequest(
        source_type='playwright',
        url='https://example.com',
        download_id='ghi789',
        force=True
    )

Expected output: Valid model instance or pydantic.ValidationError if required fields are missing or invalid.

"""

from pydantic import BaseModel, Field, validator, AnyHttpUrl, model_validator
from typing import List, Optional, Literal, Dict, Any, Union
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
    """
    Model for individual search results.

    - original_url: Source URL of the result.
    - extracted_content: The matched content snippet (for backward compatibility).
    - selector_matched: The CSS selector used for extraction.
    - content_block: (Optional) Rich metadata about the matched block (code, json, or text).
    - code_block_score: (Optional) Relevance score for code block matches.
    - json_match_info: (Optional) Details about JSON match (keys/values/structure).
    - search_context: (Optional) Context of the match ("code", "json", "text").
    """
    original_url: str
    extracted_content: str
    selector_matched: str
    content_block: Optional["ContentBlock"] = None
    code_block_score: Optional[float] = None
    json_match_info: Optional[dict] = None
    search_context: Optional[str] = None

class ContentBlock(BaseModel):
    """
    Represents a block of extracted content (code, json, or text) with metadata.
    - type: "code", "json", or "text"
    - content: The extracted content string
    - language: Programming language (if applicable, e.g., "python", "json")
    - block_type: Source block type (e.g., "pre", "code", "markdown_fence")
    - start_line, end_line: Line numbers in the source document (if available)
    - source_url: URL of the source document (if available)
    - metadata: Additional metadata (e.g., parsed_json, selector, etc.)
    """
    type: Literal["code", "json", "text"]
    content: str
    language: Optional[str] = None
    block_type: Optional[str] = None
    start_line: Optional[int] = None
    end_line: Optional[int] = None
    source_url: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None

class IndexRecord(BaseModel):
    """Internal model for tracking download attempts and results in the index file.
    
    content_blocks: Optional[List[ContentBlock]]
        List of extracted content blocks (code, json, text) with metadata.
        (For backward compatibility, code_snippets is still accepted but deprecated.)
    """
    original_url: str
    canonical_url: str
    local_path: str
    content_md5: Optional[str] = None
    fetch_status: Literal[
        "success", "failed_request", "failed_robotstxt", "failed_paywall", "skipped"
    ]
    http_status: Optional[int] = None
    error_message: Optional[str] = None
    content_blocks: Optional[List[ContentBlock]] = None
    code_snippets: Optional[list[dict]] = None  # Deprecated, for backward compatibility

# --- NEW Model for Task Status ---

class TaskStatus(BaseModel):
    """Response model for querying the status of a background download task."""
    status: Literal["pending", "running", "completed", "failed"]
    message: Optional[str] = None # Optional message for progress or final status
    start_time: Optional[datetime] = None
    end_time: Optional[datetime] = None
    error_details: Optional[str] = None # Store error details if failed
class DocDownloadRequest(BaseModel):
    """
    Request model for the /download API endpoint.

    - source_type: Literal['git', 'website', 'playwright']
    - If source_type == 'git': require repo_url (valid URL) and doc_path (relative path)
    - If source_type in ['website', 'playwright']: require url (valid URL)
    - download_id: str (required)
    - depth: int (optional, for website/playwright)
    - force: bool (optional)
    """
    source_type: Literal['git', 'website', 'playwright']
    # Git fields
    repo_url: Optional[AnyHttpUrl] = None
    doc_path: Optional[str] = None
    # Website/Playwright fields
    url: Optional[AnyHttpUrl] = None
    # Common fields
    download_id: str
    depth: Optional[int] = None
    force: Optional[bool] = None

    @model_validator(mode="after")
    def check_conditional_fields(self):
        st = self.source_type
        if st == 'git':
            if not self.repo_url:
                raise ValueError("repo_url is required when source_type is 'git'")
            if not self.doc_path:
                raise ValueError("doc_path is required when source_type is 'git'")
        elif st in ('website', 'playwright'):
            if not self.url:
                raise ValueError("url is required when source_type is 'website' or 'playwright'")
        else:
            raise ValueError("Invalid source_type")
        return self

    class Config:
        schema_extra = {
            "examples": [
                {
                    "source_type": "git",
                    "repo_url": "https://github.com/pydantic/pydantic",
                    "doc_path": "docs/",
                    "download_id": "abc123"
                },
                {
                    "source_type": "website",
                    "url": "https://docs.pydantic.dev",
                    "download_id": "def456",
                    "depth": 1
                },
                {
                    "source_type": "playwright",
                    "url": "https://example.com",
                    "download_id": "ghi789",
                    "force": True
                }
            ]
        }


if __name__ == "__main__":
    from pydantic import ValidationError
    print("Testing DocDownloadRequest validation...")

    # Valid git
    try:
        req = DocDownloadRequest(
            source_type='git',
            repo_url='https://github.com/pydantic/pydantic',
            doc_path='docs/',
            download_id='abc123'
        )
        print("Valid git:", req)
    except ValidationError as e:
        print("Git validation error:", e)

    # Valid website
    try:
        req = DocDownloadRequest(
            source_type='website',
            url='https://docs.pydantic.dev',
            download_id='def456',
            depth=1
        )
        print("Valid website:", req)
    except ValidationError as e:
        print("Website validation error:", e)

    # Valid playwright
    try:
        req = DocDownloadRequest(
            source_type='playwright',
            url='https://example.com',
            download_id='ghi789',
            force=True
        )
        print("Valid playwright:", req)
    except ValidationError as e:
        print("Playwright validation error:", e)

    # Invalid: git missing repo_url
    try:
        req = DocDownloadRequest(
            source_type='git',
            doc_path='docs/',
            download_id='fail1'
        )
    except ValidationError as e:
        print("Expected error (git missing repo_url):", e)

    # Invalid: website missing url
    try:
        req = DocDownloadRequest(
            source_type='website',
            download_id='fail2'
        )
    except ValidationError as e:
        print("Expected error (website missing url):", e)

# --- Example Usage (Optional - If running models.py directly) ---
if __name__ == "__main__":
    # Example verification for ContentBlock and IndexRecord
    print("--- Model Verification Start ---")

    # ContentBlock example
    cb_code = ContentBlock(
        type="code",
        content="def foo():\n    return 42",
        language="python",
        block_type="pre",
        start_line=10,
        end_line=12,
        source_url="https://example.com/page",
        metadata={"selector": "pre.code-block"}
    )
    print("\nContentBlock (code):")
    print(cb_code.model_dump_json(indent=2) if hasattr(cb_code, "model_dump_json") else cb_code.json(indent=2))

    cb_json = ContentBlock(
        type="json",
        content='{"key": "value"}',
        language="json",
        block_type="markdown_fence",
        start_line=20,
        end_line=22,
        source_url="https://example.com/page",
        metadata={"parsed_json": {"key": "value"}}
    )
    print("\nContentBlock (json):")
    print(cb_json.model_dump_json(indent=2) if hasattr(cb_json, "model_dump_json") else cb_json.json(indent=2))

    cb_text = ContentBlock(
        type="text",
        content="This is a paragraph of text.",
        start_line=30,
        end_line=30,
        source_url="https://example.com/page"
    )
    print("\nContentBlock (text):")
    print(cb_text.model_dump_json(indent=2) if hasattr(cb_text, "model_dump_json") else cb_text.json(indent=2))

    # IndexRecord example
    idx = IndexRecord(
        original_url="https://example.com/page",
        canonical_url="https://example.com/page",
        local_path="/downloads/example.html",
        fetch_status="success",
        content_blocks=[cb_code, cb_json, cb_text]
    )
    print("\nIndexRecord with content_blocks:")
    print(idx.model_dump_json(indent=2) if hasattr(idx, "model_dump_json") else idx.json(indent=2))

    print("\n--- Model Verification End ---")
