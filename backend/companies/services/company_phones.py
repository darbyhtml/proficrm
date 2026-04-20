"""
Company phones service — валидация и проверка уникальности телефонов компании.

До 2026-04-20 эта логика была скопирована дословно в **трёх** местах
`ui/views/company_detail.py`:
- ``company_main_phone_update`` (строки ~1238-1297, мягкая валидация)
- ``company_phone_value_update`` (строки ~1302-1368, строгая E.164)
- ``company_phone_create`` (строки ~1404-1486, строгая E.164)

Одинаковые 30-строчные блоки «регекс кириллицы + скопление латиницы +
null-byte + normalize + regex E.164». Любое изменение правил
(например, разрешить +7 000 для тестовых номеров) требовало правки
в трёх местах, с риском забыть одно.

**Phase 2** плана рефакторинга (см. refactoring-specialist plan
2026-04-20; phase 0 — services-пакет — коммит ``2048f4ef``; phase 1 —
timeline — коммит ``126b7930``).

API спроектирован как «pure functions», без HTTPResponse. View-обёртки
по-прежнему отвечают за JSON и status codes — это даёт возможность
протестировать валидацию изолированно и делает view-функции тоньше.
"""

from __future__ import annotations

import re
from typing import Optional

from companies.models import Company, CompanyPhone
from companies.normalizers import normalize_phone

# Пределы E.164 с небольшим запасом: минимум 10 цифр (Россия, СНГ),
# максимум 15 (рекомендация ITU E.164).
_E164_RE = re.compile(r"\+\d{10,15}")
_CYRILLIC_RE = re.compile(r"[А-Яа-яЁё]")
_LATIN_RE = re.compile(r"[A-Za-z]")
_DIGIT_RE = re.compile(r"\D")


def validate_phone_strict(raw: str) -> tuple[str | None, str | None]:
    """Строгая валидация (E.164) для доп. телефонов.

    Используется в ``company_phone_value_update`` и ``company_phone_create``.

    Правила:
    - Не пустое (пустое → ошибка).
    - Нет кириллицы и нет более 4 латинских символов (защита от «+ телавпвапефон»).
    - Нет NUL-байта (PostgreSQL отклоняет NUL → 500).
    - После ``normalize_phone`` должен матчиться ``+\\d{10,15}``.

    Returns:
        Кортеж ``(normalized, error_or_none)``:
        - При успехе: ``(normalized_phone_str, None)``.
        - При ошибке: ``(None, human_readable_error)``.
    """
    raw = (raw or "").strip()
    if not raw:
        return None, "Телефон не может быть пустым."
    if _CYRILLIC_RE.search(raw) or len(_LATIN_RE.findall(raw)) > 4:
        return None, "Телефон содержит недопустимые символы."
    if "\x00" in raw:
        return None, "Телефон содержит недопустимые символы."
    normalized = normalize_phone(raw)
    if not normalized:
        return None, "Телефон не может быть пустым."
    if not _E164_RE.fullmatch(normalized):
        return None, "Некорректный формат телефона."
    return normalized, None


def validate_phone_main(raw: str) -> tuple[str, str | None]:
    """Мягкая валидация для **основного** телефона компании.

    Используется в ``company_main_phone_update``. Отличия от strict:
    - Допускает пустой ввод (пользователь очищает основной телефон).
    - Проверяет минимум 10 цифр (а не полный E.164 — исторически данные
      в БД содержат номера вида ``7999...`` без ``+``).

    Returns:
        Кортеж ``(normalized_or_empty, error_or_none)``.
        Пустая строка в первом поле = «очистить телефон».
    """
    raw = (raw or "").strip()
    if not raw:
        # Пустое — это валидное действие (очистка).
        return "", None
    if _CYRILLIC_RE.search(raw) or len(_LATIN_RE.findall(raw)) > 4:
        return "", "Телефон содержит недопустимые символы."
    if "\x00" in raw:
        return "", "Телефон содержит недопустимые символы."
    normalized = normalize_phone(raw)
    if normalized:
        digits = _DIGIT_RE.sub("", normalized)
        if len(digits) < 10:
            return "", "Некорректный телефон: должно быть минимум 10 цифр."
    return normalized, None


def check_phone_duplicate(
    *,
    company: Company,
    normalized: str,
    exclude_phone_id: int | None = None,
) -> str | None:
    """Проверка уникальности телефона среди основного и дополнительных.

    Args:
        company: компания, для которой проверяем.
        normalized: нормализованный телефон (E.164 или числовой).
        exclude_phone_id: id ``CompanyPhone``, который нужно игнорировать
            (используется при обновлении — не конфликтовать с собой).

    Returns:
        Строка-ошибка или ``None`` если дубля нет.
    """
    if not normalized:
        return None
    # Дубль с основным телефоном компании
    if (company.phone or "").strip() == normalized:
        return "Этот телефон уже указан как основной."
    # Дубль с другим доп. телефоном
    qs = CompanyPhone.objects.filter(company=company, value=normalized)
    if exclude_phone_id is not None:
        qs = qs.exclude(id=exclude_phone_id)
    if qs.exists():
        return "Такой телефон уже есть в дополнительных номерах."
    return None


def validate_phone_comment(raw: str) -> tuple[str, str | None]:
    """Валидация комментария к номеру телефона (до 255 символов, без NUL).

    Returns:
        ``(sanitized_comment, error_or_none)``.
    """
    comment = (raw or "").strip()[:255]
    if "\x00" in comment:
        return "", "Комментарий содержит недопустимые символы."
    return comment, None
