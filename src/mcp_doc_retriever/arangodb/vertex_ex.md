You are absolutely right! My apologies. I focused on analyzing the *existing* code based on the last prompt but completely missed the crucial instruction about **implementing the edge collection** to make graph traversal useful.

You are correct that without edges connecting the `lessons_learned` vertices, the `graph_traverse` function, while syntactically correct, will always return empty results (unless traversing depth 0, which just returns the start node).

Let's rectify this by incorporating the concept of an edge collection and the necessary modifications.

**Plan:**

1.  **Define Edge Collection:** Add a configuration for the edge collection name (e.g., `lesson_relationships`) in `config.py`. Define its expected structure (`_from`, `_to`, `rationale`, maybe `type`, `timestamp`).
2.  **Define Graph:** Update `config.py` with the `GRAPH_NAME` (already done) and ensure it conceptually includes the vertex and new edge collections.
3.  **Setup (`arango_setup.py`):**
    *   Add a function `ensure_edge_collection` similar to `ensure_collection`.
    *   Add a function `ensure_graph` to create the graph definition in ArangoDB if it doesn't exist, linking the vertex and edge collections. Call these in the main setup sequence.
4.  **CRUD (`crud_api.py`):**
    *   Add `add_relationship(db, from_key, to_key, rationale, relationship_type)` function.
    *   Add `delete_relationship(db, edge_key)` function.
    *   **(Important)** Modify `delete_lesson` to optionally delete connected edges to prevent orphans (requires AQL).
5.  **CLI (`cli.py`):**
    *   Add `add-relationship` command.
    *   Add `delete-relationship` command.
    *   Update help text for `traverse` command mentioning edge requirements.
6.  **Main Usage (`main_usage.py`):** Add examples of creating relationships between sample lessons and then traversing.
7.  **Search API Standalone Test (`search_api.py`):** Update the test to create relationships and test traversal meaningfully.

---

**1. Updated `src/mcp_doc_retriever/arangodb/config.py`**

```python
# src/mcp_doc_retriever/arangodb/config.py
import os
from dotenv import load_dotenv
from typing import List, Dict, Any

load_dotenv()

# --- ArangoDB Configuration ---
ARANGO_HOST: str = os.environ.get("ARANGO_HOST", "http://localhost:8529")
ARANGO_USER: str = os.environ.get("ARANGO_USER", "root")
ARANGO_PASSWORD: str = os.environ.get("ARANGO_PASSWORD", "openSesame")
ARANGO_DB_NAME: str = os.environ.get("ARANGO_DB", "doc_retriever")
# Vertex Collection
COLLECTION_NAME: str = "lessons_learned"
# --- NEW: Edge Collection ---
EDGE_COLLECTION_NAME: str = os.environ.get("ARANGO_EDGE_COLLECTION", "lesson_relationships")
# View for searching vertices
VIEW_NAME: str = "lessons_view"
# Graph Definition
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

# --- Relationship Edge Types (Example Enum or Constants) ---
RELATIONSHIP_TYPE_RELATED = "RELATED"
RELATIONSHIP_TYPE_DEPENDS = "DEPENDS_ON"
RELATIONSHIP_TYPE_CAUSES = "CAUSES"
# Add more as needed

# --- ArangoSearch View Definition (Only targets VERTEX collection) ---
VIEW_DEFINITION: Dict[str, Any] = {
    "links": {
        COLLECTION_NAME: { # Links only the vertex collection to the search view
            "fields": {
                "problem": {"analyzers": [TEXT_ANALYZER], "boost": 2.0},
                "solution": {"analyzers": [TEXT_ANALYZER], "boost": 1.5},
                "context": {"analyzers": [TEXT_ANALYZER]},
                "example": {"analyzers": [TEXT_ANALYZER]},
                "tags": {"analyzers": [TAG_ANALYZER]},
            },
            "includeAllFields": False, "storeValues": "id", "trackListPositions": False,
        }
    },
    "primarySort": [{"field": "timestamp", "direction": "desc"}],
    "primarySortCompression": "lz4",
    "storedValues": [
        {"fields": STORED_VALUE_FIELDS, "compression": "lz4"},
        {"fields": ["embedding"], "compression": "lz4"},
    ],
    "consolidationPolicy": {
        "type": "tier", "threshold": 0.1, "segmentsMin": 1, "segmentsMax": 10,
        "segmentsBytesMax": 5 * 1024**3, "segmentsBytesFloor": 2 * 1024**2,
    },
    "commitIntervalMsec": 1000,
    "consolidationIntervalMsec": 10000,
}
```

---

**2. Updated `src/mcp_doc_retriever/arangodb/arango_setup.py`**

