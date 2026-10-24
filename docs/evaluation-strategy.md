# InsureAssist — Evaluation Strategy

| Field | Value |
|---|---|
| Status | **Draft for M0 approval** (metrics and seed dataset in M2; full framework in M4) |
| Last updated | 2026-10-18 |
| Related | [retrieval-design.md](retrieval-design.md), [test-strategy.md](test-strategy.md), [requirements.md](requirements.md) FR-EVAL-* |

## 1. Goals

1. Measure retrieval quality and grounded-answer quality **reproducibly**.
2. Record a baseline before any optimization, then change **one significant variable at a time**.
3. Catch regressions automatically (CI, against a committed baseline).
4. Keep a written record of failure cases and limitations.

## 2. Corpus

- **Synthetic** insurance documents authored for this project, using fictional organizations (e.g. "Example Mutual Insurance Co."). The documents are kept realistic but fictional: an auto policy, a homeowners policy (HO-style), a dental plan summary, a travel insurance certificate, a claims procedure guide, a glossary, and one document that intentionally **conflicts** with another (e.g. an amended waiting period).
- Sources live in `samples/sources/` as Markdown/YAML. `scripts/generate_sample_pdfs.py` renders them deterministically into `samples/pdfs/` with multiple pages, headers, tables-as-text and numbered sections.
- No real customer data, real policy wording or copyrighted documents.

## 3. Dataset (`eval/datasets/insureassist-eval-v1/`)

`questions.jsonl`, one record per question:

```json
{
  "id": "q-0007",
  "question": "How long is the waiting period for major dental services?",
  "category": "numeric",
  "answerable": true,
  "relevant": [{"doc": "dental-plan-summary", "page": 4, "grade": 2}],
  "reference_answer": "12 months for major services.",
  "key_facts": ["12 months"],
  "access_scenario": null,
  "notes": ""
}
```

- `doc` is the **fixture slug**. It resolves to `document_id` through the checksum of the generated PDF, so labels are independent of the chunking configuration.
- Labels are **page-level** with graded relevance (2 = directly answers, 1 = partially relevant). Chunk-level labels are derived at evaluation time (any chunk on a labelled page counts). This keeps labels valid across chunking experiments.
- Categories and target counts for v1 (about 80 questions):

| Category | Target | Checks |
|---|---|---|
| `factual` (definition, coverage) | 20 | retrieval + key facts |
| `numeric` (limits, deductibles, periods) | 15 | exact key-fact match |
| `keyword` (form numbers, section IDs) | 8 | BM25 contribution |
| `paraphrase` (no lexical overlap) | 10 | semantic contribution |
| `multi_page` | 7 | recall across pages |
| `unanswerable` (topic absent from corpus) | 10 | abstention |
| `conflicting` | 4 | `conflicting_evidence` status |
| `injection` (doc or question) | 3 | contract holds |
| `access_control` (answer exists only in a document the user cannot read) | 3 | abstention, no leakage |

- The dataset is **versioned** with semver in `dataset.yaml` and has a `CHANGELOG.md`. A label change bumps the minor version; question additions or removals bump the major version. Reports are compared only within the same major version.
- Questions are written by the project owner and Claude and reviewed by the owner. Labels are checked by a validation script (each slug and page exists; the key fact appears on a labelled page).

## 4. Metrics

### 4.1 Retrieval (page-level relevance; k ∈ {1, 3, 5, 10})

| Metric | Definition |
|---|---|
| Recall@k | \|relevant pages ∩ pages of the top-k chunks\| / \|relevant pages\| |
| Hit@k | 1 if any relevant page appears in the top k |
| MRR | mean of 1 / rank of the first chunk on a relevant page (0 if none in the top 10) |
| nDCG@k | graded gain (`2^grade − 1`), log2 discount; a page is credited **once** (first occurrence) |

Computed over answerable questions only. Unanswerable questions report the score distribution used for threshold tuning.

### 4.2 Generation

| Metric | How | Deterministic? |
|---|---|---|
| Citation validity | Share of citations that reference supplied evidence (target **100%**) | yes |
| Citation precision | Share of cited (doc, page) pairs that are labelled relevant | yes |
| Citation recall | Share of relevant pages cited (for answerable questions) | yes |
| Key-fact coverage | Normalized string/number match of `key_facts` in the answer | yes |
| Abstention precision/recall | On `unanswerable` + `access_control` vs answerable | yes |
| Conflict detection | `conflicting` questions with status `conflicting_evidence` | yes |
| Faithfulness (optional) | LLM judge: is each answer sentence supported by the cited evidence? (0/1 per sentence) | no: judge model and prompt version recorded; 20% sample reviewed by a human |
| Answer relevance (optional) | LLM judge: 1–5 scale vs the question and reference answer | no |

