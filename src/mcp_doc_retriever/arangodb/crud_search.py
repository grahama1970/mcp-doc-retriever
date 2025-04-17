"""
ArangoDB Search Operations Module for Lessons Learned.
"""

import uuid
import sys
import os
from typing import Dict, Any, Optional, List, cast

from loguru import logger
from arango.typings import DataTypes
from arango.database import StandardDatabase
from arango.cursor import Cursor
from arango.exceptions import (
    ArangoServerError,
    AQLQueryExecuteError,
    CollectionLoadError,
)

# --- CONFIG IMPORT -------------------------------------------------
try:
    _root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
    if _root not in sys.path:
        sys.path.insert(0, _root)
    from mcp_doc_retriever.arangodb.config import COLLECTION_NAME, SEARCH_FIELDS

    logger.debug(
        f"Loaded config: COLLECTION_NAME={COLLECTION_NAME}, SEARCH_FIELDS={SEARCH_FIELDS}"
    )
except ImportError:
    COLLECTION_NAME = os.environ.get("ARANGO_VERTEX_COLLECTION", "lessons_learned")
    SEARCH_FIELDS = ["problem", "solution", "tags", "role"]
    logger.warning(
        f"Using fallback config: COLLECTION_NAME={COLLECTION_NAME}, SEARCH_FIELDS={SEARCH_FIELDS}"
    )


# --- KEYWORD SEARCH ------------------------------------------------
def find_lessons_by_keyword(
    db: StandardDatabase,
    keywords: List[str],
    search_fields: Optional[List[str]] = None,
    limit: int = 10,
    match_all: bool = False,
    tags: Optional[List[str]] = None,
) -> List[Dict[str, Any]]:
    action_uuid = str(uuid.uuid4())
    with logger.contextualize(
        action="find_lessons_by_keyword",
        crud_id=action_uuid,
        keywords=keywords,
        match_all=match_all,
        limit=limit,
        tags=tags,
    ):
        # 1) No filters: return all up to limit
        if not keywords and not tags:
            try:
                return list(db.collection(COLLECTION_NAME).all(limit=limit))
            except Exception as e:
                logger.error(f"Error fetching all docs: {e}")
                return []

        fields = search_fields or SEARCH_FIELDS
        bind_vars: Dict[str, DataTypes] = {}
        keyword_filters: List[str] = []
        tag_filters: List[str] = []

        # 2) tags-parameter filter (AND logic)
        if tags:
            bind_vars["input_tags"] = tags
            tag_filters.append(
                "doc.tags != null AND IS_ARRAY(doc.tags) "
                "AND LENGTH(INTERSECTION(doc.tags, @input_tags)) == LENGTH(@input_tags)"
            )

        # 3) keyword filters (text fields + tags[])
        if keywords:
            safe = [f for f in fields if f.replace("_", "").isalnum()]
            if not safe:
                logger.error("No valid search fields.")
                return []

            for i, kw in enumerate(keywords):
                var = f"kw{i}"
                list_var = f"{var}_list"
                bind_vars[var] = kw
                bind_vars[list_var] = [kw]  # 1-element list for INTERSECTION
                parts: List[str] = []

                # text fields
                for f in safe:
                    if f != "tags":
                        parts.append(
                            f"(doc.`{f}` != null AND IS_STRING(doc.`{f}`) "
                            f"AND CONTAINS(LOWER(doc.`{f}`), LOWER(@{var})))"
                        )

                # now also check tags array via INTERSECTION
                parts.append(
                    f"(doc.tags != null AND IS_ARRAY(doc.tags) "
                    f"AND LENGTH(INTERSECTION(doc.tags, @{list_var})) > 0)"
                )

                keyword_filters.append(f"({' OR '.join(parts)})")

        # 4) assemble FILTER clause
        clauses: List[str] = []
        if keyword_filters:
            joiner = " AND " if match_all else " OR "
            clauses.append(f"({joiner.join(keyword_filters)})")
        clauses.extend(tag_filters)
        filter_clause = f"FILTER {' AND '.join(clauses)}" if clauses else ""

        # always define @search_tags for SORT
        bind_vars["search_tags"] = tags or []

        aql = f"""
        FOR doc IN {COLLECTION_NAME}
          {filter_clause}
          LET intersectionCount = LENGTH(INTERSECTION(doc.tags, @search_tags))
          SORT intersectionCount DESC
          LIMIT {limit}
          RETURN doc
        """
        logger.debug(aql)
        logger.debug(f"Bind vars: {bind_vars}")

        try:
            cursor: Cursor = db.aql.execute(
                aql,
                bind_vars=cast(Dict[str, DataTypes], bind_vars),
                count=True,
            )
            return list(cursor)
        except (AQLQueryExecuteError, ArangoServerError, CollectionLoadError) as e:
            logger.error(f"AQL error: {e}")
            return []
        except Exception as e:
            logger.exception(f"Unexpected error: {e}")
            return []