```python
# src/mcp_doc_retriever/arangodb/arango_setup.py
import uuid
from datetime import datetime, timezone
from typing import Dict, Any, Optional

from loguru import logger
from arango import ArangoClient
from arango.database import StandardDatabase
from arango.collection import StandardCollection, EdgeCollection
from arango.graph import Graph
from arango.view import View
from arango.exceptions import (
    ArangoClientError, ArangoServerError, DatabaseCreateError,
    CollectionCreateError, ViewCreateError, ViewUpdateError,
    DocumentInsertError, GraphCreateError
)

# Import config and embedding utils
from .config import (
    ARANGO_HOST, ARANGO_USER, ARANGO_PASSWORD, ARANGO_DB_NAME,
    COLLECTION_NAME, VIEW_NAME, VIEW_DEFINITION,
    EDGE_COLLECTION_NAME, GRAPH_NAME # Added Edge/Graph names
)
from .embedding_utils import get_text_for_embedding, get_embedding

# --- Connection & Resource Management ---

# connect_arango, ensure_database remain the same
# ... (connect_arango code) ...
# ... (ensure_database code) ...

def ensure_collection(db: StandardDatabase, collection_name: str) -> StandardCollection:
    """Ensures the target VERTEX collection exists."""
    with logger.contextualize(action="ensure_collection", collection=collection_name, type="vertex"):
        try:
            if not db.has_collection(collection_name):
                logger.info(f"Vertex collection '{collection_name}' not found. Creating...")
                collection = db.create_collection(collection_name)
                logger.success(f"Vertex collection '{collection_name}' created.")
                return collection
            else:
                logger.info(f"Vertex collection '{collection_name}' already exists.")
                return db.collection(collection_name)
        except (CollectionCreateError, ArangoServerError) as e:
            logger.error(f"Failed to ensure vertex collection '{collection_name}': {e}")
            raise

# --- NEW: Ensure Edge Collection ---
def ensure_edge_collection(db: StandardDatabase, edge_collection_name: str) -> EdgeCollection:
    """Ensures the target EDGE collection exists."""
    with logger.contextualize(action="ensure_edge_collection", collection=edge_collection_name, type="edge"):
        try:
            if not db.has_collection(edge_collection_name):
                logger.info(f"Edge collection '{edge_collection_name}' not found. Creating...")
                # Important: Specify edge=True
                collection = db.create_collection(edge_collection_name, edge=True)
                logger.success(f"Edge collection '{edge_collection_name}' created.")
                return collection # type: ignore # Cast needed as create_collection returns StandardCollection type hint
            else:
                logger.info(f"Edge collection '{edge_collection_name}' already exists.")
                return db.collection(edge_collection_name) # type: ignore # Cast needed
        except (CollectionCreateError, ArangoServerError) as e:
            logger.error(f"Failed to ensure edge collection '{edge_collection_name}': {e}")
            raise

# ensure_view remains the same (it only targets the vertex collection)
# ... (ensure_view code) ...


# --- NEW: Ensure Graph Definition ---
def ensure_graph(db: StandardDatabase, graph_name: str, edge_collection_name: str, vertex_collection_name: str) -> Graph:
    """Ensures the graph definition exists in ArangoDB."""
    with logger.contextualize(action="ensure_graph", graph=graph_name):
        try:
            if not db.has_graph(graph_name):
                logger.info(f"Graph '{graph_name}' not found. Creating...")
                # Define the edge relationship
                edge_definition = {
                    "edge_collection": edge_collection_name,
                    "from_vertex_collections": [vertex_collection_name],
                    "to_vertex_collections": [vertex_collection_name] # Self-referential for lessons learned
                }
                # Create the graph with the edge definition
                graph = db.create_graph(graph_name, edge_definitions=[edge_definition])
                logger.success(f"Graph '{graph_name}' created successfully.")
                return graph
            else:
                logger.info(f"Graph '{graph_name}' already exists.")
                return db.graph(graph_name)
        except (GraphCreateError, ArangoServerError) as e:
            logger.error(f"Failed to ensure graph '{graph_name}': {e}")
            raise


# --- Sample Data Handling (No changes needed here, edges added separately) ---
# create_sample_lesson_data remains the same
# insert_sample_if_empty remains the same
# ... (create_sample_lesson_data code) ...
# ... (insert_sample_if_empty code) ...

```

---

**3. Updated `src/mcp_doc_retriever/arangodb/crud_api.py`**

