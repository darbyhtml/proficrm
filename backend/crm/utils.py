# Backwards-compatibility shim: перенесён в accounts/permissions.py
from accounts.permissions import get_effective_user, get_view_as_user, require_admin

__all__ = ["get_effective_user", "get_view_as_user", "require_admin"]
