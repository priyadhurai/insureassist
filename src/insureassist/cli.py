"""Command-line tool.

uv run insureassist ingest samples/pdfs        # add PDFs to the vector store
uv run insureassist ask "Is flood damage covered?"     # answer with citations (Gemini)
uv run insureassist search "Is flood damage covered?"  # only show matching chunks
uv run insureassist list                       # documents in the store
uv run insureassist delete doc_1cf3888b6671383b
uv run insureassist eval --name baseline-semantic  # score retrieval on the question set
"""

import argparse
import hashlib
import os
import sys
import time
from pathlib import Path

from insureassist.config import settings
from insureassist.evaluation import answer_eval, retrieval_eval
from insureassist.generation.answer import Answer, answer_question
from insureassist.generation.llm import GenerationError, LLMProvider
from insureassist.ingestion.embedder import Embedder
from insureassist.ingestion.pipeline import find_pdfs, ingest_files, save_report
from insureassist.ingestion.vector_db import VectorDB, open_vector_db
from insureassist.ingestion.vector_store import SearchResult, VectorStore
from insureassist.retrieval.search import MODES, Searcher

# Keep the terminal readable: hide model-download progress bars and library chatter.
os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")
os.environ.setdefault("TRANSFORMERS_VERBOSITY", "error")
os.environ.setdefault("HF_HUB_VERBOSITY", "error")


def make_embedder() -> Embedder:
    """Separate function so tests can swap in the fast fake embedder."""
    from insureassist.ingestion.embedder import SentenceTransformerEmbedder

    print(f"Loading embedding model {settings.embedding_model_name} ...", flush=True)
    return SentenceTransformerEmbedder()


def make_llm() -> LLMProvider:
    """Separate function so tests can swap in the FakeProvider."""
    from insureassist.generation.llm import create_llm

    if settings.llm_provider == "huggingface":
        print(f"Loading local model {settings.hf_llm_model} ...", flush=True)
    return create_llm()


def open_store(index_dir: Path, embedder: Embedder) -> VectorDB:
    """The vector database chosen by VECTOR_STORE (--index-dir applies to FAISS only)."""
    if settings.vector_store == "qdrant":
        return open_vector_db(embedder)
    return VectorStore(index_dir, embedder.model_name, embedder.dimension)


# --- commands ----------------------------------------------------------------


def cmd_ask(args: argparse.Namespace) -> int:
    try:
        llm = make_llm()  # fail fast (e.g. missing API key) before loading the big model
        embedder = make_embedder()
        store = open_store(args.index_dir, embedder)
        if store.count == 0:
            print("The store is empty. Run: insureassist ingest <folder>")
            return 1
        searcher = Searcher(store, embedder, mode=args.retriever)
        result = answer_question(args.question, searcher, llm, top_k=args.top_k)
    except GenerationError as error:
        print(f"Error: {error}")  # safe message only; no provider details or secrets
        return 1

    print(f"\nQ: {args.question}\n")
    print(f"[{result.status}]")
    print(result.answer)
    if result.citations:
        print("\nSources:")
        for c in result.citations:
            print(f"  [{c.ref}] {c.filename}, page {c.page_number}")
            print(f'       "{c.snippet[:150]}..."')
    print(f"\n{result.disclaimer}")
    print(
        f"(model: {result.model or 'not called'}, search: {args.retriever}, "
        f"retrieval {result.retrieval_ms} ms, "
        f"generation {result.generation_ms} ms)"
    )
    return 0


def cmd_ingest(args: argparse.Namespace) -> int:
    paths = find_pdfs(Path(args.path))
    if not paths:
        print(f"No PDF files found at {args.path}")
        return 1
    embedder = make_embedder()
    store = open_store(args.index_dir, embedder)
    report = ingest_files(paths, store, embedder)

    print(f"\n{'file':36} {'status':10} {'pages':>5} {'chunks':>6}  details")
    for f in report.files:
        details = f.error_message or "; ".join(f.warnings) or f.document_id or ""
        print(f"{f.filename[:36]:36} {f.status:10} {f.pages:>5} {f.chunks:>6}  {details}")
    print(
        f"\nIngested {report.count('ingested')}, duplicates {report.count('duplicate')}, "
        f"failed {report.count('failed')}. Store now holds {len(store.documents)} documents, "
        f"{store.count} chunks."
    )
    print(f"Report: {save_report(report, args.report_dir)}")
    return 1 if report.count("failed") else 0


