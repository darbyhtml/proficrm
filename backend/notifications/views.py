from __future__ import annotations

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from datetime import timedelta

from notifications.models import Notification
from notifications.context_processors import notifications_panel
from tasksapp.models import Task
from companies.models import Company
from policy.engine import enforce


@login_required
def mark_all_read(request: HttpRequest) -> HttpResponse:
    if request.method != "POST":
        return redirect(request.META.get("HTTP_REFERER") or "/")
    enforce(user=request.user, resource_type="action", resource="ui:notifications:mark_all_read", context={"path": request.path, "method": request.method})
    Notification.objects.filter(user=request.user, is_read=False).update(is_read=True)
    messages.success(request, "Уведомления отмечены как прочитанные.")
    return redirect(request.META.get("HTTP_REFERER") or "/")


@login_required
def mark_read(request: HttpRequest, notification_id: int) -> HttpResponse:
    if request.method != "POST":
        return redirect(request.META.get("HTTP_REFERER") or "/")
    enforce(user=request.user, resource_type="action", resource="ui:notifications:mark_read", context={"path": request.path, "method": request.method})
    n = get_object_or_404(Notification, id=notification_id, user=request.user)
    n.is_read = True
    n.save(update_fields=["is_read"])
    return redirect(n.url or (request.META.get("HTTP_REFERER") or "/"))


@login_required
def poll(request: HttpRequest) -> HttpResponse:
    """
    Live-обновление колокольчика: возвращает JSON со списком непрочитанных уведомлений и напоминаний.
    Безопасный polling (без WebSocket), ничего не ломает если JS отключен.
    """
    enforce(user=request.user, resource_type="action", resource="ui:notifications:poll", context={"path": request.path, "method": request.method})
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
            }
        )
    return JsonResponse(
        {
            "ok": True,
            "bell_count": int(ctx.get("bell_count") or 0),
            "notif_unread_count": int(ctx.get("notif_unread_count") or 0),
            "reminder_count": int(ctx.get("reminder_count") or 0),
            "reminder_items": ctx.get("reminder_items") or [],
            "notif_items": notif_payload,
        }
    )


@login_required
def all_notifications(request: HttpRequest) -> HttpResponse:
    """Страница со всеми уведомлениями (непрочитанными и прочитанными)."""
    enforce(user=request.user, resource_type="page", resource="ui:notifications:all", context={"path": request.path})
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
    enforce(user=request.user, resource_type="page", resource="ui:notifications:reminders", context={"path": request.path})
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
        .only("id", "name", "contract_until")
        .order_by("contract_until")
    )
    
    # Все договоры в пределах 30 дней
    soon_until = today_date + timedelta(days=30)
    contracts = contract_qs.filter(contract_until__lte=soon_until)
    
    for c in contracts:
        days_left = (c.contract_until - today_date).days if c.contract_until else None
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
