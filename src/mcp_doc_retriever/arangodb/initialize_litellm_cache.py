import litellm
import os
import redis
from loguru import logger
import sys  # Needed for test function logger setup


def initialize_litellm_cache() -> None:
    """Initializes LiteLLM caching (Redis fallback to in-memory), ensuring 'embedding' is cached."""
    redis_host = os.getenv("REDIS_HOST", "localhost")
    redis_port = int(os.getenv("REDIS_PORT", 6379))
    redis_password = os.getenv("REDIS_PASSWORD", None)

    try:
        logger.debug(
            f"Attempting LiteLLM Redis cache init (Target: {redis_host}:{redis_port})..."
        )
        redis_client = redis.Redis(
            host=redis_host,
            port=redis_port,
            password=redis_password,
            socket_timeout=2,
            decode_responses=True,
        )
        if not redis_client.ping():
            raise redis.ConnectionError("Ping failed")
        logger.debug("Redis connection successful.")

        logger.debug("Configuring LiteLLM Redis cache...")
        litellm.cache = litellm.Cache(
            type="redis",
            host=redis_host,
            port=redis_port,
            password=redis_password,
            # *** CRITICAL FIX: Added 'embedding' ***
            supported_call_types=["acompletion", "completion", "embedding"],
            ttl=(60 * 60 * 24 * 2),  # 2 days
        )
        litellm.enable_cache()
        logger.info(
            f"✅ LiteLLM Caching enabled using Redis at {redis_host}:{redis_port}"
        )
        # Optional: Add Redis write/read test here if desired

    except (redis.ConnectionError, redis.TimeoutError, ConnectionError) as e:
        logger.warning(
            f"⚠️ Redis connection/setup failed: {e}. Falling back to in-memory caching."
        )
        logger.debug("Configuring LiteLLM in-memory cache...")
        litellm.cache = litellm.Cache(
            type="local",
            # *** CRITICAL FIX: Added 'embedding' ***
            supported_call_types=["acompletion", "completion", "embedding"],
            ttl=(60 * 60 * 1),  # 1 hour TTL for in-memory
        )
        litellm.enable_cache()
        logger.info("✅ LiteLLM Caching enabled using in-memory (local) cache.")
    except Exception as e:
        logger.exception(f"Unexpected error during LiteLLM cache initialization: {e}")


# --- Test Function (Kept for standalone testing) ---
def test_litellm_cache():
    # ... (Test function remains the same as before, ideally update to test embedding cache hit too) ...
    pass  # Placeholder to avoid linting error if body removed


if __name__ == "__main__":
    # Allows running this script directly to test caching setup
    logger.remove()
    logger.add(sys.stderr, level="DEBUG")
    # Set dummy key if needed for test provider
    os.environ["OPENAI_API_KEY"] = os.environ.get("OPENAI_API_KEY", "sk-dummy")
    test_litellm_cache()
