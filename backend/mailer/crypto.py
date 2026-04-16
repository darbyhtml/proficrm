# Backwards-compatibility shim: crypto перенесён в core/crypto.py
# Этот файл сохранён для миграций, которые ссылаются на mailer.crypto
from core.crypto import _collect_keys, _fernet, encrypt_str, decrypt_str  # noqa: F401

__all__ = ["encrypt_str", "decrypt_str", "_fernet", "_collect_keys"]
