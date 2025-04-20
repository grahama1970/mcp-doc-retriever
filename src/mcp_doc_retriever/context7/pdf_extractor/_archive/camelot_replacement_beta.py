from itertools import chain, product
from typing import Any, Dict, List, Optional, Tuple

import cv2
import fitz
import numpy as np
import pandas as pd
import pdfplumber
import pytesseract
from pydantic import BaseModel, Field


class ExtractionResult(BaseModel):
    tables: Optional[List[Any]] = Field(default=None, description='Extracted tables')
    quality: float = Field(ge=0.0, le=1.0, description='Quality score of the extraction')
    params: Dict[str, Any] = Field(default_factory=dict, description='Parameters used for extraction')

class ExtractionSettingsManager:

    def __init__(self):
        self.settings_usage = {}
        self.settings_quality = {}

    def update_settings(self, settings: Dict[str, Any], quality: float) -> None:
        settings_key = self._settings_to_key(settings)
        if settings_key in self.settings_usage:
            self.settings_usage[settings_key] += 1
            self.settings_quality[settings_key] = (self.settings_quality[settings_key] + quality) / 2
        else:
            self.settings_usage[settings_key] = 1
            self.settings_quality[settings_key] = quality

    def get_best_settings(self) -> List[Dict[str, Any]]:
        sorted_settings = sorted(self.settings_usage.items(), key=lambda x: x[1], reverse=True)
        return [self._key_to_settings(key) for (key, _) in sorted_settings]

    def _settings_to_key(self, settings: Dict[str, Any]) -> str:
        return str(sorted(settings.items()))

    def _key_to_settings(self, key: str) -> Dict[str, Any]:
        return dict(eval(key))

def convert_pdfplumber_bbox_to_pymupdf(pdfplumber_bbox: Tuple[float, float, float, float], page_height: float) -> Tuple[float, float, float, float]:
    (x0, y0, x1, y1) = pdfplumber_bbox
    return (x0, page_height - y1, x1, page_height - y0)

def extract_table_bboxes_with_pdfplumber(pdf_path: str, page_num: int) -> List[Tuple[float, float, float, float]]:
    with pdfplumber.open(pdf_path) as pdf:
        page = pdf.pages[page_num - 1]
        tables = page.find_tables()
        page_height = page.height
        return [convert_pdfplumber_bbox_to_pymupdf(table.bbox, page_height) for table in tables]

def extract_tables_using_stream(pdf_path: str, page_num: int) -> List[pd.DataFrame]:
    doc = fitz.open(pdf_path)
    page = doc.load_page(page_num - 1)
    text = page.get_text('dict')
    lines = text['blocks']
    table_data = []
    current_table = []
    for block in lines:
        if block['type'] == 0:
            line_texts = [span['text'] for span in block['lines'][0]['spans']]
            current_table.append(line_texts)
        elif current_table:
            table_data.append(pd.DataFrame(current_table))
            current_table = []
    if current_table:
        table_data.append(pd.DataFrame(current_table))
    return table_data

def extract_table_with_error_tolerance(pdf_path: str, page_num: int, edge_tol: int=50, shift_tol: int=10) -> Optional[List[pd.DataFrame]]:
    doc = fitz.open(pdf_path)
    page = doc.load_page(page_num - 1)
    table_data = []
    for block in page.get_text('blocks'):
        if abs(block[0] - block[2]) > edge_tol:
            table_data.append(block)
    return table_data

def detect_columns_by_alignment(page_text: List[Dict[str, Any]]) -> List[int]:
    column_positions = set()
    for block in page_text['blocks']:
        if block['type'] == 0:
            for line in block['lines']:
                for span in line['spans']:
                    column_positions.add(int(span['bbox'][0]))
    return sorted(column_positions)

