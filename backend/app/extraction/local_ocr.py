"""Lightweight local OCR adapters that produce a ``ParseContext`` (text plus boxes).

OCR output is evidence text, not invoice semantics. Two engines are supported:

* ``tesseract``: the system Tesseract binary, called as a subprocess with a hard
  timeout and ``OMP_THREAD_LIMIT=1`` so it uses one CPU thread. Default.
* ``rapidocr``: the optional ``rapidocr_onnxruntime`` package (bundled PP-OCR ONNX
  weights). It runs in a separate process so a timeout can stop it and its memory
  is released afterwards.

Digital PDF pages use their embedded text layer and are never OCR'd. Every engine is
loaded lazily, only when selected.
"""

import multiprocessing
import ntpath
import os
import re
import shutil
import subprocess
from importlib.metadata import PackageNotFoundError, version as package_version
from statistics import median
from typing import Optional

from .evidence import ParseContext


class OcrError(RuntimeError):
    """OCR failed; the caller decides whether the job is retryable."""


class OcrUnavailable(OcrError):
    """The selected engine is not installed or not on PATH (permanent)."""


class OcrTimeout(OcrError):
    """OCR exceeded its configured time limit."""


# --- Geometry helpers shared with the layout mapper ---------------------------------

def _center_y(box):
    return (box[1] + box[3]) / 2.0


def group_rows(fragments: list) -> list:
    """Cluster fragments into visual rows by vertical overlap; rows top-to-bottom,
    fragments left-to-right. Works for word-level and cell-level OCR alike."""
    rows = []
    for fragment in sorted(fragments, key=lambda f: (_center_y(f["bbox"]), f["bbox"][0])):
        box = fragment["bbox"]
        height = max(box[3] - box[1], 1.0)
        target = None
        for row in rows:
            overlap = min(row["bottom"], box[3]) - max(row["top"], box[1])
            if overlap >= 0.5 * min(height, row["bottom"] - row["top"]):
                target = row
                break
        if target is None:
            rows.append({"top": box[1], "bottom": box[3], "fragments": [fragment]})
        else:
            target["fragments"].append(fragment)
            target["top"], target["bottom"] = min(target["top"], box[1]), max(target["bottom"], box[3])
    for row in rows:
        row["fragments"].sort(key=lambda f: f["bbox"][0])
    rows.sort(key=lambda r: r["top"])
    return rows


def rows_text(rows: list) -> str:
    return "\n".join("  ".join(f["text"] for f in row["fragments"]) for row in rows)


_HEADER = re.compile(r"\bdescription\b.*\b(qty|quantity|price|amount|net|worth)\b", re.I)
_ROW_NUMBER = re.compile(r"^(\d{1,3})[.):;,]?$")
_ROW_NUMBER_PREFIX = re.compile(r"^(\d{1,3})[.):;]\s")
_TABLE_END = re.compile(r"^(summary|sub\s*-?\s*total|total)\b", re.I)


def numbered_row_count(rows: list) -> Optional[int]:
    """Count consecutively numbered item rows (1., 2., ...) below a table header.

    Returns None unless numbering is present and consecutive from 1, so the value is
    only reported when the table structure supports it.
    """
    in_table, numbers = False, []
    for row in rows:
        text = "  ".join(f["text"] for f in row["fragments"])
        if not in_table:
            in_table = bool(_HEADER.search(text))
            continue
        if _TABLE_END.search(text.strip()):
            break
        first = row["fragments"][0]["text"].strip()
        match = _ROW_NUMBER.match(first) or _ROW_NUMBER_PREFIX.match(first + " ")
        if match:
            numbers.append(int(match.group(1)))
    if not numbers or numbers != list(range(1, len(numbers) + 1)):
        return None
    return len(numbers)


# --- Engines --------------------------------------------------------------------------

