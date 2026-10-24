# InsureAssist — API Specification (v1)

| Field | Value |
|---|---|
| Status | **Draft for M0 approval** (implemented in M5; FastAPI generates an OpenAPI document that must match this spec) |
| Base path | `/api/v1` |
| Last updated | 2026-10-18 |
| Related | [requirements.md](requirements.md), [security-controls.md](security-controls.md), [data-models.md](data-models.md) |

## 1. Conventions

- JSON request and response bodies (UTF-8), except `POST /auth/token` (form) and `POST /documents` (multipart).
- Authentication: `Authorization: Bearer <JWT>` on every endpoint except `health/*` and `auth/token`.
- Each response carries an `X-Request-ID` header. A client-supplied `X-Request-ID` is accepted only if it matches `^[A-Za-z0-9-]{8,64}$`; otherwise the server generates a UUID4.
- Timestamps are ISO 8601 UTC. IDs are UUID strings.
- Pagination: `limit` (1–100, default 20) and `offset` (≥ 0). Responses include `total`.
- Unknown JSON fields are rejected (`extra="forbid"`).

## 2. Error model

Every error response has the same shape:

```json
{
  "error": {
    "code": "duplicate_document",
    "message": "A document with identical content already exists.",
    "request_id": "6f1c2a0e-…",
    "details": {}
  }
}
```

`details` is optional and only contains safe, client-relevant data (e.g. field validation errors with `loc` and `msg`; never input values or internals).

| HTTP | `code` | When |
|---|---|---|
| 400 | `bad_request` | Malformed request that is not a schema error. |
| 401 | `authentication_required` | Missing bearer token. |
| 401 | `invalid_credentials` | Login failed. The same message is used for an unknown user, a wrong password or an inactive user. |
| 401 | `token_invalid` | Bad signature, expired, wrong `iss`/`aud`, unsupported `alg`, inactive user. |
| 403 | `forbidden` | Authenticated but the role is insufficient for the **endpoint**. |
| 404 | `not_found` | Resource does not exist **or the caller is not authorized to see it** (no existence oracle). |
| 409 | `duplicate_document` | Checksum already exists. |
| 409 | `duplicate_feedback` | Feedback already submitted for this query. |
| 409 | `invalid_state` | e.g. deleting a document that is already `deleting`. |
| 413 | `payload_too_large` | Body exceeds the upload or JSON size limit. |
| 415 | `unsupported_media_type` | Not a PDF (extension or magic bytes). |
| 422 | `validation_error` | Pydantic validation failure. |
| 422 | `invalid_document` | PDF is encrypted, corrupt, has zero pages or exceeds `MAX_PDF_PAGES`. |
| 429 | `rate_limited` | Includes a `Retry-After` header. |
| 500 | `internal_error` | Unexpected error; generic message only. |
| 503 | `llm_unavailable` | Provider timeout or failure after retries. |
| 503 | `index_not_ready` | Index not loaded or stale. |

## 3. Roles × endpoints (authorization matrix)

| Endpoint | anonymous | viewer | editor | admin |
|---|:-:|:-:|:-:|:-:|
| `GET /health/live`, `GET /health/ready` | ✅ | ✅ | ✅ | ✅ |
| `POST /auth/token` | ✅ | ✅ | ✅ | ✅ |
| `GET /auth/me` | ❌401 | ✅ | ✅ | ✅ |
| `POST /documents` | ❌401 | ❌403 | ✅ | ✅ |
| `GET /documents`, `GET /documents/{id}` | ❌401 | ✅ authorized docs only | ✅ authorized docs only | ✅ all |
| `DELETE /documents/{id}` | ❌401 | ❌403 | ✅ own docs (others → 404 if unreadable, 403 if readable but not owned) | ✅ |
| `POST /chat/query` | ❌401 | ✅ | ✅ | ✅ (+ diagnostics) |
| `POST /feedback` | ❌401 | ✅ own queries | ✅ own queries | ✅ own queries |
| `GET /admin/evaluations` | ❌401 | ❌403 | ❌403 | ✅ |

This matrix is tested exhaustively (TS-AUTHZ-01 in [test-strategy.md](test-strategy.md)).

## 4. Endpoints

### 4.1 `GET /api/v1/health/live`

Reports whether the process is up. No dependencies are checked.
`200 {"status": "ok"}`

### 4.2 `GET /api/v1/health/ready`

Checks whether the service can handle traffic. Gemini is **not** called; only whether it is configured is reported.

```json
200 / 503
{
  "status": "ready" | "not_ready",
  "checks": {
    "database": "ok" | "error",
    "embedding_model": "ok" | "loading" | "error",
    "vector_index": "ok" | "stale" | "missing" | "error",
    "keyword_index": "ok" | "error",
    "llm_configured": true
  }
}
```

The response has no version strings, hostnames or error messages.

### 4.3 `POST /api/v1/auth/token`

OAuth2 password flow. `Content-Type: application/x-www-form-urlencoded`. Fields: `username` (email), `password` (max 256 chars). Rate limited by `RATE_LIMIT_LOGIN_PER_MINUTE` per client IP + username.

```json
200 {"access_token": "<jwt>", "token_type": "bearer", "expires_in": 900}
```

Errors: 401 `invalid_credentials`, 422, 429.

**JWT claims:** `sub` (user id), `iss` = `JWT_ISSUER`, `aud` = `JWT_AUDIENCE`, `iat`, `nbf`, `exp` (= `iat + JWT_ACCESS_TOKEN_TTL_MINUTES`), `jti`. Algorithm `HS256` only, from an allow-list; `alg` in the header is never trusted for selection. Roles are **not** included in the token; they are loaded from the DB on every request. There are no refresh tokens in v1.

