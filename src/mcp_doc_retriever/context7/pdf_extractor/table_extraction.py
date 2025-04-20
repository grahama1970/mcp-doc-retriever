"""
Table extraction and processing for the PDF extraction pipeline.

This module uses Camelot to extract tables, merges similar or human-specified tables,
and extracts contextual text around tables using PyMuPDF.

Dependencies:
- camelot-py: For table extraction.
- pymupdf: For PDF manipulation.
- fuzzywuzzy: For table similarity.
- loguru: For logging.
"""
import json
import re
from typing import List, Dict, Optional, Tuple
from pathlib import Path
import fitz
from loguru import logger
import camelot
from fuzzywuzzy import fuzz

from .config import (
    CAMELOT_DEFAULT_FLAVOR,
    CAMELOT_LATTICE_LINE_SCALE,
    CAMELOT_STREAM_EDGE_TOL,
    LOW_CONFIDENCE_THRESHOLD,
    TABLE_MERGE_SIMILARITY_THRESHOLD,
    TABLE_CONTEXT_MARGIN,
    TABLE_CONTEXT_MAX_LINES,
)
from .utils import _normalize_text, _assign_unique_table_ids, _get_encoder


def _run_camelot(
    pdf_path: str,
    pages: str = "1-end",
    table_areas: Optional[List[str]] = None,
    parameters: Optional[Dict] = None,
) -> List[Dict]:
    """Extracts tables using Camelot."""
    logger.info(f"Running Camelot: pages='{pages}'")
    params = {
        "flavor": CAMELOT_DEFAULT_FLAVOR,
        "line_scale": CAMELOT_LATTICE_LINE_SCALE,
        "edge_tol": CAMELOT_STREAM_EDGE_TOL,
    }
    if parameters:
        params.update(parameters)

    try:
        tables = camelot.read_pdf(
            pdf_path, flavor=params["flavor"], pages=pages, **params
        )
        camelot_tables = []
        for table in tables:
            accuracy = table.parsing_report.get("accuracy", 0.0)
            header = [str(col) for col in table.df.columns]
            body = [
                [str(cell) if cell is not None else None for cell in row]
                for row in table.df.values.tolist()
            ]
            camelot_tables.append(
                {
                    "type": "table",
                    "header": header,
                    "body": body,
                    "page": table.page,
                    "bbox": table._bbox,
                    "accuracy": accuracy,
                    "needs_review": accuracy < LOW_CONFIDENCE_THRESHOLD,
                    "source": "camelot",
                }
            )
        return _assign_unique_table_ids(camelot_tables, "camelot")
    except Exception as e:
        logger.error(f"Camelot extraction failed: {e}")
        return []


def _get_text_around_table(
    doc: fitz.Document,
    page_num: int,
    table_bbox: Optional[Tuple[float, float, float, float]],
) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """Extracts text above, below, and title for a table."""
    if not table_bbox or page_num <= 0 or page_num > len(doc):
        return None, None, None

    try:
        page = doc[page_num - 1]
        blocks = page.get_text("blocks", sort=True)
        above_texts, below_texts, table_title = [], [], None
        table_title_patterns = [
            re.compile(r"^\s*Table\s+\d+(\.\d+)*[:.]?\s+", re.IGNORECASE)
        ]
        table_top_y, table_bottom_y = table_bbox[1], table_bbox[3]

        for block in blocks:
            block_y0, block_y1, text = block[1], block[3], block[4].strip()
            if block_y1 < table_top_y - TABLE_CONTEXT_MARGIN and text:
                above_texts.append(text)
            elif block_y0 > table_bottom_y + TABLE_CONTEXT_MARGIN and text:
                below_texts.append(text)

        if above_texts:
            last_above = above_texts[-1]
            for pattern in table_title_patterns:
                if pattern.match(last_above):
                    table_title = last_above
                    break

        norm_above = (
            _normalize_text(" ".join(above_texts[-TABLE_CONTEXT_MAX_LINES:]))
            if above_texts
            else None
        )
        norm_below = (
            _normalize_text(" ".join(below_texts[:TABLE_CONTEXT_MAX_LINES]))
            if below_texts
            else None
        )
        norm_title = _normalize_text(table_title) if table_title else None
        return norm_above, norm_below, norm_title
    except Exception as e:
        logger.error(f"Error extracting table context: {e}")
        return None, None, None


