"""
Description:
  This module focuses on respecting the Robots Exclusion Protocol (robots.txt).
  The primary function, `_is_allowed_by_robots`, asynchronously checks if a given
  URL is allowed to be crawled based on the rules defined in the corresponding
  robots.txt file. It handles:
  - Fetching robots.txt using a shared `httpx.AsyncClient`.
  - Caching parsed rules per domain (`robots_cache`) to minimize network requests.
  - Parsing robots.txt content, respecting User-agent directives (both specific
    and wildcard '*'), Allow/Disallow rules, comments, and basic malformed lines.
  - Applying standard robots.txt precedence rules (specific agent over wildcard,
    longest path match determines outcome).
  - Gracefully handling errors like timeouts, network issues, or non-existent
    robots.txt files (defaulting to allowing access in most error cases).

Third-Party Documentation:
  - httpx (Used for fetching robots.txt): https://www.python-httpx.org/

Python Standard Library Documentation:
  - urllib.parse: https://docs.python.org/3/library/urllib.parse.html
  - collections.defaultdict: https://docs.python.org/3/library/collections.html#collections.defaultdict

Sample Input (Conceptual - assumes setup within a running asyncio loop):
  url = "https://example.com/private/page"
  client = httpx.AsyncClient()
  robots_cache = {} # Shared cache dictionary
  is_allowed = await _is_allowed_by_robots(url, client, robots_cache)

Sample Expected Output:
  - Returns `True` if crawling the URL is allowed according to the fetched/cached
    robots.txt rules and our defined user agent ("MCPBot/1.0").
  - Returns `False` if crawling is disallowed by the rules.
  - Returns `True` (allows crawling) by default if robots.txt cannot be fetched
    or parsed, or if no rules match the specific URL path.
  - Updates the `robots_cache` dictionary with parsed rules for the domain.
"""
import logging
import httpx
from urllib.parse import urlparse, urljoin
from collections import defaultdict  # Use defaultdict for easier rule storage
from typing import Dict, List, Tuple  # Added for type hints

logger = logging.getLogger(__name__)


