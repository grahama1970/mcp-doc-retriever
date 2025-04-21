"""
Utility functions for PDF extractor.

This module provides common utilities used across the PDF extraction package,
including path management and geometry calculations.

Example Usage:
    # Fix Python path for imports
    >>> from pdf_extractor.utils import fix_sys_path
    >>> fix_sys_path()  # Adds project root to sys.path
    
    # Calculate IOU between bounding boxes
    >>> from pdf_extractor.utils import calculate_iou
    >>> box1 = [0.0, 0.0, 2.0, 2.0]  # 2x2 box at origin
    >>> box2 = [1.0, 1.0, 3.0, 3.0]  # 2x2 box overlapping by 1x1
    >>> iou = calculate_iou(box1, box2)
    >>> print(f"{iou:.2f}")  # Output: 0.14
"""
import sys
import os
from pathlib import Path
from typing import List, Optional

def fix_sys_path(file_path: Optional[str] = None) -> None:
    """
    Adds the project root directory to sys.path to allow relative imports.
    This is particularly useful for CLI tools and standalone script execution.
    
    Args:
        file_path: Optional path to use as reference. If provided, uses this file's
                  location to determine project root. If None, uses current module location.
    """
    if file_path:
        current_dir = Path(file_path).resolve().parent
    else:
        current_dir = Path(__file__).resolve().parent
        
    project_root = current_dir
    while not (project_root / 'pyproject.toml').exists() and project_root != project_root.parent:
        project_root = project_root.parent
        
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))

def calculate_iou(box1: List[float], box2: List[float]) -> float:
    """
    Calculate intersection over union (IOU) between two bounding boxes.
    Each box is [x1, y1, x2, y2] where (x1,y1) is top-left and (x2,y2) is bottom-right.
    
    Args:
        box1: First bounding box coordinates [x1, y1, x2, y2]
        box2: Second bounding box coordinates [x1, y1, x2, y2]
    
    Returns:
        float: IOU score between 0 and 1
    """
    # Get coordinates of intersection rectangle
    x_left = max(box1[0], box2[0])
    y_top = max(box1[1], box2[1])
    x_right = min(box1[2], box2[2])
    y_bottom = min(box1[3], box2[3])

    if x_right < x_left or y_bottom < y_top:
        return 0.0

    # Area of intersection rectangle
    intersection_area = (x_right - x_left) * (y_bottom - y_top)

    # Area of both boxes
    box1_area = (box1[2] - box1[0]) * (box1[3] - box1[1])
    box2_area = (box2[2] - box2[0]) * (box2[3] - box2[1])

    # IOU = intersection / union
    union_area = box1_area + box2_area - intersection_area
    
    if union_area <= 0:
        return 0.0
        
    return intersection_area / union_area

if __name__ == "__main__":
    print("Running utils.py verification tests...\n")
    
    print("1. Testing IOU calculation:")
    print("--------------------------")
    # Test case 1: 25% overlap
    box1 = [0.0, 0.0, 2.0, 2.0]  # 2x2 box at origin
    box2 = [1.0, 1.0, 3.0, 3.0]  # 2x2 box overlapping by 1x1
    iou = calculate_iou(box1, box2)
    expected = 1/7  # Area of intersection (1) / Area of union (7)
    print(f"Test 1 - Partial overlap:")
    print(f"  Box 1: {box1}")
    print(f"  Box 2: {box2}")
    print(f"  IOU: {iou:.3f} (expected: {expected:.3f})")
    assert abs(iou - expected) < 0.001, f"IOU calculation error: got {iou}, expected {expected}"
    
    # Test case 2: No overlap
    box3 = [3.0, 3.0, 4.0, 4.0]
    iou = calculate_iou(box1, box3)
    print(f"\nTest 2 - No overlap:")
    print(f"  Box 1: {box1}")
    print(f"  Box 2: {box3}")
    print(f"  IOU: {iou:.3f} (expected: 0.000)")
    assert iou == 0.0, f"IOU should be 0 for non-overlapping boxes, got {iou}"
    
    print("\n2. Testing path fixing:")
    print("----------------------")
    # Test basic path fixing
    original_path = sys.path.copy()
    fix_sys_path()
    print(f"Test 1 - Basic path fixing:")
    print(f"  Project root found: {sys.path[0]}")
    assert (Path(sys.path[0]) / 'pyproject.toml').exists(), "Project root not correctly identified"
    
    # Test with specific file path
    test_path = __file__
    fix_sys_path(test_path)
    print(f"\nTest 2 - Path fixing with file parameter:")
    print(f"  Project root found: {sys.path[0]}")
    assert (Path(sys.path[0]) / 'pyproject.toml').exists(), "Project root not correctly identified"
    
    print("\nAll verification tests passed successfully!")
