import os
import sys
from typing import Dict, Any, Optional, List

from arango import ArangoClient
from arango.database import StandardDatabase
from arango.exceptions import DocumentInsertError
from loguru import logger

# --- Import Embedding Utilities ---
from mcp_doc_retriever.arangodb.embedding_utils import get_embedding

# --- Setup/Config from Environment ---
HOST = os.environ.get("ARANGO_HOST", "http://localhost:8529")
DATABASE_NAME = os.environ.get("ARANGO_DATABASE", "doc_retriever")
COLLECTION_NAME = os.environ.get("ARANGO_VERTEX_COLLECTION", "lessons_learned")
USERNAME = os.environ.get("ARANGO_USERNAME", "root")
PASSWORD = os.environ.get("ARANGO_PASSWORD", "openSesame")
EMBEDDING_FIELD = os.environ.get("ARANGO_EMBEDDING_FIELD", "embedding")  # NEW


# --- Basic Structure for the Document ---
def create_lesson_document(
    role: str,
    problem: str,
    solution: str,
    relevant_for: List[str],
    tags: List[str],
    example: str,
    date: str,
    embedding: List[float],  # NEW
) -> Dict[str, Any]:
    """Creates a lesson document with the given fields."""
    return {
        "role": role,
        "problem": problem,
        "solution": solution,
        "relevant_for": relevant_for,
        "tags": tags,
        "example": example,
        "date": date,
        EMBEDDING_FIELD: embedding,  # NEW
    }


# --- Main Execution ---
if __name__ == "__main__":
    # --- Logging Setup ---
    logger.remove()  # Remove default handler
    logger.add(
        sys.stderr,
        level="DEBUG",  # Set desired logging level
        format="{time:HH:mm:ss} | {level: <7} | {message}",
        colorize=True,
    )

    logger.info("--- Running Structured ArangoDB Lesson Insertion ---")

    # --- Connect to ArangoDB ---
    try:
        client = ArangoClient(hosts=HOST)
        db: StandardDatabase = client.db(
            DATABASE_NAME, username=USERNAME, password=PASSWORD
        )  # Type hint for clarity
        logger.info(f"Connected to ArangoDB database '{DATABASE_NAME}'")
    except Exception as e:
        logger.error(f"Connection to ArangoDB failed: {e}")
        sys.exit(1)

    # --- Define the Lessons (as JSON objects) ---
    lessons = [
        {
            "role": "Database Engineer",
            "problem": "Vector indexes in ArangoDB require specific parameters for proper creation and function, and a failure to fully understand each parameter can result in non-optimal configurations, resulting in ERR 10, or failed document insertions.",
            "solution": "1. Use type: 'vector' to designate the index as a vector index.\n2. Use name: '<index_name>' to assign a name to the index for reference.\n3. Use fields: ['<embedding_field>'] to specify an array that the index is to apply to.\n4. Use params: {dimension: <embedding_dimension>, metric: '<distance_metric>', nLists: <number_of_lists>}\nUse dimension to specify the vector dimension.\nUse metric: '<distance_metric>' to  specify the distance metric (e.g., 'cosine', 'euclidean').\nUse nLists: <number_of_lists> to  set the number of lists for inverted index (IVF) optimization (Optional, only for certain index types).\n",
            "relevant_for": ["arangodb", "vector_search", "embeddings", "python"],
            "tags": ["vector_index", "arangodb", "embedding", "python-arango", "parameters"],
            "example": "``````",
            "date": "2025-04-17",
            "embedding": []
            },
        {
            "role": "Assistant",
            "problem": "Overlooked the need for a full-dimensional vector during initial inserts.",
            "solution": "1. Generate a vector of the correct dimension using `generate_random_embedding`.\n2. Use this vector for initial document inserts where needed.",
            "relevant_for": ["arangodb", "vector_embedding", "python-arango"],
            "tags": ["vector_embedding", "dimension", "python", "arangodb"],
            "example": "Using `generate_random_embedding(EMBEDDING_DIMENSION)` to create a vector of the correct size.",
            "date": "2025-04-17",
        },
    ]
    # --- Insert the Documents ---
    try:
        collection = db.collection(COLLECTION_NAME)
        for lesson_data in lessons:
            # --- Create embedding
            lesson_text = f"{lesson_data['role']} {lesson_data['problem']} {lesson_data['solution']} {lesson_data['example']}"  # Combine text fields
            embedding = get_embedding(lesson_text)  # Get embedding
            lesson_document = create_lesson_document(
                role=lesson_data["role"],
                problem=lesson_data["problem"],
                solution=lesson_data["solution"],
                relevant_for=lesson_data["relevant_for"],
                tags=lesson_data["tags"],
                example=lesson_data["example"],
                date=lesson_data["date"],
                embedding=embedding,  
            )
            meta = collection.insert(lesson_document, sync=True)
            logger.success(f"Lesson inserted successfully. Meta: {meta}")

    except DocumentInsertError as e:
        logger.error(f"Failed to insert lesson document: {e}")
        sys.exit(1)
    except Exception as e:
        logger.exception(f"An unexpected error occurred: {e}")
        sys.exit(1)

    logger.info("--- Structured ArangoDB Lesson Insertion Completed ---")
