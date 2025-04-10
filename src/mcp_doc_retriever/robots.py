import logging
import httpx
from urllib.parse import urlparse, urljoin

logger = logging.getLogger(__name__)

async def _is_allowed_by_robots(
    url: str, client: httpx.AsyncClient, robots_cache: dict
) -> bool:
    """
    Check robots.txt rules for the given URL using a shared client and cache.
    Handles basic Allow/Disallow, wildcards (*), and errors gracefully.
    """
    try:
        parsed = urlparse(url)
        if not parsed.scheme or not parsed.netloc:
            logger.warning(f"Cannot check robots.txt for invalid URL: {url}")
            return False  # Treat invalid URLs as disallowed? Or allow? Let's be safe: disallow.

        base_url = f"{parsed.scheme}://{parsed.netloc}"
        robots_url = urljoin(base_url, "/robots.txt")

        if base_url in robots_cache:
            rules = robots_cache[base_url]
            logger.debug(f"Using cached robots.txt rules for {base_url}")
        else:
            logger.debug(f"Fetching robots.txt from {robots_url}")
            rules = {}  # Default: empty rules (allow all)
            try:
                resp = await client.get(
                    robots_url, timeout=15, follow_redirects=True
                )
                if resp.status_code == 200:
                    logger.debug(f"Parsing robots.txt for {base_url}")
                    current_agents = set(["*"])
                    relevant_section = False
                    specific_agent_rules = []
                    wildcard_rules = []

                    lines = resp.text.splitlines()
                    for line in lines:
                        line = line.strip()
                        if not line or line.startswith("#"):
                            continue

                        parts = line.split(":", 1)
                        if len(parts) != 2:
                            continue
                        directive = parts[0].strip().lower()
                        value = parts[1].strip()

                        if directive == "user-agent":
                            agent = value
                            if agent == "*":
                                relevant_section = True
                                current_agents.add("*")
                            elif agent.lower() == "mcpbot/1.0":
                                relevant_section = True
                                current_agents.add("specific")
                            else:
                                relevant_section = False
                        elif relevant_section and directive in ["allow", "disallow"]:
                            path = value
                            if not path:
                                if directive == "disallow":
                                    path = "/"
                                else:
                                    continue
                            rule = (directive, path)
                            if "specific" in current_agents:
                                specific_agent_rules.append(rule)
                            elif "*" in current_agents:
                                wildcard_rules.append(rule)

                    rules = {"specific": specific_agent_rules, "*": wildcard_rules}
                    robots_cache[base_url] = rules

                elif resp.status_code == 404:
                    logger.debug(f"robots.txt not found (404) at {robots_url}, allowing access.")
                    robots_cache[base_url] = {}
                else:
                    logger.warning(f"Failed to fetch robots.txt from {robots_url}, status: {resp.status_code}. Allowing access by default.")
                    robots_cache[base_url] = {}

            except httpx.TimeoutException:
                logger.warning(f"Timeout fetching robots.txt from {robots_url}. Allowing access by default.")
                robots_cache[base_url] = {}
            except httpx.RequestError as e:
                logger.warning(f"Request error fetching robots.txt from {robots_url}: {e}. Allowing access by default.")
                robots_cache[base_url] = {}
            except Exception as e:
                logger.error(f"Unexpected error fetching/parsing robots.txt from {robots_url}: {e}", exc_info=True)
                robots_cache[base_url] = {}

        agent_rules = rules.get("specific", []) or rules.get("*", [])
        if not agent_rules:
            logger.debug(f"No rules found or applicable for {url}, allowing.")
            return True

        path_to_check = "/" if not parsed.path else parsed.path
        if parsed.query:
            path_to_check += "?" + parsed.query

        best_match_rule = None
        max_len = -1

        for rule_type, path_pattern in agent_rules:
            match_len = -1
            pattern_compare = path_pattern.rstrip("*")
            is_wildcard = path_pattern.endswith("*")

            if is_wildcard:
                if path_to_check.startswith(pattern_compare):
                    match_len = len(pattern_compare)
            else:
                if path_to_check == path_pattern:
                    match_len = len(path_pattern)
                elif path_pattern.endswith("/") and path_to_check.startswith(path_pattern):
                    match_len = len(path_pattern)

            if match_len > max_len:
                max_len = match_len
                best_match_rule = rule_type

        if best_match_rule == "allow":
            logger.debug(f"Allowed by rule '{best_match_rule}' (matched length {max_len}) for {url}")
            return True
        elif best_match_rule == "disallow":
            logger.debug(f"Disallowed by rule '{best_match_rule}' (matched length {max_len}) for {url}")
            return False
        else:
            logger.debug(f"No specific rule matched for {url}, allowing by default.")
            return True

    except Exception as e:
        logger.error(f"Error during robots.txt check logic for {url}: {e}", exc_info=True)
        return True  # Default to allow if the checking logic itself fails

if __name__ == "__main__":
    """Demo robots.txt checking functionality"""
    import asyncio
    import tempfile
    from pathlib import Path
    
    async def test_robots():
        # Create a test robots.txt
        robots_content = """
        User-agent: *
        Disallow: /private/
        Allow: /public/
        
        User-agent: MCPBot/1.0
        Disallow: /admin/
        Allow: /api/
        """
        
        with tempfile.NamedTemporaryFile(delete=False) as f:
            f.write(robots_content.encode('utf-8'))
            robots_path = Path(f.name)
        
        # Test URLs
        test_urls = [
            f"file://{robots_path.parent}/public/page.html",
            f"file://{robots_path.parent}/private/secret.html",
            f"file://{robots_path.parent}/api/data.json",
            f"file://{robots_path.parent}/admin/control"
        ]
        
        # Setup test
        robots_cache = {}
        async with httpx.AsyncClient() as client:
            print("\nTesting robots.txt rules:")
            for url in test_urls:
                allowed = await _is_allowed_by_robots(url, client, robots_cache)
                print(f"- {url}: {'ALLOWED' if allowed else 'BLOCKED'}")
        
        robots_path.unlink()  # Clean up
    
    asyncio.run(test_robots())