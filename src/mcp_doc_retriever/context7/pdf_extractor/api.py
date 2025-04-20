"""
FastAPI endpoints for PDF-to-JSON conversion.

This module provides a RESTful API to upload PDF files, process them using the
PDF extraction pipeline, and return structured JSON output. It integrates with
the `pdf_converter` module to handle conversion tasks.

Dependencies:
- fastapi: For building the API.
- pydantic: For request validation.
- uvicorn: For running the server.
- loguru: For logging.
- python-multipart: For file uploads.

Usage:
    Run the server:
    ```bash
    uvicorn api:app --host 0.0.0.0 --port 8000
    ```

    Upload a PDF:
    ```bash
    curl -X POST "http://localhost:8000/convert" \
         -F "file=@sample.pdf" \
         -F "repo_link=https://github.com/example/repo"
    ```

    Check status:
    ```bash
    curl http://localhost:8000/status
    ```
"""

import os
from pathlib import Path # Import Path
import sys # Import sys
import tempfile
import json
from typing import List, Dict, Any
from typing import List, Dict
from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from loguru import logger
import uvicorn

from .config import DEFAULT_OUTPUT_DIR, DEFAULT_CORRECTIONS_DIR
from .pdf_to_json_converter import convert_pdf_to_json

# Initialize FastAPI app
app = FastAPI(
    title="PDF to JSON Converter API",
    description="API for converting PDF files to structured JSON using Marker, Camelot, and Qwen-VL.",
    version="1.0.0"
)

# Pydantic model for response structure
class ConversionResponse(BaseModel):
    """Response model for conversion endpoint."""
    status: str
    message: str
    data: List[Dict[str, Any]] # Use Any for dictionary values

class StatusResponse(BaseModel):
    """Response model for status endpoint."""
    status: str
    message: str

@app.post("/convert", response_model=ConversionResponse)
async def convert_pdf_endpoint(
    file: UploadFile = File(...),
    repo_link: str = Form(...),
    use_marker_markdown: bool = Form(False),
    force_qwen: bool = Form(False),
    output_dir: str = Form(DEFAULT_OUTPUT_DIR),
    corrections_dir: str = Form(DEFAULT_CORRECTIONS_DIR)
):
    """
    Converts an uploaded PDF to structured JSON.

    Args:
        file: PDF file to process.
        repo_link: Repository link for metadata.
        use_marker_markdown: Use Marker's Markdown output if True.
        force_qwen: Force Qwen-VL processing if True.
        output_dir: Directory for JSON output.
        corrections_dir: Directory for correction files.

    Returns:
        ConversionResponse with status, message, and extracted data.
    """
    # Check if filename exists before calling lower()
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        logger.error(f"Invalid file type: {file.filename}")
        raise HTTPException(status_code=400, detail="Only PDF files are supported.")

    logger.info(f"Processing file: {file.filename}, repo_link: {repo_link}")

    try:
        # Save uploaded file temporarily
        with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as temp_file:
            temp_file.write(await file.read())
            temp_path = temp_file.name

        # Run conversion
        # Use the correct function name
        result = convert_pdf_to_json(
            pdf_path=temp_path,
            repo_link=repo_link,
            output_dir=output_dir,
            use_marker_markdown=use_marker_markdown,
            corrections_dir=corrections_dir,
            force_qwen=force_qwen
        )

        # Clean up
        os.unlink(temp_path)

        if not result:
            logger.warning("No data extracted from PDF.")
            return ConversionResponse(
                status="success",
                message="No content extracted from the PDF.",
                data=[] # Provide empty list for data
            )
        logger.info(f"Extracted {len(result)} elements from {file.filename}")
        return ConversionResponse(
            status="success",
            message="PDF converted successfully.",
            data=result
        )

    except Exception as e:
        logger.error(f"Conversion failed: {e}")
        raise HTTPException(status_code=500, detail=f"Conversion failed: {str(e)}")

@app.get("/status", response_model=StatusResponse)
async def status_endpoint():
    """
    Checks the API's status.

    Returns:
        StatusResponse with server status.
    """
    logger.info("Status check requested.")
    return StatusResponse(
        status="success",
        message="PDF to JSON Converter API is running."
    )

def usage_function():
    """
    Simulates API usage by running a conversion.

    Returns:
        dict: Simulated API response.
    """
    # Use a PDF from the correct input directory, relative to this script
    sample_pdf = "input/BHT_CV32A65X.pdf"
    repo_link = "https://github.com/example/repo"
    try:
        # Use the correct function name
        result = convert_pdf_to_json(sample_pdf, repo_link)
        return {
            "status": "success",
            "message": "PDF converted successfully.",
            "data": result
        }
    except Exception as e:
        return {
            "status": "error",
            "message": f"Conversion failed: {str(e)}",
            "data": []
        }

if __name__ == "__main__":
    # --- Fix for standalone execution ---
    # Add the 'src' directory to sys.path to allow relative imports
    project_root = Path(__file__).resolve().parents[3] # Go up 3 levels (pdf_extractor -> context7 -> mcp_doc_retriever -> src)
    src_path = project_root / "src"
    if str(src_path) not in sys.path:
        sys.path.insert(0, str(src_path))
        logger.info(f"Added {src_path} to sys.path for standalone run.")
    # Re-import necessary modules after path modification if needed,
    # but imports at top level should work now.
    # ------------------------------------

    # Test basic functionality
    logger.info("Testing API usage function...")
    result = usage_function()
    print("API Usage Function Result:")
    print(json.dumps(result, indent=2))

    # Note: The uvicorn server run below will likely prevent the script
    # from exiting cleanly after the usage_function test in standalone mode.
    # This is acceptable for verification purposes.

    # Run the FastAPI server
    logger.info("Starting FastAPI server...")
    uvicorn.run(app, host="0.0.0.0", port=8000)