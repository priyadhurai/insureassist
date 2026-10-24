"""Run the question set through a retriever and score the results.

    load questions -> check labels against the store -> for each question:
    retrieve top 10 chunks -> turn them into pages -> recall@k, hit@k, MRR, nDCG@k
    -> averages + per-question details -> JSON report

`retrieve` is any function (question, k) -> list[SearchResult]. That way the same
evaluation compares semantic search, keyword search and hybrid search fairly.
"""

import json
import statistics
import subprocess
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from insureassist.evaluation.metrics import (
    PageRef,
    hit_at_k,
    ndcg_at_k,
    recall_at_k,
    reciprocal_rank,
)
from insureassist.ingestion.vector_db import VectorDB
from insureassist.ingestion.vector_store import SearchResult

Retriever = Callable[[str, int], list[SearchResult]]
K_VALUES = (1, 3, 5, 10)


class RelevantPage(BaseModel):
    doc: str  # filename
    page: int


class Question(BaseModel):
    id: str
    category: str
    question: str
    relevant: list[RelevantPage]
    key_facts: list[str]

    @property
    def answerable(self) -> bool:
        return bool(self.relevant)


class DatasetError(Exception):
    """The question file contains a labelling mistake."""


def load_questions(path: Path) -> list[Question]:
    lines = path.read_text(encoding="utf-8").splitlines()
    return [Question.model_validate_json(line) for line in lines if line.strip()]


def check_labels(questions: list[Question], store: VectorDB) -> None:
    """Catch labelling mistakes before measuring anything:
    the document must exist, the page must exist, and every key fact must appear
    on one of the labelled pages."""
    by_name = {d.filename: d.document_id for d in store.documents.values()}
    page_text: dict[PageRef, str] = {}
    for chunk in store.chunks:
        doc_name = next(n for n, i in by_name.items() if i == chunk.document_id)
        key = (doc_name, chunk.page_number)
        page_text[key] = page_text.get(key, "") + " " + " ".join(chunk.text.split())

    problems = []
    for q in questions:
        for rel in q.relevant:
            if (rel.doc, rel.page) not in page_text:
                problems.append(f"{q.id}: {rel.doc} page {rel.page} is not in the store")
        labelled = " ".join(page_text.get((r.doc, r.page), "") for r in q.relevant).lower()
        for fact in q.key_facts:
            if fact.lower() not in labelled:
                problems.append(f"{q.id}: key fact {fact!r} not found on the labelled pages")
    if problems:
        raise DatasetError("Dataset problems:\n  " + "\n  ".join(problems))


@dataclass
class QuestionResult:
    id: str
    category: str
    question: str
    relevant: list[str]
    retrieved: list[str]  # top-10 pages as "file p3", rank order (repeats removed)
    best_score: float
    scores: dict[str, float] = field(default_factory=dict)


@dataclass
class EvalReport:
    name: str
    created_at: str
    git_commit: str
    dataset: str
    config: dict[str, Any]
    averages: dict[str, float]
    by_category: dict[str, dict[str, float]]
    best_score_answerable: dict[str, float]  # helps choose the "not enough info" threshold
    best_score_unanswerable: dict[str, float]
    questions: list[QuestionResult]


def evaluate(
    name: str,
    questions: list[Question],
    retrieve: Retriever,
    filenames: dict[str, str],
    dataset_name: str,
    config: dict[str, Any],
) -> EvalReport:
    results: list[QuestionResult] = []
    unanswerable_scores: list[float] = []

    for q in questions:
        hits = retrieve(q.question, max(K_VALUES))
        best = round(hits[0].score, 4) if hits else 0.0
        if not q.answerable:
            unanswerable_scores.append(best)
            continue
        pages: list[PageRef] = [(filenames[h.chunk.document_id], h.chunk.page_number) for h in hits]
        relevant = {(r.doc, r.page) for r in q.relevant}
        scores = {"mrr": reciprocal_rank(pages, relevant)}
        for k in K_VALUES:
            scores[f"recall@{k}"] = recall_at_k(pages, relevant, k)
            scores[f"hit@{k}"] = hit_at_k(pages, relevant, k)
            scores[f"ndcg@{k}"] = ndcg_at_k(pages, relevant, k)
        results.append(
            QuestionResult(
                id=q.id,
                category=q.category,
                question=q.question,
                relevant=[f"{d} p{p}" for d, p in sorted(relevant)],
                retrieved=[f"{d} p{p}" for d, p in dict.fromkeys(pages)],
                best_score=best,
                scores={k: round(v, 4) for k, v in scores.items()},
            )
        )

    return EvalReport(
        name=name,
        created_at=datetime.now(UTC).isoformat(timespec="seconds"),
        git_commit=git_commit(),
        dataset=dataset_name,
        config=config,
        averages=_average([r.scores for r in results]),
        by_category={
            category: _average([r.scores for r in results if r.category == category])
            for category in sorted({r.category for r in results})
        },
        best_score_answerable=_spread([r.best_score for r in results]),
        best_score_unanswerable=_spread(unanswerable_scores),
        questions=results,
    )


def save_report(report: EvalReport, folder: Path) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    stamp = report.created_at.replace("-", "").replace(":", "").replace("T", "-")[:15]
    path = folder / f"{stamp}-{report.name}.json"
    path.write_text(json.dumps(asdict(report), indent=2), encoding="utf-8")
    return path


def _average(rows: list[dict[str, float]]) -> dict[str, float]:
    if not rows:
        return {}
    return {key: round(statistics.mean(row[key] for row in rows), 4) for key in rows[0]}


def _spread(values: list[float]) -> dict[str, float]:
    if not values:
        return {}
    return {
        "min": min(values),
        "mean": round(statistics.mean(values), 4),
        "max": max(values),
    }


def git_commit() -> str:
    """Record which code version produced the numbers (reproducibility)."""
    try:
        out = subprocess.run(  # noqa: S603 - fixed command, no user input
            ["git", "rev-parse", "--short", "HEAD"],  # noqa: S607
            capture_output=True,
            text=True,
            check=True,
        )
        return out.stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "no-commit-yet"
