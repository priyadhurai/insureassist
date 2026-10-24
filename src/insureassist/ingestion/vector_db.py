"""The shape every vector database must have, and one function to open the chosen one.

The CLI, search, evaluation and UI only use this shape, so FAISS and Qdrant are
interchangeable with the setting VECTOR_STORE=faiss|qdrant.
"""

from typing import Protocol

from insureassist.config import settings
from insureassist.ingestion.chunker import Chunk
from insureassist.ingestion.embedder import Embedder, Vectors
from insureassist.ingestion.pdf_reader import PdfDocument
from insureassist.ingestion.vector_store import DocumentRecord, SearchResult, VectorStore


class VectorDB(Protocol):
    backend: str
    embedding_model: str
    dimension: int
    chunks: list[Chunk]
    documents: dict[str, DocumentRecord]

    @property
    def count(self) -> int: ...

    def has_document(self, document_id: str) -> bool: ...

    def add_document(self, doc: PdfDocument, chunks: list[Chunk], vectors: Vectors) -> None: ...

    def delete_document(self, document_id: str) -> int: ...

    def search(
        self,
        query_vector: Vectors,
        top_k: int = 5,
        allowed_document_ids: set[str] | None = None,
    ) -> list[SearchResult]: ...

    def save(self) -> None: ...

    def all_vectors(self) -> Vectors: ...

    def describe(self) -> list[tuple[str, str]]: ...

    def close(self) -> None: ...


def open_vector_db(embedder: Embedder) -> VectorDB:
    """Open the vector database chosen by the VECTOR_STORE setting."""
    if settings.vector_store == "qdrant":
        from insureassist.ingestion.qdrant_store import QdrantStore

        return QdrantStore.connect(
            settings.qdrant_url, settings.qdrant_collection, embedder.model_name, embedder.dimension
        )
    return VectorStore(settings.index_dir, embedder.model_name, embedder.dimension)
