# src/mcp_doc_retriever/context7/tree_sitter_utils.py
"""
Module: tree_sitter_utils.py
Description: This module provides utility functions for extracting code metadata using the tree-sitter library.
It defines functions to load tree-sitter languages and traverse syntax trees to extract functions, classes, and related information.

Third-party package documentation:
- tree_sitter: https://tree-sitter.github.io/tree-sitter/
- tree_sitter_languages: https://github.com/grantjenks/tree-sitter-languages

Sample Input:
code = "def my_function(param1: int, param2: str):\n  \"\"\"This is a docstring.\"\"\"\n  return param1 + len(param2)"
code_type = "python"

Expected Output:
A dictionary containing extracted code metadata, including a list of functions with their names, parameters, and docstrings.
Example:
{
    'functions': [{'name': 'my_function', 'parameters': [{'name': 'param1', 'type': 'int'}, {'name': 'param2', 'type': 'str'}], 'docstring': 'This is a docstring.'}],
    'classes': [],
    'tree_sitter_success': True
}
"""
import json
from typing import Optional, Dict
from loguru import logger
from tree_sitter import Parser
from tree_sitter_language_pack import get_binding, get_language, get_parser


# Mapping of code block types/file extensions to Tree-sitter language names
# Based on the available languages from tree-sitter-language-pack
LANGUAGE_MAPPINGS = {
    "actionscript": "actionscript",
    "as": "actionscript",
    "ada": "ada",
    "adb": "ada",
    "ads": "ada",
    "agda": "agda",
    "ino": "arduino",
    "asm": "asm",
    "astro": "astro",
    "sh": "bash",
    "bash": "bash",
    "beancount": "beancount",
    "bib": "bibtex",
    "bicep": "bicep",
    "bb": "bitbake",
    "c": "c",
    "h": "c",
    "cairo": "cairo",
    "capnp": "capnp",
    "chatito": "chatito",
    "clarity": "clarity",
    "clj": "clojure",
    "clojure": "clojure",
    "cmake": "cmake",
    "comment": "comment",
    "lisp": "commonlisp",
    "cpon": "cpon",
    "cpp": "cpp",
    "cxx": "cpp",
    "cc": "cpp",
    "hpp": "cpp",
    "cs": "csharp",
    "csharp": "csharp",
    "css": "css",
    "csv": "csv",
    "cu": "cuda",
    "d": "d",
    "dart": "dart",
    "dockerfile": "dockerfile",
    "dox": "doxygen",
    "el": "elisp",
    "ex": "elixir",
    "exs": "elixir",
    "elm": "elm",
    "eex": "embeddedtemplate",
    "heex": "embeddedtemplate",
    "erl": "erlang",
    "fennel": "fennel",
    "fnl": "fennel",
    "firrtl": "firrtl",
    "fish": "fish",
    "f": "fortran",
    "for": "fortran",
    "func": "func",
    "gd": "gdscript",
    "gitattributes": "gitattributes",
    "gitignore": "gitignore",
    "gleam": "gleam",
    "glsl": "glsl",
    "gn": "gn",
    "go": "go",
    "gomod": "gomod",
    "gosum": "gosum",
    "groovy": "groovy",
    "gst": "gstlaunch",
    "hack": "hack",
    "hare": "hare",
    "hs": "haskell",
    "haskell": "haskell",
    "hx": "haxe",
    "hcl": "hcl",
    "hlsl": "hlsl",
    "html": "html",
    "hyprlang": "hyprlang",
    "ispc": "ispc",
    "janet": "janet",
    "java": "java",
    "js": "javascript",
    "javascript": "javascript",
    "jsx": "javascript",
    "jsdoc": "jsdoc",
    "json": "json",
    "jsonnet": "jsonnet",
    "jl": "julia",
    "julia": "julia",
    "kconfig": "kconfig",
    "kdl": "kdl",
    "kt": "kotlin",
    "kotlin": "kotlin",
    "ld": "linkerscript",
    "ll": "llvm",
    "lua": "lua",
    "luadoc": "luadoc",
    "luap": "luap",
    "luau": "luau",
    "magik": "magik",
    "make": "make",
    "mk": "make",
    "md": "markdown",
    "markdown": "markdown",
    "markdown_inline": "markdown_inline",
    "matlab": "matlab",
    "m": "matlab",
    "mermaid": "mermaid",
    "meson": "meson",
    "ninja": "ninja",
    "nix": "nix",
    "nqc": "nqc",
    "m": "objc",
    "objc": "objc",
    "ml": "ocaml",
    "mli": "ocaml_interface",
    "odin": "odin",
    "org": "org",
    "pas": "pascal",
    "pem": "pem",
    "pl": "perl",
    "perl": "perl",
    "pgn": "pgn",
    "php": "php",
    "po": "po",
    "pony": "pony",
    "ps1": "powershell",
    "powershell": "powershell",
    "printf": "printf",
    "prisma": "prisma",
    "properties": "properties",
    "proto": "proto",
    "psv": "psv",
    "pp": "puppet",
    "purescript": "purescript",
    "purs": "purescript",
    "pymanifest": "pymanifest",
    "py": "python",
    "python": "python",
    "qmldir": "qmldir",
    "query": "query",
    "r": "r",
    "rkt": "racket",
    "rbs": "rbs",
    "re2c": "re2c",
    "readline": "readline",
    "requirements": "requirements",
    "ron": "ron",
    "rst": "rst",
    "rb": "ruby",
    "ruby": "ruby",
    "rs": "rust",
    "rust": "rust",
    "scala": "scala",
    "sc": "scala",
    "scm": "scheme",
    "scss": "scss",
    "slang": "slang",
    "smali": "smali",
    "smithy": "smithy",
    "sol": "solidity",
    "sparql": "sparql",
    "sql": "sql",
    "sq": "squirrel",
    "starlark": "starlark",
    "svelte": "svelte",
    "swift": "swift",
    "td": "tablegen",
    "tcl": "tcl",
    "test": "test",
    "thrift": "thrift",
    "toml": "toml",
    "tsv": "tsv",
    "twig": "twig",
    "ts": "typescript",
    "typescript": "typescript",
    "tsx": "typescript",
    "typ": "typst",
    "udev": "udev",
    "ungrammar": "ungrammar",
    "uxn": "uxntal",
    "v": "v",
    "verilog": "verilog",
    "vhd": "vhdl",
    "vhdl": "vhdl",
    "vim": "vim",
    "vue": "vue",
    "wgsl": "wgsl",
    "xcompose": "xcompose",
    "xml": "xml",
    "yaml": "yaml",
    "yml": "yaml",
    "yuck": "yuck",
    "zig": "zig",
}