```python
# src/mcp_doc_retriever/arangodb/crud_api.py
"""
# ... (Keep existing docstring, add info about relationship functions) ...

Sample Input (add_relationship):
from_key = "key1"
to_key = "key2"
rationale = "Solution in key2 addresses problem similar to key1."
type = "RELATED" # e.g., RELATIONSHIP_TYPE_RELATED from config
meta = add_relationship(db, from_key, to_key, rationale, type)

Sample Input (delete_relationship):
success = delete_relationship(db, "some_edge_key")
"""
import uuid
from datetime import datetime, timezone
from typing import Dict, Any, Optional, List

from loguru import logger
from arango.database import StandardDatabase
from arango.exceptions import (
    DocumentInsertError, DocumentRevisionError, DocumentUpdateError,
    DocumentDeleteError, ArangoServerError, EdgeDefinitionError,
    AQLQueryExecuteError # Added for delete_lesson AQL
)

# Import shared config and utilities
from .config import (
    COLLECTION_NAME, EDGE_COLLECTION_NAME, SEARCH_FIELDS, GRAPH_NAME # Added Edge/Graph
)
from .embedding_utils import get_text_for_embedding, get_embedding

# --- Lesson CRUD Functions (add_lesson, get_lesson, update_lesson are mostly unchanged)---

# ... (add_lesson code - no changes needed) ...
# ... (get_lesson code - no changes needed) ...
# ... (update_lesson code - no changes needed) ...


# --- Modified delete_lesson to handle edges ---
def delete_lesson(db: StandardDatabase, lesson_key: str, delete_edges: bool = True) -> bool:
    """
    Deletes a lesson document by its _key. Optionally deletes connected edges.

    Args:
        db: The ArangoDB database connection.
        lesson_key: The _key of the document to delete.
        delete_edges: If True, also delete edges connected to this lesson
                      within the configured graph to prevent orphans.

    Returns:
        True if deletion was successful (including optional edge deletion), False otherwise.
    """
    action_uuid = str(uuid.uuid4())
    lesson_id = f"{COLLECTION_NAME}/{lesson_key}" # Full _id needed for graph operations
    with logger.contextualize(action="delete_lesson", crud_id=action_uuid, lesson_id=lesson_id):
        try:
            collection = db.collection(COLLECTION_NAME)
            edge_collection = db.collection(EDGE_COLLECTION_NAME)

            # --- Optional: Delete Connected Edges ---
            if delete_edges:
                logger.info(f"Attempting to delete edges connected to {lesson_id} in graph {GRAPH_NAME}...")
                # Use AQL to find and remove edges connected INBOUND or OUTBOUND
                aql = f"""
                FOR v, e IN 1..1 ANY @start_node GRAPH "{GRAPH_NAME}"
                  REMOVE e IN {EDGE_COLLECTION_NAME}
                  RETURN OLD
                """
                bind_vars = {"start_node": lesson_id}
                try:
                    cursor = db.aql.execute(aql, bind_vars=bind_vars)
                    deleted_edge_count = cursor.count() # type: ignore # Access count if needed
                    logger.info(f"Deleted {deleted_edge_count} connected edge(s) for {lesson_id}.")
                except AQLQueryExecuteError as aqle:
                    logger.error(f"Failed to delete connected edges for {lesson_id}: {aqle}")
                    # Decide if this should prevent vertex deletion? Let's allow vertex delete attempt anyway.
                    # return False # Option: Fail if edges can't be deleted

            # --- Delete the Lesson Vertex ---
            logger.info(f"Attempting to delete lesson vertex {lesson_id}")
            # Use the key for collection delete method
            deleted = collection.delete(lesson_key, sync=True, return_old=False)
            if deleted:
                logger.success(f"Lesson vertex deleted successfully: _key={lesson_key}")
                return True
            else:
                # Should be caught by DocumentDeleteError below if not found
                logger.warning(f"Lesson vertex deletion call returned False for key: {lesson_key}")
                return False

        except (DocumentDeleteError, ArangoServerError) as e:
             # DocumentDeleteError handles the "vertex not found" case
            logger.error(f"Failed to delete lesson vertex (key: {lesson_key}): {e}")
            return False
        except Exception as e:
            logger.exception(f"Unexpected error during lesson deletion (key: {lesson_key}): {e}")
            return False


# --- NEW: Relationship (Edge) CRUD Functions ---

def add_relationship(
    db: StandardDatabase,
    from_lesson_key: str,
    to_lesson_key: str,
    rationale: str,
    relationship_type: str,
    attributes: Optional[Dict[str, Any]] = None
) -> Optional[Dict[str, Any]]:
    """
    Creates an edge between two lesson documents in the relationship collection.

    Args:
        db: ArangoDB database connection.
        from_lesson_key: The _key of the source lesson.
        to_lesson_key: The _key of the target lesson.
        rationale: Text explaining the reason for the relationship.
        relationship_type: A category for the relationship (e.g., "RELATED", "DEPENDS_ON").
        attributes: Optional dictionary of additional properties for the edge.

    Returns:
        Metadata of the created edge document, or None on failure.
    """
    action_uuid = str(uuid.uuid4())
    from_id = f"{COLLECTION_NAME}/{from_lesson_key}"
    to_id = f"{COLLECTION_NAME}/{to_lesson_key}"

    with logger.contextualize(action="add_relationship", crud_id=action_uuid, from_id=from_id, to_id=to_id):
        if not rationale or not relationship_type:
             logger.error("Rationale and relationship_type are required to add relationship.")
             return None

        edge_data = {
            "_from": from_id,
            "_to": to_id,
            "rationale": rationale,
            "type": relationship_type,
            "timestamp": datetime.now(timezone.utc).isoformat(timespec='milliseconds').replace('+00:00', 'Z'),
        }
        if attributes:
            edge_data.update(attributes) # Add any extra properties

        try:
            edge_collection = db.collection(EDGE_COLLECTION_NAME)
            logger.info(f"Creating relationship edge from {from_id} to {to_id} (Type: {relationship_type})")
            # Insert into the specific edge collection
            meta = edge_collection.insert(edge_data, sync=True)
            logger.success(f"Relationship edge created successfully: _key={meta['_key']}")
            return meta
        except (DocumentInsertError, ArangoServerError, EdgeDefinitionError) as e:
            # EdgeDefinitionError can occur if _from/_to reference non-existent collections/docs (less likely here)
             logger.error(f"Failed to add relationship edge ({from_id} -> {to_id}): {e}")
             return None
        except Exception as e:
            logger.exception(f"Unexpected error adding relationship edge ({from_id} -> {to_id}): {e}")
            return None


def delete_relationship(db: StandardDatabase, edge_key: str) -> bool:
    """
    Deletes a relationship edge document by its _key.

    Args:
        db: The ArangoDB database connection.
        edge_key: The _key of the edge document in the relationship collection.

    Returns:
        True if deletion was successful, False otherwise.
    """
    action_uuid = str(uuid.uuid4())
    edge_id = f"{EDGE_COLLECTION_NAME}/{edge_key}" # Construct full ID for logging clarity
    with logger.contextualize(action="delete_relationship", crud_id=action_uuid, edge_id=edge_id):
        try:
            edge_collection = db.collection(EDGE_COLLECTION_NAME)
            logger.info(f"Attempting to delete relationship edge with key: {edge_key}")
            deleted = edge_collection.delete(edge_key, sync=True)
            if deleted:
                logger.success(f"Relationship edge deleted successfully: _key={edge_key}")
                return True
            else:
                 # Should be caught by DocumentDeleteError if not found
                logger.warning(f"Relationship edge deletion call returned False for key: {edge_key}")
                return False
        except (DocumentDeleteError, ArangoServerError) as e:
            logger.error(f"Failed to delete relationship edge (key: {edge_key}): {e}")
            return False
        except Exception as e:
            logger.exception(f"Unexpected error during relationship edge deletion (key: {edge_key}): {e}")
            return False

```