async def _is_allowed_by_robots(
    url: str, client: httpx.AsyncClient, robots_cache: dict
) -> bool:
    """
    Check robots.txt rules for the given URL using a shared client and cache.
    Handles specific agents, wildcards (*), and errors gracefully following standard precedence.
    """
    # --- Constants ---
    OUR_USER_AGENT = "mcpbot/1.0"  # Define our specific user agent name
    OUR_USER_AGENT_LOWER = OUR_USER_AGENT.lower()
    WILDCARD_AGENT = "*"

    try:
        parsed = urlparse(url)
        if not parsed.scheme or not parsed.netloc:
            logger.warning(f"Cannot check robots.txt for invalid URL: {url}")
            return False

        base_url = f"{parsed.scheme}://{parsed.netloc}"
        robots_url = urljoin(base_url, "/robots.txt")

        if base_url in robots_cache:
            rules = robots_cache[base_url]
            logger.debug(f"Using cached robots.txt rules for {base_url}")
        else:
            logger.debug(f"Fetching robots.txt from {robots_url}")
            rules: Dict[str, List[Tuple[str, str]]] = defaultdict(list)
            try:
                resp = await client.get(robots_url, timeout=15, follow_redirects=True)
                if resp.status_code == 200:
                    logger.debug(f"Parsing robots.txt for {base_url}")
                    current_agents_in_block = []  # List of agents this block applies to
                    lines = resp.text.splitlines()
                    for line_num, line in enumerate(lines, 1):
                        line = line.strip()

                        # Find comment start '#' and strip if present
                        comment_start = line.find("#")
                        if comment_start != -1:
                            line = line[
                                :comment_start
                            ].strip()  # Keep only part before comment

                        # Skip empty lines or lines that became empty after comment removal
                        if not line:
                            continue

                        parts = line.split(":", 1)
                        if len(parts) != 2:
                            logger.debug(
                                f"L{line_num}: Skipping malformed line (after comment removal): {line}"
                            )
                            continue  # Skip malformed lines

                        directive = parts[0].strip().lower()
                        value = parts[1].strip()  # Value already stripped of comments

                        if directive == "user-agent":
                            agent_name = value
                            # Clear previous agents when a new block starts
                            current_agents_in_block = [agent_name.lower()]
                            logger.debug(
                                f"L{line_num}: Processing User-agent: {agent_name}"
                            )

                        elif current_agents_in_block and directive in [
                            "allow",
                            "disallow",
                        ]:
                            path = value  # Value is already cleaned
                            if not path:
                                # Standard interpretation: Empty Allow/Disallow are ignored
                                logger.debug(
                                    f"L{line_num}: Skipping empty {directive.capitalize()} for agents {current_agents_in_block}"
                                )
                                continue

                            # Add the rule to all agents currently being defined for this block
                            for agent_lower in current_agents_in_block:
                                rules[agent_lower].append((directive, path))
                                logger.debug(
                                    f"L{line_num}: Adding rule ('{directive}', '{path}') for agent '{agent_lower}'"
                                )

                    robots_cache[base_url] = dict(rules)

                elif resp.status_code >= 400 and resp.status_code < 500:
                    logger.debug(
                        f"robots.txt not found or inaccessible ({resp.status_code}) at {robots_url}, allowing access."
                    )
                    robots_cache[base_url] = {}
                else:
                    logger.warning(
                        f"Server error ({resp.status_code}) fetching robots.txt from {robots_url}. Allowing access by default."
                    )
                    robots_cache[base_url] = {}

            except httpx.TimeoutException:
                logger.warning(
                    f"Timeout fetching robots.txt from {robots_url}. Allowing access by default."
                )
                robots_cache[base_url] = {}
            except httpx.RequestError as e:
                logger.warning(
                    f"Request error fetching robots.txt from {robots_url}: {e}. Allowing access by default."
                )
                robots_cache[base_url] = {}
            except Exception as e:
                logger.error(
                    f"Unexpected error fetching/parsing robots.txt from {robots_url}: {e}",
                    exc_info=True,
                )
                robots_cache[base_url] = {}

        # --- Rule Selection (Corrected logic already in place) ---
        specific_rules = rules.get(OUR_USER_AGENT_LOWER)
        if specific_rules is not None:
            agent_rules_to_use = specific_rules
            logger.debug(
                f"Using rules specific to {OUR_USER_AGENT} ({len(agent_rules_to_use)} rules found)."
            )
        else:
            agent_rules_to_use = rules.get(WILDCARD_AGENT, [])
            logger.debug(
                f"No specific rules for {OUR_USER_AGENT}, using wildcard rules ({len(agent_rules_to_use)} rules found)."
            )

        if not agent_rules_to_use:
            logger.debug(f"No applicable rules found for {url}, allowing.")
            return True

        path_to_check = parsed.path or "/"
        if parsed.query:
            path_to_check += "?" + parsed.query

        best_match_rule_type = None
        max_len = -1

        for rule_type, path_pattern in agent_rules_to_use:
            match_len = -1

            # Ensure pattern starts with '/' (more robust)
            if not path_pattern.startswith("/"):
                path_pattern = "/" + path_pattern

            # Check for prefix match (paths are case-sensitive)
            if path_to_check.startswith(path_pattern):
                match_len = len(path_pattern)
                log_prefix = (
                    f"URL='{path_to_check}' Rule=('{rule_type}', '{path_pattern}')"
                )
                logger.debug(
                    f"{log_prefix}: -> Prefix match found, match_len={match_len}"
                )
            else:
                continue

            if match_len > max_len:
                logger.debug(
                    f"  -> New best match: rule_type='{rule_type}', path='{path_pattern}', max_len={match_len} (was {max_len})"
                )
                max_len = match_len
                best_match_rule_type = rule_type
            # Handling ties: Implicitly uses the last rule found with max length.
            # This might differ slightly from Google's (first rule), but longest match is the primary factor.

        if best_match_rule_type == "allow":
            logger.debug(
                f"Allowed by most specific rule ('{best_match_rule_type}' path length {max_len}) for {url}"
            )
            return True
        elif best_match_rule_type == "disallow":
            logger.debug(
                f"Disallowed by most specific rule ('{best_match_rule_type}' path length {max_len}) for {url}"
            )
            return False
        else:
            logger.debug(
                f"No rules matched path '{path_to_check}', allowing by default."
            )
            return True

    except Exception as e:
        logger.error(
            f"Error during robots.txt check logic for {url}: {e}", exc_info=True
        )
        return True  # Default to allow if the checking logic itself fails


