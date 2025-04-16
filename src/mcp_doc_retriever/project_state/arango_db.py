# src/mcp_doc_retriever/project_state/arango_db.py
"""
Provides functions to interact with ArangoDB for storing and retrieving lessons learned.

This module handles the connection to ArangoDB, adding new lesson documents,
and searching for lessons using keywords and tags via an ArangoSearch view.

Links:
- python-arango documentation: (Referencing local copy at git_downloader_test/arango_full/docs/)
  - Connection: git_downloader_test/arango_full/docs/connection.rst
  - Database: git_downloader_test/arango_full/docs/database.rst
  - Collection: git_downloader_test/arango_full/docs/collection.rst
  - AQL: git_downloader_test/arango_full/docs/aql.rst
  - View: git_downloader_test/arango_full/docs/view.rst

Environment Variables:
- ARANGO_HOST: The ArangoDB host URL (e.g., http://localhost:8529).
- ARANGO_USER: The ArangoDB username (e.g., root).
- ARANGO_PASSWORD: The ArangoDB password.
- ARANGO_DB: The target database name (e.g., doc_retriever).

Sample Input (add_lesson):
lesson_data = {
    "severity": "INFO",
    "role": "Senior Coder",
    "task": "Task 1.5.1",
    "phase": "Implementation",
    "problem": "Initial connection to ArangoDB failed.",
    "solution": "Ensure ARANGO_HOST environment variable is set correctly.",
    "tags": ["arangodb", "connection", "environment"],
    "context": "Setting up the ArangoDB backend.",
    "example": "export ARANGO_HOST='http://localhost:8529'"
}
result = add_lesson(lesson_data) # Returns ArangoDB metadata like {'_id': 'lessons_learned/12345', ...}

Sample Input (find_lessons):
results = find_lessons(keywords=["connection", "failed"], tags=["arangodb"])
# Returns list of matching lesson documents

Expected Output (find_lessons):
[
    {
        "_key": "12345",
        "_id": "lessons_learned/12345",
        "_rev": "_cAbCdEf--",
        "timestamp": "2025-04-16T09:06:00Z",
        "severity": "INFO",
        "role": "Senior Coder",
        # ... other fields ...
        "problem": "Initial connection to ArangoDB failed.",
        "solution": "Ensure ARANGO_HOST environment variable is set correctly.",
        "tags": ["arangodb", "connection", "environment"],
        # ...
    },
    # ... other matching documents ...
]
"""

import os
import logging
from datetime import datetime, timezone
from typing import List, Dict, Optional, Any
from arango import ArangoClient
from arango.database import StandardDatabase
from arango.exceptions import (
    ArangoClientError,
    ArangoServerError,
    DatabaseListError,
    CollectionListError,
    DocumentInsertError,
    AQLQueryExecuteError,
)

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# --- Configuration ---
ARANGO_HOST = os.environ.get("ARANGO_HOST", "http://localhost:8529")
ARANGO_USER = os.environ.get("ARANGO_USER", "root")
ARANGO_PASSWORD = os.environ.get("ARANGO_PASSWORD", "openSesame") # Default from task
ARANGO_DB_NAME = os.environ.get("ARANGO_DB", "doc_retriever")
COLLECTION_NAME = "lessons_learned"
VIEW_NAME = "lessons_view"

# Global client and db object to potentially reuse connection
_client: Optional[ArangoClient] = None
_db: Optional[StandardDatabase] = None

def get_arango_db() -> StandardDatabase:
    """
    Establishes a connection to the ArangoDB database specified by environment variables.
    Reuses existing connection if available.

    Returns:
        StandardDatabase: The ArangoDB database object.

    Raises:
        ConnectionError: If the connection to ArangoDB fails.
    """
    global _client, _db
    if _db:
        # Basic check: Verify connection still works (might be too slow for frequent calls)
        # try:
        #     _db.ping() # ArangoDB doesn't have a direct ping, check collections instead
        #     _db.collections()
        #     return _db
        # except (ArangoClientError, ArangoServerError):
        #     logger.warning("Existing ArangoDB connection seems stale. Reconnecting.")
        #     _client = None
        #     _db = None
        # For now, assume connection is stable if _db exists
         return _db


    logger.info(f"Attempting to connect to ArangoDB: host={ARANGO_HOST}, db={ARANGO_DB_NAME}")
    try:
        _client = ArangoClient(hosts=ARANGO_HOST)
        # Connect to the target database directly
        _db = _client.db(ARANGO_DB_NAME, username=ARANGO_USER, password=ARANGO_PASSWORD)

        # Verify connection by listing collections (or checking DB existence)
        _db.collections() # Throws error if connection failed
        logger.info(f"Successfully connected to ArangoDB database '{ARANGO_DB_NAME}'.")
        return _db
    except (ArangoClientError, ArangoServerError, DatabaseListError) as e:
        logger.error(f"Failed to connect to ArangoDB database '{ARANGO_DB_NAME}': {e}", exc_info=True)
        # Reset globals on failure
        _client = None
        _db = None
        raise ConnectionError(f"Could not connect to ArangoDB: {e}") from e

