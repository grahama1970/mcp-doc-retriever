# src/mcp_doc_retriever/searcher/markdown_extractor.py
"""
Extracts content blocks (code, json, text) from Markdown using markdown-it-py.
"""

from typing import List, Optional, Tuple, Set  # Added Set
import re  # Added missing import for text cleaning
import json  # Added missing import
from textwrap import dedent

from loguru import logger
from markdown_it import MarkdownIt
from markdown_it.token import Token
# from markdown_it.utils import read_fixture_file # Not needed directly

# Use relative imports assuming standard package structure
try:
    from mcp_doc_retriever.models import ContentBlock

    # Import helpers needed within this module
    from mcp_doc_retriever.searcher.helpers import _is_json_like, _find_block_lines
except ImportError:
    # Fallback for potential standalone issues or if models/helpers moved
    logger.error(
        "Could not import ContentBlock or helpers for markdown_extractor.",
        exc_info=True,
    )

    # Define dummy classes/functions if necessary for basic loading
    class ContentBlock:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)

    def _is_json_like(text: str) -> bool:
        return False

    def _find_block_lines(
        block_text: str, source_lines: List[str], used_spans: set
    ) -> Tuple[Optional[int], Optional[int]]:
        return None, None


# Initialize markdown-it parser
# Using 'commonmark' preset for standard behavior.
# Could use 'gfm-like' if GitHub Flavored Markdown features are needed.
md_parser = MarkdownIt("commonmark")


