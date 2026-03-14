"""
Celery-задачи отправки писем: основная очередь и тестовая отправка.
"""
from __future__ import annotations

import logging

from celery import shared_task
from django.core.cache import cache
from django.db import transaction
from django.db.models import Count, Q
from django.utils import timezone
from zoneinfo import ZoneInfo

from mailer.models import (
    Campaign,
    CampaignQueue,
    CampaignRecipient,
    GlobalMailAccount,
    MailAccount,
    SendLog,
    SmtpBzQuota,
    Unsubscribe,
    UserDailyLimitStatus,
)
from mailer.smtp_sender import build_message, send_via_smtp
from mailer.utils import html_to_text, msk_day_bounds, get_next_send_window_start
from mailer.logging_utils import get_pii_log_fields
from mailer.constants import (
    PER_USER_DAILY_LIMIT_DEFAULT,
    WORKING_HOURS_START,
    WORKING_HOURS_END,
    DEFER_REASON_DAILY_LIMIT,
    DEFER_REASON_QUOTA,
    DEFER_REASON_OUTSIDE_HOURS,
    DEFER_REASON_TRANSIENT_ERROR,
    SEND_TASK_LOCK_TIMEOUT,
    SEND_BATCH_SIZE_DEFAULT,
    QUOTA_RECHECK_MINUTES,
    CIRCUIT_BREAKER_THRESHOLD,
    TRANSIENT_RETRY_DELAY_MINUTES,
    SMTP_BZ_MAX_PER_HOUR_DEFAULT,
    SMTP_BZ_EMAILS_LIMIT_DEFAULT,
    MAX_ERROR_MESSAGE_LENGTH,
)
from mailer.mail_content import (
    apply_signature,
    ensure_unsubscribe_tokens,
)
from mailer.services.queue import defer_queue
from mailer.tasks.helpers import (
    _get_campaign_attachment_bytes,
    _is_working_hours,
    _notify_campaign_started,
    _notify_campaign_finished,
    _notify_circuit_breaker_tripped,
    _notify_attachment_error,
    _process_batch_recipients,
    reserve_rate_limit_token,
    get_effective_quota_available,
)

logger = logging.getLogger(__name__)


