# src/mcp_doc_retriever/arangodb/cli.py
"""
Command-Line Interface (CLI) for ArangoDB Lessons Learned Document Retriever

**Agent Instructions:**

This CLI provides command-line access to search and manage ("CRUD") documents
within the 'lessons_learned' collection in an ArangoDB database. Use this
interface to interact with the knowledge base programmatically via shell commands.

**Prerequisites:**

Ensure the following environment variables are set before executing commands:
- `ARANGO_HOST`: URL of the ArangoDB instance (e.g., "http://localhost:8529").
- `ARANGO_USER`: ArangoDB username (e.g., "root").
- `ARANGO_PASSWORD`: ArangoDB password.
- `ARANGO_DB_NAME`: Name of the target database (e.g., "doc_retriever").
- API key for the configured embedding model (e.g., `OPENAI_API_KEY` if using OpenAI).
- **Optional:** `REDIS_HOST`, `REDIS_PORT`, `REDIS_PASSWORD` for Redis caching.
- **Optional:** `LOG_LEVEL` (e.g., DEBUG, INFO, WARNING) to control verbosity.

**Invocation:**

Execute commands using the python module execution flag `-m`:
`python -m src.mcp_doc_retriever.arangodb.cli [OPTIONS] COMMAND [ARGS]...`

**Available Commands:**

--- Search Commands ---

1.  `bm25`: Perform BM25 keyword search.
    -   ARGUMENTS:
        -   `QUERY`: (Required) The search query text string.
    -   OPTIONS:
        -   `--threshold` / `-th`: (Optional, float, default: 0.1) Minimum BM25 score.
        -   `--top-n` / `-n`: (Optional, int, default: 5) Number of results.
        -   `--offset` / `-o`: (Optional, int, default: 0) Pagination offset.
        -   `--tags` / `-t`: (Optional, str) Comma-separated list of tags to filter by (no spaces). Example: "tag1,tag2"
    -   OUTPUT: Prints a table of results to stdout on success.

2.  `semantic`: Perform semantic vector similarity search.
    -   ARGUMENTS:
        -   `QUERY`: (Required) The search query text string (will be embedded).
    -   OPTIONS:
        -   `--threshold` / `-th`: (Optional, float, default: 0.75) Minimum similarity score (0.0-1.0).
        -   `--top-n` / `-n`: (Optional, int, default: 5) Number of results.
        -   `--tags` / `-t`: (Optional, str) Comma-separated list of tags. Example: "tag1,tag2"
    -   OUTPUT: Prints a table of results to stdout on success.

3.  `hybrid`: Perform hybrid search (BM25 + Semantic with RRF re-ranking).
    -   ARGUMENTS:
        -   `QUERY`: (Required) The search query text string.
    -   OPTIONS:
        -   `--top-n` / `-n`: (Optional, int, default: 5) Final number of results.
        -   `--initial-k` / `-k`: (Optional, int, default: 20) Number of candidates from BM25/Semantic.
        -   `--bm25-th`: (Optional, float, default: 0.01) BM25 candidate threshold.
        -   `--sim-th`: (Optional, float, default: 0.70) Similarity candidate threshold.
        -   `--tags` / `-t`: (Optional, str) Comma-separated list of tags. Example: "tag1,tag2"
    -   OUTPUT: Prints a table of ranked results to stdout on success.

--- CRUD Commands ---

4.  `add`: Add a new lesson document.
    -   OPTIONS:
        -   `--data` / `-d`: (Required) A JSON string representing the lesson document.
                     **Important:** Ensure the JSON string is properly quoted/escaped for the shell.
                     Example: `'{"problem": "New issue", "solution": "New fix", "tags": ["cli", "test"]}'`
                     Must contain at least "problem" and "solution" keys.
                     Embedding will be generated automatically.
    -   OUTPUT: Prints JSON metadata (_key, _id, _rev) of the new document to stdout on success.

5.  `get`: Retrieve a lesson document by its _key.
    -   ARGUMENTS:
        -   `KEY`: (Required) The `_key` of the document to retrieve.
    -   OUTPUT: Prints the full JSON document to stdout if found. Prints a "Not Found" message otherwise.

6.  `update`: Update fields of an existing lesson document.
    -   ARGUMENTS:
        -   `KEY`: (Required) The `_key` of the document to update.
    -   OPTIONS:
        -   `--data` / `-d`: (Required) A JSON string containing ONLY the fields to update.
                     Example: `'{"severity": "HIGH", "context": "Updated context"}'`
                     If embedding-relevant fields (problem, solution, context, example) are updated,
                     the embedding will be regenerated automatically.
    -   OUTPUT: Prints JSON metadata (_key, _id, _rev, _old_rev) of the updated document to stdout on success.

7.  `delete`: Delete a lesson document by its _key.
    -   ARGUMENTS:
        -   `KEY`: (Required) The `_key` of the document to delete.
    -   OUTPUT: Prints a success message to stdout on success.

--- Graph Commands ---
8.  `traverse`: Explore relationships between lessons using graph traversal.
    -   ARGUMENTS:
        -   `START_NODE_ID`: (Required) The full `_id` of the starting lesson (e.g., "lessons_learned/12345").
    -   OPTIONS:
        -   `--graph-name`: (Optional, str, default: from config) Name of the graph.
        -   `--min-depth`: (Optional, int, default: 1) Minimum traversal depth.
        -   `--max-depth`: (Optional, int, default: 1) Maximum traversal depth.
        -   `--direction`: (Optional, str, default: OUTBOUND) Direction (OUTBOUND, INBOUND, ANY).
        -   `--limit`: (Optional, int) Max number of paths to return.
        -   `--json-output`: (Optional, bool, default: False) Output results as JSON.
    -   OUTPUT: Prints traversal results (vertex, edge, path) as JSON to stdout if --json-output is used or if results are found. Otherwise, prints a summary message.

**Error Handling:**

- Errors during execution will be printed to stderr.
- More detailed logs might be available depending on the `LOG_LEVEL`.
- Commands typically exit with code 0 on success and 1 on failure.

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

# ... (Imports remain the same) ...
from .arango_setup import connect_arango, ensure_database
from .search_api import search_bm25, search_semantic, hybrid_search, graph_traverse
from .crud_api import add_lesson, get_lesson, update_lesson, delete_lesson
from .embedding_utils import get_embedding
from .config import ARANGO_DB_NAME, GRAPH_NAME
from .initialize_litellm_cache import initialize_litellm_cache

# --- Typer App Initialization ---
app = typer.Typer(name="arangodb-search-cli", help=__doc__, add_completion=False)

# --- Rich Console ---
console = Console()


# --- Global State / Context & Logging Setup ---
@app.callback()
def main_callback(
    log_level: str = typer.Option(
        os.environ.get("LOG_LEVEL", "INFO").upper(),
        "--log-level",
        "-l",
        help="Set logging level.",
        envvar="LOG_LEVEL",
    ),
):
    """Main callback to configure logging for the CLI."""
    logger.remove()
    logger.add(
        sys.stderr,
        level=log_level,
        format="{time:HH:mm:ss} | {level: <7} | {message}",
        backtrace=False,
        diagnose=False,
    )
    logger.debug("Initializing LiteLLM Caching for CLI session...")
    initialize_litellm_cache()
    logger.debug("LiteLLM Caching initialized.")


# --- Utility ---
def get_db_connection():
    """Helper to connect and get DB object, handling errors."""
    try:
        client = connect_arango()
        db = ensure_database(client)
        return db
    except Exception as e:
        logger.error(f"DB connection failed: {e}")
        console.print(f"[bold red]Error:[/bold red] Could not connect to ArangoDB.")
        raise typer.Exit(code=1)


# --- Search Commands ---


@app.command("bm25")
def cli_search_bm25(
    query: str = typer.Argument(..., help="The search query text."),
    threshold: float = typer.Option(
        0.1, "--threshold", "-th", help="Minimum BM25 score."
    ),
    top_n: int = typer.Option(5, "--top-n", "-n", help="Number of results to return."),
    offset: int = typer.Option(0, "--offset", "-o", help="Offset for pagination."),
    tags: Optional[str] = typer.Option(
        None, "--tags", "-t", help="Comma-separated list of tags (no spaces)."
    ),
):
    """
    [Search] Find documents based on keyword relevance (BM25 algorithm).

    WHEN TO USE: Use when you need to find documents matching specific keywords
                 or terms present in the query text. Good for lexical matching.
    HOW TO USE: Provide the query text. Optionally refine with score threshold,
                result count, pagination offset, or tag filtering.
    """
    logger.info(f"CLI: Performing BM25 search for '{query}'")
    db = get_db_connection()
    tag_list = [tag.strip() for tag in tags.split(",")] if tags else None
    try:
        results_data = search_bm25(db, query, threshold, top_n, offset, tag_list)
        _display_results(results_data, "BM25", "bm25_score")
    except Exception as e:
        logger.error(f"BM25 search failed: {e}")
        console.print(f"[bold red]Error during BM25 search:[/bold red] {e}")
        raise typer.Exit(code=1)


@app.command("semantic")
def cli_search_semantic(
    query: str = typer.Argument(..., help="The search query text."),
    threshold: float = typer.Option(
        0.75, "--threshold", "-th", help="Minimum similarity score (0.0-1.0)."
    ),
    top_n: int = typer.Option(5, "--top-n", "-n", help="Number of results to return."),
    tags: Optional[str] = typer.Option(
        None, "--tags", "-t", help="Comma-separated list of tags (no spaces)."
    ),
):
    """
    [Search] Find documents based on conceptual meaning (vector similarity).

    WHEN TO USE: Use when the exact keywords might be different, but the underlying
                 meaning or concept of the query should match the documents.
                 Good for finding semantically related content.
    HOW TO USE: Provide the query text (it will be converted to an embedding).
                Optionally refine with similarity threshold, result count, or tags.
    """
    logger.info(f"CLI: Performing Semantic search for '{query}'")
    db = get_db_connection()
    tag_list = [tag.strip() for tag in tags.split(",")] if tags else None

    logger.debug("Generating query embedding...")
    query_embedding = get_embedding(query)
    if not query_embedding:
        console.print(
            "[bold red]Error:[/bold red] Failed to generate embedding for the query."
        )
        raise typer.Exit(code=1)
    logger.debug("Query embedding generated.")

    try:
        results_data = search_semantic(db, query_embedding, top_n, threshold, tag_list)
        _display_results(results_data, "Semantic", "similarity_score")
    except Exception as e:
        logger.error(f"Semantic search failed: {e}")
        console.print(f"[bold red]Error during Semantic search:[/bold red] {e}")
        raise typer.Exit(code=1)


@app.command("hybrid")
def cli_search_hybrid(
    query: str = typer.Argument(..., help="The search query text."),
    top_n: int = typer.Option(5, "--top-n", "-n", help="Final number of results."),
    initial_k: int = typer.Option(
        20, "--initial-k", "-k", help="Candidates from each method."
    ),
    bm25_threshold: float = typer.Option(
        0.01, "--bm25-th", help="BM25 candidate threshold."
    ),
    sim_threshold: float = typer.Option(
        0.70, "--sim-th", help="Similarity candidate threshold."
    ),
    tags: Optional[str] = typer.Option(
        None, "--tags", "-t", help="Comma-separated list of tags (no spaces)."
    ),
):
    """
    [Search] Combine keyword (BM25) and semantic search results using RRF.

    WHEN TO USE: Use for the best general-purpose relevance, leveraging both
                 keyword matching and conceptual understanding. Often provides
                 more robust results than either method alone.
    HOW TO USE: Provide the query text. Optionally adjust the number of final
                results (`top_n`), initial candidates (`initial_k`), candidate
                thresholds, or add tag filters.
    """
    logger.info(f"CLI: Performing Hybrid search for '{query}'")
    db = get_db_connection()
    tag_list = [tag.strip() for tag in tags.split(',')] if tags else None
    try:
        results_data = hybrid_search(
            db, query, top_n, initial_k, bm25_threshold, sim_threshold, tag_list
        )
        _display_results(results_data, "Hybrid (RRF)", "rrf_score")
    except Exception as e:
        logger.error(f"Hybrid search failed: {e}")
        console.print(f"[bold red]Error during Hybrid search:[/bold red] {e}")
        raise typer.Exit(code=1)

@app.command("traverse")
def cli_graph_traverse(
    start_node_id: str = typer.Argument(
        ..., help="Start node _id (e.g., 'lessons_learned/12345')."
    ),
    graph_name: str = typer.Option(
        GRAPH_NAME, "--graph-name", "-g", help="Name of the graph to traverse."
    ),
    min_depth: int = typer.Option(1, "--min-depth", help="Minimum traversal depth."),
    max_depth: int = typer.Option(1, "--max-depth", help="Maximum traversal depth."),
    direction: str = typer.Option(
        "OUTBOUND", "--direction", "-dir", help="Direction: OUTBOUND, INBOUND, or ANY."
    ),
    limit: Optional[int] = typer.Option(
        None, "--limit", "-lim", help="Maximum number of paths."
    ),
    json_output: bool = typer.Option(
        True,
        "--json-output",
        "-j",
        help="Output results as JSON (default for traverse).",
    ),  # Default True
):
    """
    [Graph] Explore relationships between lessons via graph traversal.

    WHEN TO USE: Use to understand connections, dependencies, or related concepts
                 starting from a specific lesson document. Requires a pre-defined graph.
    HOW TO USE: Provide the full `_id` of the starting lesson. Adjust depth,
                direction, limit, or graph name as needed. Output is typically JSON.
    """
    logger.info(f"CLI: Performing graph traversal from '{start_node_id}'")
    db = get_db_connection()
    try:
        # Validate direction input further
        if direction.upper() not in ["OUTBOUND", "INBOUND", "ANY"]:
            console.print(
                f"[bold red]Error:[/bold red] Invalid direction '{direction}'. Must be OUTBOUND, INBOUND, or ANY."
            )
            raise typer.Exit(code=1)

        results_data = graph_traverse(
            db, start_node_id, graph_name, min_depth, max_depth, direction, limit
        )

        if json_output:
            # Pretty print JSON using Rich for potentially complex graph data
            if results_data:
                console.print(JSON(json.dumps(results_data, indent=2)))
            else:
                print("[]")  # Print empty JSON list if no results
        else:
            # Non-JSON output for traverse might just be a summary
            console.print(
                f"[green]Traversal complete.[/green] Found {len(results_data)} paths. Use --json-output to view details."
            )

    except Exception as e:
        logger.error(f"Graph traversal failed: {e}")
        console.print(f"[bold red]Error during graph traversal:[/bold red] {e}")
        raise typer.Exit(code=1)


# --- CRUD Commands ---


@app.command("add")
def cli_add_lesson(
    data: str = typer.Option(..., "--data", "-d", help="Lesson data as JSON string."),
    json_output: bool = typer.Option(
        False, "--json-output", "-j", help="Output metadata as JSON."
    ),  # Added flag
):
    """[CRUD] Add a new lesson document."""
    logger.info("CLI: Adding new lesson.")
    db = get_db_connection()
    try:
        lesson_data = json.loads(data)
    except json.JSONDecodeError as e:
        console.print(f"[bold red]Error:[/bold red] Invalid JSON for --data: {e}")
        raise typer.Exit(code=1)

    try:
        meta = add_lesson(db, lesson_data)
        if meta:
            if json_output:
                print(json.dumps(meta))
            else:
                console.print(
                    f"[green]Success:[/green] Lesson added successfully. Key: {meta.get('_key')}"
                )
        else:
            console.print("[bold red]Error:[/bold red] Failed to add lesson.")
            raise typer.Exit(code=1)
    except Exception as e:
        logger.error(f"Add lesson failed: {e}")
        console.print(f"[bold red]Error during add operation:[/bold red] {e}")
        raise typer.Exit(code=1)


@app.command("get")
def cli_get_lesson(
    key: str = typer.Argument(..., help="The _key of the lesson."),
    json_output: bool = typer.Option(
        False, "--json-output", "-j", help="Output document as JSON."
    ),  # Added flag
):
    """[CRUD] Retrieve a specific lesson document by key."""
    logger.info(f"CLI: Getting lesson with key '{key}'")
    db = get_db_connection()
    try:
        doc = get_lesson(db, key)
        if doc:
            if json_output:
                print(json.dumps(doc, indent=2))
            else:
                console.print(f"[green]Lesson Found:[/green] _key={key}")
                console.print(
                    JSON(json.dumps(doc, indent=2))
                )  # Rich JSON for human view
        else:
            # Output consistent structure even for not found in JSON mode
            if json_output:
                print(json.dumps({"error": "Not Found", "key": key}))
            else:
                console.print(
                    f"[yellow]Not Found:[/yellow] No lesson found with key '{key}'."
                )
    except Exception as e:
        logger.error(f"Get lesson failed: {e}")
        # Output JSON error if requested
        if json_output:
            print(json.dumps({"error": str(e), "key": key}))
        else:
            console.print(f"[bold red]Error during get operation:[/bold red] {e}")
        raise typer.Exit(code=1)


@app.command("update")
def cli_update_lesson(
    key: str = typer.Argument(..., help="The _key of the lesson."),
    data: str = typer.Option(
        ..., "--data", "-d", help="Fields to update as JSON string."
    ),
    json_output: bool = typer.Option(
        False, "--json-output", "-j", help="Output metadata as JSON."
    ),  # Added flag
):
    """[CRUD] Modify specific fields of an existing lesson."""
    logger.info(f"CLI: Updating lesson with key '{key}'")
    db = get_db_connection()
    try:
        update_data = json.loads(data)
    except json.JSONDecodeError as e:
        console.print(f"[bold red]Error:[/bold red] Invalid JSON for --data: {e}")
        raise typer.Exit(code=1)

    try:
        meta = update_lesson(db, key, update_data)
        if meta:
            if json_output:
                print(json.dumps(meta))
            else:
                console.print(f"[green]Success:[/green] Lesson updated successfully.")
        else:
            if json_output:
                print(json.dumps({"error": "Update failed", "key": key}))
            console.print(
                f"[bold red]Error:[/bold red] Failed to update lesson '{key}'."
            )
            raise typer.Exit(code=1)
    except Exception as e:
        logger.error(f"Update lesson failed: {e}")
        if json_output:
            print(json.dumps({"error": str(e), "key": key}))
        else:
            console.print(f"[bold red]Error during update operation:[/bold red] {e}")
        raise typer.Exit(code=1)


@app.command("delete")
def cli_delete_lesson(
    key: str = typer.Argument(..., help="The _key of the lesson."),
    json_output: bool = typer.Option(
        False, "--json-output", "-j", help="Output status as JSON."
    ),  # Added flag
    # yes: bool = typer.Option(False, "--yes", "-y", help="Confirm deletion without prompt.") # Example confirmation
):
    """[CRUD] Permanently remove a lesson document by key."""
    logger.info(f"CLI: Deleting lesson with key '{key}'")
    # if not yes:
    #    typer.confirm(f"Delete lesson '{key}'?", abort=True)
    db = get_db_connection()
    try:
        success = delete_lesson(db, key)
        status = {"key": key, "deleted": success}
        if success:
            if json_output:
                print(json.dumps(status))
            else:
                console.print(f"[green]Success:[/green] Lesson '{key}' deleted.")
        else:
            status["error"] = "Deletion failed (not found or error)"
            if json_output:
                print(json.dumps(status))
            else:
                console.print(
                    f"[bold red]Error:[/bold red] Failed to delete lesson '{key}'."
                )
            raise typer.Exit(code=1)
    except Exception as e:
        logger.error(f"Delete lesson failed: {e}")
        status = {"key": key, "deleted": False, "error": str(e)}
        if json_output:
            print(json.dumps(status))
        else:
            console.print(f"[bold red]Error during delete operation:[/bold red] {e}")
        raise typer.Exit(code=1)


# --- Helper for Displaying Results (Human Readable) ---
def _display_results(search_data: dict, search_type: str, score_field: str):
    """Uses Rich to display search results in a table (for human consumption)."""
    # ... (Implementation remains the same as previous version) ...
    results = search_data.get("results", [])
    total = search_data.get("total", 0)
    offset = search_data.get("offset", 0)
    console.print(
        f"\n[bold blue]--- {search_type} Results (Showing {len(results)} of {total} total matches/candidates) ---[/bold blue]"
    )
    if not results:
        console.print(
            "[yellow]No relevant documents found matching the criteria.[/yellow]"
        )
        return
    table = Table(show_header=True, header_style="bold magenta", expand=True)
    table.add_column("#", style="dim", width=3, no_wrap=True)
    table.add_column(
        f"Score ({score_field.split('_')[0].upper()})", justify="right", width=10
    )
    table.add_column("Key", style="cyan", no_wrap=True, width=38)
    table.add_column("Problem (Preview)", style="green", overflow="fold")
    table.add_column("Tags", style="yellow", overflow="fold")
    for i, result in enumerate(results, start=1):
        score = result.get(score_field, 0.0)
        doc = result.get("doc", {})
        key = doc.get("_key", "N/A")
        problem = doc.get("problem", "N/A")
        tags = ", ".join(doc.get("tags", []))
        table.add_row(
            str(offset + i), f"{score:.4f}", key, problem.replace("\n", " "), tags
        )
    console.print(table)


# --- Main Execution Guard ---
if __name__ == "__main__":
    app()