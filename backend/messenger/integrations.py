from __future__ import annotations

import hashlib
import hmac
import json
import logging
from typing import Any, Dict, Iterable, Optional

from django.utils import timezone

from .models import Conversation, Inbox, Message


logger = logging.getLogger("messenger.integrations")


def _is_safe_outbound_url(url: str) -> bool:
    """
    SSRF-защита: разрешаем только http(s) на публичные IP.
    Резолвим хост и запрещаем loopback/private/link-local/multicast/reserved.
    """
    import ipaddress
    import socket
    from urllib.parse import urlparse

    try:
        parsed = urlparse(url)
    except Exception:
        return False
    if parsed.scheme not in ("http", "https"):
        return False
    host = (parsed.hostname or "").strip()
    if not host:
        return False
    try:
        infos = socket.getaddrinfo(host, None)
    except Exception:
        return False
    for info in infos:
        try:
            ip = ipaddress.ip_address(info[4][0])
        except (ValueError, IndexError):
            return False
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_multicast
            or ip.is_reserved
            or ip.is_unspecified
        ):
            return False
    return True


def _get_webhook_config(inbox: Inbox) -> Optional[Dict[str, Any]]:
    """
    Достаёт конфиг webhook'а из inbox.settings.integrations.webhook.
    Ожидаемый формат:
    {
        "enabled": bool,
        "url": "https://...",
        "secret": "optional-shared-secret",
        "events": ["conversation.created", "conversation.closed", "message.in", "message.out"],
    }
    """
    try:
        cfg = (inbox.settings or {}).get("integrations") or {}
        webhook = cfg.get("webhook") or {}
        url = (webhook.get("url") or "").strip()
        if not url:
            return None
        enabled = bool(webhook.get("enabled", False))
        events = webhook.get("events") or []
        if not isinstance(events, (list, tuple)):
            events = []
        return {
            "enabled": enabled,
            "url": url,
            "secret": (webhook.get("secret") or "").strip(),
            "events": list(events),
        }
    except Exception:
        logger.exception("Failed to read webhook config from inbox.settings", extra={"inbox_id": inbox.id})
        return None


def _should_send_for_event(cfg: Dict[str, Any], event_type: str) -> bool:
    if not cfg or not cfg.get("enabled"):
        return False
    events: Iterable[str] = cfg.get("events") or []
    # Пустой список = по умолчанию все события
    if not events:
        return True
    return event_type in events


def _send_webhook_async(inbox: Inbox, event_type: str, payload: Dict[str, Any]) -> None:
    cfg = _get_webhook_config(inbox)
    if not cfg or not _should_send_for_event(cfg, event_type):
        return

    url: str = cfg["url"]
    secret: str = cfg.get("secret", "")

    if not _is_safe_outbound_url(url):
        logger.warning(
            "Webhook URL rejected by SSRF guard",
            extra={"inbox_id": inbox.id, "event_type": event_type, "url": url},
        )
        return

    try:
        body = json.dumps(payload, ensure_ascii=False, default=str)
    except Exception:
        logger.exception(
            "Failed to serialize webhook payload",
            extra={"inbox_id": inbox.id, "event_type": event_type},
        )
        return

    headers = {
        "Content-Type": "application/json",
        "X-Messenger-Event": event_type,
        "X-Messenger-Inbox-Id": str(inbox.id),
    }
    if secret:
        try:
            signature = hmac.new(
                secret.encode("utf-8"),
                body.encode("utf-8"),
                hashlib.sha256,
            ).hexdigest()
            headers["X-Messenger-Signature"] = signature
        except Exception:
            logger.exception(
                "Failed to compute webhook signature",
                extra={"inbox_id": inbox.id, "event_type": event_type},
            )

    # Переход с threading.Thread на Celery: daemon-поток умирал вместе с
    # gunicorn при рестартах, теряя payload. Celery-таск с retry/backoff
    # гарантирует at-least-once доставку (P1-7 bug-hunt).
    from .tasks import send_outbound_webhook

    send_outbound_webhook.delay(
        url=url,
        body=body,
        headers=headers,
        inbox_id=inbox.id,
        event_type=event_type,
    )


def notify_conversation_created(conversation: Conversation) -> None:
    """
    Событие: создан новый диалог (conversation.created).
    """
    if not conversation.inbox_id:
        return
    inbox = conversation.inbox
    payload: Dict[str, Any] = {
        "event": "conversation.created",
        "conversation": {
            "id": conversation.id,
            "inbox_id": conversation.inbox_id,
            "branch_id": conversation.branch_id,
            "status": conversation.status,
            "created_at": conversation.created_at or timezone.now(),
        },
    }
    if conversation.contact_id:
        payload["conversation"]["contact_id"] = str(conversation.contact_id)
    _send_webhook_async(inbox, "conversation.created", payload)


def notify_conversation_closed(conversation: Conversation) -> None:
    """
    Событие: диалог закрыт (conversation.closed).
    """
    if not conversation.inbox_id:
        return
    inbox = conversation.inbox
    payload: Dict[str, Any] = {
        "event": "conversation.closed",
        "conversation": {
            "id": conversation.id,
            "inbox_id": conversation.inbox_id,
            "branch_id": conversation.branch_id,
            "status": conversation.status,
        },
    }
    _send_webhook_async(inbox, "conversation.closed", payload)


def notify_message(message: Message) -> None:
    """
    Событие: новое сообщение (message.in / message.out).
    INTERNAL-сообщения пока не отправляем.
    """
    if not message.conversation_id or not message.conversation.inbox_id:
        return

    if message.direction == Message.Direction.IN:
        event_type = "message.in"
    elif message.direction == Message.Direction.OUT:
        event_type = "message.out"
    else:
        # INTERNAL можно добавить позже отдельным флагом
        return

    conversation = message.conversation
    inbox = conversation.inbox

    payload: Dict[str, Any] = {
        "event": event_type,
        "message": {
            "id": message.id,
            "conversation_id": conversation.id,
            "direction": message.direction,
            "body": message.body,
            "created_at": message.created_at or timezone.now(),
        },
        "conversation": {
            "id": conversation.id,
            "inbox_id": conversation.inbox_id,
            "branch_id": conversation.branch_id,
            "status": conversation.status,
        },
    }

    if message.sender_contact_id:
        payload["message"]["sender_contact_id"] = str(message.sender_contact_id)
    if message.sender_user_id:
        payload["message"]["sender_user_id"] = message.sender_user_id

    _send_webhook_async(inbox, event_type, payload)

