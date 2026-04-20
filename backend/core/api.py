"""
Public API endpoints модуля core — Wave 0.3.

Сейчас один эндпоинт:

    GET /api/v1/feature-flags/

Отдаёт map ``{FLAG_NAME: bool}`` для всех известных флагов (см.
``core.feature_flags.KNOWN_FLAGS``), относительно текущего пользователя.

Фронтенд использует это чтобы условно рендерить UI (v3/b, новые меню и т.п.)
без перезагрузки страницы.
"""

from __future__ import annotations

from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from core.feature_flags import active_flags_for_user


class FeatureFlagsView(APIView):
    """``GET /api/v1/feature-flags/`` — карта активных флагов для юзера.

    Response schema:
        {
            "UI_V3B_DEFAULT": false,
            "TWO_FACTOR_MANDATORY_FOR_ADMINS": false,
            "POLICY_DECISION_LOG_DASHBOARD": false,
            "EMAIL_BOUNCE_HANDLING": false
        }

    Требует аутентификации (session или JWT) — анонимам не отдаём, чтобы
    избежать утечки информации о roadmap фич.
    """

    permission_classes = [IsAuthenticated]

    def get(self, request) -> Response:
        user = request.user if request.user.is_authenticated else None
        return Response(active_flags_for_user(user))
