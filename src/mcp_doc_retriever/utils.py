from urllib.parse import urlparse, urlunparse
import hashlib
import os
import asyncio
import re

TIMEOUT_REQUESTS = 30
TIMEOUT_PLAYWRIGHT = 60

# Limit concurrent operations to prevent resource exhaustion or rate limiting
playwright_semaphore = asyncio.Semaphore(
    3
)  # Limit concurrent Playwright browser instances/contexts
requests_semaphore = asyncio.Semaphore(
    10
)  # Limit concurrent outgoing HTTP requests via httpx


def canonicalize_url(url: str) -> str:
    """Normalize URL by:
    - Lowercasing scheme and host
    - Removing default ports (80 for http, 443 for https)
    - Removing fragments (#...)
    - Removing query parameters (?...)
    - Ensuring path starts with '/' if not empty
    """
    try:
        parsed = urlparse(url)

        # Lowercase scheme and netloc (host)
        scheme = parsed.scheme.lower()
        netloc = parsed.netloc.lower()

        # Remove default ports
        if ":" in netloc:
            host, port_str = netloc.split(":", 1)
            try:
                port = int(port_str)
                if (scheme == "http" and port == 80) or (
                    scheme == "https" and port == 443
                ):
                    netloc = host
            except ValueError:
                # Keep netloc as is if port is not a valid integer
                pass

        # Ensure path starts with '/' if it exists, otherwise it's empty
        path = parsed.path if parsed.path else ""
        if path and not path.startswith("/"):
            path = "/" + path  # Should not happen with valid urlparse, but defensive

        # Remove params, query, and fragment
        # path parameter in urlunparse handles the actual path component
        return urlunparse((scheme, netloc, path, "", "", ""))
    except Exception as e:
        # If urlparse fails or any other error occurs, maybe return original or raise?
        # Raising might be better to signal invalid input upstream.
        raise ValueError(f"Could not canonicalize URL: {url} - Error: {e}") from e


def generate_download_id(url: str) -> str:
    """Generate unique download ID as MD5 hash of canonical URL."""
    try:
        canonical_url_str = canonicalize_url(url)
        # Use utf-8 encoding for consistency
        return hashlib.md5(canonical_url_str.encode("utf-8")).hexdigest()
    except ValueError as e:
        # Handle cases where canonicalization fails
        raise ValueError(
            f"Could not generate download ID for invalid URL: {url} - {e}"
        ) from e


# Inside src/mcp_doc_retriever/utils.py


def url_to_local_path(base_dir: str, url: str) -> str:
    """Generate local file path from URL in mirrored structure:
    base_dir/{hostname}/{path}/[index.html|filename]

    Assumes base_dir is the root where the hostname directory should be created (e.g., /app/downloads/content).
    """
    try:
        parsed = urlparse(canonicalize_url(url))
        if not parsed.netloc:
            raise ValueError("URL must have a valid hostname (netloc).")

        safe_hostname = re.sub(r"[^a-zA-Z0-9\.\-]", "_", parsed.netloc)
        safe_hostname = safe_hostname.replace(":", "_")

        path_segment = parsed.path.lstrip("/")

        if not path_segment or path_segment.endswith("/"):
            filename = "index.html"
            dir_path = os.path.dirname(path_segment)
        else:
            filename = os.path.basename(path_segment)
            dir_path = os.path.dirname(path_segment)
            if not filename:
                filename = "index.html"

        safe_dir_parts = []
        for part in dir_path.split(os.sep):
            safe_part = re.sub(r'[<>:"/\\|?*]', "_", part)
            if safe_part or not safe_dir_parts:
                safe_dir_parts.append(safe_part)
        safe_dir_path = os.path.join(*safe_dir_parts)

        safe_filename = re.sub(r'[<>:"/\\|?*]', "_", filename)
        max_filename_len = 200
        if len(safe_filename) > max_filename_len:
            name, ext = os.path.splitext(safe_filename)
            safe_filename = name[: max_filename_len - len(ext) - 1] + ext

        # *** CORRECTED PATH CONSTRUCTION ***
        # Construct path relative to base_dir, NO extra 'content' prepended here.
        relative_path = os.path.join(safe_hostname, safe_dir_path, safe_filename)
        full_path = os.path.join(
            base_dir, relative_path
        )  # Join base_dir with hostname/path/file structure

        norm_path = os.path.normpath(full_path)

        # Validation against base_dir
        abs_base_dir = os.path.abspath(base_dir)
        abs_norm_path = os.path.abspath(norm_path)
        # Check against base_dir directly now
        if (
            not abs_norm_path.startswith(abs_base_dir + os.sep)
            and abs_norm_path != abs_base_dir
        ):
            raise ValueError(
                f"Constructed path '{norm_path}' escapes base directory '{base_dir}' for URL '{url}'"
            )

        max_total_path = 400
        if len(norm_path) > max_total_path:
            raise ValueError(
                f"Constructed path exceeds maximum length ({max_total_path} chars): '{norm_path}'"
            )

        return norm_path

    except ValueError as e:
        raise ValueError(f"Could not generate local path for URL: {url} - {e}") from e
    except Exception as e:
        raise RuntimeError(f"Error generating local path for URL: {url} - {e}") from e

# --- Example Usage ---
if __name__ == "__main__":
    import re  # Need re for url_to_local_path changes above

    print("--- Utility Function Examples ---")

    urls_to_test = [
        "http://example.com",
        "http://example.com/",
        "https://example.com:443/path/to/page.html?query=1#fragment",
        "HTTP://Example.com/Another_Path/",
        "http://example.com:8080/ différent /path.aspx",  # Non-standard port, encoding needed
        "http://example.com/..%2f../etc/passwd",  # Path traversal attempt
        "ftp://example.com/resource",  # Different scheme
        "invalid-url",
    ]

    print("\nCanonicalization & Download ID:")
    for url in urls_to_test:
        try:
            canon = canonicalize_url(url)
            dl_id = generate_download_id(url)
            print(f"'{url}' -> Canon='{canon}', ID='{dl_id}'")
        except ValueError as e:
            print(f"'{url}' -> ERROR: {e}")

    print("\nURL to Local Path (Base Dir: '/tmp/downloads_test'):")
    base = "/tmp/downloads_test"
    # Ensure content dir exists for realistic path generation examples
    try:
        os.makedirs(os.path.join(base, "content"), exist_ok=True)
    except Exception:
        pass

    for url in urls_to_test:
        # Filter out invalid ones handled above
        if "ERROR" in locals().get("canon", "") and url in locals().get("url", ""):
            continue
        try:
            local_path = url_to_local_path(base, url)
            print(f"'{url}' -> Local Path='{local_path}'")
        except (ValueError, RuntimeError) as e:
            print(f"'{url}' -> ERROR generating path: {e}")

    print("\n--- End Examples ---")
