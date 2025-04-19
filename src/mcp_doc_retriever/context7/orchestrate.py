"""
Orchestration Script for MCP Document Retrieval Pipeline.

This module serves as the main orchestration point for the document retrieval and processing pipeline.
It coordinates the workflow between downloading documents, processing content, storing data,
and enabling search functionality.

Sample Usage:
    orchestrate_pipeline(source_url='https://example.com/docs',
                        source_type='web',
                        search_query='api authentication')

Expected Output:
    {
        'status': 'success',
        'documents_processed': 5,
        'search_results': [
            {'title': 'Auth Guide', 'relevance': 0.95, 'content': '...'},
            ...
        ]
    }
"""
import asyncio
import logging
import tempfile
import nbformat
from docutils.core import publish_string
from pathlib import Path
from typing import Dict, List, Optional, Union, Any

# Import core functionality using absolute imports
from mcp_doc_retriever.downloader import web_downloader, git_downloader
from mcp_doc_retriever.searcher import basic_extractor
from mcp_doc_retriever.searcher.markdown_extractor import extract_content_blocks_with_markdown_it
from mcp_doc_retriever.searcher.basic_extractor import extract_text_with_selector
from mcp_doc_retriever.context7.sparse_checkout import sparse_checkout
from mcp_doc_retriever.context7.file_discovery import find_relevant_files

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class PipelineOrchestrator:
    """Coordinates the document processing pipeline steps."""
    
    def __init__(self, base_path: Optional[Path] = None):
        """Initialize the orchestrator with configuration."""
        self.base_path = base_path or Path("/app/downloads")
        self._results = []  # Initialize in-memory storage
        logger.info("Pipeline orchestrator initialized")

    async def run_pipeline(
        self,
        source: str,
        source_type: Optional[str] = None,
        search_query: Optional[str] = None,
        **kwargs: Any
    ) -> Dict[str, Any]:
        """
        Execute the complete document processing pipeline.

        Args:
            source: URL or path to the documentation source
            source_type: Type of source ('web', 'git', etc.) - auto-detected if not provided
            search_query: Optional search query to execute after processing
            **kwargs: Additional parameters for specific pipeline stages

        Returns:
            Dict containing pipeline execution results and any search results
        """
        try:
            # Step 1: Determine source type if not provided
            if not source_type:
                source_type = self._determine_source_type(source)
            logger.info(f"Processing {source_type} source: {source}")

            # Step 2: Download/retrieve content
            download_result = await self._download_content(
                source, 
                source_type or "web"  # Default to web if still None
            )
            if not download_result.get('success'):
                return {'status': 'error', 'message': 'Download failed'}

            # Step 2.5: Find relevant files after successful download
            exclude_patterns = [
                '*.png', '*.jpg', '*.jpeg', '*.gif', '*.pdf',
                '*.zip', '*.gz', '*.tar', '*.exe', '*.dll',
                '*.so', '*.o', '*.class', '*.jar', '*.pyc'
            ]
            content_path = download_result.get('content_path')
            if not content_path:
                return {'status': 'error', 'message': 'Download succeeded but content path is missing'}

            try:
                relevant_files = find_relevant_files(
                    repo_dir=str(content_path),
                    exclude_patterns=exclude_patterns
                )
                logger.info(f"Found {len(relevant_files)} relevant files after filtering.")

                # Store the relevant files in the download result for processing
                download_result['relevant_files'] = relevant_files
            except Exception as e:
                logger.error(f"File discovery failed: {str(e)}")
                return {'status': 'error', 'message': f'File discovery failed: {str(e)}'}

            # Step 3: Extract and process content
            content_path = Path(download_result.get('content_path', self.base_path / "content"))
            processed_content = await self._process_content(
                content_path,
                download_result.get('relevant_files', [])
            )

            # Step 4: Store results in memory (file-based storage can be added later)
            self._store_results(processed_content)

            # Step 5: Execute search if query provided
            search_results: List[Dict[str, Any]] = []
            if search_query:
                search_results = await self._execute_search(search_query)

            return {
                'status': 'success',
                'documents_processed': len(processed_content),
                'search_results': search_results
            }

        except Exception as e:
            logger.error(f"Pipeline execution failed: {str(e)}")
            return {'status': 'error', 'message': str(e)}

    def _determine_source_type(self, source: str) -> str:
        """Determine the type of source (web, git, etc.)."""
        if source.startswith(('http://', 'https://')):
            return 'web'
        if source.endswith('.git') or 'github.com' in source:
            return 'git'
        return 'web'  # Default to web

    async def _download_content(self, source: str, source_type: str) -> Dict[str, Any]:
        """Download or retrieve content from the source."""
        logger.info(f"Downloading content from {source}")
        try:
            # Choose appropriate downloader based on source type
            if source_type == 'git':
                try:
                    # Create temporary directory for Git checkout
                    temp_dir = Path(tempfile.mkdtemp(dir=self.base_path))
                    logger.info(f"Created temporary directory for Git checkout: {temp_dir}")

                    # Define patterns for documentation files
                    patterns = ['*.md', '*.mdx', '*.rst', '*.txt', '*.ipynb']
                    
                    # Perform sparse checkout
                    success = sparse_checkout(
                        repo_url=source,
                        output_dir=str(temp_dir),  # Convert Path to string
                        patterns=patterns
                    )
                    
                    if success:
                        return {'success': True, 'content_path': str(temp_dir)}
                    else:
                        logger.error("Sparse checkout failed")
                        return {'success': False, 'error': 'Sparse checkout failed'}
                except Exception as e:
                    logger.error(f"Git checkout failed: {str(e)}")
                    return {'success': False, 'error': str(e)}
            elif source_type == 'web':
                # Simple web download placeholder (success case)
                content_dir = self.base_path / "content"
                content_dir.mkdir(exist_ok=True)
                return {'success': True, 'content_path': str(content_dir)}
            elif source_type == 'local':
                # For testing with local files
                if not Path(source).exists():
                    return {'success': False, 'error': f'Local path does not exist: {source}'}
                return {'success': True, 'content_path': source}
            else:
                # Return error for unsupported source types
                return {'success': False, 'error': f'Unsupported source type: {source_type}'}
        except Exception as e:
            logger.error(f"Download failed: {str(e)}")
            return {'success': False, 'error': str(e)}

    async def _process_content(self, content_path: Path, relevant_files: List[str]) -> List[Dict[str, Any]]:
        """
        Extract and process content from downloaded files based on file type.
        
        Args:
            content_path: Base path where content is stored
            relevant_files: List of relative file paths to process
            
        Returns:
            List of processed content items with extracted text and metadata
        """
        logger.info(f"Processing {len(relevant_files)} files from {content_path}")
        processed_data: List[Dict[str, Any]] = []
        
        try:
            for file_rel_path in relevant_files:
                full_file_path = content_path / file_rel_path
                file_suffix = full_file_path.suffix.lower()
                content_map = {}
                
                try:
                    if file_suffix in ['.md', '.mdx']:
                        # Extract Markdown content using markdown-it
                        content = full_file_path.read_text(encoding='utf-8')
                        extracted_items = extract_content_blocks_with_markdown_it(content, str(full_file_path))
                        for item in extracted_items:
                            content_map = {
                                'file_path': file_rel_path,
                                'content': item.content,
                                'type': item.type,
                                'metadata': item.metadata or {},
                                'language': item.language
                            }
                            processed_data.append(content_map)
                            
                    elif file_suffix == '.rst':
                        # Convert RST to HTML using docutils, then extract text
                        content = full_file_path.read_text(encoding='utf-8')
                        html = publish_string(source=content, writer_name='html').decode('utf-8')
                        text_content = extract_text_with_selector(
                            full_file_path,
                            selector='body',  # Extract main content
                        )
                        if text_content:
                            content_map = {
                                'file_path': file_rel_path,
                                'content': '\n'.join(text_content),
                                'type': 'text',
                                'metadata': {'source': 'rst'},
                            }
                            processed_data.append(content_map)
                            
                    elif file_suffix == '.ipynb':
                        # Process Jupyter notebook
                        with full_file_path.open() as f:
                            nb = nbformat.read(f, as_version=4)
                            
                        for cell in nb.cells:
                            if cell.cell_type == 'markdown':
                                md_items = extract_content_blocks_with_markdown_it(
                                    cell.source,
                                    str(full_file_path)
                                )
                                for item in md_items:
                                    content_map = {
                                        'file_path': file_rel_path,
                                        'content': item.content,
                                        'type': item.type,
                                        'metadata': {
                                            'cell_type': 'markdown',
                                            **(item.metadata or {})
                                        },
                                        'language': item.language
                                    }
                                    processed_data.append(content_map)
                            elif cell.cell_type == 'code':
                                content_map = {
                                    'file_path': file_rel_path,
                                    'content': cell.source,
                                    'type': 'code',
                                    'metadata': {
                                        'cell_type': 'code',
                                        'outputs': [str(o) for o in cell.outputs] if hasattr(cell, 'outputs') else []
                                    },
                                    'language': cell.metadata.get('language', 'python')
                                }
                                processed_data.append(content_map)
                                
                    elif file_suffix == '.txt':
                        # Process plain text files
                        content = full_file_path.read_text(encoding='utf-8')
                        content_map = {
                            'file_path': file_rel_path,
                            'content': content,
                            'type': 'text',
                            'metadata': {'source': 'txt'},
                        }
                        processed_data.append(content_map)
                        
                    else:
                        logger.warning(f"Unsupported file type skipped: {file_rel_path}")
                        
                except Exception as file_error:
                    logger.error(f"Error processing file {file_rel_path}: {str(file_error)}")
                    continue  # Skip this file but continue with others
                    
            return processed_data
            
        except Exception as e:
            logger.error(f"Processing failed: {str(e)}")
            return []

    def _store_results(self, processed_content: List[Dict[str, Any]]) -> None:
        """Store results in memory (temporary implementation)."""
        logger.info(f"Storing {len(processed_content)} processed documents")
        self._results = processed_content  # Simple in-memory storage

    async def _execute_search(self, query: str) -> List[Dict[str, Any]]:
        """Execute search query on processed content."""
        logger.info(f"Executing search query: {query}")
        try:
            # Simple case-insensitive text search on stored results
            matches = []
            query_lower = query.lower()
            for item in self._results:
                content = item.get('content', '').lower()
                if query_lower in content:
                    relevance = float(len(query_lower)) / len(content) if content else 0.0
                    matches.append({
                        'content': item['content'],
                        'type': item.get('type', 'text'),
                        'file_path': item.get('file_path', ''),
                        'relevance': relevance,
                        'metadata': item.get('metadata', {})
                    })
            # Sort by relevance score
            matches.sort(key=lambda x: x['relevance'], reverse=True)
            return matches
        except Exception as e:
            logger.error(f"Search failed: {str(e)}")
            return []

