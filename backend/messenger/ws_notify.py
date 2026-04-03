"""
Утилиты для отправки WebSocket-уведомлений из синхронного кода.

Использование из views/services:
    from messenger.ws_notify import notify_new_message, notify_conversation_updated

    notify_new_message(conversation, message_data)
    notify_conversation_updated(conversation, {"status": "resolved"})
"""

import logging

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer

logger = logging.getLogger("messenger.ws")


def _get_layer():
    """Получить channel layer (может быть None в тестах без Redis)."""
    try:
        return get_channel_layer()
    except Exception:
        return None


def notify_new_message(conversation, message_data: dict) -> None:
    """Отправить уведомление о новом сообщении в диалог и inbox."""
    layer = _get_layer()
    if not layer:
        return

    group_send = async_to_sync(layer.group_send)

    # В канал диалога
    try:
        group_send(
            f"conversation_{conversation.id}",
            {
                "type": "new_message",
                "conversation_id": conversation.id,
                "message": message_data,
            },
        )
    except Exception:
        logger.debug("WS notify_new_message failed for conversation %s", conversation.id, exc_info=True)

    # В канал inbox (для списка диалогов)
    if conversation.inbox_id:
        try:
            group_send(
                f"inbox_{conversation.inbox_id}",
                {
                    "type": "new_message",
                    "conversation_id": conversation.id,
                    "message": message_data,
                },
            )
        except Exception:
            pass

    # Личное уведомление назначенному оператору
    if conversation.assignee_id:
        try:
            group_send(
                f"operator_{conversation.assignee_id}",
                {
                    "type": "operator.notification",
                    "title": "Новое сообщение",
                    "body": (message_data.get("body") or "")[:100],
                    "conversation_id": conversation.id,
                },
            )
        except Exception:
            pass


def notify_conversation_updated(conversation, changes: dict) -> None:
    """Отправить уведомление об изменении диалога."""
    layer = _get_layer()
    if not layer:
        return

    group_send = async_to_sync(layer.group_send)

    try:
        group_send(
            f"conversation_{conversation.id}",
            {
                "type": "conversation_updated",
                "conversation_id": conversation.id,
                "changes": changes,
            },
        )
    except Exception:
        logger.debug("WS notify_conversation_updated failed", exc_info=True)

    if conversation.inbox_id:
        try:
            group_send(
                f"inbox_{conversation.inbox_id}",
                {
                    "type": "conversation_updated",
                    "conversation_id": conversation.id,
                    "changes": changes,
                },
            )
        except Exception:
            pass


def notify_new_conversation(conversation, conversation_data: dict) -> None:
    """Отправить уведомление о новом диалоге в inbox."""
    layer = _get_layer()
    if not layer:
        return

    group_send = async_to_sync(layer.group_send)

    if conversation.inbox_id:
        try:
            group_send(
                f"inbox_{conversation.inbox_id}",
                {
                    "type": "new_conversation",
                    "conversation": conversation_data,
                },
            )
        except Exception:
            pass

    # Уведомить назначенного оператора
    if conversation.assignee_id:
        try:
            group_send(
                f"operator_{conversation.assignee_id}",
                {
                    "type": "operator.notification",
                    "title": "Новый диалог",
                    "body": f"Вам назначен новый диалог #{conversation.id}",
                    "conversation_id": conversation.id,
                },
            )
        except Exception:
            pass
