"""Vector store backed by Qdrant, a real vector database (runs in Docker).

Same methods as the FAISS VectorStore, so the rest of the app doesn't care which one
is used (setting VECTOR_STORE=faiss|qdrant).

What Qdrant adds over FAISS:
  * A server with a web dashboard (http://localhost:6333/dashboard) to browse the data.
  * Each vector ("point") carries its own data ("payload"): chunk text, page, document.
    So there is no separate chunks.jsonl to keep in sync.
  * Filtering by document is done INSIDE the database (permission filter).

Layout: one collection, one point per chunk.
  point id = UUID derived from the chunk id (stable: same chunk -> same point)
  vector   = the chunk's 384 numbers (cosine distance)
  payload  = chunk fields + the embedding model + the document record
"""

import uuid
from dataclasses import asdict
from datetime import UTC, datetime
from typing import Any

import numpy as np
from qdrant_client import QdrantClient
from qdrant_client import models as qm

from insureassist.ingestion.chunker import Chunk
from insureassist.ingestion.embedder import Vectors
from insureassist.ingestion.pdf_reader import PdfDocument
from insureassist.ingestion.vector_store import (
    DocumentRecord,
    DuplicateDocumentError,
    IncompatibleIndexError,
    SearchResult,
)

# Fixed namespace, so a chunk id always maps to the same Qdrant point id.
POINT_NAMESPACE = uuid.UUID("6f1f5a3e-6c2b-4e0f-9a51-7d1c2b0e4a10")
CHUNK_FIELDS = (
    "chunk_id",
    "document_id",
    "page_number",
    "chunk_index",
    "text",
    "char_start",
    "char_end",
)


class QdrantUnavailableError(Exception):
    """The Qdrant server could not be reached."""


def point_id(chunk_id: str) -> str:
    return str(uuid.uuid5(POINT_NAMESPACE, chunk_id))


