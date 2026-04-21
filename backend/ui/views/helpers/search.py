"""Text/phone/email normalizers для search + FTS indexing.

Extracted из backend/ui/views/_base.py в W1.1 refactor.
Zero behavior change — все функции copy-paste, callers unaffected via re-export в _base.py.
"""

from __future__ import annotations

import re

from companies.normalizers import normalize_phone as _normalize_phone_canonical


def _normalize_phone_for_search(phone: str) -> str:
    """Нормализует номер телефона для поиска через единый нормализатор."""
    return _normalize_phone_canonical(phone)


def _normalize_for_search(text: str) -> str:
    """
    Нормализует текст для поиска: убирает тире, пробелы и другие разделители.
    Используется для поиска по названию, ИНН, адресу - чтобы находить совпадения
    даже если пользователь не помнит точное написание (например, с тире или без).
    """
    if not text:
        return ""
    # Убираем тире, дефисы, пробелы и другие разделители
    normalized = (
        text.replace("-", "").replace("—", "").replace("–", "").replace(" ", "").replace("_", "")
    )
    # Приводим к нижнему регистру для регистронезависимого поиска
    return normalized.lower().strip()


_SEARCH_TOKEN_RE = re.compile(r"[0-9A-Za-zА-Яа-яЁё]+", re.UNICODE)


def _tokenize_search_query(q: str) -> list[str]:
    """
    Токенизация пользовательского поиска:
    - режем по любым разделителям/пунктуации
    - приводим к lower
    - отбрасываем слишком короткие токены (1 символ), чтобы не раздувать выдачу по "г", "и" и т.п.
    """
    if not q:
        return []
    tokens = [m.group(0).lower() for m in _SEARCH_TOKEN_RE.finditer(q)]
    out: list[str] = []
    for t in tokens:
        tt = (t or "").strip()
        if not tt:
            continue
        if len(tt) == 1 and not tt.isdigit():
            continue
        out.append(tt)
    return out


def _normalize_email_for_search(email: str) -> str:
    """
    Нормализует email для поиска: убирает пробелы, приводит к нижнему регистру.
    """
    if not email:
        return ""
    return email.strip().lower()
