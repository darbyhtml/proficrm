"""
Celery tasks для модуля phonebridge.
"""
from __future__ import annotations

import logging
from celery import shared_task
from django.utils import timezone
from datetime import timedelta

from phonebridge.models import CallRequest

logger = logging.getLogger(__name__)


@shared_task(name="phonebridge.tasks.clean_old_call_requests")
def clean_old_call_requests(days_old: int = 30):
    """
    Очистка старых запросов на звонок.
    
    Args:
        days_old: Удалять записи старше N дней (по умолчанию 30)
    """
    try:
        cutoff_date = timezone.now() - timedelta(days=days_old)
        deleted_count, _ = CallRequest.objects.filter(created_at__lt=cutoff_date).delete()
        logger.info(f"Cleaned {deleted_count} old call requests (older than {days_old} days)")
        return {"deleted": deleted_count}
    except Exception as exc:
        logger.error(f"Error cleaning old call requests: {exc}", exc_info=True)
        raise

