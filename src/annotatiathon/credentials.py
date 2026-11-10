"""Encrypt organiser-owned Supervisely API credentials at rest.

NIGHTJAR_ENCRYPTION_KEY must be a stable Fernet key supplied by the
deployment secret manager or local untracked .env. No Supervisely token
needs to be configured in environment variables.
"""
from cryptography.fernet import Fernet, InvalidToken

from .config import get_settings


def cipher() -> Fernet:
    secret = get_settings().nightjar_encryption_key
    if secret is None or not secret.get_secret_value().strip():
        raise ValueError("NIGHTJAR_ENCRYPTION_KEY is required to store an integration token.")
    try:
        return Fernet(secret.get_secret_value().strip().encode())
    except (ValueError, TypeError) as exc:
        raise ValueError("NIGHTJAR_ENCRYPTION_KEY must be a valid Fernet key.") from exc


def encrypt_token(value: str) -> str:
    return cipher().encrypt(value.encode()).decode()


def decrypt_token(value: str) -> str:
    try:
        return cipher().decrypt(value.encode()).decode()
    except (InvalidToken, ValueError, TypeError) as exc:
        raise ValueError("Unable to decrypt the saved Supervisely credential.") from exc
