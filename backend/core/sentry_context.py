"""
Sentry/GlitchTip context — Wave 0.4 (2026-04-20).

Middleware, который заполняет GlitchTip issue tags из request-context:

    user_id         — id пользователя (None для анонимов)
    role            — accounts.User.Role (MANAGER/TENDERIST/SALES_HEAD/...)
    branch          — accounts.Branch.code (ekb/tmn/krd)
    request_id      — короткий UUID, кросс-референс с logs
    feature_flags   — CSV активных флагов (для триажа A/B: «ошибка только когда
                      UI_V3B_DEFAULT=True?»)

При отсутствии SENTRY_DSN sentry_sdk — no-op, middleware не ломается.

Подключение:
    MIDDLEWARE = [
        ...
        "core.request_id.RequestIdMiddleware",       # сначала — генерирует request_id
        "core.sentry_context.SentryContextMiddleware",
        ...
    ]

Аналогичный Celery-handler — в core/celery_signals.py.
"""

from __future__ import annotations

import logging
from collections.abc import Callable

from django.http import HttpRequest, HttpResponse

logger = logging.getLogger(__name__)


class SentryContextMiddleware:
    """Заполняет sentry-scope тегами из текущего запроса."""

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        self._enrich_scope(request)
        return self.get_response(request)

    def _enrich_scope(self, request: HttpRequest) -> None:
        """Устанавливает tags в текущий sentry-scope."""
        try:
            import sentry_sdk
        except ImportError:
            return  # Sentry SDK не установлен — ok.

        scope = sentry_sdk.Scope.get_current_scope()

        # request_id — уже в request (RequestIdMiddleware).
        request_id = getattr(request, "request_id", None)
        if request_id:
            scope.set_tag("request_id", request_id)

        # user / role / branch
        user = getattr(request, "user", None)
        if user is not None and getattr(user, "is_authenticated", False):
            scope.set_tag("user_id", str(user.id))
            scope.set_user({"id": str(user.id), "username": user.get_username()})
            role = getattr(user, "role", None)
            if role:
                scope.set_tag("role", str(role))
            branch = getattr(user, "branch", None)
            if branch is not None:
                branch_code = getattr(branch, "code", None)
                if branch_code:
                    scope.set_tag("branch", str(branch_code))

        # feature_flags — CSV активных флагов для этого юзера.
        # Импорт локальный — избегаем циклов и тяжёлого setup-time.
        try:
            from core.feature_flags import active_flags_for_user

            active = active_flags_for_user(user if getattr(user, "is_authenticated", False) else None)
            enabled_names = sorted(name for name, on in active.items() if on)
            # Даже если ни один флаг не включён — шлём пустую строку,
            # чтобы по факту «tag присутствует всегда» можно было писать
            # alert-rules в GlitchTip.
            scope.set_tag("feature_flags", ",".join(enabled_names))
        except Exception:
            # Не даём middleware упасть из-за проблем с waffle-кешем/БД.
            logger.warning("sentry_context: failed to compute active feature flags", exc_info=True)
