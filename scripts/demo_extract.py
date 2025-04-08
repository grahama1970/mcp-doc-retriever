from mcp_doc_retriever.searcher import extract_text_with_selector

def demo():
    file_path = "test_data/sample.html"
    selector = "p"

    print("=== Keywords: ['apple', 'banana'] ===")
    result = extract_text_with_selector(file_path, selector, ['apple', 'banana'])
    for r in result:
        print("-", r)

    print("\n=== Keywords: ['banana'] ===")
    result = extract_text_with_selector(file_path, selector, ['banana'])
    for r in result:
        print("-", r)

    print("\n=== Keywords: [] ===")
    result = extract_text_with_selector(file_path, selector, [])
    for r in result:
        print("-", r)

if __name__ == "__main__":
    demo()