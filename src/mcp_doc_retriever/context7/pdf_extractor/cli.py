"""
Typer CLI for local PDF-to-JSON conversion.

This module provides a command-line interface to convert PDF files to structured
JSON, mirroring the FastAPI `/convert` endpoint. It integrates with the
`pdf_converter` module for core conversion logic and is designed for local
deployment, complementing the FastAPI server used in Docker containers.

Dependencies:
- typer: For building the CLI.
- loguru: For logging.

Usage:
    Convert a PDF:
    ```bash
    python cli.py convert sample.pdf \
        --repo-link https://github.com/example/repo \
        --output-dir output \
        --corrections-dir corrections
    ```

    With optional flags:
    ```bash
    python cli.py convert sample.pdf \
        --repo-link https://github.com/example/repo \
        --use-marker-markdown \
        --force-qwen
    ```
"""

import json
from pathlib import Path
from typing import List, Dict, Any
import typer
from loguru import logger
import os

from .config import DEFAULT_OUTPUT_DIR, DEFAULT_CORRECTIONS_DIR
from ._archive.pdf_to_json_converter import convert_pdf_to_json
from .utils import fix_sys_path

app = typer.Typer(
    name="pdf-extractor",
    help="CLI for converting PDFs to structured JSON, mirroring the FastAPI server.",
)


@app.command()
def convert(
    pdf_path: str = typer.Argument(..., help="Path to the PDF file to convert."),
    repo_link: str = typer.Option(
        "https://github.com/example/repo", help="Repository link for metadata."
    ),
    use_marker_markdown: bool = typer.Option(
        False, "--use-marker-markdown", help="Use Marker's Markdown output."
    ),
    force_qwen: bool = typer.Option(
        False, "--force-qwen", help="Force Qwen-VL processing."
    ),
    output_dir: str = typer.Option(
        DEFAULT_OUTPUT_DIR, help="Directory for JSON output."
    ),
    corrections_dir: str = typer.Option(
        DEFAULT_CORRECTIONS_DIR, help="Directory for correction files."
    ),
):
    """
    Converts a PDF to structured JSON, mirroring the FastAPI /convert endpoint.

    Args:
        pdf_path: Path to the PDF file.
        repo_link: Repository link for metadata.
        use_marker_markdown: Use Marker's Markdown output if True.
        force_qwen: Force Qwen-VL processing if True.
        output_dir: Directory for JSON output.
        corrections_dir: Directory for correction files.
    """
    pdf_path_obj = Path(pdf_path).resolve()
    if not pdf_path_obj.is_file() or not pdf_path_obj.suffix.lower() == ".pdf":
        logger.error(f"Invalid PDF file: {pdf_path_obj}")
        typer.echo(f"Error: '{pdf_path_obj}' is not a valid PDF file.")
        raise typer.Exit(code=1)

    # Check directory permissions
    for dir_path in [output_dir, corrections_dir]:
        dir_obj = Path(dir_path)
        try:
            dir_obj.mkdir(parents=True, exist_ok=True)
            if not os.access(dir_obj, os.W_OK):
                raise PermissionError(f"Write permission denied for {dir_path}")
        except Exception as e:
            logger.error(f"Failed to access directory {dir_path}: {e}")
            typer.echo(f"Error: Directory access failed: {str(e)}")
            raise typer.Exit(code=1)

    logger.info(f"Converting PDF: {pdf_path_obj}, repo_link: {repo_link}")
    try:
        result = convert_pdf_to_json(
            pdf_path=str(pdf_path_obj),
            repo_link=repo_link,
            output_dir=output_dir,
            use_marker_markdown=use_marker_markdown,
            corrections_dir=corrections_dir,
            force_qwen=force_qwen,
        )
        output_json = Path(output_dir) / f"{pdf_path_obj.stem}_structured.json"
        with open(output_json, "w", encoding="utf-8") as f:
            json.dump(result, f, indent=2, ensure_ascii=False)

        logger.info(f"Converted {pdf_path_obj.name}. Saved to {output_json}")
        typer.echo(f"Successfully converted '{pdf_path_obj.name}' to JSON.")
        typer.echo(f"Output saved to: {output_json}")
        typer.echo(f"Elements extracted: {len(result)}")
    except Exception as e:
        logger.error(f"Conversion failed for '{pdf_path_obj}': {e}")
        typer.echo(f"Error: Conversion failed: {str(e)}")
        raise typer.Exit(code=1)


def usage_function() -> Dict[str, Any]:
    """
    Simulates CLI usage by running a conversion.

    Returns:
        dict: Simulated CLI result.
    """
    sample_pdf = "input/BHT_CV32A65X.pdf"
    repo_link = "https://github.com/example/repo"
    output_dir = DEFAULT_OUTPUT_DIR
    try:
        result = convert_pdf_to_json(
            pdf_path=sample_pdf,
            repo_link=repo_link,
            output_dir=output_dir,
            corrections_dir=DEFAULT_CORRECTIONS_DIR,
        )
        return {
            "status": "success",
            "message": f"Converted '{sample_pdf}' to JSON.",
            "output_file": f"{output_dir}/{Path(sample_pdf).stem}_structured.json",
            "elements_extracted": len(result),
        }
    except Exception as e:
        return {
            "status": "error",
            "message": f"Conversion failed: {str(e)}",
            "output_file": "",
            "elements_extracted": 0,
        }


if __name__ == "__main__":
    fix_sys_path(__file__)
    logger.info("Testing CLI usage function...")
    result = usage_function()
    print("CLI Usage Function Result:")
    print(json.dumps(result, indent=2))
    app()
