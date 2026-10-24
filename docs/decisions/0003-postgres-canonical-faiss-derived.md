# ADR-0003: PostgreSQL canonical; FAISS and BM25 derived; hard delete

- Status: Proposed (needs owner decision on OD-07)
- Date: 2026-10-18

## Context
FAISS is a library index with no transactions, metadata filtering semantics or access control. The system needs consistent deletion, rebuildability and authoritative access control.

## Decision
- PostgreSQL holds the canonical users, roles, ACLs, documents, **chunk text** and `vector_id` values.
- FAISS (persisted) and BM25 (in memory) are **derived** and fully rebuildable from `chunks` of `ready` documents.
- A manifest with an ID-set hash detects drift between the index and the DB. A stale index fails readiness.
- **Deletion is a hard delete:** set status `deleting` → remove vectors → rebuild BM25 → delete chunks, file and row → audit event (IDs + checksum only). Startup reconciliation resumes an interrupted deletion.

## Consequences
- An index crash or corruption never loses data; only a rebuild is needed.
- Hard delete supports privacy and retention requirements, but deleted content cannot be restored except from backups (which keep it until they expire, a documented limitation).
- Re-uploading previously deleted bytes works cleanly (same deterministic ID, fresh rows).

## Alternatives considered
- Soft delete (`deleted_at`): easier undo, but keeps sensitive text indefinitely and complicates the uniqueness of deterministic IDs.
- pgvector instead of FAISS: would merge stores and allow SQL-side ACL joins, but FAISS is frozen. pgvector is a candidate for M7.
