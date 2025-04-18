Phase 3 (File Type Handling and Extraction):

Binary File Exclusion Criteria: Besides the 50KB size limit for text files, how should binary files be identified? Should we rely on file extensions (e.g., .jpg, .png, .exe) or use a library like filetype to inspect the file content? What is the preferred method?
> ONLY text files (md, mdx, rst, text) and ipynb should be downloaded 
No images or binary or exe files should be downloaded

Phase 4 (Code Block and Description Extraction):


Checkbox Section Titles: Could you provide an example of the "section title marked by checkbox" format used to identify descriptions? This will help ensure accurate description extraction. For example is it - [ ] Section Title or - [X] Section Title?
> yes. this is crucial so you will know where you are if the system crashes.

Phase 5 (Embedding Generation):

Tenacity Decorator Placement: To clarify, the tenacity decorator should be applied to the single function called within acompletion for LiteLLM embedding. Should that single function wrap a single call like litellm.embedding or are we allowed to add logic to the function, for example handling a rate limit case
> the below is an example of how to use litellm
### `git_downloader_test/arangodb_schema_generator/shared/litellm_call.py`

```python
from types import SimpleNamespace
import litellm
import async_timeout
from deepmerge import always_merger
from pydantic import BaseModel, Field
import asyncio
import os
import copy
from typing import Any, AsyncGenerator, Dict, Type, Union
from pydantic import BaseModel
from loguru import logger
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

from src.generate_schema_for_llm.shared.multimodal_utils import format_multimodal_messages, is_multimodal
from src.utils.get_project_root import get_project_root, load_env_file
from src.utils.json_utils import clean_json_string

# Redis Caching of LiteLLM Call
async def initialize_litellm_cache():
    litellm.cache = litellm.Cache(
        type="redis",
        host="localhost",  # Redis is accessible on localhost
        port=6379,         # Default Redis port
        password=None      # No password unless you configured one
    )
    litellm.enable_cache()
    os.environ['LITELLM_LOG'] = 'DEBUG'


# Load environment variables Globally
project_dir = get_project_root()
load_env_file(env_type="backend")




# Helper function to validate and update LLM config
def validate_update_config(config: Dict[str, Any], insert_into_db: bool = False) -> Dict[str, Any]:
    """
    Validates and updates LLM config to meet requirements for JSON/structured responses.
    
    Args:
        llm_config: The LLM configuration dictionary
        
    Returns:
        Dict[str, Any]: Updated LLM config with proper JSON formatting instructions
        
    Raises:
        ValueError: If JSON requirements cannot be met
    """
    llm_config = config.get("llm_config", {})
    directories = config.get("directories", {})
    if not llm_config.get("messages", []):
        raise ValueError("A message object is required to query the LLM")
        
    
    response_format = llm_config.get("response_format")
    requires_json = response_format == "json" or (
        isinstance(response_format, type) and 
        issubclass(response_format, BaseModel)
    )
    
    if requires_json:
        messages = llm_config.get("messages", []).copy()
        system_messages = [msg for msg in messages if msg.get("role") == "system"]
        
        if not system_messages:
            # Add a new system message if none exists
            messages.insert(0, {
                "role": "system",
                "content": "You must return your response as a well-formatted JSON object."
            })
        else:
            # Update existing system message if JSON instruction is missing
            system_content = system_messages[0].get("content", "").lower()
            json_instruction_keywords = ["json", "well-formatted", "well formatted"]
            
            if not any(keyword in system_content for keyword in json_instruction_keywords):
                system_messages[0]["content"] = (
                    "You must return your response as a well-formatted JSON object. "
                    + system_messages[0]["content"]
                )

        # Check for multimodal content
        if is_multimodal(messages):
            # hardcoded for now
            image_directory = directories.get("image_directory", "")
            max_size_kb = llm_config.get("max_image_size_kb", 500)
            messages = format_multimodal_messages(messages, image_directory, max_size_kb)
        
        llm_config = copy.deepcopy(llm_config)
        llm_config["messages"] = messages
        
    return llm_config


# Main
@retry(
    wait=wait_exponential(multiplier=1, min=4, max=10),  # Exponential backoff
    stop=stop_after_attempt(3),  # Max 3 retries
    retry=retry_if_exception_type(Exception),  # Retry on any exception
)
async def litellm_call(config: Dict) -> Union[BaseModel, Dict[str, Any], str]:
    try:
        llm_config = config.get("llm_config", {})
        directories = config.get("directories", {})

        llm_config = validate_update_config(config)
        
        # Default to plain text if response_format is not provided
        response_format = llm_config.get("response_format", None)
        
        api_params = {
            "model": llm_config.get("model", "openai/gpt-4o-mini"),
            "messages": llm_config.get("messages", []),
            "temperature": llm_config.get("temperature", .2),
            "max_tokens": llm_config.get("max_tokens", 1000),
            "stream": llm_config.get("stream", False),
            "caching": llm_config.get("caching", True),
            "metadata": {"request_id": llm_config.get("request_id"), "hidden": True},
        }
        if llm_config.get("api_base", None):
            
            # Remove /v1/completions from api_base if present
            # Strange LiteLLM behavior, but it works
            api_base = llm_config["api_base"]
            if api_base.endswith("/v1/completions"):
                api_base = api_base.replace("/v1/completions", "/v1")
                api_params["api_base"] = api_base

        # Add response_format only if explicitly provided
        if response_format:
            api_params["response_format"] = response_format

        response = await litellm.acompletion(**api_params)

        # Handle streaming response
        if llm_config.get("stream", False):
            full_content = ""
            async for chunk in response:
                content = chunk.choices[0].delta.content or ""
                full_content += content
            content = full_content

            # Construct a mock response object similar to non-streaming responses
            # Not sure if I need to use this...we'll see
            reconstructed_response = SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(
                            content=full_content,
                            role="assistant"
                        )
                    )
                ],
                _hidden_params={
                    "cache_hit": False,
                    "request_id": llm_config.get("request_id"),
                    "hidden": True
                }
            )
            return reconstructed_response
        else:
            # Handle non-streaming response
            hidden_params = response._hidden_params
            logger.info(f"Cache Hit: {hidden_params.get('cache_hit', False)}")
            content = response.choices[0].message.content
        
        # should we insert the message object into the db with an embed here
        
        
        return response
    

    except litellm.BadRequestError as e:
        logger.error(f"BadRequestError: {e}")
        raise
    except Exception as e:
        logger.error(f"Error calling LLM: {e}")
        raise


# Usage
async def main():
    litellm.set_verbose=True
    await initialize_litellm_cache()
    from src.settings.config import config
    
    # Define a Pydantic model for the response
    class UserDetails(BaseModel):
        """A model representing user details including name, age and favorite color"""
        name: str = Field(description="The user's full name")
        # age: int = Field(ge=1, le=120, description="The user's age in years")
        age: int = Field(description="The user's age in years")
        favorite_color: str = Field(description="The user's preferred color")
    
    updates = {
        "llm_config": {
            # "api_base": "https://api.openai.com/v1",
            # "model": "openai/gpt-4o-mini",
            "api_base": "http://192.168.86.49:30002/v1/completions",
            "model": "openai/neuralmagic/Meta-Llama-3.1-8B-Instruct-FP8",
            "messages": [
                {"role": "system", "content": "You are a helpful assistant. You must return your response as a well-formatted JSON object. with no other text."},
                {"role": "user", "content": "Tell me about John Doe, age 30 and his favorite color is blue, in JSON format."}
            ],
            "response_format": UserDetails, # {"type": "json_object"}, # UserDetails,
            "stream": True,
            "max_image_size_kb": 500, # for multimodal calls
            "caching": True,
        }
    }
    config = always_merger.merge(config, updates)


    response = await litellm_call(config)

    content = response.choices[0].message.content
    hidden_params = response._hidden_params
    print(content)
    print(hidden_params)

    

if __name__ == "__main__":
    asyncio.run(main())
    
```



