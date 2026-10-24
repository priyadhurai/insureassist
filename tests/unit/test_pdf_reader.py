"""Tests for ingestion step 1 (pdf_reader)."""

from collections.abc import Callable
from pathlib import Path

import pymupdf
import pytest

from insureassist.ingestion.pdf_reader import PdfError, read_pdf

MakePdf = Callable[..., Path]


# --- happy path ---------------------------------------------------------------


def test_text_stays_on_its_own_page(make_pdf: MakePdf) -> None:
    path = make_pdf(["Deductible is 500 dollars.", "Waiting period is 30 days."])

    doc = read_pdf(path)

    assert doc.page_count == 2
    assert doc.pages[0].page_number == 1
    assert "Deductible" in doc.pages[0].text
    assert "Waiting" not in doc.pages[0].text
    assert "Waiting period" in doc.pages[1].text
    assert doc.warnings == []


def test_same_file_gives_same_id(make_pdf: MakePdf, tmp_path: Path) -> None:
    original_path = make_pdf(["Same content"], name="a.pdf")
    # Copy the exact bytes under another name. (Generating the PDF twice would NOT give
    # identical bytes: PyMuPDF stamps a creation time and unique file ID into each save.)
    copy_path = tmp_path / "renamed-copy.pdf"
    copy_path.write_bytes(original_path.read_bytes())

    first = read_pdf(original_path)
    copy = read_pdf(copy_path)
    different = read_pdf(make_pdf(["Other content"], name="c.pdf"))

    assert first.document_id == copy.document_id  # the filename does not matter
    assert first.document_id != different.document_id
    assert first.document_id.startswith("doc_")


def test_empty_page_produces_warning(make_pdf: MakePdf) -> None:
    doc = read_pdf(make_pdf(["Some text", ""]))

    assert doc.page_count == 2
    assert any(w.startswith("empty_page: page 2") for w in doc.warnings)


# --- rejected files -------------------------------------------------------------


def _error_code(path: Path, **limits: int) -> str:
    with pytest.raises(PdfError) as info:
        read_pdf(path, **limits)
    return info.value.code


def test_missing_file(tmp_path: Path) -> None:
    assert _error_code(tmp_path / "nope.pdf") == "not_found"


def test_empty_file(tmp_path: Path) -> None:
    path = tmp_path / "empty.pdf"
    path.write_bytes(b"")
    assert _error_code(path) == "empty_file"


def test_too_large(make_pdf: MakePdf) -> None:
    path = make_pdf(["x" * 100])
    assert _error_code(path, max_mb=0) == "too_large"


def test_wrong_extension(tmp_path: Path) -> None:
    path = tmp_path / "notes.txt"
    path.write_text("hello")
    assert _error_code(path) == "not_pdf"


def test_renamed_text_file_is_not_a_pdf(tmp_path: Path) -> None:
    path = tmp_path / "fake.pdf"
    path.write_text("I am just text pretending to be a PDF")
    assert _error_code(path) == "not_pdf"


def test_corrupt_pdf(tmp_path: Path) -> None:
    path = tmp_path / "broken.pdf"
    path.write_bytes(b"%PDF-1.7\n" + b"\x00garbage" * 50)
    assert _error_code(path) == "corrupt"


def test_password_protected_pdf(tmp_path: Path) -> None:
    doc = pymupdf.open()
    doc.new_page().insert_text((72, 72), "secret")
    path = tmp_path / "locked.pdf"
    doc.save(path, encryption=pymupdf.PDF_ENCRYPT_AES_256, user_pw="u", owner_pw="o")
    doc.close()
    assert _error_code(path) == "encrypted"


def test_too_many_pages(make_pdf: MakePdf) -> None:
    path = make_pdf(["one", "two", "three"])
    assert _error_code(path, max_pages=2) == "too_many_pages"


def test_pdf_without_any_text(make_pdf: MakePdf) -> None:
    path = make_pdf(["", ""])
    assert _error_code(path) == "no_extractable_text"


def test_scanned_pdf_is_detected(image_only_pdf: Path) -> None:
    # A scanned-only PDF has no text, so it is rejected with a clear reason.
    assert _error_code(image_only_pdf) == "no_extractable_text"


def test_mixed_pdf_warns_about_scanned_page(tmp_path: Path) -> None:
    doc = pymupdf.open()
    doc.new_page().insert_text((72, 72), "Normal text page about coverage limits.")
    scanned = doc.new_page()
    pixmap = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 50, 50), False)
    scanned.insert_image(scanned.rect, pixmap=pixmap)
    path = tmp_path / "mixed.pdf"
    doc.save(path)
    doc.close()

    result = read_pdf(path)

    assert any(w.startswith("likely_scanned: page 2") for w in result.warnings)
