# InsureAssist — Project Plan

| Field | Value |
|---|---|
| Status | **M0 in review** (awaiting owner approval) |
| Last updated | 2026-10-18 |
| Method | Milestone-gated: each milestone ends with a demo of files changed, commands, actual test results and limitations, followed by **explicit owner approval** before the next one starts. |

## 1. Milestone checklist

| MS | Name | Status | Approved |
|---|---|---|---|
| M0 | Requirements and architecture | ✅ Accepted by owner ("ok"), simplicity requested | ☑ |
| M1 | PDF ingestion **into the vector DB** (owner request: embeddings + FAISS pulled forward from M2) | 🟡 All 5 steps done, awaiting approval | ☐ |
| — | Qdrant vector DB (ADR-0011) | ✅ Selectable via `VECTOR_STORE`; same 25 store tests pass on FAISS and Qdrant; identical evaluation results | — |
| M2 | Retrieval (BM25, hybrid, metrics) | 🟡 B1 done (dataset, metrics, semantic baseline MRR 0.827). B2 done (BM25: MRR 0.832; keyword category 0.375 → 1.000, paraphrase 0.707 → 0.367). B3 done: hybrid RRF built and measured; no gain (MRR 0.826), so semantic stays the default; abstention now also uses keyword matches. Awaiting approval. | ☐ |
| M3 | Grounded generation | 🟡 Built and live-checked (2026-10-18, `gemini-3.8-flash`): 6/6 manual cases correct (answered ×3, conflict ×1, abstain ×3). 18 tests (fake LLM). Awaiting approval. | ☐ |
| M4 | Evaluation and quality improvement | ⚪ Not started | ☐ |
| M5 | API, UI and security | 🟡 Interim local demo UI (Streamlit, no login, localhost only, 7 tests incl. AppTest). API, auth and permissions not started. | ☐ |
| M6 | Local release | ⚪ Not started | ☐ |
| M7 | Cloud deployment planning and implementation | ⚪ Not started | ☐ |

## 2. Milestones

### M0 — Requirements and architecture

- **Deliverables:** every document in `docs/`, ADRs 0001–0010, `README.md`, `.env.example`, `.gitignore`, a local Git repository.
- **Acceptance:** the documents agree with one another (IDs, statuses, roles, env vars, error codes); each FR has a testable AC; open decisions (OD-01…07) are listed.
- **Dependencies:** none.

### M1 — PDF ingestion

- **Deliverables:** `pyproject.toml` (+ lock), `config.py`, logging, `ingestion/*`, `storage/jsonl_store.py`, CLI `ingest`, fixture generator, synthetic sample sources/PDFs, tests TS-ING-01..10.
- **Acceptance:** requirements FR-ING-01..08; ruff, mypy and pytest green; [ingestion-design.md](ingestion-design.md) updated to match the implementation.
- **Dependencies:** M0 approval; OD-01, OD-02, OD-03, OD-04 resolved; Python 3.12 installed.

### M2 — Retrieval

- **Deliverables:** `retrieval/*` (embedder, FAISS, BM25, hybrid, ACL-restricted search), CLI `rebuild-index`, evaluation metric functions, seed evaluation dataset (~30 questions), baseline retrieval report.
- **Acceptance:** FR-RET-01..07, FR-EVAL-02; baseline Recall@k/MRR/nDCG recorded; rebuild equivalence test passes; deleted/unauthorized documents are never returned.
- **Dependencies:** M1. Download of the embedding model (~90 MB) once.

### M3 — Grounded generation

- **Deliverables:** `generation/*` (provider interface, Gemini adapter, `FakeProvider`, prompts, structured output, citation validation, abstention/conflict), a query service usable from the CLI (`insureassist ask`).
- **Acceptance:** FR-GEN-01..08; all tests pass without an API key; one manual live check with a key (if available) documented with actual output.
- **Dependencies:** M2; `GEMINI_API_KEY` for the live check only; verify the current Gemini model names (OD-05).

### M4 — Evaluation and quality improvement

- **Deliverables:** full dataset v1 (~80 questions), evaluation runners, reports, compare + regression thresholds, experiments E0–E6 write-up, reranker implementation (kept only if justified).
- **Acceptance:** reproducible runs; reports record model/config versions; failure examples documented; reranker decision recorded as an ADR.
- **Dependencies:** M3; Gemini key for live generation evaluation (optional judge).

### M5 — API, UI and security

- **Deliverables:** SQLAlchemy models + Alembic migrations, PostgreSQL repositories, JSONL → DB import, auth (Argon2id/JWT/RBAC), all 11 endpoints, middleware (request ID, limits, rate limit, errors), audit events, document lifecycle + index sync, Streamlit UI, `create-user` CLI, SEC-01..12, TS-API/AUTHZ/DB tests.
- **Acceptance:** FR-AUTH-*, FR-DOC-*, FR-QRY-01, FR-FB-01, FR-UI-*, FR-OPS-01/03; the API matches [api-specification.md](api-specification.md); security tests green.
- **Dependencies:** M4; Docker for PostgreSQL.

### M6 — Local release

- **Deliverables:** Dockerfiles, `docker-compose.yml` (api, ui, postgres, postgres-test profile), volumes, health checks, CI workflow (lint, type, test, audit, image scan), smoke test, backup/restore scripts, release checklist, finalized [runbook.md](runbook.md) and [deployment-guide.md](deployment-guide.md).
- **Acceptance:** clean-setup start works; no secrets in images; smoke and E2E tests pass; data survives a restart; recovery and rebuild procedures are tested.
- **Dependencies:** M5; Docker Desktop.

