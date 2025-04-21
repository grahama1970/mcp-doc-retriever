"""
Table extraction utilities for PDFs.

This module provides functions for extracting tables from PDFs using Camelot-py,
with fallback mechanisms and validation. It supports both 'lattice' and 'stream' 
extraction methods and includes automatic fallback for low-confidence tables.

Third-party package documentation:
- camelot-py: https://github.com/camelot-dev/camelot
- PDF parsing: https://camelot-py.readthedocs.io/en/master/user/advanced.html

Example usage:
    >>> from mcp_doc_retriever.context7.pdf_extractor.table_extractor import extract_tables
    >>> pdf_path = "example.pdf"
    >>> tables = extract_tables(pdf_path, pages="1-3", flavor="lattice")
    >>> for table in tables:
    ...     print(f"Table on page {table['page']} with {table['rows']}x{table['cols']} cells")
    ...     print(f"Extraction accuracy: {table['accuracy']}%")
"""

from typing import List, Dict, Any, Union, Optional, Tuple
from pathlib import Path
import os
import sys
from loguru import logger

try:
    import camelot.io as camelot  # type: ignore
except ImportError:
    logger.warning("camelot-py not found. Table extraction will not be available.")
    camelot = None  # type: ignore

# Config values - inline for standalone execution (imported for module usage)
# When imported as a module, these will be overridden by .config imports
CAMELOT_DEFAULT_FLAVOR = "lattice"
CAMELOT_LATTICE_LINE_SCALE = 15  # Changed from 40 to 15 to better extract tables
CAMELOT_STREAM_EDGE_TOL = 500

# Handle imports for both standalone and module usage
if __name__ == "__main__":
    # When run as a script, configure system path
    current_dir = Path(__file__).resolve().parent
    project_root = current_dir
    while not (project_root / 'pyproject.toml').exists() and project_root != project_root.parent:
        project_root = project_root.parent
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root / "src"))
    
    # Import after path setup for standalone usage
    try:
        from mcp_doc_retriever.context7.pdf_extractor.config import (
            CAMELOT_DEFAULT_FLAVOR,
            CAMELOT_LATTICE_LINE_SCALE,
            CAMELOT_STREAM_EDGE_TOL,
        )
    except ImportError:
        logger.warning("Using default config values for standalone execution")
else:
    # When imported as a module, use relative imports
    try:
        from .config import (
            CAMELOT_DEFAULT_FLAVOR,
            CAMELOT_LATTICE_LINE_SCALE,
            CAMELOT_STREAM_EDGE_TOL,
        )
    except ImportError:
        logger.warning("Config import failed, using default values")

def extract_tables(
    pdf_path: Union[str, Path], 
    pages: Union[str, List[int]] = "all",
    flavor: str = CAMELOT_DEFAULT_FLAVOR,
) -> List[Dict[str, Any]]:
    """
    Extract tables from PDF using Camelot with fallback mechanisms.
    
    Args:
        pdf_path: Path to PDF file
        pages: Page numbers to process ("all" or list of numbers)
        flavor: Table extraction method ("lattice" or "stream")
    
    Returns:
        List of dictionaries containing table data and metadata
    """
    if camelot is None:
        raise RuntimeError("camelot-py not installed - cannot extract tables")
        
    results = []
    try:
        # Set appropriate parameters based on flavor
        kwargs = {}
        if flavor == "lattice":
            kwargs["line_scale"] = CAMELOT_LATTICE_LINE_SCALE
            # Try different process_background settings to improve extraction
            kwargs["process_background"] = True
        elif flavor == "stream":
            kwargs["edge_tol"] = CAMELOT_STREAM_EDGE_TOL
            
        # Extract tables with proper parameters for the selected flavor
        logger.info(f"Extracting tables with flavor={flavor}, kwargs={kwargs}")
        tables = camelot.read_pdf(
            str(pdf_path),
            pages=str(pages),
            flavor=flavor,
            **kwargs
        )
        
        # Log extraction results
        logger.info(f"Extracted {len(tables)} tables")
        for i, table in enumerate(tables):
            logger.info(f"Table {i+1}: page={table.page}, accuracy={table.parsing_report.get('accuracy', 0)}")
        
        # Process results, attempt stream fallback if needed
        for table in tables:
            table_data = _process_table(table)
            if table_data["accuracy"] < 80 and flavor == "lattice":
                # Try stream mode fallback for low confidence tables
                logger.info(f"Trying stream fallback for low confidence table on page {table.page}")
                stream_tables = camelot.read_pdf(
                    str(pdf_path),
                    pages=str(table.page),
                    flavor="stream",
                    edge_tol=CAMELOT_STREAM_EDGE_TOL,
                )
                if len(stream_tables) > 0:
                    stream_data = _process_table(stream_tables[0])
                    if stream_data["accuracy"] > table_data["accuracy"]:
                        logger.info(f"Stream extraction better: {stream_data['accuracy']} > {table_data['accuracy']}")
                        table_data = stream_data
            
            results.append(table_data)
            
        return results
        
    except Exception as e:
        logger.error(f"Failed to extract tables: {e}")
        return []

