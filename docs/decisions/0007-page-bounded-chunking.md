# ADR-0007: Page-bounded, character-based chunking

- Status: Proposed
- Date: 2026-10-18

## Context
Citations must name a document **and page**. `all-MiniLM-L6-v2` truncates input above 256 word pieces.

## Decision
- Chunks never span a page boundary, so every chunk has exactly one `page_number`.
- Sentence-aware greedy packing up to `CHUNK_SIZE_CHARS=800`, with about 120 characters of sentence-aligned overlap and a 50-character minimum fragment merge.
- Size is measured in characters, not tokens, to keep ingestion independent of the embedding tokenizer. M2 measures the actual truncation rate with the tokenizer; if it exceeds 1%, the default changes.
- `chunker_version` and `chunking_config_id` are recorded per document.

## Consequences
- Citations are exact and simple.
- A sentence that crosses a page break is split, which is a minor recall cost (multi-page questions in the evaluation set measure it).

## Alternatives considered
- Cross-page chunks with `page_start`/`page_end`: better continuity, but ambiguous citations.
- Token-based chunking with the embedding tokenizer: more precise, but couples M1 to M2 and to a model download.
