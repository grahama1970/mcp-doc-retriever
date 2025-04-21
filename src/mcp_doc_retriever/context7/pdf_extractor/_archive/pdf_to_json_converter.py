### src/mcp_doc_retriever/context7/pdf_extractor/pdf_to_json_converter_refactored.py

import os
import json
import datetime
import tempfile
import re
from pathlib import Path
from typing import List, Dict, Optional, Tuple, Any, Union
import sys # Import sys
import traceback  # For more detailed error logging

# Third-party imports (ensure these are installed)
import fitz  # PyMuPDF
import camelot
from pdf2image import convert_from_path
from PIL import Image
import pytesseract
from fuzzywuzzy import fuzz
from markdown_it import MarkdownIt
from markdown_it.tree import SyntaxTreeNode
from loguru import logger
import tiktoken
import torch
from transformers import AutoProcessor, AutoModelForCausalLM # type: ignore
from cleantext import clean

# Local imports (assuming they exist in the project structure)
from marker.convert import convert_single_pdf
from marker.models import load_all_models
from .. import config # ADDED: Import the new config module

# Assuming marker.pdf.text is relatively stable or accepting the risk
try:
    from marker.pdf.text import get_length_of_text
except ImportError:
    logger.warning(
        "Could not import get_length_of_text from marker. May fallback to OCR check more often."
    )
    get_length_of_text = None


# --- Helper Classes ---


class QwenVLLoader:
    """Lazy loader for Qwen-VL model from HuggingFace."""

    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(QwenVLLoader, cls).__new__(cls)
            cls._instance.model = None
            cls._instance.processor = None
            cls._instance.device = "cuda" if torch.cuda.is_available() else "cpu" # type: ignore
            logger.info(f"QwenVLLoader initialized. Device: {cls._instance.device}") # type: ignore
        return cls._instance

    def _load_model(self):
        """Loads Qwen-VL model and processor if not already loaded."""
        if self.model is None or self.processor is None:
            logger.info(f"Loading {config.QWEN_MODEL_NAME} model...")
            try:
                self.processor = AutoProcessor.from_pretrained(config.QWEN_MODEL_NAME)
                # Use bfloat16 if available on Ampere+ CUDA, float16 otherwise, float32 on CPU
                dtype = torch.float32
                if self.device == "cuda": # type: ignore
                    if torch.cuda.is_bf16_supported():
                        dtype = torch.bfloat16
                        logger.info("Using bfloat16 for Qwen model.")
                    else:
                        dtype = torch.float16
                        logger.info("Using float16 for Qwen model.")

                self.model = (
                    AutoModelForCausalLM.from_pretrained(
                        config.QWEN_MODEL_NAME,
                        torch_dtype=dtype,
                        trust_remote_code=True,  # Qwen requires this
                    )
                    .to(self.device) # type: ignore
                    .eval()
                )  # Set to evaluation mode
                logger.info(
                    f"{config.QWEN_MODEL_NAME} model loaded successfully to {self.device}." # type: ignore
                )
            except Exception as e:
                logger.error(f"Error loading Qwen model: {e}")
                logger.error(traceback.format_exc())
                raise

    def process_image(self, image_path: str, prompt: str) -> str:
        """Processes an image with Qwen-VL and returns Markdown output."""
        self._load_model()
        if self.model is None or self.processor is None:
            logger.error("Qwen model not loaded, cannot process image.")
            return ""

        logger.debug(f"Processing image {image_path} with Qwen-VL.")
        try:
            # Qwen-VL expects specific query format
            query = self.processor.from_list_format(
                [
                    {"image": image_path},
                    {"text": prompt},
                ]
            )

            with torch.no_grad():  # Ensure no gradients are calculated
                inputs = self.processor(query, return_tensors="pt").to(self.device) # type: ignore
                if hasattr(inputs, "input_ids"):
                    inputs_dtype = self.model.dtype if self.model else torch.float32
                    inputs = inputs.to(inputs_dtype)

                pred = self.model.generate(
                    **inputs, max_new_tokens=config.QWEN_MAX_NEW_TOKENS, do_sample=False
                )  # Use greedy decoding for consistency
                response = self.processor.decode(
                    pred.cpu()[0], skip_special_tokens=True
                )

            # Clean up response - models sometimes add introductory text
            # Look for the first markdown element (```, #, *, -, |)
            match = re.search(r"(```|#|\*|-|\|)", response)
            cleaned_response = response[match.start() :] if match else response
            logger.debug(f"Qwen-VL response received for {image_path}.")
            return cleaned_response

        except Exception as e:
            logger.error(f"Error processing image {image_path} with Qwen-VL: {e}")
            logger.error(traceback.format_exc())
            return ""


# --- Helper Functions ---


def _get_encoder() -> tiktoken.Encoding:
    """Gets the TikToken encoder."""
    return tiktoken.encoding_for_model(config.TIKTOKEN_ENCODING_MODEL)


def _normalize_text(text: Optional[str]) -> Optional[str]:
    """Cleans and normalizes a string."""
    if not text:
        return None
    try:
        # Basic cleaning
        cleaned = clean(
            text,
            no_line_breaks=True,  # Replaces line breaks with spaces
            no_html=True,  # Removes HTML tags
            normalize_whitespace=True,  # Squashes multiple spaces/tabs
            no_urls=True,  # Removes URLs
            no_emails=True,  # Removes email addresses
            no_punct=False,  # Keep punctuation
        )
        # Specific character replacements (example)
        cleaned = re.sub(r"[●•◦▪️]", "- ", cleaned).strip()  # Replace bullets with dash
        cleaned = re.sub(r"\\alpha|α", "alpha", cleaned, flags=re.IGNORECASE)
        # Ensure ends with punctuation if not empty and not already punctuated
        if cleaned and not re.search(r"[.!?]$", cleaned):
            cleaned += "."
        return cleaned if cleaned else None
    except Exception as e:
        logger.warning(
            f"Error during text normalization: {e}. Returning original text: '{text[:50]}...'"
        )
        return text  # Return original on error


