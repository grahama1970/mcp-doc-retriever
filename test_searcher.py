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
if __name__ == "__main__":
    test_scan_files_for_keywords()
    test_scan_files_for_security()
    test_scan_files_for_bypass_attempts()