def extract_content_blocks_with_markdown_it(
    md_content: str, source_url: Optional[str] = None
) -> List[ContentBlock]:
    """
    Extracts structured content blocks using markdown-it-py.

    Args:
        md_content: The Markdown content as a string.
        source_url: Optional source URL for metadata.

    Returns:
        A list of ContentBlock objects.
    """
    content_blocks: List[ContentBlock] = []
    if not md_content:
        return content_blocks

    source_lines = md_content.splitlines()
    used_line_spans: Set[Tuple[int, int]] = set()  # Corrected type hint

    try:
        logger.debug(f"Attempting md_parser.parse() for {source_url}") # Added log
        logger.debug(f"Attempting to parse Markdown content from {source_url}")
        tokens: List[Token] = md_parser.parse(md_content)
        # Log the tokens to understand what markdown-it-py generated
        logger.debug(f"Tokens generated by markdown-it-py for {source_url}:")
        for t in tokens:
            logger.debug(f"  Token: type={t.type}, tag={t.tag}, content='{t.content[:50]}...' if t.content else 'None'")
    except Exception as e:
        # Log the specific error during parsing
        logger.error(f"markdown-it-py parsing FAILED for {source_url}. Error: {e}", exc_info=True) # Corrected log
        return []  # Cannot proceed if parsing fails

    # NEW: Accumulate adjacent text tokens for merging.
    current_text_buffer = ""
    current_text_start_line: Optional[int] = None
    current_text_end_line: Optional[int] = None

    def flush_text_block():
        nonlocal \
            current_text_buffer, \
            current_text_start_line, \
            current_text_end_line, \
            content_blocks
        if current_text_buffer.strip():
            # Clean up markdown syntax in the accumulated text.
            cleaned_text = re.sub(
                r"\[([^\]]+)\]\([^\)]+\)", r"\1", current_text_buffer
            )  # Links -> text
            cleaned_text = re.sub(
                r"[*_]{1,2}([^*_]+)[*_]{1,2}", r"\1", cleaned_text
            )  # Bold/italic
            cleaned_text = re.sub(r"^[#]+\s+", "", cleaned_text).strip()
            # If available, use the accumulated boundaries; otherwise, fall back to _find_block_lines.
            final_start_line = current_text_start_line
            final_end_line = current_text_end_line
            if final_start_line is None or final_end_line is None:
                start_line, end_line = _find_block_lines(
                    cleaned_text, source_lines, used_line_spans
                )
                final_start_line = final_start_line or start_line
                final_end_line = final_end_line or end_line
            content_blocks.append(
                ContentBlock(
                    type="text",
                    content=cleaned_text,
                    block_type="paragraph",  # Approximate
                    start_line=final_start_line,
                    end_line=final_end_line,
                    source_url=source_url,
                    metadata={"selector": "text"},
                )
            )
        current_text_buffer = ""
        current_text_start_line = None
        current_text_end_line = None

    # Process tokens.
    for token in tokens:
        # --- Fenced Code Blocks ---
        if token.type == "fence":
            flush_text_block()  # End any text accumulation before processing a code block.
            lang = token.info.strip().lower() if token.info else None
            code_content = token.content.rstrip(
                "\n"
            )  # Remove trailing newline added by parser

            if not code_content:
                continue  # Skip empty blocks

            is_json = lang == "json" and _is_json_like(code_content)
            content_type = "json" if is_json else "code"
            final_language = "json" if is_json else lang

            # Get line numbers from token.map if available (token.map = [start_line, end_line])
            start_line_mdit, end_line_mdit = None, None
            if token.map:
                # token.map is 0-based, end line is exclusive, adjust to 1-based inclusive
                start_line_mdit = token.map[0] + 1
                # The end line in map points *after* the closing fence,
                # so the content ends on the line *before* that.
                end_line_mdit = token.map[1] - 1  # Adjust for content end

                # Basic check to ensure start isn't after end
                if end_line_mdit < start_line_mdit:
                    end_line_mdit = (
                        start_line_mdit  # Handle single-line blocks if map is weird
                    )

                # Mark span as used (adjusting to 0-based for internal tracking)
                span_to_add = (start_line_mdit - 1, end_line_mdit - 1)
                # Avoid adding if it overlaps significantly (though map should be reliable)
                is_overlapping = any(
                    max(s_start, span_to_add[0]) <= min(s_end, span_to_add[1])
                    for s_start, s_end in used_line_spans
                )
                if not is_overlapping:
                    used_line_spans.add(span_to_add)
                else:
                    logger.warning(
                        f"Detected overlap using markdown-it map for fence block at line {start_line_mdit}. Lines might be inaccurate."
                    )

            metadata = {"selector": f"fenced_{lang or 'code'}"}
            if is_json:
                try:
                    metadata["parsed_json"] = json.loads(code_content)
                except json.JSONDecodeError:
                    logger.warning(
                        f"MD block tagged 'json' failed parsing: {source_url} line {start_line_mdit}"
                    )
                    content_type, final_language = (
                        "code",
                        lang,
                    )  # Revert if parsing fails

            content_blocks.append(
                ContentBlock(
                    type=content_type,
                    content=code_content,
                    language=final_language,
                    block_type="fenced",
                    start_line=start_line_mdit,
                    end_line=end_line_mdit,
                    source_url=source_url,
                    metadata=metadata,
                )
            )
            logger.debug(
                f"-> Created CODE/JSON block: lang='{final_language}', lines={start_line_mdit}-{end_line_mdit}, content='{code_content[:30]}...'"
            )
            continue

        # --- Merge Consecutive Text Tokens ---
        # Consider tokens that open text containers as the beginning of a text region.
        if token.type in [
            "paragraph_open",
            "heading_open",
            "bullet_list_open",
            "ordered_list_open",
            "blockquote_open",
        ]:
            new_start = token.map[0] + 1 if token.map else None
            # If there's an existing text block, check if the gap (based on line numbers) is small enough to merge.
            if (
                current_text_buffer
                and current_text_end_line is not None
                and new_start is not None
            ):
                gap = (
                    new_start - current_text_end_line - 1
                )  # Number of blank lines between blocks.
                if gap >= 2:
                    flush_text_block()
            if not current_text_buffer:
                current_text_start_line = new_start
            continue

        # Accumulate inline content.
        if token.type == "inline" and token.content:
            current_text_buffer += token.content + " "
            if token.map:
                current_text_end_line = token.map[1]
            continue

        # On closing a text container, update the current text block boundary.
        if token.type in [
            "paragraph_close",
            "heading_close",
            "bullet_list_close",
            "ordered_list_close",
            "blockquote_close",
        ]:
            if token.map:
                current_text_end_line = token.map[1]
            continue

        # For any other token types (non-text elements), flush any accumulated text.
        flush_text_block()

    # Flush any trailing accumulated text.
    flush_text_block()

    logger.debug(
        f"Extracted {len(content_blocks)} blocks using markdown-it from {source_url}"
    )
    # Sort all blocks by starting line number
    content_blocks.sort(
        key=lambda b: b.start_line if b.start_line is not None else float("inf")
    )
    return content_blocks