### M7 — Cloud planning and implementation

- **Deliverables:** AWS/Azure/GCP trade-off analysis (security/identity, region, networking, managed PostgreSQL, storage, secrets, monitoring, pipeline, cost, backup/rollback) → recommendation → (after approval) provider-specific IaC.
- **Acceptance:** the owner approves the recommendation before any provider-specific work.
- **Dependencies:** M6.

## 3. Risks

| ID | Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|---|
| R-01 | Python 3.12 not installed (machine has 3.13 Anaconda / 3.14) | Certain | Medium | `uv python install 3.12` (ADR-0010); Docker image `python:3.12-slim` |
| R-02 | PyTorch (pulled in by sentence-transformers) is a large download / image size | High | Medium | CPU-only torch index in Docker; cache the model volume |
| R-03 | PyMuPDF AGPL-3.0 licence obligations if deployed publicly | Medium | High | Owner acknowledgement (OD-04); keep the source public, or a commercial licence, or swap the parser via an ADR |
| R-04 | Gemini model names/SDK behaviour change | Medium | Medium | Model set from configuration; adapter isolated; verify at M3 |
| R-05 | Prompt injection cannot be fully prevented | Certain | Medium | Layered controls, no tools, inert rendering, SEC-07 tests, documented residual risk |
| R-06 | Synthetic evaluation overstates quality | High | Medium | Documented limitation; category coverage; human review |
| R-07 | In-process indexes block horizontal scaling | Certain (by design) | Low (v1) | ADR-0009; M7 revisits (external vector store or shared index service) |
| R-08 | Windows vs Linux differences (paths, `faiss-cpu` wheels, file locking with `os.replace`) | Medium | Medium | `pathlib` everywhere; CI on Linux + local Windows runs; retry on Windows replace errors |
| R-09 | Scope creep (OCR, SSO, multi-turn) | Medium | Medium | Non-goals list; approval gates |
| R-10 | LLM API cost **and quota** during evaluation | **Occurred** (2026-10-19) | Medium | Gemini free tier: **20 requests/day per model** for `gemini-3.8-flash` (quota id `GenerateRequestsPerDayPerProjectPerModel-FreeTier`). The answer evaluation (27 questions in v2) cannot finish in one day. Mitigations: fake provider in CI; batch runs stop at the first 429 (`RateLimitError`); options: resumable answer cache, a paid tier, or a separate model for evaluation |

## 4. M0 implementation backlog

| ID | Item | Status |
|---|---|---|
| M0-01 | Assess repository and environment | ✅ Done (empty dir, no git; Python 3.13.9 default; uv 0.12.17; Docker 29.1.3) |
| M0-02 | Requirements, scope, non-goals, assumptions, open decisions | ✅ [requirements.md](requirements.md) |
| M0-03 | Architecture + Mermaid component/sequence/lifecycle/deployment diagrams | ✅ [architecture.md](architecture.md) |
| M0-04 | Target repository structure | ✅ [architecture.md §7](architecture.md) |
| M0-05 | Data models and ERD | ✅ [data-models.md](data-models.md) |
| M0-06 | API contracts | ✅ [api-specification.md](api-specification.md) |
| M0-07 | Security threat model and controls | ✅ [security-controls.md](security-controls.md) |
| M0-08 | Ingestion and retrieval designs | ✅ [ingestion-design.md](ingestion-design.md), [retrieval-design.md](retrieval-design.md) |
| M0-09 | Evaluation and test strategy | ✅ [evaluation-strategy.md](evaluation-strategy.md), [test-strategy.md](test-strategy.md) |
| M0-10 | Deployment guide and runbook skeletons | ✅ [deployment-guide.md](deployment-guide.md), [runbook.md](runbook.md) |
| M0-11 | ADRs 0001–0010 | ✅ [decisions/](decisions/) |
| M0-12 | `README.md`, `.env.example`, `.gitignore`, `git init` | ✅ |
| M0-13 | Owner review: resolve OD-01…OD-07 | ⏳ Awaiting owner |

## 5. M1 build steps (revised: ingestion straight into FAISS)

| Step | File | Status |
|---|---|---|
| 1 | `ingestion/pdf_reader.py`: validate + extract pages | ✅ 14 tests passing |
| 2 | `ingestion/chunker.py`: normalize + page-bounded chunks | ✅ 22 tests passing |
| 3 | `ingestion/embedder.py`: Sentence Transformers | ✅ 4 fast + 3 slow tests passing |
| — | Synthetic sample corpus: 6 documents in `samples/sources/` → `samples/pdfs/` (17 pages, 31 chunks) via `scripts/generate_sample_pdfs.py` | ✅ |
| 4 | `ingestion/vector_store.py`: FAISS + chunk metadata file, duplicates | ✅ 10 tests passing |
| 5 | `ingestion/pipeline.py` + `cli.py`: ingest a folder, try a search | ✅ 6 tests passing; real run: 6 PDFs → 31 chunks in ~15 s |

M2 then focuses on BM25, hybrid search, ACL filtering and the retrieval metrics.

### Original M1 backlog preview

1. `uv python install 3.12`; `uv init` → `pyproject.toml` with the dependencies needed for M1 only (pymupdf, pydantic, pydantic-settings; dev: pytest, ruff, mypy, pytest-cov).
2. `config.py` (`Settings`) + `observability/logging.py`.
3. Fixture generator + synthetic sample sources.
4. Validation → extraction → normalization → chunking → IDs → report → JSONL store.
5. CLI `insureassist ingest`.
6. Tests TS-ING-01..10; ruff/mypy clean; update the docs.
