import logging
logging.basicConfig(level=logging.DEBUG)

import os
from src.mcp_doc_retriever.searcher import scan_files_for_keywords

def create_temp_html_file(filename, html_content):
    with open(filename, 'w', encoding='utf-8') as f:
        f.write(html_content)

def test_scan_files_for_keywords():
    # Prepare test HTML files
    files = ['test1.html', 'test2.html', 'test3.html']
    html_contents = [
        "<html><body>Hello World! This is a test file.</body></html>",
        "<html><body>Another file with different CONTENT.</body></html>",
        "<html><body>Completely unrelated text.</body></html>"
    ]
    for fname, content in zip(files, html_contents):
        create_temp_html_file(fname, content)

    # Define keyword sets
    keywords1 = ["hello", "world"]
    keywords2 = ["different", "content"]
    keywords3 = ["missing", "keyword"]

    # Run scan
    result1 = scan_files_for_keywords(files, keywords1)
    result2 = scan_files_for_keywords(files, keywords2)
    result3 = scan_files_for_keywords(files, keywords3)

    # Print results
    print("Test 1 - Expect ['test1.html']:", result1)
    print("Test 2 - Expect ['test2.html']:", result2)
    print("Test 3 - Expect []:", result3)

    # Cleanup
    for fname in files:
        try:
            os.remove(fname)
        except Exception:
            pass



def test_scan_files_for_security():
    import os
    import random

    # Large file (~100MB)
    large_file = "large_test.html"
    with open(large_file, "w", encoding="utf-8") as f:
        chunk = "<html><body>" + ("Hello world! " * 1000) + "</body></html>\n"
        for _ in range(10000):  # ~100MB total
            f.write(chunk)

    # Deeply nested HTML
    nested_file = "nested_test.html"
    depth = 10000
    with open(nested_file, "w", encoding="utf-8") as f:
        f.write("<html><body>")
        f.write("<div>" * depth)
        f.write("Deep content")
        f.write("</div>" * depth)
        f.write("</body></html>")

    # Malformed HTML
    malformed_file = "malformed_test.html"
    with open(malformed_file, "w", encoding="utf-8") as f:
        f.write("<html><body><div><span>Unclosed tags" * 10000)

    # Binary file disguised as HTML
    binary_file = "binary_test.html"
    with open(binary_file, "wb") as f:
        f.write(os.urandom(1024 * 1024))  # 1MB random bytes

    # Sensitive file path (may not exist)
    sensitive_file = "/etc/passwd"  # Unix example

    files = [large_file, nested_file, malformed_file, binary_file, sensitive_file]
    keywords = ["root", "hello", "deep", "unclosed"]

    try:
        # Only allow scanning files in current directory; /etc/passwd should be skipped
        result = scan_files_for_keywords(files, keywords, allowed_base_dirs=["."])
        print("Security Test Results:", result)
    except Exception as e:
        print("Security Test Exception:", e)

    # Cleanup
    for fname in [large_file, nested_file, malformed_file, binary_file]:
        try:
            os.remove(fname)
        except Exception:
            pass


def test_scan_files_for_bypass_attempts():
    import os
    import shutil

    # Prepare small file (<1KB)
    small_file = "small_file.html"
    with open(small_file, "w", encoding="utf-8") as f:
        f.write("<html><body>hello world</body></html>")

    # Prepare large file (>10MB)
    large_file = "large_file.html"
    with open(large_file, "w", encoding="utf-8") as f:
        chunk = "A" * 1024 * 1024  # 1MB chunk
        for _ in range(11):  # 11MB total
            f.write(chunk)

    # Copy small file to swap_file
    swap_file = "swap_file.html"
    shutil.copyfile(small_file, swap_file)

    # Create symlink inside current dir pointing to /etc/passwd
    symlink_name = "symlink_to_sensitive"
    try:
        if os.path.islink(symlink_name) or os.path.exists(symlink_name):
            os.remove(symlink_name)
        os.symlink("/etc/passwd", symlink_name)
    except Exception as e:
        print(f"Could not create symlink: {e}")

    files = [swap_file, symlink_name]
    keywords = ["root", "hello"]

    try:
        # Simulate TOCTOU: replace swap_file with large_file before scan
        os.remove(swap_file)
        shutil.copyfile(large_file, swap_file)

        result = scan_files_for_keywords(files, keywords, allowed_base_dirs=["."])
        print("Bypass Attempt Test Results:", result)
    except Exception as e:
        print("Bypass Attempt Test Exception:", e)

    # Cleanup
    for fname in [small_file, large_file, swap_file, symlink_name]:
        try:
            if os.path.islink(fname) or os.path.isfile(fname):
                os.remove(fname)
        except Exception:
            pass

