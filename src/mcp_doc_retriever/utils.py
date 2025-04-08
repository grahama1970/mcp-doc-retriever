from urllib.parse import urlparse, urlunparse
import hashlib
import os
import asyncio

TIMEOUT_REQUESTS = 30
TIMEOUT_PLAYWRIGHT = 60

playwright_semaphore = asyncio.Semaphore(3)  # Limit concurrent Playwright sessions
requests_semaphore = asyncio.Semaphore(10)  # Limit concurrent HTTP requests

def canonicalize_url(url: str) -> str:
    """Normalize URL by:
    - Lowercasing scheme and host
    - Removing default ports (80, 443)
    - Removing fragments (#...) 
    - Removing query parameters (?...)
    """
    parsed = urlparse(url)
    
    # Lowercase scheme and netloc (host)
    scheme = parsed.scheme.lower()
    netloc = parsed.netloc.lower()
    
    # Remove default ports
    if ':' in netloc:
        host, port = netloc.split(':', 1)
        if (scheme == 'http' and port == '80') or (scheme == 'https' and port == '443'):
            netloc = host
    
    # Remove fragment and query
    return urlunparse((scheme, netloc, parsed.path, '', '', ''))

def generate_download_id(url: str) -> str:
    """Generate unique download ID as MD5 hash of canonical URL"""
    canonical_url = canonicalize_url(url)
    return hashlib.md5(canonical_url.encode('utf-8')).hexdigest()

def url_to_local_path(base_dir: str, url: str) -> str:
    """Generate local file path from URL in mirrored structure:
    base_dir/content/{hostname}/{path}/filename.html
    """
    parsed = urlparse(canonicalize_url(url))
    path = parsed.path.lstrip('/')
    
    # Use index.html for root paths
    filename = os.path.basename(path) or 'index.html'
    dir_path = os.path.dirname(path)
    
    # Construct full path
    return os.path.join(
        base_dir,
        'content',
        parsed.netloc,
        dir_path,
        filename
    )