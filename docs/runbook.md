# InsureAssist — Operations Runbook

| Field | Value |
|---|---|
| Status | **Skeleton (M0).** The procedures below are **planned**, and the commands do not exist yet. Each is verified and finalized in the milestone shown. |
| Last updated | 2026-10-18 |
| Related | [deployment-guide.md](deployment-guide.md), [retrieval-design.md](retrieval-design.md), [security-controls.md](security-controls.md) |

## 1. Health checks (M5)

| Check | Command | Healthy |
|---|---|---|
| Liveness | `curl -s localhost:8000/api/v1/health/live` | `200 {"status":"ok"}` |
| Readiness | `curl -s localhost:8000/api/v1/health/ready` | `200`, all checks `ok` |

| Readiness failure | Likely cause | Action |
|---|---|---|
| `database: error` | Postgres down / wrong `DATABASE_URL` | Check `docker compose ps`, `docker compose logs postgres` |
| `embedding_model: loading` | First start, downloading the model | Wait; check model-cache volume and network access |
| `vector_index: missing` | New install or deleted volume | Run [§3 index rebuild](#3-index-rebuild-m2--m6) |
| `vector_index: stale` | Model/config change or DB restored without the index | Run [§3 index rebuild](#3-index-rebuild-m2--m6) |

## 2. Troubleshooting (filled in as issues are found)

| Symptom | Check |
|---|---|
| 503 `llm_unavailable` | `GEMINI_API_KEY` set? Provider status? Logs at `llm.call` with `error_code` (no raw provider text is logged) |
| API won't start: "JWT_SECRET_KEY must be a random value…" | Set `JWT_SECRET_KEY` in `.env` to `python -c "import secrets; print(secrets.token_urlsafe(48))"` |
| API error about missing tables (`relation "users" does not exist`) | Run `uv run alembic upgrade head` |
| API won't start: address already in use | Another program uses the port; set `API_PORT` in `.env` (default 8010) |
| UI: "The API is not reachable" | Start it: `uv run insureassist serve`; check `API_BASE_URL` in `.env` |
| Lost the demo passwords | `uv run insureassist seed-demo --reset-passwords` (new ones in `data/demo-credentials.txt`) |
| `uv run` fails: insureassist.exe in use | A running `insureassist serve` locks the file on Windows: stop it, or use `uv run --no-sync …` |
| "Qdrant is not reachable at http://localhost:6333" | `docker compose up -d qdrant`; check `docker ps` shows `insureassist-qdrant`; or set `VECTOR_STORE=faiss` |
| "Collection … was built with <other model>" | The embedding model changed. Delete the collection in the Qdrant dashboard (or `curl -X DELETE http://localhost:6333/collections/insureassist_chunks`) and ingest again |
| "Gemini rate limit or quota was reached" | Free tier = 20 requests/day per model (`gemini-3.8-flash`, observed 2026-10-19); resets daily. Check usage in Google AI Studio. Wait for the reset, use a paid tier, or point `GEMINI_MODEL` at another model (separate quota) |
| "Gemini model was not found" | `GEMINI_MODEL` not available to this key (e.g. `gemini-2.5-flash` returns 404 for new users). List the models your key can use; see [requirements OD-05](requirements.md) |
| Document stuck in `processing` | Restart the API (startup reconciliation marks it `failed`); then re-upload |
| Answers always abstain | Index `ok`? User has access to any `ready` documents? Threshold `RETRIEVAL_MIN_SEMANTIC_SCORE` too high? Admin `include_diagnostics=true` |
| 429s | Rate-limit settings; the in-process limiter resets on restart |

Correlate any report with the `X-Request-ID` value: `docker compose logs api | findstr <request_id>`.

## 3. Index rebuild (M2 / M6)

Rebuild when the index is missing or stale, after changing the embedding model or chunking configuration, or after restoring the DB.

```text
insureassist rebuild-index [--rechunk]   # --rechunk regenerates chunks from stored PDFs first
```

The current index keeps serving until the atomic swap. Verify with a readiness check showing `vector_index: ok`, then run the smoke test.

## 4. Backup and restore (M6)

| Data | Backup method | Needed for restore? |
|---|---|---|
| PostgreSQL | `pg_dump -Fc` | **Yes** (canonical) |
| Original PDFs (`appdata/documents`) | Volume archive | **Yes** (re-chunking) |
| FAISS index (`appdata/indexes`) | Optional | No: rebuildable |
| Model cache | No | No: re-downloadable |

Restore order: PostgreSQL → documents volume → `insureassist rebuild-index` → smoke test.

**Deleted documents remain inside older backups until those backups expire.** This is a documented privacy limitation.

## 5. Recovery scenarios (M6)

| Scenario | Procedure |
|---|---|
| API container crash | `docker compose up -d api`; startup reconciliation handles `processing`/`deleting` |
| Corrupt index file | Delete `appdata/indexes/faiss/*`, run a rebuild |
| Lost DB volume | Restore from the latest dump, rebuild the index |
| Leaked `JWT_SECRET_KEY` | Rotate the secret and restart; all tokens are invalidated; review `audit_events` |
| Leaked `GEMINI_API_KEY` | Revoke it in Google AI Studio / Cloud console, set a new key, restart |

## 6. Manual UI checklist (M5)

Log in as each sample role → upload (editor) → see `processing → ready` → ask an answerable question (citations shown) → ask an unanswerable one (abstains) → give feedback → delete → the same question no longer cites the deleted document → a viewer cannot see the editor's private document.
