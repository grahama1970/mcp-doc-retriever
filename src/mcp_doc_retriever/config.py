"""
Module: config.py

Description:
Configuration management for MCP Document Retriever.
Loads and provides access to configuration values. Configuration precedence:
1. Environment Variables (e.g., MCP_DOWNLOAD_BASE_DIR)
2. Values in config.json (located at project root)
3. Default values defined in this module.

Third-party packages:
- json: https://docs.python.org/3/library/json.html
- os: https://docs.python.org/3/library/os.html

Sample config.json:
{
  "DOWNLOAD_BASE_DIR": "./custom_downloads_from_file"
}

Sample environment variable setting:
export MCP_DOWNLOAD_BASE_DIR=/etc/app_downloads

Expected output:
- If MCP_DOWNLOAD_BASE_DIR is set, it will be used.
- Otherwise, if config.json exists and defines DOWNLOAD_BASE_DIR, that value is used.
- Otherwise, the default './downloads' is used.
- The final path is made absolute.
"""

import os
import json
import logging

# Use module-level logger
logger = logging.getLogger(__name__)

# --- Path Calculation ---
try:
    # Calculate path relative to this file's location
    # __file__ -> /path/to/src/mcp_doc_retriever/config.py
    # os.path.dirname(__file__) -> /path/to/src/mcp_doc_retriever
    # os.path.abspath(os.path.join(..., '..', '..')) -> /path/to/project_root
    PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    CONFIG_PATH = os.path.join(PROJECT_ROOT, "config.json")
except NameError:
    # __file__ might not be defined in some environments (e.g., certain interactive sessions)
    logger.warning(
        "__file__ not defined, assuming config.json is in current working directory."
    )
    CONFIG_PATH = os.path.abspath("config.json")

# --- Load Config File Data (if exists and valid) ---
_config_data = {}
try:
    # Check if the file exists before trying to open
    if os.path.exists(CONFIG_PATH):
        logger.debug(f"Attempting to load configuration from: {CONFIG_PATH}")
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            _config_data = json.load(f)
        logger.debug(f"Successfully loaded configuration from {CONFIG_PATH}")
    else:
        logger.debug(
            f"Configuration file not found at {CONFIG_PATH}. Using defaults and environment variables."
        )
except json.JSONDecodeError as e:
    # Log specific error if JSON is invalid
    logger.warning(f"Error decoding JSON from {CONFIG_PATH}: {e}. File ignored.")
    _config_data = {}  # Ensure empty dict on decode error
except PermissionError:
    # Log specific error if permissions are insufficient
    logger.warning(
        f"Permission denied when trying to read {CONFIG_PATH}. File ignored."
    )
    _config_data = {}
except Exception as e:
    # Catch any other unexpected errors during file read/parse
    logger.error(
        f"Unexpected error loading config from {CONFIG_PATH}: {e}", exc_info=True
    )
    _config_data = {}


# --- Define Configuration Values with Precedence ---

# DOWNLOAD_BASE_DIR: Base directory for downloads
_DEFAULT_DOWNLOAD_DIR = "./downloads"  # Define default clearly
DOWNLOAD_BASE_DIR_FROM_ENV = os.environ.get(
    "MCP_DOWNLOAD_BASE_DIR"
)  # Use a prefixed env var name
DOWNLOAD_BASE_DIR_FROM_FILE = _config_data.get("DOWNLOAD_BASE_DIR")  # Check file data

# Determine final value based on precedence
if DOWNLOAD_BASE_DIR_FROM_ENV:
    DOWNLOAD_BASE_DIR_RAW = DOWNLOAD_BASE_DIR_FROM_ENV
    config_source = "environment variable MCP_DOWNLOAD_BASE_DIR"
elif DOWNLOAD_BASE_DIR_FROM_FILE:
    DOWNLOAD_BASE_DIR_RAW = DOWNLOAD_BASE_DIR_FROM_FILE
    config_source = f"config file {CONFIG_PATH}"
else:
    DOWNLOAD_BASE_DIR_RAW = _DEFAULT_DOWNLOAD_DIR
    config_source = "default value"