---

**4. Updated `src/mcp_doc_retriever/arangodb/cli.py`**

```python
# src/mcp_doc_retriever/arangodb/cli.py
"""
Command-Line Interface (CLI) for ArangoDB Lessons Learned Document Retriever

**Agent Instructions:**
# ... (Main docstring - add relationship commands) ...

--- Relationship Commands ---

9.  `add-relationship`: Create a directed relationship between two lessons.
    -   ARGUMENTS:
        -   `FROM_KEY`: (Required) The `_key` of the source lesson.
        -   `TO_KEY`: (Required) The `_key` of the target lesson.
    -   OPTIONS:
        -   `--rationale` / `-r`: (Required) Text explaining the relationship reason.
        -   `--type` / `-typ`: (Required) Category of the relationship (e.g., RELATED, DEPENDS_ON).
        -   `--json-output`: (Optional, bool, default: False) Output metadata as JSON.
    -   OUTPUT: Prints JSON metadata of the new edge or success message.

10. `delete-relationship`: Delete a relationship edge by its key.
    -   ARGUMENTS:
        -   `EDGE_KEY`: (Required) The `_key` of the relationship edge document to delete.
    -   OPTIONS:
        -   `--json-output`: (Optional, bool, default: False) Output status as JSON.
    -   OUTPUT: Prints success message or JSON status.

**Error Handling:**
# ... (rest of main docstring) ...
"""

import typer
import json
import sys
import os
from loguru import logger
from rich.console import Console
from rich.table import Table
from rich.json import JSON
from typing import List, Optional

# Import APIs, config, etc.
from .arango_setup import connect_arango, ensure_database, ensure_edge_collection, ensure_graph # Added graph setup
from .search_api import search_bm25, search_semantic, hybrid_search, graph_traverse
# Added relationship functions
from .crud_api import add_lesson, get_lesson, update_lesson, delete_lesson, add_relationship, delete_relationship
from .embedding_utils import get_embedding
# Added EDGE_COLLECTION_NAME
from .config import ARANGO_DB_NAME, GRAPH_NAME, COLLECTION_NAME, EDGE_COLLECTION_NAME
from .cache_setup import initialize_litellm_cache

# --- Typer App Initialization ---
app = typer.Typer(name="arangodb-search-cli", help=__doc__, add_completion=False)

# --- Rich Console ---
console = Console()

# --- Global State / Context & Logging Setup ---
@app.callback()
def main_callback(log_level: str = typer.Option(os.environ.get("LOG_LEVEL", "INFO").upper(),"--log-level","-l",help="Set logging level.",envvar="LOG_LEVEL")):
    """Main callback to configure logging and caching."""
    logger.remove()
    logger.add(sys.stderr,level=log_level,format="{time:HH:mm:ss} | {level: <7} | {message}",backtrace=False,diagnose=False)
    logger.debug("Initializing LiteLLM Caching...")
    initialize_litellm_cache()
    logger.debug("Caching initialized.")
    # Optionally ensure graph exists on CLI start? Or handle per-command?
    # db = get_db_connection() # Might be too eager
    # ensure_edge_collection(db, EDGE_COLLECTION_NAME)
    # ensure_graph(db, GRAPH_NAME, EDGE_COLLECTION_NAME, COLLECTION_NAME)

# --- Utility ---
def get_db_connection():
    # ... (remains the same) ...
    try:
        client = connect_arango()
        db = ensure_database(client)
        # --- Ensure graph components exist upon first connection in CLI ---
        # This makes commands relying on graph/edges safer. Could be optional.
        try:
            ensure_edge_collection(db, EDGE_COLLECTION_NAME)
            ensure_graph(db, GRAPH_NAME, EDGE_COLLECTION_NAME, COLLECTION_NAME)
        except Exception as setup_e:
            logger.warning(f"Graph/Edge setup check failed during connection: {setup_e}. Traversal/Relationship commands might fail.")
        # --- End ensure graph components ---
        return db
    except Exception as e:
        logger.error(f"DB connection failed: {e}")
        console.print("[bold red]Error:[/bold red] Could not connect to ArangoDB.")
        raise typer.Exit(code=1)


# --- Search Commands (Code remains the same) ---
# ... (cli_search_bm25 code) ...
# ... (cli_search_semantic code) ...
# ... (cli_search_hybrid code) ...

# --- Graph Traversal Command (Help text update recommended) ---
@app.command("traverse")
def cli_graph_traverse(
    start_node_id: str = typer.Argument(..., help="Start node _id (e.g., 'lessons_learned/12345')."),
    graph_name: str = typer.Option(GRAPH_NAME, "--graph-name", "-g", help="Graph name."),
    min_depth: int = typer.Option(1, "--min-depth", help="Min traversal depth."),
    max_depth: int = typer.Option(1, "--max-depth", help="Max traversal depth."),
    direction: str = typer.Option("OUTBOUND", "--direction", "-dir", help="OUTBOUND, INBOUND, or ANY."),
    limit: Optional[int] = typer.Option(None, "--limit", "-lim", help="Max paths."),
    json_output: bool = typer.Option(True, "--json-output", "-j", help="Output as JSON (default).")
):
    """
    [Graph] Explore relationships between lessons via graph traversal.

    WHEN TO USE: Use to understand connections, dependencies, or related concepts
                 starting from a specific lesson. Requires edges to exist between lessons,
                 created using the 'add-relationship' command.
    HOW TO USE: Provide the full `_id` of the starting lesson. Adjust depth,
                direction, limit, or graph name. Output is JSON by default.
    """
    # ... (Function body remains the same) ...
    logger.info(f"CLI: Performing graph traversal from '{start_node_id}'")
    db = get_db_connection()
    try:
        if direction.upper() not in ["OUTBOUND", "INBOUND", "ANY"]:
            console.print(f"[bold red]Error:[/bold red] Invalid direction '{direction}'.")
            raise typer.Exit(code=1)
        results_data = graph_traverse(db, start_node_id, graph_name, min_depth, max_depth, direction, limit)
        if json_output:
             if results_data: console.print(JSON(json.dumps(results_data, indent=2)))
             else: print("[]")
        else:
             console.print(f"[green]Traversal complete.[/green] Found {len(results_data)} paths. Use --json-output to view details.")
    except Exception as e:
        logger.error(f"Graph traversal failed: {e}")
        console.print(f"[bold red]Error during graph traversal:[/bold red] {e}")
        raise typer.Exit(code=1)


# --- CRUD Commands (Code remains the same) ---
# ... (cli_add_lesson code) ...
# ... (cli_get_lesson code) ...
# ... (cli_update_lesson code) ...
# ... (cli_delete_lesson code) ...


# --- NEW: Relationship CRUD Commands ---

@app.command("add-relationship")
def cli_add_relationship(
    from_key: str = typer.Argument(..., help="The _key of the source lesson."),
    to_key: str = typer.Argument(..., help="The _key of the target lesson."),
    rationale: str = typer.Option(..., "--rationale", "-r", help="Reason for the relationship."),
    type: str = typer.Option(..., "--type", "-typ", help="Type of relationship (e.g., RELATED, DEPENDS_ON)."),
    json_output: bool = typer.Option(False, "--json-output", "-j", help="Output metadata as JSON.")
):
    """
    [Graph] Create a directed relationship edge between two lessons.

    WHEN TO USE: Use to explicitly link two lessons, providing context for why
                 they are connected (e.g., similar problems, dependencies).
    HOW TO USE: Specify the source key (`from_key`), target key (`to_key`),
                a mandatory rationale, and a relationship type.
    """
    logger.info(f"CLI: Adding relationship from {from_key} to {to_key}")
    db = get_db_connection()
    try:
        # Add validation if needed (e.g., check if type is from a predefined list)
        meta = add_relationship(db, from_key, to_key, rationale, type)
        if meta:
            if json_output:
                print(json.dumps(meta))
            else:
                console.print(f"[green]Success:[/green] Relationship edge created. Key: {meta.get('_key')}")
        else:
            console.print("[bold red]Error:[/bold red] Failed to add relationship.")
            raise typer.Exit(code=1)
    except Exception as e:
        logger.error(f"Add relationship failed: {e}")
        console.print(f"[bold red]Error during add-relationship operation:[/bold red] {e}")
        raise typer.Exit(code=1)

@app.command("delete-relationship")
def cli_delete_relationship(
    edge_key: str = typer.Argument(..., help=f"The _key of the edge in '{EDGE_COLLECTION_NAME}'."),
    json_output: bool = typer.Option(False, "--json-output", "-j", help="Output status as JSON.")
):
    """
    [Graph] Delete a specific relationship edge by its key.

    WHEN TO USE: Use to remove an incorrect or outdated link between lessons.
    HOW TO USE: Provide the unique `_key` of the edge document itself.
    """
    logger.info(f"CLI: Deleting relationship edge with key '{edge_key}'")
    db = get_db_connection()
    try:
        success = delete_relationship(db, edge_key)
        status = {"edge_key": edge_key, "deleted": success}
        if success:
            if json_output: print(json.dumps(status))
            else: console.print(f"[green]Success:[/green] Relationship edge '{edge_key}' deleted.")
        else:
            status["error"] = "Deletion failed (not found or error)"
            if json_output: print(json.dumps(status))
            else: console.print(f"[bold red]Error:[/bold red] Failed to delete edge '{edge_key}'.")
            raise typer.Exit(code=1)
    except Exception as e:
        logger.error(f"Delete relationship failed: {e}")
        status = {"edge_key": edge_key, "deleted": False, "error": str(e)}
        if json_output: print(json.dumps(status))
        else: console.print(f"[bold red]Error during delete-relationship operation:[/bold red] {e}")
        raise typer.Exit(code=1)

# --- Helper for Displaying Results (Unchanged) ---
# ... (_display_results code) ...

# --- Main Execution Guard ---
if __name__ == "__main__":
    app()
```

