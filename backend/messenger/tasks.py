"""
Celery-задачи мессенджера (аналог Chatwoot Sidekiq jobs).

- auto_resolve: закрытие неактивных диалогов
- escalate: переназначение диалогов при таймауте
"""
import logging

from celery import shared_task
from django.conf import settings
from django.utils import timezone

logger = logging.getLogger("messenger.tasks")


@shared_task(bind=True, max_retries=0, soft_time_limit=120)
def auto_resolve_conversations(self):
    """
    Автоматически закрывать диалоги без активности (аналог Chatwoot auto_resolve).

    Правила:
    - status=RESOLVED и нет активности N дней -> CLOSED
    - status=OPEN/PENDING и нет активности от контакта N часов -> RESOLVED
    """
    from .models import Conversation

    now = timezone.now()
    resolved_to_closed_days = getattr(settings, "MESSENGER_RETENTION_RESOLVED_TO_CLOSED_DAYS", 90)
    auto_resolve_hours = getattr(settings, "MESSENGER_AUTO_RESOLVE_HOURS", 24)

    # 1. RESOLVED -> CLOSED (после N дней)
    cutoff_closed = now - timezone.timedelta(days=resolved_to_closed_days)
    closed_count = Conversation.objects.filter(
        status=Conversation.Status.RESOLVED,
        last_activity_at__lt=cutoff_closed,
    ).update(status=Conversation.Status.CLOSED)

    # 2. OPEN/PENDING -> RESOLVED (после N часов без активности контакта)
    cutoff_resolved = now - timezone.timedelta(hours=auto_resolve_hours)
    resolved_count = Conversation.objects.filter(
        status__in=[Conversation.Status.OPEN, Conversation.Status.PENDING],
        last_activity_at__lt=cutoff_resolved,
    ).exclude(
        # Не трогать snoozed
        snoozed_until__gt=now,
    ).update(status=Conversation.Status.RESOLVED)

    if closed_count or resolved_count:
        logger.info(
            "Auto-resolve: %d resolved->closed, %d open/pending->resolved",
            closed_count, resolved_count,
        )

    return {"closed": closed_count, "resolved": resolved_count}


@shared_task(bind=True, max_retries=0, soft_time_limit=60)
def escalate_stalled_conversations(self):
    """
    Переназначить диалоги, где оператор не открыл чат в течение таймаута.
    Обёртка над management command escalate_messenger_conversations.
    """
    from .services import get_conversations_eligible_for_escalation, escalate_conversation

    conversations = get_conversations_eligible_for_escalation()
    escalated = 0
    for conv in conversations:
        try:
            escalate_conversation(conv)
            escalated += 1
        except Exception:
            logger.warning("Failed to escalate conversation %s", conv.id, exc_info=True)

    if escalated:
        logger.info("Escalated %d conversations", escalated)

    return {"escalated": escalated}
