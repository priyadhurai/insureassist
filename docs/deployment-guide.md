# InsureAssist — Deployment Guide

| Field | Value |
|---|---|
| Status | **Skeleton (M0).** Nothing here can be run yet. The Docker artefacts are delivered in M6 and cloud deployment in M7. Each section is marked with the milestone that makes it real. |
| Last updated | 2026-10-18 |
| Related | [architecture.md §6](architecture.md), [runbook.md](runbook.md), [security-controls.md](security-controls.md) |

## 1. Local development without Docker (from M1)

Prerequisites: Git, [uv](https://docs.astral.sh/uv/) (≥ 0.5), Python 3.12 (installed by uv), Docker Desktop (PostgreSQL from M5).

```powershell
uv python install 3.12
uv sync                      # creates .venv from uv.lock
Copy-Item .env.example .env  # then edit values; never commit .env
uv run pytest
```

## 1b. Qdrant vector database (implemented)

`docker-compose.yml` currently contains one service: **Qdrant** (`qdrant/qdrant:v1.19.2`), bound to `127.0.0.1:6333/6334`, with data in `./data/qdrant` (git-ignored). Start it with `docker compose up -d qdrant`, then set `VECTOR_STORE=qdrant`. The dashboard is at http://localhost:6333/dashboard. Do not expose these ports without configuring a Qdrant API key. See [ADR-0011](decisions/0011-qdrant-vector-database.md).

## 2. Local Docker Compose (M6, planned)

| Service | Image | Port | Volumes | Health |
|---|---|---|---|---|
| `postgres` | `postgres:16` (pinned digest at M6) | 5432 (localhost only) | `pgdata` | `pg_isready` |
| `api` | built from `docker/api.Dockerfile` (`python:3.12-slim`, CPU-only torch, non-root) | 8000 | `appdata:/app/data`, `model-cache:/home/app/.cache/huggingface` | `GET /api/v1/health/ready` |
| `ui` | built from `docker/ui.Dockerfile` | 8501 | — | Streamlit `/_stcore/health` |
| `postgres-test` | `postgres:16` (profile `test`) | 5433 | tmpfs | `pg_isready` |

Planned startup sequence: `docker compose up -d postgres` → `docker compose run --rm api alembic upgrade head` → `docker compose run --rm api insureassist create-user …` → `docker compose up -d api ui` → `scripts/smoke_test.py`.

Configuration is supplied at runtime from `.env` (`env_file`), never baked into images. `.dockerignore` excludes `.env`, `data/`, `.venv/`, `eval/reports/runs/`.

**API worker count is 1** ([ADR-0009](decisions/0009-single-process-indexes-and-background-ingestion.md)).

## 3. Cloud deployment (M7, not decided)

No provider has been selected. M7 starts with a trade-off document covering AWS, Azure and GCP:

- identity and secrets (IAM / Entra ID / Cloud IAM; Secrets Manager / Key Vault / Secret Manager)
- region and data residency
- networking (private DB, TLS at the load balancer)
- managed PostgreSQL
- document and index storage (object storage + local disk for FAISS, or a mounted volume)
- monitoring and logs
- CI/CD
- cost at the expected usage
- backup, recovery and rollback

The single-process index constraint will be revisited there.
