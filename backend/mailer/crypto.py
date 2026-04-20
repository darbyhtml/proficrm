# Backwards-compatibility shim: crypto перенесён в core/crypto.py
# Этот файл сохранён для миграций, которые ссылаются на mailer.crypto
from core.crypto import _collect_keys, _fernet, decrypt_str, encrypt_str

__all__ = ["_collect_keys", "_fernet", "decrypt_str", "encrypt_str"]
