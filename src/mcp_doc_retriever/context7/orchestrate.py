# src/mcp_doc_retriever/context7/orchestrate.py
import asyncio
import json
import os
from pathlib import Path
from typing import List, Optional, Dict

from deepmerge import always_merger
from loguru import logger
from pydantic import ValidationError
from tqdm import tqdm  # Import tqdm
import sys

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
GRAPH_NAME = os.getenv("GRAPH_NAME", "lessons_graph")
SEARCH_VIEW_NAME = os.getenv("SEARCH_VIEW_NAME", "lessons_view")
VECTOR_INDEX_NAME = os.getenv("VECTOR_INDEX_NAME", "idx_lesson_embedding")
EMBEDDING_FIELD = os.getenv("EMBEDDING_FIELD", "embedding")


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
            MCP_LLM_API_BASE,
        )

        schema = {
            "verified": "boolean, MUST be True if the repository exists and is accessible, otherwise MUST be False",
            "url": "string, the EXACT same URL as the input, or a corrected URL if the original was invalid. MUST be a valid URL.",
            "reason": "string, a brief explanation of why the URL is valid or invalid. MUST be a valid URL.",
        }

        defaults = {
            "llm_config": {
                "api_base": os.getenv("PERPLEXITY_API_BASE", MCP_LLM_API_BASE),
                "model": os.getenv("PERPLEXITY_MODEL", MCP_LLM_MODEL),
                "messages": [
                    {
                        "role": "system",
                        "content": (
                            "You are an expert at verifying Git repository URLs. "
                            f"You MUST determine if the given URL is a valid, accessible Git repository. "
                            f"If the URL is valid, set 'verified' to True and return the exact same URL. "
                            f"If the URL is invalid (e.g., broken link, typo), set 'verified' to False and provide the reason. "
                            f"You MUST return a JSON object with the following schema: {json.dumps(schema)}"
                        ),
                    },
                    {
                        "role": "user",
                        "content": f"Is this a valid Git repository URL? Return a JSON object: {repo_url}",
                    },
                ],
                "response_format": {"type": "json_object"},  # Force JSON output
            }
        }

        llm_call_config = always_merger.merge(config, defaults)
        response = await litellm_call(llm_call_config)
        content = response.choices[0].message.content
        logger.info(f"verify_repo_url LLM Response: {content}")  # Log the full response
        result = clean_json_string(content, return_dict=True)

        if not isinstance(result, dict):
            logger.warning(f"LLM did not return a JSON object. Returning failure.")
            return (False, repo_url)

        verified = result.get("verified")
        url = result.get("url")

        if verified is None or url is None:
            logger.warning(
                f"LLM response missing 'verified' or 'url'. Returning failure."
            )
            return (False, repo_url)

        return (verified, url)

    except Exception as e:
        logger.error(f"Failed to verify repository URL: ", exc_info=True)
        return (False, repo_url)


async def correct_repo_url(original_repo_url: str) -> Dict:
    """
    Uses LiteLLM to ask Perplexity to correct a repository URL.

    Returns:
        Dict: {"verified": true/false, "url": "corrected_url"}
    """
    from mcp_doc_retriever.context7.config import (
        MCP_LLM_MODEL,
        MCP_LLM_API_BASE,
    )

    schema = {
        "verified": "boolean, Return True if the repo exist and can be accessed, Return False if not",
        "url": "the CORRECTED url of the repo, or the original URL if no correction is possible.",
    }
    defaults = {
        "llm_config": {
            "api_base": os.getenv("PERPLEXITY_API_BASE", MCP_LLM_API_BASE),
            "model": os.getenv("PERPLEXITY_MODEL", MCP_LLM_MODEL),
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "You are an expert at identifying and correcting Git repository URLs. "
                        f"If the given URL is invalid, you MUST attempt to find the correct URL. Return ONLY a JSON object with the following schema: {json.dumps(schema)}"
                    ),
                },
                {
                    "role": "user",
                    "content": f"Is this a valid Git repository URL? If not, find the correct URL. Return a JSON object: {original_repo_url}",
                },
            ],
        }
    }
    llm_call_config = always_merger.merge(config, defaults)
    try:
        response = await litellm_call(llm_call_config)
        content = response.choices[0].message.content
        logger.info(f"correct_repo_url LLM Response: {content}")
        result = clean_json_string(content, return_dict=True)
        if not isinstance(result, dict):
            logger.warning(f"LLM did not return a JSON object. Returning failure.")
            return {"verified": False, "url": original_repo_url}  # Indicate failure
        return result
    except Exception as e:
        logger.error(f"correct_repo_url failed: {e}", exc_info=True)
        return {"verified": False, "url": original_repo_url}  # Indicate failure


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
        doc = validated_data.model_dump()
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


