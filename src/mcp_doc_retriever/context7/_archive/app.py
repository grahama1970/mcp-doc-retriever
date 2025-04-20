"""
Flask API for PDF-to-JSON conversion and Label Studio integration.

This module provides endpoints to upload PDFs, extract structured JSON, serve PDF
pages as PNG images, save Label Studio corrections, and re-extract PDFs. It
integrates with the `pdf_converter` module for core conversion logic.

Dependencies:
- flask: For building the API.
- pdf2image: For converting PDF pages to images.
- python-dotenv: For environment variable loading.
- loguru: For logging.

Usage:
    Run the server:
    ```bash
    python app.py
    ```

    Get auth token:
    ```bash
    curl http://localhost:5000/auth
    ```

    Upload PDF:
    ```bash
    curl -X POST "http://localhost:5000/upload" \
         -H "X-Auth-Token: <token>" \
         -F "file=@sample.pdf" \
         -F "repo_link=https://github.com/example/repo"
    ```

    Serve PDF page:
    ```bash
    curl "http://localhost:5000/pdf/sample/page/1" -o page1.png
    ```

    Save corrections:
    ```bash
    curl -X POST "http://localhost:5000/save/sample" \
         -H "X-Auth-Token: <token>" \
         -H "Content-Type: application/json" \
         -d '{"result": [...]}'
    ```

    Re-extract PDF:
    ```bash
    curl -X POST "http://localhost:5000/reextract/sample" \
         -H "X-Auth-Token: <token>" \
         -F "repo_link=https://github.com/example/repo"
    ```
"""

import os
import json
import tempfile
import uuid
import datetime
from functools import wraps
from pathlib import Path
from flask import Flask, request, send_file, jsonify
from pdf2image import convert_from_path
from dotenv import load_dotenv
from loguru import logger

from ..pdf_extractor.config import DEFAULT_OUTPUT_DIR, DEFAULT_CORRECTIONS_DIR
from ..pdf_extractor._archive.pdf_converter import convert_pdf

# Load environment variables
load_dotenv()

app = Flask(__name__)

# Configuration
BASE_DIR = Path(__file__).parent
UPLOAD_DIR = Path(os.getenv("UPLOAD_DIR", BASE_DIR / "uploads"))
FLASK_HOST = os.getenv("FLASK_HOST", "0.0.0.0")
FLASK_PORT = int(os.getenv("FLASK_PORT", 5000))
PDF_PAGE_URL_PREFIX = os.getenv(
    "PDF_PAGE_URL_PREFIX", f"http://{FLASK_HOST}:{FLASK_PORT}/pdf"
)
DEFAULT_REPO_LINK = os.getenv("DEFAULT_REPO_LINK", "https://example.com/default-repo")

# Ensure directories exist
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
Path(DEFAULT_OUTPUT_DIR).mkdir(parents=True, exist_ok=True)
Path(DEFAULT_CORRECTIONS_DIR).mkdir(parents=True, exist_ok=True)

# Simple in-memory authentication (replace with proper auth in production)
AUTH_TOKENS = {}


def require_auth(f):
    """Decorator for authentication."""

    @wraps(f)
    def decorated_function(*args, **kwargs):
        auth_token = request.headers.get("X-Auth-Token")
        if not auth_token or auth_token not in AUTH_TOKENS:
            logger.warning(f"Authentication failed. Token: {auth_token}")
            return jsonify({"error": "Authentication required or invalid token"}), 401
        logger.debug("Authentication successful.")
        return f(*args, **kwargs)

    return decorated_function


@app.route("/auth", methods=["GET"])
def get_auth_token():
    """Generates an authentication token."""
    token = str(uuid.uuid4())
    AUTH_TOKENS[token] = True
    logger.info(f"Generated auth token: {token}")
    return jsonify({"token": token}), 200


