# Downloader Package

Handles fetching documentation content from Git, Web (HTTPX), and Playwright sources.

See the main project README.md for architecture and module details.

Key Modules:
- `workflow.py`: Main orchestration (`fetch_documentation_workflow`)
- `git.py`: Git cloning and scanning
- `web.py`: Web crawling (`start_recursive_download`)
- `fetchers.py`: HTTPX/Playwright implementations
- `robots.py`: Robots.txt handling
- `helpers.py`: Path generation (`url_to_local_path`)