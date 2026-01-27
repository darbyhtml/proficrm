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


def can_view_company(user: User, company: Company) -> bool:
    """
    Проверяет, может ли пользователь просматривать конкретную компанию.

    Базируется на visible_companies_qs, чтобы UI и API использовали одинаковые правила.
    Сейчас visible_companies_qs возвращает всю базу для всех ролей, но
    в будущем сюда можно добавить ограничения по филиалу/роли.
    """
    if not user or not user.is_authenticated or not user.is_active:
        return False
    return visible_companies_qs(user).filter(pk=company.pk).exists()


def can_view_company_id(user: User, company_id: int) -> bool:
    """
    То же, что can_view_company, но без предварительного get().
    Удобно вызывать из policy-движка по company_id из контекста.
    """
    if not user or not user.is_authenticated or not user.is_active:
        return False
    return visible_companies_qs(user).filter(pk=company_id).exists()


def visible_company_notes_qs(user: User) -> QuerySet[CompanyNote]:
    """
    Заметки компаний, видимые пользователю: через видимые компании.
    """
    qs = CompanyNote.objects.all()
    if not user or not user.is_authenticated or not user.is_active:
        return qs.none()
    return qs.filter(company__in=visible_companies_qs(user))

