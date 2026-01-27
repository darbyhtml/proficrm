"""
Универсальные чистильщики (cleaners) для входных значений.

Не зависят от UI и могут использоваться:
- в API/сервисах,
- в формах,
- в доменной логике.
"""

from __future__ import annotations

import ast
import json
from typing import Any
from uuid import UUID


def clean_int_id(value: Any) -> int | None:
    """
    Достаёт положительный int ID из "грязных" значений.

    Поддерживает входы вида:
    - 1 / "1"
    - ["1"] / [" 1 "]
    - "['1']" / '["1"]'
    - '{"id": 1}' / "1" (как JSON scalar)
    """
    if value is None:
        return None

    # list/tuple → первый элемент
    if isinstance(value, (list, tuple)):
        if not value:
            return None
        value = value[0]

    s = str(value).strip()
    if not s:
        return None

    # 1) JSON (может быть "1", ["1"], {"id": 1})
    try:
        parsed = json.loads(s)
        if isinstance(parsed, list) and parsed:
            s = str(parsed[0]).strip()
        elif isinstance(parsed, dict):
            cand = parsed.get("id")
            s = str(cand).strip() if cand is not None else ""
        else:
            s = str(parsed).strip()
    except (json.JSONDecodeError, ValueError, TypeError):
        pass

    if not s:
        return None

    # 2) Python literal list: "['1']"
    if s.startswith("[") and s.endswith("]"):
        try:
            parsed = ast.literal_eval(s)
            if isinstance(parsed, list) and parsed:
                s = str(parsed[0]).strip().strip("'\"")
        except (ValueError, SyntaxError, TypeError):
            pass

    s = s.strip().strip("'\"")
    if not s:
        return None

    # 3) int
    try:
        i = int(s)
    except (ValueError, TypeError):
        return None

    return i if i > 0 else None


def clean_uuid(value: Any) -> UUID | None:
    """
    Безопасно достаёт UUID из строки/любого значения.
    """
    if value is None:
        return None
    s = str(value).strip().strip("'\"")
    if not s:
        return None
    try:
        return UUID(s)
    except (ValueError, TypeError):
        return None

