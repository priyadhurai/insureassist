"""Tests for B2 keyword search (BM25)."""

import pytest

from insureassist.ingestion.chunker import Chunk
from insureassist.retrieval.keyword_search import KeywordIndex, tokenize


def _chunk(doc: str, page: int, text: str) -> Chunk:
    return Chunk(f"{doc}-p{page}-c0", doc, page, 0, text, 0, len(text))


CHUNKS = [
    _chunk("auto", 1, "A covered auto includes a newly acquired auto reported within 14 days."),
    _chunk(
        "auto", 2, "Ridesharing is excluded unless the Rideshare Endorsement Form AP-RS-12 applies."
    ),
    _chunk("auto", 3, "The collision deductible is $500 per claim. Rental cars up to $40 per day."),
    _chunk("home", 3, "Flood is excluded. Flood coverage requires Form HO-17."),
    _chunk("travel", 1, "Emergency medical evacuation is covered up to $250,000."),
]


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Form AP-RS-12", ["form", "ap-rs-12", "ap", "rs", "12"]),
        ("What is the $1,500 limit?", ["1500", "limit"]),
        ("FLOOD   Damage", ["flood", "damage"]),
        ("Is it not covered?", ["not", "covered"]),  # "not" is kept on purpose
        ("Section 4.2", ["section", "4.2", "4", "2"]),
        ("", []),
    ],
)
def test_tokenize(text: str, expected: list[str]) -> None:
    assert tokenize(text) == expected


def _ids(results: list) -> list[str]:  # type: ignore[type-arg]
    return [r.chunk.chunk_id for r in results]


def test_exact_form_number_is_found_first() -> None:
    index = KeywordIndex(CHUNKS)
    assert _ids(index.search("What does Form AP-RS-12 cover?", top_k=1)) == ["auto-p2-c0"]
    assert _ids(index.search("What is HO-17?", top_k=1)) == ["home-p3-c0"]


def test_rare_word_beats_common_word() -> None:
    index = KeywordIndex(CHUNKS)
    # "evacuation" appears once in the corpus, so it dominates the ranking.
    assert _ids(index.search("is evacuation covered", top_k=1)) == ["travel-p1-c0"]


def test_numbers_with_commas_match() -> None:
    index = KeywordIndex(CHUNKS)
    assert _ids(index.search("limit of 250000", top_k=1)) == ["travel-p1-c0"]


def test_no_shared_words_returns_nothing() -> None:
    assert KeywordIndex(CHUNKS).search("capital of France") == []


def test_only_allowed_documents_are_returned() -> None:
    index = KeywordIndex(CHUNKS)
    results = index.search("excluded form", top_k=10, allowed_document_ids={"home"})
    assert {r.chunk.document_id for r in results} == {"home"}
    assert index.search("flood", allowed_document_ids=set()) == []


def test_results_are_sorted_and_repeatable() -> None:
    index = KeywordIndex(CHUNKS)
    first = index.search("covered excluded auto", top_k=5)
    assert [r.score for r in first] == sorted((r.score for r in first), reverse=True)
    assert _ids(first) == _ids(index.search("covered excluded auto", top_k=5))


def test_empty_index() -> None:
    assert KeywordIndex([]).search("anything") == []


def test_word_in_half_of_the_chunks_still_matches() -> None:
    """Regression: BM25Okapi gives a word in exactly half the chunks a weight of 0.
    The chunk must still count as a match (it shares the word)."""
    index = KeywordIndex([_chunk("a", 1, "Form AP-RS-12 applies."), _chunk("b", 1, "Flood.")])
    assert _ids(index.search("AP-RS-12", top_k=5)) == ["a-p1-c0"]