@shared_task(name="mailer.tasks.send_pending_emails", bind=True, max_retries=3)
def send_pending_emails(self, batch_size: int | None = None):
    """
    Отправка писем из очереди.
    batch_size берётся из settings.MAILER_SEND_BATCH_SIZE (или константы по умолчанию).
    """
    # Определяем размер батча
    if batch_size is None:
        from django.conf import settings as _s
        batch_size = getattr(_s, "MAILER_SEND_BATCH_SIZE", SEND_BATCH_SIZE_DEFAULT)

    # Глобальный Redis-лок — не допускаем параллельных запусков
    lock_key = "mailer:send_pending_emails:lock"
    lock_val = str(timezone.now().timestamp())
    from django.conf import settings as _dj_settings
    _lock_timeout = getattr(_dj_settings, "MAILER_SEND_LOCK_TIMEOUT", SEND_TASK_LOCK_TIMEOUT)
    if not cache.add(lock_key, lock_val, timeout=_lock_timeout):
        return {"processed": False, "campaigns": 0, "reason": "locked"}

    try:
        did_work = False

        # Авто-очистка записей очереди без pending-получателей
        # Используем list() чтобы избежать двойного запроса (exists + iterate).
        stale_list = list(
            CampaignQueue.objects.filter(
                status__in=(CampaignQueue.Status.PENDING, CampaignQueue.Status.PROCESSING)
            )
            .exclude(campaign__recipients__status=CampaignRecipient.Status.PENDING)
            .select_related("campaign")
        )
        if stale_list:
            now = timezone.now()
            for q in stale_list:
                camp = q.campaign
                with transaction.atomic():
                    if camp and camp.status in (Campaign.Status.READY, Campaign.Status.SENDING):
                        camp.status = Campaign.Status.SENT
                        camp.save(update_fields=["status", "updated_at"])
                    q.status = CampaignQueue.Status.COMPLETED
                    q.completed_at = now
                    q.save(update_fields=["status", "completed_at"])

        # --- Выбор кампании из очереди ---
        processing_queue = (
            CampaignQueue.objects.filter(status=CampaignQueue.Status.PROCESSING)
            .select_related("campaign")
            .first()
        )

        if processing_queue:
            now_check = timezone.now()
            if getattr(processing_queue, "deferred_until", None) and processing_queue.deferred_until > now_check:
                processing_queue.status = CampaignQueue.Status.PENDING
                processing_queue.started_at = None
                processing_queue.save(update_fields=["status", "started_at"])
                processing_queue = None
            else:
                camp = processing_queue.campaign
                if not camp.recipients.filter(status=CampaignRecipient.Status.PENDING).exists():
                    with transaction.atomic():
                        if camp.status in (Campaign.Status.READY, Campaign.Status.SENDING):
                            camp.status = Campaign.Status.SENT
                            camp.save(update_fields=["status", "updated_at"])
                        processing_queue.status = CampaignQueue.Status.COMPLETED
                        processing_queue.completed_at = timezone.now()
                        processing_queue.save(update_fields=["status", "completed_at"])
                    processing_queue = None
                    camps = []
                else:
                    camps = [camp]
                    # Вне рабочего времени — откладываем обработку
                    if not _is_working_hours():
                        msk_now = timezone.now().astimezone(ZoneInfo("Europe/Moscow"))
                        next_start = msk_now.replace(hour=WORKING_HOURS_START, minute=0, second=0, microsecond=0)
                        if msk_now.hour >= WORKING_HOURS_END:
                            next_start = (msk_now + timezone.timedelta(days=1)).replace(
                                hour=WORKING_HOURS_START, minute=0, second=0, microsecond=0
                            )
                        defer_queue(processing_queue, DEFER_REASON_OUTSIDE_HOURS, next_start, notify=True)
                        return {"processed": False, "campaigns": 0, "reason": "outside_working_hours"}

        if not processing_queue:
            # Вне рабочего времени — не начинаем новую кампанию
            if not _is_working_hours():
                msk_now = timezone.now().astimezone(ZoneInfo("Europe/Moscow"))
                next_start = msk_now.replace(hour=WORKING_HOURS_START, minute=0, second=0, microsecond=0)
                if msk_now.hour >= WORKING_HOURS_END:
                    next_start = (msk_now + timezone.timedelta(days=1)).replace(
                        hour=WORKING_HOURS_START, minute=0, second=0, microsecond=0
                    )
                pending_qs = CampaignQueue.objects.filter(
                    status__in=(CampaignQueue.Status.PROCESSING, CampaignQueue.Status.PENDING),
                    campaign__recipients__status=CampaignRecipient.Status.PENDING,
                ).select_related("campaign")
                for q in pending_qs:
                    defer_queue(q, DEFER_REASON_OUTSIDE_HOURS, next_start, notify=True)
                return {"processed": False, "campaigns": 0, "reason": "outside_working_hours"}

            # Атомарно берём следующую кампанию
            now_atomic = timezone.now()
            with transaction.atomic():
                next_queue = (
                    CampaignQueue.objects.select_for_update(skip_locked=True)
                    .filter(
                        status=CampaignQueue.Status.PENDING,
                        campaign__status__in=(Campaign.Status.READY, Campaign.Status.SENDING),
                        campaign__recipients__status=CampaignRecipient.Status.PENDING,
                    )
                    .filter(Q(deferred_until__isnull=True) | Q(deferred_until__lte=now_atomic))
                    .filter(Q(campaign__send_at__isnull=True) | Q(campaign__send_at__lte=now_atomic))
                    .select_related("campaign")
                    .order_by("-priority", "queued_at")
                    .first()
                )
                if next_queue:
                    next_queue.status = CampaignQueue.Status.PROCESSING
                    next_queue.started_at = timezone.now()
                    next_queue.deferred_until = None
                    next_queue.defer_reason = ""
                    next_queue.consecutive_transient_errors = 0
                    next_queue.save(
                        update_fields=[
                            "status", "started_at", "deferred_until",
                            "defer_reason", "consecutive_transient_errors",
                        ]
                    )

            if next_queue:
                camps = [next_queue.campaign]
            else:
                return {"processed": False, "campaigns": 0, "reason": "no_queue"}

        # --- Батч-запрос sent_today по пользователям (без N+1) ---
        if camps:
            _now_batch = timezone.now()
            _start_day_utc, _end_day_utc, _ = msk_day_bounds(_now_batch)
            _user_ids = [c.created_by_id for c in camps if c.created_by_id]
            if _user_ids:
                _sent_agg = (
                    SendLog.objects.filter(
                        provider="smtp_global",
                        status=SendLog.Status.SENT,
                        campaign__created_by_id__in=_user_ids,
                        created_at__gte=_start_day_utc,
                        created_at__lt=_end_day_utc,
                    )
                    .values("campaign__created_by_id")
                    .annotate(sent=Count("id"))
                )
                _sent_by_user_id: dict = {row["campaign__created_by_id"]: row["sent"] for row in _sent_agg}
            else:
                _sent_by_user_id = {}
        else:
            _sent_by_user_id = {}

        for camp in camps:
            user = camp.created_by
            if not user:
                continue

            # Пауза — отмена очереди
            if camp.status == Campaign.Status.PAUSED:
                queue_entry = getattr(camp, "queue_entry", None)
                if queue_entry and queue_entry.status in (
                    CampaignQueue.Status.PROCESSING, CampaignQueue.Status.PENDING
                ):
                    queue_entry.status = CampaignQueue.Status.CANCELLED
                    queue_entry.completed_at = timezone.now()
                    queue_entry.save(update_fields=["status", "completed_at"])
                continue

            smtp_cfg = GlobalMailAccount.load()
            if not smtp_cfg.is_enabled:
                queue_entry = getattr(camp, "queue_entry", None)
                if queue_entry and queue_entry.status in (
                    CampaignQueue.Status.PROCESSING, CampaignQueue.Status.PENDING
                ):
                    queue_entry.status = CampaignQueue.Status.CANCELLED
                    queue_entry.completed_at = timezone.now()
                    queue_entry.save(update_fields=["status", "completed_at"])
                try:
                    camp.status = Campaign.Status.PAUSED
                    camp.save(update_fields=["status", "updated_at"])
                except Exception:
                    pass
                continue

            # Лимиты из квоты smtp.bz
            quota = SmtpBzQuota.load()
            if quota.last_synced_at and not quota.sync_error:
                max_per_hour = quota.max_per_hour or SMTP_BZ_MAX_PER_HOUR_DEFAULT
                emails_limit = quota.emails_limit or SMTP_BZ_EMAILS_LIMIT_DEFAULT
            else:
                max_per_hour = SMTP_BZ_MAX_PER_HOUR_DEFAULT
                emails_limit = SMTP_BZ_EMAILS_LIMIT_DEFAULT

            emails_available = get_effective_quota_available()
            per_user_daily_limit = smtp_cfg.per_user_daily_limit or PER_USER_DAILY_LIMIT_DEFAULT

            now = timezone.now()
            start_day_utc, end_day_utc, now_msk = msk_day_bounds(now)
            sent_today_user = _sent_by_user_id.get(user.id, 0)

            # Отслеживание дневного лимита
            today_date = now_msk.date()
            limit_status, _ = UserDailyLimitStatus.objects.get_or_create(user=user)
            if per_user_daily_limit and sent_today_user >= per_user_daily_limit:
                if limit_status.last_limit_reached_date != today_date:
                    limit_status.last_limit_reached_date = today_date
                    limit_status.save(update_fields=["last_limit_reached_date"])
            elif limit_status.last_limit_reached_date and limit_status.last_limit_reached_date < today_date:
                if not limit_status.last_notified_date or limit_status.last_notified_date < today_date:
                    try:
                        from notifications.service import notify
                        from notifications.models import Notification
                        notify(
                            user=user,
                            kind=Notification.Kind.SYSTEM,
                            title="Лимит отправки обновлен",
                            body=f"Дневной лимит ({per_user_daily_limit} писем) снова доступен.",
                            url="/mail/campaigns/",
                        )
                        limit_status.last_notified_date = today_date
                        limit_status.last_limit_reached_date = None
                        limit_status.save(update_fields=["last_notified_date", "last_limit_reached_date"])
                    except Exception:
                        pass

            queue_entry = getattr(camp, "queue_entry", None)
            if not queue_entry:
                logger.warning(f"Campaign {camp.id} has no queue_entry, skipping")
                continue

            # Проверяем дневной лимит
            if per_user_daily_limit and sent_today_user >= per_user_daily_limit:
                next_run = get_next_send_window_start(always_tomorrow=True)
                defer_queue(queue_entry, DEFER_REASON_DAILY_LIMIT, next_run, notify=True)
                continue

            # Проверяем квоту
            if emails_available <= 0:
                from datetime import timedelta
                next_check = timezone.now() + timedelta(minutes=QUOTA_RECHECK_MINUTES)
                if quota.last_synced_at:
                    next_check = quota.last_synced_at + timedelta(minutes=QUOTA_RECHECK_MINUTES)
                defer_queue(queue_entry, DEFER_REASON_QUOTA, next_check, notify=True)
                continue

            # Сколько писем можно отправить в этом батче
            remaining_quota = emails_available
            remaining_daily = (per_user_daily_limit - sent_today_user) if per_user_daily_limit else batch_size
            allowed = max(1, min(batch_size, remaining_quota, remaining_daily))

            with transaction.atomic():
                batch = list(
                    camp.recipients.filter(status=CampaignRecipient.Status.PENDING)
                    .order_by("id")
                    .select_for_update()[:allowed]
                )
            if not batch:
                if not camp.recipients.filter(status=CampaignRecipient.Status.PENDING).exists():
                    with transaction.atomic():
                        if camp.status in (Campaign.Status.READY, Campaign.Status.SENDING):
                            camp.status = Campaign.Status.SENT
                            camp.save(update_fields=["status", "updated_at"])
                        if queue_entry and queue_entry.status in (
                            CampaignQueue.Status.PROCESSING, CampaignQueue.Status.PENDING
                        ):
                            queue_entry.status = CampaignQueue.Status.COMPLETED
                            queue_entry.completed_at = timezone.now()
                            queue_entry.save(update_fields=["status", "completed_at"])
                continue

            # Помечаем кампанию как «отправляется»
            if camp.status == Campaign.Status.READY:
                camp.status = Campaign.Status.SENDING
                camp.save(update_fields=["status", "updated_at"])
                _notify_campaign_started(user, camp)

            # Готовим контент один раз на кампанию
            auto_plain = html_to_text(camp.body_html or "")
            base_html, base_text = apply_signature(
                user=user,
                body_html=(camp.body_html or ""),
                body_text=(auto_plain or camp.body_text or ""),
            )

            # Токены отписки
            tokens = ensure_unsubscribe_tokens([r.email for r in batch])

            did_work = True

            # Prefetch отписок одним запросом
            batch_emails_norm = [(r.email or "").strip().lower() for r in batch if (r.email or "").strip()]
            unsub_set = set(
                Unsubscribe.objects.filter(email__in=batch_emails_norm).values_list("email", flat=True)
            )
            unsub_set = {e.strip().lower() for e in unsub_set if (e or "").strip()}

            # Idempotency: восстанавливаем статус из SendLog при ретраях
            batch_ids = [r.id for r in batch]
            existing_logs = SendLog.objects.filter(
                campaign=camp, recipient_id__in=batch_ids
            ).values("recipient_id", "status")
            confirmed_sent_ids: set = set()
            confirmed_failed_ids: set = set()
            for log in existing_logs:
                if log["status"] == SendLog.Status.SENT:
                    confirmed_sent_ids.add(log["recipient_id"])
                elif log["status"] == SendLog.Status.FAILED and log["recipient_id"] not in confirmed_sent_ids:
                    confirmed_failed_ids.add(log["recipient_id"])

            if confirmed_sent_ids or confirmed_failed_ids:
                recovered = []
                for r in batch:
                    if r.id in confirmed_sent_ids:
                        r.status = CampaignRecipient.Status.SENT
                        r.last_error = ""
                        r.updated_at = timezone.now()
                        recovered.append(r)
                    elif r.id in confirmed_failed_ids:
                        r.status = CampaignRecipient.Status.FAILED
                        r.updated_at = timezone.now()
                        recovered.append(r)
                if recovered:
                    CampaignRecipient.objects.bulk_update(recovered, ["status", "last_error", "updated_at"])
                already_resolved = confirmed_sent_ids | confirmed_failed_ids
                batch = [r for r in batch if r.id not in already_resolved]

            # MailAccount — контейнер полей для build_message
            identity, _ = MailAccount.objects.get_or_create(user=user)

            # Вложение — читаем один раз на батч
            attachment_bytes = None
            attachment_name = None
            if camp.attachment:
                attachment_bytes, attachment_name, att_err = _get_campaign_attachment_bytes(camp)
                if att_err:
                    logger.error(f"Campaign {camp.id}: attachment missing: {att_err}")
                    try:
                        camp.status = Campaign.Status.PAUSED
                        camp.save(update_fields=["status", "updated_at"])
                    except Exception:
                        pass
                    if queue_entry and queue_entry.status == CampaignQueue.Status.PROCESSING:
                        try:
                            queue_entry.status = CampaignQueue.Status.CANCELLED
                            queue_entry.completed_at = timezone.now()
                            queue_entry.save(update_fields=["status", "completed_at"])
                        except Exception:
                            pass
                    _notify_attachment_error(camp, error=att_err)
                    continue

            transient_blocked, rate_limited = _process_batch_recipients(
                batch=batch,
                camp=camp,
                queue_entry=queue_entry,
                smtp_cfg=smtp_cfg,
                max_per_hour=max_per_hour,
                tokens=tokens,
                unsub_set=unsub_set,
                base_html=base_html,
                base_text=base_text,
                attachment_bytes=attachment_bytes,
                attachment_name=attachment_name,
                identity=identity,
                user=user,
            )

            # Circuit breaker при transient-ошибке
            if transient_blocked and not rate_limited:
                if queue_entry and queue_entry.status == CampaignQueue.Status.PROCESSING:
                    from django.conf import settings as _cb_settings
                    threshold = getattr(_cb_settings, "MAILER_CIRCUIT_BREAKER_THRESHOLD", CIRCUIT_BREAKER_THRESHOLD)
                    queue_entry.consecutive_transient_errors = (queue_entry.consecutive_transient_errors or 0) + 1

                    if queue_entry.consecutive_transient_errors >= threshold:
                        logger.error(
                            f"Campaign {camp.id}: circuit breaker tripped "
                            f"({queue_entry.consecutive_transient_errors} errors)"
                        )
                        with transaction.atomic():
                            camp.status = Campaign.Status.PAUSED
                            camp.save(update_fields=["status", "updated_at"])
                            queue_entry.status = CampaignQueue.Status.CANCELLED
                            queue_entry.completed_at = timezone.now()
                            queue_entry.save(
                                update_fields=["status", "completed_at", "consecutive_transient_errors"]
                            )
                        _notify_circuit_breaker_tripped(
                            user, camp,
                            error_count=queue_entry.consecutive_transient_errors,
                        )
                    else:
                        from datetime import timedelta
                        from django.conf import settings as _rt_settings
                        base_delay = getattr(
                            _rt_settings,
                            "MAILER_TRANSIENT_RETRY_DELAY_MINUTES",
                            TRANSIENT_RETRY_DELAY_MINUTES,
                        )
                        errors = queue_entry.consecutive_transient_errors or 1
                        delay_minutes = min(base_delay * (2 ** (errors - 1)), 60)
                        next_retry = timezone.now() + timedelta(minutes=delay_minutes)
                        defer_queue(queue_entry, DEFER_REASON_TRANSIENT_ERROR, next_retry, notify=False)
                        queue_entry.save(update_fields=["consecutive_transient_errors"])

            # Завершение кампании — один агрегатный запрос вместо трёх
            _status_counts = dict(
                camp.recipients.values_list("status").annotate(n=Count("id"))
            )
            if not _status_counts.get(CampaignRecipient.Status.PENDING, 0):
                sent_count = _status_counts.get(CampaignRecipient.Status.SENT, 0)
                failed_count = _status_counts.get(CampaignRecipient.Status.FAILED, 0)
                total_count = sum(_status_counts.values())

                with transaction.atomic():
                    if camp.status in (Campaign.Status.READY, Campaign.Status.SENDING):
                        camp.status = Campaign.Status.SENT
                        camp.save(update_fields=["status", "updated_at"])
                    if queue_entry and queue_entry.status == CampaignQueue.Status.PROCESSING:
                        queue_entry.status = CampaignQueue.Status.COMPLETED
                        queue_entry.completed_at = timezone.now()
                        queue_entry.save(update_fields=["status", "completed_at"])

                logger.info(
                    "Campaign finished",
                    extra={
                        "campaign_id": str(camp.id),
                        "sent": sent_count,
                        "failed": failed_count,
                        "total": total_count,
                    },
                )
                if camp.created_by:
                    _notify_campaign_finished(
                        camp.created_by, camp,
                        sent_count=sent_count,
                        failed_count=failed_count,
                        total_count=total_count,
                    )

        return {"processed": did_work, "campaigns": len(camps)}

    except Exception as exc:
        logger.error(f"Error in send_pending_emails: {exc}", exc_info=True)
        raise self.retry(exc=exc, countdown=60)
    finally:
        try:
            if cache.get(lock_key) == lock_val:
                cache.delete(lock_key)
        except Exception:
            pass


