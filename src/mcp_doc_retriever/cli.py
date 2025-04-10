"""
Module: cli.py

Description:
Command-line interface for MCP Document Retriever.
Provides CLI commands for:
- Recursive downloading
- Searching downloaded content (future)
- Other utilities (future)

Third-party packages:
- argparse: https://docs.python.org/3/library/argparse.html
- asyncio: https://docs.python.org/3/library/asyncio.html

Sample input:
python -m src.mcp_doc_retriever.cli --url https://docs.python.org/3/ --depth 1 --output-dir ./downloads_test --download-id cli_test_run --force

Expected output:
- Downloads content recursively from the URL.
- Saves files and index.
- Prints progress and summary.

"""

import argparse
import asyncio
import os
import sys
import time
import logging

from mcp_doc_retriever.downloader import start_recursive_download

logger = logging.getLogger(__name__)

def parse_args():
    parser = argparse.ArgumentParser(
        description="MCP Document Retriever CLI",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--url", type=str, required=True, help="Starting URL to download."
    )
    parser.add_argument(
        "--depth",
        type=int,
        default=1,
        help="Recursion depth (0 = only start URL, 1 = start URL + links on it, etc.).",
    )
    parser.add_argument(
        "--force", action="store_true", help="Force overwrite existing files."
    )
    parser.add_argument(
        "--output-dir",
        type=str,
        default="./downloads",
        help="Base output directory for 'content' and 'index' subdirectories.",
    )
    parser.add_argument(
        "--download-id",
        type=str,
        default=f"cli_download_{int(time.time())}",
        help="Identifier for this download batch (used for index filename).",
    )
    parser.add_argument(
        "--use-playwright",
        action="store_true",
        help="Use Playwright fetcher instead of httpx (slower, needs browser install).",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=30,
        help="Timeout in seconds for network requests.",
    )
    parser.add_argument(
        "--max-size",
        type=int,
        default=None,
        help="Maximum file size in bytes for downloads (e.g., 10485760 for 10MB).",
    )
    parser.add_argument(
        "--log-level",
        choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
        default="INFO",
        help="Set the logging level.",
    )
    return parser.parse_args()

async def run_downloader(args):
    abs_output_dir = os.path.abspath(args.output_dir)
    os.makedirs(abs_output_dir, exist_ok=True)

    await start_recursive_download(
        start_url=args.url,
        depth=args.depth,
        force=args.force,
        download_id=args.download_id,
        base_dir=abs_output_dir,
        use_playwright=args.use_playwright,
        timeout_requests=args.timeout,
        timeout_playwright=args.timeout,
        max_file_size=args.max_size,
    )

def main():
    args = parse_args()

    log_level = getattr(logging, args.log_level.upper(), logging.INFO)
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s - %(levelname)s - [%(name)s:%(funcName)s] - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("playwright").setLevel(logging.WARNING)

    print("\n--- MCP Document Retriever CLI ---")
    print(f"Starting download with parameters:")
    print(f"  Start URL:      {args.url}")
    print(f"  Depth:          {args.depth}")
    print(f"  Force Overwrite:{args.force}")
    print(f"  Output Dir:     {os.path.abspath(args.output_dir)}")
    print(f"  Download ID:    {args.download_id}")
    print(f"  Fetcher:        {'Playwright' if args.use_playwright else 'httpx'}")
    print(f"  Timeout:        {args.timeout}s")
    print(f"  Max File Size:  {args.max_size or 'Unlimited'} bytes")
    print(f"  Log Level:      {args.log_level}")
    print("-" * 30)

    try:
        asyncio.run(run_downloader(args))
        print("-" * 30)
        print("Download process completed.")
        print(
            f"Check index file: {os.path.join(os.path.abspath(args.output_dir), 'index', args.download_id + '.jsonl')}"
        )
        print(f"Check content in: {os.path.join(os.path.abspath(args.output_dir), 'content')}")
        print("--- Done ---")

    except KeyboardInterrupt:
        print("\nDownload interrupted by user.", file=sys.stderr)
        logger.warning("Download process interrupted by KeyboardInterrupt.")
        sys.exit(1)
    except Exception as e:
        print(f"\nAn unexpected error occurred during the download: {e}", file=sys.stderr)
        logger.critical("Unhandled exception in CLI download execution.", exc_info=True)
        sys.exit(1)

# Minimal usage function
def usage_example():
    """
    Minimal example of invoking the CLI programmatically.
    """
    import sys
    sys.argv = [
        "cli.py",
        "--url", "https://docs.python.org/3/",
        "--depth", "0",
        "--output-dir", "./downloads_test",
        "--download-id", "cli_test_run",
        "--force"
    ]
    main()

if __name__ == "__main__":
    main()
