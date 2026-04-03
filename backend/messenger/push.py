"""
Web Push уведомления для операторов (аналог Chatwoot PushNotificationService).

Использует VAPID + pywebpush для отправки browser push notifications.
Отправка идёт в фоне через threading (как в Chatwoot — daemon thread),
чтобы не блокировать HTTP response.
"""
import json
import logging
import threading

from django.conf import settings

logger = logging.getLogger("messenger.push")


def send_push_to_user(user, title, body, url=None, tag=None):
    """
    Отправить push-уведомление всем активным подпискам пользователя.
    Запускается в daemon-потоке, не блокирует вызывающий код.
    """
    if not settings.VAPID_PRIVATE_KEY or not settings.VAPID_PUBLIC_KEY:
        return

    from .models import PushSubscription

    subscriptions = list(
        PushSubscription.objects.filter(user=user, is_active=True)
    )
    if not subscriptions:
        return

    payload = json.dumps({
        "title": title,
        "body": body,
        "url": url or "/messenger/",
        "tag": tag or "messenger",
        "icon": "/static/messenger/icon-chat.png",
    }, ensure_ascii=False)

    def _send():
        try:
            from pywebpush import webpush, WebPushException
        except ImportError:
            logger.warning("pywebpush not installed, skipping push")
            return

        for sub in subscriptions:
            try:
                webpush(
                    subscription_info={
                        "endpoint": sub.endpoint,
                        "keys": {
                            "p256dh": sub.p256dh,
                            "auth": sub.auth,
                        },
                    },
                    data=payload,
                    vapid_private_key=settings.VAPID_PRIVATE_KEY,
                    vapid_claims={"sub": settings.VAPID_CLAIMS_EMAIL},
                    timeout=5,
                )
            except WebPushException as e:
                resp = getattr(e, "response", None)
                status_code = resp.status_code if resp else 0
                if status_code in (404, 410):
                    # Subscription expired/invalid — deactivate
                    sub.is_active = False
                    sub.save(update_fields=["is_active"])
                    logger.info("Push subscription deactivated (expired): %s", sub.endpoint[:50])
                else:
                    logger.warning("Push failed for %s: %s", sub.endpoint[:50], e)
            except Exception as e:
                logger.warning("Push error for %s: %s", sub.endpoint[:50], e)

    t = threading.Thread(target=_send, daemon=True)
    t.start()


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