def process_repository_logic(
    repo_url: str, output_dir: Path, exclude_patterns: List[str]
):
    """
    Downloads, discovers files, extracts content, generates embeddings, and stores data for a given repository.
    """
    logger.info(f"Processing repository: {repo_url}")

    verified_url_result = asyncio.run(verify_repo_url(repo_url))
    if not verified_url_result or not verified_url_result[0]:
        logger.error(f"Could not verify repository URL: {repo_url}. Skipping.")
        return
    verified_url = verified_url_result[1]  # Extract the validated URL

    try:
        success = sparse_checkout(verified_url, str(output_dir), ["docs/*"])
        if success:
            logger.info("Checkout Complete")

            relevant_files = find_relevant_files(str(output_dir), exclude_patterns)
            if not relevant_files:
                logger.warning(f"No relevant files found in {verified_url}.")
                return

            # Redirect Loguru output to a file during tqdm loop
            log_file = "processing_log.txt"
            logger.add(log_file, level="DEBUG", format="{time} - {level} - {message}")
            import sys

            # Use tqdm for the file processing loop
            for file_path in tqdm(relevant_files, desc="Processing Files"):
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
                    logger.info(f"Extracted data from {file_path}")
                else:
                    logger.info(f"No content extracted from {file_path}.")

            logger.remove()
            logger.add(sys.stderr, level="INFO")

        else:
            logger.error("Checkout error")

    except Exception as e:  # subprocess.CalledProcessError as e:
        logger.error(f"Sparse checkout failed for {repo_url}: {e}")
        # --- Attempt to correct the repo URL ---
        logger.info(f"Attempting to correct repository URL using LiteLLM...")
        try:
            correction_result = asyncio.run(correct_repo_url(repo_url))
            if correction_result["verified"]:
                corrected_url = correction_result["url"]
                logger.info(f"Corrected repository URL: {corrected_url}")
                # Retry sparse checkout with the corrected URL
                success = sparse_checkout(corrected_url, str(output_dir), ["docs/*"])
                if success:
                    logger.info(f"Sparse checkout successful with corrected URL.")
                    # Recursively process the repository with the new repo_url
                    process_repository_logic(
                        corrected_url, output_dir, exclude_patterns
                    )
                else:
                    logger.error(
                        f"Sparse checkout failed even with corrected URL. Proceeding to the next repository."
                    )

            else:
                logger.warning(
                    f"Could not correct repository URL. Proceeding to the next repository."
                )

        except Exception as correction_err:
            logger.error(f"Failed to correct repository URL: {correction_err}")
            logger.error(f"Proceeding to the next repository.")


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


# Add database testing
import sys


async def test_all() -> None:
    """Runs tests to ensure that the code is working."""
    test_repo = "https://github.com/grahama1970/mcp-doc-retriever-test-repo.git"  # Replace with your test repo URL
    output_dir = Path("/tmp/mcp-doc-retriever-test-repo")
    exclude_patterns: List[str] = []

    logger.info(f"Running process_repository test with {test_repo}")

    # First delete existing data if it exists
    import shutil

    if os.path.exists(output_dir):
        shutil.rmtree(output_dir)
        logger.info(f"Removed existing test directory {output_dir}")
    db = setup_database(
        host="http://localhost:8529",
        db_name="test_context7",
        truncate=True,
        seed_file=None,
        force=True,
        skip_setup=True,
    )
    if not db:
        raise AssertionError("Setup database did not complete. Check the logs!")

    process_repository_logic(test_repo, output_dir, exclude_patterns)
    logger.info("Process test complete.")

    # Basic assertions to validate the processing
    relevant_files = find_relevant_files(str(output_dir), exclude_patterns)
    assert len(relevant_files) == 7, (
        f"Expected 7 relevant files, but found {len(relevant_files)}"
    )  # 7 files for the docs directory

    # Check MD files
    number_md_file = 0
    for file in relevant_files:
        if file.endswith(".md"):
            number_md_file += 1
    assert number_md_file == 5, (
        f"Expected 5 relevant md files, but found {number_md_file}"
    )

    extracted_data: List[Dict] = []
    for file_path in relevant_files:
        logger.info(f"Processing file: {file_path}")
        repo_link = f"{test_repo}/blob/main/{Path(file_path).name}"

        if file_path.endswith((".md", ".mdx")):
            extracted_data.extend(extract_from_markdown(file_path, repo_link))
        elif file_path.endswith(".ipynb"):
            extracted_data.extend(extract_from_ipynb(file_path, repo_link))
        elif file_path.endswith(".rst"):
            extracted_data.extend(extract_from_rst(file_path, repo_link))
        else:
            logger.warning(f"Unsupported file type: {file_path}. Skipping.")
            continue

    assert len(extracted_data) > 0, "No data extracted from the repository"

    # Check for embedding keys for at least one item.
    if extracted_data:
        first_item = extracted_data[0]
        assert "embedding_code" in first_item, (
            "Missing 'embedding_code' key in extracted data"
        )
        assert "embedding_description" in first_item, (
            "Missing 'embedding_description' key in extracted data"
        )

    # Database Validation
    client = await asyncio.to_thread(arango_setup.connect_arango)
    if not client:
        logger.error("Failed to connect to ArangoDB for validation.")
        raise AssertionError("Failed to connect to ArangoDB for validation.")

    db = client.db(ARANGO_DB_NAME, username=ARANGO_USER, password=ARANGO_PASSWORD)
    collection = db.collection(COLLECTION_NAME)

    # Get counts
    count = collection.count()
    assert count > 0, f"The number of extracted code is '{count}' in the database"
    logger.info(
        f"Number of items in ArangoDB '' collection after processing is {count}"
    )
    logger.info(
        "All assertions passed. Data extraction and database load test appear successful"
    )
