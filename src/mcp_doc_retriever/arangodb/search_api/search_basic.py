"""
crud_search_advanced.py

Two‐stage search for Lessons Learned in ArangoDB:
 - Tags: INTERSECTION(AQL) + rapidfuzz in Python
 - Text: LIKE (AQL) + rapidfuzz partial_ratio in Python (OR vs AND)
 - Results sorted by match‐count or summed similarity
"""

import os
import sys
import uuid
from typing import List, Dict, Any

from loguru import logger
from arango.database import StandardDatabase
from rapidfuzz import fuzz

# --- Load config -----------------------------------------------------
try:
    _root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
    if _root not in sys.path:
        sys.path.insert(0, _root)
    from mcp_doc_retriever.arangodb.config import COLLECTION_NAME, SEARCH_FIELDS  # type: ignore

    logger.debug(
        f"Loaded config: COLLECTION_NAME={COLLECTION_NAME}, SEARCH_FIELDS={SEARCH_FIELDS}"
    )
except ImportError:
    COLLECTION_NAME = os.environ.get("ARANGO_VERTEX_COLLECTION", "lessons_learned")
    SEARCH_FIELDS = ["problem", "solution", "context", "example"]
    logger.warning(
        f"Using fallback config: COLLECTION_NAME={COLLECTION_NAME}, SEARCH_FIELDS={SEARCH_FIELDS}"
    )


def find_lessons_by_tags_advanced(
    db: StandardDatabase,
    tag_keywords: List[str],
    limit: int = 10,
    match_all: bool = False,
    similarity_threshold: float = 97.0,
) -> List[Dict[str, Any]]:
    """
    Two‐stage tag search:
      1) AQL: fetch docs where INTERSECTION(doc.tags, @keywords) > 0 (OR)
         or == len(@keywords) (AND)
      2) Python: fuzzy‐match each keyword against doc.tags,
         count how many keywords matched (ratio ≥ threshold),
         drop zero‐match docs, sort by match‐count desc.
    """
    if not tag_keywords:
        return []

    # Stage 1: coarse AQL fetch
    op = "==" if match_all else ">"
    required = len(tag_keywords) if match_all else 0
    aql = f"""
    FOR doc IN {COLLECTION_NAME}
      FILTER LENGTH(INTERSECTION(doc.tags, @keywords)) {op} {required}
      LIMIT {limit * 5}
      RETURN doc
    """
    bind_vars = {"keywords": tag_keywords}
    try:
        cursor = db.aql.execute(aql, bind_vars=bind_vars)
        candidates = list(cursor)
    except Exception as e:
        logger.error(f"AQL error in tag stage: {e}")
        return []

    # Stage 2: Python fuzzy filtering & sorting
    def tag_match_count(doc: Dict[str, Any]) -> int:
        tags = doc.get("tags", [])
        cnt = 0
        for kw in tag_keywords:
            if any(
                fuzz.ratio(kw.lower(), str(t).lower()) >= similarity_threshold
                for t in tags
            ):
                cnt += 1
        return cnt

    scored = []
    for doc in candidates:
        cnt = tag_match_count(doc)
        if cnt > 0:
            scored.append((cnt, doc))

    scored.sort(key=lambda x: x[0], reverse=True)
    return [d for _, d in scored[:limit]]


def find_lessons_by_text_like(
    db: StandardDatabase,
    text_keywords: List[str],
    limit: int = 10,
    match_all: bool = False,
    similarity_threshold: float = 97.0,
) -> List[Dict[str, Any]]:
    """
    Two‐stage text search:
      1) AQL: for OR, any field LIKE any keyword;
              for AND, every keyword must LIKE (across any field)
      2) Python:
           - AND: require fuzz.partial_ratio ≥ threshold for every keyword
           - OR:  require fuzz.partial_ratio ≥ threshold for at least one keyword
         Sum up only the passing scores for ranking.
    """
    # --- Stage 1: coarse AQL ---
    bind_vars: Dict[str, Any] = {}
    if text_keywords:
        clauses = []
        for i, kw in enumerate(text_keywords):
            var = f"kw{i}"
            bind_vars[var] = kw
            likes = [
                f"LOWER(doc.`{field}`) LIKE CONCAT('%', LOWER(@{var}), '%')"
                for field in SEARCH_FIELDS
            ]
            clause = "(" + " OR ".join(likes) + ")"
            clauses.append(clause)
        joiner = " AND " if match_all else " OR "
        filter_clause = "FILTER " + joiner.join(clauses)
    else:
        filter_clause = ""
    aql = f"""
    FOR doc IN {COLLECTION_NAME}
      {filter_clause}
      LIMIT {limit * 5}
      RETURN doc
    """
    try:
        cursor = db.aql.execute(aql, bind_vars=bind_vars)
        candidates = list(cursor)
    except Exception as e:
        logger.error(f"AQL error in text stage: {e}")
        return []

    # --- Stage 2: Python fuzzy filtering & scoring ---
    def keyword_score(doc: Dict[str, Any], kw: str) -> float:
        best = 0.0
        for field in SEARCH_FIELDS:
            val = doc.get(field)
            if isinstance(val, str):
                score = fuzz.partial_ratio(kw.lower(), val.lower())
                if score > best:
                    best = score
        return best

    scored = []
    for doc in candidates:
        scores = [keyword_score(doc, kw) for kw in text_keywords]
        if text_keywords:
            if match_all:
                # require every keyword ≥ threshold
                if any(s < similarity_threshold for s in scores):
                    continue
            else:
                # require at least one keyword ≥ threshold
                if all(s < similarity_threshold for s in scores):
                    continue
        total = sum(s for s in scores if s >= similarity_threshold)
        scored.append((total, doc))

    scored.sort(key=lambda x: x[0], reverse=True)
    return [d for _, d in scored[:limit]]


