"""Measure the ANSWERS (not just retrieval): run every question through the full
pipeline, including the LLM, and check the result against the labels.

Per question:
  answerable   -> did it answer? are the key facts in the answer? does it cite a correct page?
  conflicting  -> did it report "conflicting_evidence"?
  unanswerable -> did it abstain ("insufficient_evidence")?

Simple, transparent checks (string matching), no second LLM acting as judge. Their
limit: a correct answer worded differently ("two times" vs "twice") counts as a miss,
so key-fact coverage is a LOWER bound. Failures are listed so a human can review them.
"""

import hashlib
import json
import statistics
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from insureassist.evaluation.retrieval_eval import Question, git_commit
from insureassist.generation.answer import Answer, Citation
from insureassist.generation.llm import GenerationError, RateLimitError
from insureassist.generation.prompt import SYSTEM_INSTRUCTION

AnswerFn = Callable[[str], Answer]


def prompt_fingerprint() -> str:
    """Short hash of the rules sent to the LLM. Changing the prompt changes this."""
    return hashlib.sha256(SYSTEM_INSTRUCTION.encode()).hexdigest()[:12]


class AnswerCache:
    """Saves each successful answer in a JSONL file so evaluation can be resumed
    (free-tier quota: 20 requests/day) and re-scored without new LLM calls.

    An answer is reused only if EVERYTHING that influences it is the same: the
    question and `settings` (model, prompt fingerprint, retriever, top_k, a fingerprint
    of the indexed chunks, ...). Change any of them and the old answer is not reused.
    """

    def __init__(self, path: Path, settings: dict[str, Any]) -> None:
        self.path = path
        self._settings = json.dumps(settings, sort_keys=True)
        self._answers: dict[str, Answer] = {}
        self.hits = 0
        if path.exists():
            for line in path.read_text(encoding="utf-8").splitlines():
                record = json.loads(line)
                answer = record["answer"]
                answer["citations"] = [Citation(**c) for c in answer["citations"]]
                self._answers[record["key"]] = Answer(**answer)

    def _key(self, question: str) -> str:
        return hashlib.sha256(f"{self._settings}|{question}".encode()).hexdigest()

    def wrap(self, answer_fn: AnswerFn) -> AnswerFn:
        """Return a function that answers from the cache when possible."""

        def cached(question: str) -> Answer:
            key = self._key(question)
            if key in self._answers:
                self.hits += 1
                return self._answers[key]
            answer = answer_fn(question)  # errors propagate and are NOT cached
            self._answers[key] = answer
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as file:
                file.write(json.dumps({"key": key, "answer": asdict(answer)}) + "\n")
            return answer

        return cached


def normalize(text: str) -> str:
    """Lowercase, drop "$" and thousands commas, collapse spaces: "$1,500" -> "1500"."""
    text = text.lower().replace("$", "").replace(",", "")
    return " ".join(text.split())


def fact_found(fact: str, answer: str) -> bool:
    return normalize(fact) in normalize(answer)


@dataclass
class AnswerResult:
    id: str
    category: str
    question: str
    status: str  # answered | insufficient_evidence | conflicting_evidence | error
    answer: str
    relevant: list[str]
    cited: list[str]
    facts_missing: list[str]
    checks: dict[str, float] = field(default_factory=dict)
    generation_ms: int = 0
    error: str | None = None


def score(question: Question, answer: Answer) -> AnswerResult:
    relevant = {f"{r.doc} p{r.page}" for r in question.relevant}
    cited = list(dict.fromkeys(f"{c.filename} p{c.page_number}" for c in answer.citations))
    missing = [f for f in question.key_facts if not fact_found(f, answer.answer)]
    checks: dict[str, float] = {}

    if question.answerable:
        checks["answered"] = float(answer.status != "insufficient_evidence")
        if question.key_facts:
            found = len(question.key_facts) - len(missing)
            checks["key_fact_coverage"] = found / len(question.key_facts)
        checks["citation_hit"] = float(bool(relevant & set(cited)))
        if cited:  # precision only makes sense when something was cited
            checks["citation_precision"] = len(relevant & set(cited)) / len(cited)
        if question.category == "conflicting":
            checks["conflict_detected"] = float(answer.status == "conflicting_evidence")
    else:
        checks["abstained"] = float(answer.status == "insufficient_evidence")

    return AnswerResult(
        id=question.id,
        category=question.category,
        question=question.question,
        status=answer.status,
        answer=answer.answer,
        relevant=sorted(relevant),
        cited=cited,
        facts_missing=missing,
        checks=checks,
        generation_ms=answer.generation_ms,
    )


@dataclass
class AnswerReport:
    name: str
    created_at: str
    git_commit: str
    config: dict[str, Any]
    summary: dict[str, float]
    questions: list[AnswerResult]
    questions_planned: int = 0
    stopped_reason: str | None = None  # set when the run ended early (quota reached)


def evaluate_answers(
    name: str,
    questions: list[Question],
    answer_fn: AnswerFn,
    config: dict[str, Any],
    delay_seconds: float = 0.0,
    on_progress: Callable[[AnswerResult], None] | None = None,
) -> AnswerReport:
    results: list[AnswerResult] = []
    stopped_reason = None
    for number, question in enumerate(questions):
        if number and delay_seconds:
            time.sleep(delay_seconds)  # stay under the API rate limit
        try:
            result = score(question, answer_fn(question.question))
        except RateLimitError as error:
            # Quota used up: every further call would fail too. Stop and keep what we have.
            stopped_reason = f"stopped at {question.id}: {error}"
            break
        except GenerationError as error:
            # One failed call doesn't stop the run; it's counted as a failure.
            result = AnswerResult(
                id=question.id,
                category=question.category,
                question=question.question,
                status="error",
                answer="",
                relevant=[f"{r.doc} p{r.page}" for r in question.relevant],
                cited=[],
                facts_missing=list(question.key_facts),
                checks={"answered": 0.0} if question.answerable else {"abstained": 0.0},
                error=str(error),
            )
        results.append(result)
        if on_progress:
            on_progress(result)

    return AnswerReport(
        name=name,
        created_at=datetime.now(UTC).isoformat(timespec="seconds"),
        questions_planned=len(questions),
        stopped_reason=stopped_reason,
        git_commit=git_commit(),
        config={**config, "prompt_sha256": prompt_fingerprint()},
        summary=summarize(results),
        questions=results,
    )


def summarize(results: list[AnswerResult]) -> dict[str, float]:
    def mean_of(check: str) -> float:
        values = [r.checks[check] for r in results if check in r.checks]
        return round(statistics.mean(values), 3) if values else float("nan")

    times = sorted(r.generation_ms for r in results if r.generation_ms)
    return {
        "answer_rate": mean_of("answered"),
        "key_fact_coverage": mean_of("key_fact_coverage"),
        "citation_hit_rate": mean_of("citation_hit"),
        "citation_precision": mean_of("citation_precision"),
        "conflict_detection": mean_of("conflict_detected"),
        "abstention_accuracy": mean_of("abstained"),
        "errors": float(sum(1 for r in results if r.status == "error")),
        "generation_ms_p50": float(times[len(times) // 2]) if times else 0.0,
        "generation_ms_p95": float(times[int(len(times) * 0.95)]) if times else 0.0,
    }


def save_report(report: AnswerReport, folder: Path) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    stamp = report.created_at.replace("-", "").replace(":", "").replace("T", "-")[:15]
    path = folder / f"{stamp}-{report.name}.json"
    path.write_text(json.dumps(asdict(report), indent=2), encoding="utf-8")
    return path
