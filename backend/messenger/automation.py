"""
Automation engine for messenger — Chatwoot-style event-driven rules.

Two layers:
1. Legacy auto_reply (inbox.settings.automation.auto_reply) — kept for backward compat.
2. AutomationRule model — flexible conditions + actions engine.

Usage:
    from messenger.automation import dispatch_event
    dispatch_event("conversation_created", conversation=conv)
    dispatch_event("message_created", conversation=conv, message=msg)
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from django.db.models import Q

from .models import AutomationRule, Conversation, Message

logger = logging.getLogger("messenger.automation")


# ─── Legacy auto-reply (backward compat) ────────────────────────────────

def _get_auto_reply_config(inbox) -> Dict[str, Any]:
    cfg = (getattr(inbox, "settings", None) or {}).get("automation") or {}
    auto = cfg.get("auto_reply") or {}
    enabled = bool(auto.get("enabled", False))
    body = (auto.get("body") or "").strip()
    return {"enabled": enabled, "body": body}


def run_automation_for_incoming_message(message: Message) -> None:
    """
    Простейшая автоматизация: автоответ на первый входящий месседж в диалоге.
    Защита от race condition: select_for_update на conversation + Redis lock.
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
            return

        # Защита от дублирования автоответов при параллельных входящих:
        # 1. Redis lock (быстрый путь — отсекает параллельные вызовы)
        from django.core.cache import cache
        lock_key = f"messenger:auto_reply_lock:{conversation.id}"
        if not cache.add(lock_key, "1", timeout=30):
            return  # Другой процесс уже обрабатывает автоответ

        try:
            # 2. Атомарная проверка: select_for_update + has_out
            from django.db import transaction
            with transaction.atomic():
                conv_locked = Conversation.objects.select_for_update().get(pk=conversation.pk)
                has_out = conv_locked.messages.filter(direction=Message.Direction.OUT).exists()
                if has_out:
                    return

                from .services import record_message
                record_message(
                    conversation=conv_locked,
                    direction=Message.Direction.OUT,
                    body=cfg["body"],
                    sender_user=conv_locked.assignee,
                )
        finally:
            cache.delete(lock_key)

    except Exception:
        logger.warning(
            "Failed to run legacy auto-reply",
            exc_info=True,
            extra={"message_id": getattr(message, "id", None)},
        )


# ─── AutomationRule engine ───────────────────────────────────────────────

def dispatch_event(
    event_name: str,
    conversation: Conversation,
    message: Optional[Message] = None,
) -> int:
    """
    Основная точка входа. Вызывается при событиях:
    - conversation_created
    - message_created
    - conversation_updated

    Находит подходящие правила, проверяет условия, выполняет действия.
    Возвращает кол-во выполненных правил.
    """
    rules = AutomationRule.objects.filter(
        event_name=event_name,
        is_active=True,
    ).filter(
        Q(inbox=conversation.inbox) | Q(inbox__isnull=True),
    ).order_by("id")

    executed = 0
    for rule in rules:
        try:
            if _evaluate_conditions(rule.conditions, conversation, message):
                _execute_actions(rule.actions, conversation, message)
                executed += 1
                logger.info(
                    "Automation rule %s (id=%d) fired for conversation %d",
                    rule.name, rule.id, conversation.id,
                )
        except Exception:
            logger.warning(
                "Automation rule %s (id=%d) failed",
                rule.name, rule.id,
                exc_info=True,
            )
    return executed


# ─── Conditions evaluator ────────────────────────────────────────────────

def _evaluate_conditions(
    conditions: List[Dict],
    conversation: Conversation,
    message: Optional[Message],
) -> bool:
    """
    Все условия — AND. Каждое условие:
    {
      "attribute_key": "status" | "assignee_id" | "priority" | "inbox_id" | "message_type" | "content",
      "filter_operator": "equal_to" | "not_equal_to" | "contains" | "does_not_contain" | "is_present" | "is_not_present",
      "values": [...]
    }
    """
    if not conditions:
        return True  # Нет условий = всегда срабатывает

    for cond in conditions:
        if not isinstance(cond, dict):
            continue
        attr_key = cond.get("attribute_key", "")
        operator = cond.get("filter_operator", "")
        values = cond.get("values", [])

        actual = _get_attribute_value(attr_key, conversation, message)

        if not _check_operator(actual, operator, values):
            return False

    return True


def _get_attribute_value(
    attr_key: str,
    conversation: Conversation,
    message: Optional[Message],
) -> Any:
    """Получить значение атрибута для проверки условия."""
    if attr_key == "status":
        return conversation.status
    elif attr_key == "assignee_id":
        return conversation.assignee_id
    elif attr_key == "priority":
        return conversation.priority
    elif attr_key == "inbox_id":
        return conversation.inbox_id
    elif attr_key == "browser_language":
        return getattr(conversation.contact, "browser_language", None)
    elif attr_key == "country":
        return getattr(conversation.contact, "country", None)
    elif attr_key == "message_type" and message:
        return message.direction
    elif attr_key == "content" and message:
        return message.body or ""
    return None


