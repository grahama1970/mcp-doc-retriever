"""
Orchestration Script for MCP Document Retrieval Pipeline.
"""

import asyncio
import logging
import tempfile
import nbformat
from docutils.core import publish_string
from pathlib import Path
from typing import Dict, List, Optional, Union, Any, TypedDict
from tabulate import tabulate
import json

# Import core functionality using absolute imports
from mcp_doc_retriever.downloader import web_downloader, git_downloader
from mcp_doc_retriever.searcher import basic_extractor
from mcp_doc_retriever.searcher.markdown_extractor import (
    extract_content_blocks_with_markdown_it,
)
from mcp_doc_retriever.searcher.basic_extractor import extract_text_with_selector
from mcp_doc_retriever.context7.sparse_checkout import sparse_checkout
from mcp_doc_retriever.context7.file_discovery import find_relevant_files

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Constants
DEFAULT_BASE_PATH = Path("/app/downloads")
EXCLUDED_FILE_PATTERNS = {
    "*.png",
    "*.jpg",
    "*.jpeg",
    "*.gif",
    "*.pdf",
    "*.zip",
    "*.gz",
    "*.tar",
    "*.exe",
    "*.dll",
    "*.so",
    "*.o",
    "*.class",
    "*.jar",
    "*.pyc",
    "*.pdb",
    "*.pptx",
    "*.mp4",
    "*.webp",
    "*.htm",
    "*.html",  # ignoring html for now
}


class Metrics(TypedDict):
    files_processed: int
    documents_extracted: int
    search_results_found: int
    errors_encountered: int


class PipelineResult(TypedDict):
    status: str
    documents_processed: int
    search_results: List[Dict[str, Any]]
    metrics: Metrics