def add_lesson(lesson_data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Adds a new lesson document to the ArangoDB collection.

    Args:
        lesson_data: A dictionary containing the lesson details.
                     Expected keys match the SQLite schema (excluding id).
                     'timestamp' will be added if not present.
                     'tags' should be a list of strings.

    Returns:
        Dict[str, Any]: The metadata of the inserted document (_id, _key, _rev).

    Raises:
        ValueError: If required fields are missing or tags are not a list.
        ConnectionError: If the database connection fails.
        ArangoServerError: If there's an error during document insertion.
    """
    db = get_arango_db()

    # Basic validation
    required_fields = ["role", "problem", "solution"]
    if not all(field in lesson_data for field in required_fields):
        raise ValueError(f"Missing one or more required fields: {required_fields}")
    if "tags" in lesson_data and not isinstance(lesson_data["tags"], list):
        raise ValueError("'tags' field must be a list of strings.")

    # Ensure timestamp exists (use UTC)
    if "timestamp" not in lesson_data:
        lesson_data["timestamp"] = datetime.now(timezone.utc).isoformat()
    elif isinstance(lesson_data["timestamp"], datetime):
         lesson_data["timestamp"] = lesson_data["timestamp"].isoformat()


    try:
        collection = db.collection(COLLECTION_NAME)
        logger.info(f"Inserting lesson into collection '{COLLECTION_NAME}'...")
        meta = collection.insert(lesson_data)
        logger.info(f"Lesson inserted successfully with key: {meta['_key']}")
        return meta
    except DocumentInsertError as e:
        logger.error(f"Failed to insert lesson document: {e}", exc_info=True)
        raise ArangoServerError(f"Failed to insert lesson: {e}") from e
    except CollectionListError as e: # Should not happen if init script ran, but good practice
         logger.error(f"Collection '{COLLECTION_NAME}' not found: {e}", exc_info=True)
         raise ConnectionError(f"Collection '{COLLECTION_NAME}' not found. Run init script?") from e


def find_lessons(
    keywords: Optional[List[str]] = None,
    tags: Optional[List[str]] = None,
    limit: int = 10
) -> List[Dict[str, Any]]:
    """
    Finds lessons using keywords and/or tags via the ArangoSearch view.

    Args:
        keywords: A list of keywords to search for in text fields
                  (problem, solution, context, example).
        tags: A list of exact tags to match in the 'tags' array field.
        limit: The maximum number of results to return.

    Returns:
        List[Dict[str, Any]]: A list of matching lesson documents,
                              ordered by relevance (BM25 score).

    Raises:
        ConnectionError: If the database connection fails.
        ArangoServerError: If there's an error during the AQL query execution.
    """
    db = get_arango_db()
    bind_vars = {"limit": limit, "@view": VIEW_NAME}
    search_conditions = []

    # Build keyword search condition
    if keywords:
        # Combine keywords into a single phrase search for simplicity,
        # or use multiple PHRASE functions for more complex logic.
        # Using BM25 scoring implies searching across indexed fields.
        keyword_phrase = " ".join(keywords)
        search_conditions.append(
            f"""
            ANALYZER(
                TOKENS(@keyword_phrase, "text_en") ALL >= doc.problem
                OR TOKENS(@keyword_phrase, "text_en") ALL >= doc.solution
                OR TOKENS(@keyword_phrase, "text_en") ALL >= doc.context
                OR TOKENS(@keyword_phrase, "text_en") ALL >= doc.example
            , "text_en")
            """
            # Alternative using NGRAM_MATCH or PHRASE for more complex matching:
            # f"""
            # NGRAM_MATCH(
            #     doc.problem, @keyword_phrase, 0.5, 'text_en'
            # ) OR NGRAM_MATCH(
            #     doc.solution, @keyword_phrase, 0.5, 'text_en'
            # ) OR NGRAM_MATCH(
            #     doc.context, @keyword_phrase, 0.5, 'text_en'
            # ) OR NGRAM_MATCH(
            #     doc.example, @keyword_phrase, 0.5, 'text_en'
            # )
            # """
        )
        bind_vars["keyword_phrase"] = keyword_phrase

    # Build tag search condition (exact match within the array)
    if tags:
        # Assuming 'tags' is indexed with 'identity' analyzer or directly searchable as array
        # Using AQL array comparison operators
        tag_conditions = []
        for i, tag in enumerate(tags):
            var_name = f"tag{i}"
            tag_conditions.append(f"@{var_name} IN doc.tags")
            bind_vars[var_name] = tag
        if tag_conditions:
             # Require ALL tags to be present
            search_conditions.append("(" + " AND ".join(tag_conditions) + ")")
            # If ANY tag match is desired, use " OR " instead of " AND "

    # Combine conditions
    search_clause = ""
    if search_conditions:
        search_clause = "SEARCH " + " AND ".join(search_conditions) # Require both keywords AND tags if both provided
    else:
        # If no search criteria, return latest lessons? Or empty list?
        # Returning empty list if no criteria is safer.
        logger.info("No search criteria provided, returning empty list.")
        return []
        # Alternative: Return latest N lessons if no criteria
        # query = f"""
        # FOR doc IN {COLLECTION_NAME}
        #   SORT doc.timestamp DESC
        #   LIMIT @limit
        #   RETURN doc
        # """
        # bind_vars = {"limit": limit}


    # Construct the final AQL query using the view
    query = f"""
    FOR doc IN @@view
      {search_clause}
      SORT BM25(doc) DESC  // Order by relevance score
      LIMIT @limit
      RETURN doc
    """

    logger.info(f"Executing AQL query: {query.strip()}")
    logger.debug(f"Bind variables: {bind_vars}")

    try:
        cursor = db.aql.execute(query, bind_vars=bind_vars, count=True)
        results = [doc for doc in cursor]
        logger.info(f"Found {len(results)} matching lessons (limit: {limit}).")
        return results
    except AQLQueryExecuteError as e:
        logger.error(f"Failed to execute AQL query: {e}", exc_info=True)
        logger.error(f"Query: {query}")
        logger.error(f"Bind Vars: {bind_vars}")
        raise ArangoServerError(f"Failed to find lessons: {e}") from e


