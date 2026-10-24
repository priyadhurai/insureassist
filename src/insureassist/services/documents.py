"""Upload and delete documents, keeping PostgreSQL, the vector DB and the stored PDFs in sync.

Upload order: validate -> chunk -> embed -> vectors -> PDF file -> database row.
The database row is written LAST: a document is only visible to anyone once
everything else succeeded. If a step fails, earlier steps are undone.
"""

import shutil
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy.orm import Session

from insureassist.db.models import ROLES, Document, DocumentAccess, User
from insureassist.ingestion.chunker import chunk_document
from insureassist.ingestion.embedder import Embedder
from insureassist.ingestion.pdf_reader import read_pdf
from insureassist.ingestion.vector_db import VectorDB


class DuplicateDocument(Exception):
    def __init__(self, document_id: str) -> None:
        super().__init__(document_id)
        self.document_id = document_id


@dataclass
class Storage:
    """Everything a document lives in besides PostgreSQL."""

    store: VectorDB
    embedder: Embedder
    documents_dir: Path

    def pdf_path(self, document_id: str) -> Path:
        # A generated name, never the user's filename (no path tricks like ../../x.pdf).
        return self.documents_dir / f"{document_id}.pdf"


def safe_filename(name: str) -> str:
    """Display-only filename: base name, no control characters, at most 255 characters."""
    base = Path(name.replace("\\", "/")).name
    cleaned = "".join(ch for ch in base if ch.isprintable()).strip()
    return (cleaned or "document.pdf")[:255]


def add_document(
    session: Session,
    storage: Storage,
    pdf_file: Path,
    original_filename: str,
    owner: User,
    title: str | None = None,
    shared_with_roles: list[str] | None = None,
) -> Document:
    """Validate, index and register one PDF. Raises PdfError, DuplicateDocument or ValueError."""
    roles = sorted(set(shared_with_roles or []))
    if any(role not in ROLES for role in roles):
        raise ValueError(f"shared_with_roles must be from {ROLES}")

    doc = read_pdf(pdf_file)  # step 1: validation (raises PdfError)
    if session.get(Document, doc.document_id) is not None:
        raise DuplicateDocument(doc.document_id)

    added_vectors = False
    if not storage.store.has_document(doc.document_id):
        chunks = chunk_document(doc)
        storage.store.add_document(doc, chunks, storage.embedder.embed([c.text for c in chunks]))
        storage.store.save()
        added_vectors = True
    try:
        storage.documents_dir.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(pdf_file, storage.pdf_path(doc.document_id))
        filename = safe_filename(original_filename)
        record = Document(
            id=doc.document_id,
            checksum_sha256=doc.checksum_sha256,
            title=(title or Path(filename).stem)[:200],
            filename=filename,
            page_count=doc.page_count,
            chunk_count=storage.store.documents[doc.document_id].chunk_count,
            owner_id=owner.id,
            grants=[DocumentAccess(role=role) for role in roles],
        )
        session.add(record)
        session.flush()
    except Exception:
        # Undo the earlier steps so nothing half-registered is left behind.
        if added_vectors:
            storage.store.delete_document(doc.document_id)
            storage.store.save()
        storage.pdf_path(doc.document_id).unlink(missing_ok=True)
        raise
    return record


def delete_document(session: Session, storage: Storage, document: Document) -> int:
    """Remove vectors, the stored PDF and the database row. Returns chunks removed."""
    removed = storage.store.delete_document(document.id)
    storage.store.save()
    storage.pdf_path(document.id).unlink(missing_ok=True)
    session.delete(document)
    return removed
