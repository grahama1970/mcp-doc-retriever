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
from pathlib import Path
from typing import Dict, List, Optional, Union, Any

# Import core functionality using absolute imports
from mcp_doc_retriever.downloader import web_downloader, git_downloader
from mcp_doc_retriever.searcher import basic_extractor, scanner
from mcp_doc_retriever.context7.sparse_checkout import sparse_checkout


# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class PipelineOrchestrator:
    """Coordinates the document processing pipeline steps."""
    
    def __init__(self, base_path: Optional[Path] = None):
        """Initialize the orchestrator with configuration."""
        self.base_path = base_path or Path("/app/downloads")
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

            # Step 3: Extract and process content
            content_path = Path(download_result.get('content_path', self.base_path / "content"))
            processed_content = await self._process_content(content_path)

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
            else:
                # Simple web download placeholder (success case)
                content_dir = self.base_path / "content"
                content_dir.mkdir(exist_ok=True)
                return {'success': True, 'content_path': str(content_dir)}
        except Exception as e:
            logger.error(f"Download failed: {str(e)}")
            return {'success': False, 'error': str(e)}

    async def _process_content(self, content_path: Path) -> List[Dict[str, Any]]:
        """Extract and process content from downloaded files."""
        logger.info(f"Processing content from {content_path}")
        try:
            # Basic content extraction (placeholder)
            return []
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
            # Search logic (placeholder)
            return []
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
    test_cases = [
        {
            "source": "https://example.com/docs",
            "source_type": "web",
            "search_query": "api authentication"
        },
        {
            "source": "https://github.com/arangodb/python-arango.git",
            "source_type": "git",
            "search_query": "installation guide"
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
