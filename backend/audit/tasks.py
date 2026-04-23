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

# Chunk size для delete batches — не блокировать DB длительными транзакциями.
# Wave 0.1 audit (hotlist #6) flagged: single-transaction DELETE на 9.5M rows
# triggers OOM + lock contention. Chunked delete = safer pattern, pooled с
# policy.tasks.purge_old_policy_events (W2.1.3a).
PURGE_CHUNK_SIZE = 10_000
# Safety cap — не работать бесконечно. 10K batch × 10K cap = 100M rows theoretical
# максимум за один запуск.
PURGE_SAFETY_BATCH_CAP = 10_000


@shared_task(name="audit.tasks.purge_old_activity_events", ignore_result=True)
def purge_old_activity_events() -> int:
    """
    Удаляет записи ActivityEvent старше ACTIVITY_EVENT_RETENTION_DAYS дней.

    W3.2 (hotlist #6, 2026-04-23): chunked delete (10K per batch) —
    ported из `policy.tasks.purge_old_policy_events` (W2.1.3a).
    Каждый batch в отдельной транзакции, не блокирует DB при масштабе
    в миллионы строк.

    Returns:
        Количество удалённых rows.
    """
    from audit.models import ActivityEvent

    days = getattr(settings, "ACTIVITY_EVENT_RETENTION_DAYS", _DEFAULT_ACTIVITY_RETENTION_DAYS)
    cutoff = timezone.now() - timezone.timedelta(days=days)

    deleted_total = 0
    batch_num = 0
    while True:
        # Re-query каждую итерацию, т.к. предыдущий DELETE сдвинул выборку
        ids = list(
            ActivityEvent.objects.filter(
                created_at__lt=cutoff,
            ).values_list(
                "id", flat=True
            )[:PURGE_CHUNK_SIZE]
        )
        if not ids:
            break
        batch_num += 1
        deleted_count, _ = ActivityEvent.objects.filter(id__in=ids).delete()
        deleted_total += deleted_count
        logger.info(
            "purge_old_activity_events: batch %s deleted %s events",
            batch_num,
            deleted_count,
        )
        if batch_num >= PURGE_SAFETY_BATCH_CAP:
            logger.warning(
                "purge_old_activity_events: safety cap reached at %s batches "
                "(%s deleted total); scheduling more work for next run",
                batch_num,
                deleted_total,
            )
            break

    logger.info(
        "purge_old_activity_events: total %s events deleted (older than %s days)",
        deleted_total,
        days,
    )
    return deleted_total


@shared_task(name="audit.tasks.purge_old_error_logs", ignore_result=True)
def purge_old_error_logs() -> None:
    """
    Двухступенчатая чистка ErrorLog:
      1. resolved=True старше ERRORLOG_RETENTION_DAYS (90d).
      2. Любые (включая resolved=False) старше ERRORLOG_HARD_RETENTION_DAYS
         (по умолчанию 180d) — защита от бесконечного роста таблицы, когда
         нерешённые ошибки никто не закрывает.
    """
    from audit.models import ErrorLog

    now = timezone.now()
    days = getattr(settings, "ERRORLOG_RETENTION_DAYS", _DEFAULT_ERRORLOG_RETENTION_DAYS)
    cutoff = now - timezone.timedelta(days=days)
    soft_deleted, _ = ErrorLog.objects.filter(created_at__lt=cutoff, resolved=True).delete()

    hard_days = getattr(settings, "ERRORLOG_HARD_RETENTION_DAYS", 180)
    hard_cutoff = now - timezone.timedelta(days=hard_days)
    hard_deleted, _ = ErrorLog.objects.filter(created_at__lt=hard_cutoff).delete()

    logger.info(
        "purge_old_error_logs: удалено resolved=%d (>%dd) + hard=%d (>%dd)",
        soft_deleted,
        days,
        hard_deleted,
        hard_days,
    )
