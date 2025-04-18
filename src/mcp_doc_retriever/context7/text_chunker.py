import os
from pathlib import Path
from typing import List, Tuple, Optional, Dict
import re
import tiktoken
import spacy
import datetime
import hashlib
import json
from loguru import logger


def hash_string(input_string: str) -> str:
    """Hashes a string using SHA256 and returns the hexadecimal representation."""
    encoded_string = input_string.encode("utf-8")
    hash_object = hashlib.sha256(encoded_string)
    return hash_object.hexdigest()


class SectionHierarchy:
    """
    Manages a stack representing the current section hierarchy path.
    """

    def __init__(self):
        # Stack stores tuples: (section_number_str, full_title_str, content_hash_str)
        self.stack: List[Tuple[str, str, str]] = []
        logger.debug("SectionHierarchy initialized.")

    def update(self, section_number: str, section_title: str, section_content: str):
        """
        Updates the hierarchy stack based on a newly encountered section.

        Args:
            section_number: The number string (e.g., "5", "5.2", "5.5.1").
            section_title: The title string (e.g., "Operational Safety Requirements").
            section_content: The content associated with this specific section (used for hashing).
        """
        logger.debug(
            f"Updating hierarchy with section: {section_number=}, {section_title=}"
        )
        # Parse the current section number into a list of ints
        try:
            nums = [int(n) for n in section_number.split(".") if n]
            current_level = len(nums)
            logger.debug(f"Parsed section number parts: {nums=}, {current_level=}")
        except ValueError:
            logger.warning(
                f"Could not parse section number '{section_number}'. Skipping hierarchy update."
            )
            return

        # Pop anything on the stack that isn't an ancestor of this one
        while self.stack:
            prev_number_str, _, _ = self.stack[-1]
            try:
                prev_nums = [int(n) for n in prev_number_str.split(".") if n]
                prev_level = len(prev_nums)
            except ValueError:
                # If somehow invalid, just pop it off
                logger.warning(f"Invalid number on stack '{prev_number_str}', popping.")
                self.stack.pop()
                continue

            # ancestor if prev_level < current_level and prefix matches
            is_ancestor = prev_level < current_level and nums[:prev_level] == prev_nums
            logger.debug(
                f"Is '{prev_number_str}' an ancestor of '{section_number}'? {is_ancestor}"
            )

            if not is_ancestor:
                logger.debug(f"Popping non-ancestor '{prev_number_str}'")
                self.stack.pop()
            else:
                # once we hit a true ancestor, stop popping
                break

        # Now append the current section
        full_title = f"{section_number} {section_title}".strip()
        section_hash = hash_string(full_title + section_content)
        self.stack.append((section_number, full_title, section_hash))
        logger.debug(
            f"Appended to stack: {section_number=}, {full_title=}, hash={section_hash}"
        )
        logger.debug(f"Current hierarchy stack: {[t for _, t, _ in self.stack]}")

    def get_titles(self) -> List[str]:
        """Return all titles in the current hierarchy (ancestors + current)."""
        titles = [title for (_, title, _) in self.stack]
        logger.debug(f"Getting titles: {titles}")
        return titles

    def get_hashes(self) -> List[str]:
        """Return all hashes in the current hierarchy (ancestors + current)."""
        hashes = [h for (_, _, h) in self.stack]
        logger.debug(f"Getting hashes: {hashes}")
        return hashes

    def __str__(self):
        return " -> ".join([title for _, title, _ in self.stack])