### 4.4 `GET /api/v1/auth/me`

```json
200 {"id": "uuid", "email": "viewer@example.test", "display_name": "Sample Viewer",
     "roles": ["viewer"], "is_active": true}
```

### 4.5 `POST /api/v1/documents`

`multipart/form-data`:

| Field | Type | Required | Rules |
|---|---|---|---|
| `file` | PDF | yes | ≤ `MAX_UPLOAD_MB`, `.pdf`, `%PDF-` magic |
| `title` | string | no | 1–200 chars; defaults to the sanitized filename stem |
| `shared_with_roles` | list[`viewer`\|`editor`] | no | repeated form field; default none (owner + admins only) |
| `shared_with_user_ids` | list[UUID] | no | max 50; unknown or inactive IDs → 422 |

`202 Accepted` → `DocumentResponse` with `status: "processing"`, plus a `Location: /api/v1/documents/{id}` header.

Errors: 403, 409 `duplicate_document` (`details.document_id` is included only if the caller can read the existing document), 413, 415, 422 `invalid_document`, 429.

### 4.6 `GET /api/v1/documents`

Query: `limit`, `offset`, `status` (optional; one of the lifecycle values). Returns only documents the caller may read. Non-admins see only `ready` documents plus their **own** documents in any status.

```json
200 {"items": [DocumentSummary], "total": 3, "limit": 20, "offset": 0}
```

`DocumentSummary`: `id, title, status, page_count, chunk_count, owner_id, created_at, ingested_at`.

### 4.7 `GET /api/v1/documents/{document_id}`

`200 DocumentResponse` = `DocumentSummary` + `original_filename, size_bytes, checksum_sha256, warnings[{code, page, message}], error_code, shared_with_roles[], shared_with_user_ids[] (owner/admin only)`.
404 if missing or unauthorized.

### 4.8 `DELETE /api/v1/documents/{document_id}`

`204 No Content` after vectors are removed and canonical records deleted. 403 / 404 per the matrix; 409 `invalid_state` if the document is already `deleting`.

### 4.9 `POST /api/v1/chat/query`

Rate limited by `RATE_LIMIT_QUERY_PER_MINUTE` per user.

Request `QueryRequest`:

```json
{
  "question": "What is the waiting period for dental coverage?",
  "top_k": 5,
  "document_ids": ["uuid", "..."],
  "include_diagnostics": false
}
```

| Field | Rules |
|---|---|
| `question` | 3 – `MAX_QUESTION_CHARS` (2000) chars after trimming; control characters rejected |
| `top_k` | 1–20; default `RETRIEVAL_TOP_K` |
| `document_ids` | optional, ≤ 50; **intersected** with the authorized set; unauthorized IDs are silently dropped (no oracle) |
| `include_diagnostics` | honoured for admins only; ignored for others |

Response `QueryResponse`:

```json
200 {
  "query_id": "uuid",
  "status": "answered" | "insufficient_evidence" | "conflicting_evidence",
  "answer": "The dental waiting period is 6 months … [1]",
  "citations": [
    {"ref": 1, "document_id": "uuid", "document_title": "Sample Dental Plan",
     "page_number": 4, "chunk_id": "uuid", "snippet": "≤300 chars of the cited chunk"}
  ],
  "disclaimer": "AI-generated from your accessible documents. Verify against the source before relying on it. Not insurance advice or a coverage decision.",
  "model": {"provider": "gemini", "name": "<GEMINI_MODEL>"},
  "latency_ms": {"retrieval": 140, "rerank": 0, "generation": 2100, "total": 2290},
  "diagnostics": null
}
```

- When `status = insufficient_evidence`, `answer` is a fixed abstention message and `citations` is `[]`.
- When `status = conflicting_evidence`, `answer` describes the conflict and `citations` covers each conflicting source.
- `diagnostics` (admin only): `{"candidates": [{chunk_id, document_id, page_number, semantic_rank, semantic_score, bm25_rank, bm25_score, rrf_score, rerank_score}], "config": {...}}`, with no chunk text.

Errors: 401, 422, 429, 503 `llm_unavailable`, 503 `index_not_ready`.

### 4.10 `POST /api/v1/feedback`

```json
{"query_id": "uuid", "rating": "up" | "down", "comment": "optional ≤1000 chars"}
```

`201 {"id": 123, "query_id": "uuid", "rating": "down", "created_at": "…"}`
404 if the `query_id` does not exist or belongs to another user; 409 `duplicate_feedback`.

### 4.11 `GET /api/v1/admin/evaluations`

Query: `limit`, `offset`, `dataset_version` (optional).

```json
200 {"items": [{"id": "uuid", "started_at": "…", "finished_at": "…", "status": "completed",
                "dataset_name": "insureassist-eval", "dataset_version": "1.0.0",
                "git_commit": "abc1234", "config": {...},
                "metrics": {"recall@5": 0.82, "mrr": 0.71, "ndcg@5": 0.74, "...": 0}}],
     "total": 1, "limit": 20, "offset": 0}
```

## 5. Limits

| Limit | Default | Setting |
|---|---|---|
| Upload size | 20 MB | `MAX_UPLOAD_MB` |
| PDF pages | 500 | `MAX_PDF_PAGES` |
| JSON body | 64 KB | `MAX_JSON_BODY_KB` |
| Question length | 2000 chars | `MAX_QUESTION_CHARS` |
| Query rate | 20 / min / user | `RATE_LIMIT_QUERY_PER_MINUTE` |
| Login rate | 5 / min / IP+username | `RATE_LIMIT_LOGIN_PER_MINUTE` |
| Upload rate | 10 / hour / user | `RATE_LIMIT_UPLOAD_PER_HOUR` |
