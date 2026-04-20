"""
DRF permission classes — Wave 0.3.

``FeatureFlagPermission`` — блокирует endpoint если указанный в
``view.feature_flag_required`` флаг выключен для текущего юзера.

Usage:

    from core.feature_flags import EMAIL_BOUNCE_HANDLING
    from core.permissions import FeatureFlagPermission

    class BounceWebhookView(APIView):
        permission_classes = [IsAuthenticated, FeatureFlagPermission]
        feature_flag_required = EMAIL_BOUNCE_HANDLING

        def post(self, request):
            ...

    # Если флаг EMAIL_BOUNCE_HANDLING off — клиент получит 403.

На ViewSet-уровне атрибут работает аналогично — DRF вызывает `has_permission`
на каждый action.
"""

from __future__ import annotations

from rest_framework.permissions import BasePermission

from core.feature_flags import is_enabled


class FeatureFlagPermission(BasePermission):
    """Разрешает доступ только если ``view.feature_flag_required`` включён."""

    message = "Функциональность отключена feature-флагом."

    def has_permission(self, request, view) -> bool:
        flag_name = getattr(view, "feature_flag_required", None)
        if not flag_name:
            # Атрибут не задан — считаем что флаг не требуется → разрешаем.
            # Это соответствует принципу «fail-open для отсутствующего
            # атрибута, fail-closed для выключенного флага».
            return True
        user = request.user if request.user.is_authenticated else None
        return is_enabled(str(flag_name), user=user, request=request)