def _merge_table_if_similar(
    current_table: Dict, next_table: Dict, threshold: int
) -> bool:
    """Merges tables if headers are similar."""
    current_header = current_table.get("header", [])
    next_header = next_table.get("header", [])
    if not current_header or not next_header or len(current_header) != len(next_header):
        return False

    similarity = fuzz.token_sort_ratio(
        " ".join(map(str, current_header)), " ".join(map(str, next_header))
    )
    if similarity > threshold:
        current_table["body"].extend(next_table.get("body", []))
        start_page = current_table.get("page", 0)
        next_page = next_table.get("page", 0)
        current_table["page_range"] = (start_page, max(start_page, next_page))
        current_table["needs_review"] = current_table.get(
            "needs_review", False
        ) or next_table.get("needs_review", False)
        current_table["bbox"] = None
        current_table["accuracy"] = None
        encoding = _get_encoder()
        current_table["token_count"] = len(
            encoding.encode(
                json.dumps(
                    {"header": current_table["header"], "body": current_table["body"]}
                )
            )
        )
        return True
    return False


def _merge_tables(
    tables: List[Dict], merge_instructions: Dict[str, str], threshold: int
) -> List[Dict]:
    """Merges tables based on instructions or similarity."""
    merged_tables = []
    processed_ids = set()
    table_map = {t["table_id"]: t for t in tables if "table_id" in t}

    for source_id, target_id in merge_instructions.items():
        if source_id in processed_ids or target_id in processed_ids:
            continue
        if source_id in table_map and target_id in table_map:
            first, second = sorted(
                [table_map[source_id], table_map[target_id]],
                key=lambda t: t.get("page", 0),
            )
            first["body"].extend(second.get("body", []))
            first["page_range"] = (first.get("page", 0), second.get("page", 0))
            first["needs_review"] = False
            first["source"] = "human_merged"
            first["bbox"] = None
            first["accuracy"] = None
            encoding = _get_encoder()
            first["token_count"] = len(
                encoding.encode(
                    json.dumps({"header": first["header"], "body": first["body"]})
                )
            )
            processed_ids.add(source_id)
            processed_ids.add(target_id)

    remaining_tables = sorted(
        [t for t in tables if t.get("table_id") not in processed_ids],
        key=lambda x: x.get("page", 0),
    )
    if remaining_tables:
        current_table = remaining_tables[0]
        for next_table in remaining_tables[1:]:
            if next_table.get("page", 0) <= current_table.get("page", 0) + 1:
                if _merge_table_if_similar(current_table, next_table, threshold):
                    processed_ids.add(next_table["table_id"])
                    continue
            merged_tables.append(current_table)
            processed_ids.add(current_table["table_id"])
            current_table = next_table
        if current_table.get("table_id") not in processed_ids:
            merged_tables.append(current_table)
            processed_ids.add(current_table["table_id"])

    for table_id, table in table_map.items():
        if table_id in merge_instructions.values() and table_id not in processed_ids:
            merged_tables.append(table)
            processed_ids.add(table_id)

    encoding = _get_encoder()
    for table in merged_tables:
        if "page_range" not in table:
            page = table.get("page", 0)
            table["page_range"] = (page, page)
        if "token_count" not in table:
            table["token_count"] = len(
                encoding.encode(
                    json.dumps(
                        {
                            "header": table.get("header", []),
                            "body": table.get("body", []),
                        }
                    )
                )
            )

    return merged_tables


def extract_tables(
    pdf_path: str,
    repo_link: str,
    doc: fitz.Document,
    tables: List[Dict],
    merge_instructions: Dict[str, str],
) -> List[Dict]:
    """Extracts and processes tables with context."""
    final_tables = _merge_tables(
        tables, merge_instructions, TABLE_MERGE_SIMILARITY_THRESHOLD
    )
    encoding = _get_encoder()
    for table in final_tables:
        page = table.get("page", table.get("page_range", [0])[0])
        bbox = table.get("bbox")
        above, below, title = _get_text_around_table(doc, page, bbox)
        table.update(
            {
                "file_path": pdf_path,
                "repo_link": repo_link,
                "above_text": above,
                "below_text": below,
                "title": title,
                "extraction_date": datetime.datetime.now().isoformat(),
            }
        )
    return final_tables


def usage_function():
    """Demonstrates table extraction."""
    pdf_path = "sample.pdf"
    try:
        doc = fitz.open(pdf_path)
    except:
        doc = fitz.Document()
    tables = [
        {
            "table_id": "camelot_p1_t0",
            "header": ["Name", "Age"],
            "body": [["Alice", "30"]],
            "page": 1,
            "bbox": (100, 200, 300, 400),
        }
    ]
    result = extract_tables(pdf_path, "https://repo", doc, tables, {})
    return result[0] if result else {}


if __name__ == "__main__":
    result = usage_function()
    print("Table Extraction Result:")
    print(json.dumps(result, indent=2))
