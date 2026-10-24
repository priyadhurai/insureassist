# ADR-0009: Single-process indexes, BackgroundTasks, in-process rate limiting

- Status: Proposed (**needs owner decision, OD-06**)
- Date: 2026-10-18

## Context
Ingestion (embedding a large PDF) can take tens of seconds, which is too long for a synchronous request. Redis and Celery are excluded. FAISS and BM25 live in process memory.

## Decision
- Run the API with **one Uvicorn worker**. It owns the FAISS and BM25 indexes; one lock serializes index mutations.
- Ingestion runs through FastAPI `BackgroundTasks` (in process). Status is tracked in `documents.status`. Startup reconciliation marks stale `processing` documents `failed` and resumes `deleting`.
- Rate limiting uses an in-memory token bucket keyed by user/IP (a small internal module; no extra dependency).

## Consequences
- No extra infrastructure, simple to operate locally.
- No horizontal scaling. Rate limits reset on restart. A crash loses in-flight ingestions, which are recoverable by re-upload.
- M7 revisits this: a shared vector store (e.g. pgvector or a managed service), a job queue and gateway rate limiting, each needing approval.

## Alternatives considered
- Celery + Redis: robust queueing, but excluded and too heavy for v1.
- `slowapi`: an extra dependency for a feature that is about 40 lines in process.