def get_supported_language(code_type: str) -> Optional[str]:
    """Return the Tree-sitter language name for a given code type, if supported."""
    code_type = code_type.lstrip(".").lower()
    language_name = DYNAMIC_LANGUAGE_MAPPINGS.get(code_type)
    if not language_name:
        logger.debug(f"No language mapping for code type: {code_type}")
        return None
    try:
        get_language(language_name)
        return language_name
    except Exception as e:
        logger.debug(
            f"Language {language_name} not supported by tree-sitter-language-pack: {e}"
        )
        return None
    
def extract_code_metadata(code: str, code_type: str) -> Dict[str, any]:
    """Extracts metadata from the code using tree-sitter, including functions, classes, parameters, and docstrings."""
    metadata = {}

    try:
        if code_type not in LANGUAGE_MAPPINGS:
            logger.debug(f"No tree-sitter grammar available for code type: {code_type}")
            return metadata

        language_name = LANGUAGE_MAPPINGS[code_type]
        language = get_language(language_name)

        parser = Parser()
        parser.language = language
        tree = parser.parse(bytes(code, "utf8"))

        # Store metadata for functions and classes
        functions = []
        classes = []

        def extract_parameters(parameters_node):
            """Extract parameter names and types from a parameters node."""
            params = []
            for child in parameters_node.children:
                if child.type in ("identifier", "typed_parameter", "default_parameter"):
                    param_name = None
                    param_type = None
                    if child.type == "identifier":
                        param_name = child.text.decode()
                    elif child.type in ("typed_parameter", "default_parameter"):
                        for subchild in child.children:
                            if subchild.type == "identifier":
                                param_name = subchild.text.decode()
                            elif subchild.type == "type":
                                param_type = subchild.text.decode()
                    if param_name:
                        params.append({"name": param_name, "type": param_type})
            return params

        def extract_docstring(body_node):
            """Extract the docstring from a function or class body, if present."""
            for child in body_node.children:
                if child.type == "expression_statement":
                    string_node = child.child_by_field_name("expression")
                    if string_node and string_node.type in ("string", "string_literal"):
                        # Remove quotes from the docstring
                        docstring = string_node.text.decode().strip("'\"")
                        return docstring
            return None

        def traverse(node):
            if node.type == "function_definition":  # Python
                name_node = node.child_by_field_name("name")
                parameters_node = node.child_by_field_name("parameters")
                body_node = node.child_by_field_name("body")
                if name_node:
                    func_info = {"name": name_node.text.decode()}
                    # Extract parameters
                    if parameters_node:
                        func_info["parameters"] = extract_parameters(parameters_node)
                    else:
                        func_info["parameters"] = []
                    # Extract docstring
                    if body_node:
                        docstring = extract_docstring(body_node)
                        if docstring:
                            func_info["docstring"] = docstring
                    functions.append(func_info)
            elif node.type == "class_definition":  # Python
                name_node = node.child_by_field_name("name")
                body_node = node.child_by_field_name("body")
                if name_node:
                    class_info = {"name": name_node.text.decode()}
                    # Extract class docstring, if any
                    if body_node:
                        docstring = extract_docstring(body_node)
                        if docstring:
                            class_info["docstring"] = docstring
                    classes.append(class_info)
            elif node.type == "method_definition":  # JavaScript
                name_node = node.child_by_field_name("name")
                parameters_node = node.child_by_field_name("parameters")
                if name_node:
                    func_info = {"name": name_node.text.decode()}
                    # Extract parameters for JavaScript methods
                    if parameters_node:
                        func_info["parameters"] = extract_parameters(parameters_node)
                    else:
                        func_info["parameters"] = []
                    functions.append(func_info)

            for child in node.children:
                traverse(child)

        traverse(tree.root_node)

        metadata["functions"] = functions
        metadata["classes"] = classes
        metadata["tree_sitter_success"] = True
        logger.debug(f"Tree-sitter parsing successful for code type: {code_type}")

    except Exception as e:
        logger.error(f"Error parsing code with tree-sitter: {e}")
        metadata["tree_sitter_success"] = False

    return metadata
