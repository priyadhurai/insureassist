"""InsureAssist local demo UI.

    uv run streamlit run src/insureassist/ui/app.py

Left: documents, upload, search settings. Main: ask questions, see the answer with
its sources (file + page + text snippet).

Local demo only: no login and every document is visible. It listens on localhost
(see .streamlit/config.toml). Milestone M5 replaces the direct Python calls below with
calls to the FastAPI backend, which adds login and document permissions.
"""

import os
import tempfile
from pathlib import Path

os.environ.setdefault("TRANSFORMERS_VERBOSITY", "error")
os.environ.setdefault("HF_HUB_VERBOSITY", "error")

import streamlit as st  # noqa: E402

from insureassist.config import settings  # noqa: E402
from insureassist.generation.answer import Answer, answer_question  # noqa: E402
from insureassist.generation.llm import GenerationError, LLMProvider, create_llm  # noqa: E402
from insureassist.ingestion.embedder import Embedder, SentenceTransformerEmbedder  # noqa: E402
from insureassist.ingestion.pipeline import ingest_file  # noqa: E402
from insureassist.ingestion.vector_db import VectorDB, open_vector_db  # noqa: E402
from insureassist.retrieval.search import MODES, Searcher  # noqa: E402
from insureassist.ui.render import STATUS_LABELS, plain  # noqa: E402
from insureassist.ui.vector_db_view import render_vector_db  # noqa: E402

st.set_page_config(page_title="InsureAssist", page_icon=":shield:", layout="wide")


# --- heavy objects: created once and reused across clicks ------------------------------


@st.cache_resource(show_spinner="Loading the embedding model (first time ~15 s)...")
def get_embedder() -> Embedder:
    return SentenceTransformerEmbedder()


@st.cache_resource
def get_store() -> VectorDB:
    return open_vector_db(get_embedder())  # FAISS or Qdrant, from the VECTOR_STORE setting


@st.cache_resource(show_spinner="Loading the language model...")
def load_llm() -> LLMProvider:
    # Cached: a local Hugging Face model (~3 GB) must not be reloaded for every question.
    return create_llm()


def get_llm() -> LLMProvider | None:
    try:
        return load_llm()
    except GenerationError as error:  # e.g. missing Gemini key (errors are not cached)
        st.sidebar.error(str(error))
        return None


embedder = get_embedder()
store = get_store()

# --- sidebar: documents, upload, settings ----------------------------------------------

st.sidebar.title("InsureAssist")
st.sidebar.caption("Local demo: no login, all documents visible.")
# A direct link http://localhost:8501/?page=vector-db opens the Vector DB page.
start_page = 1 if st.query_params.get("page") == "vector-db" else 0
page = st.sidebar.radio("Page", ["Ask", "Vector DB"], index=start_page, horizontal=True)

st.sidebar.subheader(f"Documents ({len(store.documents)})")
for doc in store.documents.values():
    st.sidebar.text(f"{doc.filename}  ({doc.page_count} p, {doc.chunk_count} chunks)")

uploaded = st.sidebar.file_uploader("Add a PDF", type=["pdf"])
if uploaded is not None and st.sidebar.button("Ingest"):
    # Same pipeline and checks as the CLI (size, real PDF, duplicates, ...).
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / Path(uploaded.name).name  # never trust the client's path
        path.write_bytes(uploaded.getvalue())
        with st.spinner("Reading, chunking and embedding..."):
            result = ingest_file(path, store, embedder)
    if result.status == "ingested":
        store.save()
        st.sidebar.success(f"Ingested: {result.pages} pages, {result.chunks} chunks.")
        for warning in result.warnings:
            st.sidebar.warning(warning)
    elif result.status == "duplicate":
        st.sidebar.info("This document is already ingested.")
    else:
        st.sidebar.error(f"Not ingested: {result.error_message}")

st.sidebar.subheader("Search settings")
default_mode = MODES.index(settings.retrieval_mode)
mode = st.sidebar.radio("Retriever", MODES, index=default_mode, horizontal=True)
top_k = st.sidebar.slider("Passages given to the model", 1, 10, settings.retrieval_top_k)

if page == "Vector DB":
    render_vector_db(store, embedder)
    st.stop()  # don't draw the chat below

# --- main: chat ------------------------------------------------------------------------

st.title("Ask about your insurance documents")
st.caption(
    "Answers come only from the documents on the left and cite file and page. "
    "AI-generated: verify against the source. Not insurance advice."
)


def show_answer(answer: Answer) -> None:
    label, color = STATUS_LABELS[answer.status]
    st.markdown(f":{color}-badge[{label}]")
    st.markdown(plain(answer.answer))  # escaped: model output is never interpreted
    if answer.citations:
        with st.expander(f"Sources ({len(answer.citations)})", expanded=True):
            for c in answer.citations:
                st.markdown(f"**{plain(c.ref)}**: {plain(c.filename)}, page {c.page_number}")
                st.caption(plain(c.snippet))
    st.caption(
        f"model: {answer.model or 'not called'} · retrieval {answer.retrieval_ms} ms · "
        f"generation {answer.generation_ms} ms"
    )


if "history" not in st.session_state:
    st.session_state.history = []  # list of (question, Answer | error message)

for past_question, past_outcome in st.session_state.history:
    with st.chat_message("user"):
        st.markdown(plain(past_question))
    with st.chat_message("assistant"):
        if isinstance(past_outcome, Answer):
            show_answer(past_outcome)
        else:
            st.error(past_outcome)

question = st.chat_input("e.g. What is the deductible for a collision?")
if question:
    with st.chat_message("user"):
        st.markdown(plain(question))
    with st.chat_message("assistant"):
        llm = get_llm()
        if not store.documents:
            outcome: Answer | str = "No documents yet. Add a PDF in the sidebar."
        elif llm is None:
            outcome = "The language model is not configured (see the sidebar)."
        else:
            try:
                with st.spinner("Searching and answering..."):
                    searcher = Searcher(store, embedder, mode=mode)
                    outcome = answer_question(question, searcher, llm, top_k=top_k)
            except GenerationError as error:
                outcome = str(error)  # safe message (e.g. quota reached), no internals
        if isinstance(outcome, Answer):
            show_answer(outcome)
        else:
            st.error(outcome)
    st.session_state.history.append((question, outcome))
