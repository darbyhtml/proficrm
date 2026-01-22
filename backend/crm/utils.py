"""
Общие утилиты для проекта CRM.
"""
from __future__ import annotations

from accounts.models import User


def require_admin(user: User) -> bool:
    """
    Проверка, является ли пользователь администратором.
    
    Администратор = суперпользователь или пользователь с ролью ADMIN.
    
    Args:
        user: Пользователь для проверки
        
    Returns:
        True если пользователь администратор, False иначе
    """
    # Важно для безопасности: доступ к UI-админке /settings/ всегда только
    # у реального администратора (role=ADMIN) или суперпользователя.
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
    
    Args:
        request: HttpRequest объект
        
    Returns:
        User объект выбранного пользователя или None
    """
    user = getattr(request, "user", None)
    if not user or not user.is_authenticated:
        return None
    
    # Проверяем, является ли текущий пользователь администратором
    if not (user.is_superuser or user.role == User.Role.ADMIN):
        return None
    
    # Проверяем, включён ли режим просмотра
    session = getattr(request, "session", {})
    view_as_enabled = session.get("view_as_enabled", False)
    if not view_as_enabled:
        return None
    
    # Проверяем, выбран ли конкретный пользователь
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
    возвращает выбранного пользователя. Иначе возвращает текущего пользователя.
    
    Args:
        request: HttpRequest объект
        
    Returns:
        User объект (выбранный в view-as или текущий)
    """
    view_as_user = get_view_as_user(request)
    if view_as_user:
        return view_as_user
    return getattr(request, "user", None)
