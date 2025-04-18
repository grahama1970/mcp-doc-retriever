# src/mcp_doc_retriever/api.py

"""
Module: api.py
Description: FastAPI endpoints for the MCP Document Retriever, calling core logic.
"""

import asyncio
import json
import re
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, BackgroundTasks, HTTPException
from loguru import logger
from sse_starlette.sse import EventSourceResponse

# Import models from the new models file
from pydantic import BaseModel, Field

# Import core logic from the correct path
from mcp_doc_retriever.context7.core import process_repository, setup_database

# Create FastAPI app and router
router = APIRouter()
# app = FastAPI()

# Include the router
# app.include_router(router)


# --- Data Models ---
# Define request and response models (can be moved to a separate models.py)
class ProcessRepoRequest(BaseModel):
    repo_urls: List[str]
    output_dir: Path = Path("./data")
    exclude_patterns: Optional[List[str]] = None


class SetupDbRequest(BaseModel):
    host: str = "http://localhost:8529"
    db_name: str = "mcp_docs"
    truncate: bool = False
    seed_file: Optional[str] = None
    force: bool = False
    skip_setup: bool = False


class Message(BaseModel):
    message: str


# --- API Endpoints ---
@router.post("/process-repo", response_model=Message)
async def process_repo_endpoint(request: ProcessRepoRequest):
    """
    Downloads and processes a list of documentation repositories.
    """
    try:
        for repo_url in request.repo_urls:
            process_repository(
                repo_url=repo_url,
                output_dir=request.output_dir,
                exclude_patterns=request.exclude_patterns,
            )
        return {"message": "Repository processing completed successfully."}
    except Exception as e:
        logger.error(f"Error processing repositories: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/setup-db", response_model=Message)
async def setup_db_endpoint(request: SetupDbRequest):
    """
    Sets up the ArangoDB database.
    """
    try:
        setup_database(
            host=request.host,
            db_name=request.db_name,
            truncate=request.truncate,
            seed_file=request.seed_file,
            force=request.force,
            skip_setup=request.skip_setup,
        )
        return {"message": "Database setup completed successfully."}
    except Exception as e:
        logger.error(f"Error setting up database: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/")
async def mcp_sse():
    """SSE endpoint for MCP protocol connection (placeholder)."""

    async def event_generator():
        yield {
            "event": "connected",
            "data": json.dumps(
                {
                    "service": "DocRetriever",
                    # Version info is part of the main app instance, not needed here directly
                    # If needed, it could be passed via dependency injection or config
                    "version": "1.0.0",  # Placeholder or fetch from config if needed
                    "capabilities": [
                        "document_download",
                        "document_search",
                        "task_status",
                    ],
                }
            ),
        }
        while True:
            await asyncio.sleep(15)
            yield {"event": "heartbeat", "data": json.dumps({"timestamp": time.time()})}

    return EventSourceResponse(event_generator())


@router.get("/health")
async def health_check():
    """
    Health check endpoint.
    """
    return {"status": "ok"}
