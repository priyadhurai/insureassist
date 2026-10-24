"""Login (POST /auth/token) and "who am I" (GET /auth/me)."""

import hashlib
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy import select

from insureassist.api.deps import (
    CurrentUser,
    DbDep,
    ServicesDep,
    enforce_rate_limit,
    request_id,
)
from insureassist.api.errors import ApiError
from insureassist.api.schemas import TokenResponse, UserResponse
from insureassist.auth.passwords import hash_password, needs_rehash, verify_password
from insureassist.auth.tokens import create_access_token
from insureassist.db.models import User, utcnow
from insureassist.services.access import audit

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/token", response_model=TokenResponse)
def login(
    request: Request,
    form: Annotated[OAuth2PasswordRequestForm, Depends()],
    db: DbDep,
    services: ServicesDep,
) -> TokenResponse:
    email = form.username.strip().lower()
    client = request.client.host if request.client else "unknown"
    enforce_rate_limit(services.login_limiter, f"{client}|{email}")  # slows password guessing

    user = db.scalar(select(User).where(User.email == email))
    password_ok = verify_password(user.password_hash if user else None, form.password)
    if user is None or not password_ok or not user.is_active:
        # Same message for unknown e-mail, wrong password and disabled account,
        # so the response never tells an attacker which e-mails exist.
        audit(
            db,
            "auth.login_failure",
            "failure",
            target_type="user",
            request_id=request_id(request),
            email_sha256=hashlib.sha256(email.encode()).hexdigest(),
        )
        db.commit()
        raise ApiError(401, "invalid_credentials", "Wrong e-mail or password.")

    if needs_rehash(user.password_hash):
        user.password_hash = hash_password(form.password)
    user.last_login_at = utcnow()
    audit(
        db,
        "auth.login_success",
        "success",
        actor=user,
        target_type="user",
        target_id=str(user.id),
        request_id=request_id(request),
    )
    db.commit()
    token, expires_in = create_access_token(user.id, services.tokens)
    return TokenResponse(access_token=token, expires_in=expires_in)


@router.get("/me", response_model=UserResponse)
def me(user: CurrentUser) -> UserResponse:
    return UserResponse(
        id=user.id,
        email=user.email,
        display_name=user.display_name,
        role=user.role,
        is_active=user.is_active,
    )
