"""
Celery-задачи синхронизации smtp.bz: квота, статусы доставки, отписки.
"""

from __future__ import annotations

import logging

from celery import shared_task
from django.core.cache import cache
from django.db import transaction
from django.utils import timezone

from mailer.models import (
    CampaignRecipient,
    GlobalMailAccount,
    SendLog,
    SmtpBzQuota,
    Unsubscribe,
)
from mailer.constants import SMTP_BZ_SYNC_MAX_PAGES
from mailer.tasks.helpers import _smtp_bz_extract_tag, _smtp_bz_parse_campaign_recipient_from_tag

logger = logging.getLogger(__name__)


@shared_task(name="mailer.tasks.sync_smtp_bz_delivery_events")
def sync_smtp_bz_delivery_events():
    """
    Синхронизация post-factum статусов доставки из smtp.bz:
    bounce/return/cancel → помечаем CampaignRecipient FAILED.
    Использует X-Tag (camp:{id};rcpt:{id}) для привязки к получателю.
    """
    from mailer.smtp_bz_api import get_message_logs

    smtp_cfg = GlobalMailAccount.load()
    api_key = (smtp_cfg.smtp_bz_api_key or "").strip()
    if not api_key:
        return {"status": "skipped", "reason": "no_api_key"}

    today = timezone.now().strftime("%Y-%m-%d")
    statuses = ("bounce", "return", "cancel")

    updated = 0
    seen = 0
    logs_created = 0
    now = timezone.now()

    for st in statuses:
        offset = 0
        limit = 200
        for _page in range(SMTP_BZ_SYNC_MAX_PAGES):
            resp = get_message_logs(
                api_key, status=st, limit=limit, offset=offset, start_date=today, end_date=today
            )
            if not resp:
                break
            rows = (
                resp.get("data", [])
                if isinstance(resp, dict)
                else (resp if isinstance(resp, list) else [])
            )
            if not rows:
                break

            to_update: list[CampaignRecipient] = []
            batch_logs: list[SendLog] = []
            ids: list[str] = []
            meta: dict[str, tuple[str, str]] = {}

            for row in rows:
                if not isinstance(row, dict):
                    continue
                tag = _smtp_bz_extract_tag(row)
                camp_id, rcpt_id = _smtp_bz_parse_campaign_recipient_from_tag(tag)
                if not camp_id or not rcpt_id:
                    continue
                seen += 1
                reason = (
                    row.get("bounce_reason")
                    or row.get("bounceReason")
                    or row.get("error")
                    or row.get("reason")
                    or ""
                )
                reason = str(reason or "").strip()
                meta[str(rcpt_id)] = (st, reason)
                ids.append(str(rcpt_id))

            if ids:
                qs = CampaignRecipient.objects.filter(id__in=ids).select_related("campaign")
                for r in qs:
                    st_i, reason_i = meta.get(str(r.id), (st, ""))
                    if r.status in (
                        CampaignRecipient.Status.UNSUBSCRIBED,
                        CampaignRecipient.Status.PENDING,
                    ):
                        continue

                    msg = f"smtp.bz status={st_i}"
                    if reason_i:
                        msg += f", reason={reason_i[:180]}"

                    if r.status == CampaignRecipient.Status.SENT:
                        r.status = CampaignRecipient.Status.FAILED
                        r.last_error = msg[:500]
                        r.updated_at = now
                        to_update.append(r)
                        batch_logs.append(
                            SendLog(
                                campaign=r.campaign,
                                recipient=r,
                                account=None,
                                provider="smtp_global",
                                status=SendLog.Status.FAILED,
                                error=msg[:500],
                            )
                        )
                    elif r.status == CampaignRecipient.Status.FAILED:
                        if (r.last_error or "").strip() != msg[:500]:
                            r.last_error = msg[:500]
                            r.updated_at = now
                            to_update.append(r)

                if to_update or batch_logs:
                    with transaction.atomic():
                        if to_update:
                            CampaignRecipient.objects.bulk_update(
                                to_update, ["status", "last_error", "updated_at"]
                            )
                        if batch_logs:
                            SendLog.objects.bulk_create(batch_logs)
                    updated += len(to_update)
                    logs_created += len(batch_logs)

            offset += limit

    return {"status": "success", "seen": seen, "updated": updated, "logs_created": logs_created}


