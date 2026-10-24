"""Security regression tests (SEC-01..12 in docs/security-controls.md)."""

import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

import jwt
import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from insureassist.api.app import create_app
from insureassist.api.deps import Services
from insureassist.api.ratelimit import RateLimiter
from insureassist.auth.passwords import hash_password, validate_new_password, verify_password
from insureassist.auth.tokens import TokenConfig, create_access_token
from insureassist.db.models import Base, User
from insureassist.generation.llm import FakeProvider, GenerationError

Auth = Callable[[str], dict[str, str]]
API = "/api/v1"


# --- SEC-01 passwords -------------------------------------------------------------------


def test_passwords_use_argon2id() -> None:
    stored = hash_password("a long enough password")
    assert stored.startswith("$argon2id$")
    assert verify_password(stored, "a long enough password")
    assert not verify_password(stored, "wrong password!!")
    assert not verify_password(None, "anything at all")  # unknown user
    with pytest.raises(ValueError):
        validate_new_password("short")


# --- SEC-02 tokens ----------------------------------------------------------------------


def _me(client: TestClient, token: str) -> int:
    return client.get(f"{API}/auth/me", headers={"Authorization": f"Bearer {token}"}).status_code


def _user_id(db: Session, role: str) -> uuid.UUID:
    return db.scalars(select(User.id).where(User.email == f"{role}@example.com")).one()


def test_forged_and_expired_tokens_are_rejected(
    client: TestClient, db: Session, services: Services
) -> None:
    TOKENS = services.tokens
    user_id = _user_id(db, "viewer")
    good, _ = create_access_token(user_id, TOKENS)
    assert _me(client, good) == 200

    now = datetime.now(UTC)
    base = {
        "sub": str(user_id),
        "iss": TOKENS.issuer,
        "aud": TOKENS.audience,
        "iat": now,
        "nbf": now,
        "exp": now + timedelta(minutes=5),
        "jti": "x",
    }
    other_secret = "y" * 48
    cases = {
        "tampered signature": good[:-4] + ("AAAA" if not good.endswith("AAAA") else "BBBB"),
        "wrong secret": jwt.encode(base, other_secret, algorithm="HS256"),
        "expired": jwt.encode(
            {**base, "exp": now - timedelta(minutes=5)}, TOKENS.secret, algorithm="HS256"
        ),
        "wrong audience": jwt.encode(
            {**base, "aud": "someone-else"}, TOKENS.secret, algorithm="HS256"
        ),
        "wrong issuer": jwt.encode({**base, "iss": "evil"}, TOKENS.secret, algorithm="HS256"),
        "missing jti": jwt.encode(
            {k: v for k, v in base.items() if k != "jti"}, TOKENS.secret, algorithm="HS256"
        ),
        "alg none": jwt.encode(base, None, algorithm="none"),  # type: ignore[arg-type]
        "garbage": "not-a-token",
    }
    for name, token in cases.items():
        assert _me(client, token) == 401, name


def test_deactivated_user_is_locked_out_at_once(
    client: TestClient, db: Session, services: Services
) -> None:
    user_id = _user_id(db, "viewer")
    token, _ = create_access_token(user_id, services.tokens)
    user = db.get(User, user_id)
    assert user is not None
    user.is_active = False
    db.commit()
    assert _me(client, token) == 401  # the role/active check reads the DB every request


def test_weak_jwt_secret_is_refused() -> None:
    with pytest.raises(ValueError):
        TokenConfig("too-short", "iss", "aud")
    with pytest.raises(ValueError):
        TokenConfig("replace-with-a-random-value-of-at-least-32-bytes", "iss", "aud")


# --- SEC-03 / SEC-04 document access ---------------------------------------------------


def test_unauthorized_text_never_reaches_the_llm(
    client: TestClient, auth: Auth, spy_llm: FakeProvider
) -> None:
    """The most important RAG security test: the viewer asks a question that matches the
    SECRET document best; its text must not appear in anything sent to the LLM."""
    r = client.post(
        f"{API}/chat/query",
        headers=auth("viewer"),
        json={"question": "What is the executive collision deductible?", "top_k": 10},
    )
    assert r.status_code == 200
    assert spy_llm.prompts, "the LLM should have been called with the shared document"
    for prompt in spy_llm.prompts:
        assert "zero dollars" not in prompt  # words that exist ONLY in the secret doc
        assert "TOP SECRET" not in prompt
        assert "secret.pdf" not in prompt
    assert all(c["document_title"] != "Secret policy" for c in r.json()["citations"])


def test_document_filter_cannot_widen_access(
    client: TestClient, auth: Auth, spy_llm: FakeProvider
) -> None:
    secret_id = next(
        d["id"]
        for d in client.get(f"{API}/documents", headers=auth("admin")).json()["items"]
        if d["title"] == "Secret policy"
    )
    r = client.post(
        f"{API}/chat/query",
        headers=auth("viewer"),
        json={"question": "collision deductible", "document_ids": [secret_id]},
    )
    assert r.json()["status"] == "insufficient_evidence"
    assert spy_llm.prompts == []  # nothing the viewer may read -> the LLM is never called


