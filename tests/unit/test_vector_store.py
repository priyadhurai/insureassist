"""Tests for the vector store. EVERY test runs twice: once with FAISS and once with Qdrant
(Qdrant's local mode stores data in a folder, so no server is needed for tests).
Same tests, same expectations: this is what makes the two backends interchangeable."""

from collections.abc import Callable
from pathlib import Path

import numpy as np
import pytest
from qdrant_client import QdrantClient

from insureassist.ingestion.chunker import chunk_document
from insureassist.ingestion.embedder import HashEmbedder
from insureassist.ingestion.pdf_reader import Page, PdfDocument
from insureassist.ingestion.qdrant_store import QdrantStore, QdrantUnavailableError, point_id
from insureassist.ingestion.vector_db import VectorDB
from insureassist.ingestion.vector_store import (
    DuplicateDocumentError,
    IncompatibleIndexError,
    VectorStore,
)

EMBEDDER = HashEmbedder(dimension=256)
OpenDB = Callable[..., VectorDB]


def _doc(doc_id: str, pages: list[str]) -> PdfDocument:
    return PdfDocument(
        document_id=doc_id,
        checksum_sha256=doc_id * 4,
        filename=f"{doc_id}.pdf",
        pages=[Page(i + 1, text) for i, text in enumerate(pages)],
    )


AUTO = _doc("doc_auto", ["Collision deductible is 500 dollars.", "Rental car paid 40 dollars."])
CLAIMS = _doc("doc_claims", ["Appeal a claim decision within 60 days."])


@pytest.fixture(params=["faiss", "qdrant"])
def open_db(request: pytest.FixtureRequest, tmp_path: Path) -> OpenDB:
    """Returns a function that opens (or re-opens) the store in tmp_path."""

    def factory(model: str = EMBEDDER.model_name) -> VectorDB:
        if request.param == "faiss":
            return VectorStore(tmp_path, model, EMBEDDER.dimension)
        client = QdrantClient(path=str(tmp_path / "qdrant"))  # local mode: no server
        return QdrantStore(client, "test_chunks", model, EMBEDDER.dimension)

    return factory


def _add(store: VectorDB, doc: PdfDocument) -> None:
    chunks = chunk_document(doc)
    store.add_document(doc, chunks, EMBEDDER.embed([c.text for c in chunks]))


def _ask(store: VectorDB, question: str, **kwargs: object) -> list[str]:
    results = store.search(EMBEDDER.embed([question]), **kwargs)  # type: ignore[arg-type]
    return [r.chunk.chunk_id for r in results]


def _reopen(store: VectorDB, open_db: OpenDB) -> VectorDB:
    store.save()
    store.close()  # Qdrant local mode allows one open client per folder
    return open_db()


def test_search_finds_the_right_chunk_with_page(open_db: OpenDB) -> None:
    store = open_db()
    _add(store, AUTO)
    _add(store, CLAIMS)

    results = store.search(EMBEDDER.embed(["appeal claim decision"]), top_k=1)

    assert results[0].chunk.document_id == "doc_claims"
    assert results[0].chunk.page_number == 1
    assert 0 < results[0].score <= 1.0001


def test_results_are_sorted_best_first(open_db: OpenDB) -> None:
    store = open_db()
    _add(store, AUTO)
    _add(store, CLAIMS)

    scores = [r.score for r in store.search(EMBEDDER.embed(["collision deductible"]), top_k=3)]

    assert scores == sorted(scores, reverse=True)


def test_save_and_reopen_gives_same_results(open_db: OpenDB) -> None:
    store = open_db()
    _add(store, AUTO)
    _add(store, CLAIMS)
    before = _ask(store, "rental car", top_k=3)

    reopened = _reopen(store, open_db)

    assert reopened.count == 3
    assert set(reopened.documents) == {"doc_auto", "doc_claims"}
    assert _ask(reopened, "rental car", top_k=3) == before
    assert [c.chunk_id for c in reopened.chunks] == [  # same, stable chunk order
        "doc_auto-p1-c0",
        "doc_auto-p2-c0",
        "doc_claims-p1-c0",
    ]


def test_same_document_twice_is_rejected(open_db: OpenDB) -> None:
    store = open_db()
    _add(store, AUTO)

    with pytest.raises(DuplicateDocumentError):
        _add(store, AUTO)
    assert store.count == 2  # nothing was added the second time


def test_deleted_document_is_never_returned(open_db: OpenDB) -> None:
    store = open_db()
    _add(store, AUTO)
    _add(store, CLAIMS)

    removed = store.delete_document("doc_auto")
    reopened = _reopen(store, open_db)

    assert removed == 2
    assert reopened.count == len(reopened.chunks) == 1
    assert all("doc_auto" not in cid for cid in _ask(reopened, "collision deductible", top_k=10))
    assert reopened.delete_document("doc_unknown") == 0


def test_filter_only_returns_allowed_documents(open_db: OpenDB) -> None:
    store = open_db()
    _add(store, AUTO)
    _add(store, CLAIMS)

    # The question matches the auto policy best, but the user may only read the claims guide.
    ids = _ask(store, "collision deductible", top_k=5, allowed_document_ids={"doc_claims"})

    assert ids == ["doc_claims-p1-c0"]
    assert _ask(store, "anything", allowed_document_ids=set()) == []


def test_top_k_larger_than_store(open_db: OpenDB) -> None:
    store = open_db()
    _add(store, CLAIMS)
    assert len(_ask(store, "appeal", top_k=50)) == 1


def test_empty_store_returns_nothing(open_db: OpenDB) -> None:
    assert _ask(open_db(), "anything") == []


def test_index_from_another_model_is_refused(open_db: OpenDB) -> None:
    store = open_db()
    _add(store, CLAIMS)
    store.save()
    store.close()

    with pytest.raises(IncompatibleIndexError):
        open_db(model="some-other-model")


def test_vector_count_must_match_chunks(open_db: OpenDB) -> None:
    store = open_db()
    chunks = chunk_document(AUTO)
    with pytest.raises(ValueError):
        store.add_document(AUTO, chunks, np.zeros((1, 256), dtype=np.float32))


def test_all_vectors_follow_chunk_order(open_db: OpenDB) -> None:
    store = open_db()
    _add(store, AUTO)
    _add(store, CLAIMS)

    vectors = store.all_vectors()

    assert vectors.shape == (3, EMBEDDER.dimension)
    expected = EMBEDDER.embed([c.text for c in store.chunks])
    np.testing.assert_allclose(vectors, expected, atol=1e-5)
    assert store.describe()[0][0] == "backend"


# --- backend-specific -----------------------------------------------------------------


def test_faiss_writes_files_atomically(tmp_path: Path) -> None:
    store = VectorStore(tmp_path, EMBEDDER.model_name, EMBEDDER.dimension)
    _add(store, AUTO)
    store.save()
    assert not list(tmp_path.glob("*.tmp"))  # temporary files were renamed


def test_qdrant_point_ids_are_stable() -> None:
    assert point_id("doc_auto-p1-c0") == point_id("doc_auto-p1-c0")
    assert point_id("doc_auto-p1-c0") != point_id("doc_auto-p1-c1")


def test_qdrant_unreachable_gives_clear_message() -> None:
    with pytest.raises(QdrantUnavailableError, match="docker compose up -d qdrant"):
        QdrantStore.connect("http://127.0.0.1:1", "x", EMBEDDER.model_name, EMBEDDER.dimension)