# --- TAG SEARCH ----------------------------------------------------
def find_lessons_by_tag(
    db: StandardDatabase,
    tags_to_search: List[str],
    limit: int = 10,
    match_all: bool = False,
) -> List[Dict[str, Any]]:
    """
    Finds lesson docs containing tags in the 'tags' field.
    OR (any) vs AND (all), sorted by tag overlap count.
    """
    action_uuid = str(uuid.uuid4())
    with logger.contextualize(
        action="find_lessons_by_tag",
        crud_id=action_uuid,
        tags=tags_to_search,
        match_all=match_all,
        limit=limit,
    ):
        if not tags_to_search:
            logger.warning("No tags provided for tag search.")
            return []

        bind_vars = {"tags": tags_to_search}
        if match_all:
            aql = f"""
            FOR doc IN {COLLECTION_NAME}
              FILTER LENGTH(INTERSECTION(doc.tags,@tags)) == LENGTH(@tags)
              LET intersectionCount = LENGTH(INTERSECTION(doc.tags,@tags))
              SORT intersectionCount DESC
              LIMIT {limit}
              RETURN doc
            """
        else:
            aql = f"""
            FOR doc IN {COLLECTION_NAME}
              FILTER LENGTH(INTERSECTION(doc.tags,@tags)) > 0
              LET intersectionCount = LENGTH(INTERSECTION(doc.tags,@tags))
              SORT intersectionCount DESC
              LIMIT {limit}
              RETURN doc
            """
        logger.debug(aql)
        logger.debug(f"Bind vars: {bind_vars}")

        try:
            cursor: Cursor = db.aql.execute(
                aql, bind_vars=cast(Dict[str, DataTypes], bind_vars)
            )
            return list(cursor)
        except AQLQueryExecuteError as e:
            logger.error(f"AQL tag error: {e}")
            return []
        except Exception as e:
            logger.exception(f"Unexpected tag search error: {e}")
            return []