def calculate_whitespace_score(table: pd.DataFrame) -> float:
    if table.empty:
        return 0.0
    row_gaps = []
    col_gaps = []
    for i in range(len(table) - 1):
        row_diff = table.iloc[i + 1].name - table.iloc[i].name
        row_gaps.append(row_diff)
    for col in range(len(table.columns) - 1):
        col_diff = table.iloc[:, col + 1].name - table.iloc[:, col].name
        col_gaps.append(col_diff)
    avg_row_gap = sum(row_gaps) / len(row_gaps) if row_gaps else 0
    avg_col_gap = sum(col_gaps) / len(col_gaps) if col_gaps else 0
    max_gap = max(avg_row_gap, avg_col_gap)
    return min(1, max_gap / 100)

def generate_parsing_report(tables: List[pd.DataFrame]) -> Dict[str, Any]:
    report = {}
    for (idx, table) in enumerate(tables):
        whitespace_score = calculate_whitespace_score(table)
        accuracy_score = calculate_accuracy_score(table)
        report[f'Table_{idx + 1}'] = {'accuracy': accuracy_score, 'whitespace': whitespace_score, 'overall_quality': (accuracy_score + whitespace_score) / 2}
    return report

def detect_lines(image: np.ndarray, scale: int) -> Tuple[np.ndarray, np.ndarray]:
    horizontal = np.copy(image)
    vertical = np.copy(image)
    cols = horizontal.shape[1]
    horizontal_size = cols // scale
    horizontal_structure = cv2.getStructuringElement(cv2.MORPH_RECT, (horizontal_size, 1))
    horizontal = cv2.erode(horizontal, horizontal_structure)
    horizontal = cv2.dilate(horizontal, horizontal_structure)
    rows = vertical.shape[0]
    vertical_size = rows // scale
    vertical_structure = cv2.getStructuringElement(cv2.MORPH_RECT, (1, vertical_size))
    vertical = cv2.erode(vertical, vertical_structure)
    vertical = cv2.dilate(vertical, vertical_structure)
    return (horizontal, vertical)

def find_table_contours(horizontal: np.ndarray, vertical: np.ndarray) -> List[np.ndarray]:
    mask = horizontal + vertical
    (contours, _) = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    return contours

def get_table_bounds(contour: np.ndarray) -> Tuple[int, int, int, int]:
    (x, y, w, h) = cv2.boundingRect(contour)
    return (x, y, x + w, y + h)

def extract_table_data(image: np.ndarray, table_bounds: Tuple[int, int, int, int], horizontal: np.ndarray, vertical: np.ndarray) -> pd.DataFrame:
    (x1, y1, x2, y2) = table_bounds
    table_image = image[y1:y2, x1:x2]
    table_horizontal = horizontal[y1:y2, x1:x2]
    table_vertical = vertical[y1:y2, x1:x2]
    cells = find_cells(table_horizontal, table_vertical)
    (xs, ys) = build_table_structure(cells)
    grid = assign_cells_to_grid(cells, xs, ys)
    data = []
    for i in range(len(ys) - 1):
        row = []
        for j in range(len(xs) - 1):
            cell = grid.get((i, j))
            if cell:
                text = extract_cell_text(table_image, cell)
                row.append(text.strip())
            else:
                row.append('')
        data.append(row)
    return pd.DataFrame(data)

def pdf_to_image(pdf_path: str, page_num: int) -> np.ndarray:
    doc = fitz.open(pdf_path)
    page = doc.load_page(page_num - 1)
    pix = page.get_pixmap(matrix=fitz.Matrix(2, 2))
    image = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)
    if pix.n == 4:
        image = cv2.cvtColor(image, cv2.COLOR_RGBA2RGB)
    return image

def calculate_accuracy_score(table: pd.DataFrame) -> float:
    """Calculate accuracy score based on the table structure and data."""
    numeric_cols = table.select_dtypes(include=[np.number])
    if not numeric_cols.empty:
        non_numeric_entries = numeric_cols.apply(pd.to_numeric, errors='coerce').isna().sum().sum()
        total_entries = numeric_cols.size
        accuracy_score = (total_entries - non_numeric_entries) / total_entries
    else:
        accuracy_score = 0.8
    return accuracy_score

