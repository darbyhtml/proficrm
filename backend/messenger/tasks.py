"""
Celery-задачи мессенджера (аналог Chatwoot Sidekiq jobs).

- auto_resolve: закрытие неактивных диалогов
- escalate: переназначение диалогов при таймауте
- check_offline_operators: перевод операторов в offline по таймауту heartbeat
"""
import logging
from datetime import timedelta

from celery import shared_task
from django.conf import settings
from django.utils import timezone

logger = logging.getLogger("messenger.tasks")


@shared_task(bind=True, max_retries=0, soft_time_limit=120, acks_late=True)
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


@shared_task(bind=True, max_retries=0, soft_time_limit=60, acks_late=True)
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


@shared_task(bind=True, max_retries=2, soft_time_limit=30, acks_late=True)
def dispatch_async_listeners(self, event_name: str, timestamp_iso: str, data: dict):
    """
    Выполнить асинхронные event-слушатели через Celery.

    Получает сериализованные данные от EventDispatcher и вызывает
    все зарегистрированные async-слушатели для данного события.
    """
    from importlib import import_module
    from datetime import datetime

    from .dispatchers import get_async_listener_registry

    registry = get_async_listener_registry()
    listener_paths = registry.get(event_name, [])
    if not listener_paths:
        return {"event": event_name, "listeners": 0}

    timestamp = datetime.fromisoformat(timestamp_iso)
    executed = 0

    for path in listener_paths:
        try:
            module_path, func_name = path.rsplit(".", 1)
            module = import_module(module_path)
            listener = getattr(module, func_name)
            listener(event_name, timestamp, data)
            executed += 1
        except Exception:
            logger.error(
                "Error in async listener %s for event %s",
                path, event_name,
                exc_info=True,
            )

    logger.info(
        "Async dispatch: event=%s, listeners=%d/%d executed",
        event_name, executed, len(listener_paths),
    )
    return {"event": event_name, "listeners": executed}


@shared_task(bind=True, max_retries=1, soft_time_limit=30, acks_late=True)
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
            reply_to="",
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


@shared_task(name="messenger.escalate_waiting_conversations")
def escalate_waiting_conversations():
    """Эскалация молчаливых диалогов по waiting_minutes.

    Идемпотентна: каждый уровень триггерит события ровно один раз
    через Conversation.escalation_level (0/1/2/3/4):
      1 — warn (тихо, без уведомлений)
      2 — urgent (уведомление ассигни)
      3 — rop_alert (уведомления всем РОП филиала)
      4 — pool_return (снять ассигни + уведомить онлайн-менеджеров филиала)
    """
    from django.db import models as dj_models

    from accounts.models import User
    from notifications.models import Notification

    from .models import Conversation

    thresholds = Conversation.escalation_thresholds()
    now = timezone.now()
    stats = {"warn": 0, "urgent": 0, "rop_alert": 0, "pool_return": 0}

    candidates = Conversation.objects.filter(
        status__in=[Conversation.Status.OPEN, Conversation.Status.PENDING],
        last_customer_msg_at__isnull=False,
    ).exclude(
        last_agent_msg_at__gte=dj_models.F("last_customer_msg_at"),
    )

    for conv in candidates.select_related("assignee", "branch", "contact"):
        waiting = (now - conv.last_customer_msg_at).total_seconds() / 60
        target_level = 0
        if waiting >= thresholds["pool_return_min"]:
            target_level = 4
        elif waiting >= thresholds["rop_alert_min"]:
            target_level = 3
        elif waiting >= thresholds["urgent_min"]:
            target_level = 2
        elif waiting >= thresholds["warn_min"]:
            target_level = 1

        if target_level <= conv.escalation_level:
            continue

        contact_name = conv.contact.name if conv.contact else ""

        if target_level == 1:
            stats["warn"] += 1
        elif target_level == 2 and conv.assignee_id:
            Notification.objects.create(
                user=conv.assignee,
                kind=Notification.Kind.INFO,
                title=f"Клиент ждёт {int(waiting)} мин",
                body=f"Диалог #{conv.id} — {contact_name}",
                url=f"/messenger/?conv={conv.id}",
                payload={"conversation_id": conv.id, "level": "urgent"},
            )
            stats["urgent"] += 1
        elif target_level == 3 and conv.branch_id:
            rops = User.objects.filter(
                branch_id=conv.branch_id,
                role=User.Role.SALES_HEAD,
                is_active=True,
            )
            assignee_name = (
                conv.assignee.get_full_name() or conv.assignee.username
                if conv.assignee
                else "не назначен"
            )
            for rop in rops:
                Notification.objects.create(
                    user=rop,
                    kind=Notification.Kind.INFO,
                    title=f"Клиент ждёт {int(waiting)} мин — требуется вмешательство",
                    body=f"Диалог #{conv.id} у {assignee_name}",
                    url=f"/messenger/?conv={conv.id}",
                    payload={"conversation_id": conv.id, "level": "rop_alert"},
                )
            stats["rop_alert"] += 1
        elif target_level == 4 and conv.branch_id:
            Conversation.objects.filter(pk=conv.pk).update(assignee=None)
            branch_managers = User.objects.filter(
                branch_id=conv.branch_id,
                role=User.Role.MANAGER,
                is_active=True,
                messenger_online=True,
            )
            for m in branch_managers:
                Notification.objects.create(
                    user=m,
                    kind=Notification.Kind.INFO,
                    title=f"Диалог возвращён в пул — ждёт {int(waiting)} мин",
                    body=f"Диалог #{conv.id} ожидает свободного оператора",
                    url=f"/messenger/?conv={conv.id}",
                    payload={"conversation_id": conv.id, "level": "pool_return"},
                )
            stats["pool_return"] += 1

        Conversation.objects.filter(pk=conv.pk).update(
            escalation_level=target_level,
            last_escalated_at=now,
        )

    if any(stats.values()):
        logger.info("escalate_waiting_conversations stats: %s", stats)
    return stats


@shared_task(name="messenger.check_offline_operators")
def check_offline_operators(stale_seconds: int = 90):
    """Переводит операторов в offline, если heartbeat не приходил дольше stale_seconds.

    Запускается celery-beat раз в минуту. Порог 90 секунд = 3 интервала
    heartbeat (30с × 3). Это страховка от падения клиента без явного logout.
    """
    from accounts.models import User
    threshold = timezone.now() - timedelta(seconds=stale_seconds)
    updated = User.objects.filter(
        messenger_online=True,
        messenger_last_seen__lt=threshold,
    ).update(messenger_online=False)
    return {"marked_offline": updated}
