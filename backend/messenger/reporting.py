"""
Reporting events — create ReportingEvent records at key moments.

Call these from services/views when:
- First reply to a conversation
- Any reply (reply time)
- Conversation resolved
- Conversation opened
"""
from __future__ import annotations

import logging

from django.utils import timezone

from .models import Conversation, Message, ReportingEvent

logger = logging.getLogger("messenger.reporting")


def record_first_response(conversation: Conversation, message: Message) -> None:
    """
    Record first_response event: time from conversation creation to first operator reply.
    Called when the first OUT message is created in a conversation.
    """
    try:
        # Check if already recorded
        if ReportingEvent.objects.filter(
            conversation=conversation,
            name=ReportingEvent.EventType.FIRST_RESPONSE,
        ).exists():
            return

        delta = (message.created_at - conversation.created_at).total_seconds()
        ReportingEvent.objects.create(
            name=ReportingEvent.EventType.FIRST_RESPONSE,
            value=max(delta, 0),
            conversation=conversation,
            inbox=conversation.inbox,
            user=message.sender_user,
        )
    except Exception:
        logger.warning("Failed to record first_response event", exc_info=True)


def record_reply_time(conversation: Conversation, message: Message) -> None:
    """
    Record reply_time event: time from last incoming message to this outgoing reply.
    """
    try:
        last_incoming = (
            conversation.messages
            .filter(direction=Message.Direction.IN, created_at__lt=message.created_at)
            .order_by("-created_at")
            .values_list("created_at", flat=True)
            .first()
        )
        if not last_incoming:
            return

        delta = (message.created_at - last_incoming).total_seconds()
        ReportingEvent.objects.create(
            name=ReportingEvent.EventType.REPLY_TIME,
            value=max(delta, 0),
            conversation=conversation,
            inbox=conversation.inbox,
            user=message.sender_user,
        )
    except Exception:
        logger.warning("Failed to record reply_time event", exc_info=True)


def record_conversation_resolved(conversation: Conversation) -> None:
    """
    Record conversation_resolved event: time from open to resolved.
    """
    try:
        delta = (timezone.now() - conversation.created_at).total_seconds()
        ReportingEvent.objects.create(
            name=ReportingEvent.EventType.CONVERSATION_RESOLVED,
            value=max(delta, 0),
            conversation=conversation,
            inbox=conversation.inbox,
            user=conversation.assignee,
        )
    except Exception:
        logger.warning("Failed to record conversation_resolved event", exc_info=True)


def record_conversation_opened(conversation: Conversation) -> None:
    """
    Record conversation_opened event (for counting new conversations).
    """
    try:
        ReportingEvent.objects.create(
            name=ReportingEvent.EventType.CONVERSATION_OPENED,
            value=0,
            conversation=conversation,
            inbox=conversation.inbox,
        )
    except Exception:
        logger.warning("Failed to record conversation_opened event", exc_info=True)
