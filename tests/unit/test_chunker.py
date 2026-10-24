"""Tests for ingestion step 2 (chunker)."""

import random

import pytest

from insureassist.ingestion.chunker import (
    chunk_document,
    chunk_page,
    normalize_text,
    sentence_spans,
)
from insureassist.ingestion.pdf_reader import Page, PdfDocument

# --- cleaning -----------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("ﬁre damage", "fire damage"),  # ligature
        ("cover­age", "coverage"),  # soft hyphen
        ("zero​width", "zerowidth"),  # invisible character
        ("cover-\nage applies", "coverage applies"),  # word split across lines
        ("Blue-\nCross", "Blue- Cross"),  # capital after hyphen: not joined
        ("line one\nline two", "line one line two"),  # wrapped line
        ("para one\n\n\npara two", "para one\n\npara two"),  # paragraph kept
        ("too    many \t spaces", "too many spaces"),
        ("  trim me  ", "trim me"),
    ],
)
def test_normalize_text(raw: str, expected: str) -> None:
    assert normalize_text(raw) == expected


# --- sentences ----------------------------------------------------------------


def _sentences(text: str) -> list[str]:
    return [text[s:e] for s, e in sentence_spans(text, max_len=1000)]


def test_sentences_are_split_on_punctuation() -> None:
    assert _sentences("First one. Second one! Third?") == ["First one.", "Second one!", "Third?"]


def test_abbreviations_do_not_end_a_sentence() -> None:
    text = "See Sec. 4 for details, e.g. floods. Next sentence."
    assert _sentences(text) == ["See Sec. 4 for details, e.g. floods.", "Next sentence."]


def test_long_sentence_is_split_at_spaces() -> None:
    text = "word " * 50  # 250 characters, no sentence end
    pieces = [text[s:e] for s, e in sentence_spans(text.strip(), max_len=60)]
    assert all(len(p) <= 60 for p in pieces)
    assert all(not p.startswith(" ") for p in pieces)


def test_text_without_spaces_is_hard_cut() -> None:
    pieces = sentence_spans("x" * 250, max_len=100)
    assert [e - s for s, e in pieces] == [100, 100, 50]


# --- chunks -------------------------------------------------------------------

SENTENCE = "The policy covers accidental damage to the insured vehicle. "  # 61 chars


def test_short_page_is_one_chunk() -> None:
    assert chunk_page("Just one short sentence.", size=800, overlap=120, min_chars=50) == [(0, 24)]


def test_empty_page_has_no_chunks() -> None:
    assert chunk_page("", size=800, overlap=120, min_chars=50) == []


def test_long_page_gives_overlapping_chunks_within_size() -> None:
    text = (SENTENCE * 40).strip()
    spans = chunk_page(text, size=300, overlap=80, min_chars=20)

    assert len(spans) > 1
    assert all(end - start <= 300 for start, end in spans)
    # Every next chunk starts before the previous one ends -> they overlap...
    for (_, prev_end), (next_start, _) in zip(spans, spans[1:], strict=False):
        assert next_start < prev_end
    # ...and together they cover the whole page.
    assert spans[0][0] == 0
    assert spans[-1][1] == len(text)


def test_zero_overlap_gives_touching_chunks() -> None:
    text = (SENTENCE * 20).strip()
    spans = chunk_page(text, size=200, overlap=0, min_chars=0)
    for (_, prev_end), (next_start, _) in zip(spans, spans[1:], strict=False):
        assert next_start >= prev_end


def test_tiny_last_piece_is_merged() -> None:
    text = "A" * 70 + ". " + "B" * 70 + ". Ok."  # last sentence "Ok." is tiny
    spans = chunk_page(text, size=160, overlap=0, min_chars=10)
    assert spans[-1][1] == len(text)
    assert all(end - start >= 10 for start, end in spans)


def test_invalid_overlap_is_rejected() -> None:
    with pytest.raises(ValueError):
        chunk_page("text", size=100, overlap=100, min_chars=0)


def test_random_texts_always_finish_and_respect_size() -> None:
    """Safety net: many random inputs must never loop forever or exceed the size."""
    rng = random.Random(42)  # fixed seed -> repeatable
    words = ["claim", "deductible", "Sec.", "policy.", "x" * 40, "limit!", "e.g.", "a"]
    for _ in range(300):
        text = " ".join(rng.choice(words) for _ in range(rng.randint(0, 120)))
        size = rng.randint(20, 300)
        overlap = rng.randint(0, size - 1)
        spans = chunk_page(text, size=size, overlap=overlap, min_chars=rng.randint(0, 30))
        assert all(0 < end - start <= size for start, end in spans)
        if text:
            assert spans[-1][1] == len(text)


# --- whole document -----------------------------------------------------------


def _document() -> PdfDocument:
    return PdfDocument(
        document_id="doc_abc",
        checksum_sha256="abc",
        filename="policy.pdf",
        pages=[
            Page(1, "Page one about fire cover.\nIt is wrapped."),
            Page(2, ""),  # empty page -> no chunks
            Page(3, (SENTENCE * 30).strip()),
        ],
    )


def test_chunks_keep_page_numbers_and_trace_back_to_page_text() -> None:
    chunks = chunk_document(_document(), size=300, overlap=60, min_chars=20)

    assert {c.page_number for c in chunks} == {1, 3}
    assert chunks[0].text == "Page one about fire cover. It is wrapped."
    page3_text = normalize_text(_document().pages[2].text)
    for chunk in (c for c in chunks if c.page_number == 3):
        assert page3_text[chunk.char_start : chunk.char_end] == chunk.text


def test_chunk_ids_are_unique_and_repeatable() -> None:
    first = chunk_document(_document(), size=300, overlap=60, min_chars=20)
    second = chunk_document(_document(), size=300, overlap=60, min_chars=20)

    ids = [c.chunk_id for c in first]
    assert len(ids) == len(set(ids))
    assert ids == [c.chunk_id for c in second]
    assert ids[0] == "doc_abc-p1-c0"