class TesseractEngine:
    name = "tesseract"

    def __init__(self, command: str = "tesseract", language: str = "eng",
                 timeout_seconds: float = 120.0, psm: int = 3):
        self.command, self.language, self.timeout, self.psm = command, language, timeout_seconds, psm
        resolved = shutil.which(command) or (command if os.path.isfile(command) else None)
        if resolved is None and os.name == "nt" and command.lower() in ("tesseract", "tesseract.exe"):
            # The UB Mannheim installer may not refresh PATH in existing terminals (or
            # IDE-launched processes). Check its standard install locations as fallback.
            roots = (os.environ.get("ProgramW6432"), os.environ.get("ProgramFiles"),
                     os.environ.get("ProgramFiles(x86)"), r"C:\Program Files",
                     r"C:\Program Files (x86)")
            for root in dict.fromkeys(path for path in roots if path):
                candidate = ntpath.join(root, "Tesseract-OCR", "tesseract.exe")
                if os.path.isfile(candidate):
                    resolved = candidate
                    break
        if resolved is None:
            raise OcrUnavailable(f"Tesseract binary {command!r} not found; install it or set "
                                 "TESSERACT_CMD")
        self.binary = resolved
        self.version = self._version()

    def _version(self) -> str:
        try:
            out = subprocess.run([self.binary, "--version"], capture_output=True, text=True,
                                 timeout=30)
            first = (out.stdout or out.stderr).strip().splitlines()
            return first[0] if first else "unknown"
        except (OSError, subprocess.SubprocessError):
            return "unknown"

    def recognize(self, png_bytes: bytes) -> list:
        env = dict(os.environ, OMP_THREAD_LIMIT="1")
        try:
            out = subprocess.run([self.binary, "stdin", "stdout", "-l", self.language,
                                  "--psm", str(self.psm), "tsv"], input=png_bytes,
                                 capture_output=True, timeout=self.timeout, env=env)
        except subprocess.TimeoutExpired as exc:
            raise OcrTimeout(f"Tesseract exceeded {self.timeout:g}s") from exc
        except OSError as exc:
            raise OcrUnavailable(f"cannot run Tesseract: {exc}") from exc
        if out.returncode != 0:
            message = out.stderr.decode("utf-8", "replace").strip()[-300:]
            raise OcrError(f"Tesseract failed ({out.returncode}): {message}")
        return tesseract_tsv_fragments(out.stdout.decode("utf-8", "replace"))


def tesseract_tsv_fragments(tsv: str) -> list:
    """Words grouped by Tesseract line, split into fragments at wide horizontal gaps."""
    lines = {}
    rows = tsv.splitlines()
    for raw in rows[1:]:
        parts = raw.split("\t")
        if len(parts) < 12 or parts[0] != "5":
            continue
        text = parts[11].strip()
        try:
            conf = float(parts[10])
            left, top, width, height = (int(parts[i]) for i in range(6, 10))
        except ValueError:
            continue
        if not text or conf < 0:
            continue
        key = tuple(parts[1:5])
        lines.setdefault(key, []).append(
            {"text": text, "bbox": [left, top, left + width, top + height], "conf": conf / 100.0})
    fragments = []
    for words in lines.values():
        words.sort(key=lambda w: w["bbox"][0])
        heights = [w["bbox"][3] - w["bbox"][1] for w in words]
        gap_limit = 1.5 * (median(heights) if heights else 10)
        current = [words[0]]
        for word in words[1:]:
            if word["bbox"][0] - current[-1]["bbox"][2] > gap_limit:
                fragments.append(_merge(current))
                current = [word]
            else:
                current.append(word)
        fragments.append(_merge(current))
    return fragments


def _merge(words: list) -> dict:
    return {"text": " ".join(w["text"] for w in words),
            "bbox": [min(w["bbox"][0] for w in words), min(w["bbox"][1] for w in words),
                     max(w["bbox"][2] for w in words), max(w["bbox"][3] for w in words)],
            "conf": round(sum(w["conf"] for w in words) / len(words), 4)}


