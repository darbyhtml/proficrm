"""
Sentry/GlitchTip context — Wave 0.4 (2026-04-20, bugfix 2026-04-21).

Middleware, который заполняет GlitchTip issue tags из request-context:

    user.id, user.username  — auto через scope.set_user() (Sentry SDK built-in)
    role                    — accounts.User.Role (MANAGER/TENDERIST/SALES_HEAD/...)
    branch                  — accounts.Branch.code (ekb/tmn/krd) или "none"
    request_id              — короткий UUID, кросс-референс с logs
    feature_flags           — CSV активных флагов (для триажа A/B: «ошибка только
                              когда UI_V3B_DEFAULT=True?»), "none" или "unknown"

    environment             — через sentry_sdk.init(environment=...) из env var
                              SENTRY_ENVIRONMENT (production/staging/development).

При отсутствии SENTRY_DSN sentry_sdk — no-op, middleware не ломается.

Подключение:
    MIDDLEWARE = [
        ...
        "core.request_id.RequestIdMiddleware",       # сначала — генерирует request_id
        "core.sentry_context.SentryContextMiddleware",
        ...
    ]

Аналогичный Celery-handler — в core/celery_signals.py.

Bugfixes 2026-04-21 (после первого реального скриншота issue):
- Bug 1: `branch` тег пропадал когда `user.branch is None` или `branch.code` пуст
  (Sentry SDK фильтрует empty tags). Теперь ВСЕГДА ставим — "none" fallback.
- Bug 2: `environment: production` на staging — это настройка sentry_sdk.init().
  Чинится через SENTRY_ENVIRONMENT env var в .env (этот файл не затронут).
- Bug 3: дубль `user_id` custom + `user.id` auto из scope.user — убрали custom
  `user_id` tag. Теперь только auto из set_user() → user.id / user.username.
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

        # user + role + branch.
        # Bug 3 fix: user.id/user.username приходят автоматически из set_user(),
        # не дублируем custom user_id tag. role и branch остаются как custom
        # tags — они не стандартные Sentry user-поля.
        user = getattr(request, "user", None)
        role = "anonymous"
        branch_code = "none"  # Bug 1 fix: ВСЕГДА ставим branch, fallback "none".
        if user is not None and getattr(user, "is_authenticated", False):
            scope.set_user({"id": str(user.id), "username": user.get_username()})
            raw_role = getattr(user, "role", None)
            if raw_role:
                role = str(raw_role)
            branch_obj = getattr(user, "branch", None)
            if branch_obj is not None:
                raw_code = getattr(branch_obj, "code", None)
                if raw_code:
                    branch_code = str(raw_code)
        scope.set_tag("role", role)
        scope.set_tag("branch", branch_code)

        # feature_flags — CSV активных флагов для этого юзера.
        # Тег ставим ВСЕГДА, даже при сбоях waffle (как "unknown"), чтобы
        # можно было писать alert-rules типа "feature_flags: unknown" и
        # отлавливать саму проблему с определением флагов.
        tag_value = "unknown"
        try:
            from core.feature_flags import active_flags_for_user

            active = active_flags_for_user(user if getattr(user, "is_authenticated", False) else None)
            enabled_names = sorted(name for name, on in active.items() if on)
            # Пустая строка Sentry SDK проглатывается (tag не шлётся),
            # поэтому при отсутствии включённых флагов пишем маркер "none".
            tag_value = ",".join(enabled_names) if enabled_names else "none"
        except Exception:
            logger.warning(
                "sentry_context: failed to compute active feature flags", exc_info=True
            )
        scope.set_tag("feature_flags", tag_value)
