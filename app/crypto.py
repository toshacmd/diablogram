"""Encryption helpers for secrets at rest (session strings, proxy passwords)."""
from cryptography.fernet import Fernet

from app.config import get_settings


def _fernet() -> Fernet:
    key = get_settings().session_encryption_key
    if not key:
        raise RuntimeError(
            "SESSION_ENCRYPTION_KEY is not set. Generate one with: "
            "python -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\""
        )
    return Fernet(key.encode())


def encrypt(value: str) -> str:
    return _fernet().encrypt(value.encode()).decode()


def decrypt(token: str) -> str:
    return _fernet().decrypt(token.encode()).decode()
