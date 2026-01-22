from __future__ import annotations

from rest_framework.permissions import BasePermission

from policy.engine import enforce


def _normalize_action(action: str | None) -> str:
    if not action:
        return "unknown"
    if action == "partial_update":
        return "update"
    if action == "destroy":
        return "delete"
    return action


class PolicyPermission(BasePermission):
    """
    DRF permission, использующая policy engine.

    ViewSet должен определить `policy_resource_prefix`, например:
      policy_resource_prefix = "api:tasks"
    Тогда для action=list будет ресурс "api:tasks:list".
    """

    def has_permission(self, request, view) -> bool:
        prefix = getattr(view, "policy_resource_prefix", "") or ""
        if not prefix:
            return True
        action = _normalize_action(getattr(view, "action", None))
        resource = f"{prefix}:{action}"
        # enforce() в observe_only не блокирует, но логирует
        enforce(user=request.user, resource_type="action", resource=resource, context={"path": request.path})
        return True

