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
    hex_dig = hash_object.hexdigest()
    return hex_dig


class SectionHierarchy:
    """
    Manages the section hierarchy as a stack, including hashes.
    """

    def __init__(self):
        """Initializes the SectionHierarchy with an empty stack."""
        self.hierarchy: List[str] = []
        self.hash_hierarchy: List[str] = []  # Store hashes of section titles

    def push(self, section_title: str, content: str):
        """Adds a section title and its hash to the hierarchy."""
        self.hierarchy.append(section_title)
        section_id = hash_string(section_title + content)
        self.hash_hierarchy.append(section_id)
        logger.debug(
            f"Pushed '{section_title}' with hash '{section_id}' to hierarchy: {self.hierarchy}"
        )

    def pop(self):
        """Removes the last section title and hash from the hierarchy."""
        if self.hierarchy:
            self.hierarchy.pop()
            self.hash_hierarchy.pop()
            logger.debug(f"Popped from hierarchy: {self.hierarchy}")

    def get_path(self) -> List[str]:
        """Returns the current section hierarchy path (titles)."""
        return self.hierarchy[:]

    def get_hash_path(self) -> List[str]:
        """Returns the current section hierarchy path (hashes)."""
        return self.hash_hierarchy[:]

    def __str__(self):
        """Returns a string representation of the hierarchy."""
        return " -> ".join(self.hierarchy)


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
        logger.debug(f"Input text length: {len(text)} characters")
        sections = self._split_by_sections(text)
        extracted_data: List[Dict] = []

        if sections:
            logger.info(f"Found {len(sections)} sections")
            for idx, (title, content, span) in enumerate(sections):
                logger.debug(
                    f"Processing section {idx}: title='{title}', span={span}, content_length={len(content)}"
                )
                # Push the current section onto the hierarchy BEFORE chunking
                self.section_hierarchy.push(title, content)
                extracted_data.extend(
                    self._chunk_section(
                        text, title, content, span, repo_link, file_path
                    )
                )
                # Pop the current section AFTER chunking
                self.section_hierarchy.pop()
        else:
            logger.warning("No sections found, using fallback chunking")
            extracted_data.extend(self._fallback_chunking(text, repo_link, file_path))

        logger.info(f"Generated {len(extracted_data)} chunks")
        return extracted_data

    def _split_by_sections(self, text: str) -> List[Tuple[str, str, Tuple[int, int]]]:
        """Splits the text into sections and returns the positions of each section."""
        logger.info("Splitting text into sections")
        # Regex to match markdown headers (##, ###) or numeric sections (1., 4.1, 4.1.1), enclosed in **
        section_pattern = re.compile(
            r"^\*\*(#+|\d+(?:\.\d+)*)\s+([^\n*]+?)\*\*\s*(?=\n{1,2}(?:\*\*(?:#+|\d+(?:\.\d+)*)\s+|\Z))",
            re.MULTILINE,
        )
        sections = []
        last_end = 0

        for match in section_pattern.finditer(text):
            prefix = match.group(1)  # e.g., "##" or "4.1.1"
            title = match.group(2).strip()  # e.g., "Design Basis Events (DBEs)"
            start = match.start()
            end = match.end()

            # Log section match details
            logger.debug(
                f"Matched section: prefix='{prefix}', title='{title}', start={start}, end={end}"
            )

            # Add previous content as a section without a title if it exists
            if start > last_end:
                content = text[last_end:start].strip()
                if content:
                    sections.append(("", content, (last_end, start)))
                    logger.debug(
                        f"Added untitled section: span=({last_end}, {start}), content_length={len(content)}"
                    )

            # Extract content until the next section or end of text
            content_start = end
            next_match = section_pattern.search(text, end)
            content_end = next_match.start() if next_match else len(text)
            content = text[content_start:content_end].strip()

            # Combine prefix and title for full section title
            full_title = f"{prefix} {title}".strip() if prefix else title
            if content or full_title:
                sections.append((full_title, content, (start, content_end)))
                logger.debug(
                    f"Added section: title='{full_title}', span=({start}, {content_end}), content_length={len(content)}"
                )

            last_end = content_end

        # Add any remaining content after the last section
        if last_end < len(text):
            content = text[last_end:].strip()
            if content:
                sections.append(("", content, (last_end, len(text))))
                logger.debug(
                    f"Added final untitled section: span=({last_end}, {len(text)}), content_length={len(content)}"
                )

        logger.info(f"Split text into {len(sections)} sections")
        return sections

    def _chunk_section(
        self,
        text: str,
        title: str,
        content: str,
        span: Tuple[int, int],
        repo_link: str,
        file_path: str,
    ) -> List[Dict]:
        """Chunks a single section into smaller pieces."""
        logger.info(f"Chunking section: title='{title}'")
        sentences = [sent.text.strip() for sent in self.nlp(content).sents]
        chunks = []
        current_chunk = ""
        current_token_count = 0
        start_line = span[0] + 1

        for sentence in sentences:
            sentence_token_count = len(self.encoding.encode(sentence))

            if current_token_count + sentence_token_count > self.max_tokens:
                if current_chunk:
                    code_id = hash_string(current_chunk)
                    end_line = start_line + current_chunk.count("\n")
                    # Get the section path and hash path from the hierarchy
                    section_path = self.section_hierarchy.get_path()
                    section_hash_path = self.section_hierarchy.get_hash_path()
                    logger.debug(
                        f"Creating chunk: section_id={code_id}, section_path={section_path}, section_hash_path={section_hash_path}"
                    )
                    chunks.append(
                        {
                            "file_path": file_path,
                            "repo_link": repo_link,
                            "extraction_date": datetime.datetime.now().isoformat(),
                            "code_line_span": (start_line, end_line),
                            "description_line_span": (start_line, end_line),
                            "code": current_chunk,
                            "code_type": "text",
                            "description": title,
                            "code_token_count": current_token_count,
                            "description_token_count": len(self.encoding.encode(title)),
                            "embedding_code": None,
                            "embedding_description": None,
                            "code_metadata": {},
                            "section_id": code_id,
                            "section_path": section_path,
                            "section_hash_path": section_hash_path,  # Added hash path
                        }
                    )
                    start_line = end_line + 1
                current_chunk = sentence + "\n"
                current_token_count = sentence_token_count
            else:
                current_chunk += sentence + "\n"
                current_token_count += sentence_token_count

        if current_chunk:
            code_id = hash_string(current_chunk)
            end_line = start_line + current_chunk.count("\n")
            # Get the section path and hash path from the hierarchy
            section_path = self.section_hierarchy.get_path()
            section_hash_path = self.section_hierarchy.get_hash_path()
            logger.debug(
                f"Creating last chunk: section_id={code_id}, section_path={section_path}, section_hash_path={section_hash_path}"
            )
            chunks.append(
                {
                    "file_path": file_path,
                    "repo_link": repo_link,
                    "extraction_date": datetime.datetime.now().isoformat(),
                    "code_line_span": (start_line, end_line),
                    "description_line_span": (start_line, end_line),
                    "code": current_chunk,
                    "code_type": "text",
                    "description": title,
                    "code_token_count": current_token_count,
                    "description_token_count": len(self.encoding.encode(title)),
                    "embedding_code": None,
                    "embedding_description": None,
                    "code_metadata": {},
                    "section_id": code_id,
                    "section_path": section_path,
                    "section_hash_path": section_hash_path,  # Added hash path
                }
            )

        logger.info(f"Chunked section '{title}' into {len(chunks)} chunks")
        return chunks

    def _fallback_chunking(
        self, text: str, repo_link: str, file_path: str
    ) -> List[Dict]:
        """Handles text without identifiable sections."""
        logger.info("Performing fallback chunking")
        try:
            from mcp_doc_retriever.context7.tree_sitter_utils import (
                extract_code_metadata,
            )
        except ImportError as e:
            logger.error(f"Failed to import tree_sitter_utils: {e}")
            raise

        encoding = tiktoken.encoding_for_model("gpt-4")

        code_metadata = {}
        code_token_count = len(encoding.encode(text))
        description_token_count = 0
        code_type = "text"
        code_start_line = 1
        code_end_line = 1 + text.count("\n")
        description = ""

        code_id = hash_string(text)
        description_line_span = (1, 1)
        # Get the section path and hash path from the hierarchy
        section_path = self.section_hierarchy.get_path()
        section_hash_path = self.section_hierarchy.get_hash_path()
        logger.debug(
            f"Fallback section path: {section_path}, section_hash_path={section_hash_path}"
        )

        extracted_data: List[Dict] = [
            {
                "file_path": file_path,
                "repo_link": repo_link,
                "extraction_date": datetime.datetime.now().isoformat(),
                "code_line_span": (code_start_line, code_end_line),
                "description_line_span": description_line_span,
                "code": text,
                "code_type": code_type,
                "description": description,
                "code_token_count": code_token_count,
                "description_token_count": description_token_count,
                "embedding_code": None,
                "embedding_description": None,
                "code_metadata": code_metadata,
                "section_id": code_id,
                "section_path": section_path,
                "section_hash_path": section_hash_path,  # Added hash path
            }
        ]
        logger.info(
            f"Created fallback chunk: section_id={code_id}, code_token_count={code_token_count}"
        )
        return extracted_data


