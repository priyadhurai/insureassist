"""API behaviour: each endpoint, the role matrix, documents, chat, feedback, errors."""

import uuid
from collections.abc import Callable
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from insureassist.api.deps import Services
from insureassist.db.models import AuditEvent, QueryLog

Auth = Callable[[str], dict[str, str]]
API = "/api/v1"


# --- health and auth ------------------------------------------------------------------


def test_health(client: TestClient) -> None:
    assert client.get(f"{API}/health/live").json() == {"status": "ok"}
    ready = client.get(f"{API}/health/ready")
    assert ready.status_code == 200
    assert ready.json()["checks"]["database"] == "ok"


def test_login_and_me(client: TestClient, auth: Auth) -> None:
    me = client.get(f"{API}/auth/me", headers=auth("viewer")).json()
    assert (me["email"], me["role"]) == ("viewer@example.com", "viewer")
    assert "password_hash" not in me


def test_wrong_password_and_unknown_user_look_the_same(client: TestClient, db: Session) -> None:
    wrong = client.post(
        f"{API}/auth/token", data={"username": "viewer@example.com", "password": "not the password"}
    )
    unknown = client.post(
        f"{API}/auth/token", data={"username": "nobody@example.com", "password": "not the password"}
    )
    assert wrong.status_code == unknown.status_code == 401
    assert wrong.json()["error"]["message"] == unknown.json()["error"]["message"]
    failures = db.scalars(select(AuditEvent).where(AuditEvent.action == "auth.login_failure"))
    events = list(failures)
    assert len(events) == 2
    assert "email_sha256" in events[0].details and "nobody" not in str(events[0].details)


# --- role matrix ----------------------------------------------------------------------


def test_role_matrix(client: TestClient, auth: Auth, make_pdf: Callable[..., Path]) -> None:
    pdf = make_pdf(["A brand new travel document."], name="new.pdf")

    def upload(role: str) -> int:
        with pdf.open("rb") as file:
            return client.post(
                f"{API}/documents",
                headers=auth(role),
                files={"file": ("new.pdf", file, "application/pdf")},
            ).status_code

    assert upload("viewer") == 403
    assert upload("editor") == 201
    assert client.get(f"{API}/admin/evaluations", headers=auth("viewer")).status_code == 403
    assert client.get(f"{API}/admin/evaluations", headers=auth("editor")).status_code == 403
    assert client.get(f"{API}/admin/evaluations", headers=auth("admin")).status_code == 200
    assert client.get(f"{API}/documents").status_code == 401  # no token at all


# --- documents ------------------------------------------------------------------------


def test_each_user_lists_only_documents_they_may_read(client: TestClient, auth: Auth) -> None:
    def titles(role: str) -> set[str]:
        items = client.get(f"{API}/documents", headers=auth(role)).json()["items"]
        return {d["title"] for d in items}

    assert titles("admin") == {"Shared policy", "Secret policy"}
    assert titles("editor") == {"Shared policy", "Secret policy"}  # the owner
    assert titles("viewer") == {"Shared policy"}  # shared with the viewer role
    assert titles("editor2") == set()  # another editor: nothing shared with them


def test_upload_rules(client: TestClient, auth: Auth, tmp_path: Path) -> None:
    editor = auth("editor")

    def post(name: str, data: bytes) -> tuple[int, str]:
        r = client.post(
            f"{API}/documents", headers=editor, files={"file": (name, data, "application/pdf")}
        )
        return r.status_code, r.json()["error"]["code"] if r.status_code >= 400 else ""

    assert post("notes.txt", b"hello") == (415, "unsupported_media_type")
    assert post("fake.pdf", b"just text pretending") == (415, "unsupported_media_type")
    assert post("big.pdf", b"%PDF-" + b"0" * (3 * 1024 * 1024)) == (413, "payload_too_large")
    same_bytes = (tmp_path / "shared.pdf").read_bytes()  # the "Shared policy" file again
    assert post("copy-of-shared.pdf", same_bytes) == (409, "duplicate_document")


