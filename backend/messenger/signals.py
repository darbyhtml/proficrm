"""Сигналы мессенджера."""

import logging

from django.db.models.signals import post_save
from django.dispatch import receiver

from messenger.models import Conversation

logger = logging.getLogger("messenger.auto_assign")


@receiver(post_save, sender=Conversation)
def auto_assign_new_conversation(sender, instance: Conversation, created: bool, **kwargs):
    """Автоназначение только что созданного диалога на менеджера филиала.

    Срабатывает лишь на create и только если у диалога нет явного assignee
    и статус — OPEN. Используется queryset.update() внутри оркестратора,
    поэтому рекурсии сигнала не возникает.
    """
    if not created:
        return
    if instance.assignee_id:
        return
    if instance.status != Conversation.Status.OPEN:
        return

    # Ленивый импорт — чтобы избежать circular import при старте приложения.
    from messenger.assignment_services.auto_assign import auto_assign_conversation

    try:
        auto_assign_conversation(instance)
    except Exception:
        logger.exception("auto_assign failed for conversation %s", instance.pk)
