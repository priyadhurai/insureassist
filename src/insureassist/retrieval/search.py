"""Hybrid search: run semantic AND keyword search, then merge the two rankings.

Why merge RANKS and not scores? Semantic scores are 0-1 (cosine), BM25 scores are
0-20+. Adding them would let BM25 dominate. Reciprocal Rank Fusion (RRF) only uses
positions:

    rrf_score(chunk) = sum over both lists of  1 / (rrf_k + rank_in_that_list)

Example (rrf_k = 60): a chunk ranked 1st by keyword and 3rd by semantic gets
1/61 + 1/63 = 0.0323. A chunk ranked 1st by only one list gets 1/61 = 0.0164.
So chunks that BOTH methods like rise to the top, and a chunk only one method finds
can still make it into the results. rrf_k = 60 is the value from the original RRF
paper; a larger rrf_k gives lower ranks relatively more weight.
"""

from dataclasses import dataclass
from typing import Literal

from insureassist.config import settings
from insureassist.ingestion.embedder import Embedder
from insureassist.ingestion.vector_db import VectorDB
from insureassist.ingestion.vector_store import SearchResult
from insureassist.retrieval.keyword_search import KeywordIndex

Mode = Literal["semantic", "keyword", "hybrid"]
MODES: tuple[Mode, ...] = ("semantic", "keyword", "hybrid")


@dataclass
class Retrieval:
    results: list[SearchResult]  # best first
    best_semantic_score: float  # cosine similarity of the closest chunk (0-1)
    keyword_matches: int  # how many chunks share at least one word with the question


def reciprocal_rank_fusion(
    ranked_lists: list[list[SearchResult]], top_k: int, rrf_k: int = 60
) -> list[SearchResult]:
    """Merge several rankings into one. The returned score is the RRF score."""
    scores: dict[str, float] = {}
    best_rank: dict[str, int] = {}
    chunks = {}
    for ranked in ranked_lists:
        for rank, result in enumerate(ranked, start=1):
            chunk_id = result.chunk.chunk_id
            scores[chunk_id] = scores.get(chunk_id, 0.0) + 1.0 / (rrf_k + rank)
            best_rank[chunk_id] = min(best_rank.get(chunk_id, rank), rank)
            chunks[chunk_id] = result.chunk
    # Highest RRF score first; ties -> better single rank -> chunk id (repeatable order).
    order = sorted(scores, key=lambda cid: (-scores[cid], best_rank[cid], cid))
    return [SearchResult(chunk=chunks[cid], score=scores[cid]) for cid in order[:top_k]]


class Searcher:
    """Answers "which chunks match this question?" with the chosen mode."""

    def __init__(
        self,
        store: VectorDB,
        embedder: Embedder,
        mode: Mode | None = None,
        candidate_k: int | None = None,
        rrf_k: int | None = None,
    ) -> None:
        self.store = store
        self.embedder = embedder
        self.mode: Mode = mode or settings.retrieval_mode
        self.candidate_k = candidate_k or settings.retrieval_candidate_k
        self.rrf_k = rrf_k or settings.retrieval_rrf_k
        self.keyword_index = KeywordIndex(store.chunks)  # built once, in milliseconds

    def search(
        self, question: str, top_k: int, allowed_document_ids: set[str] | None = None
    ) -> Retrieval:
        # Both searches respect the same document filter, so hybrid search can never
        # bring in a document the user may not read.
        query_vector = self.embedder.embed([question])
        semantic = self.store.search(query_vector, self.candidate_k, allowed_document_ids)
        keyword = self.keyword_index.search(question, self.candidate_k, allowed_document_ids)

        if self.mode == "semantic":
            results = semantic[:top_k]
        elif self.mode == "keyword":
            results = keyword[:top_k]
        else:
            # Each list is cut to candidate_k first: the fusion looks at the top
            # candidates of each method, not at every chunk in the store.
            results = reciprocal_rank_fusion([semantic, keyword], top_k, self.rrf_k)

        return Retrieval(
            results=results,
            best_semantic_score=semantic[0].score if semantic else 0.0,
            keyword_matches=len(keyword),
        )
