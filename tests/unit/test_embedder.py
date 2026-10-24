"""Tests for ingestion step 3 (embedder).

Fast tests use the fake HashEmbedder. Tests marked `slow` load the real model
(downloaded once, ~90 MB). Run them with: uv run pytest -m slow
"""

import numpy as np
import pytest

from insureassist.ingestion.embedder import (
    HashEmbedder,
    SentenceTransformerEmbedder,
    cosine_similarity,
)

DEDUCTIBLE = "The deductible for collision coverage is $500 per claim."
WAITING = "A waiting period of 30 days applies to new dental policies."
QUESTION = "How much do I have to pay myself before the insurer pays?"


# --- fake embedder (fast) -------------------------------------------------------


def test_hash_embedder_shape_and_unit_length() -> None:
    vectors = HashEmbedder(dimension=64).embed([DEDUCTIBLE, WAITING])

    assert vectors.shape == (2, 64)
    assert vectors.dtype == np.float32
    np.testing.assert_allclose(np.linalg.norm(vectors, axis=1), 1.0, rtol=1e-5)


def test_hash_embedder_is_deterministic() -> None:
    embedder = HashEmbedder()
    np.testing.assert_array_equal(embedder.embed([DEDUCTIBLE]), embedder.embed([DEDUCTIBLE]))


def test_hash_embedder_prefers_shared_words() -> None:
    embedder = HashEmbedder(dimension=256)
    chunks = embedder.embed([DEDUCTIBLE, WAITING])
    query = embedder.embed(["what is the collision deductible"])

    scores = cosine_similarity(query, chunks)

    assert scores[0] > scores[1]


def test_empty_text_gives_zero_vector() -> None:
    assert not HashEmbedder().embed([""]).any()


# --- real model (slow) ----------------------------------------------------------


@pytest.fixture(scope="module")
def real_embedder() -> SentenceTransformerEmbedder:
    return SentenceTransformerEmbedder()


@pytest.mark.slow
def test_real_model_shape_and_unit_length(real_embedder: SentenceTransformerEmbedder) -> None:
    vectors = real_embedder.embed([DEDUCTIBLE, WAITING])

    assert real_embedder.dimension == 384
    assert vectors.shape == (2, 384)
    np.testing.assert_allclose(np.linalg.norm(vectors, axis=1), 1.0, rtol=1e-4)


@pytest.mark.slow
def test_real_model_understands_meaning_without_shared_words(
    real_embedder: SentenceTransformerEmbedder,
) -> None:
    # The question never says "deductible", yet it should match the deductible sentence.
    scores = cosine_similarity(
        real_embedder.embed([QUESTION]), real_embedder.embed([DEDUCTIBLE, WAITING])
    )
    assert scores[0] > scores[1]


@pytest.mark.slow
def test_real_model_empty_input(real_embedder: SentenceTransformerEmbedder) -> None:
    assert real_embedder.embed([]).shape == (0, 384)
