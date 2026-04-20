"""
Views для управления отписками (публичная отписка + управление списком).
"""

from __future__ import annotations

import json
import logging

from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import render
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt

from accounts.models import User
from mailer.constants import UNSUBSCRIBE_RATE_LIMIT_PER_HOUR
from mailer.models import Unsubscribe, UnsubscribeToken
from policy.engine import enforce

logger = logging.getLogger(__name__)


@csrf_exempt
def unsubscribe(request: HttpRequest, token: str) -> HttpResponse:
    """
    Отписка по токену. @csrf_exempt оправдан: List-Unsubscribe-Post от email-клиентов
    не несёт CSRF-токен. Защита от перебора — rate limit по IP.
    """
    from accounts.security import get_client_ip

    ip = get_client_ip(request)
    throttle_key = f"mailer:unsub_ratelimit:{ip}"
    try:
        from django.core.cache import cache as _cache

        current = _cache.get(throttle_key, 0)
        if current >= UNSUBSCRIBE_RATE_LIMIT_PER_HOUR:
            return render(
                request,
                "ui/mail/unsubscribe.html",
                {"email": None, "rate_limited": True},
                status=429,
            )
        _cache.set(throttle_key, current + 1, timeout=3600)
    except Exception:
        pass  # fail-open: не блокируем при ошибке Redis

    token = (token or "").strip()
    if not token:
        return render(request, "ui/mail/unsubscribe.html", {"email": ""})

    t = UnsubscribeToken.objects.filter(token=token).first()
    email = (t.email if t else "").strip().lower()
    if email:
        reason = "unsubscribe" if request.method == "POST" else "user"
        Unsubscribe.objects.update_or_create(
            email=email,
            defaults={"source": "token", "reason": reason, "last_seen_at": timezone.now()},
        )
    return render(request, "ui/mail/unsubscribe.html", {"email": email})


@login_required
def mail_unsubscribes_list(request: HttpRequest) -> JsonResponse:
    """Список отписок (для админского модального окна в разделе "Почта")."""
    user: User = request.user
    enforce(
        user=request.user,
        resource_type="action",
        resource="ui:mail:unsubscribes:list",
        context={"path": request.path, "method": request.method},
    )
    if user.role != User.Role.ADMIN:
        return JsonResponse({"ok": False, "error": "forbidden"}, status=403)

    q = (request.GET.get("q") or "").strip()[:100]
    try:
        limit = int(request.GET.get("limit") or 200)
    except Exception:
        limit = 200
    try:
        offset = int(request.GET.get("offset") or 0)
    except Exception:
        offset = 0

    limit = max(1, min(500, limit))
    offset = max(0, offset)

    qs = Unsubscribe.objects.all()
    if q:
        qs = qs.filter(email__icontains=q)

    total = qs.count()
    rows = list(
        qs.order_by("-last_seen_at", "-created_at").values(
            "email", "source", "reason", "last_seen_at", "created_at"
        )[offset : offset + limit]
    )

    def _dt_iso(v):
        try:
            return v.isoformat() if v else None
        except Exception:
            return None

    data = [
        {
            "email": (r.get("email") or ""),
            "source": (r.get("source") or ""),
            "reason": (r.get("reason") or ""),
            "last_seen_at": _dt_iso(r.get("last_seen_at")),
            "created_at": _dt_iso(r.get("created_at")),
        }
        for r in rows
    ]
    return JsonResponse(
        {"ok": True, "total": total, "limit": limit, "offset": offset, "data": data}
    )


@login_required
def mail_unsubscribes_delete(request: HttpRequest) -> JsonResponse:
    """Удаление выбранных email из списка отписок (админ)."""
    user: User = request.user
    enforce(
        user=request.user,
        resource_type="action",
        resource="ui:mail:unsubscribes:delete",
        context={"path": request.path, "method": request.method},
    )
    if user.role != User.Role.ADMIN:
        return JsonResponse({"ok": False, "error": "forbidden"}, status=403)
    if request.method != "POST":
        return JsonResponse({"ok": False, "error": "method_not_allowed"}, status=405)

    emails: list[str] = []
    try:
        if (request.content_type or "").lower().startswith("application/json"):
            payload = json.loads((request.body or b"{}").decode("utf-8") or "{}")
            raw = payload.get("emails") or []
            if isinstance(raw, list):
                emails = [str(x) for x in raw]
        else:
            emails = request.POST.getlist("emails")
    except Exception:
        emails = []

    emails_norm = [(e or "").strip().lower() for e in emails if (e or "").strip()]
    emails_norm = list(dict.fromkeys(emails_norm))
    if not emails_norm:
        return JsonResponse({"ok": False, "error": "no_emails"}, status=400)

    deleted, _ = Unsubscribe.objects.filter(email__in=emails_norm).delete()
    return JsonResponse({"ok": True, "deleted": int(deleted or 0)})


@login_required
def mail_unsubscribes_clear(request: HttpRequest) -> JsonResponse:
    """Полная очистка списка отписок (админ)."""
    user: User = request.user
    enforce(
        user=request.user,
        resource_type="action",
        resource="ui:mail:unsubscribes:clear",
        context={"path": request.path, "method": request.method},
    )
    if user.role != User.Role.ADMIN:
        return JsonResponse({"ok": False, "error": "forbidden"}, status=403)
    if request.method != "POST":
        return JsonResponse({"ok": False, "error": "method_not_allowed"}, status=405)

    deleted, _ = Unsubscribe.objects.all().delete()
    return JsonResponse({"ok": True, "deleted": int(deleted or 0)})
