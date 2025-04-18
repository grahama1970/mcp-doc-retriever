# src/mcp_doc_retriever/context7/core.py
"""
Module: core.py
Description: Contains the core logic for the MCP Document Retriever, including repository processing, database setup, and interactions with LiteLLM.
"""

import asyncio
import json
import os
from pathlib import Path
from typing import List, Optional

from deepmerge import always_merger
from loguru import logger
from pydantic import ValidationError

# Local imports
from mcp_doc_retriever.context7 import arango_setup  # Import the whole module
from mcp_doc_retriever.context7.embedding_utils import get_embedding
from mcp_doc_retriever.context7.file_discovery import find_relevant_files
from mcp_doc_retriever.context7.json_utils import clean_json_string
from mcp_doc_retriever.context7.litellm_call import litellm_call
from mcp_doc_retriever.context7.log_utils import log_safe_results
from mcp_doc_retriever.context7.markdown_extractor import extract_from_markdown
from mcp_doc_retriever.context7.notebook_extractor import extract_from_ipynb
from mcp_doc_retriever.context7.rst_extractor import extract_from_rst
from mcp_doc_retriever.context7.sparse_checkout import sparse_checkout
from mcp_doc_retriever.context7.text_chunker import TextChunker
from mcp_doc_retriever.context7.models import ExtractedCode  # Imported the model
from mcp_doc_retriever.context7 import config  # Import config

# NOTE Ensure that PERPLEXITY_API_KEY and ARANGO_PASSWORD are setup properly in ENV

# --- Load Environment Variables ---
ARANGO_HOST = os.getenv("ARANGO_HOST", "http://localhost:8529")
ARANGO_USER = os.getenv("ARANGO_USER", "root")
ARANGO_PASSWORD = os.getenv("ARANGO_PASSWORD", "openSesame")
ARANGO_DB_NAME = os.getenv("ARANGO_DB_NAME", "doc_retriever")
COLLECTION_NAME = os.getenv("ARANGO_COLLECTION_NAME", "lessons_learned")
EDGE_COLLECTION_NAME = os.getenv("ARANGO_EDGE_COLLECTION_NAME", "relationships")
GRAPH_NAME = os.getenv("ARANGO_GRAPH_NAME", "lessons_graph")
SEARCH_VIEW_NAME = os.getenv("SEARCH_VIEW_NAME", "lessons_view")
VECTOR_INDEX_NAME = os.getenv("VECTOR_INDEX_NAME", "idx_lesson_embedding")
EMBEDDING_FIELD = os.getenv("EMBEDDING_FIELD", "embedding")

# Setting environment variables
os.environ["ARANGO_HOST"] = ARANGO_HOST
os.environ["ARANGO_USER"] = ARANGO_USER
os.environ["ARANGO_DB_NAME"] = ARANGO_DB_NAME
os.environ["COLLECTION_NAME"] = COLLECTION_NAME
os.environ["EDGE_COLLECTION_NAME"] = EDGE_COLLECTION_NAME
os.environ["GRAPH_NAME"] = GRAPH_NAME
os.environ["SEARCH_VIEW_NAME"] = SEARCH_VIEW_NAME
os.environ["VECTOR_INDEX_NAME"] = VECTOR_INDEX_NAME
os.environ["EMBEDDING_FIELD"] = EMBEDDING_FIELD

# Log confirmation
logger.info(f"ARANGO_HOST: {ARANGO_HOST}")
logger.info(f"ARANGO_USER: {ARANGO_USER}")
logger.info(f"ARANGO_DB_NAME: {ARANGO_DB_NAME}")
logger.info(f"COLLECTION_NAME: {COLLECTION_NAME}")
logger.info(f"EDGE_COLLECTION_NAME: {EDGE_COLLECTION_NAME}")
logger.info(f"GRAPH_NAME: {GRAPH_NAME}")
logger.info(f"SEARCH_VIEW_NAME: {SEARCH_VIEW_NAME}")
logger.info(f"VECTOR_INDEX_NAME: {VECTOR_INDEX_NAME}")
logger.info(f"EMBEDDING_FIELD: {EMBEDDING_FIELD}")


