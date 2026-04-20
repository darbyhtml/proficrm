"""
Company emails service — валидация и проверка уникальности email'ов компании.

До 2026-04-20 эта логика была дублирована в двух местах
``ui/views/company_detail.py``:
- ``company_main_email_update`` (строки ~1487-1525)
- ``company_email_value_update`` (строки ~1530-1573)

Выделено в **phase 2** плана рефакторинга company_detail.

API — pure functions, без HTTPResponse. View-обёртки отвечают за JSON.
"""

from __future__ import annotations

from typing import Optional

from django.core.exceptions import ValidationError
from django.core.validators import validate_email as _dj_validate_email

from companies.models import Company, CompanyEmail


def validate_email_value(raw: str, *, allow_empty: bool = False) -> tuple[str, str | None]:
    """Нормализация и валидация email-адреса.

    Args:
        raw: сырое значение из формы.
        allow_empty: разрешить пустую строку (для основного email —
            очистка допустима; для дополнительных — нет).

    Returns:
        ``(normalized_lower_email, error_or_none)``.
        При ``allow_empty=True`` и пустом вводе: ``("", None)``.
    """
    raw = (raw or "").strip()
    email = raw.lower()
    if not email:
        if allow_empty:
            return "", None
        return "", "Email не может быть пустым."
    try:
        _dj_validate_email(email)
    except ValidationError:
        return "", "Некорректный email."
    return email, None


def check_email_duplicate(
    *,
    company: Company,
    email: str,
    exclude_email_id: int | None = None,
    check_main: bool = True,
) -> str | None:
    """Проверка уникальности email среди основного и дополнительных.

    Args:
        company: компания, для которой проверяем.
        email: normalized lowercase email.
        exclude_email_id: id ``CompanyEmail``, который игнорировать
            (при обновлении — не конфликтовать с собой).
        check_main: проверять ли ``company.email`` (для основного —
            только доп., для дополнительного — и основной, и другие доп.).

    Returns:
        Строка-ошибка или ``None`` если дубля нет.
    """
    if not email:
        return None
    # Защитный lowercase — на случай если вызывающий забыл нормализовать.
    email_norm = email.strip().lower()
    if check_main and (company.email or "").strip().lower() == email_norm:
        return "Этот email уже указан как основной."
    qs = CompanyEmail.objects.filter(company=company, value__iexact=email_norm)
    if exclude_email_id is not None:
        qs = qs.exclude(id=exclude_email_id)
    if qs.exists():
        return "Такой email уже есть в дополнительных адресах."
    return None
