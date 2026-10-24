"""Login tokens (JWT). A token is a signed note saying "this is user X until time T".

Rules that matter for security:
  * Only HS256 is accepted. The algorithm is never taken from the token itself, so
    "alg: none" and algorithm-swap tricks are rejected.
  * Every token must carry exp, iat, nbf, iss, aud, sub and jti, and they are checked.
  * Short lifetime (15 minutes). The user's role is NOT in the token: it is read from
    the database on every request, so a role change or deactivation applies at once.
"""

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import jwt

ALGORITHM = "HS256"
REQUIRED_CLAIMS = ["exp", "iat", "nbf", "iss", "aud", "sub", "jti"]
MIN_SECRET_LENGTH = 32


class TokenError(Exception):
    """The token is missing, tampered with, expired, or not meant for us."""


@dataclass(frozen=True)
class TokenConfig:
    secret: str
    issuer: str
    audience: str
    ttl_minutes: int = 15

    def __post_init__(self) -> None:
        if len(self.secret) < MIN_SECRET_LENGTH or self.secret.startswith("replace-with"):
            raise ValueError(
                f"JWT_SECRET_KEY must be a random value of at least {MIN_SECRET_LENGTH} "
                "characters (not the .env.example placeholder)."
            )


def create_access_token(
    user_id: uuid.UUID, config: TokenConfig, now: datetime | None = None
) -> tuple[str, int]:
    """Return (token, seconds until it expires)."""
    issued = now or datetime.now(UTC)
    lifetime = timedelta(minutes=config.ttl_minutes)
    claims = {
        "sub": str(user_id),
        "iss": config.issuer,
        "aud": config.audience,
        "iat": issued,
        "nbf": issued,
        "exp": issued + lifetime,
        "jti": uuid.uuid4().hex,  # unique token id (useful for audits and future revocation)
    }
    return jwt.encode(claims, config.secret, algorithm=ALGORITHM), int(lifetime.total_seconds())


def read_access_token(token: str, config: TokenConfig) -> uuid.UUID:
    """Return the user id inside a valid token, or raise TokenError."""
    try:
        claims = jwt.decode(
            token,
            config.secret,
            algorithms=[ALGORITHM],  # allow-list: never trust the token's own "alg"
            audience=config.audience,
            issuer=config.issuer,
            options={"require": REQUIRED_CLAIMS},
            leeway=30,  # tolerate small clock differences
        )
        return uuid.UUID(claims["sub"])
    except (jwt.PyJWTError, ValueError) as error:
        raise TokenError("invalid token") from error