def cmd_search(args: argparse.Namespace) -> int:
    embedder = make_embedder()
    store = open_store(args.index_dir, embedder)
    if store.count == 0:
        print("The store is empty. Run: insureassist ingest <folder>")
        return 1
    searcher = Searcher(store, embedder, mode=args.retriever)
    results = searcher.search(args.question, args.top_k).results
    print(f'\nTop {len(results)} chunks for: "{args.question}"\n')
    for rank, r in enumerate(results, start=1):
        doc = store.documents[r.chunk.document_id]
        snippet = " ".join(r.chunk.text.split())[:200]
        print(f"{rank}. score {r.score:.2f} | {doc.filename}, page {r.chunk.page_number}")
        print(f"   {snippet}...\n")
    return 0


def cmd_eval(args: argparse.Namespace) -> int:
    embedder = make_embedder()
    store = open_store(args.index_dir, embedder)
    questions = retrieval_eval.load_questions(args.dataset / "questions.jsonl")
    try:
        retrieval_eval.check_labels(questions, store)
    except retrieval_eval.DatasetError as error:
        print(error)
        return 1

    searcher = Searcher(store, embedder, mode=args.retriever)

    def retrieve(question: str, k: int) -> list[SearchResult]:
        return searcher.search(question, k).results

    config = {
        "retriever": args.retriever,
        "candidate_k": searcher.candidate_k,
        "rrf_k": searcher.rrf_k,
        "embedding_model": embedder.model_name,
        "chunk_size_chars": settings.chunk_size_chars,
        "chunk_overlap_chars": settings.chunk_overlap_chars,
        "indexed_chunks": store.count,
    }
    filenames = {doc_id: doc.filename for doc_id, doc in store.documents.items()}
    report = retrieval_eval.evaluate(
        args.name, questions, retrieve, filenames, args.dataset.name, config
    )

    avg = report.averages
    answerable = len(report.questions)
    print(f"\nRetrieval evaluation '{report.name}' on {answerable} answerable questions")
    print(f"{'k':>4} {'recall@k':>9} {'hit@k':>7} {'nDCG@k':>7}")
    for k in retrieval_eval.K_VALUES:
        print(f"{k:>4} {avg[f'recall@{k}']:>9.3f} {avg[f'hit@{k}']:>7.3f} {avg[f'ndcg@{k}']:>7.3f}")
    print(f"MRR: {avg['mrr']:.3f}\n")
    print(f"{'category':14} {'n':>3} {'recall@5':>9} {'MRR':>6}")
    for category, scores in report.by_category.items():
        n = sum(1 for q in report.questions if q.category == category)
        print(f"{category:14} {n:>3} {scores['recall@5']:>9.3f} {scores['mrr']:>6.3f}")
    misses = [q for q in report.questions if q.scores["recall@5"] < 1]
    print(f"\nNot fully found in top 5 ({len(misses)}):")
    for q in misses:
        print(f"  {q.id} {q.question}\n      want {q.relevant} | got {q.retrieved[:3]}")
    print(
        f"\nBest score, answerable:   {report.best_score_answerable}"
        f"\nBest score, unanswerable: {report.best_score_unanswerable}"
    )
    print(f"\nReport: {retrieval_eval.save_report(report, args.out)}")
    return 0


def cmd_eval_answers(args: argparse.Namespace) -> int:
    try:
        llm = make_llm()
    except GenerationError as error:
        print(f"Error: {error}")
        return 1
    embedder = make_embedder()
    store = open_store(args.index_dir, embedder)
    questions = retrieval_eval.load_questions(args.dataset / "questions.jsonl")[: args.limit]
    searcher = Searcher(store, embedder, mode=args.retriever)
    config = {
        "llm_model": llm.model_name,
        "temperature": settings.llm_temperature,
        "retriever": args.retriever,
        "top_k": settings.retrieval_top_k,
        "min_semantic_score": settings.retrieval_min_semantic_score,
        "embedding_model": embedder.model_name,
        "dataset": args.dataset.name,
    }
    # The cache key also covers the prompt and the indexed chunks: change either,
    # and old answers are not reused.
    chunk_ids = "".join(sorted(c.chunk_id for c in store.chunks))
    cache = answer_eval.AnswerCache(
        args.cache,
        {
            **config,
            "prompt": answer_eval.prompt_fingerprint(),
            "index": hashlib.sha256(chunk_ids.encode()).hexdigest()[:12],
        },
    )
    print(f"Answering {len(questions)} questions with {llm.model_name} ...\n")

    def call_llm(question: str) -> Answer:
        time.sleep(args.delay)  # pause only before REAL calls (cached answers are instant)
        return answer_question(question, searcher, llm)

    def progress(r: answer_eval.AnswerResult) -> None:
        ok = all(v == 1.0 for k, v in r.checks.items() if k != "key_fact_coverage")
        ok = ok and not r.facts_missing
        print(f"  {'ok  ' if ok else 'MISS'} {r.id} [{r.status}] {r.question[:60]}", flush=True)

    report = answer_eval.evaluate_answers(
        args.name,
        questions,
        cache.wrap(call_llm),
        config,
        on_progress=progress,
    )
    print(f"\n{cache.hits} answers came from the cache ({args.cache}).")

    if report.stopped_reason:
        done = len(report.questions)
        print(f"\nRun STOPPED EARLY ({done}/{report.questions_planned} questions): ")
        print(f"  {report.stopped_reason}")
    print(f"\nSummary ({len(report.questions)} questions)")
    for key, value in report.summary.items():
        print(f"  {key:22} {value}")
    problems = [
        r
        for r in report.questions
        if r.status == "error"
        or r.facts_missing
        or any(v < 1.0 for k, v in r.checks.items() if k != "key_fact_coverage")
    ]
    print(f"\nTo review ({len(problems)}):")
    for r in problems:
        print(f"  {r.id} [{r.category}] {r.question}")
        print(f"      status {r.status} | cited {r.cited} | want {r.relevant}")
        if r.facts_missing:
            print(f"      missing facts {r.facts_missing}")
        print(f"      answer: {(r.error or r.answer)[:220]}")
    print(f"\nReport: {answer_eval.save_report(report, args.out)}")
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    embedder = make_embedder()
    store = open_store(args.index_dir, embedder)
    print(f"\n{'document_id':22} {'file':36} {'pages':>5} {'chunks':>6}  ingested")
    for d in store.documents.values():
        print(
            f"{d.document_id:22} {d.filename[:36]:36} {d.page_count:>5} "
            f"{d.chunk_count:>6}  {d.ingested_at}"
        )
    print(f"\n{len(store.documents)} documents, {store.count} chunks")
    return 0


