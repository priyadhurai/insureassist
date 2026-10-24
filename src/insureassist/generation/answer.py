"""Question time: retrieve -> check evidence -> prompt -> LLM -> validate citations.

question
  -> search (semantic / keyword / hybrid; only allowed documents)
  -> weak evidence?  -> "insufficient_evidence" (LLM is NOT called: saves cost
                        and avoids the model guessing). Weak = the closest chunk's meaning
                        is far off AND no chunk shares a single word with the question.
  -> build prompt with E1..En evidence blocks
  -> LLM returns {status, answer, citations}
  -> keep only citations that point at evidence we actually sent
  -> "answered" but no valid citations? -> treat as insufficient (ungrounded answer)
"""

import time
from dataclasses import dataclass, field

from insureassist.config import settings
from insureassist.generation.llm import LLMProvider, Status
from insureassist.generation.prompt import SYSTEM_INSTRUCTION, build_prompt
from insureassist.retrieval.search import Searcher

DISCLAIMER = (
    "AI-generated from the available documents. Verify against the cited source before "
    "relying on it. This is not insurance advice or a coverage decision."
)
NO_ANSWER = "The available documents do not contain enough information to answer this question."


@dataclass
class Citation:
    ref: str  # "E1"
    document_id: str
    filename: str
    page_number: int
    chunk_id: str
    snippet: str  # start of the chunk text, so the user can check it


@dataclass
class Answer:
    status: Status
    answer: str
    citations: list[Citation] = field(default_factory=list)
    disclaimer: str = DISCLAIMER
    model: str = ""
    retrieval_ms: int = 0
    generation_ms: int = 0


def answer_question(
    question: str,
    searcher: Searcher,
    llm: LLMProvider,
    top_k: int | None = None,
    min_score: float | None = None,
    allowed_document_ids: set[str] | None = None,
) -> Answer:
    top_k = top_k if top_k is not None else settings.retrieval_top_k
    min_score = min_score if min_score is not None else settings.retrieval_min_semantic_score

    # 1. Retrieve (only from documents the user may read).
    start = time.perf_counter()
    retrieval = searcher.search(question, top_k, allowed_document_ids)
    results = retrieval.results
    retrieval_ms = int((time.perf_counter() - start) * 1000)

    # 2. Weak evidence: abstain without calling the LLM.
    weak = retrieval.best_semantic_score < min_score and retrieval.keyword_matches == 0
    if not results or weak:
        return Answer("insufficient_evidence", NO_ANSWER, retrieval_ms=retrieval_ms)

    # 3. Ask the LLM, with labelled evidence.
    filenames = {doc_id: doc.filename for doc_id, doc in searcher.store.documents.items()}
    start = time.perf_counter()
    output = llm.generate(SYSTEM_INSTRUCTION, build_prompt(question, results, filenames))
    generation_ms = int((time.perf_counter() - start) * 1000)

    # 4. Keep only citations that refer to evidence we actually supplied.
    by_label = {f"E{n}": result for n, result in enumerate(results, start=1)}
    citations = [
        Citation(
            ref=label,
            document_id=by_label[label].chunk.document_id,
            filename=filenames[by_label[label].chunk.document_id],
            page_number=by_label[label].chunk.page_number,
            chunk_id=by_label[label].chunk.chunk_id,
            snippet=" ".join(by_label[label].chunk.text.split())[:300],
        )
        for label in dict.fromkeys(output.citations)  # removes repeats, keeps order
        if label in by_label
    ]

    # 5. An answer with nothing valid to back it up is not trusted: show the generic
    #    message. But "not enough information" WITH valid citations is a partial answer
    #    ("the documents only say X [E1]"), and it is kept, because it is useful and sourced.
    status = output.status
    if not citations:
        return Answer(
            "insufficient_evidence",
            NO_ANSWER,
            model=llm.model_name,
            retrieval_ms=retrieval_ms,
            generation_ms=generation_ms,
        )
    return Answer(
        status,
        output.answer,
        citations,
        model=llm.model_name,
        retrieval_ms=retrieval_ms,
        generation_ms=generation_ms,
    )