def is_scanned_pdf(pdf_path: str) -> bool:
    """
    Detects if a PDF is likely scanned based on text content length and OCR confidence.
    Checks up to SCANNED_CHECK_MAX_PAGES.
    """
    logger.debug(f"Checking if '{pdf_path}' is scanned.")
    # 1. Check selectable text length using Marker's helper (if available)
    if get_length_of_text:
        try:
            text_length = get_length_of_text(pdf_path)
            # Heuristic: If significant text exists across the doc, likely not scanned
            # Adjust threshold based on expected document length/density if needed
            if text_length > (
                config.SCANNED_TEXT_LENGTH_THRESHOLD * 5
            ):  # Use a higher threshold for the whole doc check
                logger.info(
                    f"PDF has sufficient text ({text_length} chars). Assuming not scanned (based on Marker check)."
                )
                return False
        except Exception as e:
            logger.warning(
                f"Marker's get_length_of_text failed: {e}. Proceeding with OCR check."
            )
            # Fall through to OCR check

    # 2. Check OCR confidence on initial pages if text length is low or check failed
    logger.debug("Performing OCR confidence check on initial pages.")
    try:
        # Use try-except for pdf2image conversion, handle potential errors like missing Poppler
        try:
            # NOTE: Removed hardcoded poppler_path. Assumes poppler is in PATH.
            # User might need to install poppler-utils (Linux) or poppler (macOS/Windows)
            # and ensure it's accessible.
            images = convert_from_path(
                pdf_path, dpi=200, first_page=1, last_page=config.SCANNED_CHECK_MAX_PAGES
            )
        except Exception as e:  # Catching general exception from pdf2image
            logger.error(
                f"Failed to convert PDF pages to images (is Poppler installed and in PATH?): {e}"
            )
            logger.warning("Assuming PDF is scanned due to conversion error.")
            return True  # Assume scanned if we can't even convert pages

        if not images:
            logger.warning("No images extracted from PDF. Assuming scanned.")
            return True  # If no images, something is wrong, treat as scanned.

        total_confidence = 0.0
        num_chars = 0
        for i, image in enumerate(images):
            try:
                # Use image_to_data to get confidence per character/word
                data = pytesseract.image_to_data(
                    image, output_type=pytesseract.Output.DICT
                )
                page_conf = []
                page_chars = 0
                for j, text in enumerate(data["text"]):
                    conf_str = data["conf"][j]
                    if (
                        text.strip() and conf_str != "-1"
                    ):  # Ignore empty strings and non-confident entries
                        try:
                            confidence = float(conf_str)
                            if confidence >= 0:  # Ensure confidence is valid
                                page_conf.append(confidence)
                                page_chars += len(text)
                        except ValueError:
                            logger.warning(
                                f"Could not convert confidence '{conf_str}' to float on page {i + 1}."
                            )

                if page_chars > 0:
                    avg_page_conf = sum(page_conf) / len(page_conf) if page_conf else 0
                    logger.debug(
                        f"Page {i + 1}: Avg OCR confidence: {avg_page_conf:.2f} ({page_chars} chars)"
                    )
                    total_confidence += sum(page_conf)
                    num_chars += len(page_conf)  # Count confident characters
                else:
                    logger.debug(f"Page {i + 1}: No text detected by OCR.")

            except pytesseract.TesseractError as te:
                logger.error(
                    f"Tesseract error on page {i + 1}: {te}. Assuming low confidence for this page."
                )
                # Optionally, treat this page as having 0 confidence
            except Exception as e:
                logger.error(f"Error during OCR processing for page {i + 1}: {e}")
                logger.error(traceback.format_exc())
                # Optionally, treat this page as having 0 confidence

        # Calculate overall average confidence
        avg_char_confidence = (
            (total_confidence / num_chars * 1.0) if num_chars > 0 else 0.0
        )  # Ensure float division
        logger.info(
            f"Overall average OCR confidence across first {len(images)} pages: {avg_char_confidence:.2f}% ({num_chars} chars)"
        )

        is_scanned_result = avg_char_confidence < config.SCANNED_OCR_CONFIDENCE_THRESHOLD
        logger.info(
            f"Based on OCR confidence threshold ({config.SCANNED_OCR_CONFIDENCE_THRESHOLD}%), PDF is considered {'scanned' if is_scanned_result else 'not scanned'}."
        )
        return is_scanned_result

    except Exception as e:
        logger.error(f"Unexpected error during scanned PDF check: {e}")
        logger.error(traceback.format_exc())
        logger.warning("Assuming PDF is scanned due to an error during the check.")
        return True


def _assign_unique_table_ids(tables: List[Dict[str, Any]], source: str) -> List[Dict[str, Any]]:
    """Assigns unique IDs like 'camelot_p2_t0', 'marker_p5_t1'."""
    page_counters: Dict[int, int] = {}
    for table in tables:
        page = table.get("page", 0)  # Use 0 if page is missing
        if page not in page_counters:
            page_counters[page] = 0
        table_index = page_counters[page]
        table["table_id"] = f"{source}_p{page}_t{table_index}"
        page_counters[page] += 1
    return tables


