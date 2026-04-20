"""
Views для polling прогресса рассылки (глобальный и по кампании).
"""

from __future__ import annotations

import logging

from django.contrib.auth.decorators import login_required
from django.db.models import Count, Q
from django.http import HttpRequest, JsonResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone

from accounts.models import User
from mailer.constants import PER_USER_DAILY_LIMIT_DEFAULT
from mailer.models import Campaign, CampaignQueue, CampaignRecipient, GlobalMailAccount, SendLog
from mailer.utils import msk_day_bounds
from mailer.views._helpers import _can_manage_campaign
from policy.engine import enforce

logger = logging.getLogger(__name__)


@login_required
def mail_progress_poll(request: HttpRequest) -> JsonResponse:
    """
    Лёгкий polling для глобального виджета прогресса рассылки.
    Возвращает активную кампанию пользователя (если есть) и процент.

    ВАЖНО: reason_code и next_run_at берутся из CampaignQueue (единый источник правды).
    """
    user: User = request.user
    enforce(
        user=request.user,
        resource_type="action",
        resource="ui:mail:progress:poll",
        context={"path": request.path, "method": request.method},
    )

    qs = (
        Campaign.objects.filter(created_by=user)
        .order_by("-updated_at")
        .select_related("queue_entry")
    )
    active = qs.filter(status=Campaign.Status.SENDING).first()
    if not active:
        active = qs.filter(
            status=Campaign.Status.READY,
            queue_entry__status__in=[CampaignQueue.Status.PENDING, CampaignQueue.Status.PROCESSING],
        ).first()
    if not active:
        active = qs.filter(status=Campaign.Status.PAUSED).first()
    if not active:
        active = qs.filter(status=Campaign.Status.SENT).first()

    active_campaign = None
    queued_count = 0
    next_campaign_at = None

    processing_queue = (
        CampaignQueue.objects.filter(status=CampaignQueue.Status.PROCESSING)
        .select_related("campaign", "campaign__created_by")
        .first()
    )

    if processing_queue and processing_queue.campaign.created_by == user:
        active_campaign = {
            "id": str(processing_queue.campaign.id),
            "name": processing_queue.campaign.name,
        }

    queued_count = CampaignQueue.objects.filter(
        status=CampaignQueue.Status.PENDING,
        campaign__created_by=user,
        campaign__recipients__status=CampaignRecipient.Status.PENDING,
    ).count()

    next_pending = (
        CampaignQueue.objects.filter(
            status=CampaignQueue.Status.PENDING,
            campaign__created_by=user,
            campaign__recipients__status=CampaignRecipient.Status.PENDING,
            deferred_until__isnull=False,
        )
        .order_by("deferred_until")
        .first()
    )

    if next_pending:
        next_campaign_at = next_pending.deferred_until

    if not active:
        return JsonResponse(
            {
                "ok": True,
                "active": None,
                "active_campaign": active_campaign,
                "queued_count": queued_count,
                "next_campaign_at": next_campaign_at.isoformat() if next_campaign_at else None,
            }
        )

    agg = active.recipients.aggregate(
        pending=Count("id", filter=Q(status=CampaignRecipient.Status.PENDING)),
        sent=Count("id", filter=Q(status=CampaignRecipient.Status.SENT)),
        failed=Count("id", filter=Q(status=CampaignRecipient.Status.FAILED)),
        total=Count("id"),
    )
    pending = int(agg.get("pending") or 0)
    sent = int(agg.get("sent") or 0)
    failed = int(agg.get("failed") or 0)
    total = int(agg.get("total") or 0)
    done = sent + failed
    percent = int(round((done / total) * 100)) if total > 0 else 0

    q = getattr(active, "queue_entry", None)
    queue_status = getattr(q, "status", None) if q else None
    deferred_until = getattr(q, "deferred_until", None) if q else None
    defer_reason = (getattr(q, "defer_reason", None) or "") if q else ""

    reason_code = None
    reason_text = ""

    smtp_cfg = GlobalMailAccount.load()
    if not getattr(smtp_cfg, "is_enabled", True):
        reason_code = "smtp_disabled"
        reason_text = "SMTP отключен администратором"
    elif defer_reason:
        reason_code = defer_reason
        reason_texts = {
            "daily_limit": "Дневной лимит достигнут",
            "quota_exhausted": "Квота smtp.bz исчерпана",
            "outside_hours": "Вне рабочего времени",
            "rate_per_hour": "Лимит в час достигнут",
            "transient_error": "Временная ошибка отправки",
        }
        reason_text = reason_texts.get(defer_reason, defer_reason)

    next_run_at = deferred_until.isoformat() if deferred_until else None

    per_user_daily_limit = smtp_cfg.per_user_daily_limit or PER_USER_DAILY_LIMIT_DEFAULT
    start_day_utc, end_day_utc, _ = msk_day_bounds(timezone.now())
    sent_today_user = SendLog.objects.filter(
        provider="smtp_global",
        status="sent",
        campaign__created_by=user,
        created_at__gte=start_day_utc,
        created_at__lt=end_day_utc,
    ).count()
    limit_reached = bool(per_user_daily_limit and sent_today_user >= per_user_daily_limit)

    return JsonResponse(
        {
            "ok": True,
            "active": {
                "id": str(active.id),
                "name": active.name,
                "status": active.status,
                "pending": pending,
                "sent": sent,
                "failed": failed,
                "total": total,
                "percent": max(0, min(100, percent)),
                "url": f"/mail/campaigns/{active.id}/",
                "reason_code": reason_code,
                "reason_text": reason_text,
                "queue_status": queue_status,
                "deferred_until": deferred_until.isoformat() if deferred_until else None,
                "defer_reason": defer_reason,
                "next_run_at": next_run_at,
                "limit_reached": limit_reached,
            },
            "active_campaign": active_campaign,
            "queued_count": queued_count,
            "next_campaign_at": next_campaign_at.isoformat() if next_campaign_at else None,
        }
    )


