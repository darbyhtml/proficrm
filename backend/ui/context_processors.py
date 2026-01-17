from __future__ import annotations

from accounts.models import User, Branch


def ui_globals(request):
    """
    Глобальные флаги для шаблонов UI, чтобы не дублировать проверки ролей по всему проекту.
    Здесь же добавляем поддержку режима "просмотр как роль/филиал" для администратора.
    """
    user = getattr(request, "user", None)
    is_auth = bool(user and user.is_authenticated and user.is_active)
    role = getattr(user, "role", "") if is_auth else ""

    is_admin = bool(is_auth and (getattr(user, "is_superuser", False) or role == User.Role.ADMIN))
    is_group_manager = bool(
        is_auth and (getattr(user, "is_superuser", False) or role in (User.Role.ADMIN, User.Role.GROUP_MANAGER))
    )
    is_branch_lead = bool(is_auth and role in (User.Role.SALES_HEAD, User.Role.BRANCH_DIRECTOR))
    can_view_activity = bool(is_auth and (is_admin or is_group_manager or is_branch_lead))
    can_view_cold_call_reports = bool(
        is_auth and (is_admin or is_group_manager or is_branch_lead or role == User.Role.MANAGER)
    )

    # Режим "просмотр как" (только для администратора):
    # не меняет реальные права пользователя, влияет только на отображение/фильтры там,
    # где этот режим явно учитывается.
    view_as_role = role
    view_as_branch = getattr(user, "branch", None) if is_auth else None
    view_as_branches = []

    role_map = {value: label for value, label in User.Role.choices}

    if is_auth and is_admin:
        session = getattr(request, "session", {})
        # Проверяем, включён ли режим просмотра администратора
        view_as_enabled = session.get("view_as_enabled", False)

        # Режим просмотра работает только если он включён
        if view_as_enabled:
            as_role = session.get("view_as_role")
            valid_roles = {choice[0] for choice in User.Role.choices}
            if as_role in valid_roles:
                view_as_role = as_role

            as_branch_id = session.get("view_as_branch_id")
            if as_branch_id:
                try:
                    bid = int(as_branch_id)
                    view_as_branch = Branch.objects.filter(id=bid).first() or view_as_branch
                except (TypeError, ValueError):
                    pass

            # Список филиалов для выпадающего списка админа
            view_as_branches = list(Branch.objects.all().order_by("name"))
        else:
            # Если режим отключён, сбрасываем настройки просмотра
            view_as_branches = []

    # Подписи для баннера "просмотр как"
    view_as_role_label = ""
    if view_as_role and view_as_role in role_map:
        view_as_role_label = role_map[view_as_role]

    # Визуальные права (для отображения блоков в меню/доске)
    # Основаны на view_as_role, но для не-админов совпадают с реальной ролью.
    # Для визуальных прав не учитываем суперюзера: если админ выбрал роль "Менеджер",
    # хотим видеть интерфейс именно менеджера, а не всегда администратора.
    view_is_admin = bool(is_auth and view_as_role == User.Role.ADMIN)
    view_is_group_manager = bool(
        is_auth and view_as_role in (User.Role.ADMIN, User.Role.GROUP_MANAGER)
    )
    view_is_branch_lead = bool(
        is_auth and view_as_role in (User.Role.SALES_HEAD, User.Role.BRANCH_DIRECTOR)
    )
    view_can_view_activity = bool(view_is_admin or view_is_group_manager or view_is_branch_lead)
    view_can_view_cold_call_reports = bool(
        view_can_view_activity or (is_auth and view_as_role == User.Role.MANAGER)
    )

    # Проверяем, включён ли режим просмотра администратора (для всех, не только админов)
    view_as_enabled = False
    if is_auth and is_admin:
        session = getattr(request, "session", {})
        view_as_enabled = session.get("view_as_enabled", False)

    return {
        # Реальные права пользователя (для бэкенда/безопасности)
        "is_admin": is_admin,
        "is_group_manager": is_group_manager,
        "is_branch_lead": is_branch_lead,
        "can_view_activity": can_view_activity,
        "can_view_cold_call_reports": can_view_cold_call_reports,
        # Визуальные права с учётом режима "просмотр как"
        "view_is_admin": view_is_admin,
        "view_is_group_manager": view_is_group_manager,
        "view_is_branch_lead": view_is_branch_lead,
        "view_can_view_activity": view_can_view_activity,
        "view_can_view_cold_call_reports": view_can_view_cold_call_reports,
        # Режим "просмотр как"
        "view_as_enabled": view_as_enabled,
        "view_as_role": view_as_role,
        "view_as_branch": view_as_branch,
        "view_as_branches": view_as_branches,
        "view_as_roles": User.Role.choices,
        "view_as_role_label": view_as_role_label,
    }


