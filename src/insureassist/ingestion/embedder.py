"""Ingestion step 3: turn text into embeddings (lists of numbers that capture meaning).

Texts with similar meaning get vectors that point in a similar direction, even when they
share no words: "How much do I pay before insurance pays?" lands close to
"The deductible is $500 per claim."

All vectors are normalized to length 1. Then "how similar are two texts?" is a simple
dot product (cosine similarity): 1.0 = same meaning, ~0 = unrelated.

Two embedders share the same shape (the `Embedder` protocol):
  * SentenceTransformerEmbedder - the real model, used by the application.
  * HashEmbedder - a tiny fake for tests: no download, instant, but it only "understands"
    shared words, not meaning.
"""

import hashlib
import re
from typing import Protocol

import numpy as np
import numpy.typing as npt

from insureassist.config import settings

Vectors = npt.NDArray[np.float32]  # shape: (number_of_texts, dimension)


class Embedder(Protocol):
    model_name: str
    dimension: int

    def embed(self, texts: list[str]) -> Vectors: ...


class SentenceTransformerEmbedder:
    """Wraps a Sentence Transformers model (default: all-MiniLM-L6-v2, 384 numbers per text)."""

    def __init__(
        self,
        model_name: str | None = None,
        device: str | None = None,
        batch_size: int | None = None,
    ) -> None:
        # Imported here, not at the top: loading PyTorch takes seconds, and code that
        # never embeds anything (e.g. the PDF tests) shouldn't pay that cost.
        from sentence_transformers import SentenceTransformer

        self.model_name = model_name or settings.embedding_model_name
        self.batch_size = batch_size or settings.embedding_batch_size
        # The first run downloads the model (~90 MB) into the Hugging Face cache.
        self._model = SentenceTransformer(
            self.model_name, device=device or settings.embedding_device
        )
        self.dimension: int = self._model.get_embedding_dimension() or 0
        # Text longer than this many tokens is silently cut off by the model.
        self.max_tokens: int = self._model.max_seq_length or 0

    def embed(self, texts: list[str]) -> Vectors:
        if not texts:
            return np.zeros((0, self.dimension), dtype=np.float32)
        vectors = self._model.encode(
            texts,
            batch_size=self.batch_size,  # process 32 texts at a time to limit memory use
            normalize_embeddings=True,  # length 1 -> dot product = cosine similarity
            convert_to_numpy=True,
            show_progress_bar=False,
        )
        return np.asarray(vectors, dtype=np.float32)

    def count_tokens(self, text: str) -> int:
        """How many model tokens a text uses (to check chunks fit within max_tokens)."""
        return len(self._model.tokenizer.tokenize(text))


class HashEmbedder:
    """Fake embedder for tests. Each word is hashed to one position in the vector.

    Texts sharing words get similar vectors, so retrieval logic can be tested
    without downloading a model. It does NOT understand synonyms.
    """

    def __init__(self, dimension: int = 64) -> None:
        self.model_name = f"hash-{dimension}"
        self.dimension = dimension

    def embed(self, texts: list[str]) -> Vectors:
        vectors = np.zeros((len(texts), self.dimension), dtype=np.float32)
        for row, text in enumerate(texts):
            for word in re.findall(r"[a-z0-9]+", text.lower()):
                digest = hashlib.sha256(word.encode()).digest()
                vectors[row, int.from_bytes(digest[:4], "big") % self.dimension] += 1.0
        norms = np.linalg.norm(vectors, axis=1, keepdims=True)
        norms[norms == 0] = 1.0  # empty text stays an all-zero vector
        return vectors / norms


def cosine_similarity(query: Vectors, vectors: Vectors) -> npt.NDArray[np.float32]:
    """Similarity of one query vector (1, dim) with each row of `vectors` (n, dim)."""
    return np.asarray(vectors @ query[0], dtype=np.float32)
