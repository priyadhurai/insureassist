"""Retrieval scores. Each takes the retrieved pages in rank order and the correct pages.

A "page" is (filename, page_number). Several chunks can come from the same page; a
page is only counted the first time it appears.

Example: correct = {A3}, retrieved = [B2, A3, A3, C1]
  recall@1 = 0    (A3 not in the top 1)
  recall@3 = 1    (A3 is in the top 3)
  MRR      = 1/2  (first correct result is at rank 2)
"""

import math

PageRef = tuple[str, int]


def recall_at_k(retrieved: list[PageRef], relevant: set[PageRef], k: int) -> float:
    """Share of the correct pages found in the top k. 1.0 = all found."""
    if not relevant:
        return 0.0
    return len(relevant & set(retrieved[:k])) / len(relevant)


def hit_at_k(retrieved: list[PageRef], relevant: set[PageRef], k: int) -> float:
    """1.0 if at least one correct page is in the top k, else 0.0."""
    return 1.0 if relevant & set(retrieved[:k]) else 0.0


def reciprocal_rank(retrieved: list[PageRef], relevant: set[PageRef]) -> float:
    """1 / position of the first correct page (1st -> 1.0, 2nd -> 0.5, ...), 0 if none.
    The average over all questions is the MRR (mean reciprocal rank)."""
    for rank, page in enumerate(retrieved, start=1):
        if page in relevant:
            return 1.0 / rank
    return 0.0


def ndcg_at_k(retrieved: list[PageRef], relevant: set[PageRef], k: int) -> float:
    """Rewards correct pages more the higher they are ranked (1.0 = perfect order).

    DCG  = sum of 1/log2(rank+1) for each correct page in the top k (first occurrence only)
    nDCG = DCG / best possible DCG
    """
    if not relevant:
        return 0.0
    seen: set[PageRef] = set()
    dcg = 0.0
    for rank, page in enumerate(retrieved[:k], start=1):
        if page in relevant and page not in seen:
            dcg += 1.0 / math.log2(rank + 1)
        seen.add(page)
    ideal = sum(1.0 / math.log2(rank + 1) for rank in range(1, min(len(relevant), k) + 1))
    return dcg / ideal