def cmd_delete(args: argparse.Namespace) -> int:
    embedder = make_embedder()
    store = open_store(args.index_dir, embedder)
    removed = store.delete_document(args.document_id)
    if not removed:
        print(f"No document with id {args.document_id}")
        return 1
    store.save()
    print(f"Deleted {args.document_id} ({removed} chunks removed)")
    return 0


# --- argument parsing --------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="insureassist", description="InsureAssist tools")
    parser.add_argument("--index-dir", type=Path, default=settings.index_dir)
    commands = parser.add_subparsers(dest="command", required=True)

    ingest = commands.add_parser("ingest", help="add a PDF file or a folder of PDFs")
    ingest.add_argument("path")
    ingest.add_argument("--report-dir", type=Path, default=Path("data/reports"))
    ingest.set_defaults(handler=cmd_ingest)

    ask = commands.add_parser("ask", help="answer a question with citations (uses Gemini)")
    ask.add_argument("question")
    ask.add_argument("--top-k", type=int, default=settings.retrieval_top_k)
    ask.add_argument("--retriever", choices=MODES, default=settings.retrieval_mode)
    ask.set_defaults(handler=cmd_ask)

    search = commands.add_parser("search", help="find the chunks most similar to a question")
    search.add_argument("question")
    search.add_argument("--top-k", type=int, default=5)
    search.add_argument("--retriever", choices=MODES, default=settings.retrieval_mode)
    search.set_defaults(handler=cmd_search)

    commands.add_parser("list", help="list stored documents").set_defaults(handler=cmd_list)

    evaluate = commands.add_parser("eval", help="score retrieval on the labelled question set")
    evaluate.add_argument(
        "--dataset", type=Path, default=Path("eval/datasets/insureassist-eval-v2")
    )
    evaluate.add_argument("--name", default="run")
    evaluate.add_argument("--retriever", choices=MODES, default=settings.retrieval_mode)
    evaluate.add_argument("--out", type=Path, default=Path("eval/reports/runs"))
    evaluate.set_defaults(handler=cmd_eval)

    answers = commands.add_parser("eval-answers", help="score full answers (calls Gemini)")
    answers.add_argument("--dataset", type=Path, default=Path("eval/datasets/insureassist-eval-v2"))
    answers.add_argument("--name", default="answers")
    answers.add_argument("--retriever", choices=MODES, default=settings.retrieval_mode)
    answers.add_argument("--limit", type=int, default=None, help="only the first N questions")
    answers.add_argument("--delay", type=float, default=1.0, help="seconds between LLM calls")
    answers.add_argument("--out", type=Path, default=Path("eval/reports/experiments"))
    answers.add_argument("--cache", type=Path, default=Path("eval/cache/answers.jsonl"))
    answers.set_defaults(handler=cmd_eval_answers)

    delete = commands.add_parser("delete", help="remove a document from the store")
    delete.add_argument("document_id")
    delete.set_defaults(handler=cmd_delete)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    result: int = args.handler(args)
    return result


if __name__ == "__main__":
    sys.exit(main())
