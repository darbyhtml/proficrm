from __future__ import annotations

from django.conf import settings
from cryptography.fernet import Fernet


def _fernet() -> Fernet:
    key = getattr(settings, "MAILER_FERNET_KEY", "") or ""
    if not key:
        raise RuntimeError("MAILER_FERNET_KEY is not set. Set it in .env / env variables.")
    return Fernet(key.encode("utf-8"))


def encrypt_str(value: str) -> str:
    if value is None:
        value = ""
    token = _fernet().encrypt(value.encode("utf-8"))
    return token.decode("utf-8")


def decrypt_str(token: str) -> str:
    if not token:
        return ""
    value = _fernet().decrypt(token.encode("utf-8"))
    return value.decode("utf-8")


