"""Ingestion step 2: clean each page's text and cut it into overlapping chunks.

Why chunks? The embedding model reads only ~256 tokens at a time, and small pieces
give more precise search results and citations.

Rules:
  * A chunk never crosses a page boundary, so every chunk has exactly ONE page number.
  * Chunks are built from whole sentences, up to `size` characters.
  * The next chunk repeats the last sentence(s) of the previous one (up to `overlap`
    characters), so a fact sitting on a chunk boundary is not lost.
  * Each chunk's text is an exact slice of the cleaned page text
    (page_text[char_start:char_end]), so a citation can always be traced back.
"""

import re
import unicodedata
from dataclasses import dataclass

from insureassist.config import settings
from insureassist.ingestion.pdf_reader import PdfDocument

# Words ending in "." that do NOT end a sentence. Without this list,
# "see Sec. 4.2" would be split into two sentences.
ABBREVIATIONS = {
    "e.g.", "i.e.", "etc.", "vs.", "no.", "nos.", "sec.", "art.", "para.", "fig.",
    "inc.", "co.", "corp.", "ltd.", "mr.", "mrs.", "ms.", "dr.", "st.", "approx.",
}  # fmt: skip

# Sentence end: . ! or ? (optionally followed by a closing quote/bracket), then whitespace.
SENTENCE_END = re.compile(r"[.!?][\"')\]]*(?=\s)")

Span = tuple[int, int]  # (start, end) character positions in the page text


@dataclass
class Chunk:
    chunk_id: str
    document_id: str
    page_number: int
    chunk_index: int  # position of the chunk within its page: 0, 1, 2, ...
    text: str
    char_start: int  # text == cleaned_page_text[char_start:char_end]
    char_end: int


# --- 1. cleaning -------------------------------------------------------------


def normalize_text(text: str) -> str:
    """Clean raw PDF text so that equal words look equal (better search, nicer chunks)."""
    # "ﬁ" ligature -> "fi", full-width characters -> normal ones, non-breaking space -> space.
    text = unicodedata.normalize("NFKC", text)
    # Invisible characters: soft hyphen and zero-width spaces.
    text = re.sub("[­​‌‍﻿]", "", text)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+", " ", text)  # many spaces -> one space
    text = re.sub(r" *\n *", "\n", text)  # no spaces around line breaks
    # Word split across lines: "cover-\nage" -> "coverage" (lowercase on both sides only,
    # so a real hyphenated name like "Blue-\nCross" is left alone).
    text = re.sub(r"([a-z])-\n([a-z])", r"\1\2", text)
    text = re.sub(r"\n{2,}", "\n\n", text)  # a blank line = paragraph break
    text = re.sub(r"(?<!\n)\n(?!\n)", " ", text)  # single line break = wrapped line -> space
    return text.strip()


# --- 2. sentences ------------------------------------------------------------


def sentence_spans(text: str, max_len: int) -> list[Span]:
    """Return (start, end) of every sentence. Sentences longer than max_len are split."""
    spans: list[Span] = []
    # After normalize_text, paragraphs are lines separated by "\n\n".
    for paragraph in re.finditer(r"[^\n]+", text):
        start = paragraph.start()
        for match in SENTENCE_END.finditer(text, paragraph.start(), paragraph.end()):
            end = match.end()
            last_word = text[text.rfind(" ", start, end) + 1 : end].lower()
            if last_word in ABBREVIATIONS:
                continue  # "Sec." is not the end of a sentence
            spans.append((start, end))
            start = _skip_spaces(text, end)
        if start < paragraph.end():
            spans.append((start, paragraph.end()))  # last sentence without a final "."

    result: list[Span] = []
    for span in spans:
        result.extend(_split_long_span(text, span, max_len))
    return result


def _skip_spaces(text: str, pos: int) -> int:
    while pos < len(text) and text[pos] == " ":
        pos += 1
    return pos


def _split_long_span(text: str, span: Span, max_len: int) -> list[Span]:
    """Split a very long sentence at spaces (or hard-cut if there is no space)."""
    start, end = span
    pieces: list[Span] = []
    while end - start > max_len:
        cut = text.rfind(" ", start, start + max_len + 1)
        if cut <= start:
            cut = start + max_len  # no space at all: cut mid-word
        pieces.append((start, cut))
        start = _skip_spaces(text, cut)
    pieces.append((start, end))
    return pieces


# --- 3. chunks ---------------------------------------------------------------


def chunk_page(
    page_text: str,
    size: int,
    overlap: int,
    min_chars: int,
) -> list[Span]:
    """Group sentences into chunks of at most `size` characters. Returns chunk spans."""
    if size <= 0 or not 0 <= overlap < size:
        raise ValueError("need size > 0 and 0 <= overlap < size")
    sentences = sentence_spans(page_text, size)
    chunks: list[Span] = []
    first = 0  # index of the first sentence of the current chunk

    while first < len(sentences):
        # Add sentences while the chunk still fits.
        last = first
        while last + 1 < len(sentences) and sentences[last + 1][1] - sentences[first][0] <= size:
            last += 1
        chunks.append((sentences[first][0], sentences[last][1]))

        if last == len(sentences) - 1:
            break  # all sentences used

        # Start the next chunk a few sentences back to create the overlap, but
        # (a) keep the repeated part within `overlap` characters, and
        # (b) make sure the next chunk still has room for at least one NEW sentence.
        next_first = last + 1
        while (
            next_first - 1 > first
            and sentences[last][1] - sentences[next_first - 1][0] <= overlap
            and sentences[last + 1][1] - sentences[next_first - 1][0] <= size
        ):
            next_first -= 1
        first = next_first

    # A tiny final piece is merged into the chunk before it, if the result still fits.
    if len(chunks) >= 2:
        tail_start, tail_end = chunks[-1]
        prev_start, _ = chunks[-2]
        if tail_end - tail_start < min_chars and tail_end - prev_start <= size:
            chunks[-2:] = [(prev_start, tail_end)]
    return chunks


def chunk_document(
    doc: PdfDocument,
    size: int | None = None,
    overlap: int | None = None,
    min_chars: int | None = None,
) -> list[Chunk]:
    """Clean and chunk every page of a document."""
    size = size if size is not None else settings.chunk_size_chars
    overlap = overlap if overlap is not None else settings.chunk_overlap_chars
    min_chars = min_chars if min_chars is not None else settings.chunk_min_chars

    chunks: list[Chunk] = []
    for page in doc.pages:
        clean_text = normalize_text(page.text)
        for index, (start, end) in enumerate(chunk_page(clean_text, size, overlap, min_chars)):
            chunks.append(
                Chunk(
                    # Readable and deterministic: same document + same settings -> same IDs.
                    chunk_id=f"{doc.document_id}-p{page.page_number}-c{index}",
                    document_id=doc.document_id,
                    page_number=page.page_number,
                    chunk_index=index,
                    text=clean_text[start:end],
                    char_start=start,
                    char_end=end,
                )
            )
    return chunks
