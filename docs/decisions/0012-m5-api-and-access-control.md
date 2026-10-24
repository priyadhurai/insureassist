# ADR-0012: M5 API, authentication and access control as implemented

- Status: Accepted (owner approved PostgreSQL, 2026-10-24)
- Date: 2026-10-24

## Context
M5 adds the API, login, roles and document-level permissions. The M0 design (api-specification.md, data-models.md) was written before the vector store, the evaluation set and the demo scope were settled. This ADR records what was built and where it deliberately differs from the M0 design, so that the docs stay honest.

## Decision
- **PostgreSQL 16** (Docker, localhost:5433) holds users, documents, document permissions, query logs, feedback and audit events. Schema created by one Alembic migration (`0001`); a test checks that the migration matches the SQLAlchemy models.
- **Chunks and vectors stay in the vector database** (Qdrant payloads), not in PostgreSQL. Original PDFs are stored privately in `data/documents/{document_id}.pdf`, so the index can be rebuilt. *(M0 planned a `chunks` table.)*
- **One role per user** (`viewer`, `editor`, `admin`) as a column. *(M0 planned roles + user_roles tables.)* Simpler, and enough for this product.
- **Read rule**: admin, or owner, or a grant to the user, or a grant to the user's role. Computed per request in SQL and passed into the vector search as a filter; citations are re-checked against it before the response. Documents a user may not read return 404, exactly like missing ones.
- **JWT**: HS256 only, 15-minute tokens, `iss`/`aud`/`exp`/`iat`/`nbf`/`sub`/`jti` required, the role read from the database on every request. The app refuses to start with a missing, short or placeholder `JWT_SECRET_KEY`.
- **Passwords**: Argon2id (`argon2-cffi` defaults), 12–256 characters, dummy verification for unknown e-mails so timing does not reveal which accounts exist. Users are created with `insureassist create-user` (password typed or read from stdin, never as a command-line argument).
- **Uploads are processed within the request** (validate, chunk, embed, store; the DB row last, earlier steps undone on failure) and return **201**. *(M0 planned 202 + background processing; small PDFs take about a second.)*
- **Rate limits and size limits** in process (sliding window), per ADR-0009.
- **`GET /admin/evaluations`** lists the evaluation report files instead of an `evaluation_runs` table.
- **The web UI calls only the API** with the user's token. The one exception is the admin-only "Vector DB" developer page, which reads the local vector store directly.
- **Demo data**: `insureassist seed-demo` creates three users and registers the sample PDFs with grants (the amendment is not shared with viewers). Generated passwords go to the git-ignored `data/demo-credentials.txt`, never to the screen.

## Consequences
- Pro: the permission filter, the 404 rule and the "no forbidden text in the prompt" property are all covered by tests (tests/api, 29 tests).
- Pro: tests run without Docker (SQLite in memory, FAISS, fake embedder and LLM); the migration is also tested on SQLite.
- Con: in-process rate limits and synchronous uploads assume one API process (fine for v0.1; revisit in M6/M7).
- Con: changing a document's sharing after upload needs a database change; there is no endpoint for it yet.

## Alternatives considered
SQLite instead of PostgreSQL (simpler, less production-like; rejected by the owner); background ingestion with a queue (more moving parts than v0.1 needs); OAuth/OIDC identity provider (planned for a cloud deployment, M7).