@login_required
def campaign_progress_poll(request: HttpRequest, campaign_id) -> JsonResponse:
    """Опрос прогресса одной кампании для live-обновления на странице детали."""
    user: User = request.user
    enforce(
        user=request.user,
        resource_type="action",
        resource="ui:mail:campaigns:detail",
        context={"path": request.path, "method": request.method},
    )
    camp = get_object_or_404(Campaign, id=campaign_id)
    if not _can_manage_campaign(user, camp):
        return JsonResponse({"ok": False, "error": "forbidden"}, status=403)

    agg = camp.recipients.aggregate(
        pending=Count("id", filter=Q(status=CampaignRecipient.Status.PENDING)),
        sent=Count("id", filter=Q(status=CampaignRecipient.Status.SENT)),
        failed=Count("id", filter=Q(status=CampaignRecipient.Status.FAILED)),
        total=Count("id"),
    )
    pending = int(agg.get("pending") or 0)
    sent = int(agg.get("sent") or 0)
    failed = int(agg.get("failed") or 0)
    total = int(agg.get("total") or 0)
    done = sent + failed
    percent = int(round((done / total) * 100)) if total > 0 else 0
    percent = max(0, min(100, percent))

    q = getattr(camp, "queue_entry", None)
    deferred_until = getattr(q, "deferred_until", None) if q else None
    defer_reason = (getattr(q, "defer_reason", None) or "") if q else ""
    reason_texts = {
        "daily_limit": "Дневной лимит достигнут",
        "quota_exhausted": "Квота smtp.bz исчерпана",
        "outside_hours": "Вне рабочего времени",
        "rate_per_hour": "Лимит в час достигнут",
        "transient_error": "Временная ошибка отправки",
    }
    reason_text = reason_texts.get(defer_reason, defer_reason) if defer_reason else ""

    return JsonResponse(
        {
            "ok": True,
            "campaign_id": str(camp.id),
            "status": camp.status,
            "pending": pending,
            "sent": sent,
            "failed": failed,
            "total": total,
            "percent": percent,
            "queue_status": getattr(q, "status", None) if q else None,
            "deferred_until": deferred_until.isoformat() if deferred_until else None,
            "defer_reason": defer_reason,
            "reason_text": reason_text,
        }
    )