class QdrantStore:
    backend = "qdrant"

    def __init__(
        self,
        client: QdrantClient,
        collection: str,
        embedding_model: str,
        dimension: int,
        location: str = "",
    ) -> None:
        self._client = client
        self.collection = collection
        self.embedding_model = embedding_model
        self.dimension = dimension
        self.location = location or "in-process"
        self.chunks: list[Chunk] = []
        self.documents: dict[str, DocumentRecord] = {}

        if client.collection_exists(collection):
            self._load()
        else:
            client.create_collection(
                collection,
                vectors_config=qm.VectorParams(size=dimension, distance=qm.Distance.COSINE),
            )
            # An index on document_id makes the permission filter fast on large collections.
            client.create_payload_index(collection, "document_id", qm.PayloadSchemaType.KEYWORD)

    @classmethod
    def connect(
        cls, url: str, collection: str, embedding_model: str, dimension: int
    ) -> "QdrantStore":
        client = QdrantClient(url=url, timeout=10)
        try:
            client.get_collections()  # cheap call to check the server is up
        except Exception as error:  # connection refused, timeout, ...
            raise QdrantUnavailableError(
                f"Qdrant is not reachable at {url}. Start it with: docker compose up -d qdrant"
            ) from error
        return cls(client, collection, embedding_model, dimension, location=url)

    # --- information -------------------------------------------------------------

    @property
    def count(self) -> int:
        return len(self.chunks)

    def all_vectors(self) -> Vectors:
        by_id = {
            point.payload["chunk_id"]: point.vector
            for point in self._scroll(with_vectors=True)
            if point.payload is not None
        }
        return np.asarray([by_id[c.chunk_id] for c in self.chunks], dtype=np.float32).reshape(
            -1, self.dimension
        )

    def describe(self) -> list[tuple[str, str]]:
        return [
            ("backend", "Qdrant (vector database server), cosine distance"),
            ("server", self.location),
            ("collection", f"{self.collection} ({self.count} points)"),
            (
                "dashboard",
                f"{self.location}/dashboard" if self.location.startswith("http") else "-",
            ),
        ]

    def close(self) -> None:
        self._client.close()

    # --- writing ---------------------------------------------------------------

    def has_document(self, document_id: str) -> bool:
        return document_id in self.documents

    def add_document(self, doc: PdfDocument, chunks: list[Chunk], vectors: Vectors) -> None:
        if self.has_document(doc.document_id):
            raise DuplicateDocumentError(f"{doc.filename} is already ingested")
        if len(chunks) != len(vectors):
            raise ValueError("need exactly one vector per chunk")
        if len(vectors) and vectors.shape[1] != self.dimension:
            raise ValueError(f"vectors must have {self.dimension} numbers")

        record = DocumentRecord(
            document_id=doc.document_id,
            filename=doc.filename,
            checksum_sha256=doc.checksum_sha256,
            page_count=doc.page_count,
            chunk_count=len(chunks),
            warnings=doc.warnings,
            ingested_at=datetime.now(UTC).isoformat(timespec="seconds"),
        )
        points = [
            qm.PointStruct(
                id=point_id(chunk.chunk_id),
                vector=vector.tolist(),
                payload={
                    **asdict(chunk),
                    "embedding_model": self.embedding_model,
                    "document": asdict(record),
                },
            )
            for chunk, vector in zip(chunks, vectors, strict=True)
        ]
        self._client.upsert(self.collection, points=points, wait=True)  # wait = saved
        self.chunks.extend(chunks)
        self.documents[doc.document_id] = record

    def delete_document(self, document_id: str) -> int:
        if not self.has_document(document_id):
            return 0
        self._client.delete(
            self.collection,
            points_selector=qm.FilterSelector(filter=_document_filter({document_id})),
            wait=True,
        )
        removed = sum(1 for c in self.chunks if c.document_id == document_id)
        self.chunks = [c for c in self.chunks if c.document_id != document_id]
        del self.documents[document_id]
        return removed

    def save(self) -> None:
        """Nothing to do: Qdrant stores every change immediately (upsert/delete wait=True)."""

    # --- searching -------------------------------------------------------------

    def search(
        self,
        query_vector: Vectors,
        top_k: int = 5,
        allowed_document_ids: set[str] | None = None,
    ) -> list[SearchResult]:
        if self.count == 0 or top_k <= 0:
            return []
        if allowed_document_ids is not None and not allowed_document_ids:
            return []  # allowed to read nothing -> find nothing
        query_filter = (
            None if allowed_document_ids is None else _document_filter(allowed_document_ids)
        )
        hits = self._client.query_points(
            self.collection,
            query=query_vector.reshape(-1).tolist(),
            limit=top_k,
            query_filter=query_filter,  # applied inside Qdrant, before ranking
            with_payload=True,
        ).points
        by_id = {c.chunk_id: c for c in self.chunks}
        return [
            SearchResult(chunk=by_id[hit.payload["chunk_id"]], score=float(hit.score))
            for hit in hits
            if hit.payload is not None and hit.payload["chunk_id"] in by_id
        ]

    # --- loading -----------------------------------------------------------------

    def _scroll(self, with_vectors: bool = False) -> list[Any]:
        """Read every point (Qdrant returns them in pages)."""
        points: list[Any] = []
        offset = None
        while True:
            page, offset = self._client.scroll(
                self.collection,
                limit=256,
                offset=offset,
                with_payload=True,
                with_vectors=with_vectors,
            )
            points.extend(page)
            if offset is None:
                return points

    def _load(self) -> None:
        info = self._client.get_collection(self.collection)
        size = info.config.params.vectors.size  # type: ignore[union-attr]
        if size != self.dimension:
            raise IncompatibleIndexError(
                f"Collection {self.collection} holds {size}-number vectors, the current model "
                f"makes {self.dimension}. Use another collection or ingest again."
            )
        for point in self._scroll():
            payload = point.payload or {}
            if payload.get("embedding_model") != self.embedding_model:
                raise IncompatibleIndexError(
                    f"Collection {self.collection} was built with "
                    f"{payload.get('embedding_model')}, but the current model is "
                    f"{self.embedding_model}. Delete the collection and ingest again."
                )
            self.chunks.append(Chunk(**{field: payload[field] for field in CHUNK_FIELDS}))
            record = DocumentRecord(**payload["document"])
            self.documents[record.document_id] = record
        # Stable order (Qdrant returns points by id): documents by ingestion time, then
        # chunks by document, page and position.
        self.documents = dict(
            sorted(self.documents.items(), key=lambda item: (item[1].ingested_at, item[0]))
        )
        self.chunks.sort(
            key=lambda c: (
                self.documents[c.document_id].ingested_at,
                c.document_id,
                c.page_number,
                c.chunk_index,
            )
        )


def _document_filter(document_ids: set[str]) -> qm.Filter:
    return qm.Filter(
        must=[qm.FieldCondition(key="document_id", match=qm.MatchAny(any=sorted(document_ids)))]
    )
