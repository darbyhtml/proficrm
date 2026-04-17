"""F7 R1+R2 (2026-04-18): сервисный слой ролевых KPI-дашбордов.

Чистые функции без HTTP-зависимостей. Каждая возвращает dict с готовыми
числами для шаблона. Тестируются изолированно.

Подробный дизайн 5 дашбордов — в
`knowledge-base/audits/analytics-audit-2026-04-17.md`.

Текущий статус:
- ✅ MANAGER (get_manager_dashboard) — 6 ключевых метрик.
- ✅ SALES_HEAD (get_sales_head_dashboard) — рейтинг + overdue + онлайн.
- ✅ BRANCH_DIRECTOR (get_branch_director_dashboard) — KPI филиала + рост.
- ✅ GROUP_MANAGER (get_group_manager_dashboard) — агрегат по всем филиалам.
- ✅ TENDERIST (get_tenderist_dashboard) — read-only обзор компаний.
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


# ──────────────────────────────────────────────────────────────────
# F7 R2: дашборды руководителей (SALES_HEAD / BRANCH_DIRECTOR /
# GROUP_MANAGER) и TENDERIST (read-only).
# ──────────────────────────────────────────────────────────────────


def _managers_leaderboard(branch=None, period: Period = None, limit: int = 10) -> list[dict]:
    """Рейтинг менеджеров по числу выполненных задач за период.

    branch=None → все филиалы (для GROUP_MANAGER).
    """
    if period is None:
        period = period_this_month()
    qs = User.objects.filter(role=User.Role.MANAGER, is_active=True)
    if branch is not None:
        qs = qs.filter(branch=branch)
    rows = (
        qs.annotate(
            done_count=Count(
                "assigned_tasks",
                filter=Q(
                    assigned_tasks__status=Task.Status.DONE,
                    assigned_tasks__updated_at__gte=period.start,
                    assigned_tasks__updated_at__lt=period.end,
                ),
                distinct=True,
            )
        )
        .order_by("-done_count", "username")[:limit]
    )
    result = []
    for idx, u in enumerate(rows, start=1):
        result.append({
            "rank": idx,
            "user_id": u.id,
            "username": u.username,
            "full_name": u.get_full_name() or u.username,
            "done_count": u.done_count,
            "is_online": bool(getattr(u, "messenger_online", False)),
            "branch_id": u.branch_id,
        })
    return result


def _overdue_by_manager(branch=None, limit: int = 10) -> list[dict]:
    """Топ менеджеров с просроченными задачами (текущее состояние)."""
    now = timezone.now()
    qs = User.objects.filter(role=User.Role.MANAGER, is_active=True)
    if branch is not None:
        qs = qs.filter(branch=branch)
    rows = (
        qs.annotate(
            overdue_count=Count(
                "assigned_tasks",
                filter=Q(
                    assigned_tasks__status__in=[Task.Status.NEW, Task.Status.IN_PROGRESS],
                    assigned_tasks__due_at__lt=now,
                ),
                distinct=True,
            )
        )
        .filter(overdue_count__gt=0)
        .order_by("-overdue_count")[:limit]
    )
    return [
        {
            "user_id": u.id,
            "full_name": u.get_full_name() or u.username,
            "overdue_count": u.overdue_count,
        }
        for u in rows
    ]


def _online_count(branch=None) -> dict:
    """Сколько менеджеров сейчас онлайн / всего."""
    qs = User.objects.filter(role=User.Role.MANAGER, is_active=True)
    if branch is not None:
        qs = qs.filter(branch=branch)
    total = qs.count()
    online = qs.filter(messenger_online=True).count()
    return {"online": online, "total": total}


def _branch_companies_growth(branch=None) -> dict:
    """Рост компаний за месяц: прирост и абсолютное число."""
    now = timezone.now()
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    qs = Company.objects.all()
    if branch is not None:
        qs = qs.filter(branch=branch)
    total = qs.count()
    new_this_month = qs.filter(created_at__gte=month_start).count()
    return {"total": total, "new_this_month": new_this_month}


def _conversations_funnel(branch=None) -> dict:
    """Воронка диалогов в мессенджере: waiting / open / resolved (30 дн)."""
    try:
        from messenger.models import Conversation
    except Exception:
        return {"waiting": 0, "open": 0, "resolved_month": 0}
    now = timezone.now()
    month_ago = now - timedelta(days=30)
    qs = Conversation.objects.all()
    if branch is not None:
        qs = qs.filter(branch=branch)
    return {
        "waiting": qs.filter(status=Conversation.Status.WAITING_OFFLINE).count(),
        "open": qs.filter(status=Conversation.Status.OPEN).count(),
        "resolved_month": qs.filter(
            status=Conversation.Status.RESOLVED,
            last_activity_at__gte=month_ago,
        ).count(),
    }


def get_sales_head_dashboard(user: User) -> dict[str, Any]:
    """Дашборд РОПа (SALES_HEAD) — обзор своего подразделения."""
    branch = user.branch
    month_p = period_this_month()
    return {
        "user": user,
        "branch": branch,
        "period_label_month": month_p.label,
        "leaderboard": _managers_leaderboard(branch=branch, period=month_p),
        "overdue_by_manager": _overdue_by_manager(branch=branch),
        "online": _online_count(branch=branch),
        "conversations": _conversations_funnel(branch=branch),
        "companies_growth": _branch_companies_growth(branch=branch),
    }


def get_branch_director_dashboard(user: User) -> dict[str, Any]:
    """Дашборд директора подразделения — своё подразделение + сравнение."""
    branch = user.branch
    month_p = period_this_month()

    # Рейтинг всех подразделений по выполненным задачам за месяц.
    from accounts.models import Branch
    branches_rank = []
    for b in Branch.objects.all():
        done = Task.objects.filter(
            assigned_to__branch=b,
            status=Task.Status.DONE,
            updated_at__gte=month_p.start,
            updated_at__lt=month_p.end,
        ).count()
        branches_rank.append({
            "id": b.id,
            "code": b.code,
            "name": b.name,
            "done_count": done,
            "is_mine": b.id == (branch.id if branch else None),
        })
    branches_rank.sort(key=lambda r: (-r["done_count"], r["name"]))
    for i, row in enumerate(branches_rank, start=1):
        row["rank"] = i

    return {
        "user": user,
        "branch": branch,
        "period_label_month": month_p.label,
        "leaderboard": _managers_leaderboard(branch=branch, period=month_p, limit=20),
        "online": _online_count(branch=branch),
        "conversations": _conversations_funnel(branch=branch),
        "companies_growth": _branch_companies_growth(branch=branch),
        "branches_rank": branches_rank,
    }


def get_group_manager_dashboard(user: User) -> dict[str, Any]:
    """Дашборд управляющего группой компаний — executive-обзор всех филиалов."""
    month_p = period_this_month()

    from accounts.models import Branch
    branches = list(Branch.objects.all())
    per_branch = []
    total_done = 0
    total_online = 0
    total_managers = 0
    for b in branches:
        done = Task.objects.filter(
            assigned_to__branch=b,
            status=Task.Status.DONE,
            updated_at__gte=month_p.start,
            updated_at__lt=month_p.end,
        ).count()
        online_stats = _online_count(branch=b)
        new_companies = Company.objects.filter(
            branch=b, created_at__gte=month_p.start
        ).count()
        per_branch.append({
            "id": b.id,
            "code": b.code,
            "name": b.name,
            "done_count": done,
            "online": online_stats["online"],
            "total_managers": online_stats["total"],
            "new_companies_month": new_companies,
        })
        total_done += done
        total_online += online_stats["online"]
        total_managers += online_stats["total"]

    per_branch.sort(key=lambda r: (-r["done_count"], r["name"]))

    return {
        "user": user,
        "period_label_month": month_p.label,
        "per_branch": per_branch,
        "totals": {
            "done_month": total_done,
            "online": total_online,
            "total_managers": total_managers,
            "companies_total": Company.objects.count(),
            "companies_new_month": Company.objects.filter(
                created_at__gte=month_p.start
            ).count(),
        },
        "top_managers": _managers_leaderboard(branch=None, period=month_p, limit=10),
        "conversations": _conversations_funnel(branch=None),
    }


def get_tenderist_dashboard(user: User) -> dict[str, Any]:
    """Дашборд тендериста — read-only обзор компаний и активности."""
    today = timezone.localdate()
    last_7 = today - timedelta(days=7)
    return {
        "user": user,
        "companies_total": Company.objects.count(),
        "companies_new_week": Company.objects.filter(
            created_at__gte=last_7
        ).count(),
        "contracts_expiring_30": Company.objects.filter(
            contract_until__isnull=False,
            contract_until__gte=today,
            contract_until__lte=today + timedelta(days=30),
        ).count(),
        "contracts_with_value": Company.objects.filter(
            contract_until__isnull=False,
        ).count(),
    }
