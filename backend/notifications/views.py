from __future__ import annotations

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect

from notifications.models import Notification


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

from django.shortcuts import render

# Create your views here.
