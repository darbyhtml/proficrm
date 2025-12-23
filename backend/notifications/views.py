from __future__ import annotations

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect

from notifications.models import Notification
from notifications.context_processors import notifications_panel


@login_required
def mark_all_read(request: HttpRequest) -> HttpResponse:
    if request.method != "POST":
        return redirect(request.META.get("HTTP_REFERER") or "/")
    Notification.objects.filter(user=request.user, is_read=False).update(is_read=True)
    messages.success(request, "Уведомления отмечены как прочитанные.")
    return redirect(request.META.get("HTTP_REFERER") or "/")


@login_required
def mark_read(request: HttpRequest, notification_id: int) -> HttpResponse:
    if request.method != "POST":
        return redirect(request.META.get("HTTP_REFERER") or "/")
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
