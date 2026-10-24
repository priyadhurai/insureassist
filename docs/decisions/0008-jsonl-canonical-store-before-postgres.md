# ADR-0008: JSONL canonical store for M1–M4

- Status: Proposed (**needs owner decision, OD-01**)
- Date: 2026-10-18

## Context
The milestone order puts database migrations and the API in M5, while M1–M4 already need canonical document and chunk records (ingestion export, index rebuild, evaluation).

## Decision
- Define a small `ChunkStore` / `DocumentStore` interface (`get`, `list_ready`, `upsert`, `delete`, `iter_chunks`) in `insureassist.storage`.
- M1–M4: `JsonlStore`, file-based under `DATA_DIR/exports/`, using the same Pydantic records as [data-models.md §4](../data-models.md). There is no authorization in this mode: CLI/evaluation use only, single local user.
- M5: SQLAlchemy repositories implement the same interface, and an `insureassist import-jsonl` command loads existing records. PostgreSQL becomes canonical; JSONL stays as an export format.
- M2's ACL filtering is implemented and tested against an in-memory ACL fixture. M5 swaps in the DB-backed `AccessResolver`.

## Consequences
- M1–M4 can be developed and evaluated with no running database.
- There is one extra interface, which also makes the retrieval tests DB-free.
- Risk of the two backends diverging. Mitigation: a shared contract test suite runs against both implementations.

## Alternatives considered
Introduce PostgreSQL + SQLAlchemy models in M1 (Alembic early). This is fully valid and avoids the interface, but it pulls M5 work forward and makes every M1–M4 test need Docker. **If the owner prefers this, M1 adds the DB models for `documents` and `chunks` only.**
