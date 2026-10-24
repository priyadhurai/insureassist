"""The "Vector DB" page: look inside the vector store.

Shows, for learning and debugging:
  1. What is stored: counts, dimensions, the three files on disk.
  2. Every row: FAISS row number -> chunk id, document, page, text.
  3. One row's vector: the 384 numbers the model produced for that chunk.
  4. A map: all vectors squeezed from 384 dimensions down to 2 (PCA), so you can see
     which chunks are "close in meaning". Type a question to see where it lands and
     which rows are nearest (the real search uses all 384 numbers).
"""

from dataclasses import dataclass

import altair as alt
import numpy as np
import numpy.typing as npt
import pandas as pd
import streamlit as st

from insureassist.ingestion.embedder import Embedder, cosine_similarity
from insureassist.ingestion.vector_db import VectorDB
from insureassist.ui.render import plain

# Categorical colours (fixed order, validated for colour-blind separation): one per
# document, and the question gets the next slot plus a different marker shape.
SERIES_COLORS = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300"]
QUESTION_COLOR = "#1baf7a"


@dataclass
class Projection:
    points: npt.NDArray[np.float64]  # (n, 2) coordinates of the stored vectors
    question: npt.NDArray[np.float64] | None  # (2,) coordinates of the question, if any
    kept_share: float  # how much of the spread the 2D picture keeps (0-1)


def project_2d(
    vectors: npt.NDArray[np.float32], question: npt.NDArray[np.float32] | None = None
) -> Projection:
    """PCA: find the 2 directions in which the vectors differ most, and use them as x/y.

    The question is placed with the SAME directions, so its position is comparable.
    """
    data = vectors.astype(np.float64)
    mean = data.mean(axis=0)
    centered = data - mean
    _, singular_values, directions = np.linalg.svd(centered, full_matrices=False)
    axes = directions[:2]
    variance = singular_values**2
    kept = float(variance[:2].sum() / variance.sum()) if variance.sum() > 0 else 1.0
    q = None if question is None else (question.astype(np.float64).reshape(-1) - mean) @ axes.T
    return Projection(points=centered @ axes.T, question=q, kept_share=kept)


def render_vector_db(store: VectorDB, embedder: Embedder) -> None:
    st.title("Inside the vector database")
    st.caption(
        "FAISS stores only numbers. Each row is one chunk of a PDF, turned into "
        f"{store.dimension} numbers by {embedder.model_name}. Row N of the FAISS index "
        "matches line N of chunks.jsonl, which holds the chunk's text and page."
    )
    if store.count == 0:
        st.info("The vector database is empty. Add a PDF in the sidebar.")
        return

    vectors = store.all_vectors()
    filenames = {doc_id: doc.filename for doc_id, doc in store.documents.items()}
    rows = pd.DataFrame(
        {
            "row": range(len(store.chunks)),
            "chunk_id": [c.chunk_id for c in store.chunks],
            "document": [filenames[c.document_id] for c in store.chunks],
            "page": [c.page_number for c in store.chunks],
            "chars": [len(c.text) for c in store.chunks],
            "text": [" ".join(c.text.split())[:140] + "..." for c in store.chunks],
        }
    )

    # 1. What is stored -------------------------------------------------------------
    cols = st.columns(4)
    cols[0].metric("Documents", len(store.documents))
    cols[1].metric("Vectors (rows)", store.count)
    cols[2].metric("Numbers per vector", store.dimension)
    cols[3].metric("Backend", store.backend)
    st.dataframe(
        pd.DataFrame(store.describe(), columns=["what", "details"]),
        hide_index=True,
        width="stretch",
    )

    # 2. Every row ------------------------------------------------------------------
    st.subheader("Rows")
    st.dataframe(rows, hide_index=True, width="stretch")

    # 3. One vector -----------------------------------------------------------------
    st.subheader("One row's vector")
    row = st.selectbox(
        "Row",
        rows["row"],
        format_func=lambda r: (
            f"row {r}: {rows['chunk_id'][r]} ({rows['document'][r]} p{rows['page'][r]})"
        ),
    )
    vector = vectors[row]
    st.markdown(plain(" ".join(store.chunks[row].text.split())))
    st.caption(
        f"First 8 of {len(vector)} numbers: {[round(float(v), 3) for v in vector[:8]]} · "
        f"length = {np.linalg.norm(vector):.3f} (all vectors have length 1, so a dot "
        "product = cosine similarity)"
    )
    numbers = pd.DataFrame({"dimension": range(len(vector)), "value": vector})
    st.altair_chart(
        alt.Chart(numbers)
        .mark_bar(color=SERIES_COLORS[0])
        .encode(
            x=alt.X("dimension:Q", title="dimension (0-383)"),
            y=alt.Y("value:Q", title="value"),
            tooltip=["dimension", alt.Tooltip("value:Q", format=".4f")],
        )
        .properties(height=180),
        width="stretch",
    )

    # 4. Map + nearest rows ---------------------------------------------------------
    st.subheader("Map of meaning (384 dimensions squeezed into 2)")
    question = st.text_input("Type a question to place it on the map", "")
    q_vector = embedder.embed([question]) if question.strip() else None
    projection = project_2d(vectors, None if q_vector is None else q_vector[0])

    points = rows[["row", "document", "page", "text"]].copy()
    points["x"], points["y"] = projection.points[:, 0], projection.points[:, 1]
    points["kind"] = "chunk"
    if projection.question is not None:
        points.loc[len(points)] = {
            "row": -1,
            "document": "your question",
            "page": 0,
            "text": question,
            "x": projection.question[0],
            "y": projection.question[1],
            "kind": "question",
        }
    labels = list(filenames.values()) + ["your question"]
    colors = SERIES_COLORS[: len(filenames)] + [QUESTION_COLOR]
    st.altair_chart(
        alt.Chart(points)
        .mark_point(size=140, filled=True, stroke="white", strokeWidth=2)
        .encode(
            x=alt.X("x:Q", title="direction 1", axis=alt.Axis(labels=False, ticks=False)),
            y=alt.Y("y:Q", title="direction 2", axis=alt.Axis(labels=False, ticks=False)),
            color=alt.Color(
                "document:N",
                scale=alt.Scale(domain=labels, range=colors),
                legend=alt.Legend(title=None, orient="top"),
            ),
            shape=alt.Shape(
                "kind:N",
                scale=alt.Scale(domain=["chunk", "question"], range=["circle", "diamond"]),
                legend=None,
            ),
            tooltip=["row", "document", "page", "text"],
        )
        .properties(height=380),
        width="stretch",
    )
    st.caption(
        f"This 2D picture keeps {projection.kept_share:.0%} of how the vectors differ, so "
        "distances are approximate. Hover a point to see its row. The real search below "
        "uses all 384 numbers."
    )

    if q_vector is not None:
        scores = cosine_similarity(q_vector, vectors)
        nearest = rows[["row", "document", "page", "text"]].copy()
        nearest.insert(1, "similarity", np.round(scores, 3))
        st.markdown("**Nearest rows to your question** (what semantic search would return):")
        st.dataframe(
            nearest.sort_values("similarity", ascending=False).head(5),
            hide_index=True,
            width="stretch",
        )
