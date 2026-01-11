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
    return bool(user.is_authenticated and user.is_active and (user.is_superuser or user.role == User.Role.ADMIN))
