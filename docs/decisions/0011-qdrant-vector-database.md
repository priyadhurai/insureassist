# ADR-0011: Qdrant as a selectable vector database (FAISS kept)

- Status: Accepted (owner approved, 2026-10-19)
- Date: 2026-10-19

## Context
FAISS (from the frozen stack) is a library, not a database. Metadata, the per-document filter, persistence and a way to *see* the data all had to be built by us. The owner wanted to inspect the stored vectors and asked which free vector database could be used.

## Decision
- Add **Qdrant** (open source, Apache 2.0, free to self-host), pinned to `qdrant/qdrant:v1.19.2` and run with `docker compose up -d qdrant`. It binds to **127.0.0.1 only**, because no API key is configured.
- A `VectorDB` protocol ([vector_db.py](../../src/insureassist/ingestion/vector_db.py)) defines the shape every store must have. `VectorStore` (FAISS) and `QdrantStore` ([qdrant_store.py](../../src/insureassist/ingestion/qdrant_store.py)) both implement it. `VECTOR_STORE=faiss|qdrant` selects one; **the code default stays `faiss`** (works without Docker); the owner's `.env` uses `qdrant`.
- Qdrant layout: one collection `insureassist_chunks`; one point per chunk; point id = UUIDv5(chunk id); cosine distance; payload = chunk fields + embedding model + document record; keyword payload index on `document_id`. The permission filter (`MatchAny` on `document_id`) runs inside Qdrant, before ranking.
- The same test suite runs against both backends (Qdrant local mode, no server needed in tests).

## Evidence
On dataset v2 (23 answerable questions), FAISS and Qdrant gave **identical** results: same Recall@1/3/5, MRR 0.942, nDCG@5 0.948, identical page ranking for 23/23 questions, and top-1 similarity scores equal to 6 decimals. Both search exactly at this size (Qdrant uses exact search below its indexing threshold).

## Consequences
- Pro: a real database with a web dashboard (http://localhost:6333/dashboard), built-in filtering, and immediate persistence (no separate chunks file to keep in sync).
- Pro: shows the swappable design: one new class, no changes to search, generation, evaluation or UI logic.
- Con: one more service (Docker container) to run; if it is down, the CLI and UI show "Qdrant is not reachable … docker compose up -d qdrant".
- Con: Qdrant without an API key must never be exposed on a network. Cloud deployment (M7) needs an API key or private networking.
- PostgreSQL remains the planned canonical store for M5. pgvector stays an alternative to evaluate then.

## Alternatives considered
pgvector (one database with PostgreSQL; needs PostgreSQL now, no vector-specific UI); Chroma (embedded, no dashboard); keep FAISS only (no UI, filtering is our own code).
