from __future__ import annotations

import secrets
from dataclasses import dataclass
from typing import Optional

from django.conf import settings
from django.core.cache import cache
from django.http import Http404
from rest_framework import exceptions


def ensure_messenger_enabled_api():
    """
    Единая проверка feature-флага для DRF/Widget API.

    Если messenger отключён, выбрасывает DRF-исключение 404/disabled,
    не нарушая стабильность маршрутов.
    """
    if not getattr(settings, "MESSENGER_ENABLED", False):
        raise exceptions.NotFound(detail="Messenger disabled")


def ensure_messenger_enabled_view():
    """
    Единая проверка feature-флага для Django views (UI).
    """
    if not getattr(settings, "MESSENGER_ENABLED", False):
        raise Http404("Messenger disabled")


def is_messenger_enabled() -> bool:
    return bool(getattr(settings, "MESSENGER_ENABLED", False))


# ---------------------------------------------------------------------------
# Widget session tokens (для защиты публичного widget API)
# ---------------------------------------------------------------------------

WIDGET_SESSION_TTL_SECONDS = 60 * 60 * 24  # 24 часа по умолчанию


@dataclass
class WidgetSession:
    token: str
    inbox_id: int
    conversation_id: int
    contact_id: str


def _widget_session_cache_key(token: str) -> str:
    return f"messenger:widget_session:{token}"


def create_widget_session(*, inbox_id: int, conversation_id: int, contact_id: str) -> WidgetSession:
    """
    Создаёт widget_session_token и сохраняет минимальный контекст в cache/Redis.

    Используется Widget API:
    - /api/widget/bootstrap/ создаёт и возвращает token;
    - /api/widget/send/ и /api/widget/poll/ принимают token и валидируют его.
    """
    token = secrets.token_urlsafe(32)
    data = {
        "inbox_id": inbox_id,
        "conversation_id": conversation_id,
        "contact_id": contact_id,
    }
    cache.set(_widget_session_cache_key(token), data, timeout=WIDGET_SESSION_TTL_SECONDS)
    return WidgetSession(token=token, **data)


def get_widget_session(token: str) -> Optional[WidgetSession]:
    if not token:
        return None
    data = cache.get(_widget_session_cache_key(token))
    if not data:
        return None
    return WidgetSession(token=token, **data)


def delete_widget_session(token: str) -> None:
    if not token:
        return
    cache.delete(_widget_session_cache_key(token))