from src.mcp_doc_retriever.searcher import extract_text_with_selector

def test_extract_text_with_selector():
    filename = "test_selector.html"
    html_content = """
    <html><body>
    <p>This is a paragraph with apple and banana.</p>
    <p>This paragraph mentions apple only.</p>
    <p>This one has banana only.</p>
    <p>Nothing relevant here.</p>
    </body></html>
    """
    with open(filename, "w", encoding="utf-8") as f:
        f.write(html_content)

    # No keyword filtering
    snippets = extract_text_with_selector(filename, "p")
    print("All <p> snippets:", snippets)

    # Filter with keywords 'apple' and 'banana'
    filtered_snippets = extract_text_with_selector(filename, "p", ["apple", "banana"])
    print("Filtered snippets (apple AND banana):", filtered_snippets)

    # Cleanup
    try:
        os.remove(filename)
    except Exception:
        pass

def test_extract_text_with_selector_security():
    print("\n--- Running extract_text_with_selector security tests ---")

    # 1. Invalid and malicious file paths
    paths = [
        "nonexistent_file.html",
        "../../etc/passwd",
        "/dev/null"
    ]
    for path in paths:
        try:
            result = extract_text_with_selector(path, "p")
            print(f"File path '{path}' result: {result}")
        except Exception as e:
            print(f"Exception for file path '{path}': {e}")

    # 2. Malformed and malicious HTML content
    malformed_cases = {
        "broken_tags.html": "<html><body><p>Unclosed paragraph",
        "script_injection.html": "<html><body><script>alert('XSS');</script><p>Test</p></body></html>",
        "deep_nesting.html": "<div>" * 1000 + "Deep" + "</div>" * 1000
    }
    for filename, content in malformed_cases.items():
        try:
            with open(filename, "w", encoding="utf-8") as f:
                f.write(content)
            result = extract_text_with_selector(filename, "p")
            print(f"Malformed HTML '{filename}' result: {result}")
        except Exception as e:
            print(f"Exception for malformed HTML '{filename}': {e}")
        finally:
            try:
                os.remove(filename)
            except:
                pass

    # 3. CSS selector abuse
    selectors = [
        "[",            # Invalid selector
        "*",            # Universal selector
        "div div div div div"  # Complex selector
    ]
    html_file = "selector_test.html"
    with open(html_file, "w", encoding="utf-8") as f:
        f.write("<div><div><div><div><div><p>Deep</p></div></div></div></div></div>")
    for sel in selectors:
        try:
            result = extract_text_with_selector(html_file, sel)
            print(f"Selector '{sel}' result: {result}")
        except Exception as e:
            print(f"Exception for selector '{sel}': {e}")
    try:
        os.remove(html_file)
    except:
        pass

    # 4. Keyword filtering abuse
    keyword_cases = [
        [],
        [""],
        ["a" * 10000],  # Very long keyword
        ["$", "^", ".*"]
    ]
    html_file = "keyword_test.html"
    with open(html_file, "w", encoding="utf-8") as f:
        f.write("<p>Sample paragraph with special characters $ ^ .* and long text</p>")
    for kwlist in keyword_cases:
        try:
            result = extract_text_with_selector(html_file, "p", kwlist)
            print(f"Keywords {kwlist} result: {result}")
        except Exception as e:
            print(f"Exception for keywords {kwlist}: {e}")
    try:
        os.remove(html_file)
    except:
        pass

    print("--- Security tests completed ---")


if __name__ == "__main__":
    test_scan_files_for_keywords()
    test_scan_files_for_security()
    test_scan_files_for_bypass_attempts()
    test_extract_text_with_selector()
    test_extract_text_with_selector_security()
import json
import os
from src.mcp_doc_retriever.searcher import perform_search
from src.mcp_doc_retriever import config