def _process_table(table: Any) -> Dict[str, Any]:
    """Convert Camelot table to dictionary with metadata."""
    return {
        "page": table.page,
        "data": table.data,
        "accuracy": table.parsing_report.get("accuracy", 0),
        "bbox": tuple(table._bbox),  # type: ignore  # Protected access needed for bbox
        "rows": len(table.data),
        "cols": len(table.data[0]) if table.data else 0,
    }

if __name__ == "__main__":
    import logging
    import warnings
    from loguru import logger
    import json
    
    # Set up logging for better debugging
    logger.remove()
    logger.add(sys.stderr, level="INFO")
    
    # Filter out specific camelot warning about table areas that doesn't indicate failure
    warnings.filterwarnings("ignore", message="No tables found in table area.*")
    
    print("TABLE EXTRACTOR MODULE VERIFICATION")
    print("==================================")
    
    # Use the specified test PDF file
    input_dir = Path(__file__).parent / "input"
    test_file = input_dir / "BHT_CV32A65X.pdf"
    
    # CRITICAL: Define exact expected results
    # These must match exactly or the test fails
    EXPECTED_RESULTS = {
        "lattice": {
            "table_count": 2,
            "tables": [
                {
                    "page": 1,
                    "rows": 1,
                    "cols": 5,
                    "min_accuracy": 95.0
                },
                {
                    "page": 2,
                    "rows": 5,
                    "cols": 5,
                    "min_accuracy": 95.0
                }
            ]
        }
    }
    
    if test_file.exists():
        print(f"Testing with file: {test_file}")
        print(f"File exists: {test_file.exists()}")
        print(f"File size: {test_file.stat().st_size} bytes")
        
        # Track validation results
        validation_results = {}
        all_tests_passed = True
        
        try:
            # Test with the first two pages
            pages = "1-2"
            print(f"\nExtracting tables from pages {pages}...")
            
            # Focus on lattice extraction which has the most consistent results
            flavor = "lattice"
            print(f"\nUsing {flavor} extraction method:")
            try:
                # Add more debugging information
                print(f"  Settings: line_scale={CAMELOT_LATTICE_LINE_SCALE}, process_background=True")
                
                tables = extract_tables(test_file, pages=pages, flavor=flavor)
                
                # Store actual results for validation
                actual_result = {
                    "table_count": len(tables),
                    "tables": []
                }
                
                if tables:
                    print(f"  ✓ Extracted {len(tables)} tables")
                    for i, table in enumerate(tables, 1):
                        print(f"  - Table {i}:")
                        print(f"    - Page: {table['page']}")
                        print(f"    - Size: {table['rows']}x{table['cols']}")
                        print(f"    - Accuracy: {table['accuracy']:.1f}%")
                        
                        # Store table info for validation
                        actual_result["tables"].append({
                            "page": table['page'],
                            "rows": table['rows'],
                            "cols": table['cols'],
                            "accuracy": table['accuracy']
                        })
                        
                        # Show a sample of the data (first 2 rows if available)
                        if table['rows'] > 0:
                            print(f"    - Data sample:")
                            for row_idx, row in enumerate(table['data'][:2]):
                                print(f"      Row {row_idx+1}: {' | '.join(cell.strip() for cell in row[:3])}")
                else:
                    print(f"  ✗ No tables found with {flavor} flavor")
                    all_tests_passed = False
                
                # Store results for validation
                validation_results[flavor] = actual_result
                
            except Exception as e:
                print(f"  ✗ Extraction failed with {flavor}: {str(e)}")
                import traceback
                traceback.print_exc()
                all_tests_passed = False
            
            # CRITICAL: Validate against expected results
            print("\nVALIDATING RESULTS AGAINST EXPECTED OUTPUT:")
            print("-------------------------------------------")
            
            # Only validate lattice mode for now as it's most consistent
            if flavor in validation_results and flavor in EXPECTED_RESULTS:
                actual = validation_results[flavor]
                expected = EXPECTED_RESULTS[flavor]
                
                # Validate table count
                if actual["table_count"] != expected["table_count"]:
                    print(f"✗ FAIL: Table count mismatch! Expected {expected['table_count']}, got {actual['table_count']}")
                    all_tests_passed = False
                else:
                    print(f"✓ PASS: Table count matches expected ({expected['table_count']})")
                
                # Validate each expected table
                for i, expected_table in enumerate(expected["tables"]):
                    if i >= len(actual["tables"]):
                        print(f"✗ FAIL: Missing expected table at index {i}")
                        all_tests_passed = False
                        continue
                        
                    actual_table = actual["tables"][i]
                    table_valid = True
                    
                    print(f"\nValidating Table {i+1}:")
                    
                    # Check page number
                    if actual_table["page"] != expected_table["page"]:
                        print(f"  ✗ FAIL: Page mismatch! Expected {expected_table['page']}, got {actual_table['page']}")
                        table_valid = False
                        all_tests_passed = False
                    else:
                        print(f"  ✓ PASS: Page matches expected ({expected_table['page']})")
                    
                    # Check rows
                    if actual_table["rows"] != expected_table["rows"]:
                        print(f"  ✗ FAIL: Row count mismatch! Expected {expected_table['rows']}, got {actual_table['rows']}")
                        table_valid = False
                        all_tests_passed = False
                    else:
                        print(f"  ✓ PASS: Row count matches expected ({expected_table['rows']})")
                    
                    # Check columns
                    if actual_table["cols"] != expected_table["cols"]:
                        print(f"  ✗ FAIL: Column count mismatch! Expected {expected_table['cols']}, got {actual_table['cols']}")
                        table_valid = False
                        all_tests_passed = False
                    else:
                        print(f"  ✓ PASS: Column count matches expected ({expected_table['cols']})")
                    
                    # Check accuracy
                    if actual_table["accuracy"] < expected_table["min_accuracy"]:
                        print(f"  ✗ FAIL: Accuracy too low! Expected at least {expected_table['min_accuracy']}%, got {actual_table['accuracy']}%")
                        table_valid = False
                        all_tests_passed = False
                    else:
                        print(f"  ✓ PASS: Accuracy exceeds minimum ({actual_table['accuracy']}% >= {expected_table['min_accuracy']}%)")
                    
                    # Overall table validation
                    if table_valid:
                        print(f"  ✓ PASS: Table {i+1} validation COMPLETE")
                    else:
                        print(f"  ✗ FAIL: Table {i+1} validation FAILED")
            
            # Final verification based on all validation checks
            if all_tests_passed:
                print("\n✅ ALL VALIDATION CHECKS PASSED - VERIFICATION COMPLETE!")
                sys.exit(0)
            else:
                print("\n❌ VALIDATION FAILED - Results don't match expected output")
                sys.exit(1)
            
        except Exception as e:
            print(f"\n✗ Test failed: {str(e)}")
            import traceback
            traceback.print_exc()
            sys.exit(1)
    else:
        print(f"\n✗ Test file not found: {test_file}")
        print(f"Expected location: {input_dir}")
        sys.exit(1)