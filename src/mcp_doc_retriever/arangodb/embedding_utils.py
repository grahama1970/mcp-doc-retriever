# embedding_utils.py
import sys
from typing import List, Dict, Any, Optional

import litellm
from loguru import logger

# Import config variables needed
from .config import EMBEDDING_MODEL, EMBEDDING_DIMENSIONS


@logger.catch(onerror=lambda _: sys.exit("Exiting due to critical embedding error."))
def get_embedding(text: str, model: str = EMBEDDING_MODEL) -> Optional[List[float]]:
    """
    Generates an embedding for the given text using LiteLLM.

    Args:
        text (str): The input text to embed.
        model (str): The embedding model identifier (e.g., "text-embedding-3-small").

    Returns:
        Optional[List[float]]: The embedding vector, or None if an error occurs.
    """
    if not text or not text.strip():
        logger.warning("Cannot generate embedding for empty or whitespace-only text.")
        return None
    try:
        # LiteLLM abstracts the call to the specific provider based on the model name.
        # Ensure the corresponding API key (e.g., OPENAI_API_KEY) is set in the environment.
        logger.debug(f"Requesting embedding: model='{model}', text='{text[:100]}...'")
        response = litellm.embedding(model=model, input=text)
        # Access the embedding vector from the response structure
        embedding = response.data[0].embedding

        # Optional: Validate embedding dimension if needed for consistency
        if EMBEDDING_DIMENSIONS > 0 and len(embedding) != EMBEDDING_DIMENSIONS:
            logger.error(
                f"Embedding dimension mismatch! Expected {EMBEDDING_DIMENSIONS}, got {len(embedding)}. Check model '{model}'."
            )
            # Decide if this is critical enough to return None or just warn
            # return None # Make it critical

        logger.debug(f"Generated embedding ({len(embedding)} dims).")
        return embedding
    except Exception as e:
        # Log the full exception details using Loguru's exception handling
        logger.exception(f"LiteLLM embedding error: model='{model}', error='{e}'")
        # Return None to indicate failure to the calling function
        return None


def get_text_for_embedding(doc_data: Dict[str, Any]) -> str:
    """
    Combines relevant text fields from a document into a single string
    suitable for generating a representative embedding.

    Args:
        doc_data (Dict[str, Any]): The document data dictionary.

    Returns:
        str: A concatenated string of relevant text fields.
    """
    # Select fields that best represent the document's core meaning.
    # Using newline separators can sometimes help models distinguish fields.
    parts = [
        doc_data.get("problem", ""),
        doc_data.get("solution", ""),
        doc_data.get("context", ""),
        # Consider whether to include tags or examples based on desired semantics
        # " ".join(doc_data.get("tags", [])),
        # doc_data.get("example", ""),
    ]
    # Filter out empty strings and join non-empty parts with newlines.
    return "\n".join(filter(None, parts)).strip()