def usage_function():
    """Demonstrates basic usage of the TextChunker class."""
    file_path = "src/mcp_doc_retriever/context7/data/nuclear_power.txt"
    repo_link = "https://github.com/username/repo/blob/main/nuclear_power.txt"
    logger.info(f"Starting usage_function with file: {file_path}")

    try:
        with open(file_path, "r", encoding="utf-8") as f:
            sample_text = f.read()
        logger.debug(f"Read {len(sample_text)} characters from {file_path}")
    except FileNotFoundError:
        logger.error(f"File not found: {file_path}")
        return

    chunker = TextChunker(max_tokens=500)
    chunks = chunker.chunk_text(sample_text, repo_link, file_path)

    logger.info("Generated text chunks:")
    json_output = json.dumps(chunks, indent=4)
    logger.info(json_output)
    # Verify the output
    assert len(chunks) > 0, "No chunks generated"
    assert "section_id" in chunks[0], "section_id missing in chunk"
    assert "section_path" in chunks[0], "section_path missing in chunk"
    logger.info("All assertions passed")


if __name__ == "__main__":
    logger.info("Running TextChunker usage example...")
    try:
        usage_function()
        logger.info("TextChunker usage example completed successfully.")
    except Exception as e:
        logger.error(f"TextChunker usage example failed: {e}")
