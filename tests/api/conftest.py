"""Test setup for the API: the REAL app with test parts.

  database   SQLite in memory (same SQLAlchemy models as PostgreSQL)
  vectors    FAISS in a temp folder
  embedder   HashEmbedder (fake, fast)
  LLM        a spy FakeProvider that records every prompt it is sent

Users: admin, editor (owner of the docs), editor2, viewer; password for all = PASSWORD.
Documents (owner editor): "shared" -> shared with the viewer role;
                          "secret" -> not shared (owner + admins only).
"""

from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from insureassist.api.app import create_app
from insureassist.api.deps import Limits, Services
from insureassist.api.ratelimit import RateLimiter
from insureassist.auth.passwords import hash_password
from insureassist.auth.tokens import TokenConfig
from insureassist.db.models import Base, User
from insureassist.db.session import make_engine, make_session_factory
from insureassist.generation.llm import FakeProvider, LlmOutput
from insureassist.ingestion.embedder import HashEmbedder
from insureassist.ingestion.vector_store import VectorStore
from insureassist.services.documents import Storage, add_document

PASSWORD = "correct horse battery staple"
PASSWORD_HASH = hash_password(PASSWORD)  # hashed once: Argon2id is slow on purpose
TOKENS = TokenConfig("test-secret-" + "x" * 40, "insureassist-api", "insureassist-clients", 15)
SHARED_TEXT = "The collision deductible is 500 dollars per claim."
SECRET_TEXT = "Executive collision deductible is zero dollars. TOP SECRET."


def _answer(prompt: str) -> LlmOutput:
    return LlmOutput(status="answered", answer="The deductible is $500 [E1].", citations=["E1"])


@pytest.fixture
def spy_llm() -> FakeProvider:
    return FakeProvider(_answer)


@pytest.fixture
def services(tmp_path: Path, make_pdf: Callable[..., Path], spy_llm: FakeProvider) -> Services:
    engine = make_engine("sqlite://")
    Base.metadata.create_all(engine)
    embedder = HashEmbedder(dimension=256)
    svc = Services(
        session_factory=make_session_factory(engine),
        store=VectorStore(tmp_path / "index", embedder.model_name, embedder.dimension),
        embedder=embedder,
        make_llm=lambda: spy_llm,
        tokens=TOKENS,
        documents_dir=tmp_path / "documents",
        evaluation_dirs=[tmp_path / "reports"],
        limits=Limits(max_upload_bytes=2 * 1024 * 1024),
        login_limiter=RateLimiter(100, 60),
        query_limiter=RateLimiter(100, 60),
        upload_limiter=RateLimiter(100, 3600),
    )
    with svc.session_factory() as db:
        users = {
            role: User(
                email=f"{role}@example.com",
                display_name=role,
                role=role.rstrip("2"),
                password_hash=PASSWORD_HASH,
            )
            for role in ("admin", "editor", "editor2", "viewer")
        }
        db.add_all(users.values())
        db.flush()
        storage = Storage(svc.store, embedder, svc.documents_dir)
        add_document(
            db,
            storage,
            make_pdf([SHARED_TEXT], name="shared.pdf"),
            "shared.pdf",
            users["editor"],
            title="Shared policy",
            shared_with_roles=["viewer"],
        )
        add_document(
            db,
            storage,
            make_pdf([SECRET_TEXT], name="secret.pdf"),
            "secret.pdf",
            users["editor"],
            title="Secret policy",
        )
        db.commit()
    return svc


@pytest.fixture
def client(services: Services) -> TestClient:
    return TestClient(create_app(services))


@pytest.fixture
def db(services: Services) -> Session:
    return services.session_factory()


def token_for(client: TestClient, role: str) -> str:
    response = client.post(
        "/api/v1/auth/token", data={"username": f"{role}@example.com", "password": PASSWORD}
    )
    assert response.status_code == 200, response.text
    token: str = response.json()["access_token"]
    return token


@pytest.fixture
def auth(client: TestClient) -> Callable[[str], dict[str, str]]:
    """auth("viewer") -> headers with a fresh bearer token for that user."""
    return lambda role: {"Authorization": f"Bearer {token_for(client, role)}"}


def doc_id(client: TestClient, headers: dict[str, str], title: str) -> str:
    items: list[dict[str, Any]] = client.get("/api/v1/documents", headers=headers).json()["items"]
    return next(d["id"] for d in items if d["title"] == title)
