from arango import ArangoClient
from loguru import logger
import os
from arango.collection import StandardCollection
import sys
# Environment variables for configuration
HOST = os.environ.get("ARANGO_HOST", "http://localhost:8529")
DATABASE_NAME = os.environ.get("ARANGO_DATABASE", "doc_retriever")
COLLECTION_NAME = os.environ.get("ARANGO_VERTEX_COLLECTION", "lessons_learned")
USERNAME = os.environ.get("ARANGO_USERNAME", "root")
PASSWORD = os.environ.get("ARANGO_PASSWORD", "openSesame")
DIMENSION = int(os.environ.get("ARANGO_EMBEDDING_DIMENSION", "1536"))
INDEX_NAME = os.environ.get("ARANGO_VECTOR_INDEX_NAME", "idx_lesson_embedding")
EMBEDDING_FIELD = os.environ.get("ARANGO_EMBEDDING_FIELD", "embedding")


def check_vector_index(
    host: str,
    database_name: str,
    collection_name: str,
    username: str,
    password: str,
    dimension: int,
    index_name: str,
    embedding_field: str,
) -> None:
    """
    Checks if a vector index exists on a collection and has the correct properties.
    If the index exists but has incorrect properties, it will be dropped and recreated.
    If the index does not exist, it will be created.
    """
    try:
        # Initialize the ArangoDB client
        client = ArangoClient(hosts=host)

        # Connect to the database
        db = client.db(database_name, username=username, password=password)

        # Get the collection object
        collection = db.collection(collection_name)

        # Get all indexes for the collection
        indexes = collection.indexes()

        # Check if the vector index exists
        vector_index = None
        for index in indexes:
            if index["name"] == index_name and index["type"] == "vector":
                vector_index = index
                break

        # If the vector index exists, check its properties
        if vector_index:
            logger.info(f"Vector index '{index_name}' found.")

            # Check if the index has the correct fields and dimension
            fields = vector_index.get("fields")
            params = vector_index.get("params")

            if fields != [embedding_field] or not (
                params and params.get("dimension") == dimension
            ):
                logger.warning(
                    f"Vector index '{index_name}' has incorrect properties. Dropping and recreating it."
                )
                collection.delete_index(vector_index["id"])
                create_vector_index(collection, index_name, embedding_field, dimension)
            else:
                logger.info(f"Vector index '{index_name}' has correct properties.")

        # If the vector index does not exist, create it
        else:
            logger.info(f"Vector index '{index_name}' not found. Creating it.")
            create_vector_index(collection, index_name, embedding_field, dimension)

    except Exception as e:
        logger.error(f"An error occurred: {e}")


def create_vector_index(
    collection: StandardCollection,
    index_name: str,
    embedding_field: str,
    dimension: int,
) -> None:
    """Creates a vector index on the specified collection."""
    try:
        collection.add_index(
            {
                "type": "vector",
                "name": index_name,
                "fields": [embedding_field],
                "params": {
                    "metric": "cosine",
                    "dimension": dimension,
                    "nLists": 2,
                },
            }
        )
        logger.info(
            f"Vector index '{index_name}' created with dimension {dimension} and cosine metric."
        )
    except Exception as e:
        logger.error(f"Error creating vector index: {e}")


if __name__ == "__main__":
    # Basic logging setup
    logger.remove()
    logger.add(
        sys.stderr,
        level="DEBUG",  # Use DEBUG for more detail
        format="{time:HH:mm:ss} | {level: <7} | {message}",
        colorize=True,
    )

    check_vector_index(
        HOST,
        DATABASE_NAME,
        COLLECTION_NAME,
        USERNAME,
        PASSWORD,
        DIMENSION,
        INDEX_NAME,
        EMBEDDING_FIELD,
    )
