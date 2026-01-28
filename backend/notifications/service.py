from __future__ import annotations

from datetime import timedelta

from django.utils import timezone

from notifications.models import Notification


def notify(
    *,
    user,
    title: str,
    body: str = "",
    url: str = "",
    kind: str = Notification.Kind.INFO,
    dedupe_seconds: int = 0,
    payload: dict | None = None,
) -> Notification:
    """
    Создать уведомление.

    Если задан dedupe_seconds > 0, то для этого пользователя и (kind, title, url) в окне времени
    не будет создаваться дубль непрочитанного уведомления — вернём уже существующее.
    """
    t = (title or "")[:200]
    u = (url or "")[:300]
    b = body or ""

    if dedupe_seconds and dedupe_seconds > 0:
        since = timezone.now() - timedelta(seconds=int(dedupe_seconds))
        existing = (
            Notification.objects.filter(
                user=user,
                kind=kind,
                title=t,
                url=u,
                is_read=False,
                created_at__gte=since,
            )
            .order_by("-created_at")
            .first()
        )
        if existing:
            if payload is not None:
                existing.payload = payload
                existing.save(update_fields=["payload"])
            return existing

    return Notification.objects.create(user=user, title=t, body=b, url=u, kind=kind, payload=payload or {})


