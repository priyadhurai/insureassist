# ADR-0006: Deterministic UUIDv5 identifiers and derived vector IDs

- Status: Proposed
- Date: 2026-10-18

## Context
The requirements call for stable IDs, deterministic duplicate handling, reproducible evaluation and rebuildable indexes. FAISS needs `int64` IDs.

## Revision (2026-10-18, owner asked for simpler code)
Final implemented scheme (ingestion steps 1, 2 and 4):

| ID | Format | Example |
|---|---|---|
| Document | `doc_` + first 16 hex chars of the file's SHA-256 | `doc_1cf3888b6671383b` |
| Chunk | `{document_id}-p{page}-c{index}` | `doc_1cf3888b6671383b-p3-c0` |
| FAISS vector | **row position**: row N ↔ line N of `chunks.jsonl` (`IndexFlatIP`, no `IndexIDMap2`) | `0`, `1`, … |

Deleting a document rebuilds the flat index from the kept vectors (`reconstruct_n`), so row positions are always contiguous. This is simpler than derived 63-bit vector IDs and fast at our scale. When PostgreSQL arrives (M5), it can keep the same IDs. The original proposal below is kept for history.

## Decision (original proposal)
- `document_id = uuid5(NS_DOC, sha256(file_bytes))`, where `NS_DOC` is a fixed project constant.
- `chunk_id = uuid5(document_id, f"{chunking_config_id}:{page_number}:{ordinal}")`.
- `vector_id = first 8 bytes of chunk_id (big-endian) & (2^63 − 1)`. It is stored in `chunks.vector_id` UNIQUE, and a collision fails loudly.
- The storage key is derived from `document_id`, never from the filename.

## Consequences
- Re-ingesting the same bytes always gives the same IDs. Duplicates are detected by checksum and ID, with no extra state.
- Evaluation labels refer to stable fixture documents across runs.
- The vector-ID mapping needs no sequence or side file and survives full rebuilds.
- Anyone holding the exact file can compute its `document_id`. That reveals nothing they don't already have, and authorization returns 404 regardless.
- Collision probability: about n²/2^64. Negligible at 50k chunks (~10⁻¹⁰), and detected anyway.

## Alternatives considered
- Random UUIDv4 plus a DB sequence for vector IDs: not reproducible across environments, and rebuild depends on the DB sequence state.
- Human-readable IDs (`doc-p3-c2`): leak structure and are not unique across documents without a prefix.