# --- Keep the __main__ block for testing ---
if __name__ == "__main__":
    """Demo robots.txt checking functionality"""
    import asyncio
    import tempfile
    from pathlib import Path
    from collections import defaultdict
    from typing import Dict, List, Tuple

    async def test_robots():
        logging.basicConfig(
            level=logging.DEBUG,
            format="[%(levelname)-8s] %(name)s:%(lineno)d - %(message)s",
        )
        logger.setLevel(logging.DEBUG)

        robots_content = """
# Comments should be ignored
User-agent: *
Disallow: /private/
Allow: /public/
Disallow: /confidential/secret # No trailing slash check
Allow: /confidential/public # No trailing slash check
Disallow: / # Root disallowed for *

User-agent: AnotherBot
Disallow: /another/

User-agent: MCPBot/1.0
Disallow: /admin/
Allow: /api/
Allow: /public/ # Allow override specific to MCPBot
Disallow: /confidential/ # Block whole dir for MCPBot (overrides *)
Allow: / # Allow root specifically for MCPBot? Test this override.
"""
        robots_file = tempfile.NamedTemporaryFile(
            mode="w", delete=False, encoding="utf-8"
        )
        robots_file.write(robots_content)
        robots_file.close()
        robots_path = Path(robots_file.name)
        logger.info(f"Created temporary robots.txt at: {robots_path}")

        # --- FIX: Adjusted Expected Results based on correct precedence ---
        test_cases = [
            # Path                   Expected MCPBot Result   Expected * Result  Reason
            ("public/page.html", True, True, "Allowed by specific Allow /public/"),
            (
                "public/nested/page",
                True,
                True,
                "Allowed by specific Allow /public/ (prefix)",
            ),
            (
                "private/secret.html",
                True,
                False,
                "No specific rule, * ignored, default ALLOW",
            ),  # Corrected expectation
            ("api/data.json", True, True, "Allowed by specific Allow /api/"),
            ("api/", True, True, "Allowed by specific Allow /api/ (exact)"),
            ("admin/control", False, True, "Blocked by specific Disallow /admin/"),
            ("admin/", False, True, "Blocked by specific Disallow /admin/ (exact)"),
            (
                "another/path",
                True,
                True,
                "No specific rule, * ignored, default ALLOW",
            ),  # Also affected by precedence
            (
                "confidential/secret",
                False,
                False,
                "Blocked by specific Disallow /confidential/ (prefix)",
            ),
            (
                "confidential/public",
                False,
                True,
                "Blocked by specific Disallow /confidential/ (prefix, overrides *)",
            ),
            (
                "other.html",
                True,
                False,
                "Blocked by * Disallow /, but MCPBot has specific Allow /, so ALLOW",
            ),  # Corrected expectation
            ("/", True, False, "Allowed by specific Allow /"),
        ]

        dummy_base = "http://testserver.com"

        class MockAsyncClientHttp:
            # Mock remains the same
            async def get(self, req_url, **kwargs):
                if req_url == f"{dummy_base}/robots.txt":
                    try:
                        content = robots_path.read_text(encoding="utf-8")
                        logger.debug(f"Mock returning robots.txt content for {req_url}")
                        # Need to wrap in httpx.Response for type consistency if needed downstream
                        # Creating dummy request object as well
                        dummy_request = httpx.Request("GET", req_url)
                        return httpx.Response(200, text=content, request=dummy_request)
                    except Exception as e:
                        logger.error(
                            f"Mock failed to read temp robots file {robots_path}: {e}"
                        )
                        dummy_request = httpx.Request("GET", req_url)
                        return httpx.Response(500, text="", request=dummy_request)

                logger.debug(f"Mock returning 404 for {req_url}")
                dummy_request = httpx.Request("GET", req_url)
                return httpx.Response(404, text="", request=dummy_request)

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                pass

        async with MockAsyncClientHttp() as http_client:
            print("\n--- Testing robots.txt rules (MCPBot/1.0) ---")
            robots_cache = {}
            all_passed = True
            for relative_path, expected_result, _, reason in test_cases:
                http_test_url = urljoin(dummy_base, relative_path)
                print(f"\nChecking URL: {http_test_url}")
                allowed = await _is_allowed_by_robots(
                    http_test_url, http_client, robots_cache
                )
                result_str = "ALLOWED" if allowed else "BLOCKED"
                expected_str = "ALLOWED" if expected_result else "BLOCKED"
                status = "PASS" if allowed == expected_result else "FAIL"
                print(f"- Result: {result_str} (Expected: {expected_str}) - {reason}")
                if status == "FAIL":
                    all_passed = False
                    print(f"*** TEST FAILED for {http_test_url} ***")

            print("\n--- Test Summary ---")
            if all_passed:
                print("✓ All robots.txt tests passed!") # Added print, more specific
            else:
                print("✗ Some tests FAILED.")

        try:
            robots_path.unlink()
            logger.info(f"Cleaned up temporary file: {robots_path}")
        except Exception as e:
            logger.warning(f"Could not clean up temp file {robots_path}: {e}")

    asyncio.run(test_robots())
