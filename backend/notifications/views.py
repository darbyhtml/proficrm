from __future__ import annotations

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from datetime import timedelta

from django.core.cache import cache
from django.utils.http import url_has_allowed_host_and_scheme

from notifications.models import Notification, CrmAnnouncement, CrmAnnouncementRead
from notifications.context_processors import notifications_panel
from tasksapp.models import Task
from companies.models import Company
from policy.engine import enforce


def _safe_redirect_url(request, url, fallback="/"):
    if url and url_has_allowed_host_and_scheme(
        url, allowed_hosts={request.get_host()}, require_https=request.is_secure()
    ):
        return url
    return fallback


@login_required
def mark_all_read(request: HttpRequest) -> HttpResponse:
    if request.method != "POST":
        return redirect(_safe_redirect_url(request, request.META.get("HTTP_REFERER")))
    enforce(
        user=request.user,
        resource_type="action",
        resource="ui:notifications:mark_all_read",
        context={"path": request.path, "method": request.method},
    )
    Notification.objects.filter(user=request.user, is_read=False).update(is_read=True)
    cache.delete_many([f"bell_data:{request.user.pk}", f"notif_poll:{request.user.pk}"])
    messages.success(request, "Уведомления отмечены как прочитанные.")
    return redirect(_safe_redirect_url(request, request.META.get("HTTP_REFERER")))


@login_required
def mark_read(request: HttpRequest, notification_id: int) -> HttpResponse:
    if request.method != "POST":
        return redirect(_safe_redirect_url(request, request.META.get("HTTP_REFERER")))
    enforce(
        user=request.user,
        resource_type="action",
        resource="ui:notifications:mark_read",
        context={"path": request.path, "method": request.method},
    )
    n = get_object_or_404(Notification, id=notification_id, user=request.user)
    n.is_read = True
    n.save(update_fields=["is_read"])
    cache.delete_many([f"bell_data:{request.user.pk}", f"notif_poll:{request.user.pk}"])
    return redirect(_safe_redirect_url(request, n.url or request.META.get("HTTP_REFERER")))


@login_required
def poll(request: HttpRequest) -> HttpResponse:
    """
    Live-обновление колокольчика: возвращает JSON со списком непрочитанных уведомлений и напоминаний.
    Безопасный polling (без WebSocket), ничего не ломает если JS отключен.

    Ответ кэшируется per-user. TTL синхронизирован с интервалом polling (~30s):
    перекрывает burst от нескольких вкладок и параллельных setInterval. Раньше TTL был 3s
    при интервале 30s — каждый запрос был cache MISS → 8-9 SQL на poll. При TTL 28s
    cache hit ~90%, SQL падает до ~0.8 на запрос (performance audit 2026-04-20).
    Инвалидация при `mark_as_read` (строка 46) работает корректно — пользователь видит
    обновление сразу, а не ждёт expire.
    """
    enforce(
        user=request.user,
        resource_type="action",
        resource="ui:notifications:poll",
        context={"path": request.path, "method": request.method},
    )

    cache_key = f"notif_poll:{request.user.pk}"
    cached = cache.get(cache_key)
    if cached is not None:
        resp = JsonResponse(cached)
        resp["X-Cache"] = "HIT"
        return resp

    ctx = notifications_panel(request)
    user = request.user
    notif_items = Notification.objects.filter(user=user, is_read=False).order_by("-created_at")[:10]
    notif_payload = []
    for n in notif_items:
        notif_payload.append(
            {
                "id": n.id,
                "title": n.title or "",
                "body": (n.body or ""),
                "created_at": str(n.created_at),
                "url": n.url or "",
                "payload": n.payload or {},
            }
        )
    # Объявления CRM
    from django.db.models import Q

    announcement_payload = None
    active_ann = (
        CrmAnnouncement.objects.filter(
            is_active=True,
        )
        .filter(Q(scheduled_at__isnull=True) | Q(scheduled_at__lte=timezone.now()))
        .exclude(reads__user=user)
        .order_by("-created_at")
        .first()
    )
    if active_ann:
        announcement_payload = {
            "id": active_ann.id,
            "title": active_ann.title,
            "body": active_ann.body,
            "type": active_ann.announcement_type,
        }

    payload = {
        "ok": True,
        "bell_count": int(ctx.get("bell_count") or 0),
        "notif_unread_count": int(ctx.get("notif_unread_count") or 0),
        "reminder_count": int(ctx.get("reminder_count") or 0),
        "reminder_items": ctx.get("reminder_items") or [],
        "notif_items": notif_payload,
        "announcement": announcement_payload,
    }
    cache.set(cache_key, payload, 28)  # ~90% hit rate при polling интервале 30s
    resp = JsonResponse(payload)
    resp["X-Cache"] = "MISS"
    return resp


