# src/mcp_doc_retriever/context7/models.py

from typing import List, Optional, Dict, Any
from pydantic import BaseModel, Field


class ProcessRepoRequest(BaseModel):
    repo_urls: List[str] = Field(..., description="List of repository URLs to process.")
    output_dir: str = Field(
        "./data", description="Output directory for processed data."
    )
    exclude_patterns: Optional[List[str]] = Field(
        None, description="Optional glob-style patterns to exclude."
    )


class SetupDbRequest(BaseModel):
    host: str = Field("http://localhost:8529", description="ArangoDB host address.")
    db_name: str = Field("mcp_docs", description="Name of the ArangoDB database.")
    truncate: bool = Field(
        False, description="If True, truncate existing collections before setup."
    )
    seed_file: Optional[str] = Field(
        None, description="Optional path to a JSON seed file."
    )
    force: bool = Field(
        False, description="Bypass confirmation for truncate operation."
    )
    skip_setup: bool = Field(
        False, description="Skip graph, view, index creation (truncate/seed only)."
    )


class Message(BaseModel):
    message: str = Field(
        ..., description="Descriptive message about the operation status."
    )


class ExtractedCode(BaseModel):
    """
    Represents a code block and its metadata extracted from a document.
    """

    file_path: str = Field(..., description="Path to the source file.")
    repo_link: str = Field(..., description="URL of the repository.")
    extraction_date: str = Field(
        ..., description="Date and time of extraction (ISO format)."
    )
    code_line_span: tuple[int, int] = Field(
        ..., description="Start and end line numbers of the code block."
    )
    description_line_span: tuple[int, int] = Field(
        ..., description="Start and end line numbers of the description."
    )
    code: str = Field(..., description="The extracted code block.")
    code_type: str = Field(..., description="Programming language or type of the code.")
    description: str = Field(..., description="Description of the code block.")
    code_token_count: int = Field(
        ..., description="Number of tokens in the code block."
    )
    description_token_count: int = Field(
        ..., description="Number of tokens in the description."
    )
    embedding_code: Optional[List[float]] = Field(
        None, description="Embedding vector for the code block."
    )
    embedding_description: Optional[List[float]] = Field(
        None, description="Embedding vector for the description."
    )
    code_metadata: Dict[str, Any] = Field(
        ...,
        description="Metadata extracted from the code (e.g., function names, classes).",
    )
    section_id: str = Field(..., description="Unique ID for the section or code block.")
    section_path: List[str] = Field(
        ..., description="Hierarchical path of section titles."
    )
    section_hash_path: List[str] = Field(
        ..., description="Hierarchical path of section hashes."
    )
