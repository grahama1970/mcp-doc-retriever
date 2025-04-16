# config.py
import os
from dotenv import load_dotenv
from typing import List, Dict, Any

load_dotenv()

# --- ArangoDB Configuration ---
ARANGO_HOST: str = os.environ.get("ARANGO_HOST", "http://localhost:8529")
ARANGO_USER: str = os.environ.get("ARANGO_USER", "root")
ARANGO_PASSWORD: str = os.environ.get("ARANGO_PASSWORD", "openSesame")
ARANGO_DB_NAME: str = os.environ.get("ARANGO_DB", "doc_retriever")
COLLECTION_NAME: str = "lessons_learned"  # Vertex collection
VIEW_NAME: str = "lessons_view"
# --- NEW: Graph Configuration ---
# Assumes a graph exists containing COLLECTION_NAME as vertices and some edge collections.
# The graph itself needs to be created separately if it doesn't exist.
GRAPH_NAME: str = os.environ.get("ARANGO_GRAPH", "lessons_graph")

# --- Embedding Configuration ---
EMBEDDING_MODEL: str = os.environ.get("EMBEDDING_MODEL", "text-embedding-3-small")
EMBEDDING_DIMENSIONS: int = 1536

# --- Constants for Fields & Analyzers ---
SEARCH_FIELDS: List[str] = ["problem", "solution", "context", "example"]
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
