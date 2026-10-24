"""Password hashing with Argon2id (the current OWASP recommendation).

We never store a password, only its hash. Argon2id is deliberately slow and uses a lot
of memory, which makes guessing passwords from a stolen database very expensive.
"""

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

MIN_LENGTH = 12
MAX_LENGTH = 256  # protects the server from huge inputs to a slow hash

_hasher = PasswordHasher()  # library defaults = Argon2id with OWASP-level parameters
# Used when the e-mail is unknown, so a failed login takes the same time either way
# (otherwise response time would reveal which e-mail addresses have accounts).
_DUMMY_HASH = _hasher.hash("dummy password used only to equalise timing")


def validate_new_password(password: str) -> None:
    if not MIN_LENGTH <= len(password) <= MAX_LENGTH:
        raise ValueError(f"Password must be {MIN_LENGTH} to {MAX_LENGTH} characters.")


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(stored_hash: str | None, password: str) -> bool:
    if len(password) > MAX_LENGTH:
        return False
    try:
        return _hasher.verify(stored_hash or _DUMMY_HASH, password) and stored_hash is not None
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def needs_rehash(stored_hash: str) -> bool:
    """True when the hashing parameters have been strengthened since this hash was made."""
    return _hasher.check_needs_rehash(stored_hash)
