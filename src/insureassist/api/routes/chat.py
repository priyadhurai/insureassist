"""POST /chat/query: answer a question from the documents THIS user may read.

    allowed = documents the user may read (PostgreSQL) [∩ the optional document_ids filter]
    search only inside `allowed` -> prompt -> LLM -> citations -> log -> response

Defence in depth: after answering, every citation is checked against `allowed` again.
"""

import hashlib
import logging

from fastapi import APIRouter, Request

from insureassist.api.deps import CurrentUser, DbDep, ServicesDep, enforce_rate_limit, request_id
from insureassist.api.errors import ApiError
from insureassist.api.schemas import CitationOut, QueryRequest, QueryResponse
from insureassist.db.models import Document, QueryLog
from insureassist.generation.answer import DISCLAIMER, NO_ANSWER, Answer, answer_question
from insureassist.generation.llm import GenerationError, RateLimitError
from insureassist.retrieval.search import Searcher
from insureassist.services.access import audit, readable_document_ids

router = APIRouter(prefix="/chat", tags=["chat"])
log = logging.getLogger("insureassist.api")


@router.post("/query", response_model=QueryResponse)
def query(
    body: QueryRequest, request: Request, user: CurrentUser, db: DbDep, services: ServicesDep
) -> QueryResponse:
    enforce_rate_limit(services.query_limiter, str(user.id))
    if len(body.question) > services.limits.max_question_chars:
        raise ApiError(422, "validation_error", "The question is too long.")

    allowed = readable_document_ids(db, user)
    if body.document_ids is not None:
        allowed &= set(body.document_ids)  # a filter can narrow access, never widen it

    if not allowed:
        answer = Answer("insufficient_evidence", NO_ANSWER)
    else:
        try:
            llm = services.llm()
            searcher = Searcher(services.store, services.embedder)
            answer = answer_question(
                body.question,
                searcher,
                llm,
                top_k=body.top_k or services.limits.top_k,
                allowed_document_ids=allowed,
            )
        except RateLimitError:
            raise ApiError(
                503, "llm_unavailable", "The language model quota was reached. Try again later."
            ) from None
        except GenerationError:
            raise ApiError(
                503, "llm_unavailable", "The language model is unavailable. Try again later."
            ) from None

    # Defence in depth: never return a citation from a document the user may not read.
    leaked = [c for c in answer.citations if c.document_id not in allowed]
    if leaked:
        log.error(
            "citation outside allowed documents was dropped",
            extra={"request_id": request_id(request)},
        )
        audit(
            db,
            "document.access_denied",
            "denied",
            actor=user,
            target_type="query",
            request_id=request_id(request),
            dropped=len(leaked),
        )
        answer.citations = [c for c in answer.citations if c.document_id in allowed]
        if not answer.citations:
            answer = Answer("insufficient_evidence", NO_ANSWER, model=answer.model)

    titles = {d.id: d.title for d in db.query(Document).filter(Document.id.in_(allowed))}
    entry = QueryLog(
        user_id=user.id,
        question_sha256=hashlib.sha256(body.question.encode()).hexdigest(),
        question_chars=len(body.question),
        question_text=body.question if services.limits.store_query_text else None,
        status=answer.status,
        cited_chunk_ids=[c.chunk_id for c in answer.citations],
        retrieval_ms=answer.retrieval_ms,
        generation_ms=answer.generation_ms,
        llm_model=answer.model,
    )
    db.add(entry)
    db.commit()

    return QueryResponse(
        query_id=entry.id,
        status=answer.status,
        answer=answer.answer,
        citations=[
            CitationOut(
                ref=c.ref,
                document_id=c.document_id,
                document_title=titles.get(c.document_id, c.filename),
                page_number=c.page_number,
                chunk_id=c.chunk_id,
                snippet=c.snippet,
            )
            for c in answer.citations
        ],
        disclaimer=DISCLAIMER,
        model=answer.model or "not called",
        latency_ms={"retrieval": answer.retrieval_ms, "generation": answer.generation_ms},
    )
