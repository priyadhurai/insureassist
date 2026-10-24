"""Admin-only developer page: look inside the local vector database.

It loads the embedding model and opens the vector store directly (outside the API),
which is why the UI only offers it to admins. Useful for learning and debugging.
"""

import os

import streamlit as st

from insureassist.ingestion import embedder as embedder_module
from insureassist.ingestion.embedder import Embedder
from insureassist.ingestion.vector_db import VectorDB, open_vector_db
from insureassist.ui.vector_db_view import render_vector_db

os.environ.setdefault("TRANSFORMERS_VERBOSITY", "error")
os.environ.setdefault("HF_HUB_VERBOSITY", "error")


@st.cache_resource(show_spinner="Loading the embedding model (first time ~15 s)...")
def _embedder() -> Embedder:
    return embedder_module.SentenceTransformerEmbedder()  # looked up at call time


@st.cache_resource
def _store() -> VectorDB:
    return open_vector_db(_embedder())


def show_vector_db_page() -> None:
    st.caption("Developer view (admins only): reads the local vector database directly.")
    render_vector_db(_store(), _embedder())