@shared_task(name="mailer.tasks.send_test_email")
def send_test_email(
    to_email: str,
    subject: str,
    body_html: str,
    body_text: str,
    from_email: str = None,
    from_name: str = None,
    reply_to: str = None,
    x_tag: str = None,
    campaign_id: str = None,
    attachment_path: str = None,
    attachment_original_name: str = None,
):
    """Celery-задача для отправки тестового письма."""
    smtp_cfg = GlobalMailAccount.load()
    if not smtp_cfg.is_enabled:
        return {"success": False, "error": "SMTP отключен"}

    quota = SmtpBzQuota.load()
    if quota.last_synced_at and not quota.sync_error:
        max_per_hour = quota.max_per_hour or 100
    else:
        max_per_hour = smtp_cfg.rate_per_minute * 60 if smtp_cfg.rate_per_minute else 100

    token_reserved, token_count, _ = reserve_rate_limit_token(max_per_hour)
    if not token_reserved:
        return {"success": False, "error": f"Лимит отправки достигнут ({token_count}/{max_per_hour})."}

    try:
        temp_account = MailAccount()
        temp_account.from_email = from_email or smtp_cfg.from_email or smtp_cfg.smtp_username
        temp_account.from_name = from_name or smtp_cfg.from_name

        attachment_bytes = None
        attachment_filename = None
        if attachment_path and campaign_id:
            try:
                from mailer.models import Campaign as _Campaign
                camp = _Campaign.objects.get(id=campaign_id)
                attachment_bytes, attachment_filename, att_err = _get_campaign_attachment_bytes(camp)
                if att_err:
                    return {"success": False, "error": f"Ошибка вложения: {att_err}"}
                if attachment_original_name:
                    attachment_filename = attachment_original_name
            except Exception as e:
                return {"success": False, "error": f"Кампания не найдена: {e}"}

        msg = build_message(
            account=temp_account,
            to_email=to_email,
            subject=subject,
            body_text=body_text,
            body_html=body_html,
            from_email=from_email or smtp_cfg.from_email or smtp_cfg.smtp_username,
            from_name=from_name or smtp_cfg.from_name,
            reply_to=reply_to,
            attachment_content=attachment_bytes,
            attachment_filename=attachment_filename,
        )
        if x_tag:
            msg["X-Tag"] = x_tag

        send_via_smtp(smtp_cfg, msg)

        # Записываем SendLog если есть campaign_id
        if campaign_id:
            try:
                from mailer.models import Campaign as _Campaign
                camp_obj = _Campaign.objects.get(id=campaign_id)
                SendLog.objects.create(
                    campaign=camp_obj,
                    recipient=None,
                    account=None,
                    provider="smtp_global",
                    status=SendLog.Status.SENT,
                    message_id=str(msg["Message-ID"]),
                )
            except Exception:
                pass

        logger.info(
            "Test email sent",
            extra={
                **get_pii_log_fields(to_email, log_level=logging.INFO),
                "campaign_id": campaign_id,
            },
        )
        return {"success": True, "message_id": str(msg["Message-ID"])}

    except Exception as e:
        logger.error(
            "Test email failed",
            exc_info=True,
            extra={**get_pii_log_fields(to_email, log_level=logging.ERROR), "campaign_id": campaign_id},
        )
        if campaign_id:
            try:
                from mailer.models import Campaign as _Campaign
                camp_obj = _Campaign.objects.get(id=campaign_id)
                SendLog.objects.create(
                    campaign=camp_obj,
                    recipient=None,
                    account=None,
                    provider="smtp_global",
                    status=SendLog.Status.FAILED,
                    error=str(e)[:MAX_ERROR_MESSAGE_LENGTH],
                )
            except Exception:
                pass
        return {"success": False, "error": str(e)}
