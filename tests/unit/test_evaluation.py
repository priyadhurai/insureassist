"""Tests for the retrieval metrics (hand-computed values) and the evaluation runner."""

import math
from pathlib import Path

import pytest

from insureassist.evaluation.metrics import hit_at_k, ndcg_at_k, recall_at_k, reciprocal_rank
from insureassist.evaluation.retrieval_eval import (
    DatasetError,
    Question,
    check_labels,
    evaluate,
    load_questions,
)
from insureassist.ingestion.chunker import chunk_document
from insureassist.ingestion.embedder import HashEmbedder
from insureassist.ingestion.pdf_reader import Page, PdfDocument
from insureassist.ingestion.vector_store import SearchResult, VectorStore

A3, B2, C1, D4 = ("a.pdf", 3), ("b.pdf", 2), ("c.pdf", 1), ("d.pdf", 4)


# --- metrics (worked examples) -------------------------------------------------------


def test_recall_and_hit() -> None:
    retrieved = [B2, A3, A3, C1]
    assert recall_at_k(retrieved, {A3}, 1) == 0.0
    assert recall_at_k(retrieved, {A3}, 3) == 1.0
    assert recall_at_k(retrieved, {A3, D4}, 10) == 0.5  # found one of two
    assert hit_at_k(retrieved, {A3, D4}, 2) == 1.0
    assert hit_at_k(retrieved, {D4}, 10) == 0.0


def test_reciprocal_rank() -> None:
    assert reciprocal_rank([A3, B2], {A3}) == 1.0
    assert reciprocal_rank([B2, C1, A3], {A3}) == pytest.approx(1 / 3)
    assert reciprocal_rank([B2, C1], {A3}) == 0.0


def test_ndcg() -> None:
    assert ndcg_at_k([A3, B2], {A3}, 5) == 1.0  # perfect: correct page first
    # correct page at rank 2: (1/log2(3)) / (1/log2(2)) = 0.6309
    assert ndcg_at_k([B2, A3], {A3}, 5) == pytest.approx(1 / math.log2(3))
    # the same page twice is only credited once
    assert ndcg_at_k([A3, A3], {A3, D4}, 5) == pytest.approx(1 / (1 + 1 / math.log2(3)))
    assert ndcg_at_k([B2], set(), 5) == 0.0


# --- runner ---------------------------------------------------------------------------

EMBEDDER = HashEmbedder(dimension=256)


@pytest.fixture
def store(tmp_path: Path) -> VectorStore:
    s = VectorStore(tmp_path, EMBEDDER.model_name, EMBEDDER.dimension)
    doc = PdfDocument(
        "doc_auto",
        "x",
        "auto.pdf",
        [Page(1, "Covered auto definition."), Page(2, "Collision deductible is $500.")],
    )
    chunks = chunk_document(doc)
    s.add_document(doc, chunks, EMBEDDER.embed([c.text for c in chunks]))
    return s


def _q(qid: str, page: int, fact: str) -> Question:
    return Question.model_validate(
        {
            "id": qid,
            "category": "factual",
            "question": "collision deductible",
            "relevant": [{"doc": "auto.pdf", "page": page}],
            "key_facts": [fact],
        }
    )


def test_check_labels_accepts_correct_labels(store: VectorStore) -> None:
    check_labels([_q("q1", 2, "$500")], store)  # no error


def test_check_labels_catches_wrong_page_and_missing_fact(store: VectorStore) -> None:
    with pytest.raises(DatasetError) as info:
        check_labels([_q("q1", 9, "$500"), _q("q2", 1, "$500")], store)
    message = str(info.value)
    assert "q1: auto.pdf page 9 is not in the store" in message
    assert "q2: key fact '$500' not found" in message


def test_evaluate_scores_and_keeps_unanswerable_separate(store: VectorStore) -> None:
    unanswerable = Question.model_validate(
        {
            "id": "u1",
            "category": "unanswerable",
            "question": "pets",
            "relevant": [],
            "key_facts": [],
        }
    )

    def retrieve(question: str, k: int) -> list[SearchResult]:
        return store.search(EMBEDDER.embed([question]), k)

    report = evaluate(
        "test", [_q("q1", 2, "$500"), unanswerable], retrieve, {"doc_auto": "auto.pdf"}, "ds", {}
    )

    assert [q.id for q in report.questions] == ["q1"]  # unanswerable not in the averages
    assert report.averages["recall@1"] == 1.0
    assert report.averages["mrr"] == 1.0
    assert report.questions[0].retrieved[0] == "auto.pdf p2"
    assert "max" in report.best_score_unanswerable


def test_real_dataset_file_is_well_formed() -> None:
    questions = load_questions(Path("eval/datasets/insureassist-eval-v2/questions.jsonl"))
    assert len(questions) == 28
    assert len({q.id for q in questions}) == 28  # ids are unique
    assert sum(1 for q in questions if not q.answerable) == 5
