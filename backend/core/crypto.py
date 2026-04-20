from __future__ import annotations

import logging
from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken, MultiFernet
from django.conf import settings

logger = logging.getLogger(__name__)


def _collect_keys() -> list[str]:
    """
    Возвращает список ключей Fernet в порядке приоритета:
    первый — актуальный (используется для encrypt), остальные — для decrypt
    старых токенов. Позволяет безопасно вращать ключ:
      MAILER_FERNET_KEY=<new>
      MAILER_FERNET_KEYS_OLD=<old1>,<old2>
    """
    keys: list[str] = []
    primary = (getattr(settings, "MAILER_FERNET_KEY", "") or "").strip()
    if primary:
        keys.append(primary)
    old_raw = getattr(settings, "MAILER_FERNET_KEYS_OLD", None)
    if isinstance(old_raw, (list, tuple)):
        old_list = [str(k).strip() for k in old_raw if str(k).strip()]
    else:
        old_list = [k.strip() for k in str(old_raw or "").split(",") if k.strip()]
    for k in old_list:
        if k and k not in keys:
            keys.append(k)
    return keys


@lru_cache(maxsize=1)
def _fernet() -> MultiFernet:
    """
    Кешированный MultiFernet instance для шифрования паролей и API-ключей.
    Первый ключ в списке используется для encrypt,
    все — для decrypt (поддержка ротации ключей).
    """
    keys = _collect_keys()
    if not keys:
        raise RuntimeError("MAILER_FERNET_KEY is not set. Set it in .env / env variables.")
    fernets = [Fernet(k.encode("utf-8")) for k in keys]
    return MultiFernet(fernets)


def encrypt_str(value: str) -> str:
    """Зашифровать строку (пароль SMTP, API-ключ и т.п.)."""
    if value is None:
        value = ""
    token = _fernet().encrypt(value.encode("utf-8"))
    return token.decode("utf-8")


def decrypt_str(token: str) -> str:
    """Расшифровать строку (пароль SMTP, API-ключ и т.п.).

    При `InvalidToken` (повреждённое значение, либо зашифровано другим
    `MAILER_FERNET_KEY` — например после потери/ротации ключа) возвращаем
    пустую строку и пишем WARN. Это предотвращает полный отказ раздела
    (раньше `/mail/campaigns/` падал 500, если любой SMTP-объект в БД имел
    enc-поле от старого ключа).

    Если нужен fail-fast для диагностики — передавайте `strict=True`.
    """
    if not token:
        return ""
    try:
        value = _fernet().decrypt(token.encode("utf-8"))
        return value.decode("utf-8")
    except InvalidToken:
        logger.error(
            "decrypt_str: InvalidToken. "
            "Вероятно MAILER_FERNET_KEY был ротирован без MAILER_FERNET_KEYS_OLD, "
            "либо значение повреждено. Возвращаем пустую строку вместо падения."
        )
        return ""
