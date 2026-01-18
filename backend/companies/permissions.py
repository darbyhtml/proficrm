from __future__ import annotations

from django.db.models import Q, QuerySet

from accounts.models import User
from .models import Company


def can_edit_company(user: User, company: Company) -> bool:
    """
    Единое правило редактирования компании (UI + API):
    - Админ/суперпользователь/управляющий группой компаний: всегда
    - Менеджер: только если он ответственный
    - РОП / Директор филиала: да, если компания в его филиале (branch) или у ответственного тот же филиал
    Просмотр компании разрешён всем (это правило тут НЕ проверяем).
    """
    if not user or not user.is_authenticated or not user.is_active:
        return False

    if user.is_superuser or user.role in (User.Role.ADMIN, User.Role.GROUP_MANAGER):
        return True

    if company.responsible_id and company.responsible_id == user.id:
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

    q = Q(responsible_id=user.id)
    if user.role in (User.Role.BRANCH_DIRECTOR, User.Role.SALES_HEAD) and user.branch_id:
        q = q | Q(branch_id=user.branch_id) | Q(responsible__branch_id=user.branch_id)

    return qs.filter(q).distinct()


def can_transfer_company(user: User, company: Company) -> bool:
    """
    Проверка прав на ПЕРЕДАЧУ конкретной компании.
    
    Правила:
    - Менеджер: может передавать ТОЛЬКО свои компании (company.responsible_id == user.id)
    - РОП/Директор филиала: может передавать компании менеджеров СВОЕГО филиала
      (company.responsible.branch_id == user.branch_id И company.responsible.role == MANAGER)
    - GROUP_MANAGER/ADMIN: может передавать любые компании
    """
    if not user or not user.is_authenticated or not user.is_active:
        return False
    
    # Админ/суперпользователь/управляющий группой компаний: всегда
    if user.is_superuser or user.role in (User.Role.ADMIN, User.Role.GROUP_MANAGER):
        return True
    
    # Менеджер: только свои компании
    if user.role == User.Role.MANAGER:
        return bool(company.responsible_id and company.responsible_id == user.id)
    
    # РОП/Директор филиала: компании менеджеров своего филиала
    if user.role in (User.Role.BRANCH_DIRECTOR, User.Role.SALES_HEAD) and user.branch_id:
        if not company.responsible_id:
            # Компания без ответственного - нельзя передавать
            return False
        # Загружаем ответственного, если ещё не загружен
        resp = getattr(company, "responsible", None)
        if resp is None:
            from django.shortcuts import get_object_or_404
            resp = get_object_or_404(User, id=company.responsible_id)
        # Проверяем, что ответственный в том же филиале
        if getattr(resp, "branch_id", None) == user.branch_id:
            # И что ответственный - менеджер, РОП или директор (не GROUP_MANAGER/ADMIN)
            if resp.role in (User.Role.MANAGER, User.Role.BRANCH_DIRECTOR, User.Role.SALES_HEAD):
                return True
    
    return False


def get_transfer_targets(user: User) -> QuerySet[User]:
    """
    Получить список получателей передачи компании.
    
    Исключает:
    - GROUP_MANAGER (управляющий группой компаний)
    - ADMIN (администраторы)
    
    Возвращает только:
    - MANAGER (менеджеры)
    - BRANCH_DIRECTOR (директора филиалов)
    - SALES_HEAD (РОП)
    """
    return User.objects.filter(
        is_active=True,
        role__in=[User.Role.MANAGER, User.Role.BRANCH_DIRECTOR, User.Role.SALES_HEAD]
    ).select_related("branch").order_by("branch__name", "last_name", "first_name")


def get_users_for_lists(user: User = None, exclude_admin: bool = True) -> QuerySet[User]:
    """
    Универсальная функция для получения пользователей для списков.
    
    Исключает администраторов (ADMIN) по умолчанию.
    Группирует по филиалам (сортировка по branch__name, last_name, first_name).
    
    Args:
        user: Пользователь, для которого формируется список (опционально, для фильтрации по филиалу)
        exclude_admin: Исключать ли администраторов (по умолчанию True)
    
    Returns:
        QuerySet пользователей, отсортированный по филиалу, фамилии, имени
    """
    qs = User.objects.filter(is_active=True)
    
    # Исключаем администраторов
    if exclude_admin:
        qs = qs.exclude(role=User.Role.ADMIN)
    
    # Если указан пользователь и у него есть филиал, можно ограничить по филиалу
    # Но для универсальности не ограничиваем - пусть вызывающий код сам фильтрует при необходимости
    
    return qs.select_related("branch").order_by("branch__name", "last_name", "first_name")


def can_transfer_companies(user: User, company_ids: list) -> dict:
    """
    Проверка прав на массовую передачу компаний.
    
    Возвращает:
    {
        "allowed": [company_id, ...],  # Разрешённые для передачи
        "forbidden": [  # Запрещённые с причинами
            {"id": company_id, "name": "Компания", "reason": "Причина"},
            ...
        ]
    }
    """
    if not user or not user.is_authenticated or not user.is_active:
        return {"allowed": [], "forbidden": []}
    
    # Админ/суперпользователь/управляющий группой компаний: все разрешены
    if user.is_superuser or user.role in (User.Role.ADMIN, User.Role.GROUP_MANAGER):
        companies = Company.objects.filter(id__in=company_ids).select_related("responsible", "branch")
        return {
            "allowed": list(company_ids),
            "forbidden": []
        }
    
    # Загружаем все компании одним запросом
    companies = Company.objects.filter(id__in=company_ids).select_related("responsible", "branch")
    
    allowed = []
    forbidden = []
    
    for company in companies:
        if can_transfer_company(user, company):
            allowed.append(company.id)
        else:
            # Определяем причину запрета
            reason = _get_transfer_forbidden_reason(user, company)
            forbidden.append({
                "id": str(company.id),
                "name": company.name,
                "reason": reason
            })
    
    return {
        "allowed": allowed,
        "forbidden": forbidden
    }


def _get_transfer_forbidden_reason(user: User, company: Company) -> str:
    """
    Получить причину, почему компанию нельзя передать.
    """
    if user.role == User.Role.MANAGER:
        if not company.responsible_id:
            return "У компании нет ответственного"
        if company.responsible_id != user.id:
            return "Вы не являетесь ответственным за эту компанию"
    
    if user.role in (User.Role.BRANCH_DIRECTOR, User.Role.SALES_HEAD):
        if not company.responsible_id:
            return "У компании нет ответственного"
        resp = getattr(company, "responsible", None)
        if resp is None:
            from django.shortcuts import get_object_or_404
            resp = get_object_or_404(User, id=company.responsible_id)
        if getattr(resp, "branch_id", None) != user.branch_id:
            return "Компания из другого филиала"
        if resp.role in (User.Role.GROUP_MANAGER, User.Role.ADMIN):
            return "Компания принадлежит управляющему или администратору"
    
    return "Недостаточно прав для передачи"

