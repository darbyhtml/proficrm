"""Policy maintenance tasks.

W2.1.3a (2026-04-22): retention для policy decision events (Q17).
"""

from __future__ import annotations

import logging
from datetime import timedelta

from celery import shared_task
from django.utils import timezone

logger = logging.getLogger(__name__)

# Chunk size для delete batches — не блокировать DB длительными транзакциями.
# Wave 0.1 audit (hotlist #6) flagged: single-transaction DELETE на 9.5M rows
# triggers OOM + lock contention. Chunked delete = safer pattern.
PURGE_CHUNK_SIZE = 10_000


@shared_task(name="policy.purge_old_events")
def purge_old_policy_events(retention_days: int = 14) -> int:
    """Delete policy audit events старше retention_days.

    Q17 decision 2026-04-22 (W2.1.3a): 14-day retention для policy events.
    Complements deny-only logging в `policy.engine._log_decision()`.

    Args:
        retention_days: Сколько дней хранить policy events (default 14).

    Returns:
        Количество удалённых rows.

    Scope: Only events с `entity_type='policy'` (из `_log_decision()`).
    Chunked delete (10K per batch) чтобы не блокировать DB.
    """
    # Late import чтобы избежать circular import
    from audit.models import ActivityEvent

    cutoff = timezone.now() - timedelta(days=retention_days)

    base_qs = ActivityEvent.objects.filter(
        entity_type="policy",
        created_at__lt=cutoff,
    )

    total_to_delete = base_qs.count()
    if total_to_delete == 0:
        logger.info(
            "purge_old_policy_events: no events older than %s days",
            retention_days,
        )
        return 0

    deleted_total = 0
    batch_num = 0
    while True:
        # Re-query каждую итерацию, т.к. предыдущий DELETE сдвинул выборку
        ids = list(
            ActivityEvent.objects.filter(
                entity_type="policy",
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
            "purge_old_policy_events: batch %s deleted %s events",
            batch_num,
            deleted_count,
        )

    logger.info(
        "purge_old_policy_events: total %s events deleted (older than %s days)",
        deleted_total,
        retention_days,
    )
    return deleted_total
