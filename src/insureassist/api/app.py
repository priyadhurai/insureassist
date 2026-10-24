"""Builds the FastAPI app.

    uv run insureassist serve            # http://127.0.0.1:8010/docs for interactive API docs

create_app(services) is used by tests with fake parts; create_app() builds the real
ones from settings at startup (database, vector DB, embedding model, LLM on first use).
"""

import re
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware

from insureassist.api.deps import Limits, Services
from insureassist.api.errors import error_response, install_error_handlers
from insureassist.api.ratelimit import RateLimiter
from insureassist.api.routes import admin, auth, chat, documents, feedback, health
from insureassist.auth.tokens import TokenConfig
from insureassist.config import settings

API_PREFIX = "/api/v1"
VALID_REQUEST_ID = re.compile(r"^[A-Za-z0-9-]{8,64}$")


def build_services() -> Services:
    """The real services, from settings (.env). Fails fast on a missing JWT secret."""
    from insureassist.db.session import make_engine, make_session_factory
    from insureassist.generation.llm import create_llm
    from insureassist.ingestion.embedder import SentenceTransformerEmbedder
    from insureassist.ingestion.vector_db import open_vector_db

    secret = settings.jwt_secret_key.get_secret_value() if settings.jwt_secret_key else ""
    tokens = TokenConfig(
        secret, settings.jwt_issuer, settings.jwt_audience, settings.jwt_access_token_ttl_minutes
    )  # raises if secret is weak
    embedder = SentenceTransformerEmbedder()
    return Services(
        session_factory=make_session_factory(make_engine(settings.database_url)),
        store=open_vector_db(embedder),
        embedder=embedder,
        make_llm=create_llm,
        tokens=tokens,
        documents_dir=settings.documents_dir,
        evaluation_dirs=[Path("eval/reports/baselines"), Path("eval/reports/experiments")],
        limits=Limits(
            max_upload_bytes=settings.max_upload_mb * 1024 * 1024,
            max_json_bytes=settings.max_json_body_kb * 1024,
            max_question_chars=settings.max_question_chars,
            top_k=settings.retrieval_top_k,
            store_query_text=settings.store_query_text,
        ),
        login_limiter=RateLimiter(settings.rate_limit_login_per_minute, 60),
        query_limiter=RateLimiter(settings.rate_limit_query_per_minute, 60),
        upload_limiter=RateLimiter(settings.rate_limit_upload_per_hour, 3600),
    )


def create_app(services: Services | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        if getattr(app.state, "services", None) is None:
            app.state.services = build_services()
        yield

    app = FastAPI(
        title="InsureAssist API",
        version="0.1.0",
        lifespan=lifespan,
        docs_url=f"{API_PREFIX}/docs",
        openapi_url=f"{API_PREFIX}/openapi.json",
    )
    app.state.services = services
    install_error_handlers(app)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[o.strip() for o in settings.cors_allowed_origins.split(",") if o.strip()],
        allow_methods=["GET", "POST", "DELETE"],
        allow_headers=["Authorization", "Content-Type", "X-Request-ID"],
    )

    @app.middleware("http")
    async def request_context(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        # Request id: accept a well-formed one from the client, else make one.
        incoming = request.headers.get("X-Request-ID", "")
        request.state.request_id = (
            incoming if VALID_REQUEST_ID.match(incoming) else str(uuid.uuid4())
        )
        # Size limit from Content-Length, before the body is read.
        limits = app.state.services.limits if app.state.services else Limits()
        is_upload = request.headers.get("content-type", "").startswith("multipart/")
        max_bytes = limits.max_upload_bytes + 1024 * 1024 if is_upload else limits.max_json_bytes
        length = request.headers.get("content-length", "")
        if length.isdigit() and int(length) > max_bytes:
            response: Response = error_response(
                request, 413, "payload_too_large", "The request body is too large."
            )
        else:
            response = await call_next(request)
        response.headers["X-Request-ID"] = request.state.request_id
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Cache-Control"] = "no-store"  # answers and tokens are not cached
        return response

    for router in (
        health.router,
        auth.router,
        documents.router,
        chat.router,
        feedback.router,
        admin.router,
    ):
        app.include_router(router, prefix=API_PREFIX)
    return app
