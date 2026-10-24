"""Tests for answer evaluation (no API key: answers are built by hand or by a fake)."""

import math
from pathlib import Path

import pytest

from insureassist.evaluation.answer_eval import AnswerCache, evaluate_answers, fact_found, score
from insureassist.evaluation.retrieval_eval import Question
from insureassist.generation.answer import Answer, Citation
from insureassist.generation.llm import GenerationError, RateLimitError


def _q(qid: str, category: str, relevant: list[tuple[str, int]], facts: list[str]) -> Question:
    return Question.model_validate(
        {
            "id": qid,
            "category": category,
            "question": f"question {qid}",
            "relevant": [{"doc": d, "page": p} for d, p in relevant],
            "key_facts": facts,
        }
    )


def _cite(filename: str, page: int) -> Citation:
    return Citation("E1", "doc", filename, page, "chunk", "snippet")


@pytest.mark.parametrize(
    ("fact", "answer", "found"),
    [
        ("$1,500", "The limit is 1,500 dollars.", True),
        ("$1,500", "It is $1500.", True),
        ("HO-17", "requires form ho-17", True),
        ("12 months", "twelve months", False),  # known limit of string matching
    ],
)
def test_fact_found(fact: str, answer: str, found: bool) -> None:
    assert fact_found(fact, answer) is found


def test_score_correct_answer() -> None:
    q = _q("q1", "factual", [("auto.pdf", 3)], ["$500"])
    a = Answer("answered", "The deductible is $500 [E1].", [_cite("auto.pdf", 3)])

    r = score(q, a)

    assert r.checks == {
        "answered": 1.0,
        "key_fact_coverage": 1.0,
        "citation_hit": 1.0,
        "citation_precision": 1.0,
    }


def test_score_wrong_page_and_missing_fact() -> None:
    q = _q("q1", "factual", [("auto.pdf", 3)], ["$500", "per claim"])
    a = Answer("answered", "It is $500.", [_cite("auto.pdf", 3), _cite("home.pdf", 2)])

    r = score(q, a)

    assert r.checks["key_fact_coverage"] == 0.5
    assert r.checks["citation_precision"] == 0.5  # one of two citations is correct
    assert r.facts_missing == ["per claim"]


def test_score_conflict_and_unanswerable() -> None:
    conflict = _q("q2", "conflicting", [("a.pdf", 1), ("b.pdf", 1)], [])
    unanswerable = _q("u1", "unanswerable", [], [])

    c = score(conflict, Answer("answered", "x", [_cite("a.pdf", 1)]))
    u = score(unanswerable, Answer("insufficient_evidence", "no", []))

    assert c.checks["conflict_detected"] == 0.0  # answered instead of flagging the conflict
    assert u.checks == {"abstained": 1.0}


def test_errors_are_counted_and_do_not_stop_the_run() -> None:
    questions = [_q("q1", "factual", [("a.pdf", 1)], ["x"]), _q("u1", "unanswerable", [], [])]

    def answer_fn(text: str) -> Answer:
        if text.endswith("q1"):
            raise GenerationError("The language model is unavailable.")
        return Answer("insufficient_evidence", "no", [])

    report = evaluate_answers("t", questions, answer_fn, {})

    assert [r.status for r in report.questions] == ["error", "insufficient_evidence"]
    assert report.summary["errors"] == 1.0
    assert report.summary["answer_rate"] == 0.0
    assert report.summary["abstention_accuracy"] == 1.0
    assert math.isnan(report.summary["conflict_detection"])  # no conflict questions here
    assert len(report.config["prompt_sha256"]) == 12


def test_quota_error_stops_the_run_early() -> None:
    questions = [_q(f"q{n}", "factual", [("a.pdf", 1)], ["x"]) for n in range(1, 5)]
    calls: list[str] = []

    def answer_fn(text: str) -> Answer:
        calls.append(text)
        if len(calls) == 2:
            raise RateLimitError("The Gemini rate limit or quota was reached.")
        return Answer("answered", "x [E1]", [_cite("a.pdf", 1)])

    report = evaluate_answers("t", questions, answer_fn, {})

    assert len(calls) == 2  # questions 3 and 4 were never sent
    assert [r.id for r in report.questions] == ["q1"]
    assert report.questions_planned == 4
    assert report.stopped_reason is not None and "q2" in report.stopped_reason


# --- answer cache -----------------------------------------------------------------------


def test_cache_reuses_answers_and_survives_restart(tmp_path: Path) -> None:
    path = tmp_path / "cache.jsonl"
    calls: list[str] = []

    def answer_fn(text: str) -> Answer:
        calls.append(text)
        return Answer("answered", "It is $500 [E1].", [_cite("auto.pdf", 3)])

    first = AnswerCache(path, {"model": "m1"}).wrap(answer_fn)
    first("q?")
    first("q?")  # same question again -> from the cache
    assert calls == ["q?"]

    restarted = AnswerCache(path, {"model": "m1"})  # e.g. tomorrow, after the quota reset
    answer = restarted.wrap(answer_fn)("q?")
    assert calls == ["q?"]  # still no new LLM call
    assert restarted.hits == 1
    assert answer.citations[0].page_number == 3  # citations restored as objects


def test_cache_is_not_reused_when_settings_change(tmp_path: Path) -> None:
    path = tmp_path / "cache.jsonl"
    calls: list[str] = []

    def answer_fn(text: str) -> Answer:
        calls.append(text)
        return Answer("answered", "x", [])

    AnswerCache(path, {"model": "m1"}).wrap(answer_fn)("q?")
    AnswerCache(path, {"model": "m2"}).wrap(answer_fn)("q?")  # other model -> new call
    assert len(calls) == 2


def test_errors_are_not_cached(tmp_path: Path) -> None:
    path = tmp_path / "cache.jsonl"

    def failing(text: str) -> Answer:
        raise GenerationError("unavailable")

    with pytest.raises(GenerationError):
        AnswerCache(path, {}).wrap(failing)("q?")
    assert not path.exists()