def orchestrate_pipeline(
    source: str,
    source_type: Optional[str] = None,
    search_query: Optional[str] = None,
    **kwargs: Any
) -> Dict[str, Any]:
    """
    Convenience function to run the pipeline without directly instantiating orchestrator.
    """
    orchestrator = PipelineOrchestrator()
    import asyncio
    return asyncio.run(orchestrator.run_pipeline(source, source_type, search_query, **kwargs))

if __name__ == "__main__":
    # Set up a local downloads directory for testing
    from pathlib import Path
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
                "metadata": {}
            },
            {
                "cell_type": "code",
                "source": "print('Notebook code')",
                "outputs": [],
                "metadata": {"language": "python"}
            }
        ],
        "metadata": {},
        "nbformat": 4,
        "nbformat_minor": 4
    }
    import json
    (test_dir / "test.ipynb").write_text(json.dumps(nb_content))
    
    # Test cases with actual files
    test_cases = [
        {
            "source": str(test_dir),
            "source_type": "local",
            "relevant_files": [
                "test.md",
                "test.rst",
                "test.ipynb"
            ],
            "search_query": "test"
        }
    ]
    
    for test in test_cases:
        print(f"\nTesting with {test['source_type']} source...")
        # Create orchestrator with test downloads directory
        orchestrator = PipelineOrchestrator(base_path=test_downloads)
        result = asyncio.run(orchestrator.run_pipeline(
            source=test["source"],
            source_type=test["source_type"],
            search_query=test["search_query"]
        ))
        print(f"Pipeline execution result: {result}")
