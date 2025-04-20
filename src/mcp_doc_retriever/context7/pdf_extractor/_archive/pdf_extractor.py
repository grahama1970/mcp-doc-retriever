import os
from pathlib import Path
from typing import List, Dict, Optional, Tuple
import json
import datetime
import tempfile

from marker.convert import convert_single_pdf
from marker.models import load_all_models
from pdf2image import convert_from_path
from markdown_it import MarkdownIt
from markdown_it.tree import SyntaxTreeNode
from loguru import logger
import tiktoken
import camelot
from transformers import AutoProcessor, AutoModelForCausalLM
import torch
from mcp_doc_retriever.context7.markdown_extractor import extract_from_markdown
from PIL import Image
import pytesseract
import numpy as np


class QwenVLLoader:
    """Lazy loader for Qwen-VL-2B model from HuggingFace."""

    def __init__(self):
        self.model = None
        self.processor = None
        self.device = "cuda" if torch.cuda.is_available() else "cpu"

    def load(self):
        """Loads Qwen-VL-2B model and processor if not already loaded."""
        if self.model is None or self.processor is None:
            logger.info("Loading Qwen-VL-2B model...")
            self.processor = AutoProcessor.from_pretrained("Qwen/Qwen-VL-Chat")
            self.model = AutoModelForCausalLM.from_pretrained(
                "Qwen/Qwen-VL-Chat",
                torch_dtype=torch.float16 if self.device == "cuda" else torch.float32,
            ).to(self.device)
            logger.info("Qwen-VL-2B model loaded successfully.")

    def process_image(self, image_path: str, prompt: str) -> str:
        """Processes an image with Qwen-VL-2B and returns Markdown output."""
        self.load()
        try:
            messages = [
                {
                    "role": "user",
                    "content": [
                        {"type": "image", "image": image_path},
                        {"type": "text", "text": prompt},
                    ],
                }
            ]
            text = self.processor.apply_chat_template(messages, tokenize=False)
            inputs = self.processor(
                text=text, images=[image_path], return_tensors="pt"
            ).to(self.device)
            generated_ids = self.model.generate(**inputs, max_new_tokens=512)
            generated_text = self.processor.batch_decode(
                generated_ids,
                skip_special_tokens=True,
                clean_up_tokenization_spaces=False,
            )[0]
            return generated_text
        except Exception as e:
            logger.error(f"Error processing image {image_path} with Qwen-VL-2B: {e}")
            return ""


def is_scanned_pdf(
    pdf_path: str, confidence_threshold: float = 0.7, text_threshold: int = 100
) -> bool:
    """Detects scanned PDFs using text length and OCR confidence."""
    try:
        from marker.pdf.text import get_length_of_text

        text_length = get_length_of_text(pdf_path)
        if text_length > text_threshold:
            return False  # Not scanned: sufficient selectable text

        # PDF is potentially scanned; perform OCR and check confidence
        images = convert_from_path(pdf_path, dpi=200, poppler_path="/opt/homebrew/bin")
        if not images:
            return True  # could not extract any images
        total_confidence = 0
        num_chars = 0
        for image in images:
            text = pytesseract.image_to_string(image)
            data = pytesseract.image_to_data(image, output_type=pytesseract.Output.DICT)

            # Calculate average confidence
            page_confidence = (
                sum(data["conf"]) / len(data["conf"]) if data["conf"] else 0
            )

            # calculate char confidences
            for i, t in enumerate(data["text"]):
                if t != "":
                    num_chars += 1
                    total_confidence += data["conf"][i]
        avg_char_confidence = total_confidence / num_chars if num_chars > 0 else 0

        return avg_char_confidence < confidence_threshold
    except Exception as e:
        logger.warning(f"Error checking if PDF is scanned: {e}. Assuming scanned.")
        return True


def extract_tables_with_camelot(pdf_path: str, pages: str = "1-end") -> List[Dict]:
    """Extracts tables using Camelot with dynamic tolerance adjustments."""
    try:
        row_tolerance = 5  # Default, tuneable values
        col_tolerance = 5

        tables = camelot.read_pdf(
            pdf_path,
            flavor="lattice",
            pages=pages,
            line_scale=40,
            row_tol=row_tolerance,
            col_tol=col_tolerance,
        )
        extracted_tables = []

        # Dynamic adjustment: (This should probably be an iterative procedure)
        if tables.n_explicit_tables == 0:
            logger.warning("No explicit tables found. Relaxing tolerance...")
            row_tolerance = 15  # Increase tolerance if no tables found
            col_tolerance = 15
            tables = camelot.read_pdf(
                pdf_path,
                flavor="lattice",
                pages=pages,
                line_scale=40,
                row_tol=row_tolerance,
                col_tol=col_tolerance,
            )  # Use relaxed tolerances
        for table in tables:
            extracted_tables.append(
                {
                    "type": "table",
                    "header": table.df.columns.tolist(),
                    "body": table.df.values.tolist(),
                    "page": table.page,
                    "parsing_report": table.parsing_report,
                    "confidence": table.parsing_report.get("accuracy", 0),
                }
            )

        merged_tables = merge_camelot_tables(extracted_tables)
        logger.info(
            f"Extracted {len(merged_tables)} tables with Camelot (after merging)"
        )
        return merged_tables

    except Exception as e:
        logger.warning(f"Error extracting tables with Camelot: {e}")
        return []


