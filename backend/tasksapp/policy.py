"""
Domain policy/visibility для задач.

Цель:
- единое место, где описано "что пользователь может видеть/делать с задачами",
- переиспользование между UI (Django views) и API (DRF ViewSet).
"""

from __future__ import annotations

from django.db.models import Q, QuerySet

from accounts.models import User
from .models import Task


def visible_tasks_qs(user: User) -> QuerySet[Task]:
    """
    Базовый queryset задач, видимых пользователю.

    Логика синхронизирована с UI (task_list) и API (TaskViewSet.get_queryset):
    - менеджер: только свои задачи (исполнитель),
    - директор/РОП: задачи своего филиала + свои,
    - админ/управляющий: все активные задачи.
    """
    qs = Task.objects.select_related("company", "assigned_to", "created_by").order_by("-created_at")

    if not user or not user.is_authenticated or not user.is_active:
        return qs.none()

    if user.role == User.Role.MANAGER:
        qs = qs.filter(assigned_to=user)
    elif user.role in (User.Role.BRANCH_DIRECTOR, User.Role.SALES_HEAD) and user.branch_id:
        qs = qs.filter(
            Q(assigned_to__branch_id=user.branch_id)
            | Q(company__branch_id=user.branch_id)
            | Q(assigned_to=user)
        )
    # ADMIN / GROUP_MANAGER / superuser видят все (фильтрации не добавляем)

    return qs.distinct()


def can_manage_task_status(user: User, task: Task) -> bool:
    """
    Единое правило управления статусом задачи (UI + API).

    Взято из прежнего `_can_manage_task_status_api` (tasksapp.api) и вынесено
    в доменный слой.
    """
    if not user or not user.is_authenticated or not user.is_active:
        return False
    if user.is_superuser or user.role in (User.Role.ADMIN, User.Role.GROUP_MANAGER):
        return True
    # Создатель всегда может менять статус своей задачи
    if task.created_by_id and task.created_by_id == user.id:
        return True
    # Исполнитель может менять статус назначенной ему задачи
    if task.assigned_to_id and task.assigned_to_id == user.id:
        return True
    # Менеджер: только свои задачи (создатель/исполнитель уже проверены)
    if user.role == User.Role.MANAGER:
        return False
    if user.role in (User.Role.BRANCH_DIRECTOR, User.Role.SALES_HEAD) and user.branch_id:
        # По задачам работаем по "филиалу компании" в первую очередь.
        branch_id = None
        if getattr(task, "company_id", None) and getattr(task, "company", None):
            branch_id = getattr(task.company, "branch_id", None)
        if not branch_id and getattr(task, "assigned_to", None):
            branch_id = getattr(task.assigned_to, "branch_id", None)
        return bool(branch_id and branch_id == user.branch_id)
    return False

