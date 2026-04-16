"""
Утилиты авторизации и прав доступа для модуля accounts.

Перенесено из crm/utils.py — это доменная логика авторизации,
а не конфигурация проекта.
"""
from __future__ import annotations

from accounts.models import User


def require_admin(user: User) -> bool:
    """
    Проверка, является ли пользователь администратором.

    Администратор = суперпользователь или пользователь с ролью ADMIN.
    """
    return bool(
        user
        and user.is_authenticated
        and user.is_active
        and (user.is_superuser or user.role == User.Role.ADMIN)
    )


def get_view_as_user(request) -> User | None:
    """
    Получить пользователя для режима "просмотр как" (view-as).

    Если администратор выбрал конкретного пользователя в режиме просмотра,
    возвращает этого пользователя. Иначе возвращает None.
    """
    user = getattr(request, "user", None)
    if not user or not user.is_authenticated:
        return None

    if not (user.is_superuser or user.role == User.Role.ADMIN):
        return None

    session = getattr(request, "session", {})
    view_as_enabled = session.get("view_as_enabled", False)
    if not view_as_enabled:
        return None

    view_as_user_id = session.get("view_as_user_id")
    if view_as_user_id:
        try:
            view_as_user = User.objects.filter(id=view_as_user_id, is_active=True).first()
            if view_as_user:
                return view_as_user
        except (TypeError, ValueError):
            pass

    return None


def get_effective_user(request) -> User:
    """
    Получить эффективного пользователя для проверки прав.

    Если включён режим "просмотр как" с выбранным пользователем,
    возвращает выбранного пользователя. Иначе возвращает текущего.
    """
    view_as_user = get_view_as_user(request)
    if view_as_user:
        return view_as_user
    return getattr(request, "user", None)