def merge_camelot_tables(tables: List[Dict]) -> List[Dict]:
    """Merges Camelot-extracted tables that span multiple pages based on column count and header similarity."""
    if not tables:
        return []

    merged_tables = []
    current_table = None

    for table in sorted(tables, key=lambda x: x.get("page", 1)):
        if not current_table:
            current_table = table
            continue

        current_header = current_table["header"]
        table_header = table["header"]
        if len(current_header) == len(table_header) and all(
            h1.lower() == h2.lower() for h1, h2 in zip(current_header, table_header)
        ):
            # Merge rows
            current_table["body"].extend(table["body"])
            current_table["page_range"] = (
                current_table.get(
                    "page_range", (current_table["page"], current_table["page"])
                )[0],
                table["page"],
            )
        else:
            merged_tables.append(current_table)
            current_table = table

    if current_table:
        merged_tables.append(current_table)

    # Update token counts
    encoding = tiktoken.encoding_for_model("gpt-4")
    for table in merged_tables:
        table["token_count"] = len(
            encoding.encode(
                json.dumps({"header": table["header"], "body": table["body"]})
            )
        )
        if "page_range" not in table:
            table["page_range"] = (table["page"], table["page"])

    return merged_tables


def extract_from_markdown_extended(file_path: str, repo_link: str) -> List[Dict]:
    """Extends markdown_extractor.py to handle tables and create a hierarchical JSON structure."""
    try:
        md = MarkdownIt("commonmark", {"html": False, "typographer": True})
        markdown_content = Path(file_path).read_text(encoding="utf-8")
        tokens = md.parse(markdown_content)
        tree = SyntaxTreeNode(tokens)

        encoding = tiktoken.encoding_for_model("gpt-4")
        extracted_data = []
        current_section = {"type": "root", "children": []}
        section_stack = [current_section]

        for node in tree.walk():
            if node.type == "heading":
                level = int(node.tag[1])
                text = node.children[0].content if node.children else ""
                token_count = len(encoding.encode(text))
                new_section = {
                    "type": "heading",
                    "level": level,
                    "text": text,
                    "token_count": token_count,
                    "children": [],
                }
                while len(section_stack) > level:
                    section_stack.pop()
                section_stack[-1]["children"].append(new_section)
                section_stack.append(new_section)

            elif node.type == "paragraph":
                text = node.children[0].content if node.children else ""
                token_count = len(encoding.encode(text))
                section_stack[-1]["children"].append(
                    {"type": "paragraph", "text": text, "token_count": token_count}
                )

            elif node.type == "table":
                header = [
                    th.children[0].content
                    for th in node.children[0].children[0].children
                ]
                body = [
                    [td.children[0].content for td in row.children]
                    for row in node.children[1].children
                ]
                token_count = len(
                    encoding.encode(json.dumps({"header": header, "body": body}))
                )
                section_stack[-1]["children"].append(
                    {
                        "type": "table",
                        "header": header,
                        "body": body,
                        "token_count": token_count,
                    }
                )

            def add_metadata(node, file_path, repo_link):
                node["file_path"] = file_path
                node["repo_link"] = repo_link
                node["extraction_date"] = datetime.datetime.now().isoformat()
                for child in node.get("children", []):
                    add_metadata(child, file_path, repo_link)

            add_metadata(current_section, file_path, repo_link)
        return current_section["children"]

    except Exception as e:
        logger.error(f"Error extracting from Markdown file {file_path}: {e}")
        return []


