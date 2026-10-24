# Architecture Decision Records

Each significant decision is recorded as an ADR (format: Context → Decision → Consequences → Alternatives).

**Status values:** `Proposed` → `Accepted` (owner approval) → `Superseded by ADR-NNNN` / `Deprecated`.
All ADRs below are **Proposed** until the M0 approval. They become **Accepted** when the owner approves, except any the owner changes.

| ADR | Title | Status |
|---|---|---|
| [0001](0001-record-architecture-decisions.md) | Record architecture decisions | Proposed |
| [0002](0002-frozen-stack-and-supporting-libraries.md) | Frozen stack, no RAG frameworks, supporting libraries | Proposed (OD-03, OD-04) |
| [0003](0003-postgres-canonical-faiss-derived.md) | PostgreSQL canonical; FAISS/BM25 derived; hard delete | Proposed (OD-07) |
| [0004](0004-authorization-before-generation.md) | Authorization enforced before generation | Proposed |
| [0005](0005-hybrid-retrieval-rrf.md) | Hybrid retrieval fused with Reciprocal Rank Fusion | Implemented; **not default** (no measured gain); see the Result section |
| [0006](0006-deterministic-identifiers.md) | Deterministic UUIDv5 identifiers and derived vector IDs | Proposed |
| [0007](0007-page-bounded-chunking.md) | Page-bounded, character-based chunking | Proposed |
| [0008](0008-jsonl-canonical-store-before-postgres.md) | JSONL canonical store for M1–M4 | Proposed (OD-01) |
| [0009](0009-single-process-indexes-and-background-ingestion.md) | Single-process indexes, BackgroundTasks, in-process rate limiting | Proposed (OD-06) |
| [0010](0010-python-tooling-uv.md) | Python 3.12 environment and dependencies via uv | Proposed (OD-02) |
| [0011](0011-qdrant-vector-database.md) | Qdrant as a selectable vector database (FAISS kept) | **Accepted** |
| [0012](0012-m5-api-and-access-control.md) | M5 API, authentication and access control as implemented | **Accepted** |

## Template

```markdown
# ADR-NNNN: Title
- Status: Proposed | Accepted | Superseded by ADR-XXXX
- Date: YYYY-MM-DD
## Context
## Decision
## Consequences
## Alternatives considered
```
