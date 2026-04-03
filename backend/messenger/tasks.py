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


@shared_task(bind=True, max_retries=1, soft_time_limit=30)
def send_offline_email_notification(self, conversation_id: int, message_id: int):
    """
    Отправить email оператору, если он офлайн и получил новое сообщение.

    Throttle: максимум 1 email на диалог каждые 15 минут (через Redis cache).
    """
    from django.core.cache import cache
    from .models import Conversation, Message, AgentProfile

    try:
        conversation = Conversation.objects.select_related("assignee", "contact", "inbox").get(pk=conversation_id)
        message = Message.objects.get(pk=message_id)
    except (Conversation.DoesNotExist, Message.DoesNotExist):
        return {"status": "not_found"}

    assignee = conversation.assignee
    if not assignee or not assignee.email:
        return {"status": "no_assignee_or_email"}

    # Проверяем офлайн/away статус оператора
    try:
        profile = assignee.agent_profile
        if profile.status not in (AgentProfile.Status.OFFLINE, AgentProfile.Status.AWAY):
            return {"status": "agent_online"}
    except AgentProfile.DoesNotExist:
        pass  # Нет профиля = считаем офлайн

    # Throttle: 1 email на диалог каждые 15 минут
    cache_key = f"messenger:email_notify:{conversation_id}"
    if cache.get(cache_key):
        return {"status": "throttled"}
    cache.set(cache_key, "1", timeout=900)

    # Собираем данные для письма
    contact_name = conversation.contact.name if conversation.contact else "Посетитель"
    inbox_name = conversation.inbox.name if conversation.inbox else "Мессенджер"
    msg_preview = (message.body or "")[:200]

    subject = f"Новое сообщение от {contact_name} — {inbox_name}"
    body = (
        f"Здравствуйте, {assignee.get_full_name() or assignee.username}!\n\n"
        f"Вам пришло новое сообщение в мессенджере:\n\n"
        f"От: {contact_name}\n"
        f"Сообщение: {msg_preview}\n\n"
        f"Откройте CRM, чтобы ответить.\n"
    )

    try:
        from mailer.models import GlobalMailAccount
        account = GlobalMailAccount.load()
        if not account.is_enabled or not account.smtp_username:
            logger.info("Email notification skipped: GlobalMailAccount disabled")
            return {"status": "smtp_disabled"}

        from mailer.smtp_sender import build_message, send_via_smtp
        msg = build_message(
            account=account,
            to_email=assignee.email,
            subject=subject,
            body_text=body,
            body_html="",
        )
        send_via_smtp(account=account, msg=msg)
        logger.info(
            "Sent offline email notification to %s for conversation %d",
            assignee.email, conversation_id,
        )
        return {"status": "sent"}
    except Exception:
        logger.warning(
            "Failed to send offline email notification",
            exc_info=True,
            extra={"conversation_id": conversation_id, "assignee_id": assignee.id},
        )
        raise  # Позволяет retry
