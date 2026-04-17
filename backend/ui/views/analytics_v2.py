"""F7 R1 (2026-04-18): ролевые KPI-дашборды v2.

Router-view `/analytics/v2/` выбирает дашборд по роли пользователя.
MANAGER → manager.html, прочие роли → stub-заглушка до R2.

Дизайн: `knowledge-base/audits/analytics-audit-2026-04-17.md`.
"""
from __future__ import annotations

from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, HttpResponse
from django.shortcuts import render

from accounts.models import User
from ui.analytics_service import get_manager_dashboard


@login_required
def analytics_v2_home(request: HttpRequest) -> HttpResponse:
    """Роутер: по роли пользователя показывает нужный дашборд."""
    user: User = request.user
    role = getattr(user, "role", None)

    # MANAGER — свой дашборд личной продуктивности.
    if role == User.Role.MANAGER:
        ctx = {"dashboard": get_manager_dashboard(user)}
        return render(request, "ui/analytics_v2/manager.html", ctx)

    # Остальные роли — заглушка до R2.
    ctx = {"role": role}
    return render(request, "ui/analytics_v2/stub.html", ctx)