def find_cells(horizontal: np.ndarray, vertical: np.ndarray) -> List[Tuple[int, int, int, int]]:
    """Find individual cells in the table using horizontal and vertical line intersections."""
    mask = cv2.bitwise_and(horizontal, vertical)
    (contours, _) = cv2.findContours(mask, cv2.RETR_TREE, cv2.CHAIN_APPROX_SIMPLE)
    return [cv2.boundingRect(c) for c in contours]

def build_table_structure(cells: List[Tuple[int, int, int, int]]) -> Tuple[List[int], List[int]]:
    """Build table structure from cell positions."""
    xs = sorted(set([c[0] for c in cells] + [c[0] + c[2] for c in cells]))
    ys = sorted(set([c[1] for c in cells] + [c[1] + c[3] for c in cells]))
    return (xs, ys)

def assign_cells_to_grid(cells: List[Tuple[int, int, int, int]], xs: List[int], ys: List[int]) -> Dict[Tuple[int, int], Tuple[int, int, int, int]]:
    """Assign cells to a grid structure."""
    grid = {}
    for cell in cells:
        (x, y, w, h) = cell
        col = xs.index(x)
        row = ys.index(y)
        grid[row, col] = (x, y, w, h)
    return grid

def extract_cell_text(image: np.ndarray, cell: Tuple[int, int, int, int]) -> str:
    """Extract text from a cell using OCR."""
    (x, y, w, h) = cell
    cell_image = image[y:y + h, x:x + w]
    return pytesseract.image_to_string(cell_image, config='--psm 6')

def find_best_table_extraction(pdf_path: str, page_num: int, manager: ExtractionSettingsManager) -> Tuple[Optional[List[pd.DataFrame]], Dict[str, Any]]:
    """Find the best table extraction by testing multiple parameters and extraction methods."""
    best_result = ExtractionResult(tables=None, quality=0, params={})
    param_combinations = manager.get_best_settings()
    if not param_combinations:
        param_combinations = list(chain(({'method': 'cv', 'line_scale': ls} for ls in [15, 40, 80]), ({'method': 'ocr', 'edge_tol': et, 'min_text_height': mth, 'split_text': st} for (et, mth, st) in product([500, 1000, 1500], [1.0, 2.0, 3.0], [True, False]))))
    for current_params in param_combinations:
        tables = extract_table_with_params(pdf_path, page_num, **current_params)
        (best_result.quality, best_result.tables, best_result.params) = evaluate_extraction(tables, current_params, best_result.quality, best_result.tables, best_result.params)
        manager.update_settings(current_params, best_result.quality)
    return (best_result.tables, best_result.params)

def calculate_table_quality(tables: List[Any]) -> Dict[str, Any]:
    """Calculate table quality based on accuracy, completeness, and consistency."""
    if not tables:
        return {'average_table_extraction_quality': 0.0, 'table_scores': []}
    table_scores = []
    for table in tables:
        if table is None or table.empty:
            continue
        accuracy_score = calculate_accuracy_score(table)
        completeness_score = calculate_completeness_score(table)
        consistency_score = calculate_consistency_score(table)
        weighted_score = accuracy_score * 0.4 + completeness_score * 0.3 + consistency_score * 0.3
        table_scores.append({'accuracy': accuracy_score, 'completeness': completeness_score, 'consistency': consistency_score, 'weighted_score': weighted_score})
    average_quality = sum((score['weighted_score'] for score in table_scores)) / len(table_scores) if table_scores else 0.0
    return {'average_table_extraction_quality': average_quality, 'table_scores': table_scores}

def extract_table_with_params(pdf_path: str, page_num: int, **params) -> Optional[List[pd.DataFrame]]:
    """Extract tables from a specific page using the provided parameters."""
    image = pdf_to_image(pdf_path, page_num)
    if params['method'] == 'cv':
        table_bounds_list = extract_tables_from_page(image, params['line_scale'])
        tables = []
        for bounds in table_bounds_list:
            table_df = extract_table_data_from_pdf(pdf_path, page_num, bounds)
            tables.append(table_df)
        return tables
    elif params['method'] == 'ocr':
        return extract_tables_with_ocr(image)
    return None