def _run_camelot(
    pdf_path: str,
    pages: str = "1-end",
    table_areas: Optional[List[str]] = None,
    parameters: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    """Extracts tables using Camelot, assigns IDs, and handles basic errors."""
    logger.info(
        f"Running Camelot: pages='{pages}', areas={table_areas is not None}, params={parameters is not None}"
    )
    extracted_tables = []
    try:
        # Set default parameters, allow overrides
        params = {
            "flavor": config.CAMELOT_DEFAULT_FLAVOR,
            "line_scale": config.CAMELOT_LATTICE_LINE_SCALE,
            "edge_tol": config.CAMELOT_STREAM_EDGE_TOL,  # Used for stream fallback
        }
        if parameters:
            params.update(parameters)

        logger.debug(f"Camelot base parameters: {params}")
        tables = camelot.read_pdf(
            pdf_path,
            flavor=params["flavor"],
            line_scale=params.get("line_scale"),  # Only relevant for lattice
            table_areas=table_areas,
            pages=pages,
            # Add other relevant params from params dict if needed, e.g., edge_tol for stream
            **{
                k: v
                for k, v in params.items()
                if k not in ["flavor", "line_scale", "edge_tol"]
            },  # Pass extra params
        )
        logger.info(f"Camelot found {len(tables)} table(s) initially.")

        camelot_tables = []
        for i, table in enumerate(tables):
            accuracy = table.parsing_report.get("accuracy", 0.0)
            is_low_confidence = accuracy < config.LOW_CONFIDENCE_THRESHOLD
            needs_review = is_low_confidence

            # Attempt stream fallback for low confidence lattice tables if no specific areas were given
            if params["flavor"] == "lattice" and is_low_confidence and not table_areas:
                logger.warning(
                    f"Camelot table {i + 1} on page {table.page} has low accuracy ({accuracy:.2f}%) with lattice. Trying stream."
                )
                try:
                    stream_tables = camelot.read_pdf(
                        pdf_path,
                        flavor="stream",
                        edge_tol=params["edge_tol"],
                        pages=str(table.page),
                        # Add other stream-relevant params if needed
                    )
                    if stream_tables:
                        stream_table = stream_tables[
                            0
                        ]  # Assume first table found is the relevant one
                        stream_accuracy = stream_table.parsing_report.get(
                            "accuracy", 0.0
                        )
                        if stream_accuracy > accuracy:
                            logger.info(
                                f"Stream mode improved accuracy ({stream_accuracy:.2f}%) for table {i + 1} on page {table.page}. Using stream result."
                            )
                            table = stream_table  # Replace original table
                            accuracy = stream_accuracy
                            needs_review = accuracy < config.LOW_CONFIDENCE_THRESHOLD
                        else:
                            logger.info(
                                f"Stream mode did not improve accuracy ({stream_accuracy:.2f}%) for table {i + 1} on page {table.page}. Keeping lattice result."
                            )

                except Exception as e_stream:
                    logger.warning(
                        f"Camelot stream mode retry failed for page {table.page}: {e_stream}"
                    )

            # Ensure header is list of strings, handle potential non-string headers
            header = [str(col) for col in table.df.columns]
            # Ensure body is list of lists of strings/None
            body = [
                [str(cell) if cell is not None else None for cell in row]
                for row in table.df.values.tolist()
            ]

            camelot_tables.append(
                {
                    "type": "table",
                    "header": header,
                    "body": body,
                    "page": table.page,
                    "bbox": table._bbox,  # Use tuple directly
                    "parsing_report": table.parsing_report,
                    "accuracy": accuracy,
                    "needs_review": needs_review,
                    "source": "camelot",  # Add source
                }
            )

        extracted_tables = _assign_unique_table_ids(camelot_tables, "camelot")
        logger.info(
            f"Successfully processed {len(extracted_tables)} tables from Camelot."
        )

    except ImportError:
        logger.error(
            "Camelot prerequisites (like OpenCV or Ghostscript) might be missing."
        )
    except Exception as e:
        logger.error(f"Error during Camelot extraction: {e}")
        logger.error(traceback.format_exc())
        # Return empty list on failure
    return extracted_tables


def _run_marker(
    pdf_path: str, output_format: str = "json", force_ocr: bool = False
) -> Tuple[Union[List[Dict[str, Any]], str], Dict[str, Any]]:
    """Runs Marker PDF conversion, returns structured data or markdown string."""
    logger.info(
        f"Running Marker conversion: format='{output_format}', force_ocr={force_ocr}"
    )
    try:
        # Marker uses lazy loading internally via load_all_models
        model_lst = load_all_models()
        # Note: Marker's `convert_single_pdf` can be resource-intensive
        full_data, images, out_meta = convert_single_pdf(
            pdf_path,
            model_lst=model_lst,
            # max_pages=None, # Consider adding a limit for very large PDFs if needed
            # parallel_factor=1, # Adjust based on CPU cores if needed
            # langs=None, # Specify languages if known for better OCR
            output_format=output_format,  # "json" or "markdown"
            force_ocr=force_ocr,  # Use OCR engine even if text layer exists
        )
        logger.info(
            f"Marker conversion completed. Output type: {type(full_data)}. Metadata keys: {list(out_meta.keys())}"
        )
        return full_data, out_meta

    except Exception as e:
        logger.error(f"Error during Marker conversion: {e}")
        logger.error(traceback.format_exc())
        return [] if output_format == "json" else "", {}  # Return empty data on failure


def _parse_marker_markdown(
    markdown_content: str, pdf_path: str, repo_link: str
) -> List[Dict[str, Any]]:
    """Parses Markdown string (from Marker or Qwen) into a structured list."""
    logger.debug("Parsing Markdown content into structured data.")
    extracted_data = []
    encoding = _get_encoder()
    md = MarkdownIt("commonmark", {"html": False})  # Basic Markdown parser

    try:
        tokens = md.parse(markdown_content)
        tree = SyntaxTreeNode(tokens)

        current_heading_level = 0
        current_section_text = []  # Accumulate text under current heading

        for node in tree.children:  # Iterate through top-level nodes
            if node.type == "heading":
                # Finalize previous paragraph block if any
                if current_section_text:
                    text = " ".join(current_section_text).strip()
                    if text:
                        extracted_data.append(
                            {
                                "type": "paragraph",
                                "text": text,
                                "token_count": len(encoding.encode(text)),
                                "source": "marker_md",  # or qwen_md based on caller
                            }
                        )
                    current_section_text = []  # Reset text accumulator

                level = int(node.tag[1])
                text = "".join(
                    c.content for c in node.children if c.type == "text"
                ).strip()
                extracted_data.append(
                    {
                        "type": "heading",
                        "level": level,
                        "text": text,
                        "token_count": len(encoding.encode(text)),
                        "source": "marker_md",  # or qwen_md based on caller
                    }
                )
                current_heading_level = level

            elif node.type == "paragraph":
                # Append paragraph text to the accumulator for the current section
                text = "".join(
                    c.content for c in node.children if c.type == "text"
                ).strip()
                if text:
                    current_section_text.append(text)

            elif node.type == "fence" or node.type == "code_block":
                # Finalize previous paragraph block if any
                if current_section_text:
                    text = " ".join(current_section_text).strip()
                    if text:
                        extracted_data.append(
                            {
                                "type": "paragraph",
                                "text": text,
                                "token_count": len(encoding.encode(text)),
                                "source": "marker_md",
                            }
                        )
                    current_section_text = []

                code_content = node.content.strip()
                extracted_data.append(
                    {
                        "type": "code",
                        "language": node.info if node.type == "fence" else None,
                        "text": code_content,
                        "token_count": len(encoding.encode(code_content)),
                        "source": "marker_md",
                    }
                )

            elif node.type == "bullet_list" or node.type == "ordered_list":
                # Finalize previous paragraph block if any
                if current_section_text:
                    text = " ".join(current_section_text).strip()
                    if text:
                        extracted_data.append(
                            {
                                "type": "paragraph",
                                "text": text,
                                "token_count": len(encoding.encode(text)),
                                "source": "marker_md",
                            }
                        )
                    current_section_text = []

                list_items = []
                for item_node in node.children:  # list_item
                    # Get text content, handling potential inline elements like bold/italic
                    item_text = "".join(
                        n.content for n in item_node.walk() if n.type == "text"
                    ).strip()
                    if item_text:
                        list_items.append(item_text)

                if list_items:
                    extracted_data.append(
                        {
                            "type": "list",
                            "items": list_items,
                            "ordered": node.type == "ordered_list",
                            "token_count": len(encoding.encode("\n".join(list_items))),
                            "source": "marker_md",
                        }
                    )

            elif node.type == "table":
                # Finalize previous paragraph block if any
                if current_section_text:
                    text = " ".join(current_section_text).strip()
                    if text:
                        extracted_data.append(
                            {
                                "type": "paragraph",
                                "text": text,
                                "token_count": len(encoding.encode(text)),
                                "source": "marker_md",
                            }
                        )
                    current_section_text = []

                try:
                    header_row = node.children[0].children[0]  # thead > tr
                    header = [
                        "".join(
                            c.content for c in th.children if c.type == "text"
                        ).strip()
                        for th in header_row.children  # th
                    ]

                    body_rows = node.children[1].children  # tbody > tr*
                    body = [
                        [
                            "".join(
                                c.content for c in td.children if c.type == "text"
                            ).strip()
                            for td in row.children  # td
                        ]
                        for row in body_rows
                    ]

                    # Note: Page number and bbox are lost in Markdown conversion
                    # We can assign a placeholder or try to infer based on surrounding elements if needed later.
                    extracted_data.append(
                        {
                            "type": "table",
                            "header": header,
                            "body": body,
                            "page": 0,  # Placeholder - page info lost in MD
                            "bbox": None,  # Placeholder - bbox info lost in MD
                            "needs_review": True,  # MD tables often need review
                            "source": "marker_md",  # or qwen_md
                            # Assign ID later in the main flow after collecting all tables
                        }
                    )
                except (IndexError, AttributeError) as e_table:
                    logger.warning(
                        f"Could not parse Markdown table structure: {e_table}. Skipping table."
                    )
                    logger.debug(f"Problematic table node structure: {node}")

        # Add any remaining text from the last section
        if current_section_text:
            text = " ".join(current_section_text).strip()
            if text:
                extracted_data.append(
                    {
                        "type": "paragraph",
                        "text": text,
                        "token_count": len(encoding.encode(text)),
                        "source": "marker_md",
                    }
                )

        logger.info(f"Parsed {len(extracted_data)} elements from Markdown.")
        return extracted_data

    except Exception as e:
        logger.error(f"Error parsing Markdown content: {e}")
        logger.error(traceback.format_exc())
        return []


def _run_qwen_vl(pdf_path: str, repo_link: str) -> List[Dict[str, Any]]:
    """Processes PDF pages with Qwen-VL, parses results, returns structured data."""
    logger.info("Using Qwen-VL for PDF processing (likely scanned or fallback).")
    qwen_vl = QwenVLLoader()
    all_qwen_data = []
    num_pages = 0

    try:
        with fitz.open(pdf_path) as doc:
            num_pages = len(doc)
    except Exception as e:
        logger.error(f"Failed to open PDF with fitz to get page count: {e}")
        # Try getting page count from pdf2image conversion if fitz fails
        try:
            # Note: This is less efficient as it converts pages just for count
            images = convert_from_path(
                pdf_path, first_page=1, last_page=1
            )  # Check if at least one page exists
            if images:
                # Cannot get exact count easily here without full conversion or another lib
                logger.warning(
                    "Could not get exact page count, processing page by page."
                )
                pass  # Proceed without known total
            else:
                logger.error(
                    "Cannot determine page count and conversion seems problematic."
                )
                return []
        except Exception as e_conv:
            logger.error(f"Also failed getting page count via pdf2image: {e_conv}")
            return []

    with tempfile.TemporaryDirectory() as temp_dir:
        logger.info(f"Converting PDF to images for Qwen-VL in temp dir: {temp_dir}")
        try:
            # Process page by page to handle potentially large PDFs
            # Use pdf2image generator for memory efficiency if needed, but direct list is simpler here
            paths = convert_from_path(
                pdf_path, output_folder=temp_dir, fmt="png", dpi=200
            )  # High DPI often helps VLM
            logger.info(f"Converted {len(paths)} pages to images.")

            for i, img_path in enumerate(paths):
                page_num = i + 1
                logger.info(f"Processing page {page_num}/{len(paths)} with Qwen-VL...")
                try:
                    markdown_output = qwen_vl.process_image(img_path, config.QWEN_PROMPT)
                    if markdown_output:
                        logger.debug(
                            f"Qwen-VL output received for page {page_num}, parsing Markdown..."
                        )
                        # Parse the Markdown output from Qwen for this page
                        page_data = _parse_marker_markdown(
                            markdown_output, pdf_path, repo_link
                        )
                        # Add page number info (best guess)
                        for item in page_data:
                            item["page"] = page_num  # Assign page number
                            item["source"] = "qwen_md"  # Mark source
                        all_qwen_data.extend(page_data)
                    else:
                        logger.warning(
                            f"Qwen-VL returned no output for page {page_num}."
                        )

                except Exception as e_page:
                    logger.error(
                        f"Error processing page {page_num} image '{img_path}' with Qwen-VL or parsing its output: {e_page}"
                    )
                    logger.error(traceback.format_exc())
                finally:
                    # Clean up the image file immediately if possible (though temp_dir handles it)
                    # os.remove(img_path)
                    pass

        except Exception as e_conv_all:
            logger.error(
                f"Error during pdf2image conversion for Qwen-VL: {e_conv_all} (Is Poppler installed/accessible?)"
            )
            logger.error(traceback.format_exc())
            return []  # Cannot proceed if conversion fails

    logger.info(f"Qwen-VL processing finished. Found {len(all_qwen_data)} elements.")
    return all_qwen_data


def _load_corrections(pdf_path: str, corrections_dir: str) -> Dict[str, Any]:
    """Loads human corrections from a JSON file, if it exists."""
    pdf_filename = os.path.basename(pdf_path)
    pdf_id = os.path.splitext(pdf_filename)[0]
    corrections_path = (
        Path(corrections_dir) / f"{pdf_id}_corrections.json"
    )  # Standardized name
    logger.debug(f"Looking for corrections file: {corrections_path}")

    if corrections_path.exists():
        try:
            with open(corrections_path, "r", encoding="utf-8") as f:
                corrections_data = json.load(f)
                logger.info(f"Successfully loaded corrections from {corrections_path}")
                # Basic validation (optional)
                if not isinstance(corrections_data, dict):
                    logger.warning(
                        "Corrections file is not a valid JSON object. Ignoring."
                    )
                    return {}
                return corrections_data
        except json.JSONDecodeError as e:
            logger.error(
                f"Error decoding JSON from corrections file {corrections_path}: {e}"
            )
            return {}
        except Exception as e:
            logger.error(f"Error loading corrections file {corrections_path}: {e}")
            logger.error(traceback.format_exc())
            return {}
    else:
        logger.info(f"No corrections file found at {corrections_path}.")
        return {}


def _get_text_around_table(
    doc: fitz.Document,
    page_num: int,
    table_bbox: Optional[Tuple[float, float, float, float]],
    margin: float = config.TABLE_CONTEXT_MARGIN,
) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """
    Extracts normalized text above, below, and a potential title for a table, given its page (1-based) and bbox.
    Handles cases where bbox might be None (e.g., for merged tables).
    """
    if table_bbox is None:
        logger.debug(
            f"No bounding box provided for table on page {page_num}, cannot extract context."
        )
        return None, None, None
    if page_num <= 0 or page_num > len(doc):
        logger.warning(
            f"Invalid page number {page_num} provided for context extraction."
        )
        return None, None, None

    try:
        page = doc[page_num - 1]  # Fitz uses 0-based index
        blocks = page.get_text("blocks", sort=True)  # Sort blocks by vertical position
        if not blocks:
            return None, None, None

        above_texts = []
        below_texts = []
        table_title = None

        # Define common patterns for table titles (customize as needed)
        table_title_patterns = [
            re.compile(r"^\s*Table\s+\d+(\.\d+)*[:.]?\s+", re.IGNORECASE),
            # Add other patterns like "Exhibit", "Appendix Table", etc. if common
        ]

        # Iterate through blocks to find text relative to the table bbox
        # Blocks are (x0, y0, x1, y1, text, block_no, block_type)
        table_top_y = table_bbox[1]
        table_bottom_y = table_bbox[3]

        # Find text above the table
        relevant_above_blocks = []
        for block in blocks:
            block_bottom_y = block[3]
            if block_bottom_y < table_top_y - margin:
                relevant_above_blocks.append(block)
            elif block[1] >= table_top_y:  # Stop if block starts at or below table top
                break

        # Process relevant blocks above (take last N lines)
        for block in reversed(relevant_above_blocks[-config.TABLE_CONTEXT_MAX_LINES:]):
            block_text = block[4].strip()
            if block_text:
                above_texts.insert(0, block_text)  # Prepend to maintain order

        # Find potential title in the text immediately above
        if above_texts:
            last_above_line = above_texts[-1]
            for pattern in table_title_patterns:
                match = pattern.match(last_above_line)
                if match:
                    # Extract the full line as title if pattern matches start
                    table_title = last_above_line
                    logger.debug(f"Potential table title found: '{table_title}'")
                    # Optional: Remove title from above_text? Decided against for now.
                    break

        # Find text below the table
        relevant_below_blocks = []
        for block in blocks:
            block_top_y = block[1]
            if block_top_y > table_bottom_y + margin:
                relevant_below_blocks.append(block)

        # Process relevant blocks below (take first N lines)
        for block in relevant_below_blocks[:config.TABLE_CONTEXT_MAX_LINES]:
            block_text = block[4].strip()
            if block_text:
                below_texts.append(block_text)

        # Normalize extracted text
        norm_above = _normalize_text(" ".join(above_texts)) if above_texts else None
        norm_below = _normalize_text(" ".join(below_texts)) if below_texts else None
        norm_title = (
            _normalize_text(table_title) if table_title else None
        )  # Normalize found title

        return norm_above, norm_below, norm_title

    except Exception as e:
        logger.error(f"Error extracting text around table on page {page_num}: {e}")
        logger.error(traceback.format_exc())
        return None, None, None


def _merge_table_if_similar(
    current_table: Dict[str, Any], next_table: Dict[str, Any], similarity_threshold: int
) -> bool:
    """
    Checks if next_table header is similar to current_table header.
    If similar, merges next_table body into current_table and updates page_range.
    Returns True if merged, False otherwise.
    Modifies current_table in place.
    """
    if not current_table or not next_table:
        return False

    current_header = current_table.get("header", [])
    next_header = next_table.get("header", [])

    # Basic checks for merge possibility
    if not current_header or not next_header or len(current_header) != len(next_header):
        return False

    # Calculate header similarity
    # Use token_sort_ratio for robustness against word order changes
    header_similarity = fuzz.token_sort_ratio(
        " ".join(map(str, current_header)),  # Ensure elements are strings
        " ".join(map(str, next_header)),
    )
    logger.debug(
        f"Comparing headers for merge: Sim={header_similarity}%, Threshold={similarity_threshold}%. Table IDs: {current_table.get('table_id')} vs {next_table.get('table_id')}"
    )

    if header_similarity > similarity_threshold:
        logger.info(
            f"Merging table {next_table.get('table_id')} into {current_table.get('table_id')} (Similarity: {header_similarity}%)"
        )
        # Merge body
        current_table["body"].extend(next_table.get("body", []))

        # Update page range - assumes tables are sorted by page
        current_start_page = current_table.get(
            "page_range", (current_table.get("page"), current_table.get("page"))
        )[0]
        next_page = next_table.get("page")
        if next_page:
            current_table["page_range"] = (current_start_page, next_page)

        # Update needs_review flag (if either needs review, merged table does)
        current_table["needs_review"] = current_table.get(
            "needs_review", False
        ) or next_table.get("needs_review", False)

        # Bbox becomes invalid after merge
        current_table["bbox"] = None
        current_table["accuracy"] = None  # Accuracy is no longer well-defined

        # Update token count (approximate)
        encoding = _get_encoder()
        current_table["token_count"] = len(
            encoding.encode(
                json.dumps(
                    {"header": current_table["header"], "body": current_table["body"]}
                )
            )
        )

        return True  # Indicates merge happened

    return False


def _merge_tables(
    tables: List[Dict[str, Any]], merge_instructions: Dict[str, str], similarity_threshold: int
) -> List[Dict[str, Any]]:
    """
    Merges tables based on similarity or explicit human instructions.
    Tables should be pre-sorted by page number.
    merge_instructions: Dict mapping source_table_id to target_table_id.
    """
    if not tables:
        return []

    merged_tables = []
    processed_table_ids = set()
    table_map = {t["table_id"]: t for t in tables if "table_id" in t}

    # 1. Apply Human-Specified Merges
    logger.debug(f"Applying human merge instructions: {merge_instructions}")
    for source_id, target_id in merge_instructions.items():
        if source_id in processed_table_ids or target_id in processed_table_ids:
            logger.warning(
                f"Skipping merge instruction {source_id} -> {target_id} as one table is already processed."
            )
            continue

        if source_id in table_map and target_id in table_map:
            source_table = table_map[source_id]
            target_table = table_map[target_id]

            # Determine which table comes first (usually target is earlier)
            first_table, second_table = sorted(
                [source_table, target_table], key=lambda t: t.get("page", 0)
            )

            logger.info(
                f"Applying human merge instruction: {second_table['table_id']} into {first_table['table_id']}"
            )

            # Merge second table's body into the first
            first_table["body"].extend(second_table.get("body", []))

            # Update page range
            start_page = first_table.get(
                "page_range", (first_table.get("page", 0), first_table.get("page", 0))
            )[0]
            end_page = second_table.get(
                "page_range", (second_table.get("page", 0), second_table.get("page", 0))
            )[1]
            first_table["page_range"] = (
                start_page,
                max(start_page, end_page),
            )  # Ensure end page is not smaller

            # Mark as human merged, reset review flag potentially
            first_table["needs_review"] = False  # Assume human merge is correct
            first_table["source"] = "human_merged"  # Update source
            first_table["bbox"] = None  # Bbox invalid
            first_table["accuracy"] = None  # Accuracy invalid

            # Update token count
            encoding = _get_encoder()
            first_table["token_count"] = len(
                encoding.encode(
                    json.dumps(
                        {"header": first_table["header"], "body": first_table["body"]}
                    )
                )
            )

            # Add the merged table (the first one) to results later
            # Mark both as processed
            processed_table_ids.add(source_id)
            processed_table_ids.add(target_id)
            # We don't add to merged_tables yet, handle remaining tables first
        else:
            logger.warning(
                f"Could not apply merge instruction: {source_id} -> {target_id}. Table ID not found."
            )

    # 2. Apply Automated Merging for remaining tables
    logger.debug("Applying automated similarity-based merging.")
    remaining_tables = sorted(
        [t for t in tables if t.get("table_id") not in processed_table_ids],
        key=lambda x: x.get("page", 0),  # Sort by page
    )

    if not remaining_tables:
        # Add any tables that were targets of human merges but not sources
        for table_id, table in table_map.items():
            if (
                table_id in merge_instructions.values()
                and table_id not in processed_table_ids
            ):
                merged_tables.append(table)
                processed_table_ids.add(table_id)  # Mark as added
        return merged_tables

    current_table = remaining_tables[0]
    for i in range(1, len(remaining_tables)):
        next_table = remaining_tables[i]
        # Attempt merge only if pages are consecutive or same
        if next_table.get("page", 0) <= current_table.get("page", 0) + 1:
            was_merged = _merge_table_if_similar(
                current_table, next_table, similarity_threshold
            )
            if was_merged:
                processed_table_ids.add(
                    next_table["table_id"]
                )  # Mark next table as merged into current
                continue  # Continue with the expanded current_table

        # If not merged, finalize the current_table and start with next_table
        merged_tables.append(current_table)
        processed_table_ids.add(current_table["table_id"])
        current_table = next_table

    # Add the last processed table if it wasn't added already
    if current_table.get("table_id") not in processed_table_ids:
        merged_tables.append(current_table)
        processed_table_ids.add(current_table["table_id"])

    # Add any tables that were targets of human merges but haven't been added yet
    for table_id, table in table_map.items():
        if (
            table_id in merge_instructions.values()
            and table_id not in processed_table_ids
        ):
            merged_tables.append(table)
            processed_table_ids.add(table_id)

    # Final pass for page range and token counts if not set during merge
    encoding = _get_encoder()
    for table in merged_tables:
        if "page_range" not in table:
            page = table.get("page", 0)
            table["page_range"] = (page, page)
        if "token_count" not in table:
            table["token_count"] = len(
                encoding.encode(
                    json.dumps(
                        {
                            "header": table.get("header", []),
                            "body": table.get("body", []),
                        }
                    )
                )
            )

    logger.info(f"Finished merging tables. Result: {len(merged_tables)} final tables.")
    return merged_tables


def _process_and_prioritize_tables(
    all_tables: List[
        Dict[str, Any]
    ],  # List of table dicts from all sources (Camelot, Marker, Qwen)
    human_corrections: List[Dict[str, Any]],  # List of table dicts from corrections file
    pdf_path: str,
    repo_link: str,
    doc: fitz.Document,
) -> List[Dict[str, Any]]:
    """
    Prioritizes, merges, and adds context to tables based on source and human corrections.
    """
    logger.info(
        f"Processing and prioritizing {len(all_tables)} extracted tables against {len(human_corrections)} human corrections."
    )
    encoding = _get_encoder()
    final_tables = []
    processed_auto_table_ids = (
        set()
    )  # Keep track of automated tables handled by corrections
    human_merge_instructions = {}  # source_id -> target_id

    # 1. Process Human Corrections
    human_tables_map = {
        ht.get("table_id"): ht for ht in human_corrections if ht.get("table_id")
    }
    for correction in human_corrections:
        table_id = correction.get("table_id")
        status = correction.get("status", "unknown").lower()
        logger.debug(
            f"Processing human correction for table: {table_id}, status: {status}"
        )

        if not table_id:
            logger.warning(
                f"Human correction found without a 'table_id'. Skipping: {correction}"
            )
            continue

        # Mark the corresponding auto-extracted table as processed
        processed_auto_table_ids.add(table_id)

        # Handle different correction statuses
        if status in ["approved", "edited", "added"]:
            # Use the human-corrected version
            page = correction.get(
                "page", correction.get("page_range", [0])[0]
            )  # Get page number
            bbox = correction.get("bbox")  # Use corrected bbox if available
            above, below, title = _get_text_around_table(doc, page, bbox)

            final_table = {
                "type": "table",
                "table_id": table_id,
                "header": correction.get("header", []),
                "body": correction.get("body", []),
                "page": page,
                "page_range": correction.get("page_range", (page, page)),
                "bbox": bbox,  # Use corrected bbox
                "token_count": len(
                    encoding.encode(
                        json.dumps(
                            {
                                "header": correction.get("header", []),
                                "body": correction.get("body", []),
                            }
                        )
                    )
                ),
                "file_path": pdf_path,
                "repo_link": repo_link,
                "above_text": correction.get(
                    "above_text", above
                ),  # Prefer explicit correction text
                "below_text": correction.get("below_text", below),
                "title": correction.get("title", title),
                "extraction_date": datetime.datetime.now().isoformat(),
                "source": f"human_{status}",  # e.g., human_edited
                "needs_review": False,  # Assume corrected tables don't need review
                "human_comment": correction.get("comment"),  # Include comment if any
            }
            final_tables.append(final_table)

        elif status == "rejected":
            logger.info(
                f"Table {table_id} was rejected by human correction. Excluding."
            )
            # Do nothing, effectively removing the table

        elif status == "merge":
            merge_target_id = correction.get("merge_target_id")
            if merge_target_id:
                logger.info(
                    f"Registering human merge instruction: {table_id} -> {merge_target_id}"
                )
                human_merge_instructions[table_id] = merge_target_id
                # The actual merge happens later with all tables
            else:
                logger.warning(
                    f"Human correction has status 'merge' but missing 'merge_target_id' for table {table_id}."
                )

        elif status == "reextract":
            logger.info(
                f"Table {table_id} marked for re-extraction. It should have been handled by specific Camelot/Marker calls earlier."
            )
            # This status mainly influences the extraction step, not prioritization here.

        else:
            logger.warning(
                f"Unknown human correction status '{status}' for table {table_id}. Treating as needing review."
            )
            # Optionally, add the original table but mark for review
            # original_table = next((t for t in all_tables if t.get('table_id') == table_id), None)
            # if original_table:
            #     original_table['needs_review'] = True
            #     final_tables.append(original_table) # Decide if should be included

    # 2. Collect remaining automated tables
    remaining_auto_tables = [
        table
        for table in all_tables
        if table.get("table_id") not in processed_auto_table_ids
        and table.get("table_id")
        not in human_merge_instructions  # Don't process tables marked as sources for human merge here
    ]
    logger.debug(
        f"Collected {len(remaining_auto_tables)} remaining automated tables for merging and processing."
    )

    # 3. Merge tables (both human-instructed and automated)
    # Combine the remaining auto tables with the targets of human merges (if they exist in original data)
    tables_to_merge = remaining_auto_tables + [
        table
        for table_id, table in ((t.get("table_id"), t) for t in all_tables)
        if table_id in human_merge_instructions.values()
        and table_id not in processed_auto_table_ids
    ]

    # Deduplicate just in case
    unique_tables_to_merge = {t["table_id"]: t for t in tables_to_merge}.values()

    merged_auto_tables = _merge_tables(
        list(unique_tables_to_merge),
        human_merge_instructions,
        config.TABLE_MERGE_SIMILARITY_THRESHOLD,
    )

    # 4. Add context and finalize remaining tables
    for table in merged_auto_tables:
        # Skip if this table ID was already fully processed via human correction (e.g., edited/approved)
        # This check might be redundant given processed_auto_table_ids usage earlier, but safer.
        if table["table_id"] in [ft["table_id"] for ft in final_tables]:
            continue

        page = table.get("page", table.get("page_range", [0])[0])
        bbox = table.get("bbox")  # Might be None if merged
        above, below, title = _get_text_around_table(doc, page, bbox)

        final_table = {
            "type": "table",
            "table_id": table.get("table_id"),
            "header": table.get("header", []),
            "body": table.get("body", []),
            "page": page,
            "page_range": table.get("page_range", (page, page)),
            "bbox": bbox,
            "token_count": table.get("token_count"),  # Should be set during merge
            "file_path": pdf_path,
            "repo_link": repo_link,
            "above_text": above,
            "below_text": below,
            "title": title,
            "extraction_date": datetime.datetime.now().isoformat(),
            "source": table.get(
                "source", "unknown"
            ),  # Should be camelot, marker_json, marker_md, qwen_md, or human_merged
            "needs_review": table.get("needs_review", False),
            "accuracy": table.get(
                "accuracy"
            ),  # Include if available (e.g., from Camelot)
        }
        final_tables.append(final_table)

    # Sort final list by page number
    final_tables.sort(key=lambda x: x.get("page", 0))

    logger.info(
        f"Table processing complete. Returning {len(final_tables)} final tables."
    )
    return final_tables


# --- Main Conversion Function ---


def convert_pdf_to_json(
    pdf_path: str,
    repo_link: str,
    output_dir: str = config.DEFAULT_OUTPUT_DIR,
    use_marker_markdown: bool = False,
    corrections_dir: str = config.DEFAULT_CORRECTIONS_DIR,
    force_qwen: bool = False,  # Add flag to force Qwen-VL usage
) -> List[Dict[str, Any]]:
    """
    Converts a PDF to a hierarchical JSON list using combination of methods.

    Args:
        pdf_path: Path to the input PDF file.
        repo_link: URL link associated with the document repository.
        output_dir: Directory to save the final JSON output.
        use_marker_markdown: If True, use Marker's Markdown output as primary source
                             (still uses Camelot for potential table refinement).
                             If False (default), uses Marker's JSON output.
        corrections_dir: Directory containing human correction JSON files.
        force_qwen: If True, bypasses other methods and uses Qwen-VL directly.

    Returns:
        A list of dictionaries representing the structured content of the PDF.
    """
    logger.info(f"Starting conversion for PDF: {pdf_path}")
    logger.info(
        f"Options: use_marker_markdown={use_marker_markdown}, force_qwen={force_qwen}"
    )
    os.makedirs(output_dir, exist_ok=True)
    os.makedirs(corrections_dir, exist_ok=True)

    pdf_filename = os.path.basename(pdf_path)
    base_filename = os.path.splitext(pdf_filename)[0]
    output_json_path = Path(output_dir) / f"{base_filename}_structured.json"
    encoding = _get_encoder()

    all_extracted_elements = []
    all_extracted_tables = []  # Keep tables separate initially

    try:
        # 1. Load Corrections (early to inform extraction)
        corrections = _load_corrections(pdf_path, corrections_dir)
        human_tables_corrections = corrections.get("tables", [])
        # Identify pages/areas needing re-extraction based on corrections
        reextract_instructions = [
            table
            for table in human_tables_corrections
            if table.get("status") == "reextract"
        ]
        reextract_pages = sorted(
            list(
                set(
                    instr["page"] for instr in reextract_instructions if "page" in instr
                )
            )
        )
        reextract_pages_str = (
            ",".join(map(str, reextract_pages)) if reextract_pages else "1-end"
        )
        # TODO: Handle re-extraction with specific bounding boxes if provided in corrections

        # 2. Open PDF Document with Fitz
        with fitz.open(pdf_path) as doc:
            num_pages_doc = len(doc)
            logger.info(f"PDF opened successfully. Pages: {num_pages_doc}")

            # 3. Determine Extraction Strategy
            is_scanned = False  # Default assumption
            if force_qwen:
                logger.info("Qwen-VL processing forced by flag.")
                strategy = "qwen_only"
            else:
                is_scanned = is_scanned_pdf(pdf_path)
                if is_scanned:
                    logger.info("PDF detected as scanned. Primary strategy: Qwen-VL.")
                    strategy = "qwen_only"  # Treat scanned same as forced Qwen for simplicity now
                elif use_marker_markdown:
                    logger.info("Strategy: Marker (Markdown) + Camelot")
                    strategy = "marker_md_camelot"
                else:
                    logger.info("Strategy: Marker (JSON) + Camelot")
                    strategy = "marker_json_camelot"

            # 4. Execute Extraction based on Strategy
            marker_elements = []
            marker_tables = []
            camelot_tables = []
            qwen_elements = []

            if strategy == "qwen_only":
                qwen_elements = _run_qwen_vl(pdf_path, repo_link)
                # Separate tables from Qwen output
                all_extracted_tables.extend(
                    [el for el in qwen_elements if el["type"] == "table"]
                )
                all_extracted_elements.extend(
                    [el for el in qwen_elements if el["type"] != "table"]
                )
                # Assign IDs to Qwen tables
                all_extracted_tables = _assign_unique_table_ids(
                    all_extracted_tables, "qwen"
                )

            elif strategy == "marker_md_camelot":
                # Run Marker for Markdown
                markdown_content, _ = _run_marker(
                    pdf_path, output_format="markdown", force_ocr=False
                )  # Assume not scanned here
                if markdown_content:
                    # Ignore potential type mismatch if markdown_content is not str (should be handled by _run_marker return)
                    marker_elements = _parse_marker_markdown(
                        markdown_content, pdf_path, repo_link # type: ignore
                    )
                    # Separate tables from Marker Markdown output
                    marker_tables = [
                        el for el in marker_elements if el["type"] == "table"
                    ]
                    all_extracted_elements.extend(
                        [el for el in marker_elements if el["type"] != "table"]
                    )
                    # Assign IDs to Marker MD tables
                    all_extracted_tables.extend(
                        _assign_unique_table_ids(marker_tables, "marker_md")
                    )
                else:
                    logger.warning("Marker (Markdown) returned no content.")

                # Run Camelot (potentially informed by reextract flags)
                camelot_tables = _run_camelot(
                    pdf_path, pages=reextract_pages_str
                )  # Add specific area logic if needed
                all_extracted_tables.extend(
                    camelot_tables
                )  # Camelot assigns its own IDs

            elif strategy == "marker_json_camelot":
                # Run Marker for JSON
                marker_json_data, _ = _run_marker(
                    pdf_path, output_format="json", force_ocr=False
                )  # Assume not scanned
                if marker_json_data:
                    # Process Marker JSON output
                    temp_marker_tables = []
                    for item in marker_json_data: # type: ignore
                        element_type = item.get("type") # type: ignore
                        # Adapt Marker's JSON output to our standard format
                        adapted_item = { # type: ignore
                            "file_path": pdf_path,
                            "repo_link": repo_link,
                            "extraction_date": datetime.datetime.now().isoformat(),
                            "source": "marker_json",
                        }
                        if element_type == "heading":
                            adapted_item.update( # type: ignore
                                {
                                    "type": "heading",
                                    "level": item.get("level", 0), # type: ignore
                                    "text": item.get("text", ""), # type: ignore
                                    "token_count": len(
                                        encoding.encode(item.get("text", "")) # type: ignore
                                    ),
                                }
                            )
                            all_extracted_elements.append(adapted_item)
                        elif element_type == "paragraph":
                            adapted_item.update( # type: ignore
                                {
                                    "type": "paragraph",
                                    "text": item.get("text", ""), # type: ignore
                                    "token_count": len(
                                        encoding.encode(item.get("text", "")) # type: ignore
                                    ),
                                }
                            )
                            all_extracted_elements.append(adapted_item)
                        elif (
                            element_type == "list"
                        ):  # Assuming Marker outputs lists this way
                            adapted_item.update( # type: ignore
                                {
                                    "type": "list",
                                    "items": item.get("items", []), # type: ignore
                                    "ordered": item.get("ordered", False), # type: ignore
                                    "token_count": len(
                                        encoding.encode(
                                            "\n".join(item.get("items", [])) # type: ignore
                                        )
                                    ),
                                }
                            )
                            all_extracted_elements.append(adapted_item)
                        elif element_type == "table":
                            # Table needs more fields like bbox, page if available from Marker JSON
                            table_item = {
                                "type": "table",
                                "header": item.get("header", []), # type: ignore
                                "body": item.get( # type: ignore
                                    "rows", []
                                ),  # Assuming Marker JSON uses 'rows'
                                "page": item.get("page", 0), # type: ignore
                                "bbox": item.get( # type: ignore
                                    "bbox"
                                ),  # Assuming Marker JSON provides bbox
                                "needs_review": item.get( # type: ignore
                                    "needs_review", True
                                ),  # Check Marker's confidence if available
                                "source": "marker_json",
                            }
                            temp_marker_tables.append(table_item)
                        # Add handling for other types (code, image, etc.) if Marker provides them
                    all_extracted_tables.extend(
                        _assign_unique_table_ids(temp_marker_tables, "marker_json")
                    )
                else:
                    logger.warning("Marker (JSON) returned no content.")

                # Run Camelot
                camelot_tables = _run_camelot(pdf_path, pages=reextract_pages_str)
                all_extracted_tables.extend(camelot_tables)

            # Fallback: If no content extracted at all, try Qwen anyway?
            if (
                not all_extracted_elements
                and not all_extracted_tables
                and strategy != "qwen_only"
            ):
                logger.warning(
                    "Initial extraction methods yielded no content. Attempting Qwen-VL as fallback."
                )
                qwen_elements = _run_qwen_vl(pdf_path, repo_link)
                all_extracted_tables.extend(
                    [el for el in qwen_elements if el["type"] == "table"]
                )
                all_extracted_elements.extend(
                    [el for el in qwen_elements if el["type"] != "table"]
                )
                all_extracted_tables = _assign_unique_table_ids(
                    [
                        t for t in all_extracted_tables if "table_id" not in t
                    ],  # Only ID tables from Qwen run
                    "qwen_fallback",
                )

            # 5. Process and Prioritize Tables
            final_tables = _process_and_prioritize_tables(
                all_extracted_tables,
                human_tables_corrections,
                pdf_path,
                repo_link,
                doc,  # Pass the open fitz document
            )

            # 6. Combine non-table elements and final tables
            # TODO: Interleave tables correctly based on page/position if possible
            # For now, append tables at the end or sort all by page? Simple append for now.
            # A better approach would require page numbers for all elements and sorting.
            final_structured_data = all_extracted_elements + final_tables

            # Add metadata to non-table elements (tables get it in _process_and_prioritize_tables)
            for elem in all_extracted_elements:
                elem["file_path"] = pdf_path
                elem["repo_link"] = repo_link
                elem["extraction_date"] = datetime.datetime.now().isoformat()
                # Page info might be missing for some elements depending on source

            # Optional: Sort the final combined list by page number if available
            # Requires ensuring 'page' exists reliably in all elements
            try:
                final_structured_data.sort(key=lambda x: x.get("page", 0))
                logger.debug("Sorted final combined data by page number.")
            except Exception as sort_e:
                logger.warning(f"Could not sort final data by page number: {sort_e}")

        # 7. Save Final JSON
        logger.info(f"Saving final structured data to {output_json_path}")
        with open(output_json_path, "w", encoding="utf-8") as f:
            json.dump(final_structured_data, f, indent=2, ensure_ascii=False)

        logger.info(f"Conversion successful for {pdf_path}. Output saved.")
        return final_structured_data

    except Exception as e:
        logger.error(f"FATAL Error converting PDF {pdf_path}: {e}")
        logger.error(traceback.format_exc())
        return []  # Return empty list on critical failure


# --- Usage Example ---


def run_conversion(
    pdf_filepath: str,
    repo_link_url: str,
    output_directory: str = config.DEFAULT_OUTPUT_DIR,
    use_marker_md_format: bool = False,
    corrections_directory: str = config.DEFAULT_CORRECTIONS_DIR,
    force_qwen_processing: bool = False,
):
    """
    High-level function to run the PDF conversion process for a single file.

    Args:
        pdf_filepath: Path to the input PDF.
        repo_link_url: Repository link for metadata.
        output_directory: Directory for JSON output.
        use_marker_md_format: Whether to use Marker's Markdown output.
        corrections_directory: Directory for correction files.
        force_qwen_processing: Whether to force using Qwen-VL.
    """
    if not os.path.exists(pdf_filepath):
        logger.error(f"Input PDF not found: {pdf_filepath}")
        return

    logger.add(
        f"{output_directory}/conversion_{os.path.basename(pdf_filepath)}.log",
        rotation="10 MB",
    )  # Log to file per PDF

    logger.info(f"--- Starting conversion for: {pdf_filepath} ---")
    logger.info(f"Repository Link: {repo_link_url}")
    logger.info(f"Output Directory: {output_directory}")
    logger.info(f"Corrections Directory: {corrections_directory}")
    logger.info(f"Use Marker MD: {use_marker_md_format}")
    logger.info(f"Force Qwen: {force_qwen_processing}")

    start_time = datetime.datetime.now()

    extracted_data = convert_pdf_to_json(
        pdf_path=pdf_filepath,
        repo_link=repo_link_url,
        output_dir=output_directory,
        use_marker_markdown=use_marker_md_format,
        corrections_dir=corrections_directory,
        force_qwen=force_qwen_processing,
    )

    end_time = datetime.datetime.now()
    duration = end_time - start_time

    if extracted_data:
        logger.info(f"--- Conversion successful for: {pdf_filepath} ---")
        logger.info(f"Total elements extracted: {len(extracted_data)}")
        logger.info(f"Processing time: {duration}")
        # logger.info("Sample output (first 2 elements):")
        # logger.info(json.dumps(extracted_data[:2], indent=4))
    else:
        logger.error(f"--- Conversion failed for: {pdf_filepath} ---")
        logger.error(f"Processing time: {duration}")


if __name__ == "__main__":
    # --- Fix for standalone execution ---
    # Add the 'src' directory to sys.path to allow relative imports
    project_root = Path(__file__).resolve().parents[3] # Go up 3 levels (pdf_extractor -> context7 -> mcp_doc_retriever -> src)
    src_path = project_root / "src"
    if str(src_path) not in sys.path:
        sys.path.insert(0, str(src_path))
        logger.info(f"Added {src_path} to sys.path for standalone run.")
    # Re-import necessary modules after path modification if needed,
    # but imports at top level should work now.
    # ------------------------------------

    # --- Configuration for standalone run ---
    # Use a real PDF from the input directory relative to this script
    pdf_to_process = "input/BHT_CV32A65X.pdf"
    # Replace with the relevant repository link
    repository_url = "https://github.com/example/repo"
    # Set output and corrections directories
    output_location = "conversion_output" # Output will be relative to CWD
    corrections_location = "conversion_corrections" # Corrections dir relative to CWD

    # Set processing flags
    use_markdown = False  # Use Marker's JSON output (default)
    force_qwen = False  # Don't force Qwen unless necessary (e.g., scanned)

    # Ensure input PDF exists
    if not os.path.exists(pdf_to_process):
        logger.error(f"Input PDF not found: {pdf_to_process}. Please ensure it exists relative to the script.")
        exit(1)

    os.makedirs(output_location, exist_ok=True)
    os.makedirs(corrections_location, exist_ok=True)
    # Optional: Create a dummy corrections file
    # dummy_correction_file = os.path.join(corrections_location, f"{os.path.splitext(os.path.basename(pdf_to_process))[0]}_corrections.json")
    # if not os.path.exists(dummy_correction_file):
    #     with open(dummy_correction_file, "w") as f:
    #         json.dump({"tables": []}, f) # Empty corrections
    #     logger.info(f"Created dummy corrections file: {dummy_correction_file}")

    # --- Run the conversion ---
    logger.info("--- Starting PDF-to-JSON conversion Script ---")
    run_conversion(
        pdf_filepath=pdf_to_process,
        repo_link_url=repository_url,
        output_directory=output_location,
        use_marker_md_format=use_markdown,
        corrections_directory=corrections_location,
        force_qwen_processing=force_qwen,
    )
    logger.info("--- PDF-to-JSON conversion Script Finished ---")
