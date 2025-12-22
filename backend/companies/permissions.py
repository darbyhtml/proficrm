from __future__ import annotations

from django.db.models import Q, QuerySet

from accounts.models import User
from .models import Company


def can_edit_company(user: User, company: Company) -> bool:
    """
    Единое правило редактирования компании (UI + API):
    - Админ/суперпользователь/управляющий группой компаний: всегда
    - Создатель или ответственный: да
    - Директор филиала: да, если компания в его филиале (branch) или у ответственного тот же филиал
    Просмотр компании разрешён всем (это правило тут НЕ проверяем).
    """
    if not user or not user.is_authenticated or not user.is_active:
        return False

    if user.is_superuser or user.role in (User.Role.ADMIN, User.Role.GROUP_MANAGER):
        return True

    if company.responsible_id and company.responsible_id == user.id:
        return True

    if getattr(company, "created_by_id", None) and company.created_by_id == user.id:
        return True

    if user.role in (User.Role.BRANCH_DIRECTOR, User.Role.SALES_HEAD) and user.branch_id:
        if company.branch_id == user.branch_id:
            return True
        resp = getattr(company, "responsible", None)
        if resp and getattr(resp, "branch_id", None) == user.branch_id:
            return True

    return False


def editable_company_qs(user: User) -> QuerySet[Company]:
    """
    QuerySet компаний, которые пользователь может РЕДАКТИРОВАТЬ.
    Используется для выпадающих списков (например, выбор компании при постановке задачи).
    """
    qs = Company.objects.all().select_related("responsible", "branch")

    if user.is_superuser or user.role in (User.Role.ADMIN, User.Role.GROUP_MANAGER):
        return qs

    q = Q(responsible_id=user.id) | Q(created_by_id=user.id)
    if user.role in (User.Role.BRANCH_DIRECTOR, User.Role.SALES_HEAD) and user.branch_id:
        q = q | Q(branch_id=user.branch_id) | Q(responsible__branch_id=user.branch_id)

    return qs.filter(q).distinct()


