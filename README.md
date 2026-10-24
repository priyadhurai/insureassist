# InsureAssist — Insurance Knowledge Assistant

A production-oriented Retrieval-Augmented Generation (RAG) application. Authorized users upload insurance PDFs and ask questions. Answers are grounded in retrieved passages and cite the document and page they came from. When the evidence is insufficient, the assistant **abstains** instead of guessing.

> **InsureAssist is a knowledge assistant, not an insurance decision system.** Generated answers can be wrong; always verify them against the cited source. The repository contains only synthetic sample data.

## Project status

| Milestone | Status |
|---|---|
| **M0 — Requirements and architecture** | ✅ Done |
| M1 — PDF ingestion into vector DB | ✅ Done: PDF reading, chunking, embeddings, FAISS store, pipeline + CLI (awaiting approval) |
| M2 — Retrieval | ✅ Evaluation, semantic, BM25 and hybrid built and measured (semantic stays default), awaiting approval |
| M3 — Grounded generation | ✅ Built + live-checked with `gemini-3.8-flash` (`ask` command), awaiting approval |
| M4 — Evaluation | ⚪ Not started |
| M5 — API, UI and security | 🟡 Local demo UI (Streamlit, no login) done; API + security not started |
| M6 — Local release (Docker) | ⚪ Not started |
| M7 — Cloud deployment | ⚪ Not started |

Ingestion into the vector database and similarity search work. Answer generation, the API, the UI and security are planned; see [docs/project-plan.md](docs/project-plan.md). **New here? Start with [docs/explained.md](docs/explained.md)**, a plain-language tour of the architecture, every technology choice and its pros and cons.

## Architecture at a glance

Streamlit UI → FastAPI (JWT + RBAC) → access resolver (PostgreSQL ACLs) → hybrid retrieval (Sentence Transformers + FAISS, BM25, RRF, optional cross-encoder), restricted to authorized documents → grounded prompt → Gemini (`google-genai`) → citation validation and abstention → answer with sources.

Details: [docs/architecture.md](docs/architecture.md).

## Technology stack

Python 3.12 · FastAPI · Pydantic · Streamlit · Gemini via `google-genai` (or a local Hugging Face model) · Sentence Transformers (`all-MiniLM-L6-v2`) · FAISS or Qdrant · rank-bm25 · cross-encoder reranking · PyMuPDF · PostgreSQL · SQLAlchemy 2.x · Alembic · JWT · Argon2id · pytest · HTTPX · Ruff · mypy · Docker Compose.
No LangChain or LlamaIndex: the pipeline is plain Python ([ADR-0002](docs/decisions/0002-frozen-stack-and-supporting-libraries.md)).

## Documentation

| Document | Contents |
|---|---|
| [requirements.md](docs/requirements.md) | Scope, non-goals, assumptions, FR/NFR with acceptance criteria, open decisions |
| [project-plan.md](docs/project-plan.md) | Milestones, deliverables, risks, backlog |
| [architecture.md](docs/architecture.md) | Components, Mermaid diagrams, repository structure |
| [data-models.md](docs/data-models.md) | ERD, tables, constraints, retention |
| [api-specification.md](docs/api-specification.md) | Endpoints, schemas, errors, authorization matrix |
| [security-controls.md](docs/security-controls.md) | Threat model and controls (with implementation status) |
| [ingestion-design.md](docs/ingestion-design.md) | PDF validation, extraction, chunking, IDs |
| [chunking-guide.md](docs/chunking-guide.md) | All chunking strategies, how to choose chunk size, decision guide |
| [retrieval-design.md](docs/retrieval-design.md) | Embeddings, FAISS, BM25, fusion, reranking, ACL filtering |
| [evaluation-strategy.md](docs/evaluation-strategy.md) | Dataset, metrics, experiments, regression gates |
| [test-strategy.md](docs/test-strategy.md) | Test levels, catalogue, quality gates |
| [deployment-guide.md](docs/deployment-guide.md) | Local and cloud deployment (planned) |
| [runbook.md](docs/runbook.md) | Health checks, troubleshooting, backup, rebuild (planned) |
| [decisions/](docs/decisions/) | Architecture Decision Records |

## Prerequisites (from M1)

- Git
- [uv](https://docs.astral.sh/uv/). It installs Python 3.12 for the project: `uv python install 3.12`
- Docker Desktop (PostgreSQL from M5; full stack from M6)
- Optional: a Gemini API key (M3+). Every automated test runs without one.

## Setup

```powershell
git clone <repo-url> insureassist; cd insureassist
uv python install 3.12
uv sync                       # installs dependencies incl. PyTorch (CPU), ~1 GB on disk
Copy-Item .env.example .env   # optional; defaults work. .env is git-ignored
uv run pytest                 # fast tests (fake embedder)
uv run pytest -m slow         # real-model tests (downloads ~90 MB once)
```

## Try it: ingest and search (works now)

**Vector database:** FAISS (files, default) or **Qdrant** (a real vector DB with a web dashboard). For Qdrant, set `VECTOR_STORE=qdrant` in `.env` and start it once:

```powershell
docker compose up -d qdrant     # free, runs locally; data kept in data/qdrant
# dashboard: http://localhost:6333/dashboard
```

```powershell
uv run insureassist ingest samples/pdfs                          # PDF -> chunks -> embeddings -> FAISS
uv run insureassist ask "Is flood damage covered for my house?"  # answer + page citations (needs GEMINI_API_KEY in .env)
uv run insureassist search "Is flood damage covered for my house?"  # only the retrieved passages
uv run insureassist list                                         # stored documents
uv run insureassist eval --name my-run                           # score retrieval on 27 labelled questions
uv run insureassist eval --retriever hybrid --name hybrid        # same, with keyword or hybrid search
uv run insureassist delete <document_id>
uv run insureassist eval-answers                                 # score full answers (Gemini; cached, resumable)
uv run streamlit run src/insureassist/ui/app.py                  # web UI at http://localhost:8501
```

**Web UI (local demo):** ask questions in the browser, see the answer, a status badge and the sources (file, page, snippet), and upload PDFs. The **Vector DB** page (sidebar, or http://localhost:8501/?page=vector-db) shows what is stored: every FAISS row with its chunk text and page, one row's 384 numbers, and a 2D map of meaning where you can place a question and see its nearest rows. There is **no login yet**: it listens on localhost only, and every document is visible. Milestone M5 adds the API with login and document permissions.

The index is saved in `data/index/` and ingestion reports in `data/reports/` (both git-ignored). Running `ingest` again skips files that are already stored (duplicates). `ask` sends only the retrieved passages (not whole documents) to Gemini, and answers "not enough information" when the evidence is weak.

Without uv: create a Python 3.12 virtual environment, then run `pip install -e ".[dev]"`.

## Troubleshooting

| Problem | Fix |
|---|---|
| `python --version` shows 3.13/3.14 | Use `uv run …` (the project pins 3.12 via `.python-version`), or activate `.venv` |
| Anaconda Python is used instead of the project venv | Run commands via `uv run`, or deactivate conda (`conda deactivate`) |

More operational guidance: [docs/runbook.md](docs/runbook.md).

## Data and licensing notes

- Sample documents ([samples/](samples/README.md)): **2 synthetic documents**, a motor policy and a claims guide, from a fictional insurer ("Example Mutual Insurance Co."). Do not add confidential or real policyholder data.
- PyMuPDF is AGPL-3.0 licensed ([ADR-0002](docs/decisions/0002-frozen-stack-and-supporting-libraries.md)).
- Evidence text is sent to the Gemini API when generating answers. Check the provider's data-use terms for your API tier.