async def verify_repo_url(repo_url: str) -> Optional[str]:
    """
    Verifies the repository URL using a LiteLLM call to Perplexity AI.
    Returns the corrected URL or None if verification fails.
    Uses configuration defined in the settings.
    """
    try:
        # Load all LLM configs from project settings
        from mcp_doc_retriever.context7.config import (
            MCP_LLM_MODEL,
            MCP_LLM_API_BASE
        )
        
        schema= {
            "verified": "boolean, Return True if the repo exist, Return False if not", 
            "url": "the url of the repo." 
        }
        defaults = {
            "llm_config": {
                "api_base": os.getenv("PERPLEXITY_API_BASE", MCP_LLM_API_BASE),
                "model": os.getenv("PERPLEXITY_MODEL", MCP_LLM_MODEL),
                "messages": [
                    {
                        "role": "system",
                        "content": (
                            "You are helpful agent that can validate if a repository exist if you are provided with the name. "
                            "You must return in well formatted JSON only with the following schema: "
                            f"{json.dumps(schema)}"
                        ),
                    },
                    {
                        "role": "user",
                        "content": f"Is this a valid repository URL? If not correct it else say that it can't find it. Return in JSON  {repo_url}",
                    },
                ],
            }
        }
        # from mcp_doc_retriever.context7 import config

        llm_call_config = always_merger.merge(config, defaults)
        response = await litellm_call(llm_call_config)
        content = response.choices[0].message.content
        logger.info(content)
        result = clean_json_string(content, return_dict=True)
        return (result.get("verified", None), result.get("url", None))
    except Exception as e:
        logger.error(f"Failed to verify repository URL: {e}")
        return None


async def embed_and_upsert_data(validated_data: ExtractedCode) -> None:
    """Performs embedding generation and calls Arango to store the code asynchronously."""
    if not validated_data:
        logger.warning("Skipping this data as not valid")
        return

    logger.info(
        f"Storing to arango: {validated_data.file_path=}, {validated_data.section_id=}"
    )

    # Connect to ArangoDB (moved here to avoid global connection issues)
    try:
        client = await asyncio.to_thread(arango_setup.connect_arango)
        if not client:
            logger.error("Failed to connect to ArangoDB.")
            return

        db = client.db(ARANGO_DB_NAME, username=ARANGO_USER, password=ARANGO_PASSWORD)
        collection = db.collection(COLLECTION_NAME)

        # Prepare the document for insertion/update
        doc = validated_data.dict()
        key = doc.get("section_id")  # Use code_id or another unique identifier as _key
        doc["_key"] = key  # Ensure _key is present

        # Attempt to insert or update the document (using _key)
        try:
            # THIS is the AYSNC
            meta = await asyncio.to_thread(
                collection.insert, doc, overwrite=True
            )  # overwrite allows upsert
            logger.info(
                f"Successfully inserted/updated document with key '{meta['_key']}'."
            )
            return True

        except Exception as insert_err:
            logger.error(
                f"Failed to insert/update document: {insert_err}", exc_info=True
            )
            

    except Exception as db_err:
        logger.error(
            f"Error connecting to or interacting with ArangoDB: {db_err}", exc_info=True
        )
    return False


def process_repository(repo_url: str, output_dir: Path, exclude_patterns: List[str]):
    """
    Downloads, discovers files, extracts content, generates embeddings, and stores data for a given repository.
    """
    logger.info(f"Processing repository: {repo_url}")

    verified_url = asyncio.run(verify_repo_url(repo_url))
    if verified_url:
        logger.info(f"Verified repository URL: {verified_url}")
    else:
        logger.error(f"Could not verify repository URL: {repo_url}. Skipping.")
        return

    success = sparse_checkout(verified_url, str(output_dir), ["docs/*"])
    if success:
        logger.info("Checkout Complete")

        relevant_files = find_relevant_files(str(output_dir), exclude_patterns)
        if not relevant_files:
            logger.warning(f"No relevant files found in {output_dir}.")
            return

        for file_path in relevant_files:
            logger.info(f"Processing file: {file_path}")
            repo_link = f"{verified_url}/blob/main/{Path(file_path).name}"

            if file_path.endswith((".md", ".mdx")):
                extracted_data = extract_from_markdown(file_path, repo_link)
            elif file_path.endswith(".ipynb"):
                extracted_data = extract_from_ipynb(file_path, repo_link)
            elif file_path.endswith(".rst"):
                extracted_data = extract_from_rst(file_path, repo_link)
            else:
                logger.warning(f"Unsupported file type: {file_path}. Skipping.")
                continue

            # Create instance of the TextChunker
            text_chunker = TextChunker()

            # Chunk the data
            extracted_data = text_chunker.chunk_text(
                file_path, repo_link, str(Path(file_path).name)
            )

            for data in extracted_data:
                # Generate embeddings
                code_embedding = get_embedding(data["code"])
                description_embedding = get_embedding(data["description"])

                # Add embeddings to the data dictionary
                data["embedding_code"] = code_embedding
                data["embedding_description"] = description_embedding

                # Data validation
                try:
                    validated_data = ExtractedCode(**data)
                    logger.debug(f"Successfully validated chunk data")
                    asyncio.run(embed_and_upsert_data(validated_data))
                except ValidationError as e:
                    logger.error(f"Validation error for chunk: {e}")
                    # extracted_data[i] = None # Skip insertion or handle invalid data differently
                    pass  # or continue

            if extracted_data:
                logger.info(f"Extracted data")
            else:
                logger.info("No content extracted from this file.")

    else:
        logger.error("Checkout error")


