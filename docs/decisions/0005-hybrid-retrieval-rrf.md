# ADR-0005: Hybrid retrieval fused with Reciprocal Rank Fusion

- Status: Proposed
- Date: 2026-10-18

## Context
Insurance text mixes paraphrasable concepts (semantic search helps) with exact identifiers such as form numbers, section numbers and amounts (BM25 helps). Cosine and BM25 scores are on different, uncalibrated scales.

## Decision
Retrieve `candidate_k` (default 50) results from each retriever and fuse them with RRF (`k=60`, 1-based ranks). Ties are broken by semantic score, then `chunk_id`. Per-retriever ranks and scores are kept as diagnostics. Cross-encoder reranking is an optional later stage (M4), behind a flag.

## Consequences
- No score normalization or weight tuning is needed for the baseline, and fusion is deterministic.
- RRF scores are not calibrated confidences, so abstention thresholds use the semantic or rerank scores instead.

## Result (2026-10-18, step B3)
Implemented in [search.py](../../src/insureassist/retrieval/search.py) (`Searcher`, `reciprocal_rank_fusion`). Measured on dataset v1 (31 answerable questions), hybrid did **not** beat semantic search: MRR 0.826 vs 0.827, and 0.819–0.830 across `candidate_k` ∈ {5, 10, 50} × `rrf_k` ∈ {5, 20, 60}. Details in [evaluation-strategy §7a](../evaluation-strategy.md).

**Revised decision:** hybrid is kept as an option (`RETRIEVAL_MODE=hybrid`), but the default is `semantic`. Keyword matching IS used for abstention: the LLM is skipped only when the semantic score is low **and** no chunk shares a word with the question. To be revisited with a larger dataset and with reranking (E4).

## Alternatives considered
Weighted linear fusion of min-max-normalized scores: needs tuning and is sensitive to score distributions. It will be tested as an M4 ablation.
