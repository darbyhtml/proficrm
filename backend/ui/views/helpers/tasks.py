"""Task access / edit / delete permission helpers.

Extracted из backend/ui/views/_base.py в W1.1 refactor.
Zero behavior change — copy-paste, callers still import from _base.py via re-exports.
"""

from __future__ import annotations

from accounts.models import User


def _can_manage_task_status_ui(user, task):
    if not user or not user.is_authenticated or not user.is_active:
        return False
    if user.is_superuser or user.role in (User.Role.ADMIN, User.Role.GROUP_MANAGER):
        return True
    if task.created_by_id and task.created_by_id == user.id:
        return True
    if task.assigned_to_id and task.assigned_to_id == user.id:
        return True
    if task.company_id:
        try:
            company = getattr(task, "company", None)
            if company and company.responsible_id == user.id:
                return True
        except Exception:
            pass
    if user.role in (User.Role.BRANCH_DIRECTOR, User.Role.SALES_HEAD) and user.branch_id:
        branch_id = None
        if task.company_id and getattr(task, "company", None):
            branch_id = getattr(task.company, "branch_id", None)
        if not branch_id and getattr(task, "assigned_to", None):
            branch_id = getattr(task.assigned_to, "branch_id", None)
        return bool(branch_id and branch_id == user.branch_id)
    return False


def _can_edit_task_ui(user, task):
    if task.created_by_id and task.created_by_id == user.id:
        return True
    if task.assigned_to_id and task.assigned_to_id == user.id:
        return True
    if user.role in (User.Role.ADMIN, User.Role.GROUP_MANAGER):
        return True
    if task.company_id:
        try:
            company = getattr(task, "company", None)
            if company and company.responsible_id == user.id:
                return True
        except Exception:
            pass
    if (
        user.role in (User.Role.SALES_HEAD, User.Role.BRANCH_DIRECTOR)
        and user.branch_id
        and task.company_id
    ):
        try:
            if getattr(task.company, "branch_id", None) == user.branch_id:
                return True
        except Exception:
            pass
    return False


def _can_delete_task_ui(user, task):
    if not user or not user.is_authenticated or not user.is_active:
        return False
    if user.is_superuser or user.role in (User.Role.ADMIN, User.Role.GROUP_MANAGER):
        return True
    if task.created_by_id and task.created_by_id == user.id:
        return True
    if task.assigned_to_id and task.assigned_to_id == user.id:
        return True
    if task.company_id and getattr(task, "company", None):
        try:
            if getattr(task.company, "responsible_id", None) == user.id:
                return True
        except Exception:
            pass
    if user.role in (User.Role.BRANCH_DIRECTOR, User.Role.SALES_HEAD) and user.branch_id:
        branch_id = None
        if task.company_id and getattr(task, "company", None):
            branch_id = getattr(task.company, "branch_id", None)
        if not branch_id and getattr(task, "assigned_to", None):
            branch_id = getattr(task.assigned_to, "branch_id", None)
        return bool(branch_id and branch_id == user.branch_id)
    return False
