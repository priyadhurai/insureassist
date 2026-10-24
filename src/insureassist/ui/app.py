"""InsureAssist web UI. It talks ONLY to the API, as the signed-in user.

    uv run insureassist serve                          # the API (start it first)
    uv run streamlit run src/insureassist/ui/app.py    # this UI, http://localhost:8501

Every permission check happens in the API: the UI shows whatever the API returns for
this user's token. The token lives in this browser session only. The one exception is
the admin-only "Vector DB" developer page, which reads the local vector database.
"""

from typing import Any

import streamlit as st

from insureassist.config import settings
from insureassist.ui.api_client import ApiClient, ApiClientError
from insureassist.ui.render import STATUS_LABELS, plain

st.set_page_config(page_title="InsureAssist", page_icon=":shield:", layout="wide")
state = st.session_state


def sign_out(message: str | None = None) -> None:
    for key in ("token", "user", "history"):
        state.pop(key, None)
    if message:
        state["login_message"] = message


def api() -> ApiClient:
    return ApiClient(token=state.get("token"))


def handle(error: ApiClientError) -> str:
    """Expired or invalid session -> back to the login screen; else the API's message."""
    if error.status == 401:
        sign_out("Your session has expired. Please sign in again.")
        st.rerun()
    return error.message


# --- login -----------------------------------------------------------------------------

if "token" not in state:
    st.title("Sign in to InsureAssist")
    if message := state.pop("login_message", None):
        st.info(message)
    with st.form("login"):
        email = st.text_input("E-mail")
        password = st.text_input("Password", type="password")
        submitted = st.form_submit_button("Sign in")
    if submitted:
        client = ApiClient()
        try:
            client.login(email, password)
            state["user"] = client.me()
            state["token"] = client.token
            st.rerun()
        except ApiClientError as error:
            st.error(error.message)
    st.caption(
        "Local demo: the demo accounts are in data/demo-credentials.txt "
        "(created by `insureassist seed-demo`)."
    )
    st.stop()

user: dict[str, Any] = state["user"]

# --- sidebar ---------------------------------------------------------------------------

st.sidebar.title("InsureAssist")
st.sidebar.write(f"Signed in as **{plain(user['display_name'])}** ({user['role']})")
if st.sidebar.button("Sign out"):
    sign_out()
    st.rerun()

pages = ["Ask", "Vector DB"] if user["role"] == "admin" else ["Ask"]
page = st.sidebar.radio("Page", pages, horizontal=True) if len(pages) > 1 else "Ask"

try:
    documents = api().documents()
except ApiClientError as error:
    documents = []
    st.sidebar.error(handle(error))
st.sidebar.subheader(f"Your documents ({len(documents)})")
st.sidebar.caption("Only the documents you are allowed to read.")
for doc in documents:
    st.sidebar.text(f"{doc['title']}  ({doc['page_count']} p)")

if user["role"] in ("editor", "admin"):
    uploaded = st.sidebar.file_uploader("Add a PDF", type=["pdf"])
    share = st.sidebar.checkbox("Share with all viewers", value=False)
    if uploaded is not None and st.sidebar.button("Upload"):
        try:
            with st.spinner("Uploading, chunking and embedding..."):
                doc = api().upload(uploaded.name, uploaded.getvalue(), share)
            st.sidebar.success(f"Added {doc['title']}: {doc['chunk_count']} chunks.")
        except ApiClientError as error:
            st.sidebar.error(handle(error))

top_k = st.sidebar.slider("Passages given to the model", 1, 10, settings.retrieval_top_k)

if page == "Vector DB":
    from insureassist.ui.vector_db_page import show_vector_db_page

    show_vector_db_page()
    st.stop()

# --- ask -------------------------------------------------------------------------------

st.title("Ask about your insurance documents")
st.caption(
    "Answers use only the documents you may read and cite file and page. "
    "AI-generated: verify against the source. Not insurance advice."
)


def show_answer(answer: dict[str, Any], index: int) -> None:
    label, color = STATUS_LABELS[answer["status"]]
    st.markdown(f":{color}-badge[{label}]")
    st.markdown(plain(answer["answer"]))  # escaped: model output is never interpreted
    if answer["citations"]:
        with st.expander(f"Sources ({len(answer['citations'])})", expanded=True):
            for c in answer["citations"]:
                st.markdown(
                    f"**{plain(c['ref'])}**: {plain(c['document_title'])}, page {c['page_number']}"
                )
                st.caption(plain(c["snippet"]))
    st.caption(
        f"model: {answer['model']} · retrieval {answer['latency_ms']['retrieval']} ms · "
        f"generation {answer['latency_ms']['generation']} ms"
    )
    rated = state.setdefault("rated", set())
    if answer["query_id"] in rated:
        st.caption("Thanks for your feedback.")
        return
    up, down = st.columns([1, 6])
    for column, rating, text in ((up, "up", "Helpful"), (down, "down", "Not helpful")):
        if column.button(text, key=f"{rating}-{index}"):
            try:
                api().feedback(answer["query_id"], rating)
                rated.add(answer["query_id"])
                st.rerun()
            except ApiClientError as error:
                st.error(handle(error))


history: list[tuple[str, dict[str, Any] | str]] = state.setdefault("history", [])
for index, (past_question, past_outcome) in enumerate(history):
    with st.chat_message("user"):
        st.markdown(plain(past_question))
    with st.chat_message("assistant"):
        if isinstance(past_outcome, dict):
            show_answer(past_outcome, index)
        else:
            st.error(past_outcome)

question = st.chat_input("e.g. What is the collision deductible?")
if question:
    try:
        with st.spinner("Searching your documents and answering..."):
            outcome: dict[str, Any] | str = api().ask(question, top_k)
    except ApiClientError as error:
        outcome = handle(error)
    history.append((question, outcome))
    st.rerun()
