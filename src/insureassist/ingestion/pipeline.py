"""Ingestion step 5: run steps 1-4 for many files and report what happened.

For each PDF:   read_pdf -> duplicate check -> chunk_document -> embed -> store.add_document
One bad file never stops the batch: it is recorded as "failed" with a reason, and the
next file is processed. The store is saved once, at the end.
"""

import json
import time
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from insureassist.ingestion.chunker import chunk_document
from insureassist.ingestion.embedder import Embedder
from insureassist.ingestion.pdf_reader import PdfError, read_pdf
from insureassist.ingestion.vector_db import VectorDB


@dataclass
class FileResult:
    filename: str
    status: str  # "ingested" | "duplicate" | "failed"
    document_id: str | None = None
    pages: int = 0
    chunks: int = 0
    warnings: list[str] = field(default_factory=list)
    error_code: str | None = None
    error_message: str | None = None
    seconds: float = 0.0


@dataclass
class IngestionReport:
    started_at: str
    embedding_model: str
    files: list[FileResult] = field(default_factory=list)

    def count(self, status: str) -> int:
        return sum(1 for f in self.files if f.status == status)


def find_pdfs(path: Path) -> list[Path]:
    """A single file, or every .pdf in a folder (including subfolders), in a fixed order."""
    if path.is_file():
        return [path]
    return sorted(p for p in path.rglob("*") if p.is_file() and p.suffix.lower() == ".pdf")


def ingest_file(path: Path, store: VectorDB, embedder: Embedder) -> FileResult:
    """Ingest one PDF into the store (in memory; the caller saves)."""
    start = time.perf_counter()
    try:
        doc = read_pdf(path)  # step 1
    except PdfError as error:
        return FileResult(
            filename=path.name,
            status="failed",
            error_code=error.code,
            error_message=error.message,
            seconds=round(time.perf_counter() - start, 2),
        )

    if store.has_document(doc.document_id):
        # Same content already stored (maybe under another filename): skip it.
        return FileResult(
            filename=path.name,
            status="duplicate",
            document_id=doc.document_id,
            pages=doc.page_count,
            seconds=round(time.perf_counter() - start, 2),
        )

    chunks = chunk_document(doc)  # step 2
    vectors = embedder.embed([chunk.text for chunk in chunks])  # step 3
    store.add_document(doc, chunks, vectors)  # step 4
    return FileResult(
        filename=path.name,
        status="ingested",
        document_id=doc.document_id,
        pages=doc.page_count,
        chunks=len(chunks),
        warnings=doc.warnings,
        seconds=round(time.perf_counter() - start, 2),
    )


def ingest_files(paths: list[Path], store: VectorDB, embedder: Embedder) -> IngestionReport:
    report = IngestionReport(
        started_at=datetime.now(UTC).isoformat(timespec="seconds"),
        embedding_model=embedder.model_name,
    )
    for path in paths:
        report.files.append(ingest_file(path, store, embedder))
    if report.count("ingested"):
        store.save()  # one save for the whole batch
    return report


def save_report(report: IngestionReport, folder: Path) -> Path:
    """Write the report as JSON, e.g. data/reports/ingest-20261018-101500.json."""
    folder.mkdir(parents=True, exist_ok=True)
    stamp = report.started_at.replace("-", "").replace(":", "").replace("T", "-")[:15]
    path = folder / f"ingest-{stamp}.json"
    path.write_text(json.dumps(asdict(report), indent=2), encoding="utf-8")
    return path
