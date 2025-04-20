"""
Configuration settings for the PDF extraction pipeline.

This module defines constants used across the PDF extraction scripts, including
thresholds, model names, and default directories. Centralizing these settings
ensures consistency and simplifies updates.

Usage:
    from config import DEFAULT_OUTPUT_DIR, QWEN_MODEL_NAME
    print(DEFAULT_OUTPUT_DIR)  # Outputs: conversion_output
"""

# Default directories
DEFAULT_OUTPUT_DIR = "output"
DEFAULT_CORRECTIONS_DIR = "corrections"

# Camelot table extraction settings
CAMELOT_DEFAULT_FLAVOR = "lattice"
CAMELOT_LATTICE_LINE_SCALE = 40
CAMELOT_STREAM_EDGE_TOL = 50
LOW_CONFIDENCE_THRESHOLD = 85.0  # Percentage for table accuracy
TABLE_MERGE_SIMILARITY_THRESHOLD = 90  # Percentage for merging similar tables

# Scanned PDF detection settings
SCANNED_CHECK_MAX_PAGES = 5
SCANNED_TEXT_LENGTH_THRESHOLD = 100  # Characters per page
SCANNED_OCR_CONFIDENCE_THRESHOLD = 70.0  # Percentage

# Qwen-VL model settings
QWEN_MODEL_NAME = "Qwen/Qwen-VL"
QWEN_MAX_NEW_TOKENS = 512
QWEN_PROMPT = "Convert this PDF page to structured Markdown, including headings, paragraphs, lists, tables, and code blocks."

# Table context extraction settings
TABLE_CONTEXT_MARGIN = 50.0  # Pixels around table for text extraction
TABLE_CONTEXT_MAX_LINES = 5  # Max lines to extract above/below table

# TikToken encoding
TIKTOKEN_ENCODING_MODEL = "gpt-3.5-turbo"


def usage_function():
    """
    Demonstrates usage of configuration constants.

    Returns:
        dict: Sample configuration settings.
    """
    return {
        "output_dir": DEFAULT_OUTPUT_DIR,
        "qwen_model": QWEN_MODEL_NAME,
        "table_merge_threshold": TABLE_MERGE_SIMILARITY_THRESHOLD,
    }


if __name__ == "__main__":
    # Test basic functionality
    config_sample = usage_function()
    print("Sample Configuration:")
    print(config_sample)