# Ensure the final path is absolute. Relative paths are resolved relative to the CWD.
try:
    DOWNLOAD_BASE_DIR = os.path.abspath(DOWNLOAD_BASE_DIR_RAW)
    logger.info(
        f"Using DOWNLOAD_BASE_DIR='{DOWNLOAD_BASE_DIR}' (Source: {config_source})"
    )
except TypeError as e:
    logger.error(
        f"Invalid type for DOWNLOAD_BASE_DIR ('{DOWNLOAD_BASE_DIR_RAW}' from {config_source}): {e}. Falling back to absolute default."
    )
    DOWNLOAD_BASE_DIR = os.path.abspath(_DEFAULT_DOWNLOAD_DIR)


# --- Add other config variables here following the same pattern ---
# Example: TIMEOUT_REQUESTS (assuming it's defined in utils, but config could override)
try:
    from .utils import TIMEOUT_REQUESTS as _DEFAULT_TIMEOUT_REQUESTS
except ImportError:
    _DEFAULT_TIMEOUT_REQUESTS = 30  # Fallback if utils not available during config load

_TIMEOUT_REQUESTS_FROM_ENV = os.environ.get("MCP_TIMEOUT_REQUESTS")
_TIMEOUT_REQUESTS_FROM_FILE = _config_data.get("TIMEOUT_REQUESTS")

if _TIMEOUT_REQUESTS_FROM_ENV:
    try:
        TIMEOUT_REQUESTS = int(_TIMEOUT_REQUESTS_FROM_ENV)
        logger.info(
            f"Using TIMEOUT_REQUESTS={TIMEOUT_REQUESTS} from environment variable MCP_TIMEOUT_REQUESTS"
        )
    except (ValueError, TypeError):
        logger.warning(
            f"Invalid integer value for MCP_TIMEOUT_REQUESTS env var: '{_TIMEOUT_REQUESTS_FROM_ENV}'. Using default."
        )
        TIMEOUT_REQUESTS = _DEFAULT_TIMEOUT_REQUESTS
elif _TIMEOUT_REQUESTS_FROM_FILE:
    try:
        TIMEOUT_REQUESTS = int(_TIMEOUT_REQUESTS_FROM_FILE)
        logger.info(
            f"Using TIMEOUT_REQUESTS={TIMEOUT_REQUESTS} from config file {CONFIG_PATH}"
        )
    except (ValueError, TypeError):
        logger.warning(
            f"Invalid integer value for TIMEOUT_REQUESTS in config file: '{_TIMEOUT_REQUESTS_FROM_FILE}'. Using default."
        )
        TIMEOUT_REQUESTS = _DEFAULT_TIMEOUT_REQUESTS
else:
    TIMEOUT_REQUESTS = _DEFAULT_TIMEOUT_REQUESTS
    logger.info(f"Using default TIMEOUT_REQUESTS={TIMEOUT_REQUESTS}")

# Ensure timeout is positive
if TIMEOUT_REQUESTS <= 0:
    logger.warning(
        f"TIMEOUT_REQUESTS must be positive ({TIMEOUT_REQUESTS} provided). Resetting to default {_DEFAULT_TIMEOUT_REQUESTS}."
    )
    TIMEOUT_REQUESTS = _DEFAULT_TIMEOUT_REQUESTS


def usage_example():
    """Demonstrates accessing the config values programmatically."""
    # Basic logging setup for example usage if run directly
    # Ensure handlers aren't added multiple times if imported
    if not logging.getLogger().handlers:
        logging.basicConfig(
            level=logging.INFO, format="[%(levelname)s] %(name)s: %(message)s"
        )

    print(f"\n--- Configuration Usage Example ---")
    print(f"Attempted to load config file from: {CONFIG_PATH}")
    print(f"Resolved DOWNLOAD_BASE_DIR: {DOWNLOAD_BASE_DIR}")
    print(f"Resolved TIMEOUT_REQUESTS: {TIMEOUT_REQUESTS}")
    # Add other configured variables here
    print("---------------------------------")


if __name__ == "__main__":
    usage_example()
