"""Shared objects (Services) and request dependencies: database session, current user,
required role, rate limits.

`Services` bundles everything the endpoints need. The real app builds it from
settings; tests build it with SQLite, a fake embedder and a fake LLM. Endpoints never
reach for globals, so they behave the same in both.
"""

from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Annotated

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session, sessionmaker

from insureassist.api.errors import ApiError
from insureassist.api.ratelimit import RateLimiter
from insureassist.auth.tokens import TokenConfig, TokenError, read_access_token
from insureassist.db.models import User
from insureassist.generation.llm import LLMProvider
from insureassist.ingestion.embedder import Embedder
from insureassist.ingestion.vector_db import VectorDB
from insureassist.services.documents import Storage


@dataclass
class Limits:
    max_upload_bytes: int = 20 * 1024 * 1024
    max_json_bytes: int = 64 * 1024
    max_question_chars: int = 2000
    top_k: int = 5
    store_query_text: bool = False


@dataclass
class Services:
    session_factory: sessionmaker[Session]
    store: VectorDB
    embedder: Embedder
    make_llm: Callable[[], LLMProvider]  # created on first use (a local model is heavy)
    tokens: TokenConfig
    documents_dir: Path
    evaluation_dirs: list[Path]
    limits: Limits = field(default_factory=Limits)
    login_limiter: RateLimiter = field(default_factory=lambda: RateLimiter(5, 60))
    query_limiter: RateLimiter = field(default_factory=lambda: RateLimiter(20, 60))
    upload_limiter: RateLimiter = field(default_factory=lambda: RateLimiter(10, 3600))
    _llm: LLMProvider | None = None

    @property
    def storage(self) -> Storage:
        return Storage(self.store, self.embedder, self.documents_dir)

    def llm(self) -> LLMProvider:
        if self._llm is None:
            self._llm = self.make_llm()
        return self._llm


def get_services(request: Request) -> Services:
    services: Services = request.app.state.services
    return services


ServicesDep = Annotated[Services, Depends(get_services)]


def get_db(services: ServicesDep) -> Iterator[Session]:
    with services.session_factory() as session:
        yield session


DbDep = Annotated[Session, Depends(get_db)]
_bearer = HTTPBearer(auto_error=False)


def get_current_user(
    db: DbDep,
    services: ServicesDep,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> User:
    if credentials is None:
        raise ApiError(401, "authentication_required", "Log in first.", headers=_challenge())
    try:
        user_id = read_access_token(credentials.credentials, services.tokens)
    except TokenError:
        raise ApiError(
            401, "token_invalid", "Your session is invalid or has expired.", headers=_challenge()
        ) from None
    # Role and active status come from the database on EVERY request, not from the token.
    user = db.get(User, user_id)
    if user is None or not user.is_active:
        raise ApiError(
            401, "token_invalid", "Your session is invalid or has expired.", headers=_challenge()
        )
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


def require_role(*roles: str) -> Callable[[User], User]:
    def check(user: CurrentUser) -> User:
        if user.role not in roles:
            raise ApiError(403, "forbidden", "Your role does not allow this action.")
        return user

    return check


def enforce_rate_limit(limiter: RateLimiter, key: str) -> None:
    wait = limiter.hit(key)
    if wait is not None:
        raise ApiError(
            429,
            "rate_limited",
            "Too many requests. Try again shortly.",
            headers={"Retry-After": str(max(1, int(wait)))},
        )


def request_id(request: Request) -> str | None:
    value: str | None = getattr(request.state, "request_id", None)
    return value


def _challenge() -> dict[str, str]:
    return {"WWW-Authenticate": "Bearer"}
