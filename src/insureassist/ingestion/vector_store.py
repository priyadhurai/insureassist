"""Ingestion step 4: store chunk vectors in FAISS and find the most similar ones.

FAISS only stores vectors and finds the nearest ones fast. It knows nothing about
text, pages or documents. So we keep three files side by side:

    data/index/
      index.faiss     the vectors (row 0, row 1, ...)
      chunks.jsonl    chunk details; line N describes FAISS row N (text, document, page)
      info.json       which embedding model built the index + the list of documents

The link between FAISS and our chunks is simply the position: FAISS row N <-> chunks[N].

Index type: IndexFlatIP = compare the question with EVERY vector (exact, no approximation)
using the inner product. Because vectors have length 1, inner product = cosine similarity.
Exact search is fast enough for tens of thousands of chunks.
"""

import json
import os
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import faiss
import numpy as np

from insureassist.ingestion.chunker import Chunk
from insureassist.ingestion.embedder import Vectors
from insureassist.ingestion.pdf_reader import PdfDocument

INDEX_FILE = "index.faiss"
CHUNKS_FILE = "chunks.jsonl"
INFO_FILE = "info.json"


class DuplicateDocumentError(Exception):
    """The same file (same content) is already in the store."""


class IncompatibleIndexError(Exception):
    """The saved index was built with a different embedding model, so its vectors
    cannot be compared with vectors from the current model. Rebuild the index."""


@dataclass
class DocumentRecord:
    document_id: str
    filename: str
    checksum_sha256: str
    page_count: int
    chunk_count: int
    warnings: list[str] = field(default_factory=list)
    ingested_at: str = ""


@dataclass
class SearchResult:
    chunk: Chunk
    score: float  # cosine similarity: higher = more similar


