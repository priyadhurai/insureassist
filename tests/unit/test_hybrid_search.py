"""Tests for B3 hybrid search (Reciprocal Rank Fusion + Searcher)."""

from pathlib import Path

import pytest

from insureassist.ingestion.chunker import Chunk, chunk_document
from insureassist.ingestion.embedder import HashEmbedder
from insureassist.ingestion.pdf_reader import Page, PdfDocument
from insureassist.ingestion.vector_store import SearchResult, VectorStore
from insureassist.retrieval.search import Searcher, reciprocal_rank_fusion


def _r(chunk_id: str) -> SearchResult:
    return SearchResult(Chunk(chunk_id, "doc", 1, 0, chunk_id, 0, 1), score=0.0)


def _ids(results: list[SearchResult]) -> list[str]:
    return [r.chunk.chunk_id for r in results]


# --- Reciprocal Rank Fusion (hand-computed) ---------------------------------------------


def test_rrf_scores_match_the_formula() -> None:
    semantic = [_r("A"), _r("B"), _r("C")]
    keyword = [_r("C"), _r("A")]

    fused = reciprocal_rank_fusion([semantic, keyword], top_k=3, rrf_k=60)

    # A: 1/61 + 1/62,  C: 1/63 + 1/61,  B: 1/62 only
    assert _ids(fused) == ["A", "C", "B"]
    assert fused[0].score == pytest.approx(1 / 61 + 1 / 62)
    assert fused[2].score == pytest.approx(1 / 62)


def test_rrf_found_by_both_beats_found_by_one() -> None:
    fused = reciprocal_rank_fusion([[_r("X"), _r("Y")], [_r("Y")]], top_k=2)
    assert _ids(fused) == ["Y", "X"]  # Y: 1/62 + 1/61 > X: 1/61


def test_rrf_ties_are_broken_deterministically() -> None:
    # X is 1st in list one, Y is 1st in list two -> equal scores, order by chunk id.
    fused = reciprocal_rank_fusion([[_r("Y")], [_r("X")]], top_k=2)
    assert _ids(fused) == ["X", "Y"]


def test_rrf_respects_top_k_and_empty_lists() -> None:
    assert len(reciprocal_rank_fusion([[_r("A"), _r("B")], []], top_k=1)) == 1
    assert reciprocal_rank_fusion([[], []], top_k=5) == []


# --- Searcher -------------------------------------------------------------------------

EMBEDDER = HashEmbedder(dimension=256)


@pytest.fixture
def store(tmp_path: Path) -> VectorStore:
    s = VectorStore(tmp_path, EMBEDDER.model_name, EMBEDDER.dimension)
    for doc_id, text in [
        ("auto", "Ridesharing requires the endorsement Form AP-RS-12."),
        ("home", "Flood damage is excluded unless Form HO-17 is purchased."),
    ]:
        doc = PdfDocument(doc_id, doc_id, f"{doc_id}.pdf", [Page(1, text)])
        chunks = chunk_document(doc)
        s.add_document(doc, chunks, EMBEDDER.embed([c.text for c in chunks]))
    return s


@pytest.mark.parametrize("mode", ["semantic", "keyword", "hybrid"])
def test_every_mode_respects_the_document_filter(store: VectorStore, mode: str) -> None:
    searcher = Searcher(store, EMBEDDER, mode=mode)  # type: ignore[arg-type]
    retrieval = searcher.search("Form AP-RS-12 flood", 10, allowed_document_ids={"home"})
    assert {r.chunk.document_id for r in retrieval.results} == {"home"}


def test_searcher_reports_signals_for_abstaining(store: VectorStore) -> None:
    searcher = Searcher(store, EMBEDDER, mode="hybrid")

    match = searcher.search("AP-RS-12", 5)
    no_match = searcher.search("capital of France", 5)

    assert match.keyword_matches == 1
    assert match.results[0].chunk.document_id == "auto"
    assert no_match.keyword_matches == 0
    assert 0.0 <= no_match.best_semantic_score <= 1.0
