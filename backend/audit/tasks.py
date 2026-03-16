"""
Celery-задачи для очистки устаревших записей audit-приложения.
"""
from __future__ import annotations

import logging

from celery import shared_task
from django.conf import settings
from django.utils import timezone

logger = logging.getLogger(__name__)

# Срок хранения по умолчанию — 180 дней (6 месяцев)
_DEFAULT_ACTIVITY_RETENTION_DAYS = 180
# Срок хранения ErrorLog — 90 дней
_DEFAULT_ERRORLOG_RETENTION_DAYS = 90


@shared_task(name="audit.tasks.purge_old_activity_events", ignore_result=True)
def purge_old_activity_events() -> None:
    """
    Удаляет записи ActivityEvent старше ACTIVITY_EVENT_RETENTION_DAYS дней.
    Запускается через Celery Beat еженедельно.
    """
    from audit.models import ActivityEvent

    days = getattr(settings, "ACTIVITY_EVENT_RETENTION_DAYS", _DEFAULT_ACTIVITY_RETENTION_DAYS)
    cutoff = timezone.now() - timezone.timedelta(days=days)
    deleted, _ = ActivityEvent.objects.filter(created_at__lt=cutoff).delete()
    logger.info("purge_old_activity_events: удалено %d записей (старше %d дней)", deleted, days)


@shared_task(name="audit.tasks.purge_old_error_logs", ignore_result=True)
def purge_old_error_logs() -> None:
    """
    Удаляет записи ErrorLog старше ERRORLOG_RETENTION_DAYS дней.
    Запускается через Celery Beat еженедельно.
    """
    from audit.models import ErrorLog

    days = getattr(settings, "ERRORLOG_RETENTION_DAYS", _DEFAULT_ERRORLOG_RETENTION_DAYS)
    cutoff = timezone.now() - timezone.timedelta(days=days)
    deleted, _ = ErrorLog.objects.filter(created_at__lt=cutoff, resolved=True).delete()
    logger.info("purge_old_error_logs: удалено %d записей (старше %d дней, resolved=True)", deleted, days)
