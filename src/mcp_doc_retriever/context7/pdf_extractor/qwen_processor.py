"""
Qwen-VL processing for scanned PDFs in the PDF extraction pipeline.

This module processes scanned PDFs using Qwen-VL for image-based extraction
and detects scanned PDFs using OCR confidence checks with pytesseract.

Dependencies:
- transformers: For Qwen-VL model.
- pytesseract: For OCR.
- pdf2image: For PDF-to-image conversion.
- loguru: For logging.
- torch: For model inference.
"""

import os
import json
import tempfile
from typing import List, Dict
import fitz
from loguru import logger
import torch
from transformers import AutoProcessor, AutoModelForCausalLM
from pdf2image import convert_from_path
import pytesseract
import re

from .config import (
    QWEN_MODEL_NAME,
    QWEN_MAX_NEW_TOKENS,
    QWEN_PROMPT,
    SCANNED_CHECK_MAX_PAGES,
    SCANNED_TEXT_LENGTH_THRESHOLD,
    SCANNED_OCR_CONFIDENCE_THRESHOLD,
)
from .marker_processor import parse_markdown
from .utils import _assign_unique_table_ids


class QwenVLLoader:
    """Lazy loader for Qwen-VL model."""

    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(QwenVLLoader, cls).__new__(cls)
            cls._instance.model = None
            cls._instance.processor = None
            cls._instance.device = "cuda" if torch.cuda.is_available() else "cpu"
        return cls._instance

    def _load_model(self):
        if self.model is None or self.processor is None:
            logger.info(f"Loading {QWEN_MODEL_NAME}...")
            self.processor = AutoProcessor.from_pretrained(QWEN_MODEL_NAME)
            dtype = (
                torch.float32
                if self.device == "cpu"
                else (
                    torch.bfloat16 if torch.cuda.is_bf16_supported() else torch.float16
                )
            )
            self.model = (
                AutoModelForCausalLM.from_pretrained(
                    QWEN_MODEL_NAME, torch_dtype=dtype, trust_remote_code=True
                )
                .to(self.device)
                .eval()
            )

    def process_image(self, image_path: str, prompt: str) -> str:
        self._load_model()
        if not self.model or not self.processor:
            return ""
        try:
            query = self.processor.from_list_format(
                [{"image": image_path}, {"text": prompt}]
            )
            with torch.no_grad():
                inputs = (
                    self.processor(query, return_tensors="pt")
                    .to(self.device)
                    .to(self.model.dtype)
                )
                pred = self.model.generate(
                    **inputs, max_new_tokens=QWEN_MAX_NEW_TOKENS, do_sample=False
                )
                response = self.processor.decode(
                    pred.cpu()[0], skip_special_tokens=True
                )
            match = re.search(r"(```|#|\*|-|\|)", response)
            return response[match.start() :] if match else response
        except Exception as e:
            logger.error(f"Qwen-VL processing failed: {e}")
            return ""


def is_scanned_pdf(pdf_path: str) -> bool:
    """Detects if a PDF is scanned based on OCR confidence."""
    logger.debug(f"Checking if '{pdf_path}' is scanned.")
    try:
        images = convert_from_path(
            pdf_path, dpi=200, first_page=1, last_page=SCANNED_CHECK_MAX_PAGES
        )
        total_confidence, num_chars = 0.0, 0
        for i, image in enumerate(images):
            data = pytesseract.image_to_data(image, output_type=pytesseract.Output.DICT)
            page_conf = []
            for j, text in enumerate(data["text"]):
                if text.strip() and data["conf"][j] != "-1":
                    try:
                        confidence = float(data["conf"][j])
                        if confidence >= 0:
                            page_conf.append(confidence)
                    except ValueError:
                        continue
            if page_conf:
                total_confidence += sum(page_conf)
                num_chars += len(page_conf)
        avg_confidence = total_confidence / num_chars if num_chars > 0 else 0.0
        return avg_confidence < SCANNED_OCR_CONFIDENCE_THRESHOLD
    except Exception as e:
        logger.error(f"Scanned PDF check failed: {e}")
        return True


def process_qwen(pdf_path: str, repo_link: str) -> List[Dict]:
    """Processes PDF with Qwen-VL."""
    qwen_vl = QwenVLLoader()
    elements = []

    try:
        with fitz.open(pdf_path) as doc:
            num_pages = len(doc)
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = convert_from_path(
                pdf_path, output_folder=temp_dir, fmt="png", dpi=200
            )
            for i, img_path in enumerate(paths):
                page_num = i + 1
                markdown = qwen_vl.process_image(img_path, QWEN_PROMPT)
                if markdown:
                    page_data = parse_markdown(markdown, pdf_path, repo_link)
                    for item in page_data:
                        item["page"] = page_num
                        item["source"] = "qwen_md"
                    elements.extend(page_data)
    except Exception as e:
        logger.error(f"Qwen-VL processing failed: {e}")

    tables = [e for e in elements if e["type"] == "table"]
    non_tables = [e for e in elements if e["type"] != "table"]
    tables = _assign_unique_table_ids(tables, "qwen")
    return tables + non_tables


def usage_function():
    """Demonstrates Qwen-VL processing."""
    return process_qwen("sample.pdf", "https://repo")


if __name__ == "__main__":
    result = usage_function()
    print("Qwen Processing Result:")
    print(json.dumps(result, indent=2))