Phase 6 (Database Storage):

Pydantic Model Definition: Should I create Pydantic models for all data stored in ArangoDB, including settings, versions, and individual code snippets, or only for the core code/description/embedding data? Defining models for all data provides stricter validation but adds complexity.
> Only create the Pyndatic classes that you absolutely need. Do not overcomplicate with too many classes as you have trouble updating them when code changes and is debuged. All pyndatic classes need to be inline with code file...not help externally. Duplication across files is fine

Phase 8 (Pipeline Orchestration):

Orchestration Script Modularity: To ensure "very easy to follow" code, how strictly should I adhere to separate functions for each step? Should I aim for a single function per major phase (sparse checkout, file discovery, etc.) or allow smaller sub-steps within each function?
> basic steps is fine. Each step can contain multiple function. Example below
1. sparse download
2. extract
    a. markdown
    b. rst
    c. ....

3. Convert to Json objects and embedd
4. Insert into arango DB

Phase 9 (CLI Implementation):

CLI Parameter Grouping: Should I group CLI parameters into logical categories (e.g., "Repository Settings," "Embedding Settings," "Database Settings") using Typer's typer.Option grouping features? This can improve usability but adds complexity.
Phase 10 (FastAPI):

FastAPI MVP Scope: Besides mirroring the CLI commands and providing a health endpoint, are there any other "must-have" features for the FastAPI MVP? For example, should it include basic error reporting or monitoring capabilities?
> Fastapi must be a mirror of the cli. Each cli typer command MUST have a FastAPI equivalent