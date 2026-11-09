"""Password, token and session primitives. Do not log credentials."""
import hashlib
import secrets

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError

HASHER = PasswordHasher(time_cost=2, memory_cost=65536, parallelism=2)
# Avoid a trivial timing distinction for unknown account emails.
DUMMY_HASH = HASHER.hash("account-not-found-sentinel-not-a-password")


def password_hash(password: str) -> str:
    if len(password) < 12 or len(password) > 1024:
        raise ValueError("Password must be 12 to 1024 characters.")
    return HASHER.hash(password)


def password_matches(stored_hash: str, password: str) -> bool:
    try:
        return HASHER.verify(stored_hash, password)
    except (VerifyMismatchError, InvalidHashError):
        return False


def random_token() -> str:
    return secrets.token_urlsafe(40)


def token_digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()
