from __future__ import annotations

from django.db.models import Q, QuerySet

from accounts.models import User


def company_scope_q(user: User) -> Q:
    """
    Возвращает Q-фильтр, ограничивающий выборку компаний согласно data_scope пользователя.
    По твоему решению: по умолчанию всем видно всё (GLOBAL), но админ может ограничить.
    """
    if user.is_superuser or user.role in (User.Role.ADMIN, User.Role.GROUP_MANAGER):
        return Q()

    scope = user.data_scope
    if scope == User.DataScope.GLOBAL:
        return Q()
    if scope == User.DataScope.BRANCH:
        if user.branch_id is None:
            return Q(pk__isnull=True)  # ничего
        return Q(branch_id=user.branch_id) | Q(responsible__branch_id=user.branch_id)
    if scope == User.DataScope.SELF:
        return Q(responsible_id=user.id)

    return Q()


def apply_company_scope(qs: QuerySet, user: User) -> QuerySet:
    return qs.filter(company_scope_q(user)).distinct()


