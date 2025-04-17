# src/mcp_doc_retriever/arangodb/search_api/bm25.py
import sys
import os
import uuid
from typing import List, Dict, Any, Optional

from loguru import logger
import textwrap
from arango.database import StandardDatabase
from arango.exceptions import AQLQueryExecuteError, ArangoServerError

# Import config variables and embedding utils
# --- Configuration and Imports ---
try:
    # Try relative imports first for package structure
    from mcp_doc_retriever.arangodb.config import (
        SEARCH_FIELDS,
        ALL_DATA_FIELDS_PREVIEW,
        TEXT_ANALYZER,
        TAG_ANALYZER,
        VIEW_NAME as BASE_VIEW_NAME,  # Rename to avoid conflict if testing modifies it
        COLLECTION_NAME as BASE_COLLECTION_NAME,  # Rename for clarity
        GRAPH_NAME,
    )
    from mcp_doc_retriever.arangodb.embedding_utils import get_embedding
    from mcp_doc_retriever.arangodb.search_api.utils import validate_search_params

except ImportError:
    # Fallback for script execution or different structure
    _root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
    if _root not in sys.path:
        sys.path.insert(0, _root)
    from mcp_doc_retriever.arangodb.config import (  # type: ignore
        SEARCH_FIELDS,
        ALL_DATA_FIELDS_PREVIEW,
        TEXT_ANALYZER,
        TAG_ANALYZER,
        VIEW_NAME as BASE_VIEW_NAME,
        COLLECTION_NAME as BASE_COLLECTION_NAME,
        GRAPH_NAME,
    )
    from mcp_doc_retriever.arangodb.embedding_utils import get_embedding  # type: ignore
    from mcp_doc_retriever.arangodb.search_api.utils import validate_search_params  # type: ignore

    logger.warning("Using fallback imports for config/utils in bm25.py")


def search_bm25(
    db: StandardDatabase,
    search_text: str,
    bm25_threshold: float = 0.1,
    top_n: int = 5,
    offset: int = 0,
    tags: Optional[List[str]] = None,
    view_name: str = BASE_VIEW_NAME,
) -> Dict[str, Any]:
    """
    Performs BM25 keyword search with pagination, tag filtering, and total count,
    ensuring each returned doc includes its _key.
    """
    search_uuid = str(uuid.uuid4())[:8]
    with logger.contextualize(
        action="search_bm25", search_id=search_uuid, view=view_name
    ):
        validate_search_params(search_text, bm25_threshold, top_n, offset, tags, None)

        logger.info(
            f"Executing BM25 search: text='{search_text}', tags={tags}, "
            f"th={bm25_threshold}, top_n={top_n}, offset={offset}"
        )

        # Build SEARCH conditions
        safe_search_fields = [
            f for f in SEARCH_FIELDS if isinstance(f, str) and f.isidentifier()
        ]
        if not safe_search_fields:
            raise ValueError("SEARCH_FIELDS configuration is empty or invalid.")

        search_field_conditions = " OR ".join(
            f'ANALYZER(doc.`{f}` IN TOKENS(@search_text, "{TEXT_ANALYZER}"), "{TEXT_ANALYZER}")'
            for f in safe_search_fields
        )

        # Tag filtering
        bind_vars: Dict[str, Any] = {
            "search_text": search_text,
            "bm25_threshold": bm25_threshold,
            "top_n": top_n,
            "offset": offset,
        }
        tag_filter_clause = ""
        if tags:
            tags = [str(t) for t in tags if t]
            if tags:
                tag_conds = " AND ".join(
                    f"@tag_{i} IN doc.tags" for i in range(len(tags))
                )
                tag_filter_clause = f"FILTER {tag_conds}"
                for i, t in enumerate(tags):
                    bind_vars[f"tag_{i}"] = t

        # Build KEEP + MERGE clause to include _key
        preview_fields = [
            f"'{f}'"
            for f in ALL_DATA_FIELDS_PREVIEW
            if isinstance(f, str) and f.isidentifier() and f != "_key"
        ]
        preview_list = ", ".join(preview_fields)
        keep_clause = (
            f"MERGE(KEEP(item.doc, {preview_list}), {{ _key: item.doc._key }})"
            if preview_list
            else "item.doc"
        )

        # Dedent the AQL for cleaner logging
        aql = textwrap.dedent(f"""
            LET matching_docs = (
              FOR doc IN {view_name}
                SEARCH {search_field_conditions}
                {tag_filter_clause}
                LET score = BM25(doc)
                FILTER score >= @bm25_threshold
                RETURN {{ doc: doc, score: score }}
            )
            LET total_count = LENGTH(matching_docs)

            LET paged_results = (
              FOR item IN matching_docs
                SORT item.score DESC
                LIMIT @offset, @top_n
                RETURN {{
                  doc: {keep_clause},
                  bm25_score: item.score
                }}
            )

            RETURN {{
              results: paged_results,
              total: total_count,
              offset: @offset,
              limit: @top_n
            }}
        """).strip()

        logger.debug(f"BM25 AQL (ID: {search_uuid}):\n{aql}")

        try:
            cursor = db.aql.execute(aql, bind_vars=bind_vars)
            data = cursor.next()
            results_list = data.get("results", [])
            total = data.get("total", 0)
            logger.success(
                f"BM25 OK (ID: {search_uuid}). Found {len(results_list)} results (total={total})"
            )
            return {
                "results": results_list,
                "total": total,
                "offset": data.get("offset", offset),
                "limit": data.get("limit", top_n),
            }
        except AQLQueryExecuteError as e:
            logger.error(
                f"BM25 AQL Error (ID: {search_uuid}): {e}\nQuery:\n{aql}\nBind Vars: {bind_vars}"
            )
            raise
        except Exception as e:
            logger.exception(f"BM25 Unexpected Error (ID: {search_uuid}): {e}")
            raise