def test_perform_search_basic():
    # Setup temporary download_id and paths
    download_id = "test_download"
    index_dir = os.path.join(config.DOWNLOAD_BASE_DIR, "index")
    os.makedirs(index_dir, exist_ok=True)
    index_path = os.path.join(index_dir, f"{download_id}.jsonl")

    # Create a sample HTML file
    html_path = os.path.join(config.DOWNLOAD_BASE_DIR, "test_file.html")
    html_content = "<html><body><div class='content'>Hello World! Extract me.</div></body></html>"
    os.makedirs(os.path.dirname(html_path), exist_ok=True)
    with open(html_path, "w", encoding="utf-8") as f:
        f.write(html_content)

    # Create a sample index record with fetch_status success
    record = {
        "original_url": "http://example.com",
        "canonical_url": "http://example.com",
        "local_path": html_path,
        "content_md5": None,
        "fetch_status": "success",
        "http_status": 200,
        "error_message": None
    }
    with open(index_path, "w", encoding="utf-8") as f:
        f.write(json.dumps(record) + "\n")

    # Call perform_search
    results = perform_search(
        download_id=download_id,
        scan_keywords=["hello", "world"],
        selector=".content",
        extract_keywords=["extract"]
    )

    # Check results
    assert len(results) == 1, f"Expected 1 result, got {len(results)}"
    result = results[0]
    assert result.original_url == "http://example.com"
    assert "Extract me" in result.extracted_content
    assert result.selector_matched == ".content"

    # Cleanup
    try:
        os.remove(index_path)
    except:
        pass
    try:
        os.remove(html_path)
    except:
        pass

def test_perform_search_invalid_download_id():
    from src.mcp_doc_retriever.searcher import perform_search

    # Invalid download_id with path traversal attempt
    invalid_ids = ["../secret", "bad/id", "id with spaces", "id;rm -rf", "id..", "id/..", "id/../etc/passwd"]

    for bad_id in invalid_ids:
        results = perform_search(
            download_id=bad_id,
            scan_keywords=["test"],
            selector="body"
        )
        assert results == [], f"Expected empty results for invalid download_id '{bad_id}'"

def test_perform_search_skips_disallowed_paths():
    import json
    import os
    from src.mcp_doc_retriever.searcher import perform_search
    from src.mcp_doc_retriever import config

    # Convert DOWNLOAD_BASE_DIR to absolute path to avoid path check issues
    base_dir = os.path.abspath(config.DOWNLOAD_BASE_DIR)

    download_id = "security_test"
    index_dir = os.path.join(base_dir, "index")
    os.makedirs(index_dir, exist_ok=True)
    index_path = os.path.join(index_dir, f"{download_id}.jsonl")

    # Create a valid HTML file inside allowed dir
    valid_html_path = os.path.join(base_dir, "valid_file.html")
    os.makedirs(os.path.dirname(valid_html_path), exist_ok=True)
    with open(valid_html_path, "w", encoding="utf-8") as f:
        f.write("<html><body><div class='content'>Safe Content</div></body></html>")

    # Create index with one valid and one malicious entry
    valid_record = {
        "original_url": "http://safe.com",
        "canonical_url": "http://safe.com",
        "local_path": valid_html_path,
        "content_md5": None,
        "fetch_status": "success",
        "http_status": 200,
        "error_message": None
    }
    malicious_record = {
        "original_url": "http://evil.com",
        "canonical_url": "http://evil.com",
        "local_path": "/etc/passwd",
        "content_md5": None,
        "fetch_status": "success",
        "http_status": 200,
        "error_message": None
    }
    with open(index_path, "w", encoding="utf-8") as f:
        f.write(json.dumps(valid_record) + "\n")
        f.write(json.dumps(malicious_record) + "\n")

    print("Valid HTML path:", valid_html_path)
    print("File exists:", os.path.exists(valid_html_path))
    with open(valid_html_path, 'r', encoding='utf-8') as f:
        print("File content:", f.read())

    results = perform_search(
        download_id=download_id,
        scan_keywords=["safe", "content"],
        selector=".content"
    )

    print("Results:", results)

    # Only the valid file should be returned
    assert len(results) == 1, f"Expected 1 result, got {len(results)}"
    assert results[0].original_url == "http://safe.com"
    assert "Safe Content" in results[0].extracted_content

    # Cleanup
    try:
        os.remove(index_path)
    except:
        pass
    try:
        os.remove(valid_html_path)
    except:
        pass
