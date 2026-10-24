"""Keyword search with BM25: finds chunks that contain the question's exact words.

Why, when we already have semantic search? Embeddings capture meaning but are weak at
exact codes and rare words: "Form AP-RS-12" ranked only 4th in the baseline. BM25 is
the classic search-engine formula and is very good at exactly that.

BM25 in one sentence: a chunk scores higher when it contains the question's words,
especially RARE words (a word found in every chunk, like "policy", counts little),
with a correction so long chunks don't win just by being long.

Scores are NOT on the same scale as cosine similarity (they can be 0.5 or 12.3).
Only the order matters. B3 (hybrid) therefore combines rankings, not raw scores.
"""

import re
import unicodedata

from rank_bm25 import BM25Okapi

from insureassist.ingestion.chunker import Chunk
from insureassist.ingestion.vector_store import SearchResult

# Very common words carry no meaning for search. Kept small on purpose:
# "not" and "no" are NOT removed, because they matter in insurance text.
STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "be", "by", "can", "do", "does", "for", "from",
    "has", "have", "how", "i", "if", "in", "is", "it", "its", "me", "my", "of", "on", "or",
    "our", "so", "that", "the", "their", "them", "then", "there", "these", "this", "to",
    "was", "we", "what", "when", "where", "which", "who", "will", "with", "you", "your",
}  # fmt: skip

# A word, number or code: "deductible", "500", "ap-rs-12", "4.2", "24/7".
TOKEN = re.compile(r"[a-z0-9]+(?:[-./][a-z0-9]+)*")


def tokenize(text: str) -> list[str]:
    """Split text into search words.

    "Form AP-RS-12 costs $1,500" -> ["form", "ap-rs-12", "ap", "rs", "12", "costs", "1500"]
    A code is kept whole AND split into parts, so "AP-RS-12" matches exactly,
    and a search for just "RS-12" still finds it.
    """
    text = unicodedata.normalize("NFKC", text).lower()
    text = re.sub(r"(?<=\d),(?=\d{3})", "", text)  # "1,500" -> "1500"
    tokens: list[str] = []
    for word in TOKEN.findall(text):
        if word in STOPWORDS:
            continue
        tokens.append(word)
        parts = re.split(r"[-./]", word)
        if len(parts) > 1:
            tokens.extend(p for p in parts if p not in STOPWORDS)
    return tokens


class KeywordIndex:
    """BM25 index over chunks. Built in memory in milliseconds, so it is never saved:
    it is simply rebuilt from the vector store's chunks whenever needed."""

    def __init__(self, chunks: list[Chunk]) -> None:
        self.chunks = chunks
        tokenized = [tokenize(c.text) for c in chunks]
        # Remember each chunk's words, to check "shares at least one word" directly.
        self._words = [set(tokens) for tokens in tokenized]
        self._bm25 = BM25Okapi(tokenized) if chunks else None

    def search(
        self,
        question: str,
        top_k: int = 5,
        allowed_document_ids: set[str] | None = None,
    ) -> list[SearchResult]:
        """Top chunks by BM25 score, best first. Chunks sharing no word are left out."""
        tokens = tokenize(question)
        if self._bm25 is None or not tokens or top_k <= 0:
            return []
        scores = self._bm25.get_scores(tokens)
        query_words = set(tokens)

        # A chunk matches if it shares a word with the question. We check the words
        # directly instead of using "score > 0": BM25Okapi gives a word found in exactly
        # half of the chunks a weight of 0, so a real match can score 0.
        candidates = [
            (float(score), row)
            for row, score in enumerate(scores)
            if query_words & self._words[row]
            and (
                allowed_document_ids is None or self.chunks[row].document_id in allowed_document_ids
            )
        ]
        # Highest score first; equal scores keep chunk order, so results are repeatable.
        candidates.sort(key=lambda item: (-item[0], item[1]))
        return [
            SearchResult(chunk=self.chunks[row], score=score) for score, row in candidates[:top_k]
        ]
