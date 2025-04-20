import requests
from urllib import robotparser, parse # Corrected import
from playwright.sync_api import sync_playwright
from loguru import logger
from pathlib import Path
from typing import Optional
import argparse
import sys
import os

# Ensure the logger is configured (can be moved to a central logging setup)
log_file_path = Path(__file__).parent / "scraper.log"
logger.add(
    log_file_path,
    rotation="10 MB",
    retention="7 days",
    format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {message}"
)

def is_js_rendered(url: str) -> bool:
    """
    Uses requests to get the page and check if its javascript rendered.
    NOTE: This is a basic heuristic and might not be accurate for all sites.
    """
    try:
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'}
        response = requests.get(url, timeout=5, headers=headers)
        response.raise_for_status()
        html_content = response.text.lower()
        if "<body" in html_content and "</html>" in html_content:
            if any(framework in html_content for framework in ["react", "vue", "angular", "svelte"]):
                 logger.debug(f"JS framework detected in {url}. Assuming JS rendering needed.")
                 return True
            body_start = html_content.find('<body')
            body_content = html_content[body_start:] if body_start != -1 else html_content
            if len(body_content) < 1500:
                 logger.debug(f"Body content size small ({len(body_content)} bytes) for {url}. Assuming JS rendering needed.")
                 return True
            logger.debug(f"Basic checks passed for {url}. Assuming static rendering sufficient.")
            return False
        else:
            logger.warning(f"Incomplete HTML structure found for {url}. Assuming JS rendering needed.")
            return True
    except requests.exceptions.RequestException as e:
        logger.warning(f"Initial request failed for JS check on {url}: {e}. Assuming JS rendering needed.")
        return True

def check_robots_txt(url: str, user_agent: str) -> bool:
    """
    Checks if a URL is allowed by the site's robots.txt.
    """
    try:
        robots_url = parse.urljoin(url, "/robots.txt")
        rp = robotparser.RobotFileParser()
        rp.set_url(robots_url)
        logger.debug(f"Reading robots.txt from {robots_url}")
        rp.read()
        can_fetch = rp.can_fetch(user_agent, url)
        logger.debug(f"Robots.txt check for {url} with agent '{user_agent}': {'Allowed' if can_fetch else 'Disallowed'}")
        return can_fetch
    except Exception as e:
        logger.warning(f"Error checking robots.txt for {url}: {e}")
        return True

def get_html_content(
    url: str, output_file: str, ignore_robots: bool = False, execute_js: bool = True
) -> str:
    """Fetches HTML content from a URL, optionally executing JavaScript."""
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()  # or use firefox or webkit
            context = browser.new_context()

            if ignore_robots:
                context.route(
                    "**/*", lambda route: route.continue_()
                )  # Ignore robots.txt

            page = context.new_page()

            if execute_js:
                page.goto(url, wait_until="networkidle")  # Wait for JS to render
            else:
                page.goto(url)

            html_content = page.content()  # Get the rendered HTML

            browser.close()
            return html_content

    except Exception as e:
        print(f"Error fetching content: {e}")
        return None
    
    
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Fetch HTML content using requests or Playwright.")
    parser.add_argument("--url", required=True, help="URL to fetch.")
    parser.add_argument("--output", help="Optional path to save the HTML file.")
    parser.add_argument("--ignore-robots", action='store_true', help="Ignore robots.txt rules (for testing only).")
    args = parser.parse_args()

    try:
        html = get_html_content(args.url, args.output, ignore_robots=args.ignore_robots)
        if html:
            page_name = Path(args.url).name or Path(args.url).parent.name or "page"
            print(f"✅ HTML Page '{page_name}' Successfully Downloaded/Processed.")
        else:
            print(f"❌ Failed to retrieve or disallowed fetching for URL: {args.url}")
    except Exception as e:
        logger.critical(f"Script failed for URL {args.url}: {e}")
        print(f"❌ Script failed: {e}")
