# Changelog

## v0.1.0 (2026-10-24)

First public release.

- **Ingestion**: PDF validation (size, real PDF, encryption, page limits, scanned-page detection), page-aware sentence chunking (800/120 characters), `all-MiniLM-L6-v2` embeddings, content-hash document IDs and duplicate detection.
- **Vector stores**: Qdrant (default in the demo) and FAISS behind one interface, verified to return identical rankings.
- **Retrieval**: semantic (default), BM25 keyword and hybrid (Reciprocal Rank Fusion), measured on a labelled dataset (semantic MRR 0.942).
- **Answers**: Gemini with structured JSON output, server-side citation validation, abstention, partial answers and conflict reporting; optional local Hugging Face model.
- **API (FastAPI)**: 11 endpoints; PostgreSQL + Alembic; JWT + Argon2id; viewer/editor/admin roles; document-level permissions applied inside the vector search; audit log; rate and size limits; consistent error format with request ids.
- **Web UI (Streamlit)**: sign-in, chat with sources and feedback, uploads for editors, admin-only Vector DB view.
- **Evaluation**: labelled dataset v2 (28 questions), retrieval metrics, answer evaluation with cache (full answer run pending due to API quota).
- **Quality**: 180 automated tests, Ruff, mypy strict; 12 Architecture Decision Records.