def _check_operator(actual: Any, operator: str, values: list) -> bool:
    """Проверить один оператор условия."""
    actual_str = str(actual) if actual is not None else ""
    values_str = [str(v) for v in values] if values else []

    if operator == "equal_to":
        return actual_str in values_str
    elif operator == "not_equal_to":
        return actual_str not in values_str
    elif operator == "contains":
        return any(v.lower() in actual_str.lower() for v in values_str)
    elif operator == "does_not_contain":
        return not any(v.lower() in actual_str.lower() for v in values_str)
    elif operator == "is_present":
        return bool(actual)
    elif operator == "is_not_present":
        return not bool(actual)
    return True  # Неизвестный оператор — пропускаем


# ─── Actions executor ────────────────────────────────────────────────────

def _execute_actions(
    actions: List[Dict],
    conversation: Conversation,
    message: Optional[Message],
) -> None:
    """
    Выполнить список действий:
    {
      "action_name": "assign_agent" | "resolve" | "add_label" | "remove_label" | "send_message" | "set_priority" | "mute",
      "action_params": [...]
    }
    """
    for action in actions:
        if not isinstance(action, dict):
            continue
        action_name = action.get("action_name", "")
        params = action.get("action_params", [])
        _run_action(action_name, params, conversation, message)


def _run_action(
    action_name: str,
    params: list,
    conversation: Conversation,
    message: Optional[Message],
) -> None:
    """Выполнить одно действие."""

    if action_name == "assign_agent":
        _action_assign_agent(conversation, params)

    elif action_name == "resolve":
        _action_resolve(conversation)

    elif action_name == "add_label":
        _action_add_label(conversation, params)

    elif action_name == "remove_label":
        _action_remove_label(conversation, params)

    elif action_name == "send_message":
        _action_send_message(conversation, params)

    elif action_name == "set_priority":
        _action_set_priority(conversation, params)

    elif action_name == "mute":
        _action_mute(conversation)

    else:
        logger.warning("Unknown automation action: %s", action_name)


def _action_assign_agent(conversation: Conversation, params: list) -> None:
    """Назначить оператора. params = [user_id]"""
    if not params:
        return
    from accounts.models import User
    from .services import assign_conversation
    try:
        user = User.objects.get(pk=int(params[0]), is_active=True)
        assign_conversation(conversation, user)
    except (User.DoesNotExist, ValueError, IndexError):
        logger.warning("assign_agent: user %s not found", params)


def _action_resolve(conversation: Conversation) -> None:
    """Перевести диалог в resolved."""
    if conversation.status != Conversation.Status.RESOLVED:
        conversation.status = Conversation.Status.RESOLVED
        conversation.save(update_fields=["status"])


def _action_add_label(conversation: Conversation, params: list) -> None:
    """Добавить метки. params = [label_id, ...]"""
    from .models import ConversationLabel
    for label_id in params:
        try:
            label = ConversationLabel.objects.get(pk=int(label_id))
            conversation.labels.add(label)
        except (ConversationLabel.DoesNotExist, ValueError):
            pass


def _action_remove_label(conversation: Conversation, params: list) -> None:
    """Убрать метки. params = [label_id, ...]"""
    for label_id in params:
        try:
            conversation.labels.remove(int(label_id))
        except (ValueError, TypeError):
            pass


def _action_send_message(conversation: Conversation, params: list) -> None:
    """Отправить автосообщение. params = ["текст сообщения"]"""
    if not params:
        return
    body = str(params[0]).strip()
    if not body:
        return
    from .services import record_message
    # Отправляем от назначенного оператора (или без sender, если нет)
    record_message(
        conversation=conversation,
        direction=Message.Direction.OUT,
        body=body,
        sender_user=conversation.assignee,
    )


def _action_set_priority(conversation: Conversation, params: list) -> None:
    """Установить приоритет. params = [10|20|30]"""
    if not params:
        return
    try:
        priority = int(params[0])
        if priority in (Conversation.Priority.LOW, Conversation.Priority.NORMAL, Conversation.Priority.HIGH):
            conversation.priority = priority
            conversation.save(update_fields=["priority"])
    except (ValueError, TypeError):
        pass


def _action_mute(conversation: Conversation) -> None:
    """Замьютить диалог (выключить уведомления)."""
    if hasattr(conversation, "is_muted"):
        conversation.is_muted = True
        conversation.save(update_fields=["is_muted"])