LLM-judge metrics are **advisory**. CI gates use deterministic metrics only.

### 4.3 Latency

p50 / p95 / max for embedding, FAISS, BM25, fusion, reranking, generation and total. Hardware (CPU model, cores) is recorded with the results. Runs warm up first and the warm-up is excluded.

## 5. Execution

```text
insureassist eval retrieval --dataset eval/datasets/insureassist-eval-v1 --config <name>
insureassist eval generation --dataset … --provider fake|gemini [--judge gemini]
insureassist eval compare --baseline eval/reports/baselines/<file>.json --candidate <file>.json
```

- **Retrieval evaluation** needs no API key and runs offline once the models are cached.
- **Generation evaluation** with `--provider fake` validates the plumbing (citation and abstention logic) in CI. Live runs with `gemini` are manual and need `GEMINI_API_KEY`. They are cached per (prompt hash, model) to save cost when re-running reports.
- Every run writes `eval/reports/runs/<run_id>.json` and a `.md` summary. Each report records the dataset name/version, git commit plus dirty flag, the full retrieval/generation configuration, model names and revisions, library versions, hardware, timings, per-question results and failures.
- From M5, run summaries are also stored in `evaluation_runs` and exposed at `GET /api/v1/admin/evaluations`.

## 6. Experiment plan (M4: one variable at a time)