def usage_function():
    """
    Demonstrates basic usage of the extract_code_metadata function.
    It parses a sample Python code snippet and logs the extracted metadata as a JSON string.
    The sample code includes a function with parameters and a docstring.

    Example:
        >>> usage_function()
        {
            "functions": [
                {
                    "name": "my_function",
                    "parameters": [
                        {"name": "param1", "type": "int"},
                        {"name": "param2", "type": "str"}
                    ],
                    "docstring": "This is a docstring."
                }
            ],
            "classes": [],
            "tree_sitter_success": true
        }
    """
    sample_code = (
        "def my_function(param1: int, param2: str):\n"
        '    """This is a docstring."""\n'
        "    return param1 + len(param2)"
    )
    code_type = "python"

    logger.info("Running tree-sitter metadata extraction example...")
    try:
        metadata = extract_code_metadata(sample_code, code_type)
        if metadata["tree_sitter_success"]:
            logger.info("Extracted metadata from sample code:")
            json_output = json.dumps(metadata, indent=4)
            logger.info(f"\n{json_output}")
            logger.info("Metadata extraction example completed successfully.")
        else:
            logger.error("Failed to extract metadata from sample code.")
            raise RuntimeError("Metadata extraction failed.")
    except Exception as e:
        logger.error(f"Metadata extraction example failed: {e}")
        raise


if __name__ == "__main__":
    usage_function()