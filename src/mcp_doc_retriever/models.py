"""
Pydantic models for MCP Document Retriever.

Defines data structures for:
- Indexing downloaded content (`IndexRecord`, `ContentBlock`).
- API request validation (`DocDownloadRequest`).
- Search functionality (`SearchRequest`, `SearchResultItem`).
- Task status tracking (`TaskStatus`).
- Code extraction models (`ExtractedBlock`).
- Potentially older/alternative request models (`DownloadRequest`, `DownloadStatus`).

Third-party documentation:
- Pydantic: https://docs.pydantic.dev
- Pydantic GitHub: https://github.com/pydantic/pydantic

Sample Input/Output:

Input (Python Code):
  record = IndexRecord(
      original_url="http://example.com/page",
      canonical_url="http://example.com/page",
      local_path="content/example.com/http_example.com_page-abcdef12.html",
      fetch_status="success",
      content_hash="md5...",
      last_fetched="2024-01-01T12:00:00Z"
  )
  print(record.model_dump_json(indent=2))

Output (JSON String):
  {
    "original_url": "http://example.com/page",
    "canonical_url": "http://example.com/page",
    "local_path": "content/example.com/http_example.com_page-abcdef12.html",
    "fetch_status": "success",
    "content_hash": "md5...",
    "last_fetched": "2024-01-01T12:00:00Z",
    "http_status": null,
    "content_type": null,
    "content_blocks": null,
    "error_message": null,
    "redirect_url": null
  }
"""

from pydantic import (
    BaseModel, 
    Field, 
    field_validator, 
    AnyHttpUrl, 
    model_validator, 
    ConfigDict
)
from typing import List, Optional, Literal, Dict, Any
from datetime import datetime

# ==============================================================================
# Code Extraction Models
# ==============================================================================

class ExtractedBlock(BaseModel):
    """
    Represents a block of code extracted from source files using tree-sitter.
    Used to track function/method/class definitions before converting to ContentBlock.
    
    Attributes:
        type: The node type from tree-sitter (e.g., 'function_definition', 'class_definition')
        name: The identifier name of the code block (e.g., function or class name)
        content: The full source code content of the block
        start_line: Line number where the block starts (1-based)
        end_line: Line number where the block ends (1-based)
    """
    type: str = Field(description="Tree-sitter node type (e.g., 'function_definition')")
    name: Optional[str] = Field(None, description="Identifier name (e.g., function/class name)")
    content: str = Field(description="Full source code of the block")
    start_line: int = Field(gt=0, description="Starting line number (1-based)")
    end_line: int = Field(gt=0, description="Ending line number (1-based)")
    
    @field_validator('end_line')
    def end_line_after_start(cls, v, info):
        if v < info.data['start_line']:
            raise ValueError('end_line must be >= start_line')
        return v

# ==============================================================================
# Models used by the Downloader Workflow for Indexing
# ==============================================================================


# Model moved to src/mcp_doc_retriever/searcher/helpers.py


# Model moved to src/mcp_doc_retriever/downloader/web_downloader.py


# ==============================================================================
# Models potentially used by an API Layer (e.g., FastAPI)
# ==============================================================================


# Model moved to src/mcp_doc_retriever/main.py


# Model moved to src/mcp_doc_retriever/main.py


# ==============================================================================
# Models potentially used for Search Functionality
# ==============================================================================


# Model moved to src/mcp_doc_retriever/searcher/searcher.py




# Model moved to src/mcp_doc_retriever/main.py

# Model moved to src/mcp_doc_retriever/searcher/searcher.py
# Note: This model uses ContentBlock, which is now in searcher.helpers


# ==============================================================================
# Older / Alternative Models (Potentially Deprecated or for specific use cases)
# ==============================================================================


class DownloadRequest(BaseModel):
    """
    Older/Alternative request model for initiating a download.
    Consider using DocDownloadRequest for API interactions. Not used by CLI.

    Attributes:
        url: The URL to download. Stricter validation via AnyHttpUrl is possible.
        force: Overwrite existing files.
        depth: Max crawl depth.
        use_playwright: Flag to force Playwright usage.
        timeout: Request timeout.
        max_file_size: Max size for downloaded files.
    """

    url: str  # Use AnyHttpUrl for stricter validation if needed for this specific model's use case
    force: bool = False
    depth: int = Field(default=1, ge=0)
    use_playwright: Optional[bool] = False
    timeout: Optional[int] = Field(None, gt=0)  # Ensure positive if provided
    max_file_size: Optional[int] = Field(
        None, alias="max_size", gt=0
    )  # Ensure positive if provided

    # Note: Pydantic v2 handles gt=0 validation directly in Field


class DownloadStatus(BaseModel):
    """
    Older/Alternative response model for download status after *initiating* a request.
    Consider using TaskStatus for querying background task progress/results.

    Attributes:
        status: Outcome of the initial request validation/start attempt.
        message: Human-readable message.
        download_id: The ID assigned if the download started successfully.
    """

    status: Literal["started", "failed_validation"]
    message: str
    download_id: Optional[str] = None  # download_id is None if validation fails early


# ------------------------------------------------------------------
# New: Define AdvancedSearchOptions so that it can be imported for testing.
# ------------------------------------------------------------------
class AdvancedSearchOptions:
    scan_keywords: List[str]
    extract_keywords: Optional[List[str]] = None
    search_code_blocks: bool = True
    search_json: bool = True
    code_block_priority: bool = False
    json_match_mode: str = "keys"


# ==============================================================================
# Testing / Verification Block
# ==============================================================================

# Keep the __main__ block for validating models when running this file directly
if __name__ == "__main__":
    from pydantic import ValidationError

    print("--- Model Verification Start ---")
    all_models_passed = True # Flag to track overall success

    # Test ExtractedBlock validation
    print("\nTesting ExtractedBlock validation...")
    try:
        valid_block = ExtractedBlock(
            type="function_definition",
            name="test_func",
            content="def test_func(): pass",
            start_line=1,
            end_line=1
        )
        print("OK: Valid ExtractedBlock")
    except ValidationError as e:
        print("FAIL: Valid ExtractedBlock:", e)
        all_models_passed = False

    try:
        ExtractedBlock(
            type="function_definition",
            content="def test_func(): pass",
            start_line=2,
            end_line=1  # Invalid: end before start
        )
        print("FAIL: Should reject end_line < start_line")
        all_models_passed = False
    except ValidationError:
        print("OK: Expected error (end_line < start_line)")

    # Tests for DocDownloadRequest removed as the model was moved to main.py

    # Tests for IndexRecord removed as the model was moved to downloader/web_downloader.py

    print("\n------------------------------------")
    if all_models_passed:
        print("✓ All Model verification tests passed successfully.")
    else:
        print("✗ Some Model verification tests failed.")
    print("------------------------------------")

    print("\n--- Model Verification End ---")