@app.route("/upload", methods=["POST"])
@require_auth
def upload_pdf():
    """Uploads a PDF and generates Label Studio tasks for tables."""
    if "file" not in request.files:
        logger.warning("No file part in request.")
        return jsonify({"error": "No file part in the request"}), 400
    file = request.files["file"]
    if not file or file.filename == "":
        logger.warning("No file selected.")
        return jsonify({"error": "No file selected"}), 400
    if not file.filename.lower().endswith(".pdf"):
        logger.warning(f"Invalid file type: {file.filename}")
        return jsonify({"error": "Only PDF files are allowed"}), 400

    pdf_filename = Path(file.filename).name
    pdf_id = Path(pdf_filename).stem
    pdf_path = UPLOAD_DIR / pdf_filename

    try:
        file.save(pdf_path)
        logger.info(f"Saved uploaded file: {pdf_path}")
    except Exception as e:
        logger.error(f"Error saving file '{pdf_filename}': {e}")
        return jsonify({"error": f"Error saving file: {e}"}), 500

    repo_link = request.form.get("repo_link", DEFAULT_REPO_LINK)
    logger.info(f"Using repo link: {repo_link}")

    try:
        extracted_data = convert_pdf(
            pdf_path=str(pdf_path),
            repo_link=repo_link,
            output_dir=DEFAULT_OUTPUT_DIR,
            corrections_dir=DEFAULT_CORRECTIONS_DIR,
        )
        logger.info(f"Extracted {len(extracted_data)} elements for {pdf_id}.")
    except Exception as e:
        logger.error(f"Conversion failed for '{pdf_id}': {e}")
        pdf_path.unlink(missing_ok=True)
        return jsonify({"error": f"PDF conversion failed: {e}"}), 500

    tasks = []
    table_count = 0
    for item in extracted_data:
        if item.get("type") == "table":
            table_count += 1
            page = item.get("page", item.get("page_range", [0])[0])
            table_id = item.get("table_id", f"unknown_{pdf_id}_p{page}_t{table_count}")
            bbox = item.get("bbox", [0.0, 0.0, 1.0, 1.0])
            if isinstance(bbox, (list, tuple)) and len(bbox) == 4:
                try:
                    bbox = [float(b) for b in bbox]
                except (ValueError, TypeError):
                    bbox = [0.0, 0.0, 1.0, 1.0]
            tasks.append(
                {
                    "id": table_id,
                    "data": {
                        "pdf_page_url": f"{PDF_PAGE_URL_PREFIX}/{pdf_id}/page/{page}",
                        "table_id": table_id,
                        "source": item.get("source", "unknown"),
                        "needs_review": item.get("needs_review", False),
                        "table_data": {
                            "header": item.get("header", []),
                            "body": item.get("body", []),
                        },
                    },
                }
            )

    tasks_filename = f"{pdf_id}_tasks.json"
    tasks_path = Path(DEFAULT_CORRECTIONS_DIR) / tasks_filename
    try:
        with open(tasks_path, "w", encoding="utf-8") as f:
            json.dump(tasks, f, indent=2, ensure_ascii=False)
        logger.info(f"Saved {len(tasks)} tasks to: {tasks_path}")
    except Exception as e:
        logger.error(f"Error saving tasks for '{pdf_id}': {e}")
        pdf_path.unlink(missing_ok=True)
        return jsonify({"error": f"Error saving tasks JSON: {e}"}), 500

    return jsonify(
        {
            "message": f"PDF '{pdf_filename}' uploaded and processed.",
            "pdf_id": pdf_id,
            "table_tasks_generated": len(tasks),
            "tasks_file": str(tasks_path),
        }
    ), 200


