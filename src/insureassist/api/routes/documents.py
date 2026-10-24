"""Documents: upload (editor/admin), list and view (only what you may read), delete.

A document you may not read answers 404, exactly like one that doesn't exist, so the
API never confirms that a hidden document is there.
"""

import tempfile
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, Query, Request, Response, UploadFile
from sqlalchemy import select
from sqlalchemy.orm import Session

from insureassist.api.deps import (
    CurrentUser,
    DbDep,
    ServicesDep,
    enforce_rate_limit,
    request_id,
    require_role,
)
from insureassist.api.errors import ApiError
from insureassist.api.schemas import DocumentList, DocumentResponse, DocumentSummary
from insureassist.db.models import Document, User
from insureassist.ingestion.pdf_reader import PdfError
from insureassist.services import documents as document_service
from insureassist.services.access import audit, can_delete, readable_document_ids

router = APIRouter(prefix="/documents", tags=["documents"])
Uploader = Annotated[User, Depends(require_role("editor", "admin"))]
READ_CHUNK = 1024 * 1024


def _summary(doc: Document) -> DocumentSummary:
    return DocumentSummary(
        id=doc.id,
        title=doc.title,
        filename=doc.filename,
        page_count=doc.page_count,
        chunk_count=doc.chunk_count,
        owner_id=doc.owner_id,
        created_at=doc.created_at,
    )


def _detail(doc: Document, user: User) -> DocumentResponse:
    show_sharing = user.role == "admin" or doc.owner_id == user.id
    return DocumentResponse(
        **_summary(doc).model_dump(),
        checksum_sha256=doc.checksum_sha256,
        shared_with_roles=sorted(g.role for g in doc.grants if g.role) if show_sharing else None,
    )


def _readable_or_404(db: Session, user: User, document_id: str, request: Request) -> Document:
    doc = db.get(Document, document_id)
    if doc is None or document_id not in readable_document_ids(db, user):
        if doc is not None:
            audit(
                db,
                "document.access_denied",
                "denied",
                actor=user,
                target_type="document",
                target_id=document_id,
                request_id=request_id(request),
            )
            db.commit()
        raise ApiError(404, "not_found", "Document not found.")
    return doc


@router.post("", status_code=201, response_model=DocumentResponse)
async def upload_document(
    request: Request,
    user: Uploader,
    db: DbDep,
    services: ServicesDep,
    file: Annotated[UploadFile, File()],
    title: Annotated[str | None, Form(max_length=200)] = None,
    shared_with_roles: Annotated[list[str] | None, Form()] = None,
) -> DocumentResponse:
    enforce_rate_limit(services.upload_limiter, str(user.id))
    if not (file.filename or "").lower().endswith(".pdf"):
        raise ApiError(415, "unsupported_media_type", "Only PDF files are supported.")

    # Stream to a private temp file, stopping as soon as the size limit is passed:
    # a huge upload is never held in memory or fully written to disk.
    limit = services.limits.max_upload_bytes
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / "upload.pdf"
        size = 0
        with path.open("wb") as out:
            while part := await file.read(READ_CHUNK):
                size += len(part)
                if size > limit:
                    raise ApiError(
                        413,
                        "payload_too_large",
                        f"The file is larger than {limit // (1024 * 1024)} MB.",
                    )
                out.write(part)
        try:
            doc = document_service.add_document(
                db,
                services.storage,
                path,
                file.filename or "document.pdf",
                user,
                title=title,
                shared_with_roles=shared_with_roles,
            )
        except PdfError as error:
            status, code = {
                "not_pdf": (415, "unsupported_media_type"),
                "too_large": (413, "payload_too_large"),
            }.get(error.code, (422, "invalid_document"))
            raise ApiError(status, code, error.message, {"reason": error.code}) from None
        except document_service.DuplicateDocument as dup:
            # Reveal the existing id only to someone who may read that document.
            details = (
                {"document_id": dup.document_id}
                if dup.document_id in readable_document_ids(db, user)
                else {}
            )
            raise ApiError(
                409,
                "duplicate_document",
                "A document with identical content already exists.",
                details,
            ) from None
        except ValueError as error:
            raise ApiError(422, "validation_error", str(error)) from None

    audit(
        db,
        "document.upload",
        "success",
        actor=user,
        target_type="document",
        target_id=doc.id,
        request_id=request_id(request),
        pages=doc.page_count,
        chunks=doc.chunk_count,
    )
    db.commit()
    return _detail(doc, user)


@router.get("", response_model=DocumentList)
def list_documents(
    user: CurrentUser,
    db: DbDep,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> DocumentList:
    ids = readable_document_ids(db, user)
    docs = list(
        db.scalars(
            select(Document).where(Document.id.in_(ids)).order_by(Document.created_at, Document.id)
        )
    )
    page = docs[offset : offset + limit]
    return DocumentList(
        items=[_summary(d) for d in page], total=len(docs), limit=limit, offset=offset
    )


@router.get("/{document_id}", response_model=DocumentResponse)
def get_document(
    document_id: str, request: Request, user: CurrentUser, db: DbDep
) -> DocumentResponse:
    return _detail(_readable_or_404(db, user, document_id, request), user)


@router.delete("/{document_id}", status_code=204)
def delete_document(
    document_id: str, request: Request, user: CurrentUser, db: DbDep, services: ServicesDep
) -> Response:
    doc = _readable_or_404(db, user, document_id, request)
    if not can_delete(user, doc):
        raise ApiError(403, "forbidden", "Only the owner or an admin can delete this document.")
    removed = document_service.delete_document(db, services.storage, doc)
    audit(
        db,
        "document.delete",
        "success",
        actor=user,
        target_type="document",
        target_id=document_id,
        request_id=request_id(request),
        chunks_removed=removed,
    )
    db.commit()
    return Response(status_code=204)
