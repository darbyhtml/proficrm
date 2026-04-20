"""Автосвязка messenger.Contact -> companies.Company по email domain / phone.

Используется в post_save сигнале Conversation. Логика:
1. Если у контакта есть корпоративный email (домен НЕ публичный) — ищем
   компании, в которых встречается этот домен (в основных полях или в
   дочерних моделях CompanyEmail/ContactEmail).
2. Если email отсутствует либо домен публичный — пробуем phone: берём
   нормализованный E.164 (через companies.normalizers.normalize_phone) и
   ищем вхождение "хвоста" (последние 10 цифр) в Company.phone /
   CompanyPhone.value / ContactPhone.value.
3. Привязываем только если нашли РОВНО одну компанию-кандидата.

ВАЖНО: модуль лежит на верхнем уровне приложения `messenger`, а не в
`messenger.services`, потому что `messenger/services.py` — существующий
файл (widget helpers), и пакет с тем же именем затенил бы его.
"""

from __future__ import annotations

import re

# Публичные почтовые домены, которые нельзя использовать как признак
# принадлежности к компании.
PUBLIC_EMAIL_DOMAINS: frozenset[str] = frozenset(
    {
        "gmail.com",
        "yahoo.com",
        "yahoo.ru",
        "outlook.com",
        "hotmail.com",
        "live.com",
        "mail.ru",
        "internet.ru",
        "yandex.ru",
        "yandex.com",
        "ya.ru",
        "bk.ru",
        "list.ru",
        "inbox.ru",
        "icloud.com",
        "me.com",
        "rambler.ru",
        "protonmail.com",
        "proton.me",
        "gmx.com",
        "aol.com",
    }
)

_NON_DIGIT_RE = re.compile(r"\D+")


def _extract_email_domain(email: str | None) -> str:
    if not email:
        return ""
    email = str(email).strip().lower()
    if "@" not in email:
        return ""
    return email.split("@", 1)[1].strip()


def _phone_tail(phone: str | None) -> str:
    """Возвращает последние 10 цифр нормализованного номера или ''."""
    if not phone:
        return ""
    try:
        from companies.normalizers import normalize_phone

        normalized = normalize_phone(str(phone))
    except Exception:
        normalized = str(phone)
    digits = _NON_DIGIT_RE.sub("", normalized or "")
    if len(digits) < 10:
        return ""
    return digits[-10:]


def _find_candidates_by_domain(domain: str) -> set:
    from django.db.models import Q

    from companies.models import Company

    if not domain or domain in PUBLIC_EMAIL_DOMAINS:
        return set()

    suffix = f"@{domain}"
    q = (
        Q(email__iendswith=suffix)
        | Q(emails__value__iendswith=suffix)
        | Q(contacts__emails__value__iendswith=suffix)
    )
    return set(Company.objects.filter(q).values_list("id", flat=True).distinct()[:5])


def _find_candidates_by_phone(phone_tail: str) -> set:
    from django.db.models import Q

    from companies.models import Company

    if not phone_tail:
        return set()

    q = (
        Q(phone__endswith=phone_tail)
        | Q(phones__value__endswith=phone_tail)
        | Q(contacts__phones__value__endswith=phone_tail)
    )
    return set(Company.objects.filter(q).values_list("id", flat=True).distinct()[:5])


def find_company_for_contact(contact):
    """Возвращает Company, если удалось однозначно определить по email/phone."""
    from companies.models import Company

    if contact is None:
        return None

    domain = _extract_email_domain(getattr(contact, "email", ""))
    candidate_ids: set = set()

    if domain and domain not in PUBLIC_EMAIL_DOMAINS:
        candidate_ids = _find_candidates_by_domain(domain)
        if len(candidate_ids) > 1:
            # Домен неоднозначен — не фолбэкаемся на phone.
            return None

    if not candidate_ids:
        tail = _phone_tail(getattr(contact, "phone", ""))
        if tail:
            candidate_ids = _find_candidates_by_phone(tail)

    if len(candidate_ids) != 1:
        return None

    company_id = next(iter(candidate_ids))
    return Company.objects.filter(pk=company_id).first()


def autolink_conversation_company(conversation) -> bool:
    """Привязывает company к conversation, если контакт однозначно определяется."""
    if conversation is None:
        return False
    if getattr(conversation, "company_id", None):
        return False
    if not getattr(conversation, "contact_id", None):
        return False

    company = find_company_for_contact(conversation.contact)
    if company is None:
        return False

    # queryset.update — чтобы не вызывать post_save повторно.
    conversation.__class__.objects.filter(pk=conversation.pk).update(company=company)
    conversation.company = company
    return True