# --- Standalone Verification Harness -------------------------------
if __name__ == "__main__":
    import sys
    from loguru import logger as _logger
    from mcp_doc_retriever.arangodb.arango_setup import (
        connect_arango,
        ensure_database,
    )
    from mcp_doc_retriever.arangodb.lessons import add_lesson, delete_lesson

    _logger.remove()
    _logger.add(
        sys.stderr, level="INFO", format="{time:HH:mm:ss} | {level:<7} | {message}"
    )

    # 1) Fresh test collection
    run_id = str(uuid.uuid4())[:6]
    test_coll = f"{COLLECTION_NAME}_tests_{run_id}"
    client = connect_arango()
    db = ensure_database(client)
    # drop & recreate
    try:
        db.delete_collection(test_coll)
    except Exception:
        pass
    db.create_collection(test_coll)
    COLLECTION_NAME = test_coll
    _logger.info(f"Using test collection: {COLLECTION_NAME}")

    # 2) Insert 3 test docs
    TEST = [
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
    keys = []
    for doc in TEST:
        meta = db.collection(COLLECTION_NAME).insert(doc)
        if meta and meta.get("_key"):
            keys.append(meta["_key"])
            _logger.success(f"Inserted: {meta['_key']}")
        else:
            _logger.error(f"Insert failed: {doc['_key']}")
            sys.exit(1)

    passed = True

    # --- Tag OR/AND/NONE tests ---
    expect_all = {f"search_test_{run_id}_{i}" for i in (1, 2, 3)}
    got = {
        d["_key"] for d in find_lessons_by_tags_advanced(db, ["alpha", "beta", run_id])
    }
    if expect_all.issubset(got):
        _logger.success("✅ Tag OR PASSED")
    else:
        _logger.error(f"❌ Tag OR FAILED. Got {got}")
        passed = False

    got = {
        d["_key"]
        for d in find_lessons_by_tags_advanced(db, ["alpha", "beta"], match_all=True)
    }
    if got == {f"search_test_{run_id}_3"}:
        _logger.success("✅ Tag AND PASSED")
    else:
        _logger.error(f"❌ Tag AND FAILED. Got {got}")
        passed = False

    if not find_lessons_by_tags_advanced(db, ["no_such_tag"]):
        _logger.success("✅ Tag NONE PASSED")
    else:
        _logger.error("❌ Tag NONE FAILED")
        passed = False

    # --- Text OR/AND/NONE tests ---
    # OR: match either “alpha” or run_id
    got_or = {
        d["_key"]
        for d in find_lessons_by_text_like(db, ["alpha", run_id], match_all=False)
    }
    if got_or == {f"search_test_{run_id}_1", f"search_test_{run_id}_2"}:
        _logger.success("✅ Text OR PASSED")
    else:
        _logger.error(f"❌ Text OR FAILED. Got {got_or}")
        passed = False

    # AND: match both run_id AND “beta”
    got_and = {
        d["_key"]
        for d in find_lessons_by_text_like(db, [run_id, "beta"], match_all=True)
    }
    if got_and == {f"search_test_{run_id}_2"}:
        _logger.success("✅ Text AND PASSED")
    else:
        _logger.error(f"❌ Text AND FAILED. Got {got_and}")
        passed = False

    # NONE: unlikely term
    if not find_lessons_by_text_like(db, ["unlikely_term"], match_all=False):
        _logger.success("✅ Text NONE PASSED")
    else:
        _logger.error("❌ Text NONE FAILED")
        passed = False

    # 3) Clean up
    for k in keys:
        delete_lesson(db, k)
    db.delete_collection(COLLECTION_NAME)
    _logger.info(f"Dropped test collection: {COLLECTION_NAME}")
    _logger.info("-" * 40)
    sys.exit(0 if passed else 1)