@login_required
def all_notifications(request: HttpRequest) -> HttpResponse:
    """Страница со всеми уведомлениями (непрочитанными и прочитанными)."""
    enforce(
        user=request.user,
        resource_type="page",
        resource="ui:notifications:all",
        context={"path": request.path},
    )
    user = request.user

    # Получаем все уведомления пользователя, отсортированные по дате создания (новые сверху)
    all_notifications_list = Notification.objects.filter(user=user).order_by("-created_at")

    # Разделяем на непрочитанные и прочитанные
    unread_notifications = all_notifications_list.filter(is_read=False)
    read_notifications = all_notifications_list.filter(is_read=True)

    return render(
        request,
        "notifications/all_notifications.html",
        {
            "unread_notifications": unread_notifications,
            "read_notifications": read_notifications,
            "total_count": all_notifications_list.count(),
            "unread_count": unread_notifications.count(),
            "read_count": read_notifications.count(),
        },
    )


@login_required
def all_reminders(request: HttpRequest) -> HttpResponse:
    """Страница со всеми напоминаниями (задачи и договоры)."""
    enforce(
        user=request.user,
        resource_type="page",
        resource="ui:notifications:reminders",
        context={"path": request.path},
    )
    user = request.user
    now = timezone.now()
    today_date = timezone.localdate(now)

    # Все задачи пользователя (не выполненные и не отмененные)
    reminders = (
        Task.objects.filter(assigned_to=user)
        .exclude(status__in=[Task.Status.DONE, Task.Status.CANCELLED])
        .select_related("company")
        .order_by("due_at")
    )

    # Просроченные задачи
    overdue_tasks = reminders.filter(due_at__lt=now)

    # Задачи на сегодня
    today_tasks = reminders.filter(due_at__date=today_date)

    # Задачи на ближайшие дни (до 7 дней)
    week_tasks = reminders.filter(due_at__gt=now, due_at__lte=now + timedelta(days=7))

    # Задачи на будущее (более 7 дней)
    future_tasks = reminders.filter(due_at__gt=now + timedelta(days=7))

    # Напоминания по договорам
    contract_reminders = []
    contract_qs = (
        Company.objects.filter(responsible=user, contract_until__isnull=False)
        .select_related("contract_type")
        .only("id", "name", "contract_until", "contract_type")
        .order_by("contract_until")
    )

    # Все договоры в пределах максимального warning_days
    max_warning_days = 30
    try:
        from companies.models import ContractType
        from django.db.models import Max

        max_warning = ContractType.objects.aggregate(max_warning=Max("warning_days"))
        if max_warning["max_warning"]:
            max_warning_days = max(max_warning["max_warning"], 30)
    except Exception:
        pass

    soon_until = today_date + timedelta(days=max_warning_days)
    contracts = contract_qs.filter(contract_until__lte=soon_until)

    for c in contracts:
        days_left = (c.contract_until - today_date).days if c.contract_until else None
        if days_left is not None and c.contract_type:
            danger_days = c.contract_type.danger_days
            prefix = "Срочно: " if days_left <= danger_days else ""
        else:
            prefix = "Срочно: " if (days_left is not None and days_left < 14) else ""
        contract_reminders.append(
            {
                "title": f"{prefix}Договор до {c.contract_until.strftime('%d.%m.%Y')}",
                "subtitle": c.name,
                "url": f"/companies/{c.id}/",
                "kind": "contract",
                "days_left": days_left,
                "company_id": c.id,
            }
        )

    return render(
        request,
        "notifications/all_reminders.html",
        {
            "overdue_tasks": overdue_tasks,
            "today_tasks": today_tasks,
            "week_tasks": week_tasks,
            "future_tasks": future_tasks,
            "contract_reminders": contract_reminders,
            "total_tasks": reminders.count(),
            "total_contracts": contracts.count(),
        },
    )


@login_required
def mark_announcement_read(request: HttpRequest, announcement_id: int) -> JsonResponse:
    if request.method != "POST":
        return JsonResponse({"ok": False}, status=405)
    try:
        ann = CrmAnnouncement.objects.get(id=announcement_id)
        CrmAnnouncementRead.objects.get_or_create(user=request.user, announcement=ann)
        return JsonResponse({"ok": True})
    except CrmAnnouncement.DoesNotExist:
        return JsonResponse({"ok": False}, status=404)