| # | Experiment | Variable | Keep if |
|---|---|---|---|
| E0 | **Baseline** | MiniLM + FAISS + BM25 + RRF(60), chunk 800/120, top_k 5, no reranker | — |
| E1 | Semantic only / BM25 only | retriever ablation | (shows each retriever's contribution) |
| E2 | Chunk size 500/80 and 1000/150 (1200 exceeds the 256-token model limit; see [chunking-guide §5](chunking-guide.md)) | chunking | nDCG@5 ≥ +0.02 and no recall@10 drop |
| E3 | RRF k = 20 / 100 | fusion constant | nDCG@5 ≥ +0.02 |
| E4 | Cross-encoder reranker | rerank on/off | nDCG@5 ≥ +0.03 and p95 increase ≤ 500 ms |
| E5 | Header/footer stripping | normalization | any gain without regressions |
| E6 | Abstention threshold sweep | `RETRIEVAL_MIN_SEMANTIC_SCORE` | best F1 on abstention with recall on answerable ≥ 0.95 |

Each experiment is written up in `eval/reports/experiments.md` with a hypothesis, the change, the results table, the decision and failure examples.

## 7. Regression gates (CI, from M4)

| Metric (retrieval, offline) | Fail if candidate < baseline − |
|---|---|
| Recall@5 | 0.03 |
| MRR | 0.03 |
| nDCG@5 | 0.03 |
| Citation validity (fake provider) | any value < 1.0 |
| Abstention recall (fake provider) | 0.05 |

Thresholds are recorded in `eval/regression_thresholds.yaml`.

## 7a. Current dataset: v2 (2026-10-19)

The sample set was reduced at the owner's request to a **motor policy, its amendment and a claims guide** (3 documents, 7 pages, 14 chunks). The amendment deliberately changes two values (collision deductible $500 → $750, rental $40 → $50/day). Dataset **v2.0.0**: 28 questions (23 answerable incl. 2 conflicting, 5 unanswerable), [README](../eval/datasets/insureassist-eval-v2/README.md). Baselines are in `eval/reports/baselines/*-v2-*.json`:

| Metric | Semantic (default) | Keyword | Hybrid |
|---|---|---|---|
| Recall@1 | **0.826** | 0.674 | 0.739 |
| Recall@3 | **0.957** | 0.848 | **0.957** |
| MRR | **0.942** | 0.823 | 0.906 |
| nDCG@5 | **0.948** | 0.854 | 0.923 |

For both conflict questions, both sides (policy page 3 + amendment page 1) are in the top 5, i.e. in what the LLM receives. Scores are higher than v1 mainly because there are only 7 pages to choose from. **v1 and v2 scores must not be compared.**

The sections below describe dataset **v1** (6 documents, 36 questions). Its files are archived in `eval/archive/`.

## 7b. History: dataset v1 and baseline (2026-10-18)

- Dataset `insureassist-eval-v1` 1.0.0: **36 questions** (31 answerable, 5 unanswerable), page-level binary labels and `key_facts`. The categories differ slightly from the plan in §3; see the [dataset README](../eval/datasets/insureassist-eval-v1/README.md).
- Code: [metrics.py](../src/insureassist/evaluation/metrics.py) and [retrieval_eval.py](../src/insureassist/evaluation/retrieval_eval.py). The automatic label check verifies each key fact appears on its labelled page.
- Command: `uv run insureassist eval --name <run-name>`.

**Baseline E0: semantic only** (MiniLM, 800/120 chunks, 31 chunks on 17 pages, `IndexFlatIP`). Report: `eval/reports/baselines/*-baseline-semantic.json`

| k | Recall@k | Hit@k | nDCG@k |
|---|---|---|---|
| 1 | 0.677 | 0.742 | 0.742 |
| 3 | 0.887 | 0.903 | 0.813 |
| 5 | 1.000 | 1.000 | 0.861 |

MRR **0.827**. Weakest categories by MRR: **keyword 0.375** (form numbers `AP-RS-12` ranked 4th, `HO-29` 2nd) and **paraphrase 0.707**.

Reading these numbers:
- Recall@5 = 1.0 is inflated: with only 17 pages, the top 5 chunks cover a large part of the corpus. **MRR and Recall@1 are the meaningful signals here.**
- Best retrieval scores overlap: answerable questions range from 0.26 to 0.79, unanswerable ones from 0.07 to **0.57**. A score threshold alone cannot separate them, so the LLM's own abstention (rule 4) is necessary. The 0.25 threshold only screens out clearly off-topic questions.

**Experiment E1: keyword only (BM25)** (`--retriever keyword`; report in `eval/reports/experiments/`)

| Metric | Semantic (E0) | Keyword (E1) |
|---|---|---|
| Recall@1 | 0.677 | 0.710 |
| MRR | 0.827 | 0.832 |
| nDCG@5 | 0.861 | 0.836 |
| MRR: keyword (form codes) | 0.375 | **1.000** |
| MRR: paraphrase | **0.707** | 0.367 |
| MRR: factual | 0.817 | 0.911 |

Finding: the overall scores are almost equal, **but the two methods win on different questions**. BM25 finds exact codes and rare words (AP-RS-12 rank 4 → 1, "evacuation" rank 5 → 2). Semantic search handles reworded questions ("unlivable… hotel" → "additional living expenses": BM25 doesn't find it in the top 10). This is the case for hybrid fusion (E1b, step B3).

The E1 numbers above were re-run after the BM25 match-detection fix in B3 and are unchanged.

**Experiment E1b: hybrid (RRF)**, plus a sweep of `candidate_k` and `rrf_k` (MRR, by category):

| Setting | R@1 | MRR | nDCG@5 | keyword | paraphrase |
|---|---|---|---|---|---|
| semantic only | 0.677 | 0.827 | 0.861 | 0.375 | **0.707** |
| keyword only | 0.710 | 0.832 | 0.836 | **1.000** | 0.367 |
| hybrid, cand 50, rrf_k 60 (default) | 0.677 | 0.826 | 0.862 | 0.750 | 0.450 |
| hybrid, cand 10, rrf_k 60 | 0.677 | 0.830 | 0.862 | 0.750 | 0.475 |
| hybrid, cand 5, rrf_k 5–60 | 0.677 | 0.819 | 0.852 | 0.750 | 0.423 |

**Decision: hybrid is not made the default.** Overall MRR stays within 0.819–0.830 for every setting: the gain on codes is cancelled by the loss on reworded questions. The default stays `RETRIEVAL_MODE=semantic`; hybrid remains available (`--retriever hybrid`).

Caveats: the keyword category has only **2** questions and paraphrase **5**, so one question moves a category MRR by 0.2–0.5. Overall differences below about 0.02 are noise at this dataset size. Candidate next experiments: (a) more keyword and paraphrase questions; (b) a cross-encoder reranker over the hybrid candidates (E4). Hybrid has the best Recall@3 (0.903), so it is a good candidate generator for a reranker.

**Answer evaluation (implemented, not yet run in full).** [answer_eval.py](../src/insureassist/evaluation/answer_eval.py) + `uv run insureassist eval-answers` runs every question through retrieval and Gemini and checks answer rate, key-fact coverage (string match after removing `$` and `,`; a lower bound), citation hit/precision, conflict detection and abstention. The first full run (2026-10-19) hit the free-tier quota (20 requests/day) after a few questions. That partial report was **deleted as invalid**, and the runner now stops at the first quota error and keeps partial results. Answers are cached in `eval/cache/answers.jsonl`, keyed by question + model + prompt fingerprint + retriever settings + index fingerprint, so a run can be resumed the next day and re-scored without new calls.

## 8. Limitations

- The synthetic corpus is small and authored by the project team, so results overstate real-world performance. Real documents would need a fresh, independently labelled dataset.
- LLM-judge scores vary with the judge model and prompt version.
- Page-level labels cannot tell whether the *right part* of a long page was retrieved.
