import sys
import os

# Adjust path to ensure src is importable
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from src.mcp_doc_retriever import config
from src.mcp_doc_retriever.searcher import perform_search

def main():
    # Override base dir to point to test_data
    config.DOWNLOAD_BASE_DIR = 'test_data'

    # Call perform_search with provided parameters
    results = perform_search("test_dl", ["example", "test"], "p", None)

    # Print results
    print("Search Results:")
    for item in results:
        print(f"URL: {item.original_url}")
        print(f"Content: {item.extracted_content}")
        print(f"Selector: {item.selector_matched}")
        print("-" * 40)

if __name__ == "__main__":
    main()