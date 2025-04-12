"""
Tree-sitter based code file extractor.

This module provides basic code parsing and extraction using tree-sitter-language-pack.
It parses supported file types and extracts their content into ContentBlock objects.

Links:
- tree-sitter-language-pack: https://github.com/Goldziher/tree-sitter-language-pack

Sample Input/Output:
    
Input:
    # test.py
    def hello(name):
        print(f"Hello {name}")
        
    class TestClass:
        def method(self):
            pass

Output:
    [
        ContentBlock(
            content="def hello(name):\n    print(f\"Hello {name}\")",
            language="python",
            type="code",
            metadata={
                "selector": "function_definition",
                "name": "hello",
                "start_line": 1,
                "end_line": 2
            }
        ),
        ContentBlock(
            content="class TestClass:\n    def method(self):\n        pass",
            language="python", 
            type="code",
            metadata={
                "selector": "class_definition",
                "name": "TestClass",
                "start_line": 4,
                "end_line": 6
            }
        )
    ]
"""

from pathlib import Path
from typing import List, Optional
from tree_sitter_language_pack import get_parser
from pydantic import BaseModel, Field
from loguru import logger

# Define mock models for standalone testing
if __name__ == "__main__":
    class MockContentBlock(BaseModel):
        """Minimal ContentBlock implementation for standalone testing."""
        content: str
        language: str
        type: str
        metadata: dict

    class MockExtractedBlock(BaseModel):
        """Mock ExtractedBlock for standalone testing."""
        type: str = Field(description="Tree-sitter node type (e.g., 'function_definition')")
        name: Optional[str] = Field(None, description="Identifier name (e.g., function/class name)")
        content: str = Field(description="Full source code of the block")
        start_line: int = Field(gt=0, description="Starting line number (1-based)")
        end_line: int = Field(gt=0, description="Ending line number (1-based)")

    ContentBlock = MockContentBlock
    ExtractedBlock = MockExtractedBlock
else:
    from mcp_doc_retriever.models import ContentBlock, ExtractedBlock

def extract_blocks_from_file(file_path: str, language: str) -> List[ContentBlock]:
    """Extract code blocks from a source file using tree-sitter.
    
    Args:
        file_path: Path to the source file
        language: Language identifier (e.g., 'python', 'javascript')
        
    Returns:
        List of ContentBlock objects containing the extracted code
    """
    try:
        parser = get_parser(language)
    except Exception as e:
        logger.warning(f"Failed to get parser for language {language}: {e}")
        return []

    try:
        with open(file_path, 'rb') as f:
            content = f.read()
    except Exception as e:
        logger.error(f"Failed to read file {file_path}: {e}")
        return []

    try:
        tree = parser.parse(content)
    except Exception as e:
        logger.error(f"Failed to parse {file_path}: {e}")
        return []
    
    extracted_blocks: List[ExtractedBlock] = []
    
    def visit_node(node) -> None:
        """Extract relevant nodes from the syntax tree."""
        # Common node types across languages that we want to extract
        if node.type in {
            # Python
            'function_definition',
            'class_definition',
            # JavaScript
            'function_declaration',
            'class_declaration',
            'method_definition',
            # Java
            'method_declaration',
            'class_declaration',
        }:
            # Find the name node (usually the first identifier child)
            name_node = None
            for child in node.children:
                if child.type == 'identifier':
                    name_node = child
                    break
            
            name = name_node.text.decode('utf-8') if name_node else None
            content = node.text.decode('utf-8')
            
            try:
                block = ExtractedBlock(
                    type=node.type,
                    name=name,
                    content=content,
                    start_line=node.start_point[0] + 1,  # Convert to 1-based line numbers
                    end_line=node.end_point[0] + 1
                )
                extracted_blocks.append(block)
            except Exception as e:
                logger.warning(f"Failed to create ExtractedBlock: {e}")
        
        # Recurse into children
        for child in node.children:
            visit_node(child)
    
    visit_node(tree.root_node)
    
    # Convert ExtractedBlock to ContentBlock
    content_blocks = []
    for block in extracted_blocks:
        content_blocks.append(ContentBlock(
            content=block.content,
            language=language,
            type='code',
            metadata={
                'selector': block.type,
                'name': block.name,
                'start_line': block.start_line,
                'end_line': block.end_line,
                'source_file': str(Path(file_path).name)
            }
        ))
    
    return content_blocks

if __name__ == "__main__":
    # Simple test with a Python file
    test_content = '''
def hello(name):
    print(f"Hello {name}")

class TestClass:
    def method(self):
        pass
'''
    import tempfile
    with tempfile.NamedTemporaryFile(mode='w', suffix='.py') as f:
        f.write(test_content)
        f.flush()
        
        blocks = extract_blocks_from_file(f.name, 'python')
        print(f"\nFound {len(blocks)} blocks in test file:")
        for block in blocks:
            print(f"\n- {block.metadata['selector']}: {block.metadata['name']}")
            print(f"  Lines {block.metadata['start_line']}-{block.metadata['end_line']}")
            print(f"  Content preview: {block.content[:60]}...")