def setup_database(
    host: str,
    db_name: str,
    truncate: bool,
    seed_file: Optional[str] = None,
    force: bool = False,
    skip_setup: bool = False,
):
    """
    Sets up the ArangoDB database, including connection, truncation, seeding, and ensuring collections, views, and indexes.
    """
    logger.info(f"Setting up ArangoDB database: {db_name} on {host}")

    # Use the arango_setup module's initialize_database function
    # Assuming initialize_database is the main setup function in that module
    db = arango_setup.initialize_database(
        run_setup=not skip_setup,
        truncate=truncate,
        force_truncate=force,
        seed_file_path=seed_file,
    )

    if db:
        logger.success(f"Successfully initialized database '{db_name}'.")
        return db
    else:
        logger.error(f"Failed to initialize database '{db_name}'.")


async def test_all() -> None:
    """runs tests to ensure that the code is working.
    To ensure that all tests work please make sure:
    Arango is loaded and set up, and running. It is ok to run in docker.
    Use a github and to load it
    add the perplexity Key
    If any function fails, fix it and I expect it is fixed
    """
    # Test function to test things and now everything async
    # You need to have the ARANGO key setup to be proper
    print("Verify repo test - step 1")
    test_repo = "https://github.com/fastapi/fastapi.git"
    logger.info("Running Test of github access")
    verified, url = await verify_repo_url(test_repo)
    if url != test_repo and verified != True:
        logger.info("Github test does not")
        raise AssertionError(
            f"Verification test to access and confirm {test_repo} did not load"
        )

    # Test values (for the test, don't rely on a full database)
    print("Set up test - test 2")
    db = await asyncio.to_thread(setup_database,
        host="http://localhost:8529",
        db_name="test_context7",
        truncate=True,
        seed_file=None,
        force=True,
        skip_setup=True,
    )
    if not db:
        raise AssertionError("Setup database did not complete. Check the logs!")

    # Verify functions to do next
    print("Test data load  - step 3")
    test_data = {
        "file_path": "test_file.md",
        "repo_link": "https://github.com/fastapi/fastapi.git",
        "extraction_date": "2024-04-18T12:00:00",
        "code_line_span": (1, 5),
        "description_line_span": (1, 2),
        "code": "def hello():\n print('hello')",
        "code_type": "python",
        "description": "A simple hello function",
        "code_token_count": 10,
        "description_token_count": 4,
        "embedding_code": [0.1] * 1536,  # Placeholder embedding for code
        "embedding_description": [0.2] * 1536,  # Placeholder embedding for description
        "code_metadata": {},
        "section_id": "test_section_id",
        "section_path": [],
        "section_hash_path": [],
    }
    validated_data = ExtractedCode(**test_data)

    print("embed_and_upsert_data() Test Starting ")
    embed_succsessful: bool = await embed_and_upsert_data(validated_data)
    if embed_succsessful:
            safe_for_logs = log_safe_results(validated_data.model_dump())
            print(f"It worked to load and test {safe_for_logs}")
            


def usage_function():
    # all code should be triggered by now so nothing is triggered here
    # test_process_repository(test_repo, test_output, ["bad"])

    print("It workds to run")


# code to trigger the function
if __name__ == "__main__":
    print("Starting!")
    # async functions must not be defined to code that you can run
    asyncio.run(test_all())
    print("Finished Running Tests, it worked! Congratulations")
