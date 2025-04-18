"""
Module: tree_sitter_utils.py
Description: This module provides utility functions for extracting code metadata using the tree-sitter library.
It defines functions to load tree-sitter languages and traverse syntax trees to extract functions, classes, and related information.

Third-party package documentation:
- tree_sitter: https://tree-sitter.github.io/tree-sitter/
- tree_sitter_language_pack: https://github.com/Goldziher/tree-sitter-language-pack

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
from typing import Optional, Dict, List
from loguru import logger
from tree_sitter import Parser, Language, Query
from tree_sitter_language_pack import get_language
from functools import lru_cache


# Mapping of code block types/file extensions to Tree-sitter language names
DYNAMIC_LANGUAGE_MAPPINGS = {
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


@lru_cache(maxsize=128)
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


@lru_cache(maxsize=128)
def get_cached_language(language_name: str) -> Language:
    """Cache and return a Tree-sitter Language object."""
    return get_language(language_name)


def extract_code_metadata(code: str, code_type: str) -> Dict[str, any]:
    """Extracts metadata from code using tree-sitter, including functions, classes, parameters, and docstrings."""
    metadata = {"functions": [], "classes": [], "tree_sitter_success": False}

    try:
        language_name = get_supported_language(code_type)
        if not language_name:
            logger.debug(f"No tree-sitter grammar available for code type: {code_type}")
            return metadata

        language = get_cached_language(language_name)
        parser = Parser()
        parser.set_language(language)
        tree = parser.parse(bytes(code, "utf-8"))

        # Language-specific node types for parsing
        LANGUAGE_NODE_TYPES = {
            "python": {
                "function": "function_definition",
                "class": "class_definition",
                "parameters": "parameters",
                "body": "block",
                "param_types": ["identifier", "typed_parameter", "default_parameter"],
                "docstring": ("expression_statement", "string"),
            },
            "javascript": {
                "function": ["function_declaration", "method_definition"],
                "class": "class_declaration",
                "parameters": "formal_parameters",
                "body": "statement_block",
                "param_types": ["identifier", "assignment_pattern"],
                "docstring": ("comment", None),
            },
            "typescript": {
                "function": ["function_declaration", "method_definition"],
                "class": "class_declaration",
                "parameters": "formal_parameters",
                "body": "statement_block",
                "param_types": ["required_parameter", "optional_parameter"],
                "docstring": ("comment", None),
            },
            "java": {
                "function": "method_declaration",
                "class": "class_declaration",
                "parameters": "formal_parameters",
                "body": "block",
                "param_types": ["formal_parameter"],
                "docstring": ("block_comment", None),
            },
            "cpp": {
                "function": "function_definition",
                "class": "class_specifier",
                "parameters": "parameter_list",
                "body": "compound_statement",
                "param_types": ["parameter_declaration"],
                "docstring": ("comment", None),
            },
            "go": {
                "function": "function_declaration",
                "class": None,
                "parameters": "parameter_list",
                "body": "block",
                "param_types": ["parameter_declaration"],
                "docstring": ("comment", None),
            },
            "ruby": {
                "function": "method",
                "class": "class",
                "parameters": "parameters",
                "body": "body_statement",
                "param_types": [
                    "identifier",
                    "optional_parameter",
                    "keyword_parameter",
                ],
                "docstring": ("comment", None),
            },
            "csharp": {
                "function": "method_declaration",
                "class": "class_declaration",
                "parameters": "parameter_list",
                "body": "block",
                "param_types": ["parameter"],
                "docstring": ("comment", None),
            },
            "actionscript": {
                "function": "function_declaration",
                "class": "class_declaration",
                "parameters": "formal_parameters",
                "body": "block",
                "param_types": ["parameter"],
                "docstring": ("block_comment", None),
            },
            "ada": {
                "function": "subprogram_specification",
                "class": None,
                "parameters": "parameter_specification",
                "body": "declarative_part",
                "param_types": ["parameter_specification"],
                "docstring": ("comment", None),
            },
            "clojure": {
                "function": "list",
                "class": None,
                "parameters": "vector",
                "body": "list",
                "param_types": ["symbol"],
                "docstring": ("string", None),
            },
            "kotlin": {
                "function": "function_declaration",
                "class": "class_declaration",
                "parameters": "function_value_parameters",
                "body": "function_body",
                "param_types": ["function_value_parameter"],
                "docstring": ("block_comment", None),
            },
            "swift": {
                "function": "function_declaration",
                "class": "class_declaration",
                "parameters": "parameter_clause",
                "body": "code_block",
                "param_types": ["parameter"],
                "docstring": ("line_comment", None),
            },
        }

        # Tree-sitter queries for supported languages
        QUERIES = {
            "python": """
                (function_definition
                    name: (identifier) @func_name
                    parameters: (parameters) @params
                    body: (block
                        (expression_statement
                            (string) @docstring)?))
                (class_definition
                    name: (identifier) @class_name
                    body: (block
                        (expression_statement
                            (string) @docstring)?))
            """,
            "javascript": """
                (function_declaration
                    name: (identifier) @func_name
                    parameters: (formal_parameters) @params
                    body: (statement_block)?) @func
                (method_definition
                    name: (property_identifier) @func_name
                    parameters: (formal_parameters) @params
                    body: (statement_block)?) @func
                (class_declaration
                    name: (identifier) @class_name
                    body: (class_body)?)
                (comment) @comment
            """,
        }

        node_types = LANGUAGE_NODE_TYPES.get(language_name, {})
        query = QUERIES.get(language_name)

        functions = []
        classes = []

        def extract_parameters(parameters_node, param_types):
            """Extract parameter names and types from a parameters node."""
            params = []
            for child in parameters_node.children:
                if child.type in param_types:
                    param_name = None
                    param_type = None
                    if child.type == "identifier":
                        param_name = child.text.decode("utf-8")
                    else:
                        for subchild in child.children:
                            if subchild.type == "identifier":
                                param_name = subchild.text.decode("utf-8")
                            elif subchild.type in (
                                "type",
                                "type_identifier",
                                "primitive_type",
                            ):
                                param_type = subchild.text.decode("utf-8")
                        if param_name:
                            params.append({"name": param_name, "type": param_type})
            return params

        def extract_docstring(body_node, docstring_type):
            """Extract docstring or comment from a body node."""
            if not docstring_type:
                return None
            if docstring_type[0] == "expression_statement":
                for child in body_node.children:
                    if child.type == docstring_type[0]:
                        string_node = child.children[0]  # Direct access to string node
                        if string_node and string_node.type == docstring_type[1]:
                            return string_node.text.decode("utf-8").strip("'\"")
            elif docstring_type[0] in ("comment", "block_comment", "line_comment"):
                for child in body_node.children:
                    if child.type == docstring_type[0]:
                        return (
                            child.text.decode("utf-8")
                            .strip()
                            .lstrip("/*//")
                            .rstrip("*/")
                            .strip()
                        )
            return None

        if query:
            # Use Tree-sitter query for extraction
            try:
                query_obj = language.query(query)
                captures = query_obj.captures(tree.root_node)
                logger.debug(
                    f"Captures for {language_name}: {[(node.type, tag) for node, tag in captures]}"
                )

                # Group captures by function/class
                current_func = None
                current_class = None
                for node, tag in captures:
                    if tag == "func_name":
                        current_func = {
                            "name": node.text.decode("utf-8"),
                            "parameters": [],
                        }
                        functions.append(current_func)
                    elif tag == "params" and current_func:
                        current_func["parameters"] = extract_parameters(
                            node, node_types.get("param_types", [])
                        )
                    elif tag == "docstring" and current_func:
                        current_func["docstring"] = node.text.decode("utf-8").strip(
                            "'\""
                        )
                    elif tag == "comment" and current_func:
                        # Check if comment precedes function
                        if node.prev_sibling and node.prev_sibling.type in (
                            "function_declaration",
                            "method_definition",
                        ):
                            current_func["docstring"] = (
                                node.text.decode("utf-8").strip().lstrip("//").strip()
                            )
                    elif tag == "class_name":
                        current_class = {"name": node.text.decode("utf-8")}
                        classes.append(current_class)
                    elif tag == "docstring" and current_class:
                        current_class["docstring"] = node.text.decode("utf-8").strip(
                            "'\""
                        )
                    elif tag == "comment" and current_class:
                        if (
                            node.prev_sibling
                            and node.prev_sibling.type == "class_declaration"
                        ):
                            current_class["docstring"] = (
                                node.text.decode("utf-8").strip().lstrip("//").strip()
                            )
            except Exception as e:
                logger.warning(
                    f"Query processing failed for {language_name}: {e}. Falling back to manual traversal."
                )
                query = None  # Trigger fallback

        if not query or not functions and not classes:
            # Manual traversal fallback
            def traverse(node):
                func_node_types = node_types.get("function", [])
                if isinstance(func_node_types, str):
                    func_node_types = [func_node_types]
                if node.type in func_node_types:
                    name_node = node.child_by_field_name("name")
                    parameters_node = node.child_by_field_name(
                        node_types.get("parameters")
                    )
                    body_node = node.child_by_field_name(node_types.get("body"))
                    if name_node:
                        func_info = {"name": name_node.text.decode("utf-8")}
                        if parameters_node:
                            func_info["parameters"] = extract_parameters(
                                parameters_node, node_types.get("param_types", [])
                            )
                        else:
                            func_info["parameters"] = []
                        if body_node and node_types.get("docstring"):
                            docstring = extract_docstring(
                                body_node, node_types["docstring"]
                            )
                            if docstring:
                                func_info["docstring"] = docstring
                        functions.append(func_info)
                elif node.type == node_types.get("class"):
                    name_node = node.child_by_field_name("name")
                    body_node = node.child_by_field_name(node_types.get("body"))
                    if name_node:
                        class_info = {"name": name_node.text.decode("utf-8")}
                        if body_node and node_types.get("docstring"):
                            docstring = extract_docstring(
                                body_node, node_types["docstring"]
                            )
                            if docstring:
                                class_info["docstring"] = docstring
                        classes.append(class_info)

                for child in node.children:
                    traverse(child)

            traverse(tree.root_node)

        metadata["functions"] = functions
        metadata["classes"] = classes
        metadata["tree_sitter_success"] = bool(functions or classes)
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
        logger.info("Extracted metadata from sample code:")
        json_output = json.dumps(metadata, indent=4)
        logger.info(f"\n{json_output}")
        if metadata["tree_sitter_success"]:
            logger.info("Metadata extraction example completed successfully.")
        else:
            logger.error("Failed to extract metadata from sample code.")
            raise RuntimeError("Metadata extraction failed.")
    except Exception as e:
        logger.error(f"Metadata extraction example failed: {e}")
        raise


def test_extraction():
    """
    Basic test function to verify extract_code_metadata functionality.
    Tests Python and JavaScript code snippets to ensure correct metadata extraction.
    """
    tests = [
        {
            "code": (
                "def my_function(param1: int, param2: str):\n"
                '    """This is a docstring."""\n'
                "    return param1 + len(param2)"
            ),
            "code_type": "python",
            "expected": {
                "functions": [
                    {
                        "name": "my_function",
                        "parameters": [
                            {"name": "param1", "type": "int"},
                            {"name": "param2", "type": "str"},
                        ],
                        "docstring": "This is a docstring.",
                    }
                ],
                "classes": [],
                "tree_sitter_success": True,
            },
        },
        {
            "code": (
                "// Fetch user data\n"
                "async function getUser(id) {\n"
                "    return await db.users.find(id);\n"
                "}"
            ),
            "code_type": "javascript",
            "expected": {
                "functions": [
                    {
                        "name": "getUser",
                        "parameters": [{"name": "id", "type": None}],
                        "docstring": "Fetch user data",
                    }
                ],
                "classes": [],
                "tree_sitter_success": True,
            },
        },
        {
            "code": (
                "@app.get('/')\n"
                "async def read_results():\n"
                "    results = await some_library()\n"
                "    return results"
            ),
            "code_type": "python",
            "expected": {
                "functions": [{"name": "read_results", "parameters": []}],
                "classes": [],
                "tree_sitter_success": True,
            },
        },
        {
            "code": (
                "async def get_burgers(number: int):\n"
                "    # Do some asynchronous stuff to create the burgers\n"
                "    return burgers"
            ),
            "code_type": "python",
            "expected": {
                "functions": [
                    {
                        "name": "get_burgers",
                        "parameters": [{"name": "number", "type": "int"}],
                    }
                ],
                "classes": [],
                "tree_sitter_success": True,
            },
        },
    ]

    logger.info("Running tree-sitter metadata extraction tests...")
    for i, test in enumerate(tests, 1):
        try:
            metadata = extract_code_metadata(test["code"], test["code_type"])
            if metadata == test["expected"]:
                logger.info(f"Test {i} ({test['code_type']}): Passed")
            else:
                logger.error(
                    f"Test {i} ({test['code_type']}): Failed. Expected {test['expected']}, got {metadata}"
                )
        except Exception as e:
            logger.error(f"Test {i} ({test['code_type']}): Failed with error: {e}")

    logger.info("Tests completed.")


if __name__ == "__main__":
    usage_function()
    test_extraction()
