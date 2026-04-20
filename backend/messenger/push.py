"""
Web Push уведомления для операторов (аналог Chatwoot PushNotificationService).

Использует VAPID + pywebpush для отправки browser push notifications.
Доставка идёт через Celery-таск `messenger.send_push_notification` с retry —
раньше был `threading.Thread(daemon=True)`, но daemon-поток умирал вместе
с gunicorn при рестартах и терял payload (P1-8 bug-hunt).
"""

import json
import logging

from django.conf import settings

logger = logging.getLogger("messenger.push")


def _deliver_push_to_subscription(*, subscription_id: int, payload: dict) -> dict:
    """
    Реальная доставка Web Push одному subscriber'у.
    Вызывается из Celery-таска `messenger.send_push_notification`.
    """
    from .models import PushSubscription

    try:
        sub = PushSubscription.objects.get(id=subscription_id, is_active=True)
    except PushSubscription.DoesNotExist:
        return {"skipped": "inactive_or_missing"}

    try:
        from pywebpush import WebPushException, webpush
    except ImportError:
        logger.warning("pywebpush not installed, skipping push")
        return {"skipped": "pywebpush_missing"}

    data = json.dumps(payload, ensure_ascii=False)

    try:
        webpush(
            subscription_info={
                "endpoint": sub.endpoint,
                "keys": {
                    "p256dh": sub.p256dh,
                    "auth": sub.auth,
                },
            },
            data=data,
            vapid_private_key=settings.VAPID_PRIVATE_KEY,
            vapid_claims={"sub": settings.VAPID_CLAIMS_EMAIL},
            timeout=5,
        )
        return {"delivered": True, "subscription_id": subscription_id}
    except WebPushException as e:
        resp = getattr(e, "response", None)
        status_code = resp.status_code if resp else 0
        if status_code in (404, 410):
            # Subscription expired/invalid — deactivate, не ретраим
            sub.is_active = False
            sub.save(update_fields=["is_active"])
            logger.info("Push subscription deactivated (expired): %s", sub.endpoint[:50])
            return {"deactivated": True, "subscription_id": subscription_id}
        # Остальные ошибки — пробрасываем, Celery сделает retry с backoff
        logger.warning("Push failed for %s: %s", sub.endpoint[:50], e)
        raise


def send_push_to_user(user, title, body, url=None, tag=None):
    """
    Отправить push-уведомление всем активным подпискам пользователя.
    Ставит задачу в Celery на каждую подписку (at-least-once с retry).
    """
    if not settings.VAPID_PRIVATE_KEY or not settings.VAPID_PUBLIC_KEY:
        return

    from .models import PushSubscription
    from .tasks import send_push_notification

    subscription_ids = list(
        PushSubscription.objects.filter(user=user, is_active=True).values_list("id", flat=True)
    )
    if not subscription_ids:
        return

    payload = {
        "title": title,
        "body": body,
        "url": url or "/messenger/",
        "tag": tag or "messenger",
        "icon": "/static/messenger/icon-chat.png",
    }

    for sub_id in subscription_ids:
        send_push_notification.delay(subscription_id=sub_id, payload=payload)


def send_push_new_message(conversation, message):
    """
    Уведомить назначенного оператора о новом входящем сообщении.
    """
    assignee = conversation.assignee
    if not assignee:
        return

    # Не уведомлять, если оператор сам отправил
    if message.sender_user_id == assignee.id:
        return

    contact_name = ""
    if conversation.contact:
        c = conversation.contact
        contact_name = c.name or c.email or c.phone or "Посетитель"

    preview = (message.body or "")[:100]
    send_push_to_user(
        user=assignee,
        title=f"Сообщение от {contact_name}",
        body=preview,
        url=f"/messenger/#conversation/{conversation.id}",
        tag=f"msg-{message.id}",
    )


def send_push_assignment(conversation, assigned_to):
    """
    Уведомить оператора о назначении нового диалога.
    """
    contact_name = ""
    if conversation.contact:
        c = conversation.contact
        contact_name = c.name or c.email or c.phone or "Посетитель"

    send_push_to_user(
        user=assigned_to,
        title="Назначен новый диалог",
        body=contact_name,
        url=f"/messenger/#conversation/{conversation.id}",
        tag=f"assign-{conversation.id}",
    )
