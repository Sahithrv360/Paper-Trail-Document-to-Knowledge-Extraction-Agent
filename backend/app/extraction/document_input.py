"""Bounded loading of JPG/PNG images and PDFs into per-page inputs.

* Images are EXIF-rotated, converted to RGB and downscaled to ``max_page_pixels``.
* PDFs (optional ``pypdfium2``) use the embedded text layer first. A page with a
  usable text layer yields text fragments with boxes and is *not* OCR'd; a page
  without one is rendered for OCR or a vision model.
* Each page is kept as compressed PNG bytes, not a decoded pixel array, so a
  multi-page document does not hold several full-resolution bitmaps in memory.

Coordinates of every box on a page are pixels of that page's stored image.
Documents with more pages than ``max_pages`` are refused (never silently truncated).
"""

import io
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png"}
PDF_SUFFIXES = {".pdf"}
MIN_TEXT_LAYER_CHARS = 30          # alphanumerics needed to trust a PDF text layer
HARD_PIXEL_LIMIT = 120_000_000     # decompression-bomb guard before any resize


class DocumentLoadError(Exception):
    """The document cannot be read; retrying will not help."""


class DocumentTooLarge(DocumentLoadError):
    """The document exceeds the configured page limit."""


class OptionalDependencyMissing(DocumentLoadError):
    """An optional extra (for example PDF support) is not installed."""


@dataclass
class PageInput:
    number: int                      # 1-based
    width: int
    height: int
    image_png: Optional[bytes]       # stored page image (PNG); None if not rendered
    source: str                      # "image" | "pdf_text" | "pdf_scan"
    text_fragments: list = field(default_factory=list)   # digital-PDF text with boxes
    scale: float = 1.0               # stored pixels per original pixel (or per PDF point)

    def image(self):
        if self.image_png is None:
            raise DocumentLoadError(f"page {self.number} has no rendered image")
        from PIL import Image
        with Image.open(io.BytesIO(self.image_png)) as opened:
            return opened.convert("RGB")


@dataclass
class LoadedDocument:
    path: Path
    kind: str                        # "image" | "pdf"
    pages: list
    page_count_in_file: int

    def first_image(self):
        return self.pages[0].image()

    def summary(self) -> dict:
        return {"kind": self.kind, "pages": [
            {"page": p.number, "source": p.source, "width": p.width, "height": p.height,
             "scale": round(p.scale, 6)} for p in self.pages]}


def _encode_png(image) -> bytes:
    buffer = io.BytesIO()
    image.save(buffer, format="PNG", optimize=False)
    return buffer.getvalue()


def _bounded(image, max_pixels: int):
    """Downscale (never upscale) so width*height <= max_pixels; return (image, scale)."""
    width, height = image.size
    if width * height <= max_pixels:
        return image, 1.0
    scale = (max_pixels / float(width * height)) ** 0.5
    size = (max(1, int(width * scale)), max(1, int(height * scale)))
    from PIL import Image
    return image.resize(size, Image.LANCZOS), size[0] / float(width)


def load_image_document(path: Path, max_page_pixels: int) -> LoadedDocument:
    try:
        from PIL import Image, ImageOps
    except ImportError as exc:
        raise OptionalDependencyMissing("Image input requires Pillow") from exc
    try:
        with Image.open(path) as opened:
            if opened.size[0] * opened.size[1] > HARD_PIXEL_LIMIT:
                raise DocumentTooLarge(f"image is {opened.size[0]}x{opened.size[1]} pixels")
            if getattr(opened, "n_frames", 1) > 1:
                raise DocumentLoadError("multi-frame images are not supported")
            image = ImageOps.exif_transpose(opened).convert("RGB")
    except DocumentLoadError:
        raise
    except Exception as exc:  # Pillow raises several types for corrupt files
        raise DocumentLoadError(f"unreadable image: {type(exc).__name__}: {exc}") from exc
    image, scale = _bounded(image, max_page_pixels)
    page = PageInput(1, image.size[0], image.size[1], _encode_png(image), "image", scale=scale)
    return LoadedDocument(Path(path), "image", [page], 1)


def load_pdf_document(path: Path, max_pages: int, max_page_pixels: int,
                      render_dpi: int = 200) -> LoadedDocument:
    try:
        import pypdfium2 as pdfium
    except ImportError as exc:
        raise OptionalDependencyMissing(
            "PDF input requires the optional pypdfium2 package "
            "(pip install -r backend/requirements-pdf.txt); PDF extraction is not "
            "implemented without it") from exc
    try:
        pdf = pdfium.PdfDocument(str(path))
    except Exception as exc:
        raise DocumentLoadError(f"unreadable PDF: {type(exc).__name__}: {exc}") from exc
    try:
        count = len(pdf)
        if count == 0:
            raise DocumentLoadError("PDF has no pages")
        if count > max_pages:
            raise DocumentTooLarge(f"PDF has {count} pages; MAX_PAGES is {max_pages}")
        pages = []
        for index in range(count):
            page = pdf[index]
            try:
                width_pt, height_pt = page.get_size()
                scale = render_dpi / 72.0
                if width_pt * height_pt * scale * scale > max_page_pixels:
                    scale = (max_page_pixels / float(width_pt * height_pt)) ** 0.5
                bitmap = page.render(scale=scale)
                image = bitmap.to_pil().convert("RGB")
                fragments = _text_fragments(page, height_pt, scale)
                alnum = sum(len(re.sub(r"\W", "", f["text"])) for f in fragments)
                source = "pdf_text" if alnum >= MIN_TEXT_LAYER_CHARS else "pdf_scan"
                pages.append(PageInput(index + 1, image.size[0], image.size[1],
                                       _encode_png(image), source,
                                       fragments if source == "pdf_text" else [], scale))
            finally:
                page.close()
        return LoadedDocument(Path(path), "pdf", pages, count)
    finally:
        pdf.close()


def _text_fragments(page, height_pt: float, scale: float) -> list:
    """Embedded text runs with boxes converted to stored-image pixel coordinates."""
    textpage = page.get_textpage()
    try:
        fragments = []
        for index in range(textpage.count_rects()):
            left, bottom, right, top = textpage.get_rect(index)
            text = textpage.get_text_bounded(left, bottom, right, top).strip()
            if not text:
                continue
            box = [left * scale, (height_pt - top) * scale,
                   right * scale, (height_pt - bottom) * scale]
            fragments.append({"text": " ".join(text.split()),
                              "bbox": [round(v, 1) for v in box], "conf": None})
        return fragments
    finally:
        textpage.close()


def load_document(path, max_pages: int = 5, max_page_pixels: int = 9_000_000,
                  render_dpi: int = 300) -> LoadedDocument:
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix in IMAGE_SUFFIXES:
        return load_image_document(path, max_page_pixels)
    if suffix in PDF_SUFFIXES:
        return load_pdf_document(path, max_pages, max_page_pixels, render_dpi)
    raise DocumentLoadError(f"unsupported file type {suffix or '(none)'}")
