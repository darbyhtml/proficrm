"""
Celery-задачи для очистки устаревших уведомлений.
"""
from __future__ import annotations

import logging

from celery import shared_task
from django.conf import settings
from django.utils import timezone

logger = logging.getLogger(__name__)

# Срок хранения по умолчанию — 90 дней (3 месяца)
_DEFAULT_NOTIFICATION_RETENTION_DAYS = 90


@shared_task(name="notifications.tasks.purge_old_notifications", ignore_result=True)
def purge_old_notifications() -> None:
    """
    Удаляет прочитанные уведомления старше NOTIFICATION_RETENTION_DAYS дней.
    Непрочитанные уведомления не удаляются.
    Запускается через Celery Beat еженедельно.
    """
    from notifications.models import Notification

    days = getattr(settings, "NOTIFICATION_RETENTION_DAYS", _DEFAULT_NOTIFICATION_RETENTION_DAYS)
    cutoff = timezone.now() - timezone.timedelta(days=days)
    deleted, _ = Notification.objects.filter(created_at__lt=cutoff, is_read=True).delete()
    logger.info(
        "purge_old_notifications: удалено %d записей (старше %d дней, is_read=True)", deleted, days
    )
