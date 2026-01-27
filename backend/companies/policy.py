"""
Domain policy/visibility для компаний и связанных сущностей.

Цель:
- единое место, где описано, какие компании/контакты/заметки пользователь "может видеть",
- переиспользование между UI (Django views) и API (DRF ViewSet).

Важно:
Сейчас Web UI по требованиям проекта показывает "всю базу" компаний всем ролям
(см. комментарий в ui/views.py: company_list). Поэтому базовая видимость — без
ограничений по филиалу/ответственному.

Если в будущем правила изменятся (например, менеджер видит только свои компании),
менять нужно будет здесь, а не в каждом viewset/view.
"""

from __future__ import annotations

from django.db.models import QuerySet

from accounts.models import User
from .models import Company, Contact, CompanyNote


def visible_companies_qs(user: User) -> QuerySet[Company]:
    """
    Базовый queryset компаний, видимых пользователю.

    По текущей логике проекта: всем аутентифицированным активным пользователям
    доступна вся база (ограничения применяются только через фильтры UI / policy gate).
    """
    qs = Company.objects.all()
    if not user or not user.is_authenticated or not user.is_active:
        return qs.none()
    return qs


def visible_contacts_qs(user: User) -> QuerySet[Contact]:
    """
    Контакты, видимые пользователю: через видимые компании.
    """
    qs = Contact.objects.all()
    if not user or not user.is_authenticated or not user.is_active:
        return qs.none()
    return qs.filter(company__in=visible_companies_qs(user))


def visible_company_notes_qs(user: User) -> QuerySet[CompanyNote]:
    """
    Заметки компаний, видимые пользователю: через видимые компании.
    """
    qs = CompanyNote.objects.all()
    if not user or not user.is_authenticated or not user.is_active:
        return qs.none()
    return qs.filter(company__in=visible_companies_qs(user))

