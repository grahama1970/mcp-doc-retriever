# src/mcp_doc_retriever/context7/config.py
"""
Configuration Module for ArangoDB and Related Services.

Description:
This module centralizes configuration settings for connecting to ArangoDB,
defining collection/view names, specifying embedding models, and configuring
the ArangoSearch view definition. It loads sensitive information like passwords
and API keys from environment variables (optionally loaded from a .env file).

Third-Party Package Documentation:
- python-dotenv: https://github.com/theskumar/python-dotenv

Sample Input:
Environment variables (e.g., in .env file or exported):
ARANGO_HOST="http://localhost:8529"
ARANGO_USER="root"
ARANGO_PASSWORD="yourpassword"
ARANGO_DB="doc_retriever"
EMBEDDING_MODEL="text-embedding-3-small"
OPENAI_API_KEY="sk-..."
MCP_LLM_MODEL="openai/gpt-4o-mini" # or other LiteLLM-compatible model
MCP_LLM_API_BASE="https://api.openai.com/v1" # or other API base URL
MCP_LLM_TEMPERATURE=0.2

Expected Output (when imported):
Configuration variables are available for use by other modules.
e.g.,
from mcp_doc_retriever.arangodb.config import ARANGO_HOST
print(ARANGO_HOST)
"""

import os
from dotenv import load_dotenv
from typing import List, Dict, Any

# Load environment variables from .env file if it exists
# This allows for easy local development configuration
load_dotenv()

# --- ArangoDB Configuration ---
ARANGO_HOST: str = os.environ.get("ARANGO_HOST", "http://localhost:8529")
ARANGO_USER: str = os.environ.get("ARANGO_USER", "root")
ARANGO_PASSWORD: str = os.environ.get("ARANGO_PASSWORD", "openSesame")
ARANGO_DB_NAME: str = os.environ.get("ARANGO_DB", "doc_retriever")

# --- Collection Configuration ---
COLLECTION_NAME: str = "lessons_learned"  # Vertex collection for lessons
EDGE_COLLECTION_NAME: str = os.environ.get(
    "ARANGO_EDGE_COLLECTION", "lesson_relationships"
)  # Edge collection for relationships
VIEW_NAME: str = "lessons_view"  # Search view for vertices

# --- Graph Configuration ---
GRAPH_NAME: str = os.environ.get("ARANGO_GRAPH", "lessons_graph")

# --- Relationship Types ---
RELATIONSHIP_TYPE_RELATED = "RELATED"  # General relationship between lessons
RELATIONSHIP_TYPE_DEPENDS = "DEPENDS_ON"  # One lesson depends on another
RELATIONSHIP_TYPE_CAUSES = "CAUSES"  # One lesson's problem causes another
RELATIONSHIP_TYPE_FIXES = "FIXES"  # One lesson's solution fixes another's problem

# --- Embedding Configuration ---
EMBEDDING_MODEL: str = os.environ.get("EMBEDDING_MODEL", "text-embedding-ada-002")
EMBEDDING_DIMENSIONS: int = 1536

# --- LiteLLM Configuration ---
MCP_LLM_MODEL: str = os.environ.get("MCP_LLM_MODEL", "openai/gpt-4o-mini")
MCP_LLM_API_BASE: str = os.environ.get(
    "MCP_LLM_API_BASE"
)  # Optional: Leave blank for OpenAI default
MCP_LLM_TEMPERATURE: float = float(os.environ.get("MCP_LLM_TEMPERATURE", 0.2))

# --- Constants for Fields & Analyzers ---
SEARCH_FIELDS: List[str] = ["_key", "problem", "solution", "context", "example"]
STORED_VALUE_FIELDS: List[str] = ["timestamp", "severity", "role", "task", "phase"]
ALL_DATA_FIELDS_PREVIEW: List[str] = STORED_VALUE_FIELDS + SEARCH_FIELDS + ["tags"]
TEXT_ANALYZER: str = "text_en"
TAG_ANALYZER: str = "identity"

# --- ArangoSearch View Definition ---
VIEW_DEFINITION: Dict[str, Any] = {
    "links": {
        COLLECTION_NAME: {
            "fields": {
                "problem": {"analyzers": [TEXT_ANALYZER], "boost": 2.0},
                "solution": {"analyzers": [TEXT_ANALYZER], "boost": 1.5},
                "context": {"analyzers": [TEXT_ANALYZER]},
                "example": {"analyzers": [TEXT_ANALYZER]},
                "tags": {"analyzers": [TAG_ANALYZER]},
            },
            "includeAllFields": False,
            "storeValues": "id",
            "trackListPositions": False,
        }
    },
    "primarySort": [{"field": "timestamp", "direction": "desc"}],
    "primarySortCompression": "lz4",
    "storedValues": [
        {"fields": STORED_VALUE_FIELDS, "compression": "lz4"},
        {"fields": ["embedding"], "compression": "lz4"},
    ],
    "consolidationPolicy": {
        "type": "tier",
        "threshold": 0.1,
        "segmentsMin": 1,
        "segmentsMax": 10,
        "segmentsBytesMax": 5 * 1024**3,
        "segmentsBytesFloor": 2 * 1024**2,
    },
    "commitIntervalMsec": 1000,
    "consolidationIntervalMsec": 10000,
}
