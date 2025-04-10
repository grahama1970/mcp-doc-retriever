import asyncio
import os
import tempfile
import pytest
import aiofiles
import json
from src.mcp_doc_retriever.downloader import start_recursive_download
from src.mcp_doc_retriever.models import IndexRecord

# Test recursive download security
@pytest.mark.asyncio
async def test_symlink_attack(tmpdir):
    """Test that symlinks in base directory can't be exploited"""
    # Create symlink in base directory
    base_dir = str(tmpdir)
    os.symlink("/etc/passwd", os.path.join(base_dir, "malicious_link"))
    
    # Run recursive download
    download_id = "symlink_test"
    await start_recursive_download(
        "http://example.com",
        depth=1,
        force=False,
        download_id=download_id,
        base_dir=base_dir
    )
    
    # Verify no files were written outside base_dir
    index_path = os.path.join(base_dir, "index", f"{download_id}.jsonl")
    assert os.path.exists(index_path)
    
    # Check index for any paths outside base_dir
    async with aiofiles.open(index_path, 'r') as f:
        async for line in f:
            record = IndexRecord.parse_raw(line)
            if record.local_path:
                assert record.local_path.startswith(base_dir), \
                    f"Path traversal via symlink: {record.local_path}"

@pytest.mark.asyncio
async def test_deep_recursion(tmpdir):
    """Test that deep recursion doesn't cause stack overflow"""
    download_id = "deep_recursion_test"
    await start_recursive_download(
        "http://example.com/start",
        depth=200,  # Very deep recursion
        force=False,
        download_id=download_id,
        base_dir=str(tmpdir)
    )
    
    # Just verify it completes without crashing
    index_path = os.path.join(str(tmpdir), "index", f"{download_id}.jsonl")
    assert os.path.exists(index_path)

@pytest.mark.asyncio
async def test_malformed_urls(tmpdir):
    """Test handling of malformed URLs with special chars"""
    malformed_urls = [
        "http://example.com/\x00evil",
        "http://example.com/evil<script>",
        "http://example.com/evil%00.html",
        "http://example.com/evil?param=<script>"
    ]
    
    for url in malformed_urls:
        download_id = f"malformed_{hash(url)}"
        await start_recursive_download(
            url,
            depth=1,
            force=False,
            download_id=download_id,
            base_dir=str(tmpdir)
        )
        
        # Verify index was created and contains the URL
        index_path = os.path.join(str(tmpdir), "index", f"{download_id}.jsonl")
        assert os.path.exists(index_path)
        
        # Check URL was properly escaped in index
        async with aiofiles.open(index_path, 'r') as f:
            content = await f.read()
            # Compare JSON-decoded URLs to account for escaping
            record = json.loads(content.strip())
            assert url in record['original_url'] or url in record['canonical_url']

@pytest.mark.asyncio
async def test_many_small_files(tmpdir):
    """Test resource exhaustion via many small files"""
    download_id = "many_files_test"
    await start_recursive_download(
        "http://example.com/many-files",
        depth=10,
        force=False,
        download_id=download_id,
        base_dir=str(tmpdir)
    )
    
    # Verify process didn't crash
    index_path = os.path.join(str(tmpdir), "index", f"{download_id}.jsonl")
    assert os.path.exists(index_path)
    
    # Check file count is reasonable
    files = [f for f in os.listdir(str(tmpdir)) if not f.startswith('index')]
    assert len(files) < 1000, "Possible resource exhaustion"

@pytest.mark.asyncio
async def test_circular_references(tmpdir):
    """Test handling of circular references in links"""
    download_id = "circular_test"
    await start_recursive_download(
        "http://example.com/circular",
        depth=100,
        force=False,
        download_id=download_id,
        base_dir=str(tmpdir)
    )
    
    # Verify process completed
    index_path = os.path.join(str(tmpdir), "index", f"{download_id}.jsonl")
    assert os.path.exists(index_path)
    
    # Check reasonable number of downloads (not infinite)
    count = 0
    async with aiofiles.open(index_path, 'r') as f:
        async for line in f:
            count += 1
    assert count < 1000, "Possible infinite recursion"

@pytest.mark.asyncio
async def test_concurrent_recursive_downloads(tmpdir):
    """Test that concurrent recursive downloads don't interfere"""
    base_dir = str(tmpdir)
    download_ids = [f"concurrent_{i}" for i in range(5)]
    
    # Start concurrent downloads
    tasks = [
        start_recursive_download(
            "http://example.com",
            depth=3,
            force=False,
            download_id=did,
            base_dir=base_dir
        )
        for did in download_ids
    ]
    await asyncio.gather(*tasks)
    
    # Verify all indexes were created
    for did in download_ids:
        index_path = os.path.join(base_dir, "index", f"{did}.jsonl")
        assert os.path.exists(index_path), f"Missing index for {did}"