"""
Единые "cleaners" для входных значений UI.

Задача: держать нормализацию/парсинг входов вне `ui/views.py`, чтобы:
- формы не импортировали views (нет циклов forms ↔ views)
- UI и API могли переиспользовать одинаковые правила
"""

from __future__ import annotations

from typing import Any

from core.input_cleaners import clean_int_id as _core_clean_int_id


def clean_int_id(value: Any) -> int | None:
    """
    UI-обёртка над core.input_cleaners.clean_int_id.

    Оставляем здесь с тем же контрактом, чтобы:
    - формы/вьюхи UI не трогали core напрямую (можно подменять/расширять поведение),
    - при этом единая реализация живёт в core.
    """
    return _core_clean_int_id(value)