def _rapidocr_worker(png_bytes, connection):
    try:
        import io
        import numpy
        from PIL import Image
        from rapidocr_onnxruntime import RapidOCR
        with Image.open(io.BytesIO(png_bytes)) as opened:
            array = numpy.asarray(opened.convert("RGB"))[:, :, ::-1]  # RGB -> BGR
        result, _elapsed = RapidOCR()(array)
        fragments = []
        for box, text, score in result or []:
            xs, ys = [p[0] for p in box], [p[1] for p in box]
            fragments.append({"text": str(text).strip(),
                              "bbox": [float(min(xs)), float(min(ys)), float(max(xs)), float(max(ys))],
                              "conf": round(float(score), 4)})
        connection.send(("ok", fragments))
    except Exception as exc:  # reported to the parent process
        connection.send(("error", f"{type(exc).__name__}: {exc}"))
    finally:
        connection.close()


class RapidOcrEngine:
    name = "rapidocr"

    def __init__(self, timeout_seconds: float = 120.0):
        self.timeout = timeout_seconds
        try:
            self.version = "rapidocr_onnxruntime " + package_version("rapidocr_onnxruntime")
        except PackageNotFoundError as exc:
            raise OcrUnavailable("rapidocr_onnxruntime is not installed "
                                 "(pip install -r backend/requirements-ocr.txt)") from exc

    def recognize(self, png_bytes: bytes) -> list:
        context = multiprocessing.get_context("spawn")
        parent, child = context.Pipe(duplex=False)
        process = context.Process(target=_rapidocr_worker, args=(png_bytes, child), daemon=True)
        process.start()
        child.close()
        try:
            if not parent.poll(self.timeout):
                raise OcrTimeout(f"RapidOCR exceeded {self.timeout:g}s")
            status, payload = parent.recv()
        except EOFError as exc:
            raise OcrError("RapidOCR worker exited without a result") from exc
        finally:
            if process.is_alive():
                process.terminate()
            process.join(5)
            parent.close()
        if status != "ok":
            raise OcrError(f"RapidOCR failed: {payload}")
        return [f for f in payload if f["text"]]


def build_engine(name: str, settings=None):
    if name == "tesseract":
        return TesseractEngine(getattr(settings, "tesseract_cmd", "tesseract"),
                               getattr(settings, "ocr_language", "eng"),
                               getattr(settings, "ocr_timeout_seconds", 120.0),
                               getattr(settings, "tesseract_psm", 3))
    if name == "rapidocr":
        return RapidOcrEngine(getattr(settings, "ocr_timeout_seconds", 120.0))
    raise OcrUnavailable(f"unknown OCR engine {name!r}")


def parse_document(document, engine=None) -> ParseContext:
    """Text, fragments and boxes for every page; OCR only pages without a text layer."""
    pages, engines_used = [], set()
    for page in document.pages:
        if page.source == "pdf_text":
            fragments = page.text_fragments
            engines_used.add("pdf_text")
        else:
            if engine is None:
                raise OcrUnavailable(f"page {page.number} needs OCR but no engine is configured")
            fragments = engine.recognize(page.image_png)
            engines_used.add(engine.name)
        rows = group_rows(fragments)
        pages.append({"page": page.number, "text": rows_text(rows), "width": page.width,
                      "height": page.height, "source": page.source, "fragments": fragments,
                      "table_row_count": numbered_row_count(rows)})
    counts = [p["table_row_count"] for p in pages if p["table_row_count"] is not None]
    multi = len(pages) > 1
    return ParseContext(
        pages=pages,
        # Numbering restarts per page on some invoices, so a count is only reported for
        # single-page documents; it is a heuristic over the parser's own layout.
        table_row_count=counts[0] if counts and not multi else None,
        table_row_method="numbered_rows_heuristic" if counts and not multi else None,
        engine="+".join(sorted(engines_used)),
        engine_version=(getattr(engine, "version", None) if engine is not None
                        and engine.name in engines_used else None))