# --- STANDALONE TEST HARNESS ---------------------------------------
if __name__ == "__main__":
    from mcp_doc_retriever.arangodb.arango_setup import (
        connect_arango,
        ensure_database,
    )

    logger.remove()
    logger.add(
        sys.stderr, level="INFO", format="{time:HH:mm:ss} | {level:<7} | {message}"
    )

    # 1) Build a fresh test collection
    run_id = str(uuid.uuid4())[:6]
    test_coll = f"{COLLECTION_NAME}_tests_{run_id}"

    client = connect_arango()
    db = ensure_database(client)

    # drop any stale and create clean
    if db.has_collection(test_coll):
        db.delete_collection(test_coll)
    db.create_collection(test_coll)

    # override for this run
    COLLECTION_NAME = test_coll
    logger.info(f"Using test collection: {COLLECTION_NAME}")

    # 2) Directly insert three documents
    TEST_DATA = [
        {
            "_key": f"search_test_{run_id}_1",
            "problem": f"Unique problem alpha {run_id}",
            "solution": "Common solution.",
            "tags": ["search", "alpha", run_id],
            "role": "Searcher",
        },
        {
            "_key": f"search_test_{run_id}_2",
            "problem": f"Common problem {run_id}",
            "solution": "Unique solution beta.",
            "tags": ["search", "beta", run_id],
            "role": "Finder",
        },
        {
            "_key": f"search_test_{run_id}_3",
            "problem": "Another common problem.",
            "solution": "Common solution.",
            "tags": ["search", "alpha", "beta", run_id],
            "role": "Tester",
        },
    ]
    col = db.collection(COLLECTION_NAME)
    for doc in TEST_DATA:
        col.insert(doc)
        logger.success(f"Inserted: {doc['_key']}")

    # --- 3.x Keyword Tests ---
    # 3.1 OR
    res = find_lessons_by_keyword(db, ["alpha", run_id])
    got = {d["_key"] for d in res}
    expect = {
        f"search_test_{run_id}_1",
        f"search_test_{run_id}_2",
        f"search_test_{run_id}_3",
    }
    if expect.issubset(got):
        logger.success("✅ 3.1 Keyword OR PASSED")
    else:
        logger.error(f"❌ 3.1 Keyword OR FAILED. Got {got}")

    # 3.2 AND
    res = find_lessons_by_keyword(db, [run_id, "beta"], match_all=True)
    got = {d["_key"] for d in res}
    expect = {f"search_test_{run_id}_2", f"search_test_{run_id}_3"}
    if got == expect:
        logger.success("✅ 3.2 Keyword AND PASSED")
    else:
        logger.error(f"❌ 3.2 Keyword AND FAILED. Got {got}")

    # 3.3 + Tag
    res = find_lessons_by_keyword(db, [run_id], tags=["alpha"])
    got = {d["_key"] for d in res}
    expect = {f"search_test_{run_id}_1", f"search_test_{run_id}_3"}
    if got == expect:
        logger.success("✅ 3.3 Keyword + Tag PASSED")
    else:
        logger.error(f"❌ 3.3 Keyword + Tag FAILED. Got {got}")

    # 3.4 Limit
    res = find_lessons_by_keyword(db, [run_id], limit=1)
    if len(res) == 1:
        logger.success("✅ 3.4 Keyword Limit PASSED")
    else:
        logger.error(f"❌ 3.4 Keyword Limit FAILED. Got {len(res)}")

    # 3.5 None
    res = find_lessons_by_keyword(db, ["unlikely_term"])
    if not res:
        logger.success("✅ 3.5 Keyword None PASSED")
    else:
        logger.error(f"❌ 3.5 Keyword None FAILED. Got {res}")

    # --- 4.x Tag Tests ---
    # 4.1 OR
    res = find_lessons_by_tag(db, ["alpha", "beta"])
    got = {d["_key"] for d in res}
    expect = {
        f"search_test_{run_id}_1",
        f"search_test_{run_id}_2",
        f"search_test_{run_id}_3",
    }
    if expect.issubset(got):
        logger.success("✅ 4.1 Tag OR PASSED")
    else:
        logger.error(f"❌ 4.1 Tag OR FAILED. Got {got}")

    # 4.2 AND
    res = find_lessons_by_tag(db, ["alpha", "beta"], match_all=True)
    got = {d["_key"] for d in res}
    expect = {f"search_test_{run_id}_3"}
    if got == expect:
        logger.success("✅ 4.2 Tag AND PASSED")
    else:
        logger.error(f"❌ 4.2 Tag AND FAILED. Got {got}")

    # 4.3 None
    res = find_lessons_by_tag(db, ["no_such_tag"])
    if not res:
        logger.success("✅ 4.3 Tag None PASSED")
    else:
        logger.error(f"❌ 4.3 Tag None FAILED. Got {res}")

    # teardown
    for d in TEST_DATA:
        col.delete(d["_key"])
    db.delete_collection(COLLECTION_NAME)
    logger.info(f"Dropped test collection: {COLLECTION_NAME}")

    sys.exit(0)