# Example usage for standalone testing
if __name__ == "__main__":
    import sys
    import re  # Import re for cleaning in main block
    from pathlib import Path

    # Setup path for potential imports if run directly
    project_root_dir = Path(__file__).resolve().parent.parent.parent
    src_dir = project_root_dir / "src"
    if str(src_dir) not in sys.path:
        sys.path.insert(0, str(src_dir))
        print(f"DEBUG: Added {src_dir} to sys.path for standalone execution.")

    # Re-import ContentBlock here to ensure it's available after path setup
    try:
        from mcp_doc_retriever.models import ContentBlock
        from mcp_doc_retriever.searcher.helpers import (
            _is_json_like,
            _find_block_lines,
        )  # Import needed helpers
    except ImportError:
        print("ERROR: Failed to import ContentBlock or helpers even after path setup.")

        # Define dummy again if needed
        class ContentBlock:
            def __init__(self, **kwargs):
                self.__dict__.update(kwargs)

        def _is_json_like(text: str) -> bool:
            return False

        def _find_block_lines(
            block_text: str, source_lines: List[str], used_spans: set
        ) -> Tuple[Optional[int], Optional[int]]:
            return None, None

    # Remove all default Loguru handlers, then add one for stderr at DEBUG level
    logger.remove()
    logger.add(sys.stderr, level="DEBUG")
    logger.info("Testing markdown_extractor standalone...")

    sample_md_content = dedent(
        """
        # Header

        This is the first paragraph.
        It has two lines.

        ```python
        # This is python code
        def example():
            return "hello"
        ```

        Another paragraph here.

        ```json
        {
            "name": "markdown-it test",
            "version": 1.0,
            "valid": true
        }
        ```

        Final text block.
        """
    )

    print("\n--- Input Markdown ---")
    print(sample_md_content)
    print("--------------------")

    extracted = extract_content_blocks_with_markdown_it(
        sample_md_content, "file:///test.md"
    )

    print("\n--- Extracted Blocks ---")
    if not extracted:
        print("No blocks extracted.")
    for i, block in enumerate(extracted):
        print(f"\nBlock {i + 1}:")
        print(f"  Type: {getattr(block, 'type', 'N/A')}")
        print(f"  Language: {getattr(block, 'language', 'N/A')}")
        print(f"  Block Type: {getattr(block, 'block_type', 'N/A')}")
        print(
            f"  Lines: {getattr(block, 'start_line', 'N/A')} - {getattr(block, 'end_line', 'N/A')}"
        )
        print(f"  Content Preview: {getattr(block, 'content', '')[:80]}...")
        print(f"  Metadata: {getattr(block, 'metadata', {})}")

    print("\n--- Standalone Test Finished ---")

    # Basic assertions for the sample
    # Expect the header and the first paragraph to be merged, then a code block, then a merged text block for "Another paragraph here." and "Final text block.", then the JSON block.
    # Depending on tokenization, you might expect 5 merged blocks.
    assert len(extracted) == 5, f"Expected 5 blocks, got {len(extracted)}"
    assert extracted[0].type == "text" and "Header" in extracted[0].content
    assert extracted[0].type == "text" and "first paragraph" in extracted[0].content
    assert extracted[1].type == "code" and extracted[1].language == "python"
    # The merged text block for the remaining text should include both "Another paragraph here." and "Final text block."
    assert extracted[2].type == "text" and "Another paragraph" in extracted[2].content
    assert extracted[3].type == "json" and extracted[3].language == "json"
    print("\n✓ Basic assertions passed.")
