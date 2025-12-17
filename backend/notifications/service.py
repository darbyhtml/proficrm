from __future__ import annotations

from notifications.models import Notification


def notify(*, user, title: str, body: str = "", url: str = "", kind: str = Notification.Kind.INFO) -> Notification:
    return Notification.objects.create(user=user, title=title[:200], body=body or "", url=url or "", kind=kind)