class TextChunker:
    """
    A class for chunking text files, preserving section titles, spans, and hierarchy.
    """

    def __init__(
        self,
        max_tokens: int = 500,
        encoding_name: str = "gpt-4",
        spacy_model: str = "en_core_web_sm",
    ):
        """Initializes the TextChunker."""
        self.max_tokens = max_tokens
        self.encoding = tiktoken.encoding_for_model(encoding_name)
        try:
            self.nlp = spacy.load(spacy_model)
        except OSError:
            logger.warning(f"SpaCy model '{spacy_model}' not found. Downloading...")
            spacy.cli.download(spacy_model)
            self.nlp = spacy.load(spacy_model)
        logger.info(
            f"Initialized TextChunker with max_tokens={max_tokens}, encoding={encoding_name}, spacy_model={spacy_model}"
        )
        self.section_hierarchy = SectionHierarchy()

    def chunk_text(self, text: str, repo_link: str, file_path: str) -> List[Dict]:
        """Chunks the text and returns a list of dictionaries."""
        logger.info(f"Starting chunk_text for file: {file_path}")
        sections = self._split_by_sections(text)
        extracted_data: List[Dict] = []

        if sections:
            logger.info(f"Found {len(sections)} sections")
            for idx, (sec_num, sec_title, span) in enumerate(sections):
                start, end = span
                section_content = text[start:end].strip()
                self.section_hierarchy.update(sec_num, sec_title, section_content)

                extracted_data.extend(
                    self._chunk_section(
                        text,
                        sec_num,
                        sec_title,
                        section_content,
                        span,
                        repo_link,
                        file_path,
                    )
                )
        else:
            logger.warning("No sections found, using fallback chunking")
            extracted_data.extend(self._fallback_chunking(text, repo_link, file_path))

        logger.info(f"Generated {len(extracted_data)} chunks")
        return extracted_data

    def _split_by_sections(self, text: str) -> List[Tuple[str, str, Tuple[int, int]]]:
        """
        Splits text on ONE‑LINE headers of the form:
          **4. General Design Requirements**
          **4 General Plant Design**
          **4.1 Plant Design Basis**
        i.e. no newline between '**' and the closing '**'.
        """
        logger.info("Splitting text into sections")
        section_pattern = re.compile(
            r"^"  # start of line
            r"\*\*"  # **
            r"(?P<number>\d+(?:\.\d+)*\.?)"  # 4 or 4. or 4.1 or 4.1.1.
            r"\s+"  # at least one space
            r"(?P<title>[A-Z0-9][^\n*]+?)"  # title (no newlines allowed)
            r"\s*\*\*"  # optional space, then closing **
            r"[ \t]*$",  # only whitespace until EOL
            re.MULTILINE,
        )

        sections: List[Tuple[str, str, Tuple[int, int]]] = []
        for m in section_pattern.finditer(text):
            raw = m.group("number")
            num = raw.rstrip(".")  # normalize “4.” → “4”
            title = m.group("title").strip()
            start, hdr_end = m.start(), m.end()

            nxt = section_pattern.search(text, hdr_end)
            end = nxt.start() if nxt else len(text)

            sections.append((num, title, (start, end)))

        # trailing “untitled” chunk if any
        if sections:
            last_end = sections[-1][2][1]
            if last_end < len(text) and text[last_end:].strip():
                sections.append(("", text[last_end:].strip(), (last_end, len(text))))
        else:
            # no headers → entire doc
            sections.append(("", text, (0, len(text))))

        logger.info(f"Split text into {len(sections)} sections")
        return sections

    def _chunk_section(
        self,
        text: str,
        section_number: str,
        section_title: str,
        section_content: str,
        span: Tuple[int, int],
        repo_link: str,
        file_path: str,
    ) -> List[Dict]:
        """Chunks a single section into smaller pieces based on token limit."""
        logger.info(f"Chunking section: {section_title!r}")
        sentences = [sent.text.strip() for sent in self.nlp(section_content).sents]
        chunks = []
        current_chunk = ""
        token_count = 0
        start_line = span[0] + 1

        for sent in sentences:
            sent_tokens = len(self.encoding.encode(sent))
            # if adding this sentence would exceed, flush the current chunk
            if token_count + sent_tokens > self.max_tokens and current_chunk:
                code_id = hash_string(current_chunk)
                end_line = start_line + current_chunk.count("\n")
                chunks.append(
                    {
                        "file_path": file_path,
                        "repo_link": repo_link,
                        "extraction_date": datetime.datetime.now().isoformat(),
                        "code_line_span": (start_line, end_line),
                        "description_line_span": (start_line, end_line),
                        "code": current_chunk,
                        "code_type": "text",
                        "description": section_title,
                        "code_token_count": token_count,
                        "description_token_count": len(
                            self.encoding.encode(section_title)
                        ),
                        "embedding_code": None,
                        "embedding_description": None,
                        "code_metadata": {},
                        "section_id": code_id,
                        "section_path": self.section_hierarchy.get_titles(),
                        "section_hash_path": self.section_hierarchy.get_hashes(),
                    }
                )
                start_line = end_line + 1
                current_chunk = ""
                token_count = 0

            current_chunk += sent + "\n"
            token_count += sent_tokens

        # flush any remaining
        if current_chunk:
            code_id = hash_string(current_chunk)
            end_line = start_line + current_chunk.count("\n")
            chunks.append(
                {
                    "file_path": file_path,
                    "repo_link": repo_link,
                    "extraction_date": datetime.datetime.now().isoformat(),
                    "code_line_span": (start_line, end_line),
                    "description_line_span": (start_line, end_line),
                    "code": current_chunk,
                    "code_type": "text",
                    "description": section_title,
                    "code_token_count": token_count,
                    "description_token_count": len(self.encoding.encode(section_title)),
                    "embedding_code": None,
                    "embedding_description": None,
                    "code_metadata": {},
                    "section_id": code_id,
                    "section_path": self.section_hierarchy.get_titles(),
                    "section_hash_path": self.section_hierarchy.get_hashes(),
                }
            )

        logger.info(f"Section {section_title!r} chunked into {len(chunks)} pieces")
        return chunks

    def _fallback_chunking(
        self, text: str, repo_link: str, file_path: str
    ) -> List[Dict]:
        """Handles text without identifiable sections."""
        logger.info("Performing fallback chunking")
        from mcp_doc_retriever.context7.tree_sitter_utils import extract_code_metadata

        encoding = tiktoken.encoding_for_model("gpt-4")
        code_token_count = len(encoding.encode(text))
        code_id = hash_string(text)
        code_start_line = 1
        code_end_line = text.count("\n") + 1

        data = {
            "file_path": file_path,
            "repo_link": repo_link,
            "extraction_date": datetime.datetime.now().isoformat(),
            "code_line_span": (code_start_line, code_end_line),
            "description_line_span": (1, 1),
            "code": text,
            "code_type": "text",
            "description": "",
            "code_token_count": code_token_count,
            "description_token_count": 0,
            "embedding_code": None,
            "embedding_description": None,
            "code_metadata": extract_code_metadata(text),
            "section_id": code_id,
            "section_path": self.section_hierarchy.get_titles(),
            "section_hash_path": self.section_hierarchy.get_hashes(),
        }
        logger.info(f"Created fallback chunk with section_id={code_id}")
        return [data]


def usage_function():
    import pyperclip

    """Demonstrates basic usage of the TextChunker class."""
    file_path = "src/mcp_doc_retriever/context7/data/nuclear_power.txt"
    repo_link = "https://github.com/username/repo/blob/main/nuclear_power.txt"

    logger.info(f"Starting usage_function with file: {file_path}")
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            sample_text = f.read()
    except FileNotFoundError:
        logger.error(f"File not found: {file_path}")
        return

    chunker = TextChunker(max_tokens=500)
    chunks = chunker.chunk_text(sample_text, repo_link, file_path)

    # log & simple sanity checks
    logger.info(f"Generated {len(chunks)} chunks")
    assert chunks, "No chunks generated"
    first = chunks[0]
    assert "section_id" in first and "section_path" in first, "Missing section metadata"
    logger.info("All assertions passed")

    logger.info(json.dumps(chunks[0:5], indent=2))


if __name__ == "__main__":
    logger.info("Running TextChunker usage example...")
    try:
        usage_function()
        logger.info("TextChunker usage example completed successfully.")
    except Exception as e:
        logger.error(f"TextChunker usage example failed: {e}")
