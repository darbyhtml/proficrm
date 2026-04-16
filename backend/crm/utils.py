# Backwards-compatibility shim: перенесён в accounts/permissions.py
from accounts.permissions import require_admin, get_view_as_user, get_effective_user  # noqa: F401

__all__ = ["require_admin", "get_view_as_user", "get_effective_user"]