@app.route("/pdf/<pdf_id>/page/<int:page>", methods=["GET"])
def serve_pdf_page(pdf_id, page):
    """Serves a PDF page as a PNG image."""
    pdf_path = UPLOAD_DIR / f"{pdf_id}.pdf"
    if not pdf_path.is_file():
        logger.warning(f"PDF not found: {pdf_path}")
        return jsonify({"error": f"PDF '{pdf_id}.pdf' not found"}), 404
    if page <= 0:
        logger.warning(f"Invalid page number: {page}")
        return jsonify({"error": "Page number must be positive"}), 400

    try:
        with tempfile.TemporaryDirectory() as temp_dir:
            images = convert_from_path(
                pdf_path,
                first_page=page,
                last_page=page,
                dpi=200,
                fmt="png",
                output_folder=temp_dir,
            )
            if not images:
                logger.warning(f"Page {page} not found in {pdf_id}.")
                return jsonify({"error": f"Page {page} not found"}), 404
            image_path = Path(temp_dir) / list(Path(temp_dir).glob("*.png"))[0]
            logger.debug(f"Serving image: {image_path}")
            return send_file(image_path, mimetype="image/png")
    except Exception as e:
        logger.error(f"Error serving page {page} for '{pdf_id}': {e}")
        return jsonify({"error": f"Failed to serve page: {e}"}), 500


@app.route("/save/<pdf_id>", methods=["POST"])
@require_auth
def save_corrections(pdf_id):
    """Saves Label Studio annotations to corrections JSON."""
    pdf_path = UPLOAD_DIR / f"{pdf_id}.pdf"
    if not pdf_path.is_file():
        logger.error(f"PDF not found: {pdf_path}")
        return jsonify({"error": f"PDF '{pdf_id}.pdf' not found"}), 404

    try:
        payload = request.get_json(force=True)
        annotations_list = payload if isinstance(payload, list) else [payload]
        if not annotations_list:
            logger.warning("No valid annotations in payload.")
            return jsonify({"error": "No valid annotations found"}), 400
    except Exception as e:
        logger.error(f"Error decoding JSON: {e}")
        return jsonify({"error": f"Invalid JSON format: {e}"}), 400

    corrections = {
        "pdf_id": pdf_id,
        "pdf_path": str(pdf_path),
        "tables": [],
        "correction_timestamp": datetime.datetime.now().isoformat(),
    }

    for annotation in annotations_list:
        result_list = annotation.get("result", [])
        task_data = annotation.get("task", {}).get("data", {})
        table_id = task_data.get("table_id", annotation.get("id"))
        if not table_id:
            logger.warning(f"Skipping annotation: No table_id.")
            continue

        page = task_data.get("page", 0)
        if not page:
            try:
                page = int(table_id.split("_p")[1].split("_")[0])
            except Exception:
                page = 0

        table_data, bbox_data, status, merge_target_id, comment = (
            {},
            None,
            None,
            None,
            None,
        )
        for res in result_list:
            value = res.get("value", {})
            from_name = res.get("from_name")
            if res.get("type") == "rectanglelabels" and from_name == "table_bbox_label":
                if all(k in value for k in ["x", "y", "width", "height"]):
                    bbox_data = [
                        value["x"],
                        value["y"],
                        value["x"] + value["width"],
                        value["y"] + value["height"],
                    ]
            elif res.get("type") == "table" and from_name == "table_data":
                table_data = value
            elif res.get("type") == "choices" and from_name == "validation_status":
                status = (
                    value.get("choices", [None])[0].lower()
                    if value.get("choices")
                    else None
                )
            elif res.get("type") == "textarea" and from_name == "merge_instruction":
                merge_target_id = (
                    value.get("text", [None])[0].strip() if value.get("text") else None
                )
            elif res.get("type") == "textarea" and from_name == "comment":
                comment = (
                    value.get("text", [None])[0].strip() if value.get("text") else None
                )

        if status and status != "reject":
            table_entry = {
                "table_id": table_id,
                "page": page,
                "page_range": (page, page),
                "header": table_data.get(
                    "header", task_data.get("table_data", {}).get("header", [])
                ),
                "body": table_data.get(
                    "body", task_data.get("table_data", {}).get("body", [])
                ),
                "bbox": bbox_data,
                "source": task_data.get("source", "unknown"),
                "status": status,
            }
            if status == "merge" and merge_target_id:
                table_entry["merge_target_id"] = merge_target_id
            if comment:
                table_entry["comment"] = comment
            corrections["tables"].append(table_entry)

    corrections_path = Path(DEFAULT_CORRECTIONS_DIR) / f"{pdf_id}_corrections.json"
    try:
        with open(corrections_path, "w", encoding="utf-8") as f:
            json.dump(corrections, f, indent=2, ensure_ascii=False)
        logger.info(f"Saved corrections to: {corrections_path}")
    except Exception as e:
        logger.error(f"Error saving corrections for '{pdf_id}': {e}")
        return jsonify({"error": f"Error saving corrections: {e}"}), 500

    return jsonify(
        {
            "message": f"Corrections saved for {pdf_id}.",
            "corrections_file": str(corrections_path),
        }
    ), 200