# Minimal real-world usage example for standalone verification
if __name__ == "__main__":
    print("--- Running ArangoDB Lessons Module Standalone Test ---")

    # Ensure environment variables are set for this test if running manually
    # Example: export ARANGO_PASSWORD=yourpassword

    test_lesson = {
        "severity": "DEBUG",
        "role": "Test Runner",
        "task": "Standalone Verification",
        "phase": "Testing",
        "problem": "Testing the arango_db module connection and add/find.",
        "solution": "Run this script directly with `uv run python ...`",
        "tags": ["test", "arangodb", "standalone", "verification"],
        "context": "Executing the __main__ block of arango_db.py",
        "example": "uv run python src/mcp_doc_retriever/project_state/arango_db.py"
    }

    try:
        # 1. Test Connection (implicitly tested by get_arango_db)
        print("\n1. Testing Connection...")
        db_conn = get_arango_db()
        print(f"   Connection successful to DB: {db_conn.name}")
        print(f"   Available collections: {db_conn.collections()}")

        # 2. Add a lesson
        print("\n2. Testing Add Lesson...")
        print(f"   Adding lesson: {test_lesson['problem']}")
        added_meta = add_lesson(test_lesson)
        print(f"   Lesson added successfully: {added_meta}")
        lesson_key = added_meta['_key'] # Get the key for potential cleanup/lookup

        # 3. Find the added lesson by tag
        print("\n3. Testing Find Lesson (by tag)...")
        find_tags = ["standalone", "test"]
        print(f"   Finding lessons with tags: {find_tags}")
        results_tag = find_lessons(tags=find_tags, limit=5)
        print(f"   Found {len(results_tag)} lessons.")
        if not any(r['_key'] == lesson_key for r in results_tag):
             print(f"   *** WARNING: Test lesson with key {lesson_key} not found by tags! ***")
        # print(f"   Results: {results_tag}") # Can be verbose

        # 4. Find the added lesson by keyword
        print("\n4. Testing Find Lesson (by keyword)...")
        find_keywords = ["connection", "module"]
        print(f"   Finding lessons with keywords: {find_keywords}")
        results_kw = find_lessons(keywords=find_keywords, limit=5)
        print(f"   Found {len(results_kw)} lessons.")
        if not any(r['_key'] == lesson_key for r in results_kw):
             print(f"   *** WARNING: Test lesson with key {lesson_key} not found by keywords! ***")
        # print(f"   Results: {results_kw}") # Can be verbose

        # 5. Find by both
        print("\n5. Testing Find Lesson (by keyword and tag)...")
        find_tags_both = ["arangodb"]
        find_keywords_both = ["testing"]
        print(f"   Finding lessons with keywords: {find_keywords_both} AND tags: {find_tags_both}")
        results_both = find_lessons(keywords=find_keywords_both, tags=find_tags_both, limit=5)
        print(f"   Found {len(results_both)} lessons.")
        if not any(r['_key'] == lesson_key for r in results_both):
             print(f"   *** WARNING: Test lesson with key {lesson_key} not found by keywords+tags! ***")
        # print(f"   Results: {results_both}") # Can be verbose


        # Optional: Cleanup - Delete the test lesson
        # try:
        #     print(f"\n--- Cleaning up test lesson {lesson_key} ---")
        #     collection = db_conn.collection(COLLECTION_NAME)
        #     collection.delete(lesson_key)
        #     print("   Cleanup successful.")
        # except Exception as cleanup_e:
        #     print(f"   Cleanup failed: {cleanup_e}")


        print("\n--- Standalone Test Completed Successfully ---")

    except ConnectionError as e:
        print(f"\n*** Standalone Test Failed: Connection Error - {e} ***")
        print("*** Ensure ArangoDB is running and environment variables are set correctly. ***")
        print("*** (ARANGO_HOST, ARANGO_USER, ARANGO_PASSWORD, ARANGO_DB) ***")
        exit(1)
    except Exception as e:
        print(f"\n*** Standalone Test Failed: Unexpected Error - {e} ***")
        logger.error("Standalone test failed", exc_info=True)
        exit(1)