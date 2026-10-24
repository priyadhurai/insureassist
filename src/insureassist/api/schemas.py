"""Request and response shapes (Pydantic). Unknown fields in requests are rejected."""

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


# --- auth ---------------------------------------------------------------------------


class TokenResponse(BaseModel):
    access_token: str
    token_type: Literal["bearer"] = "bearer"  # noqa: S105 - token type name, not a secret
    expires_in: int


class UserResponse(BaseModel):
    id: uuid.UUID
    email: str
    display_name: str
    role: str
    is_active: bool


# --- documents ----------------------------------------------------------------------


class DocumentSummary(BaseModel):
    id: str
    title: str
    filename: str
    page_count: int
    chunk_count: int
    owner_id: uuid.UUID
    created_at: datetime


class DocumentResponse(DocumentSummary):
    checksum_sha256: str
    shared_with_roles: list[str] | None = None  # shown to the owner and admins only


class DocumentList(BaseModel):
    items: list[DocumentSummary]
    total: int
    limit: int
    offset: int


# --- chat ---------------------------------------------------------------------------


class QueryRequest(Strict):
    question: str = Field(min_length=3, max_length=2000)
    top_k: int | None = Field(default=None, ge=1, le=20)
    document_ids: list[str] | None = Field(default=None, max_length=50)

    @field_validator("question")
    @classmethod
    def clean_question(cls, value: str) -> str:
        value = value.strip()
        if len(value) < 3:
            raise ValueError("question is too short")
        if any(ord(ch) < 32 and ch not in "\n\t" for ch in value):
            raise ValueError("question contains control characters")
        return value


class CitationOut(BaseModel):
    ref: str
    document_id: str
    document_title: str
    page_number: int
    chunk_id: str
    snippet: str


class QueryResponse(BaseModel):
    query_id: uuid.UUID
    status: Literal["answered", "insufficient_evidence", "conflicting_evidence"]
    answer: str
    citations: list[CitationOut]
    disclaimer: str
    model: str
    latency_ms: dict[str, int]


# --- feedback -----------------------------------------------------------------------


class FeedbackRequest(Strict):
    query_id: uuid.UUID
    rating: Literal["up", "down"]
    comment: str | None = Field(default=None, max_length=1000)


class FeedbackResponse(BaseModel):
    id: int
    query_id: uuid.UUID
    rating: str
    created_at: datetime


# --- admin / health -----------------------------------------------------------------


class EvaluationSummary(BaseModel):
    name: str
    created_at: str
    kind: Literal["retrieval", "answers"]
    dataset: str
    config: dict[str, Any]
    metrics: dict[str, Any]


class EvaluationList(BaseModel):
    items: list[EvaluationSummary]
    total: int


class ReadyResponse(BaseModel):
    status: Literal["ready", "not_ready"]
    checks: dict[str, str]