class PipelineOrchestrator:
    """Coordinates the document processing pipeline steps."""

    def __init__(self, base_path: Optional[Path] = None):
        """Initialize the orchestrator with configuration."""
        self.base_path: Path = base_path or DEFAULT_BASE_PATH
        self._results: List[Dict[str, Any]] = []  # Initialize in-memory storage
        self.metrics: Metrics = {
            "files_processed": 0,
            "documents_extracted": 0,
            "search_results_found": 0,
            "errors_encountered": 0,
        }
        logger.info("Pipeline orchestrator initialized")

    async def run_pipeline(
        self,
        source: str,
        source_type: Optional[str] = None,
        search_query: Optional[str] = None,
        **kwargs: Any,
    ) -> PipelineResult:
        """
        Execute the complete document processing pipeline.
        """
        try:
            # Step 1: Determine source type if not provided
            if not source_type:
                source_type = self._determine_source_type(source)
            logger.info(f"Processing source: {source} (type: {source_type})")

            # Step 2: Download/retrieve content
            download_result = await self._download_content(source, source_type)
            if not download_result.get("success"):
                self.metrics["errors_encountered"] += 1
                return {
                    "status": "error",
                    "documents_processed": 0,
                    "search_results": [],
                    "message": "Download failed",
                    "metrics": self.metrics,
                }

            # Step 2.5: Find relevant files after successful download
            content_path = download_result.get("content_path")
            if not content_path:
                self.metrics["errors_encountered"] += 1
                return {
                    "status": "error",
                    "documents_processed": 0,
                    "search_results": [],
                    "message": "Download succeeded but content path is missing",
                    "metrics": self.metrics,
                }

            try:
                relevant_files = find_relevant_files(
                    repo_dir=str(content_path), exclude_patterns=EXCLUDED_FILE_PATTERNS
                )
                logger.info(
                    f"Found {len(relevant_files)} relevant files after filtering."
                )
                self.metrics["files_processed"] = len(relevant_files)

                # Store the relevant files in the download result for processing
                download_result["relevant_files"] = relevant_files
            except Exception as e:
                logger.error(f"File discovery failed: {e}")
                self.metrics["errors_encountered"] += 1
                return {
                    "status": "error",
                    "documents_processed": 0,
                    "search_results": [],
                    "message": f"File discovery failed: {e}",
                    "metrics": self.metrics,
                }

            # Step 3: Extract and process content
            content_path = Path(
                download_result.get("content_path", str(self.base_path / "content"))
            )
            processed_content = await self._process_content(
                content_path, download_result.get("relevant_files", [])
            )
            self.metrics["documents_extracted"] = len(processed_content)

            # Step 4: Store results in memory (file-based storage can be added later)
            self._store_results(processed_content)

            # Step 5: Execute search if query provided
            search_results: List[Dict[str, Any]] = []
            if search_query:
                search_results = await self._execute_search(search_query)
                self.metrics["search_results_found"] = len(search_results)

            return {
                "status": "success",
                "documents_processed": len(processed_content),
                "search_results": search_results,
                "metrics": self.metrics,
            }

        except Exception as e:
            logger.exception(
                f"Pipeline execution failed: {e}"
            )  # Log exception with traceback
            self.metrics["errors_encountered"] += 1
            return {
                "status": "error",
                "documents_processed": 0,
                "search_results": [],
                "message": str(e),
                "metrics": self.metrics,
            }

    def _determine_source_type(self, source: str) -> str:
        """Determine the type of source (web, git, etc.)."""
        if source.startswith(("http://", "https://")):
            return "web"
        if source.endswith(".git") or "github.com" in source:
            return "git"
        return "local" if Path(source).exists() else "web"  # Default to web

    async def _download_content(self, source: str, source_type: str) -> Dict[str, Any]:
        """Download or retrieve content from the source."""
        logger.info(f"Downloading content from {source} (type: {source_type})")
        try:
            if source_type == "git":
                temp_dir = Path(tempfile.mkdtemp(dir=self.base_path))
                logger.info(f"Created temporary directory for Git checkout: {temp_dir}")
                patterns = ["*.md", "*.mdx", "*.rst", "*.txt", "*.ipynb"]
                success = sparse_checkout(
                    repo_url=source,
                    output_dir=str(temp_dir),  # Convert Path to string
                    patterns=patterns,
                )

                if success:
                    return {"success": True, "content_path": str(temp_dir)}
                else:
                    logger.error("Sparse checkout failed")
                    self.metrics["errors_encountered"] += 1
                    return {"success": False, "error": "Sparse checkout failed"}

            elif source_type == "web":
                content_dir = self.base_path / "content"
                content_dir.mkdir(exist_ok=True)
                return {"success": True, "content_path": str(content_dir)}

            elif source_type == "local":
                if not Path(source).exists():
                    self.metrics["errors_encountered"] += 1
                    error_message = f"Local path does not exist: {source}"
                    logger.error(error_message)
                    return {"success": False, "error": error_message}
                return {"success": True, "content_path": source}

            else:
                error_message = f"Unsupported source type: {source_type}"
                logger.error(error_message)
                self.metrics["errors_encountered"] += 1
                return {"success": False, "error": error_message}

        except Exception as e:
            logger.exception(f"Download failed: {e}")
            self.metrics["errors_encountered"] += 1
            return {"success": False, "error": str(e)}

    async def _process_content(
        self, content_path: Path, relevant_files: List[str]
    ) -> List[Dict[str, Any]]:
        """Extract and process content from downloaded files based on file type."""
        logger.info(f"Processing {len(relevant_files)} files from {content_path}")
        processed_data: List[Dict[str, Any]] = []

        for file_rel_path in relevant_files:
            full_file_path = content_path / file_rel_path
            try:
                processed_item = await self._process_file(full_file_path, file_rel_path)
                if processed_item:
                    processed_data.append(processed_item)
            except Exception as file_error:
                logger.error(f"Error processing file {full_file_path}: {file_error}")
                self.metrics["errors_encountered"] += 1

        return processed_data

    async def _process_file(
        self, full_file_path: Path, file_rel_path: str
    ) -> Optional[Dict[str, Any]]:
        """Process a single file based on its suffix."""
        file_suffix = full_file_path.suffix.lower()

        try:
            if file_suffix in [".md", ".mdx"]:
                content = full_file_path.read_text(encoding="utf-8")
                extracted_items = extract_content_blocks_with_markdown_it(
                    content, str(full_file_path)
                )
                for item in extracted_items:
                    return self._create_content_map(
                        file_rel_path,
                        item.content,
                        item.type,
                        item.metadata,
                        item.language,
                    )

            elif file_suffix == ".rst":
                content = full_file_path.read_text(encoding="utf-8")
                return self._create_content_map(
                    file_rel_path, content, "rst", {"source": "rst"}
                )

            elif file_suffix == ".txt":
                content = full_file_path.read_text(encoding="utf-8")
                return self._create_content_map(
                    file_rel_path, content, "text", {"source": "txt"}
                )

            elif file_suffix == ".ipynb":
                content = full_file_path.read_text(encoding="utf-8")
                notebook = nbformat.reads(content, as_version=4)
                for cell in notebook.cells:
                    cell_type = cell.cell_type
                    if cell_type == "markdown":
                        return self._create_content_map(
                            file_rel_path, cell.source, "markdown", {"source": "ipynb"}
                        )
                    elif cell_type == "code":
                        return self._create_content_map(
                            file_rel_path,
                            cell.source,
                            "code",
                            {"source": "ipynb", "language": "python"},
                        )
                return None

            else:
                logger.warning(
                    f"Unsupported file type skipped: {file_suffix} in {full_file_path}"
                )
                self.metrics["errors_encountered"] += 1
                return None

        except Exception as e:
            logger.error(f"Error processing file {full_file_path}: {e}")
            self.metrics["errors_encountered"] += 1
            return None

    def _create_content_map(
        self,
        file_path: str,
        content: str,
        content_type: str,
        metadata: Dict[str, Any],
        language: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Helper function to create a content map."""
        content_map: Dict[str, Any] = {
            "file_path": file_path,
            "content": content,
            "type": content_type,
            "metadata": metadata,
        }
        if language:
            content_map["language"] = language
        return content_map

    def _store_results(self, processed_content: List[Dict[str, Any]]) -> None:
        """Store results in memory (temporary implementation)."""
        logger.info(f"Storing {len(processed_content)} processed documents")
        self._results = processed_content  # Simple in-memory storage

    async def _execute_search(self, query: str) -> List[Dict[str, Any]]:
        """Execute search query on processed content."""
        logger.info(f"Executing search query: {query}")
        try:
            matches = []
            query_lower = query.lower()
            for item in self._results:
                content = item.get("content", "").lower()
                if query_lower in content:
                    relevance = (
                        float(len(query_lower)) / len(content) if content else 0.0
                    )
                    matches.append(
                        {
                            "content": item["content"],
                            "type": item.get("type", "text"),
                            "file_path": item.get("file_path", ""),
                            "relevance": relevance,
                            "metadata": item.get("metadata", {}),
                        }
                    )
            matches.sort(key=lambda x: x["relevance"], reverse=True)
            return matches
        except Exception as e:
            logger.error(f"Search failed: {e}")
            self.metrics["errors_encountered"] += 1
            return []


def orchestrate_pipeline(
    source: str,
    source_type: Optional[str] = None,
    search_query: Optional[str] = None,
    **kwargs: Any,
) -> PipelineResult:
    """Convenience function to run the pipeline."""
    orchestrator = PipelineOrchestrator()
    return asyncio.run(
        orchestrator.run_pipeline(source, source_type, search_query, **kwargs)
    )


if __name__ == "__main__":
    # Set up a local downloads directory for testing
    test_downloads = Path(__file__).parent.parent.parent.parent / "downloads"
    test_downloads.mkdir(exist_ok=True)

    # Example usage with both web and git sources
    # Create test files of different types
    test_dir = test_downloads / "content"
    test_dir.mkdir(exist_ok=True)

    # Test Markdown file
    md_content = """
    # Test Header

    This is a test paragraph.

    ```python
    def test_func():
        print("Hello")
    ```
    """
    (test_dir / "test.md").write_text(md_content)

    # Test RST file
    rst_content = """
    Test Title
    ==========

    This is a reStructuredText test.

    .. code-block:: python

       def rst_func():
           print("RST")
    """
    (test_dir / "test.rst").write_text(rst_content)

    # Test Jupyter notebook
    nb_content = {
        "cells": [
            {
                "cell_type": "markdown",
                "source": "## Notebook Test\n\nMarkdown cell.",
                "metadata": {},
            },
            {
                "cell_type": "code",
                "source": "print('Notebook code')",
                "outputs": [],
                "metadata": {"language": "python"},
            },
        ],
        "metadata": {},
        "nbformat": 4,
        "nbformat_minor": 4,
    }

    (test_dir / "test.ipynb").write_text(json.dumps(nb_content))

    # Test cases with actual files
    test_cases = [
        {
            "source": str(test_dir),
            "source_type": "local",
            "relevant_files": ["test.md", "test.rst", "test.ipynb"],
            "search_query": "test",
        }
    ]

    # Collect metrics for all test cases
    metrics_table = []
    for test in test_cases:
        print(f"\nTesting with {test['source_type']} source...")
        # Create orchestrator with test downloads directory
        orchestrator = PipelineOrchestrator(base_path=test_downloads)
        result = asyncio.run(
            orchestrator.run_pipeline(
                source=test["source"],
                source_type=test["source_type"],
                search_query=test["search_query"],
            )
        )
        print(f"Pipeline execution result: {result}")

        # Add metrics to table
        metrics = result.get("metrics", {})
        metrics_table.append(
            [
                test["source_type"],
                metrics.get("files_processed", 0),
                metrics.get("documents_extracted", 0),
                metrics.get("search_results_found", 0),
                metrics.get("errors_encountered", 0),
                result.get("status", "unknown"),
            ]
        )

    # Display metrics table
    headers = [
        "Source Type",
        "Files Processed",
        "Docs Extracted",
        "Search Results",
        "Errors",
        "Status",
    ]
    print("\nSuccess Metrics:")
    print(tabulate(metrics_table, headers=headers, tablefmt="grid"))
