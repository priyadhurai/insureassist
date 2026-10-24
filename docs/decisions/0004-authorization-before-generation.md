# ADR-0004: Authorization enforced before generation

- Status: Proposed
- Date: 2026-10-18

## Context
In multi-user RAG, a common leak is retrieving chunks from documents the user cannot read and sending them to the LLM, which can then repeat them. FAISS has no concept of users.

## Decision
Defence in depth, all on the backend:
1. **Resolve** the allowed document IDs from PostgreSQL per request (owner, user grants, role grants, admin; `status='ready'`; active user).
2. **Restrict search:** FAISS search uses `SearchParameters(sel=IDSelectorBatch(allowed_vector_ids))`; BM25 scores at non-allowed positions are masked. A client `document_ids` filter is only intersected with the allowed set.
3. **Re-check** final candidates against the DB right before prompt construction; drop and audit any mismatch.
4. **Prove it with a test:** a `SpyProvider` records every prompt, and security test SEC-03 asserts that no unauthorized text appears across a randomized ACL matrix.
5. Unauthorized document IDs give 404 (no existence oracle).

## Consequences
- An extra DB round trip per query (cheap).
- Restricting the search, rather than post-filtering, keeps recall for users with small allowed sets.
- `IDSelectorBatch` cost grows with the allowed set size. Acceptable at the v1 scale (A-02).

## Alternatives considered
- One FAISS index per user/role: avoids the selector, but index count explodes and sharing changes require re-indexing.
- Post-filter a global top-k: simpler, but users can get zero results and it is easy to get wrong.
