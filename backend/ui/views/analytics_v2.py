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
from policy.decorators import policy_required
from ui.analytics_service import (
    get_branch_director_dashboard,
    get_group_manager_dashboard,
    get_manager_dashboard,
    get_sales_head_dashboard,
    get_tenderist_dashboard,
)


@login_required
@policy_required(resource_type="page", resource="ui:analytics:v2")
def analytics_v2_home(request: HttpRequest) -> HttpResponse:
    """Роутер: по роли пользователя показывает нужный дашборд.

    F7 R2: покрыты все 5 ролей.
    """
    user: User = request.user
    role = getattr(user, "role", None)

    if role == User.Role.MANAGER:
        ctx = {"dashboard": get_manager_dashboard(user)}
        return render(request, "ui/analytics_v2/manager.html", ctx)

    if role == User.Role.SALES_HEAD:
        ctx = {"dashboard": get_sales_head_dashboard(user)}
        return render(request, "ui/analytics_v2/sales_head.html", ctx)

    if role == User.Role.BRANCH_DIRECTOR:
        ctx = {"dashboard": get_branch_director_dashboard(user)}
        return render(request, "ui/analytics_v2/branch_director.html", ctx)

    if role == User.Role.GROUP_MANAGER or user.is_superuser or role == User.Role.ADMIN:
        ctx = {"dashboard": get_group_manager_dashboard(user)}
        return render(request, "ui/analytics_v2/group_manager.html", ctx)

    if role == User.Role.TENDERIST:
        ctx = {"dashboard": get_tenderist_dashboard(user)}
        return render(request, "ui/analytics_v2/tenderist.html", ctx)

    # Неизвестная роль — stub.
    ctx = {"role": role}
    return render(request, "ui/analytics_v2/stub.html", ctx)
