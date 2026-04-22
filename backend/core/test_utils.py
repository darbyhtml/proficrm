"""Shared test utilities — cross-app helpers для destructive endpoint testing.

Created W2.1.4.2 (2026-04-22) после incident W2.1.4.1: shared staging user
qa_manager был случайно удалён during shell testing of settings_user_delete.

Prevention pattern: use make_disposable_user() / make_disposable_dict_entry()
для любого destructive endpoint test. Username/name prefix `disp_<timestamp>`
guarantees uniqueness + clear signal "safe to delete".

Usage в tests:
    from core.test_utils import make_disposable_user, make_disposable_dict_entry
    from companies.models import CompanyStatus

    def test_delete_endpoint(self):
        target = make_disposable_user(role='manager', branch=self.branch)
        # ... test delete via HTTP Client ...
        self.assertFalse(User.objects.filter(id=target.id).exists())

    def test_dict_delete(self):
        status = make_disposable_dict_entry(CompanyStatus)
        # ... test delete ...
"""

from __future__ import annotations

import time
from typing import Any

from django.contrib.auth import get_user_model


def make_disposable_user(
    role: str = "manager",
    branch: Any = None,
    prefix: str = "disp",
    is_active: bool = True,
    is_staff: bool = False,
    is_superuser: bool = False,
):
    """Create a disposable User safe to delete в tests.

    Args:
        role: User.Role value (default 'manager'). Use User.Role choices
            для compile-time safety: User.Role.MANAGER, User.Role.ADMIN, etc.
        branch: Branch instance (optional). None для global/no-branch user.
        prefix: Username prefix (default 'disp'). Keep short — full username
            includes timestamp nanoseconds для uniqueness.
        is_active: default True.
        is_staff: default False.
        is_superuser: default False (use UserFactory.create_superuser иначе).

    Returns:
        Created User instance с unusable password (consistent W2.6 policy —
        non-admin users не должны иметь password login path).
    """
    User = get_user_model()
    timestamp_ns = time.time_ns()
    user = User.objects.create_user(
        username=f"{prefix}_{timestamp_ns}",
        email=f"{prefix}_{timestamp_ns}@disposable.local",
        role=role,
        branch=branch,
        is_active=is_active,
        is_staff=is_staff,
        is_superuser=is_superuser,
    )
    user.set_unusable_password()
    user.save(update_fields=["password"])
    return user


def make_disposable_dict_entry(model_class, prefix: str = "disp", **kwargs):
    """Create a disposable dictionary model instance safe to delete.

    Applicable для: CompanyStatus, CompanySphere, ContractType, TaskType и
    подобных simple dict models где есть `name` field. Другие required
    fields passed через **kwargs.

    Args:
        model_class: Django model class с `name` field.
        prefix: Name prefix (default 'disp'). Full name includes timestamp_ns.
        **kwargs: Дополнительные fields для model instance.

    Returns:
        Created model instance.
    """
    timestamp_ns = time.time_ns()
    defaults: dict[str, Any] = {"name": f"{prefix}_{timestamp_ns}"}
    defaults.update(kwargs)
    return model_class.objects.create(**defaults)
