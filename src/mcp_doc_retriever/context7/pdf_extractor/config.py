"""
Configuration settings for PDF extraction and processing.

This module contains all configuration values used across the PDF extraction process,
including model settings, thresholds, and processing parameters.

Links to third-party package documentation:
- Qwen-VL: https://huggingface.co/Qwen/Qwen-VL-Chat
- Camelot: https://camelot-py.readthedocs.io/en/master/
- Tiktoken: https://github.com/openai/tiktoken

Configuration values here are used by the PDF extractor modules to control:
1. Visual-language model settings (Qwen-VL parameters)
2. PDF scanning and OCR quality thresholds
3. Table extraction parameters (using Camelot)
4. Token counting (using Tiktoken)
5. Output file organization
"""

# Qwen-VL model settings
QWEN_MODEL_NAME = "Qwen/Qwen-VL-Chat"
QWEN_MAX_NEW_TOKENS = 1024
QWEN_PROMPT = """
Please provide a detailed textual description of the image in Markdown format.
Focus on the key visual elements, ensuring any text content is accurately transcribed.
Include layout information if it's a diagram or complex visualization.
End your description with any key insights or observations about the image purpose.
"""

# PDF scanning and quality thresholds
SCANNED_CHECK_MAX_PAGES = 5  # Pages to check for scanned content detection
SCANNED_TEXT_LENGTH_THRESHOLD = 100  # Min character count per page for non-scanned
SCANNED_OCR_CONFIDENCE_THRESHOLD = 80.0  # Minimum OCR confidence percentage
LOW_CONFIDENCE_THRESHOLD = 75.0  # Table extraction confidence threshold

# Model configuration
TIKTOKEN_ENCODING_MODEL = "gpt-4"  # Model to use for token counting

# Directory paths
DEFAULT_OUTPUT_DIR = "output"
DEFAULT_CORRECTIONS_DIR = "corrections"

# Table extraction settings
CAMELOT_DEFAULT_FLAVOR = "lattice"
CAMELOT_LATTICE_LINE_SCALE = 15  # Changed from 40 to 15 for better table extraction
CAMELOT_STREAM_EDGE_TOL = 500
TABLE_CONTEXT_MARGIN = 200.0  # pixels
TABLE_CONTEXT_MAX_LINES = 5
# Convert float to int percentage for merge threshold
TABLE_MERGE_SIMILARITY_THRESHOLD = 80  # percent (0-100)

if __name__ == "__main__":
    # Print configuration for verification
    import json
    from pathlib import Path
    
    config_dict = {k: v for k, v in globals().items()
                  if k.isupper() and not k.startswith('_')}
    
    print("PDF Extractor Configuration Verification")
    print("======================================")
    
    # Group settings by category for clearer output
    categories = {
        "Model Settings": {
            "QWEN_MODEL_NAME",
            "QWEN_MAX_NEW_TOKENS",
            "TIKTOKEN_ENCODING_MODEL"
        },
        "Quality Thresholds": {
            "SCANNED_CHECK_MAX_PAGES",
            "SCANNED_TEXT_LENGTH_THRESHOLD",
            "SCANNED_OCR_CONFIDENCE_THRESHOLD",
            "LOW_CONFIDENCE_THRESHOLD"
        },
        "Table Extraction": {
            "CAMELOT_DEFAULT_FLAVOR",
            "CAMELOT_LATTICE_LINE_SCALE",
            "CAMELOT_STREAM_EDGE_TOL",
            "TABLE_CONTEXT_MARGIN",
            "TABLE_CONTEXT_MAX_LINES",
            "TABLE_MERGE_SIMILARITY_THRESHOLD"
        },
        "Directories": {
            "DEFAULT_OUTPUT_DIR",
            "DEFAULT_CORRECTIONS_DIR"
        }
    }
    
    # Verify and display settings by category
    for category, settings in categories.items():
        print(f"\n{category}:")
        print("-" * len(category) + ":")
        for setting in settings:
            if setting in config_dict:
                value = config_dict[setting]
                # Format multiline strings nicely
                if isinstance(value, str) and "\n" in value:
                    print(f"{setting}:")
                    for line in value.strip().split("\n"):
                        print(f"    {line}")
                else:
                    print(f"{setting}: {value}")
            else:
                print(f"{setting}: NOT FOUND!")
    
    # Verify directories exist/can be created
    print("\nVerifying directory settings:")
    for dir_setting in ["DEFAULT_OUTPUT_DIR", "DEFAULT_CORRECTIONS_DIR"]:
        if dir_setting in config_dict:
            path = Path(config_dict[dir_setting])
            if not path.exists():
                print(f"Creating {dir_setting} directory: {path}")
                path.mkdir(parents=True, exist_ok=True)
            print(f"✓ {dir_setting} verified: {path}")
    
    # Verify all required settings are present
    required_settings = {
        'QWEN_MODEL_NAME',
        'QWEN_MAX_NEW_TOKENS',
        'QWEN_PROMPT',
        'SCANNED_CHECK_MAX_PAGES',
        'SCANNED_TEXT_LENGTH_THRESHOLD',
        'SCANNED_OCR_CONFIDENCE_THRESHOLD',
        'LOW_CONFIDENCE_THRESHOLD',
        'TIKTOKEN_ENCODING_MODEL',
    }
    
    missing = required_settings - set(config_dict.keys())
    if missing:
        raise ValueError(f"Missing required settings: {missing}")
    print("\n✓ All required settings present and verified.")