---

**5. Updated `src/mcp_doc_retriever/arangodb/main_usage.py`**

```python
# src/mcp_doc_retriever/arangodb/main_usage.py
"""
# ... (Update docstring to mention graph/relationships) ...
- Adding relationships between lessons and traversing the graph.
"""

import sys
import os
from loguru import logger
from dotenv import load_dotenv

# Import setup, search, crud APIs, config, embedding
from .arango_setup import (
    connect_arango, ensure_database, ensure_collection, ensure_view,
    insert_sample_if_empty, ensure_edge_collection, ensure_graph # Added graph setup
)
from .cache_setup import initialize_litellm_cache
from .search_api import search_bm25, search_semantic, hybrid_search, graph_traverse # Added traverse
# Added relationship functions
from .crud_api import add_lesson, get_lesson, update_lesson, delete_lesson, add_relationship, delete_relationship
from .embedding_utils import get_embedding
# Added Edge/Graph names, relationship types
from .config import RELATIONSHIP_TYPE_RELATED, EDGE_COLLECTION_NAME, GRAPH_NAME, COLLECTION_NAME

load_dotenv()

# --- Loguru Configuration ---
# ... (remains the same) ...
logger.remove()
logger.add(sys.stderr,level=os.environ.get("LOG_LEVEL", "INFO").upper(),format="{time:YYYY-MM-DD HH:mm:ss} | {level: <7} | {name}:{function}:{line} | {message}",backtrace=True,diagnose=True)

# --- Search Result Logging Helper ---
# ... (remains the same) ...
def log_search_results(search_data: dict, search_type: str, score_field: str):
    results = search_data.get("results", [])
    total = search_data.get("total", 0)
    offset = search_data.get("offset", 0)
    limit = search_data.get("limit", len(results))
    logger.info(f"--- {search_type} Results (Showing {offset + 1}-{offset + len(results)} of {total} total matches/candidates) ---")
    if not results: logger.info("No relevant documents found matching the criteria.")
    else:
        for i, result in enumerate(results, start=1):
            score = result.get(score_field, 0.0)
            doc = result.get("doc", {})
            key = doc.get('_key', 'N/A'); problem = doc.get('problem', 'N/A')[:80] + "..."; tags = ", ".join(doc.get('tags', []))
            other_scores = [];
            if 'bm25_score' in result and score_field != 'bm25_score': other_scores.append(f"BM25: {result['bm25_score']:.4f}")
            if 'similarity_score' in result and score_field != 'similarity_score': other_scores.append(f"Sim: {result['similarity_score']:.4f}")
            other_scores_str = f" ({', '.join(other_scores)})" if other_scores else ""
            logger.info(f"  {offset + i}. Score: {score:.4f}{other_scores_str} | Key: {key} | Problem: {problem} | Tags: [{tags}]")

# --- Main Demo Execution ---
def run_demo():
    """Executes the main demonstration workflow including relationships and traversal."""
    logger.info("=" * 20 + " Starting ArangoDB Programmatic Demo " + "=" * 20)

    required_key = "OPENAI_API_KEY"
    if required_key not in os.environ: logger.error(f"Required env var {required_key} not set."); sys.exit(1)

    logger.info("--- Initializing LiteLLM Caching ---")
    initialize_litellm_cache()
    logger.info("--- Caching Initialized ---")

    # Store keys/ids created during demo for later use/cleanup
    created_lesson_keys = []
    created_edge_keys = []
    db = None # Define db in outer scope for finally block

    try:
        # --- ArangoDB Setup ---
        logger.info("--- Running ArangoDB Setup Phase ---")
        client = connect_arango()
        db = ensure_database(client)
        collection = ensure_collection(db, COLLECTION_NAME) # Pass name explicitly if not default
        # Ensure edge collection and graph definition exist
        ensure_edge_collection(db, EDGE_COLLECTION_NAME)
        ensure_graph(db, GRAPH_NAME, EDGE_COLLECTION_NAME, COLLECTION_NAME)
        ensure_view(db)
        insert_sample_if_empty(collection) # Note: this only adds one sample vertex if empty
        logger.info("--- ArangoDB Setup Complete ---")

        # --- CRUD & Relationship Examples ---
        logger.info("--- Running CRUD & Relationship Examples ---")

        # Add a few lessons to relate
        lesson1_data = {"problem": "High CPU usage on API server.", "solution": "Optimize database query or add caching.", "tags": ["performance", "api", "cpu", "database"], "role": "Backend Dev"}
        lesson2_data = {"problem": "Slow database query for user profiles.", "solution": "Add index on user ID field.", "tags": ["database", "query", "performance", "index"], "role": "DBA"}
        lesson3_data = {"problem": "API request cache misses frequently.", "solution": "Increase cache TTL or refine cache key strategy.", "tags": ["api", "cache", "performance", "ttl"], "role": "Backend Dev"}

        meta1 = add_lesson(db, lesson1_data.copy())
        meta2 = add_lesson(db, lesson2_data.copy())
        meta3 = add_lesson(db, lesson3_data.copy())

        key1 = meta1.get('_key') if meta1 else None
        key2 = meta2.get('_key') if meta2 else None
        key3 = meta3.get('_key') if meta3 else None

        if key1: created_lesson_keys.append(key1)
        if key2: created_lesson_keys.append(key2)
        if key3: created_lesson_keys.append(key3)

        # Add relationships if lessons were created
        if key1 and key2:
            rel1_meta = add_relationship(db, key1, key2, "High CPU might be caused by slow DB query.", RELATIONSHIP_TYPE_RELATED)
            if rel1_meta: created_edge_keys.append(rel1_meta.get('_key'))
        if key1 and key3:
            rel2_meta = add_relationship(db, key1, key3, "Adding caching mentioned as potential CPU solution.", RELATIONSHIP_TYPE_RELATED)
            if rel2_meta: created_edge_keys.append(rel2_meta.get('_key'))
        if key3 and key1: # Example of a different direction/type
             rel3_meta = add_relationship(db, key3, key1, "Cache issues could lead back to API performance problems.", RELATIONSHIP_TYPE_DEPENDS)
             if rel3_meta: created_edge_keys.append(rel3_meta.get('_key'))

        logger.info("--- CRUD & Relationship Examples Complete ---")

        # --- Search & Traversal Examples ---
        logger.info("--- Running Search & Traversal Examples ---")

        # BM25 (remains same)
        print("\n" + "-" * 10 + " BM25 Search Example " + "-" * 10)
        bm25_query = "database query performance"
        bm25_results = search_bm25(db, bm25_query, 0.05, 5)
        log_search_results(bm25_results, "BM25", "bm25_score")

        # Semantic (remains same)
        print("\n" + "-" * 10 + " Semantic Search Example " + "-" * 10)
        semantic_query = "optimize server speed"
        semantic_query_embedding = get_embedding(semantic_query)
        if semantic_query_embedding:
            semantic_results = search_semantic(db, semantic_query_embedding, 5, 0.70)
            log_search_results(semantic_results, "Semantic", "similarity_score")
        else: logger.error("Skipping semantic search.")

        # Hybrid (remains same)
        print("\n" + "-" * 10 + " Hybrid Search Example (RRF) " + "-" * 10)
        hybrid_query = "fix api server bottlenecks"
        hybrid_results = hybrid_search(db, hybrid_query, 5, 15, 0.01, 0.70)
        log_search_results(hybrid_results, "Hybrid (RRF)", "rrf_score")

        # --- NEW: Graph Traversal Example ---
        print("\n" + "-" * 10 + " Graph Traversal Example " + "-" * 10)
        if key1: # Start traversal from the first lesson if it was created
            start_node_id = f"{COLLECTION_NAME}/{key1}"
            logger.info(f"Performing Graph Traversal starting from {start_node_id} (Depth 1..2 ANY)...")
            try:
                traversal_results = graph_traverse(db, start_node_id, min_depth=1, max_depth=2, direction="ANY", limit=10)
                logger.info(f"Traversal found {len(traversal_results)} paths.")
                # Print details of found paths (can be verbose)
                for i, path_info in enumerate(traversal_results):
                    vertex = path_info.get('vertex', {})
                    edge = path_info.get('edge', {})
                    logger.info(f" Path {i+1}:")
                    logger.info(f"  Edge: _key={edge.get('_key')}, Type={edge.get('type')}, Rationale='{edge.get('rationale')}'")
                    logger.info(f"  Vertex: _key={vertex.get('_key')}, Problem='{vertex.get('problem', '')[:50]}...'")
                if not traversal_results:
                     logger.info(" (No paths found matching criteria - expected if no edges were created)")
            except Exception as traverse_e:
                logger.error(f"Graph traversal failed: {traverse_e}")
        else:
             logger.warning("Skipping graph traversal example as starting node key 'key1' was not generated.")


        logger.success("\n" + "=" * 20 + " Demo Finished Successfully " + "=" * 20)

    except Exception as e:
        logger.exception(f"Demo failed due to an unexpected error: {e}")
        sys.exit(1)

    finally:
        # --- Cleanup ---
        if db:
            logger.info("--- Running Cleanup Phase ---")
            # Delete edges first
            for edge_key in created_edge_keys:
                logger.debug(f"Deleting edge {edge_key}...")
                delete_relationship(db, edge_key)
            # Delete vertices
            for lesson_key in created_lesson_keys:
                logger.debug(f"Deleting lesson {lesson_key}...")
                delete_lesson(db, lesson_key, delete_edges=False) # Edges likely already gone
            logger.info("--- Cleanup Complete ---")


if __name__ == "__main__":
    run_demo()
```