@app.route("/reextract/<pdf_id>", methods=["POST"])
@require_auth
def reextract(pdf_id):
    """Re-extracts a PDF using saved corrections."""
    pdf_path = UPLOAD_DIR / f"{pdf_id}.pdf"
    if not pdf_path.is_file():
        logger.error(f"PDF not found: {pdf_path}")
        return jsonify({"error": f"PDF '{pdf_id}.pdf' not found"}), 404

    repo_link = request.form.get("repo_link", DEFAULT_REPO_LINK)
    try:
        extracted_data = convert_pdf(
            pdf_path=str(pdf_path),
            repo_link=repo_link,
            output_dir=DEFAULT_OUTPUT_DIR,
            corrections_dir=DEFAULT_CORRECTIONS_DIR,
        )
        output_json_path = Path(DEFAULT_OUTPUT_DIR) / f"{pdf_id}_structured.json"
        logger.info(f"Re-extracted {len(extracted_data)} elements for {pdf_id}.")
        return jsonify(
            {
                "message": "Re-extraction successful.",
                "output_file": str(output_json_path)
                if output_json_path.exists()
                else None,
                "elements_extracted": len(extracted_data),
            }
        ), 200
    except Exception as e:
        logger.error(f"Re-extraction failed for '{pdf_id}': {e}")
        return jsonify({"error": f"Re-extraction failed: {e}"}), 500


def usage_function():
    """Simulates API usage by mimicking an upload and conversion."""
    sample_pdf = "sample.pdf"
    repo_link = "https://github.com/example/repo"
    try:
        result = convert_pdf(
            pdf_path=sample_pdf,
            repo_link=repo_link,
            output_dir=DEFAULT_OUTPUT_DIR,
            corrections_dir=DEFAULT_CORRECTIONS_DIR,
        )
        return {
            "message": f"PDF '{sample_pdf}' processed.",
            "elements_extracted": len(result),
        }
    except Exception as e:
        return {"error": f"Conversion failed: {str(e)}"}


if __name__ == "__main__":
    log_file_path = Path(DEFAULT_OUTPUT_DIR) / "pdf_extractor_api.log"
    logger.add(log_file_path, rotation="10 MB", level="DEBUG")
    logger.add(lambda msg: print(msg, end=""), level="INFO")

    logger.info("Starting PDF Extractor Flask API")
    logger.info(f"Host: {FLASK_HOST}, Port: {FLASK_PORT}")
    logger.info(f"Upload Dir: {UPLOAD_DIR}, Output Dir: {DEFAULT_OUTPUT_DIR}")
    logger.info(f"Corrections Dir: {DEFAULT_CORRECTIONS_DIR}")
    logger.info(f"PDF Page URL Prefix: {PDF_PAGE_URL_PREFIX}")

    result = usage_function()
    print("Usage Function Result:")
    print(json.dumps(result, indent=2))

    app.run(host=FLASK_HOST, port=FLASK_PORT, debug=False)
