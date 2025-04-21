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

import json
import os
import tempfile
from pathlib import Path
from typing import List, Dict, Any
from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from loguru import logger
import uvicorn

from .config import DEFAULT_OUTPUT_DIR, DEFAULT_CORRECTIONS_DIR
from ._archive.pdf_to_json_converter import convert_pdf_to_json
from .utils import fix_sys_path

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
    data: List[Dict[str, Any]]

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
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        logger.error(f"Invalid file type: {file.filename}")
        raise HTTPException(status_code=400, detail="Only PDF files are supported.")

    # Check directory permissions
    for dir_path in [output_dir, corrections_dir]:
        dir_obj = Path(dir_path)
        try:
            dir_obj.mkdir(parents=True, exist_ok=True)
            if not os.access(dir_obj, os.W_OK):
                raise PermissionError(f"Write permission denied for {dir_path}")
        except Exception as e:
            logger.error(f"Failed to access directory {dir_path}: {e}")
            raise HTTPException(status_code=500, detail=f"Directory access failed: {str(e)}")

    logger.info(f"Processing file: {file.filename}, repo_link: {repo_link}")
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as temp_file:
            temp_file.write(await file.read())
            temp_path = temp_file.name
        result = convert_pdf_to_json(
            pdf_path=temp_path,
            repo_link=repo_link,
            output_dir=output_dir,
            use_marker_markdown=use_marker_markdown,
            corrections_dir=corrections_dir,
            force_qwen=force_qwen
        )
        if not result:
            logger.warning("No data extracted from PDF.")
            return ConversionResponse(
                status="success",
                message="No content extracted from the PDF.",
                data=[]
            )
        logger.info(f"Extracted {len(result)} elements from {file.filename}")
        return ConversionResponse(
            status="success",
            message=f"PDF converted successfully. Extracted {len(result)} elements.",
            data=result
        )
    except Exception as e:
        logger.error(f"Conversion failed: {e}")
        raise HTTPException(status_code=500, detail=f"Conversion failed: {str(e)}")
    finally:
        if temp_path and os.path.exists(temp_path):
            try:
                os.unlink(temp_path)
            except Exception as e:
                logger.warning(f"Failed to delete temporary file {temp_path}: {e}")

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
    sample_pdf = "input/BHT_CV32A65X.pdf"
    repo_link = "https://github.com/example/repo"
    try:
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
    fix_sys_path(__file__)
    logger.info("Testing API usage function...")
    result = usage_function()
    print("API Usage Function Result:")
    print(json.dumps(result, indent=2))
    logger.info("Starting FastAPI server...")
    uvicorn.run(app, host="0.0.0.0", port=8000)