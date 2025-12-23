from __future__ import annotations

from accounts.models import User


def ui_globals(request):
    """
    Глобальные флаги для шаблонов UI, чтобы не дублировать проверки ролей по всему проекту.
    """
    user = getattr(request, "user", None)
    is_auth = bool(user and user.is_authenticated and user.is_active)
    role = getattr(user, "role", "") if is_auth else ""

    is_admin = bool(is_auth and (getattr(user, "is_superuser", False) or role == User.Role.ADMIN))
    is_group_manager = bool(is_auth and (getattr(user, "is_superuser", False) or role in (User.Role.ADMIN, User.Role.GROUP_MANAGER)))
    is_branch_lead = bool(is_auth and role in (User.Role.SALES_HEAD, User.Role.BRANCH_DIRECTOR))
    can_view_activity = bool(is_auth and (is_admin or is_group_manager or is_branch_lead))

    return {
        "is_admin": is_admin,
        "is_group_manager": is_group_manager,
        "is_branch_lead": is_branch_lead,
        "can_view_activity": can_view_activity,
    }