def test_hidden_document_looks_like_it_does_not_exist(
    client: TestClient, auth: Auth, db: Session
) -> None:
    secret_id = next(
        d["id"]
        for d in client.get(f"{API}/documents", headers=auth("admin")).json()["items"]
        if d["title"] == "Secret policy"
    )
    hidden = client.get(f"{API}/documents/{secret_id}", headers=auth("viewer"))
    missing = client.get(f"{API}/documents/doc_0000000000000000", headers=auth("viewer"))
    assert hidden.status_code == missing.status_code == 404
    assert hidden.json()["error"]["message"] == missing.json()["error"]["message"]


# --- SEC-05 / SEC-06 uploads ------------------------------------------------------------


def test_stored_file_name_never_comes_from_the_user(
    client: TestClient, auth: Auth, services: Services, make_pdf: Callable[..., Path]
) -> None:
    pdf = make_pdf(["Path traversal attempt document."], name="evil.pdf")
    with pdf.open("rb") as file:
        r = client.post(
            f"{API}/documents",
            headers=auth("editor"),
            files={"file": ("..\\..\\windows\\evil.pdf", file, "application/pdf")},
        )
    doc = r.json()
    assert r.status_code == 201
    assert (services.documents_dir / f"{doc['id']}.pdf").exists()
    assert sorted(p.name for p in services.documents_dir.iterdir()) == sorted(
        f"{d}.pdf" for d in services.store.documents
    )  # only generated names on disk


# --- SEC-09 errors ----------------------------------------------------------------------


def test_llm_failure_is_a_safe_503(services: Services, auth: Auth, client: TestClient) -> None:
    class Broken(FakeProvider):
        def generate(self, system_instruction: str, prompt: str):  # type: ignore[no-untyped-def]
            raise GenerationError("provider said: key AIzaSECRET is invalid") from ValueError()

    services._llm = Broken(lambda p: None)  # type: ignore[arg-type, return-value]
    r = client.post(
        f"{API}/chat/query", headers=auth("viewer"), json={"question": "collision deductible?"}
    )
    assert (r.status_code, r.json()["error"]["code"]) == (503, "llm_unavailable")
    assert "AIza" not in r.text and "provider said" not in r.text


def test_unexpected_errors_hide_details(services: Services, auth: Auth) -> None:
    services.store.search = lambda *a, **k: 1 / 0  # type: ignore[method-assign]
    client = TestClient(create_app(services), raise_server_exceptions=False)
    r = client.post(
        f"{API}/chat/query", headers=auth("viewer"), json={"question": "collision deductible?"}
    )
    assert (r.status_code, r.json()["error"]["code"]) == (500, "internal_error")
    assert "ZeroDivisionError" not in r.text and "Traceback" not in r.text


# --- SEC-10 limits ----------------------------------------------------------------------


def test_login_rate_limit(services: Services, client: TestClient) -> None:
    services.login_limiter = RateLimiter(3, 60)
    codes = [
        client.post(
            f"{API}/auth/token",
            data={"username": "viewer@example.com", "password": "guess-" + str(i)},
        ).status_code
        for i in range(4)
    ]
    assert codes == [401, 401, 401, 429]


def test_query_rate_limit(services: Services, client: TestClient, auth: Auth) -> None:
    services.query_limiter = RateLimiter(2, 60)
    headers = auth("viewer")
    codes = [
        client.post(
            f"{API}/chat/query", headers=headers, json={"question": "collision deductible?"}
        ).status_code
        for _ in range(3)
    ]
    assert codes == [200, 200, 429]


def test_large_json_body_is_rejected(client: TestClient, auth: Auth) -> None:
    r = client.post(
        f"{API}/chat/query",
        headers=auth("viewer"),
        content=b'{"question": "' + b"x" * (70 * 1024) + b'"}',
    )
    assert (r.status_code, r.json()["error"]["code"]) == (413, "payload_too_large")


def test_rate_limiter_window_slides() -> None:
    now = [0.0]
    limiter = RateLimiter(2, 60, clock=lambda: now[0])
    assert limiter.hit("k") is None and limiter.hit("k") is None
    assert limiter.hit("k") is not None  # third hit inside the window
    now[0] = 61.0
    assert limiter.hit("k") is None  # the window moved on


# --- SEC-12 database ---------------------------------------------------------------------


def test_sql_injection_text_is_just_data(client: TestClient) -> None:
    r = client.post(
        f"{API}/auth/token", data={"username": "' OR '1'='1' --", "password": "anything at all"}
    )
    assert r.status_code == 401


def test_migration_matches_the_models(tmp_path: Path) -> None:
    """The Alembic migration creates exactly the tables and columns the models describe."""
    url = f"sqlite:///{tmp_path / 'migrated.db'}"
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", url)
    config.attributes["configure_logging"] = False
    command.upgrade(config, "head")
    with create_engine(url).connect() as connection:
        differences = compare_metadata(MigrationContext.configure(connection), Base.metadata)
    assert differences == []
    command.downgrade(config, "base")
