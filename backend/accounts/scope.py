from __future__ import annotations

from django.db.models import Q, QuerySet

from accounts.models import User


def company_scope_q(user: User) -> Q:
    """
    Возвращает Q-фильтр для выборки компаний.

    Бизнес-правило (ADR 2026-04-15): база клиентов общая для всех 3 подразделений
    (Екатеринбург, Тюмень, Краснодар) — любой сотрудник ВИДИТ любую компанию.
    Это нужно чтобы при входящем обращении (звонок/почта/сайт) оператор мог
    найти клиента в базе, определить текущего владельца и подразделение,
    и корректно смаршрутизировать/передать заявку. Разрезание базы по
    подразделениям приводило бы к дублям карточек при кросс-региональных
    входящих.

    Разграничение доступа реализовано НЕ на уровне видимости, а на уровне
    прав РЕДАКТИРОВАНИЯ/ЗАБОРА владения: см. permissions/policy для правил,
    кто может менять владельца, редактировать карточку чужого менеджера и т.д.

    Не заменять на фильтр по user.branch без согласования с бизнесом.
    """
    if not user or not user.is_authenticated or not user.is_active:
        return Q(pk__isnull=True)
    return Q()


def apply_company_scope(qs: QuerySet, user: User) -> QuerySet:
    return qs.filter(company_scope_q(user)).distinct()