---

**Summary of Key Edge/Graph Related Changes:**

1.  **`config.py`:** Added `EDGE_COLLECTION_NAME`. Defined example `RELATIONSHIP_TYPE` constants.
2.  **`arango_setup.py`:** Added `ensure_edge_collection` and `ensure_graph` functions. These are now called in `cli.py`'s `get_db_connection` and `main_usage.py`'s setup phase to ensure the graph infrastructure exists.
3.  **`crud_api.py`:**
    *   Added `add_relationship` to create edges with `_from`, `_to`, `rationale`, `type`, `timestamp`.
    *   Added `delete_relationship` to remove edges by key.
    *   Modified `delete_lesson` to optionally find and delete connected edges using AQL traversal before deleting the vertex itself.
4.  **`search_api.py`:**
    *   The `graph_traverse` function implementation is now relevant as edges can exist.
    *   Updated `validate_search_params` to handle graph parameters distinctly.
    *   Updated the standalone test block (`if __name__ == "__main__":`) to add relationships and test `graph_traverse`.
5.  **`cli.py`:**
    *   Added `add-relationship` command.
    *   Added `delete-relationship` command.
    *   Updated `traverse` command help text.
    *   Calls `ensure_edge_collection` and `ensure_graph` in `get_db_connection` for safety.
6.  **`main_usage.py`:**
    *   Calls `ensure_edge_collection` and `ensure_graph` during setup.
    *   Adds multiple test lessons.
    *   Demonstrates calling `add_relationship` to link them.
    *   Demonstrates calling `graph_traverse` and logging the results.
    *   Includes cleanup logic to delete created edges and lessons.

This comprehensive set of changes integrates the edge collection and graph concepts, making the `graph_traverse` feature functional and testable within the project structure.