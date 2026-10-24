# ADR-0002: Frozen stack, no RAG frameworks, supporting libraries

- Status: Proposed (needs owner decision on OD-03 and OD-04)
- Date: 2026-10-18

## Context
The owner has frozen the core stack: Python 3.12, FastAPI, Pydantic, Streamlit, google-genai, Sentence Transformers, FAISS, rank-bm25, cross-encoder, PyMuPDF, PostgreSQL, SQLAlchemy 2.x, Alembic, JWT, Argon2id, pytest, HTTPX, Ruff, Docker. LangChain, LlamaIndex, Redis, Celery and Kubernetes are excluded unless approved. Implementing the frozen stack still requires some small libraries that are not on the list.

## Decision
1. Build the RAG pipeline in plain Python modules (no orchestration framework).
2. Propose these **supporting libraries**, each with its reason:

| Library | Why | Alternative |
|---|---|---|
| `pydantic-settings` | Typed env/.env configuration (frozen requirement: env-based configuration) | Hand-rolled `os.environ` parsing |
| `PyJWT` | JWT encode/verify with algorithm allow-list and claim validation | `python-jose` (less maintained) |
| `argon2-cffi` | Reference Argon2id binding | `passlib` (wraps argon2-cffi anyway; less maintained) |
| `python-multipart` | Required by FastAPI for form/multipart (login form, PDF upload) | none |
| `psycopg[binary]` (v3) | PostgreSQL driver for SQLAlchemy | `psycopg2` (legacy) |
| `httpx` | Already frozen for tests; also used by the Streamlit UI as the API client | `requests` |
| `uvicorn` | ASGI server for FastAPI | — |
| `numpy` | Transitive (FAISS / sentence-transformers); used directly for vectors | — |
| dev: `mypy`, `pytest-cov`, `pytest-asyncio` (or `anyio` plugin) | Type checking, coverage, async API tests | — |
| CI (M6, proposed): `pip-audit`, `gitleaks`, `trivy` | Dependency / secret / image scanning (security requirement) | — |

3. **PyMuPDF licence:** PyMuPDF is dual-licensed under AGPL-3.0 or a commercial licence from Artifex. Using it in a network-accessible service under the AGPL means offering the complete corresponding source to users. That is acceptable for a public portfolio repository, but it must be acknowledged, and it matters for any commercial reuse.

## Consequences
- More code is owned directly, in exchange for full control and transparency, which suits the portfolio goal.
- Each new library beyond this list requires an ADR update and owner approval.

## Alternatives considered
- LangChain/LlamaIndex: faster scaffolding, but excluded by the owner, and they hide the retrieval and authorization details this project is meant to demonstrate.
- `pypdf` instead of PyMuPDF: permissive licence but weaker extraction quality. It is the fallback if AGPL is unacceptable (would need a new ADR).
