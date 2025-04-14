"""
Tree-sitter based code validation.

This module provides code validation using tree-sitter-language-pack.
It validates code snippets and determines their language.

Links:
- tree-sitter-language-pack: https://github.com/Goldziher/tree-sitter-language-pack

Sample Input/Output:
    Input:
        code = '''
        def hello():
            print("Hello")
        '''
        lang_hint = "python"
        
    Output:
        (True, "python")  # Valid Python code
"""

from typing import Optional, Tuple
from tree_sitter_language_pack import get_parser
from loguru import logger


def validate_code_snippet(code: str, lang_hint: Optional[str] = None) -> Tuple[bool, Optional[str]]:
    """Validate a code snippet using tree-sitter and determine its language.
    
    Args:
        code: The code snippet to validate
        lang_hint: Optional language hint (e.g., 'python', 'javascript')
        
    Returns:
        Tuple of (is_valid, detected_language)
        - is_valid: True if code is valid in any supported language
        - detected_language: The language name if validation succeeded, None otherwise
    """
    if not code or not code.strip():
        return False, None
        
    # Try the hinted language first if provided
    if lang_hint:
        lang_hint = lang_hint.lower()
        # Handle special cases
        if lang_hint == "js":
            lang_hint = "javascript"
        elif lang_hint == "ts":
            lang_hint = "typescript"
            
        try:
            parser = get_parser(lang_hint)
            tree = parser.parse(code.encode())
            if not tree.root_node.has_error:
                return True, lang_hint
        except Exception as e:
            logger.debug(f"Validation failed for hinted language {lang_hint}: {e}")
    
    # Try common languages if hint fails or isn't provided
    common_languages = ["python", "javascript", "typescript", "java", "go", "ruby"]
    
    for lang in common_languages:
        if lang == lang_hint:
            continue  # Already tried this one
        try:
            parser = get_parser(lang)
            tree = parser.parse(code.encode())
            if not tree.root_node.has_error:
                return True, lang
        except Exception as e:
            logger.debug(f"Validation failed for language {lang}: {e}")
            
    return False, None


if __name__ == "__main__":
    # Test valid Python code
    test_py = '''
def hello():
    print("Hello")
'''
    is_valid, lang = validate_code_snippet(test_py, "python")
    print(f"Valid Python test: {is_valid}, lang={lang}")
    assert is_valid and lang == "python"
    
    # Test valid JavaScript code
    test_js = '''
function hello() {
    console.log("Hello");
}
'''
    is_valid, lang = validate_code_snippet(test_js, "javascript")
    print(f"Valid JavaScript test: {is_valid}, lang={lang}")
    assert is_valid and lang == "javascript"
    
    # Test invalid code
    test_invalid = '''
def broken_function(
    print("Missing closing parenthesis"
'''
    is_valid, lang = validate_code_snippet(test_invalid, "python")
    print(f"Invalid code test: {is_valid}, lang={lang}")
    assert not is_valid
    
    print("All tests passed!")
