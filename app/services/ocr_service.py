"""Render PDF pages and preserve Tesseract words and their PDF coordinates.

Coordinates are in points, relative to the top-left of the unrotated crop box.
This is the coordinate convention used by PyMuPDF for unrotated page content.
Page indices are zero-based. Each page's character offsets address its own text.
"""

from contextlib import closing
from functools import lru_cache
from pathlib import Path
from typing import Any

import anyio
import anyio.to_process
import pymupdf
import pytesseract
from PIL import Image
from pytesseract import Output

from app.core.config import Settings, get_settings
from app.models.document import OCRDocument, OCRPage, OCRWord, WordBox


class InvalidPDFError(ValueError):
    """The input is empty, invalid, encrypted or exceeds the page limit."""


class PDFTooLargeError(ValueError):
    """The input exceeds the configured byte limit."""


class OCRProcessingError(RuntimeError):
    """A page could not be rendered or processed by the OCR engine."""


def _validate_pdf_size(pdf_bytes: bytes, max_upload_bytes: int) -> None:
    if not pdf_bytes:
        raise InvalidPDFError("The PDF is empty")
    if len(pdf_bytes) > max_upload_bytes:
        raise PDFTooLargeError("The PDF exceeds the configured size limit")


def _word_box(page: pymupdf.Page, pix: pymupdf.Pixmap, scale: float, row: dict[str, Any]) -> WordBox | None:
    """Map normalized-image pixels back through rendering to PDF coordinates."""
    left, top = float(row["left"]), float(row["top"])
    width, height = float(row["width"]), float(row["height"])
    if width <= 0 or height <= 0:
        return None

    right, bottom = left + width, top + height
    if page.rotation == 90:
        left, top, right, bottom = pix.width - bottom, left, pix.width - top, right
    elif page.rotation == 180:
        left, top, right, bottom = pix.width - right, pix.height - bottom, pix.width - left, pix.height - top
    elif page.rotation == 270:
        left, top, right, bottom = top, pix.height - right, bottom, pix.height - left
    pixels = pymupdf.Rect(pix.x + left, pix.y + top, pix.x + right, pix.y + bottom)
    rect = pixels * ~pymupdf.Matrix(scale, scale) * page.derotation_matrix
    visible = page.rect * page.derotation_matrix
    rect &= visible
    if rect.is_empty or rect.is_infinite:
        return None
    return WordBox(rect.x0, rect.y0, rect.width, rect.height)


class OCRService:
    """Extract a document in a single pass with bounded PDF and OCR inputs."""

    def __init__(self, settings: Settings | None = None):
        self.settings = settings if settings is not None else get_settings()
        # This limiter must be shared by callers in this application process.
        self._process_limiter = anyio.CapacityLimiter(self.settings.ocr_max_concurrent_jobs)

    async def extract_async(self, pdf_bytes: bytes) -> OCRDocument:
        """Run blocking PDF/OCR work in a bounded worker process, not the event loop.

        Reuse this service (e.g. through get_ocr_service) so calls share the limit.
        The limit is per application process, not global across server workers.
        """
        _validate_pdf_size(pdf_bytes, self.settings.max_upload_bytes)
        return await anyio.to_process.run_sync(
            _extract_pdf_in_worker, pdf_bytes, self.settings,
            cancellable=False, limiter=self._process_limiter,
        )

    async def extract_file_async(self, pdf_path: str | Path) -> OCRDocument:
        """Read and process a local PDF in a bounded worker process."""
        return await anyio.to_process.run_sync(
            _extract_file_in_worker, str(pdf_path), self.settings,
            cancellable=False, limiter=self._process_limiter,
        )

    def extract(self, pdf_bytes: bytes) -> OCRDocument:
        _validate_pdf_size(pdf_bytes, self.settings.max_upload_bytes)

        try:
            document = pymupdf.open(stream=pdf_bytes, filetype="pdf")
        except (pymupdf.FileDataError, ValueError) as exc:
            raise InvalidPDFError("The input is not a valid PDF") from exc

        with document:
            if not document.is_pdf or document.needs_pass:
                raise InvalidPDFError("The input must be an unencrypted PDF")
            if document.page_count == 0:
                raise InvalidPDFError("The PDF contains no pages")
            if document.page_count > self.settings.max_pdf_pages:
                raise InvalidPDFError("The PDF exceeds the configured page limit")

            pages = tuple(self._extract_page(page) for page in document)
            return OCRDocument(pages=pages)

    def extract_file(self, pdf_path: str | Path) -> OCRDocument:
        # Bounded reads avoid loading an oversized file into memory first.
        with Path(pdf_path).open("rb") as source:
            pdf_bytes = source.read(self.settings.max_upload_bytes + 1)
        return self.extract(pdf_bytes)

    def _extract_page(self, page: pymupdf.Page) -> OCRPage:
        scale = self.settings.ocr_dpi / 72.0
        try:
            pix = page.get_pixmap(matrix=pymupdf.Matrix(scale, scale), alpha=False)
            with closing(Image.frombytes("RGB", (pix.width, pix.height), pix.samples)) as image:
                # Undo the PDF rotation before OCR; no general deskew or OSD is implied.
                with closing(image.rotate(page.rotation, expand=True)) as normalized:
                    data = pytesseract.image_to_data(
                        normalized,
                        lang=self.settings.ocr_languages,
                        output_type=Output.DICT,
                        timeout=self.settings.ocr_timeout_seconds,
                    )
        except (pytesseract.TesseractError, OSError, RuntimeError, ValueError) as exc:
            raise OCRProcessingError(f"OCR failed on page {page.number}") from exc

        words: list[OCRWord] = []
        parts: list[str] = []
        offset = 0
        previous_line: tuple[int, int, int] | None = None
        for index, raw_text in enumerate(data["text"]):
            text = raw_text.strip()
            if not text:
                continue
            row = {key: values[index] for key, values in data.items()}
            box = _word_box(page, pix, scale, row)
            if box is None:
                continue
            line = (int(row["block_num"]), int(row["par_num"]), int(row["line_num"]))
            separator = "" if not words else (" " if line == previous_line else "\n")
            parts.append(separator)
            offset += len(separator)
            start = offset
            parts.append(text)
            offset += len(text)
            words.append(OCRWord(text, box, start, offset, *line, float(row["conf"])))
            previous_line = line

        visible = page.rect * page.derotation_matrix
        return OCRPage(page.number, visible.width, visible.height, "".join(parts), tuple(words))


def _extract_pdf_in_worker(pdf_bytes: bytes, settings: Settings) -> OCRDocument:
    """Process entrypoint; construct PDF and OCR objects inside the worker."""
    return OCRService(settings).extract(pdf_bytes)


def _extract_file_in_worker(pdf_path: str, settings: Settings) -> OCRDocument:
    return OCRService(settings).extract_file(pdf_path)


@lru_cache
def get_ocr_service() -> OCRService:
    """Provide one reusable OCR service and limiter per application process."""
    return OCRService()


def extract_text_from_pdf(pdf_path: str) -> str:
    """Compatibility wrapper for callers needing the full OCR text."""
    return OCRService().extract_file(pdf_path).text


def get_word_bounding_boxes(pdf_path: str) -> list[dict]:
    """Compatibility wrapper returning one PDF-coordinate box per OCR word."""
    result = OCRService().extract_file(pdf_path)
    return [
        {"word": word.text, "page": page.page_number, "x": word.box.x,
         "y": word.box.y, "width": word.box.width, "height": word.box.height}
        for page in result.pages for word in page.words
    ]
