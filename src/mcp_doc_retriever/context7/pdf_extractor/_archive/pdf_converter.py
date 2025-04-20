"""
Main PDF conversion pipeline for the PDF extraction system.

This module orchestrates PDF processing using Marker, Camelot, Qwen-VL, and human
corrections, producing structured JSON output.

Dependencies:
- pymupdf: For PDF handling.
- loguru: For logging.
"""

import os
import json
import datetime
from pathlib import Path
from typing import List, Dict
import fitz
from loguru import logger

from ..config import DEFAULT_OUTPUT_DIR, DEFAULT_CORRECTIONS_DIR
from ..utils import _assign_unique_table_ids, _get_encoder
from ..table_extraction import _run_camelot, extract_tables
from ..marker_processor import process_marker
from ..qwen_processor import is_scanned_pdf, process_qwen


def _load_corrections(pdf_path: str, corrections_dir: str) -> Dict:
    """Loads human corrections from JSON."""
    pdf_id = os.path.splitext(os.path.basename(pdf_path))[0]
    corrections_path = Path(corrections_dir) / f"{pdf_id}_corrections.json"
    if corrections_path.exists():
        try:
            with open(corrections_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"Failed to load corrections: {e}")
    return {"tables": []}


def convert_pdf(
    pdf_path: str,
    repo_link: str,
    output_dir: str = DEFAULT_OUTPUT_DIR,
    use_marker_markdown: bool = False,
    corrections_dir: str = DEFAULT_CORRECTIONS_DIR,
    force_qwen: bool = False,
) -> List[Dict]:
    """Converts PDF to structured JSON."""
    logger.info(f"Converting PDF: {pdf_path}")
    os.makedirs(output_dir, exist_ok=True)
    os.makedirs(corrections_dir, exist_ok=True)

    output_json = (
        Path(output_dir)
        / f"{os.path.splitext(os.path.basename(pdf_path))[0]}_structured.json"
    )
    elements, tables = [], []

    try:
        corrections = _load_corrections(pdf_path, corrections_dir)
        human_tables = corrections.get("tables", [])
        reextract_pages = sorted(
            set(
                t.get("page", 0) for t in human_tables if t.get("status") == "reextract"
            )
        )
        pages_str = ",".join(map(str, reextract_pages)) if reextract_pages else "1-end"

        with fitz.open(pdf_path) as doc:
            strategy = (
                "qwen_only"
                if force_qwen or is_scanned_pdf(pdf_path)
                else (
                    "marker_md_camelot"
                    if use_marker_markdown
                    else "marker_json_camelot"
                )
            )
            logger.info(f"Using strategy: {strategy}")

            if strategy == "qwen_only":
                elements = process_qwen(pdf_path, repo_link)
                tables = [e for e in elements if e["type"] == "table"]
                elements = [e for e in elements if e["type"] != "table"]
                tables = _assign_unique_table_ids(tables, "qwen")
            else:
                elements = process_marker(pdf_path, repo_link, use_marker_markdown)
                tables.extend([e for e in elements if e["type"] == "table"])
                elements = [e for e in elements if e["type"] != "table"]
                tables.extend(_run_camelot(pdf_path, pages=pages_str))

                if not elements and not tables:
                    logger.warning("No content extracted. Falling back to Qwen-VL.")
                    elements = process_qwen(pdf_path, repo_link)
                    tables.extend([e for e in elements if e["type"] == "table"])
                    elements = [e for e in elements if e["type"] != "table"]
                    tables = _assign_unique_table_ids(tables, "qwen_fallback")

            merge_instructions = {
                t["table_id"]: t["merge_target_id"]
                for t in human_tables
                if t.get("status") == "merge" and t.get("merge_target_id")
            }
            final_tables = extract_tables(
                pdf_path, repo_link, doc, tables, merge_instructions
            )

            for table in human_tables:
                if table.get("status") in ["approved", "edited", "added"]:
                    page = table.get("page", table.get("page_range", [0])[0])
                    encoding = _get_encoder()
                    final_tables.append(
                        {
                            "type": "table",
                            "table_id": table.get("table_id"),
                            "header": table.get("header", []),
                            "body": table.get("body", []),
                            "page": page,
                            "page_range": table.get("page_range", (page, page)),
                            "bbox": table.get("bbox"),
                            "token_count": len(
                                encoding.encode(
                                    json.dumps(
                                        {
                                            "header": table.get("header", []),
                                            "body": table.get("body", []),
                                        }
                                    )
                                )
                            ),
                            "file_path": pdf_path,
                            "repo_link": repo_link,
                            "above_text": table.get("above_text"),
                            "below_text": table.get("below_text"),
                            "title": table.get("title"),
                            "extraction_date": datetime.datetime.now().isoformat(),
                            "source": f"human_{table.get('status')}",
                            "needs_review": False,
                            "human_comment": table.get("comment"),
                        }
                    )

            final_data = elements + final_tables
            for elem in elements:
                elem.update(
                    {
                        "file_path": pdf_path,
                        "repo_link": repo_link,
                        "extraction_date": datetime.datetime.now().isoformat(),
                    }
                )
            final_data.sort(key=lambda x: x.get("page", 0))

            with open(output_json, "w", encoding="utf-8") as f:
                json.dump(final_data, f, indent=2, ensure_ascii=False)
            return final_data
    except Exception as e:
        logger.error(f"PDF conversion failed: {e}")
        return []


def usage_function():
    """Demonstrates PDF conversion."""
    return convert_pdf("sample.pdf", "https://repo")


if __name__ == "__main__":
    result = usage_function()
    print("PDF Conversion Result:")
    print(json.dumps(result, indent=2))
