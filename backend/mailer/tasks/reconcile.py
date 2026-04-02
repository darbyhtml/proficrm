"""
Celery-задача сверки очереди рассылок (reconcile).
"""
from __future__ import annotations

import logging
from datetime import timedelta

from celery import shared_task
from django.db import transaction
from django.db.models import Count, Q
from django.utils import timezone

from mailer.models import Campaign, CampaignQueue, CampaignRecipient
from mailer.constants import STUCK_CAMPAIGN_TIMEOUT_MINUTES

logger = logging.getLogger(__name__)


@shared_task(name="mailer.tasks.reconcile_campaign_queue")
def reconcile_campaign_queue():
    """
    Периодическая сверка очереди и статусов кампаний:
    - Несколько PROCESSING → оставляем одну, остальные → PENDING
    - Queue активна, но pending-получателей нет → COMPLETED + кампания SENT
    - Queue активна, кампания не READY/SENDING → CANCELLED
    - READY/SENDING с pending, но без записи в очереди → создаём запись
    """
    now = timezone.now()

    # 1) Исправляем «несколько PROCESSING» — должна быть только одна
    processing = list(
        CampaignQueue.objects.filter(status=CampaignQueue.Status.PROCESSING)
        .order_by("started_at", "queued_at")
        .values_list("id", flat=True)
    )
    if len(processing) > 1:
        keep_id = processing[0]
        CampaignQueue.objects.filter(id__in=processing[1:]).update(
            status=CampaignQueue.Status.PENDING, started_at=None
        )
        logger.warning(
            "Queue reconcile: multiple PROCESSING, kept %s, reset %d to PENDING",
            keep_id,
            len(processing) - 1,
        )

    # 2) Сброс «зависших» PROCESSING-кампаний (дольше STUCK_CAMPAIGN_TIMEOUT_MINUTES) → PENDING
    # Это обеспечивает восстановление после краша Celery-воркера с PROCESSING-записью.
    stuck_qs = CampaignQueue.objects.filter(
        status=CampaignQueue.Status.PROCESSING,
        started_at__lt=now - timedelta(minutes=STUCK_CAMPAIGN_TIMEOUT_MINUTES),
    )
    stuck_ids = list(stuck_qs.values_list("campaign_id", flat=True))
    if stuck_ids:
        stuck_qs.update(status=CampaignQueue.Status.PENDING, started_at=None)
        logger.warning(
            "Queue reconcile: reset %d stuck PROCESSING campaigns to PENDING (threshold=%d min): %s",
            len(stuck_ids),
            STUCK_CAMPAIGN_TIMEOUT_MINUTES,
            stuck_ids,
        )

    # 3) Закрываем или отменяем записи очереди по состоянию кампании
    active_queues = (
        CampaignQueue.objects.filter(
            status__in=(CampaignQueue.Status.PENDING, CampaignQueue.Status.PROCESSING)
        )
        .select_related("campaign")
        .annotate(
            pending_count=Count(
                "campaign__recipients",
                filter=Q(campaign__recipients__status=CampaignRecipient.Status.PENDING),
            )
        )
    )
    for q in active_queues.iterator():
        camp = q.campaign
        has_pending = q.pending_count > 0

        if not has_pending:
            with transaction.atomic():
                if camp.status in (Campaign.Status.READY, Campaign.Status.SENDING):
                    camp.status = Campaign.Status.SENT
                    camp.save(update_fields=["status", "updated_at"])
                q.status = CampaignQueue.Status.COMPLETED
                q.completed_at = now
                q.save(update_fields=["status", "completed_at"])
            continue

        if camp.status not in (Campaign.Status.READY, Campaign.Status.SENDING):
            q.status = CampaignQueue.Status.CANCELLED
            q.completed_at = now
            q.save(update_fields=["status", "completed_at"])

    # 4) Гарантируем, что активные кампании с pending-получателями есть в очереди
    missing = (
        Campaign.objects.filter(
            status__in=(Campaign.Status.READY, Campaign.Status.SENDING),
            recipients__status=CampaignRecipient.Status.PENDING,
            queue_entry__isnull=True,
        )
        .distinct()
        .only("id")
    )
    created = 0
    for camp in missing[:500]:
        CampaignQueue.objects.get_or_create(
            campaign=camp,
            defaults={"status": CampaignQueue.Status.PENDING, "priority": 0},
        )
        created += 1

    return {"status": "success", "created_queue": created}
