from __future__ import annotations

import logging
from typing import Any, Dict

from .models import Conversation, Message
from .services import record_message


logger = logging.getLogger("messenger.automation")


def _get_auto_reply_config(inbox) -> Dict[str, Any]:
    cfg = (getattr(inbox, "settings", None) or {}).get("automation") or {}
    auto = cfg.get("auto_reply") or {}
    enabled = bool(auto.get("enabled", False))
    body = (auto.get("body") or "").strip()
    return {"enabled": enabled, "body": body}


def run_automation_for_incoming_message(message: Message) -> None:
    """
    Простейшая автоматизация: автоответ на первый входящий месседж в диалоге.

    Условия:
    - direction == IN;
    - у диалога есть inbox и assignee;
    - статус диалога OPEN;
    - ещё не было исходящих сообщений в этом диалоге;
    - в настройках Inbox включён automation.auto_reply.enabled и задан текст.
    """
    try:
        if message.direction != Message.Direction.IN:
            return

        conversation: Conversation = message.conversation
        if not conversation or not conversation.inbox_id:
            return

        inbox = conversation.inbox
        cfg = _get_auto_reply_config(inbox)
        if not cfg["enabled"] or not cfg["body"]:
            return

        if conversation.status != Conversation.Status.OPEN:
            return

        if not conversation.assignee_id:
            # Чтобы не нарушать инвариант sender_user для OUT-сообщений, шлём автоответ
            # только если диалог уже назначен оператору.
            return

        # Уже есть исходящие сообщения — автоответ не нужен
        has_out = conversation.messages.filter(direction=Message.Direction.OUT).exists()
        if has_out:
            return

        # Отправляем автоответ от назначенного оператора
        record_message(
            conversation=conversation,
            direction=Message.Direction.OUT,
            body=cfg["body"],
            sender_user=conversation.assignee,
        )

    except Exception:
        logger.warning(
            "Failed to run automation for incoming message",
            exc_info=True,
            extra={"message_id": getattr(message, "id", None), "conversation_id": getattr(message, "conversation_id", None)},
        )