class VectorStore:
    """FAISS-backed store (files on disk, no server). See qdrant_store.py for the
    Qdrant version; both follow the VectorDB protocol in vector_db.py."""

    backend = "faiss"

    def __init__(self, folder: Path, embedding_model: str, dimension: int) -> None:
        """Open the store in `folder`, or start an empty one if nothing is saved there yet."""
        self.folder = folder
        self.embedding_model = embedding_model
        self.dimension = dimension
        self.index: faiss.Index = faiss.IndexFlatIP(dimension)
        self.chunks: list[Chunk] = []
        self.documents: dict[str, DocumentRecord] = {}
        if (folder / INFO_FILE).exists():
            self._load()

    # --- information -------------------------------------------------------------

    @property
    def count(self) -> int:
        """Number of stored vectors (= chunks)."""
        return int(self.index.ntotal)

    def all_vectors(self) -> Vectors:
        """Every stored vector, in the same order as self.chunks."""
        return np.asarray(self.index.reconstruct_n(0, self.index.ntotal), dtype=np.float32)

    def describe(self) -> list[tuple[str, str]]:
        """Where and how the data is stored, for display."""
        rows = [("backend", "FAISS IndexFlatIP (exact search), files on disk")]
        for name, holds in (
            (INDEX_FILE, "the vectors"),
            (CHUNKS_FILE, "chunk text, document, page (line N = row N)"),
            (INFO_FILE, "embedding model + documents"),
        ):
            path = self.folder / name
            size = path.stat().st_size if path.exists() else 0
            rows.append((str(path), f"{size:,} bytes: {holds}"))
        return rows

    def close(self) -> None:
        """Nothing to release for files (kept so both backends share one interface)."""

    # --- writing ---------------------------------------------------------------

    def has_document(self, document_id: str) -> bool:
        return document_id in self.documents

    def add_document(self, doc: PdfDocument, chunks: list[Chunk], vectors: Vectors) -> None:
        """Add one document's chunks and their vectors (same order). Call save() afterwards."""
        if self.has_document(doc.document_id):
            raise DuplicateDocumentError(f"{doc.filename} is already ingested")
        if len(chunks) != len(vectors):
            raise ValueError("need exactly one vector per chunk")
        if len(vectors) and vectors.shape[1] != self.dimension:
            raise ValueError(f"vectors must have {self.dimension} numbers")

        self.index.add(np.ascontiguousarray(vectors, dtype=np.float32))
        self.chunks.extend(chunks)  # same order as the vectors -> row N matches chunk N
        self.documents[doc.document_id] = DocumentRecord(
            document_id=doc.document_id,
            filename=doc.filename,
            checksum_sha256=doc.checksum_sha256,
            page_count=doc.page_count,
            chunk_count=len(chunks),
            warnings=doc.warnings,
            ingested_at=datetime.now(UTC).isoformat(timespec="seconds"),
        )

    def delete_document(self, document_id: str) -> int:
        """Remove a document and all its vectors. Returns the number of chunks removed.

        Simple approach: rebuild the index from the vectors we keep. Fine at our scale.
        """
        if not self.has_document(document_id):
            return 0
        keep = [i for i, c in enumerate(self.chunks) if c.document_id != document_id]
        all_vectors = self.index.reconstruct_n(0, self.index.ntotal)

        self.index = faiss.IndexFlatIP(self.dimension)
        if keep:
            self.index.add(np.ascontiguousarray(all_vectors[keep]))
        removed = len(self.chunks) - len(keep)
        self.chunks = [self.chunks[i] for i in keep]
        del self.documents[document_id]
        return removed

    # --- searching -------------------------------------------------------------

    def search(
        self,
        query_vector: Vectors,
        top_k: int = 5,
        allowed_document_ids: set[str] | None = None,
    ) -> list[SearchResult]:
        """Return the `top_k` chunks most similar to the query, best first.

        `allowed_document_ids` restricts the search to those documents (None = all).
        This is how users will later only see documents they are allowed to read:
        FAISS skips the other rows entirely, so their text can never be returned.
        """
        if self.index.ntotal == 0 or top_k <= 0:
            return []

        params = None
        if allowed_document_ids is not None:
            allowed_rows = np.array(
                [i for i, c in enumerate(self.chunks) if c.document_id in allowed_document_ids],
                dtype=np.int64,
            )
            if len(allowed_rows) == 0:
                return []
            params = faiss.SearchParameters()
            params.sel = faiss.IDSelectorBatch(allowed_rows)  # search ONLY these rows

        k = min(top_k, self.index.ntotal)
        query = np.ascontiguousarray(query_vector.reshape(1, -1), dtype=np.float32)
        scores, rows = self.index.search(query, k, params=params)

        # FAISS fills empty slots with row -1 when fewer than k rows are allowed.
        return [
            SearchResult(chunk=self.chunks[row], score=float(score))
            for score, row in zip(scores[0], rows[0], strict=True)
            if row != -1
        ]

    # --- saving and loading ----------------------------------------------------

    def save(self) -> None:
        """Write the three files. Each file is written to a temporary name first and then
        renamed, so a crash mid-write never leaves a half-written file behind."""
        self.folder.mkdir(parents=True, exist_ok=True)

        faiss.write_index(self.index, str(self.folder / f"{INDEX_FILE}.tmp"))
        chunk_lines = "".join(json.dumps(asdict(c)) + "\n" for c in self.chunks)
        (self.folder / f"{CHUNKS_FILE}.tmp").write_text(chunk_lines, encoding="utf-8")
        info = {
            "embedding_model": self.embedding_model,
            "dimension": self.dimension,
            "documents": [asdict(d) for d in self.documents.values()],
        }
        (self.folder / f"{INFO_FILE}.tmp").write_text(json.dumps(info, indent=2), encoding="utf-8")

        for name in (INDEX_FILE, CHUNKS_FILE, INFO_FILE):
            os.replace(self.folder / f"{name}.tmp", self.folder / name)

    def _load(self) -> None:
        info = json.loads((self.folder / INFO_FILE).read_text(encoding="utf-8"))
        if info["embedding_model"] != self.embedding_model or info["dimension"] != self.dimension:
            raise IncompatibleIndexError(
                f"Index was built with {info['embedding_model']} but the current model is "
                f"{self.embedding_model}. Delete {self.folder} and ingest again."
            )
        self.index = faiss.read_index(str(self.folder / INDEX_FILE))
        lines = (self.folder / CHUNKS_FILE).read_text(encoding="utf-8").splitlines()
        self.chunks = [Chunk(**json.loads(line)) for line in lines]
        self.documents = {d["document_id"]: DocumentRecord(**d) for d in info["documents"]}
        if self.index.ntotal != len(self.chunks):
            raise IncompatibleIndexError(
                f"{INDEX_FILE} has {self.index.ntotal} vectors but {CHUNKS_FILE} has "
                f"{len(self.chunks)} chunks. The files are out of sync; ingest again."
            )
