"""Ingestion step 1: check that a file is a safe, readable PDF and extract its text page by page.

Flow:  file -> size check -> "is it really a PDF?" -> open -> page checks -> text per page

Every failure raises PdfError with a short `code` (e.g. "too_large"), so callers
(CLI now, API later) can show a clear message without leaking internals.
"""

import hashlib
from dataclasses import dataclass, field
from pathlib import Path

import pymupdf

from insureassist.config import settings

# Every real PDF starts with these bytes. Checking them is more reliable than
# trusting the file extension, which anyone can rename.
PDF_MAGIC = b"%PDF-"


class PdfError(Exception):
    """A file that cannot be ingested. `code` is machine-readable, `message` is for humans."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass
class Page:
    page_number: int  # 1-based, matching what a PDF viewer shows
    text: str


@dataclass
class PdfDocument:
    document_id: str
    checksum_sha256: str
    filename: str
    pages: list[Page]
    warnings: list[str] = field(default_factory=list)

    @property
    def page_count(self) -> int:
        return len(self.pages)


def make_document_id(checksum_sha256: str) -> str:
    """The ID comes from the file's content, so the same file always gets the same ID.

    This makes duplicate detection trivial: same bytes -> same ID.
    """
    return f"doc_{checksum_sha256[:16]}"


def read_pdf(
    path: Path,
    max_mb: int | None = None,
    max_pages: int | None = None,
) -> PdfDocument:
    """Validate the PDF at `path` and return its text, page by page."""
    max_mb = max_mb if max_mb is not None else settings.max_upload_mb
    max_pages = max_pages if max_pages is not None else settings.max_pdf_pages

    data = _read_file(path, max_mb)
    _check_is_pdf(path, data)
    checksum = hashlib.sha256(data).hexdigest()

    doc = _open_pdf(data)
    try:
        _check_pages(doc, max_pages)
        pages, warnings = _extract_pages(doc)
    finally:
        doc.close()

    if not any(page.text for page in pages):
        raise PdfError(
            "no_extractable_text",
            "No text found. The PDF may be scanned images; OCR is not supported yet.",
        )

    return PdfDocument(
        document_id=make_document_id(checksum),
        checksum_sha256=checksum,
        filename=path.name,
        pages=pages,
        warnings=warnings,
    )


# --- helpers (each does one check) -------------------------------------------


def _read_file(path: Path, max_mb: int) -> bytes:
    if not path.is_file():
        raise PdfError("not_found", f"File not found: {path.name}")
    size = path.stat().st_size
    # Check the size BEFORE reading, so a huge file never gets loaded into memory.
    if size == 0:
        raise PdfError("empty_file", "The file is empty.")
    if size > max_mb * 1024 * 1024:
        raise PdfError("too_large", f"File is larger than the {max_mb} MB limit.")
    return path.read_bytes()


def _check_is_pdf(path: Path, data: bytes) -> None:
    if path.suffix.lower() != ".pdf":
        raise PdfError("not_pdf", "Only .pdf files are supported.")
    # The PDF header must appear near the start of the file.
    if PDF_MAGIC not in data[:1024]:
        raise PdfError("not_pdf", "The file content is not a PDF.")


def _open_pdf(data: bytes) -> pymupdf.Document:
    try:
        doc = pymupdf.open(stream=data, filetype="pdf")
    except Exception as exc:  # PyMuPDF raises several error types for broken files
        raise PdfError("corrupt", "The PDF is damaged and cannot be read.") from exc
    if doc.needs_pass:
        doc.close()
        raise PdfError("encrypted", "Password-protected PDFs are not supported.")
    return doc


def _check_pages(doc: pymupdf.Document, max_pages: int) -> None:
    if doc.page_count == 0:
        raise PdfError("no_pages", "The PDF has no pages.")
    if doc.page_count > max_pages:
        raise PdfError("too_many_pages", f"The PDF has more than {max_pages} pages.")


def _extract_pages(doc: pymupdf.Document) -> tuple[list[Page], list[str]]:
    pages: list[Page] = []
    warnings: list[str] = []
    for index in range(doc.page_count):
        pdf_page: pymupdf.Page = doc[index]
        page_number = index + 1
        # sort=True returns text in natural reading order (top-left to bottom-right).
        text = pdf_page.get_text("text", sort=True).strip()
        pages.append(Page(page_number=page_number, text=text))

        if _looks_scanned(pdf_page, text):
            warnings.append(f"likely_scanned: page {page_number} looks like an image (no OCR)")
        elif not text:
            warnings.append(f"empty_page: page {page_number} has no text")
    return pages, warnings


def _looks_scanned(pdf_page: pymupdf.Page, text: str) -> bool:
    """A page with almost no text but a large image is probably a scanned picture."""
    if len(text) >= settings.scanned_page_min_chars:
        return False
    page_area = pdf_page.rect.width * pdf_page.rect.height
    for image in pdf_page.get_image_info():
        x0, y0, x1, y1 = image["bbox"]
        if (x1 - x0) * (y1 - y0) >= 0.5 * page_area:
            return True
    return False
