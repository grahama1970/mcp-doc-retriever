import time
import requests
import sys
import logging

logging.basicConfig(level=logging.INFO, format='%(asctime)s | %(levelname)s | %(message)s')

start_time = time.time()
timeout = 30
url = "http://localhost:8005/health"

logging.info("Starting server readiness check...")

while True:
    current_time = time.time()
    elapsed_time = current_time - start_time

    if elapsed_time >= timeout:
        logging.error(f"Server readiness check failed: Timeout after {timeout} seconds.")
        sys.exit(1)

    logging.info(f"Polling {url} (Elapsed: {elapsed_time:.1f}s)...")
    try:
        response = requests.get(url, timeout=1)
        if response.status_code == 200:
            logging.info("Server is ready!")
            sys.exit(0)
        else:
            logging.warning(f"Received status code {response.status_code}, retrying...")
    except requests.exceptions.ConnectionError:
        logging.warning("Connection error, server might not be up yet. Retrying...")
    except requests.exceptions.Timeout:
        logging.warning("Request timed out, retrying...")
    except Exception as e:
        logging.error(f"An unexpected error occurred: {e}")
        # Optionally exit on unexpected errors, or just log and retry
        # sys.exit(1)

    time.sleep(2)