def print_usage():
    """Prints usage instructions for the standalone test mode."""
    print(f"""
Usage: python {os.path.basename(__file__)}

This script runs a self-contained test for the search_bm25 function.
It requires ArangoDB connection details to be set via environment variables
(ARANGO_HOST, ARANGO_USER, ARANGO_PASSWORD, ARANGO_DB_NAME).

It will:
  - Create a temporary collection and ArangoSearch view.
  - Insert sample documents.
  - Run BM25 search tests (keyword, keyword+tag, no results).
  - Print PASS/FAIL status for each test.
  - Clean up the temporary collection and view.
""")


# --- Standalone Verification Harness (BM25) ---
if __name__ == "__main__":
    # Use alias for logger specific to this test harness
    from loguru import logger as _logger

    # --- Check for help flag ---
    if "--help" in sys.argv or "-h" in sys.argv:
        print_usage()
        sys.exit(0)

    # --- Test-specific Imports ---
    # Assuming these modules are importable based on project structure or sys.path manipulation
    try:
        from mcp_doc_retriever.arangodb.arango_setup import (
            connect_arango,
            ensure_database,
            ensure_collection,  # To create the test collection
            ensure_search_view,  # To create the test view
            # delete_collection_safely,  # Helper for cleanup
            # delete_view_safely,  # Helper for cleanup
        )
        # Use a simple CRUD for testing - direct insert/delete is often easiest
        # If crud_lessons exists and is simple:
        # from mcp_doc_retriever.arangodb.crud_lessons import add_lesson, delete_lesson
    except ImportError as e:
        _logger.error(f"Failed to import necessary modules for testing: {e}")
        _logger.error(
            "Please ensure the script is run from the project root or the necessary paths are in PYTHONPATH."
        )
        sys.exit(1)

    # --- Logger Configuration for Test Output ---
    _logger.remove()
    _logger.add(
        sys.stderr,
        level=os.environ.get("LOG_LEVEL", "INFO").upper(),
        format="{time:HH:mm:ss} | {level:<7} | {message}",
        colorize=True
    )
    _logger.info("=" * 20 + " Starting BM25 Standalone Test " + "=" * 20)

    # --- Test Setup ---
    run_id = str(uuid.uuid4())[:6]
    test_coll_name = f"{BASE_COLLECTION_NAME}_bm25_test_{run_id}"
    test_view_name = f"{BASE_VIEW_NAME}_bm25_test_{run_id}"
    db = None  # Initialize db to None for cleanup check
    passed = True  # Track overall test status

    try:
        # 1. Connect to ArangoDB
        client = connect_arango()
        if not client:
            sys.exit(1)  # connect_arango logs errors

        db = ensure_database(client)
        if not db:
            sys.exit(1)  # ensure_database logs errors

        # 2. Create Test Collection
        # _logger.info(f"Creating test collection: {test_coll_name}")
        
        # logger.info(f"Creating test collection: {test_coll_name}")
        #_logger.debug(f"→ ensure_collection(db, '{test_coll_name}')")
        # test_collection = ensure_collection(db, test_coll_name)
        
        # if not test_collection:
        #     raise RuntimeError("Failed to create test collection")
        
        try:
            test_collection = db.create_collection(test_coll_name, edge=False)
            _logger.success(f"Created test collection: {test_coll_name}")
        except ArangoServerError as e:
            # 1203 = not found / no permission, etc.
            _logger.warning(f"Could not create collection (maybe it exists?): {e}")
            test_collection = db.collection(test_coll_name)
                
                
        
        # 3. Create Test ArangoSearch View for BM25
        _logger.info(f"Creating test view: {test_view_name}")
        # Ensure the view is configured correctly for BM25 on SEARCH_FIELDS
        # view_created = ensure_search_view(
        #     db,
        #     view_name=test_view_name,
        #     collection_name=test_coll_name,  # Link view to the test collection
        #     analyzer=TEXT_ANALYZER,
        #     search_fields=SEARCH_FIELDS,
        #     stored_values_fields=ALL_DATA_FIELDS_PREVIEW,
        #     # Add specific BM25 parameters if needed by ensure_search_view
        #     # Example: primary_sort_bm25={'field': 'your_primary_field', 'asc': False}
        # )
        view_created = ensure_search_view(
            db,
            view_name=test_view_name,
            collection_name=test_coll_name,
        )
        if not view_created:
            raise RuntimeError("Failed to create test view")
        _logger.info(
            f"Using test collection '{test_coll_name}' and view '{test_view_name}'"
        )

        # 4. Insert Test Data directly into the test collection
        _logger.info("Inserting test documents...")
        TEST_DOCS = [
            {
                "_key": f"bm25_doc1_{run_id}",
                "problem": f"Python script has JSON decoding failure {run_id}",
                "solution": "Validate JSON input, handle exceptions gracefully.",
                "tags": ["python", "json", "debug", "script"],
                "context": "Occurs with malformed API responses.",
            },
            {
                "_key": f"bm25_doc2_{run_id}",
                "problem": f"Shell script argument parsing error {run_id}",
                "solution": "Use 'getopts' or check '$#' for argument count.",
                "tags": ["shell", "script", "arguments", "posix"],
                "context": "Needed for robust command-line tools.",
            },
            {
                "_key": f"bm25_doc3_{run_id}",
                "problem": f"Another Python issue, this time with encoding in JSON {run_id}",
                "solution": "Specify UTF-8 encoding during file read/write or JSON dumps/loads.",
                "tags": ["python", "json", "encoding", "utf8"],
                "context": "Common problem when dealing with external data sources.",
            },
            {
                "_key": f"bm25_doc4_{run_id}",
                "problem": f"Database connection timeout {run_id}",
                "solution": "Increase timeout settings, check network, use connection pooling.",
                "tags": ["database", "network", "timeout", "python"],
                "context": "Intermittent failures during peak load.",
            },
        ]
        inserted_keys = []
        coll = db.collection(test_coll_name)
        for doc in TEST_DOCS:
            try:
                meta = coll.insert(doc, overwrite=True)
                inserted_keys.append(meta["_key"])
            except Exception as e_ins:
                _logger.error(f"Failed to insert test doc {doc.get('_key')}: {e_ins}")
                raise RuntimeError("Test data insertion failed.") from e_ins
        _logger.success(f"Inserted {len(inserted_keys)} test documents.")

        # Short wait for view indexing (often needed in test scenarios)
        import time

        _logger.info("Waiting 2s for view indexing...")
        time.sleep(2)

        # --- Run Test Cases ---
        _logger.info("--- Running BM25 Search Test Cases ---")

        # Test Case 1: Keyword search matching multiple docs
        _logger.info("Test Case 1: Keyword 'python json'")
        results1 = search_bm25(db, "python json", view_name=test_view_name, top_n=3)
        keys1 = {r["doc"]["_key"] for r in results1.get("results", [])}
        expected_keys1 = {
            f"bm25_doc1_{run_id}",
            f"bm25_doc3_{run_id}",
        }  # Doc 1 and 3 strongly match
        # Doc 4 might match 'python' weakly, allow for it depending on BM25 scoring/threshold
        if expected_keys1.issubset(keys1) and results1.get("total", 0) >= 2:
            _logger.success(
                f"✅ Test Case 1 PASSED. Found keys: {keys1}, Total: {results1.get('total')}"
            )
        else:
            _logger.error(
                f"❌ Test Case 1 FAILED. Expected subset {expected_keys1}, Got keys: {keys1}, Total: {results1.get('total')}"
            )
            passed = False

        # Test Case 2: Keyword search with tag filtering
        _logger.info("Test Case 2: Keyword 'script', Tag 'shell'")
        results2 = search_bm25(
            db, "script", tags=["shell"], view_name=test_view_name, top_n=3
        )
        keys2 = {r["doc"]["_key"] for r in results2.get("results", [])}
        expected_keys2 = {
            f"bm25_doc2_{run_id}"
        }  # Only doc 2 matches 'script' AND tag 'shell'
        if keys2 == expected_keys2 and results2.get("total", 0) == 1:
            _logger.success(
                f"✅ Test Case 2 PASSED. Found keys: {keys2}, Total: {results2.get('total')}"
            )
        else:
            _logger.error(
                f"❌ Test Case 2 FAILED. Expected {expected_keys2}, Got keys: {keys2}, Total: {results2.get('total')}"
            )
            passed = False

        # Test Case 3: Keyword search matching no specific tags, but keyword
        _logger.info("Test Case 3: Keyword 'timeout', No specific tag filter")
        results3 = search_bm25(db, "timeout", view_name=test_view_name, top_n=3)
        keys3 = {r["doc"]["_key"] for r in results3.get("results", [])}
        expected_keys3 = {f"bm25_doc4_{run_id}"}  # Only doc 4 matches 'timeout'
        if keys3 == expected_keys3 and results3.get("total", 0) == 1:
            _logger.success(
                f"✅ Test Case 3 PASSED. Found keys: {keys3}, Total: {results3.get('total')}"
            )
        else:
            _logger.error(
                f"❌ Test Case 3 FAILED. Expected {expected_keys3}, Got keys: {keys3}, Total: {results3.get('total')}"
            )
            passed = False

        # Test Case 4: Search yielding no results
        _logger.info("Test Case 4: Keyword 'nonexistenttermxyz'")
        results4 = search_bm25(
            db, "nonexistenttermxyz", view_name=test_view_name, top_n=3
        )
        if not results4.get("results") and results4.get("total", 0) == 0:
            _logger.success(f"✅ Test Case 4 PASSED. Found 0 results as expected.")
        else:
            _logger.error(
                f"❌ Test Case 4 FAILED. Expected 0 results, Got {len(results4.get('results', []))} results, Total: {results4.get('total')}"
            )
            passed = False

        # Test Case 5: Search with pagination (offset)
        _logger.info("Test Case 5: Keyword 'python', Offset 1")
        # First get total matching 'python'
        total_python = search_bm25(db, "python", view_name=test_view_name, top_n=10)[
            "total"
        ]
        results5 = search_bm25(
            db, "python", view_name=test_view_name, top_n=2, offset=1
        )
        keys5 = {r["doc"]["_key"] for r in results5.get("results", [])}
        # We expect fewer results than total, and offset should be correct
        # Exact keys depend on BM25 scores which can vary, so check counts/offset mainly
        expected_total5 = 3  # Docs 1, 3, 4 contain 'python'
        if (
            results5.get("total") == expected_total5
            and len(keys5) <= 2
            and results5.get("offset") == 1
        ):
            _logger.success(
                f"✅ Test Case 5 PASSED. Found {len(keys5)} results with offset 1 (Total={results5.get('total')}). Keys: {keys5}"
            )
        else:
            _logger.error(
                f"❌ Test Case 5 FAILED. Check results count/total/offset. Got keys: {keys5}, Total: {results5.get('total')}, Offset: {results5.get('offset')}"
            )
            passed = False

    except Exception as e:
        _logger.exception(f"An error occurred during the test execution: {e}")
        passed = False  # Mark as failed if any exception occurs

    finally:
        # --- Cleanup ---
        _logger.info("--- Cleaning up test resources ---")
        if db:
            # Use safe delete functions if they exist, otherwise direct delete
            if "delete_view_safely" in locals():
                delete_view_safely(db, test_view_name)
            else:
                try:
                    db.delete_view(test_view_name)
                    _logger.info(f"Dropped test view: {test_view_name}")
                except Exception as e_del_v:
                    _logger.warning(
                        f"Could not drop test view {test_view_name}: {e_del_v}"
                    )

            if "delete_collection_safely" in locals():
                delete_collection_safely(db, test_coll_name)
            else:
                try:
                    db.delete_collection(test_coll_name)
                    _logger.info(f"Dropped test collection: {test_coll_name}")
                except Exception as e_del_c:
                    _logger.warning(
                        f"Could not drop test collection {test_coll_name}: {e_del_c}"
                    )
        else:
            _logger.warning("DB connection not established, skipping cleanup.")

        _logger.info("-" * 40)
        if passed:
            _logger.success("✅ BM25 Standalone Test Completed Successfully")
            sys.exit(0)
        else:
            _logger.error("❌ BM25 Standalone Test FAILED")
            sys.exit(1)
