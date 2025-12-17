from __future__ import annotations

from django.db.models import Q, QuerySet

from accounts.models import User


def company_scope_q(user: User) -> Q:
    """
    Возвращает Q-фильтр для выборки компаний.
    Текущее правило проекта: ВСЕМ пользователям видна ВСЯ база компаний (без ограничения по филиалу).
    """
    if not user or not user.is_authenticated or not user.is_active:
        return Q(pk__isnull=True)
    return Q()


def apply_company_scope(qs: QuerySet, user: User) -> QuerySet:
    return qs.filter(company_scope_q(user)).distinct()