@shared_task(name="mailer.tasks.sync_smtp_bz_quota")
def sync_smtp_bz_quota():
    """
    Синхронизация тарифа и квоты smtp.bz через API.
    Запускается периодически (Celery Beat).
    """
    from mailer.smtp_bz_api import get_quota_info

    try:
        smtp_cfg = GlobalMailAccount.load()
        if not smtp_cfg.smtp_bz_api_key:
            logger.debug("smtp.bz API key not configured, skipping quota sync")
            return {"status": "skipped", "reason": "no_api_key"}

        quota_info = get_quota_info(smtp_cfg.smtp_bz_api_key)
        if not quota_info:
            quota = SmtpBzQuota.load()
            quota.sync_error = (
                "Не удалось получить данные через API. "
                "Проверьте правильность API ключа в личном кабинете smtp.bz."
            )
            quota.save(update_fields=["sync_error", "updated_at"])
            logger.warning("Failed to fetch smtp.bz quota info")
            return {"status": "error", "reason": "api_failed"}

        quota = SmtpBzQuota.load()
        quota.tariff_name = quota_info.get("tariff_name", "")
        quota.tariff_renewal_date = quota_info.get("tariff_renewal_date")
        quota.emails_available = quota_info.get("emails_available", 0)
        quota.emails_limit = quota_info.get("emails_limit", 0)
        quota.sent_per_hour = quota_info.get("sent_per_hour", 0)
        quota.max_per_hour = quota_info.get("max_per_hour", 100)
        quota.last_synced_at = timezone.now()
        quota.sync_error = ""
        quota.save()

        logger.info(
            f"smtp.bz quota synced: {quota.emails_available}/{quota.emails_limit} available"
        )
        return {
            "status": "success",
            "emails_available": quota.emails_available,
            "emails_limit": quota.emails_limit,
        }
    except Exception as e:
        logger.error(f"Error syncing smtp.bz quota: {e}", exc_info=True)
        quota = SmtpBzQuota.load()
        quota.sync_error = str(e)
        quota.save(update_fields=["sync_error", "updated_at"])
        return {"status": "error", "error": str(e)}


@shared_task(name="mailer.tasks.sync_smtp_bz_unsubscribes")
def sync_smtp_bz_unsubscribes():
    """
    Фоновая синхронизация отписок из smtp.bz в локальную таблицу Unsubscribe.
    Работает чанками, хранит курсор в кеше.
    """
    smtp_cfg = GlobalMailAccount.load()
    api_key = (smtp_cfg.smtp_bz_api_key or "").strip()
    if not api_key:
        return {"status": "skipped", "reason": "no_api_key"}

    try:
        from mailer.smtp_bz_api import get_unsubscribers

        limit = 500
        offset_key = "smtp_bz:unsub:offset"
        offset = int(cache.get(offset_key) or 0)

        resp = get_unsubscribers(api_key, limit=limit, offset=offset)
        if not resp:
            return {"status": "error", "reason": "api_failed"}

        data = resp.get("data", []) if isinstance(resp, dict) else []
        if not data:
            cache.set(offset_key, 0, timeout=None)
            return {"status": "success", "synced": 0, "reset": True}

        now = timezone.now()
        items = []
        for row in data:
            if not isinstance(row, dict):
                continue
            addr = (row.get("address") or row.get("email") or "").strip().lower()
            if not addr:
                continue
            rsn = (row.get("reason") or "").strip().lower()
            items.append((addr, rsn))

        if not items:
            cache.set(offset_key, offset + limit, timeout=None)
            return {"status": "success", "synced": 0, "offset": offset + limit}

        emails = [e for e, _ in items]
        existing_qs = Unsubscribe.objects.filter(email__in=emails)
        existing_map = {u.email.lower(): u for u in existing_qs}

        to_create = []
        to_update = []
        for email, rsn in items:
            if email in existing_map:
                u = existing_map[email]
                u.source = u.source or "smtp_bz"
                u.reason = rsn or (u.reason or "")
                u.last_seen_at = now
                to_update.append(u)
            else:
                to_create.append(
                    Unsubscribe(email=email, source="smtp_bz", reason=rsn or "", last_seen_at=now)
                )

        if to_create:
            Unsubscribe.objects.bulk_create(to_create, ignore_conflicts=True)
        if to_update:
            Unsubscribe.objects.bulk_update(to_update, ["source", "reason", "last_seen_at"])

        cache.set(offset_key, offset + limit, timeout=None)
        return {
            "status": "success",
            "synced": len(items),
            "created": len(to_create),
            "updated": len(to_update),
            "offset": offset + limit,
        }
    except Exception as e:
        logger.error(f"Error syncing smtp.bz unsubscribes: {e}", exc_info=True)
        return {"status": "error", "error": str(e)}