def test_upload_response_and_sharing(
    client: TestClient, auth: Auth, make_pdf: Callable[..., Path]
) -> None:
    pdf = make_pdf(["Glass repair has no deductible."], name="glass.pdf")
    with pdf.open("rb") as file:
        r = client.post(
            f"{API}/documents",
            headers=auth("editor"),
            data={"title": "Glass guide", "shared_with_roles": ["viewer"]},
            files={"file": ("../../etc/glass.pdf", file, "application/pdf")},
        )
    body = r.json()
    assert r.status_code == 201
    assert body["filename"] == "glass.pdf"  # path parts stripped
    assert body["shared_with_roles"] == ["viewer"]
    assert body["id"].startswith("doc_")


def test_delete_rules(client: TestClient, auth: Auth, services: Services) -> None:
    shared = client.get(f"{API}/documents", headers=auth("viewer")).json()["items"][0]["id"]
    assert client.delete(f"{API}/documents/{shared}", headers=auth("viewer")).status_code == 403
    assert client.delete(f"{API}/documents/{shared}", headers=auth("editor2")).status_code == 404
    assert client.delete(f"{API}/documents/{shared}", headers=auth("editor")).status_code == 204
    assert client.get(f"{API}/documents/{shared}", headers=auth("admin")).status_code == 404
    assert not services.store.has_document(shared)  # vectors gone too
    assert not (services.documents_dir / f"{shared}.pdf").exists()  # stored file gone


# --- chat and feedback ----------------------------------------------------------------


def test_query_returns_answer_with_citation(client: TestClient, auth: Auth, db: Session) -> None:
    r = client.post(
        f"{API}/chat/query",
        headers=auth("viewer"),
        json={"question": "What is the collision deductible?"},
    )
    body = r.json()
    assert r.status_code == 200
    assert body["status"] == "answered"
    assert body["citations"][0]["document_title"] == "Shared policy"
    assert body["citations"][0]["page_number"] == 1
    assert body["disclaimer"]
    log = db.get(QueryLog, uuid.UUID(body["query_id"]))
    assert log is not None and log.question_text is None  # only a hash is stored
    assert len(log.question_sha256) == 64


def test_feedback_rules(client: TestClient, auth: Auth) -> None:
    viewer = auth("viewer")
    query_id = client.post(
        f"{API}/chat/query", headers=viewer, json={"question": "collision deductible?"}
    ).json()["query_id"]
    body = {"query_id": query_id, "rating": "up"}
    assert client.post(f"{API}/feedback", headers=viewer, json=body).status_code == 201
    assert client.post(f"{API}/feedback", headers=viewer, json=body).status_code == 409
    other = client.post(f"{API}/feedback", headers=auth("editor"), json=body)
    assert other.status_code == 404  # someone else's answer


def test_evaluations_list_reports(client: TestClient, auth: Auth, services: Services) -> None:
    folder = services.evaluation_dirs[0]
    folder.mkdir(parents=True)
    (folder / "r.json").write_text(
        '{"name": "v2-semantic", "created_at": "2026-10-19", '
        '"dataset": "insureassist-eval-v2", "config": {}, '
        '"averages": {"mrr": 0.942}}',
        encoding="utf-8",
    )
    items = client.get(f"{API}/admin/evaluations", headers=auth("admin")).json()["items"]
    assert items[0]["metrics"]["mrr"] == 0.942 and items[0]["kind"] == "retrieval"


# --- errors ---------------------------------------------------------------------------


def test_error_format_and_request_id(client: TestClient, auth: Auth) -> None:
    r = client.get(f"{API}/documents/doc_doesnotexist", headers=auth("viewer"))
    error = r.json()["error"]
    assert (r.status_code, error["code"]) == (404, "not_found")
    assert error["request_id"] == r.headers["X-Request-ID"]

    mine = client.get(f"{API}/health/live", headers={"X-Request-ID": "my-trace-123"})
    assert mine.headers["X-Request-ID"] == "my-trace-123"
    bad = client.get(f"{API}/health/live", headers={"X-Request-ID": "<script>"})
    assert bad.headers["X-Request-ID"] != "<script>"  # replaced by a generated id
    assert mine.headers["X-Content-Type-Options"] == "nosniff"


def test_validation_errors_do_not_echo_input(client: TestClient, auth: Auth) -> None:
    r = client.post(
        f"{API}/chat/query",
        headers=auth("viewer"),
        json={"question": "ok?", "secret_field": "SENSITIVE-VALUE"},
    )
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "validation_error"
    assert "SENSITIVE-VALUE" not in r.text
