from __future__ import annotations

from functools import lru_cache

from django.conf import settings
from cryptography.fernet import Fernet


@lru_cache(maxsize=1)
def _fernet() -> Fernet:
    """
    Кешированный Fernet instance для шифрования SMTP-паролей.
    Ключ берётся из MAILER_FERNET_KEY и не меняется на лету, поэтому кеширование безопасно.
    """
    key = getattr(settings, "MAILER_FERNET_KEY", "") or ""
    if not key:
        raise RuntimeError("MAILER_FERNET_KEY is not set. Set it in .env / env variables.")
    return Fernet(key.encode("utf-8"))


def encrypt_str(value: str) -> str:
    """Зашифровать строку (пароль SMTP)."""
    if value is None:
        value = ""
    token = _fernet().encrypt(value.encode("utf-8"))
    return token.decode("utf-8")


def decrypt_str(token: str) -> str:
    """Расшифровать строку (пароль SMTP)."""
    if not token:
        return ""
    value = _fernet().decrypt(token.encode("utf-8"))
    return value.decode("utf-8")


