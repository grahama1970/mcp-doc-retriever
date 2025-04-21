"""
Common type definitions for PDF extractor.

This module provides shared type definitions and aliases used across the PDF
extraction modules to ensure consistent typing and data validation. It defines
TypedDict classes and type aliases for structured data representation of PDF content.

Links to third-party package documentation:
    - typing: https://docs.python.org/3/library/typing.html
    - TypedDict: https://peps.python.org/pep-0589/

Example input/output:
    Input: Raw data from PDF extraction
    >>> raw_table = {
    ...     "page": 1,
    ...     "data": [["cell"]],
    ...     "accuracy": 0.95
    ... }
    >>> table_data: TableData = raw_table  # Type validation
    
    Output: Structured data with type safety
    >>> assert isinstance(table_data["page"], int)
    >>> assert isinstance(table_data["data"], list)
"""
from typing import Dict, List, Any, Union, TypedDict, Tuple, Sequence, cast

# Basic dictionary types
JsonDict = Dict[str, Any]

# Common value types
MetadataValue = Union[str, int, float, List[str], Dict[str, Any]]
NodeMetadata = Dict[str, MetadataValue]

# Table types
class TableData(TypedDict, total=False):
    """Table data with row/column content and metadata."""
    page: int
    data: List[List[str]]
    accuracy: float
    bbox: Union[Tuple[float, float, float, float], List[float]]
    rows: int
    cols: int
    id: str

# Document types
class DocumentNode(TypedDict, total=False):
    """Document node with content and metadata."""
    type: str
    content: str
    metadata: NodeMetadata 
    children: List['DocumentNode']

# Result types
class ExtractedData(TypedDict):
    """Container for extraction results."""
    status: str
    content: Union[List[TableData], str]
    metadata: JsonDict

# Type aliases
TableList = List[TableData]
DocumentList = List[DocumentNode]

def ensure_node_metadata(data: Dict[str, Any]) -> NodeMetadata:
    """Convert raw dictionary to proper metadata format."""
    result = {}
    for k, v in data.items():
        if isinstance(v, (str, int, float, list, dict)):
            result[k] = v
    return result

if __name__ == "__main__":
    # Test basic type usage
    print("Testing type definitions...")
    
    # Test TableData
    table: TableData = {
        "page": 1,
        "data": [["header"], ["cell"]],
        "accuracy": 95.5,
        "bbox": (0.0, 0.0, 100.0, 100.0),
        "rows": 2,
        "cols": 1,
        "id": "table_1"
    }
    assert isinstance(table["page"], int), "page should be int"
    assert isinstance(table["data"], list), "data should be list"
    print("✓ TableData validated")
    
    # Test DocumentNode
    node: DocumentNode = {
        "type": "text",
        "content": "Sample text",
        "metadata": {"source": "test"},
        "children": []
    }
    assert isinstance(node["type"], str), "type should be str"
    assert isinstance(node["metadata"], dict), "metadata should be dict"
    print("✓ DocumentNode validated")
    
    # Test metadata conversion
    metadata = ensure_node_metadata({
        "str_field": "text",
        "int_field": 42,
        "float_field": 3.14,
        "list_field": ["a", "b"],
        "dict_field": {"key": "value"},
        "invalid_field": lambda x: x  # Should be filtered out
    })
    assert "invalid_field" not in metadata, "Invalid field should be filtered"
    assert len(metadata) == 5, "Should have 5 valid fields"
    print("✓ Metadata conversion validated")
    
    # Test extraction results
    result: ExtractedData = {
        "status": "success",
        "content": [table],
        "metadata": {"source": "test"}
    }
    assert isinstance(result["content"], list), "content should be list"
    print("✓ ExtractedData validated")
    
    print("\nAll type validations passed successfully!")