def convert_pdf_to_json(
    pdf_path: str,
    repo_link: str,
    output_dir: str = "output",
    use_markdown: bool = False,
) -> List[Dict]:
    """
    Converts a PDF to a hierarchical JSON list using Marker, with Qwen-VL-2B fallback for scanned PDFs
    and robust Camelot integration with adaptive tolerances.

    Args:
        pdf_path (str): Path to the PDF file.
        repo_link (str): Repository URL for metadata.
        output_dir (str): Directory for output files.
        use_markdown (bool): If True, outputs Markdown and parses with extract_from_markdown_extended.

    Returns:
        List[Dict]: Hierarchical JSON list of document elements.
    """
    try:
        os.makedirs(output_dir, exist_ok=True)
        output_json_path = os.path.join(output_dir, "structured.json")
        encoding = tiktoken.encoding_for_model("gpt-4")

        is_scanned = is_scanned_pdf(pdf_path)
        qwen_vl = QwenVLLoader()

        # Attempt Camelot extraction on all pages *first* if it's a text-based PDF
        camelot_tables: List[Dict] = []
        if not is_scanned:
            logger.info(
                "Attempting Camelot extraction with dynamic tolerance adjustments..."
            )
            camelot_tables = extract_tables_with_camelot(pdf_path, pages="1-end")

        # Process with Marker
        logger.info(f"Converting PDF {pdf_path} with Marker...")
        full_text, images, out_meta = convert_single_pdf(
            pdf_path,
            load_all_models(),
            use_llm=True,  # Enables multi-page table merging in Marker
            output_format="json" if not use_markdown else "markdown",
            force_ocr=is_scanned,
        )

        if use_markdown:
            # Save Markdown and parse with extended extractor
            markdown_path = os.path.join(output_dir, "output.md")
            with open(markdown_path, "w", encoding="utf-8") as f:
                f.write(full_text)
            extracted_data = extract_from_markdown_extended(markdown_path, repo_link)
        else:
            # Adapt Marker's JSON output and integrate/prioritize Camelot tables
            extracted_data: List[Dict] = []
            marker_tables: List[Dict] = []
            for item in full_text:
                adapted_item = {
                    "file_path": pdf_path,
                    "repo_link": repo_link,
                    "extraction_date": datetime.datetime.now().isoformat(),
                }

                if item["type"] == "heading":
                    adapted_item.update(
                        {
                            "type": "heading",
                            "level": item["level"],
                            "text": item["text"],
                            "token_count": len(encoding.encode(item["text"])),
                        }
                    )
                elif item["type"] == "paragraph":
                    adapted_item.update(
                        {
                            "type": "paragraph",
                            "text": item["text"],
                            "token_count": len(encoding.encode(item["text"])),
                        }
                    )
                elif item["type"] == "table":
                    item["page"] = item.get("page", 1)  # Ensure page number exists
                    marker_tables.append(item)
                    continue  # Process tables separately after initial extraction
                extracted_data.append(adapted_item)
            extracted_data.extend(
                process_tables(
                    marker_tables, camelot_tables, pdf_path, repo_link, encoding
                )
            )

        # Fallback to Qwen-VL-2B for scanned PDFs
        if is_scanned or not any(item["type"] == "table" for item in extracted_data):
            logger.info("Using Qwen-VL-2B for scanned PDF or missing tables...")
            with tempfile.TemporaryDirectory() as temp_dir:
                images = convert_from_path(pdf_path, output_folder=temp_dir)
                for i, image in enumerate(images):
                    image_path = os.path.join(temp_dir, f"qwen_page_{i}.png")
                    image.save(image_path)
                    prompt = "Extract all tables and text from this PDF page as Markdown, preserving table structure."
                    markdown_output = qwen_vl.process_image(image_path, prompt)
                    if markdown_output:
                        markdown_path = os.path.join(temp_dir, f"qwen_page_{i}.md")
                        with open(markdown_path, "w", encoding="utf-8") as f:
                            f.write(markdown_output)
                        qwen_data = extract_from_markdown_extended(
                            markdown_path, repo_link
                        )
                        extracted_data.extend(qwen_data)

        # Save final JSON
        with open(output_json_path, "w", encoding="utf-8") as f:
            json.dump(extracted_data, f, indent=2)
        logger.info(f"Output saved to {output_json_path}")

        return extracted_data

    except Exception as e:
        logger.error(f"Error converting PDF {pdf_path}: {e}")
        return []


def process_tables(marker_tables, camelot_tables, pdf_path, repo_link, encoding):
    # helper function to handle cases between marker and camelot
    extracted_tables = []

    def create_table_entry(table):
        return {
            "type": "table",
            "header": table["header"],
            "body": table["body"],
            "page_range": table.get("page_range", (table["page"], table["page"])),
            "token_count": table["token_count"],
            "file_path": pdf_path,
            "repo_link": repo_link,
            "extraction_date": datetime.datetime.now().isoformat(),
        }

    # Prioritize Camelot tables: create lookup to mark the tables and pages
    camelot_pages = set()
    for table in camelot_tables:
        camelot_pages.add(table["page"])
        extracted_tables.append(create_table_entry(table))

    # Append Marker tables *only* if Camelot didn't get that page
    for table in marker_tables:
        if table.get("page", 1) not in camelot_pages:
            table["page_range"] = (table.get("page", 1), table.get("page", 1))
            table["token_count"] = len(
                encoding.encode(
                    json.dumps({"header": table["header"], "body": table["body"]})
                )
            )
            extracted_tables.append(create_table_entry(table))

    return extracted_tables


def usage_example():
    """Demonstrates usage of the PDF-to-JSON converter."""
    pdf_path = "input.pdf"
    repo_link = "https://example.com"
    output_dir = "output"

    logger.info("Running PDF-to-JSON conversion example...")
    extracted_data = convert_pdf_to_json(
        pdf_path=pdf_path,
        repo_link=repo_link,
        output_dir=output_dir,
        use_markdown=False,  # Set to True to use Markdown parsing
    )

    if extracted_data:
        logger.info("Conversion successful. Sample output:")
        logger.info(json.dumps(extracted_data[:2], indent=4))
    else:
        logger.error("Conversion failed. No data extracted.")


if __name__ == "__main__":
    logger.info("Starting PDF-to-JSON conversion...")
    try:
        usage_example()
        logger.info("Conversion completed successfully.")
    except Exception as e:
        logger.error(f"Conversion failed: {e}")
