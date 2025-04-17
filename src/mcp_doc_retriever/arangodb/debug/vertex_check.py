from arango import ArangoClient
import os
import random
from typing import List

# Use environment variables for configuration
HOST = os.environ.get("ARANGO_HOST", "http://localhost:8529")
DATABASE_NAME = os.environ.get("ARANGO_DATABASE", "doc_retriever")
COLLECTION_NAME = os.environ.get("ARANGO_VERTEX_COLLECTION", "lessons_learned")
USERNAME = os.environ.get("ARANGO_USERNAME", "root")
PASSWORD = os.environ.get("ARANGO_PASSWORD", "openSesame")
EMBEDDING_FIELD = os.environ.get("ARANGO_EMBEDDING_FIELD", "embedding")  # ADDED
DIMENSION = int(os.environ.get("ARANGO_EMBEDDING_DIMENSION", "1536"))


def generate_random_embedding(dimension: int) -> List[float]:
    """Generates a list of random floats."""
    return [random.random() for _ in range(dimension)]


# Initialize the client
client = ArangoClient(hosts=HOST)

# Connect to the database
try:
    db = client.db(DATABASE_NAME, username=USERNAME, password=PASSWORD)
    print("Connected to database.")
except Exception as e:
    print(f"Connection error: {e}")
    exit(1)

# Get the collection object
try:
    collection = db.collection(COLLECTION_NAME)
    print(f"Collection '{COLLECTION_NAME}' found.")
except Exception as e:
    print(f"Collection error: {e}")
    exit(1)

# Insert a document
embedding = generate_random_embedding(DIMENSION)
doc = {"_key": "test_doc_1", EMBEDDING_FIELD: embedding}  # ADDED embedding field
try:
    meta = collection.insert(doc, sync=True)
    print(f"Document inserted successfully. Meta: {meta}")
except Exception as e:
    print(f"Insert error: {e}")
