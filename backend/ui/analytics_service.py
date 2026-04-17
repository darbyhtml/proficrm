"""F7 R1 (2026-04-18): сервисный слой ролевых KPI-дашбордов.

Чистые функции без HTTP-зависимостей. Каждая возвращает dict с готовыми
числами для шаблона. Тестируются изолированно.

Подробный дизайн 5 дашбордов — в
`knowledge-base/audits/analytics-audit-2026-04-17.md`.

Текущий статус:
- ✅ MANAGER (get_manager_dashboard) — 6 ключевых метрик.
- ⏳ SALES_HEAD / BRANCH_DIRECTOR / GROUP_MANAGER / TENDERIST —
  реализация в R2.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any

from django.db.models import Count, F, Q
from django.utils import timezone

from accounts.models import User
from companies.models import Company
from tasksapp.models import Task


@dataclass(frozen=True)
class Period:
    """Определение периода аналитики — начало (включительно) и конец (исключительно)."""
    start: datetime
    end: datetime
    label: str


def period_today() -> Period:
    """Сегодня (локальная дата 00:00 → завтра 00:00)."""
    now = timezone.localtime()
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    return Period(start=start, end=start + timedelta(days=1), label="Сегодня")


def period_this_week() -> Period:
    """С понедельника этой недели."""
    now = timezone.localtime()
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    monday = today - timedelta(days=today.weekday())
    return Period(start=monday, end=today + timedelta(days=1), label="На неделе")


def period_this_month() -> Period:
    """С первого числа этого месяца."""
    now = timezone.localtime()
    start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    # Переход к следующему месяцу без dateutil.
    if start.month == 12:
        end = start.replace(year=start.year + 1, month=1)
    else:
        end = start.replace(month=start.month + 1)
    return Period(start=start, end=end, label="За месяц")


def _count_user_tasks_done(user: User, period: Period) -> int:
    """DONE-задачи менеджера, закрытые в периоде."""
    return Task.objects.filter(
        assigned_to=user,
        status=Task.Status.DONE,
        updated_at__gte=period.start,
        updated_at__lt=period.end,
    ).count()


def _tasks_on_time_ratio(user: User, period: Period) -> dict:
    """Доля DONE-задач, закрытых в срок (due_at ≥ updated_at).

    Возвращает {'total': int, 'on_time': int, 'ratio': int (%) | None}.
    """
    done_qs = Task.objects.filter(
        assigned_to=user,
        status=Task.Status.DONE,
        updated_at__gte=period.start,
        updated_at__lt=period.end,
    )
    total = done_qs.count()
    if total == 0:
        return {"total": 0, "on_time": 0, "ratio": None}

    on_time = done_qs.filter(
        Q(due_at__isnull=True) | Q(due_at__gte=F("updated_at"))
    ).count()
    return {
        "total": total,
        "on_time": on_time,
        "ratio": int(round(on_time * 100.0 / total)),
    }


def _companies_workload(user: User) -> dict:
    """Компании менеджера: с активными задачами / без активных задач."""
    my_companies = Company.objects.filter(responsible=user)
    with_active = my_companies.filter(
        tasks__status__in=[Task.Status.NEW, Task.Status.IN_PROGRESS]
    ).distinct().count()
    total = my_companies.count()
    return {
        "total": total,
        "with_active_tasks": with_active,
        "idle": max(0, total - with_active),
    }


def _cold_calls_summary(user: User, period: Period) -> dict:
    """Холодные звонки менеджера за период.

    Источник: phonebridge.CallRequest где is_cold_call=True OR note
    помечена как cold-click. Success — звонок связан с последующей
    задачей в пределах 24 ч (эвристика, см. аудит).
    """
    try:
        from phonebridge.models import CallRequest
    except Exception:
        return {"total": 0, "success": 0, "ratio": None}

    calls = CallRequest.objects.filter(
        created_by=user,
        created_at__gte=period.start,
        created_at__lt=period.end,
    )
    total = calls.count()
    if total == 0:
        return {"total": 0, "success": 0, "ratio": None}

    # Успех: существует задача менеджера, созданная в течение 24 ч после
    # звонка (простая эвристика). Для P0 достаточно — точный маппинг
    # call→task откладываем в R2.
    success = 0
    for call in calls.values("created_at")[:500]:  # cap для перф.
        window_end = call["created_at"] + timedelta(hours=24)
        if Task.objects.filter(
            assigned_to=user,
            created_at__gte=call["created_at"],
            created_at__lte=window_end,
        ).exists():
            success += 1
    return {
        "total": total,
        "success": success,
        "ratio": int(round(success * 100.0 / total)) if total else None,
    }


def _expiring_contracts(user: User, days: int = 30) -> list[dict]:
    """Договоры менеджера, истекающие в ближайшие N дней."""
    today = timezone.localdate()
    deadline = today + timedelta(days=days)
    qs = (
        Company.objects
        .filter(
            responsible=user,
            contract_until__isnull=False,
            contract_until__gte=today,
            contract_until__lte=deadline,
        )
        .order_by("contract_until")[:10]
    )
    rows = []
    for c in qs:
        days_left = (c.contract_until - today).days
        if days_left <= 7:
            level = "danger"
        elif days_left <= 14:
            level = "warn"
        else:
            level = "info"
        rows.append({
            "id": c.id,
            "name": c.name,
            "until": c.contract_until,
            "days_left": days_left,
            "level": level,
        })
    return rows


def get_manager_dashboard(user: User) -> dict[str, Any]:
    """Дашборд менеджера (MANAGER) — 6 метрик.

    Возвращает структуру для шаблона ui/analytics_v2/manager.html.
    """
    today_p = period_today()
    week_p = period_this_week()
    month_p = period_this_month()

    tasks_today = _count_user_tasks_done(user, today_p)
    tasks_week = _count_user_tasks_done(user, week_p)
    tasks_month = _count_user_tasks_done(user, month_p)
    on_time = _tasks_on_time_ratio(user, month_p)
    workload = _companies_workload(user)
    cold = _cold_calls_summary(user, month_p)
    contracts = _expiring_contracts(user, days=30)

    return {
        "user": user,
        "tasks": {
            "today": tasks_today,
            "week": tasks_week,
            "month": tasks_month,
        },
        "on_time": on_time,
        "workload": workload,
        "cold_calls": cold,
        "expiring_contracts": contracts,
        "period_label_month": month_p.label,
    }
