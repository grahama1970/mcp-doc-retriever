import json
import os
from pathlib import Path
import re
from typing import Union

from json_repair import repair_json
from loguru import logger


class PathEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, Path):
            return str(obj)
        return super().default(obj)


def json_serialize(data, handle_paths=False, **kwargs):
    """Serialize data to JSON, optionally handling Path objects.

    Args:
        data: The data to serialize.
        handle_paths (bool): Whether to handle Path objects explicitly.
        **kwargs: Additional arguments for json.dumps().

    Returns:
        str: JSON-serialized string.
    """
    if handle_paths:
        return json.dumps(data, cls=PathEncoder, **kwargs)
    return json.dumps(data, **kwargs)


def load_json_file(file_path):
    """Load JSON data from a file.

    Args:
        file_path (str): Path to the JSON file.

    Returns:
        The loaded JSON data, or None if file does not exist.
    """
    if not os.path.exists(file_path):
        logger.warning(f"File does not exist: {file_path}")
        return None

    try:
        with open(file_path, "r") as file:
            data = json.load(file)
        logger.info("JSON file loaded successfully")
        return data
    except json.JSONDecodeError as e:
        logger.warning(f"JSON decoding error: {e}, trying utf-8-sig encoding")
        try:
            with open(file_path, "r", encoding="utf-8-sig") as file:
                data = json.load(file)
            logger.info("JSON file loaded successfully with utf-8-sig encoding")
            return data
        except json.JSONDecodeError:
            logger.error("JSON decoding error persists with utf-8-sig encoding")
            raise
    except IOError as e:
        logger.error(f"I/O error: {e}")
        raise


def save_json_to_file(data, file_path):
    """Save data to a JSON file.

    Args:
        data: The data to save.
        file_path (Union[str, Path]): A string or Path object representing the file path.
    """
    # Convert file_path to a Path object if it isn't one already
    if not isinstance(file_path, Path):
        file_path = Path(file_path)

    directory = file_path.parent

    try:
        if directory:
            directory.mkdir(parents=True, exist_ok=True)
            logger.info(f"Created directory: {directory}")
    except OSError as e:
        logger.error(f"Failed to create directory {directory}: {e}")
        raise

    try:
        with file_path.open("w") as f:
            json.dump(data, f, indent=4)
            logger.info(f"Saved JSON to: {file_path}")
    except Exception as e:
        logger.error(f"Failed to save to {file_path}: {e}")
        raise


def parse_json(content: str) -> Union[dict, list, str]:
    """Parse a JSON string, attempting repairs if needed.

    Args:
        content (str): JSON string to parse.

    Returns:
        Union[dict, list, str]: Parsed JSON data or original string if parsing fails.
    """
    try:
        parsed_content = json.loads(content)
        logger.debug("Successfully parsed JSON")
        return parsed_content
    except json.JSONDecodeError as e:
        logger.warning(f"Direct JSON parsing failed: {e}")

    try:
        # Try to extract JSON from mixed content
        json_match = re.search(r"(\[.*\]|\{.*\})", content, re.DOTALL)
        if json_match:
            content = json_match.group(1)

        # Attempt repair
        repaired_json = repair_json(content, return_objects=True)
        if isinstance(repaired_json, (dict, list)):
            logger.info("Successfully repaired JSON")
            return repaired_json

        parsed_content = json.loads(repaired_json)
        logger.debug("Successfully parsed repaired JSON")
        return parsed_content

    except json.JSONDecodeError as e:
        logger.error(f"JSON repair failed: {e}")
    except Exception as e:
        logger.error(f"JSON parsing failed: {e}")

    logger.debug("Returning original content")
    return content


def clean_json_string(
    content: Union[str, dict, list], return_dict: bool = False
) -> Union[str, dict, list]:
    """Clean and parse JSON content.

    Args:
        content: JSON string, dict, or list to clean.
        return_dict: If True, return Python dict/list; if False, return JSON string.

    Returns:
        Cleaned JSON as string, dict, or list based on return_dict parameter.
    """
    # Handle dict/list input
    if isinstance(content, (dict, list)):
        return content if return_dict else json.dumps(content)

    # Handle string input
    if isinstance(content, str):
        if not return_dict:
            return content

        parsed_content = parse_json(content)
        if return_dict and isinstance(parsed_content, str):
            try:
                return json.loads(parsed_content)
            except Exception as e:
                logger.error(f"Failed to convert to dict/list: {e}")
                return parsed_content
        return parsed_content

    logger.info("Returning original content")
    return content


def usage_example():
    """Example usage of the clean_json_string function."""
    examples = {
        "valid_json": '{"name": "John", "age": 30, "city": "New York"}',
        "invalid_json": '{"name": "John", "age": 30, "city": "New York" some invalid text}',
        "dict": {"name": "John", "age": 30, "city": "New York"},
        "list_of_dicts": """[
            {
                "type": "function",
                "function": {
                    "name": "get_current_weather",
                    "description": "Get current weather in a location",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "location": {"type": "string"},
                            "unit": {"type": "string", "default": "celsius"}
                        },
                        "required": ["location"]
                    }
                }
            }
        ]""",
        "mixed_content": 'Text {"name": "John"} more text',
        "nested_json": '{"person": {"name": "John", "details": {"age": 30}}}',
        "partial_json": '{"name": "John", "age": 30, "city":',
    }

    for name, example in examples.items():
        print(f"\n{name}:")
        print(clean_json_string(example, return_dict=True))

if __name__ == "__main__":
    # Run the example usage
    usage_example()