def preprocess_image(image: np.ndarray) -> np.ndarray:
    """Preprocess the image for line detection."""
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    (_, thresh) = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    return thresh

def extract_tables_from_page(image: np.ndarray, line_scale: int) -> List[Tuple[int, int, int, int]]:
    """Extract tables from a page using OpenCV's line detection method."""
    preprocessed_image = preprocess_image(image)
    (horizontal_lines, vertical_lines) = detect_lines(preprocessed_image, line_scale)
    contours = find_table_contours(horizontal_lines, vertical_lines)
    table_bounds = [get_table_bounds(contour) for contour in contours]
    return table_bounds

def extract_table_data_from_pdf(pdf_path: str, page_num: int, table_bounds: Tuple[int, int, int, int]) -> pd.DataFrame:
    """Extract table data from the specified table bounds within the PDF using text extraction."""
    (x1, y1, x2, y2) = table_bounds
    doc = fitz.open(pdf_path)
    page = doc.load_page(page_num - 1)
    table_text = page.get_textbox(fitz.Rect(x1, y1, x2, y2))
    rows = [row.strip() for row in table_text.split('\n') if row.strip()]
    table_data = [row.split() for row in rows]
    return pd.DataFrame(table_data)

def extract_tables_with_ocr(image: np.ndarray) -> List[pd.DataFrame]:
    """Extract tables from an image using OCR (Optical Character Recognition)."""
    ocr_text = pytesseract.image_to_string(image)
    table_rows = [row.split() for row in ocr_text.split('\n') if row.strip()]
    return [pd.DataFrame(table_rows)]

def evaluate_extraction(tables: Optional[List[Any]], current_params: Dict[str, Any], best_quality: float, best_tables: Optional[List[Any]], best_params: Dict[str, Any]) -> Tuple[float, Optional[List[Any]], Dict[str, Any]]:
    """Evaluate the quality of extracted tables and update the best result if necessary."""
    if tables is None:
        return (best_quality, best_tables, best_params)
    quality_result = calculate_table_quality(tables)
    overall_quality = quality_result.get('average_table_extraction_quality', 0.0)
    if overall_quality > best_quality:
        best_quality = overall_quality
        best_tables = tables
        best_params = current_params
    return (best_quality, best_tables, best_params)

def calculate_completeness_score(df: pd.DataFrame) -> float:
    """Calculate completeness score (percentage of non-null cells)."""
    total_cells = df.size
    if total_cells == 0:
        return 0.0
    filled_cells = total_cells - df.isna().sum().sum()
    completeness_score = filled_cells / total_cells
    return completeness_score

def calculate_consistency_score(df: pd.DataFrame) -> float:
    """Calculate consistency score (100 for consistent columns or 0 for inconsistent)."""
    if df.empty:
        return 0.0
    consistent_columns = all((len(row) == len(df.columns) for row in df.values))
    return 1.0 if consistent_columns else 0.0

def table_extraction_pipeline(pdf_path: str, page_num: int, manager: ExtractionSettingsManager) -> ExtractionResult:
    """Main pipeline for extracting tables from a PDF page, prioritizing PDF text over OCR."""
    (tables, params) = find_best_table_extraction(pdf_path, page_num, manager)
    if not tables:
        return ExtractionResult(tables=None, quality=0, params={})
    quality_result = calculate_table_quality(tables)
    overall_quality = quality_result.get('average_table_extraction_quality', 0.0)
    return ExtractionResult(tables=tables, quality=overall_quality, params=params)
if __name__ == '__main__':
    pdf_path = 'path/to/your/pdf.pdf'
    page_num = 1
    manager = ExtractionSettingsManager()
    result = table_extraction_pipeline(pdf_path, page_num, manager)
    print(f'Extraction Quality: {result.quality}')
    for (i, table) in enumerate(result.tables):
        print(f'Table {i + 1}:')
        print(table)