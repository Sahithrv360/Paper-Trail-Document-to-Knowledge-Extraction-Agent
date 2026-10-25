"""Run PaddleOCR's document pipeline and save inspectable text/table OCR output.

This is an OCR reader, not an invoice-field parser. It preserves recognized text,
recognition scores, bounding polygons, table structure, and layout reading order so a
later structured extractor can be evaluated against the labelled invoice dataset.
"""

import argparse
import json
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any


PIPELINE_NAME = "PP-StructureV3"
RECOGNITION_MODEL = "en_PP-OCRv4_mobile_rec"


def _jsonable(value: Any) -> Any:
    """Convert Paddle/Numpy values into ordinary JSON types without losing geometry."""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if hasattr(value, "tolist"):
        return _jsonable(value.tolist())
    if hasattr(value, "item"):
        return _jsonable(value.item())
    return str(value)


def _result_dict(result: Any) -> dict:
    """Read the public PaddleX result JSON property across supported result wrappers."""
    payload = getattr(result, "json", None)
    if callable(payload):
        payload = payload()
    if payload is None and hasattr(result, "to_dict"):
        payload = result.to_dict()
    if isinstance(payload, str):
        payload = json.loads(payload)
    if not isinstance(payload, dict):
        raise RuntimeError("PaddleOCR returned a result without a JSON object")
    payload = _jsonable(payload)
    nested = payload.get("res")
    return nested if isinstance(nested, dict) else payload


def _list(value: Any) -> list:
    value = _jsonable(value)
    return value if isinstance(value, list) else []


def _ocr_lines(ocr_result: dict) -> list[dict]:
    texts = _list(ocr_result.get("rec_texts"))
    scores = _list(ocr_result.get("rec_scores"))
    polygons = _list(ocr_result.get("rec_polys"))
    if not polygons:
        polygons = _list(ocr_result.get("rec_boxes"))
    count = max(len(texts), len(scores), len(polygons))
    lines = []
    for index in range(count):
        text = texts[index] if index < len(texts) else None
        if text is None or not str(text).strip():
            continue
        score = scores[index] if index < len(scores) else None
        lines.append({
            "line_number": index + 1,
            "text": str(text),
            # This is the OCR recognizer's score, not a calibrated invoice-field confidence.
            "recognition_score": score,
            "polygon": polygons[index] if index < len(polygons) else None,
        })
    return lines


def _tables(table_results: Any) -> list[dict]:
    tables = []
    for index, table in enumerate(_list(table_results), start=1):
        if not isinstance(table, dict):
            continue
        table_ocr = table.get("table_ocr_pred")
        if not isinstance(table_ocr, dict):
            table_ocr = {}
        tables.append({
            "table_number": index,
            "html": table.get("pred_html"),
            "cell_boxes": _jsonable(table.get("cell_box_list") or []),
            "cell_texts": _list(table_ocr.get("rec_texts")),
            "cell_scores": _list(table_ocr.get("rec_scores")),
            "cell_polygons": _jsonable(table_ocr.get("rec_polys") or []),
        })
    return tables


def _layout_blocks(parsing_results: Any) -> list[dict]:
    blocks = []
    for item in _list(parsing_results):
        if not isinstance(item, dict):
            continue
        block = {
            key: _jsonable(item[key])
            for key in ("block_label", "block_bbox", "block_content", "block_id", "block_order")
            if key in item
        }
        if block:
            blocks.append(block)
    return blocks


class PaddleOCRReader:
    """Lazy PP-StructureV3 reader for English invoice images."""

    def __init__(self, device: str = "cpu"):
        if device not in {"cpu", "gpu"}:
            raise ValueError("PaddleOCR device must be 'cpu' or 'gpu'")
        self.device = device
        self._pipeline = None

    def _load_pipeline(self):
        if self._pipeline is not None:
            return self._pipeline
        try:
            from paddleocr import PPStructureV3
        except ImportError as exc:
            raise RuntimeError(
                "Install PaddleOCR with: python -m pip install "
                "-r backend/requirements-paddleocr.txt"
            ) from exc
        self._pipeline = PPStructureV3(
            text_recognition_model_name=RECOGNITION_MODEL,
            device="gpu:0" if self.device == "gpu" else "cpu",
            # Paddle's Windows CPU oneDNN path can reject PIR model attributes;
            # plain CPU kernels avoid that backend-specific failure.
            enable_mkldnn=False,
            use_doc_orientation_classify=False,
            use_doc_unwarping=False,
            use_textline_orientation=True,
            use_table_recognition=True,
            use_formula_recognition=False,
            use_seal_recognition=False,
        )
        return self._pipeline

    def read_image(self, image) -> dict:
        """Return OCR lines and tables from a PIL image, with coordinates and scores."""
        try:
            import numpy as np
        except ImportError as exc:
            raise RuntimeError(
                "Install PaddleOCR with: python -m pip install "
                "-r backend/requirements-paddleocr.txt"
            ) from exc
        rgb = image.convert("RGB")
        image_array = np.asarray(rgb)
        predictions = self._load_pipeline().predict(input=image_array)
        pages = []
        for page_number, prediction in enumerate(predictions, start=1):
            result = _result_dict(prediction)
            overall = result.get("overall_ocr_res") or {}
            if not isinstance(overall, dict):
                overall = {}
            lines = _ocr_lines(overall)
            tables = _tables(result.get("table_res_list"))
            pages.append({
                "page_number": page_number,
                "text": "\n".join(line["text"] for line in lines),
                "lines": lines,
                "tables": tables,
                "layout_blocks": _layout_blocks(result.get("parsing_res_list")),
            })
        if not pages:
            raise RuntimeError("PaddleOCR returned no page results")
        return {
            "pipeline": PIPELINE_NAME,
            "recognition_model": RECOGNITION_MODEL,
            "language": "en",
            "device": self.device,
            "image_width": rgb.width,
            "image_height": rgb.height,
            "pages": pages,
            "recognized_text": "\n\n".join(page["text"] for page in pages),
            "line_count": sum(len(page["lines"]) for page in pages),
            "table_count": sum(len(page["tables"]) for page in pages),
            "limitations": [
                "OCR output is not mapped to canonical invoice fields.",
                "Recognition scores are not calibrated invoice-field confidence.",
                "No accuracy result is produced by this command.",
            ],
        }


def read_image_file(image_path: Path, reader: PaddleOCRReader) -> dict:
    try:
        from PIL import Image
    except ImportError as exc:
        raise RuntimeError(
            "Install PaddleOCR with: python -m pip install "
            "-r backend/requirements-paddleocr.txt"
        ) from exc
    path = Path(image_path)
    with Image.open(path) as opened:
        result = reader.read_image(opened)
    try:
        result["paddleocr_version"] = version("paddleocr")
    except PackageNotFoundError:
        result["paddleocr_version"] = "unknown"
    result["source_file"] = path.name
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", required=True, type=Path, help="Local invoice image path")
    parser.add_argument("--output", type=Path,
                        help="JSON output path; defaults to reports/paddleocr/<image>.json")
    parser.add_argument("--device", choices=("cpu", "gpu"), default="cpu")
    args = parser.parse_args()
    if not args.image.is_file():
        parser.error(f"image file not found: {args.image}")
    output = args.output or Path("reports/paddleocr") / f"{args.image.stem}.json"
    result = read_image_file(args.image, PaddleOCRReader(args.device))
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Recognized {result['line_count']} text lines and {result['table_count']} tables.")
    print(f"OCR result written to {output}")
    print("\n" + result["recognized_text"])


if __name__ == "__main__":
    main()
