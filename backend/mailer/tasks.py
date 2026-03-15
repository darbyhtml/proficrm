"""
Celery tasks для модуля mailer.
Отправка email кампаний, синхронизация квоты и отписок smtp.bz.
"""
from __future__ import annotations

import logging
from datetime import timedelta

from celery import shared_task
from django.conf import settings
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from mailer.constants import (
    BULK_UPDATE_BATCH_SIZE,
    CIRCUIT_BREAKER_THRESHOLD,
    DEFER_REASON_DAILY_LIMIT,
    DEFER_REASON_OUTSIDE_HOURS,
    DEFER_REASON_QUOTA,
    DEFER_REASON_RATE_HOUR,
    DEFER_REASON_TRANSIENT_ERROR,
    MAX_ERROR_MESSAGE_LENGTH,
    SEND_BATCH_SIZE_DEFAULT,
    SEND_TASK_LOCK_TIMEOUT,
    SMTP_BZ_MAX_PER_HOUR_DEFAULT,
    SMTP_BZ_SYNC_MAX_PAGES,
    STUCK_CAMPAIGN_TIMEOUT_MINUTES,
    TRANSIENT_RETRY_DELAY_MINUTES,
    WORKING_HOURS_END,
    WORKING_HOURS_START,
)
from mailer.logging_utils import mask_email
from mailer.models import (
    Campaign,
    CampaignQueue,
    CampaignRecipient,
    GlobalMailAccount,
    SendLog,
    SmtpBzQuota,
    Unsubscribe,
    UserDailyLimitStatus,
)
from mailer.smtp_bz_api import get_message_logs, get_quota_info, get_unsubscribers
from mailer.smtp_sender import build_message, open_smtp_connection, send_via_smtp
from mailer.mail_content import (
    append_unsubscribe_footer,
    apply_signature,
    build_unsubscribe_url,
    ensure_unsubscribe_tokens,
)
from mailer.utils import get_next_send_window_start, msk_day_bounds

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _is_working_hours() -> bool:
    """Проверяет, находимся ли мы в рабочих часах (МСК 9–18)."""
    from zoneinfo import ZoneInfo
    now_msk = timezone.now().astimezone(ZoneInfo("Europe/Moscow"))
    return WORKING_HOURS_START <= now_msk.hour < WORKING_HOURS_END


def _get_send_batch_size() -> int:
    return int(getattr(settings, "MAILER_SEND_BATCH_SIZE", SEND_BATCH_SIZE_DEFAULT))


def _get_send_lock_timeout() -> int:
    return int(getattr(settings, "MAILER_SEND_LOCK_TIMEOUT", SEND_TASK_LOCK_TIMEOUT))


def _count_sent_last_hour() -> int:
    """Сколько писем отправлено за последний час через глобальный аккаунт."""
    cutoff = timezone.now() - timedelta(hours=1)
    return SendLog.objects.filter(
        provider="smtp",
        status=SendLog.Status.SENT,
        created_at__gte=cutoff,
    ).count()


def _count_user_sent_today(user_id: int) -> int:
    """Сколько писем отправлено пользователем сегодня."""
    start_utc, end_utc, _ = msk_day_bounds()
    return SendLog.objects.filter(
        status=SendLog.Status.SENT,
        created_at__gte=start_utc,
        created_at__lt=end_utc,
        recipient__campaign__created_by_id=user_id,
    ).count()


def _complete_campaign(queue_entry: CampaignQueue, campaign: Campaign) -> None:
    """Завершает кампанию: обновляет статусы очереди и кампании."""
    CampaignQueue.objects.filter(id=queue_entry.id).update(
        status=CampaignQueue.Status.COMPLETED,
        completed_at=timezone.now(),
    )
    Campaign.objects.filter(id=campaign.id).update(status=Campaign.Status.SENT)
    logger.info("send_pending_emails: кампания завершена (campaign=%s)", campaign.id)


def _handle_transient_error(queue_entry: CampaignQueue, new_count: int) -> None:
    """Откладывает кампанию при временной SMTP ошибке."""
    retry_at = timezone.now() + timedelta(minutes=TRANSIENT_RETRY_DELAY_MINUTES)
    CampaignQueue.objects.filter(id=queue_entry.id).update(
        status=CampaignQueue.Status.PENDING,
        deferred_until=retry_at,
        defer_reason=DEFER_REASON_TRANSIENT_ERROR,
        consecutive_transient_errors=new_count,
    )
    logger.warning(
        "send_pending_emails: transient error #%s, откладываем до %s",
        new_count, retry_at,
    )


def _notify_daily_limit(user) -> None:
    """Уведомляет пользователя о достижении дневного лимита (не чаще раза в день)."""
    try:
        from django.utils.timezone import localdate
        today = localdate()
        status_obj, _ = UserDailyLimitStatus.objects.get_or_create(user=user)
        if status_obj.last_notified_date == today:
            return
        status_obj.last_limit_reached_date = today
        status_obj.last_notified_date = today
        status_obj.save(update_fields=["last_limit_reached_date", "last_notified_date", "updated_at"])
        try:
            from notifications.models import Notification
            Notification.objects.create(
                user=user,
                kind=Notification.Kind.SYSTEM,
                title="Дневной лимит рассылок исчерпан",
                body="Вы достигли дневного лимита отправки писем. Рассылки продолжатся завтра.",
                url="/mail/",
            )
        except Exception as e:
            logger.warning(
                "_notify_daily_limit: не удалось создать уведомление для user=%s: %s",
                user.id, e,
            )
    except Exception as e:
        logger.warning("_notify_daily_limit: ошибка: %s", e)


# ---------------------------------------------------------------------------
# send_pending_emails
# ---------------------------------------------------------------------------

@shared_task(name="mailer.tasks.send_pending_emails", max_retries=0)
def send_pending_emails():
    """
    Обрабатывает очередь email-рассылок. Запускается каждую минуту.
    Distributed lock через Redis предотвращает параллельный запуск.
    За один вызов обрабатывается один батч одной кампании.
    """
    from django.core.cache import cache

    lock_timeout = _get_send_lock_timeout()
    lock_key = "mailer:send_pending_emails:lock"
    acquired = cache.add(lock_key, "1", timeout=lock_timeout)
    if not acquired:
        logger.debug("send_pending_emails: lock занят, пропускаем")
        return {"skipped": "lock_busy"}
    try:
        return _process_campaign_queue()
    finally:
        cache.delete(lock_key)


def _process_campaign_queue() -> dict:
    now = timezone.now()

    queue_entry = (
        CampaignQueue.objects
        .select_related("campaign__created_by")
        .filter(status=CampaignQueue.Status.PENDING)
        .filter(Q(deferred_until__isnull=True) | Q(deferred_until__lte=now))
        .order_by("-priority", "queued_at")
        .first()
    )

    if not queue_entry:
        logger.debug("send_pending_emails: очередь пуста")
        return {"processed": 0, "reason": "queue_empty"}

    campaign = queue_entry.campaign

    # 1. Рабочие часы
    if not _is_working_hours():
        next_window = get_next_send_window_start()
        CampaignQueue.objects.filter(id=queue_entry.id).update(
            deferred_until=next_window,
            defer_reason=DEFER_REASON_OUTSIDE_HOURS,
        )
        logger.info(
            "send_pending_emails: вне рабочего времени, откладываем до %s (campaign=%s)",
            next_window, campaign.id,
        )
        return {"processed": 0, "reason": "outside_hours"}

    # 2. Глобальный аккаунт
    global_account = GlobalMailAccount.load()
    if not global_account.is_enabled:
        logger.warning("send_pending_emails: GlobalMailAccount отключён")
        return {"processed": 0, "reason": "account_disabled"}

    # 3. Квота smtp.bz (проверяем только если данные свежие — < 10 мин)
    quota = SmtpBzQuota.load()
    if quota.last_synced_at:
        age_minutes = (now - quota.last_synced_at).total_seconds() / 60
        if age_minutes < 10 and quota.emails_available < 5:
            next_window = get_next_send_window_start(always_tomorrow=True)
            CampaignQueue.objects.filter(id=queue_entry.id).update(
                deferred_until=next_window,
                defer_reason=DEFER_REASON_QUOTA,
            )
            logger.warning(
                "send_pending_emails: квота smtp.bz исчерпана (available=%s), откладываем (campaign=%s)",
                quota.emails_available, campaign.id,
            )
            return {"processed": 0, "reason": "quota_exhausted"}

    # 4. Лимит в час
    max_per_hour = quota.max_per_hour if quota.last_synced_at else SMTP_BZ_MAX_PER_HOUR_DEFAULT
    sent_last_hour = _count_sent_last_hour()
    if sent_last_hour >= max_per_hour:
        CampaignQueue.objects.filter(id=queue_entry.id).update(
            deferred_until=now + timedelta(minutes=10),
            defer_reason=DEFER_REASON_RATE_HOUR,
        )
        logger.info(
            "send_pending_emails: лимит в час (%s/%s), откладываем на 10 мин (campaign=%s)",
            sent_last_hour, max_per_hour, campaign.id,
        )
        return {"processed": 0, "reason": "rate_per_hour"}

    # 5. Дневной лимит пользователя
    user = campaign.created_by
    per_user_limit = global_account.per_user_daily_limit
    if user and per_user_limit > 0:
        user_sent_today = _count_user_sent_today(user.id)
        if user_sent_today >= per_user_limit:
            next_window = get_next_send_window_start(always_tomorrow=True)
            CampaignQueue.objects.filter(id=queue_entry.id).update(
                deferred_until=next_window,
                defer_reason=DEFER_REASON_DAILY_LIMIT,
            )
            _notify_daily_limit(user)
            logger.info(
                "send_pending_emails: дневной лимит user=%s (%s/%s), откладываем (campaign=%s)",
                user.id, user_sent_today, per_user_limit, campaign.id,
            )
            return {"processed": 0, "reason": "daily_limit"}

    # 6. Атомарно переводим в PROCESSING (защита от race condition)
    with transaction.atomic():
        refreshed = (
            CampaignQueue.objects
            .select_for_update()
            .filter(id=queue_entry.id, status=CampaignQueue.Status.PENDING)
            .first()
        )
        if not refreshed:
            return {"processed": 0, "reason": "race_condition"}
        CampaignQueue.objects.filter(id=queue_entry.id).update(
            status=CampaignQueue.Status.PROCESSING,
            started_at=now,
            deferred_until=None,
            defer_reason="",
        )

    Campaign.objects.filter(id=campaign.id).update(status=Campaign.Status.SENDING)

    # 7. Батч получателей
    batch_size = _get_send_batch_size()
    recipients = list(
        CampaignRecipient.objects.filter(
            campaign=campaign,
            status=CampaignRecipient.Status.PENDING,
        ).order_by("created_at")[:batch_size]
    )

    if not recipients:
        _complete_campaign(queue_entry, campaign)
        return {"processed": 0, "reason": "all_sent"}

    # 8. Отписавшиеся
    recipient_emails = [r.email for r in recipients]
    unsubscribed_set = set(
        Unsubscribe.objects.filter(email__in=recipient_emails).values_list("email", flat=True)
    )

    # 9. Подготовка контента
    body_html = campaign.body_html or ""
    body_text = campaign.body_text or ""
    if user:
        body_html, body_text = apply_signature(
            user=user, body_html=body_html, body_text=body_text
        )

    active_emails = [r.email for r in recipients if r.email not in unsubscribed_set]
    token_map = ensure_unsubscribe_tokens(active_emails) if active_emails else {}

    # Вложение — читаем один раз для всего батча
    attachment_content: bytes | None = None
    attachment_filename: str | None = None
    if campaign.attachment:
        try:
            campaign.attachment.open("rb")
            attachment_content = campaign.attachment.read()
            campaign.attachment.close()
            attachment_filename = (
                campaign.attachment_original_name or campaign.attachment.name or "attachment"
            )
        except Exception as e:
            logger.warning(
                "send_pending_emails: не удалось прочитать вложение (campaign=%s): %s",
                campaign.id, e,
            )

    # 10. Открываем SMTP соединение
    smtp_conn = None
    try:
        smtp_conn = open_smtp_connection(global_account)
    except Exception as e:
        error_msg = str(e)[:MAX_ERROR_MESSAGE_LENGTH]
        logger.error(
            "send_pending_emails: не удалось открыть SMTP соединение (campaign=%s): %s",
            campaign.id, error_msg,
        )
        new_count = queue_entry.consecutive_transient_errors + 1
        _handle_transient_error(queue_entry, new_count)
        if new_count >= CIRCUIT_BREAKER_THRESHOLD:
            Campaign.objects.filter(id=campaign.id).update(status=Campaign.Status.PAUSED)
            logger.error(
                "send_pending_emails: circuit breaker triggered (%s errors) (campaign=%s)",
                new_count, campaign.id,
            )
        return {"processed": 0, "reason": "smtp_connect_error", "error": error_msg}

    # 11. Отправка батча
    sent_count = 0
    failed_count = 0
    unsubscribed_count = 0
    to_update: list[CampaignRecipient] = []
    to_create_logs: list[SendLog] = []
    consecutive_errors = queue_entry.consecutive_transient_errors
    circuit_breaker_triggered = False

    try:
        for recipient in recipients:
            email = (recipient.email or "").strip().lower()
            if not email:
                recipient.status = CampaignRecipient.Status.FAILED
                recipient.last_error = "Пустой email"
                to_update.append(recipient)
                failed_count += 1
                continue

            if email in unsubscribed_set:
                recipient.status = CampaignRecipient.Status.UNSUBSCRIBED
                to_update.append(recipient)
                unsubscribed_count += 1
                continue

            # Idempotency: не дублировать отправку
            already_sent = SendLog.objects.filter(
                campaign=campaign,
                recipient=recipient,
                status=SendLog.Status.SENT,
            ).exists()
            if already_sent:
                recipient.status = CampaignRecipient.Status.SENT
                to_update.append(recipient)
                continue

            # Персональный unsubscribe footer
            token = token_map.get(email, "")
            unsub_url = build_unsubscribe_url(token) if token else ""
            html_final, text_final = append_unsubscribe_footer(
                body_html=body_html,
                body_text=body_text,
                unsubscribe_url=unsub_url,
            )

            try:
                msg = build_message(
                    account=global_account,
                    to_email=recipient.email,
                    subject=campaign.subject,
                    body_text=text_final,
                    body_html=html_final,
                    from_name=campaign.sender_name or global_account.from_name or "",
                    attachment_content=attachment_content,
                    attachment_filename=attachment_filename,
                )
                send_via_smtp(global_account, msg, smtp=smtp_conn)

                message_id = msg.get("Message-ID", "")
                recipient.status = CampaignRecipient.Status.SENT
                recipient.last_error = ""
                to_update.append(recipient)
                to_create_logs.append(SendLog(
                    campaign=campaign,
                    recipient=recipient,
                    account=None,
                    provider="smtp",
                    message_id=message_id,
                    status=SendLog.Status.SENT,
                ))
                sent_count += 1
                consecutive_errors = 0  # Сбрасываем при успехе

            except Exception as send_err:
                error_msg = str(send_err)[:MAX_ERROR_MESSAGE_LENGTH]
                recipient.status = CampaignRecipient.Status.FAILED
                recipient.last_error = error_msg
                to_update.append(recipient)
                to_create_logs.append(SendLog(
                    campaign=campaign,
                    recipient=recipient,
                    account=None,
                    provider="smtp",
                    message_id="",
                    status=SendLog.Status.FAILED,
                    error=error_msg,
                ))
                failed_count += 1
                consecutive_errors += 1
                logger.warning(
                    "send_pending_emails: ошибка отправки %s (campaign=%s): %s",
                    mask_email(recipient.email), campaign.id, error_msg,
                )
                if consecutive_errors >= CIRCUIT_BREAKER_THRESHOLD:
                    circuit_breaker_triggered = True
                    break
    finally:
        if smtp_conn:
            try:
                smtp_conn.quit()
            except Exception:
                pass

    # 12. Сохраняем результаты батча
    if to_update:
        for i in range(0, len(to_update), BULK_UPDATE_BATCH_SIZE):
            CampaignRecipient.objects.bulk_update(
                to_update[i:i + BULK_UPDATE_BATCH_SIZE],
                ["status", "last_error"],
            )

    if to_create_logs:
        SendLog.objects.bulk_create(to_create_logs, ignore_conflicts=True)

    # 13. Circuit breaker
    if circuit_breaker_triggered:
        _handle_transient_error(queue_entry, consecutive_errors)
        Campaign.objects.filter(id=campaign.id).update(status=Campaign.Status.PAUSED)
        logger.error(
            "send_pending_emails: circuit breaker triggered (%s errors), "
            "кампания поставлена на паузу (campaign=%s)",
            consecutive_errors, campaign.id,
        )
        return {
            "processed": sent_count,
            "failed": failed_count,
            "reason": "circuit_breaker",
            "errors": consecutive_errors,
        }

    # 14. Проверяем есть ли ещё ожидающие получатели
    remaining = CampaignRecipient.objects.filter(
        campaign=campaign,
        status=CampaignRecipient.Status.PENDING,
    ).count()

    if remaining == 0:
        _complete_campaign(queue_entry, campaign)
    else:
        CampaignQueue.objects.filter(id=queue_entry.id).update(
            status=CampaignQueue.Status.PENDING,
            deferred_until=None,
            defer_reason="",
            consecutive_transient_errors=consecutive_errors,
        )

    logger.info(
        "send_pending_emails: батч — sent=%s failed=%s unsub=%s remaining=%s (campaign=%s)",
        sent_count, failed_count, unsubscribed_count, remaining, campaign.id,
    )
    return {
        "processed": sent_count,
        "failed": failed_count,
        "unsubscribed": unsubscribed_count,
        "remaining": remaining,
        "campaign_id": str(campaign.id),
    }


# ---------------------------------------------------------------------------
# sync_smtp_bz_quota
# ---------------------------------------------------------------------------

@shared_task(name="mailer.tasks.sync_smtp_bz_quota", max_retries=0)
def sync_smtp_bz_quota():
    """
    Синхронизирует информацию о тарифе и квоте smtp.bz.
    Запускается каждые 5 минут.
    """
    global_account = GlobalMailAccount.load()
    api_key = global_account.get_api_key()
    quota = SmtpBzQuota.load()

    if not api_key:
        quota.sync_error = "API ключ smtp.bz не задан"
        quota.save(update_fields=["sync_error", "updated_at"])
        logger.debug("sync_smtp_bz_quota: API ключ не задан, пропускаем")
        return {"skipped": "no_api_key"}

    data = get_quota_info(api_key)
    now = timezone.now()

    if data:
        quota.tariff_name = data.get("tariff_name") or ""
        quota.tariff_renewal_date = data.get("tariff_renewal_date")
        quota.emails_available = int(data.get("emails_available") or 0)
        quota.emails_limit = int(data.get("emails_limit") or 0)
        quota.sent_per_hour = int(data.get("sent_per_hour") or 0)
        quota.max_per_hour = int(data.get("max_per_hour") or 100)
        quota.last_synced_at = now
        quota.sync_error = ""
        quota.save(update_fields=[
            "tariff_name", "tariff_renewal_date", "emails_available",
            "emails_limit", "sent_per_hour", "max_per_hour",
            "last_synced_at", "sync_error", "updated_at",
        ])
        logger.info(
            "sync_smtp_bz_quota: OK — available=%s limit=%s max_per_hour=%s",
            quota.emails_available, quota.emails_limit, quota.max_per_hour,
        )
        return {
            "emails_available": quota.emails_available,
            "emails_limit": quota.emails_limit,
            "max_per_hour": quota.max_per_hour,
        }

    quota.sync_error = f"Не удалось получить данные квоты ({now.isoformat()})"
    quota.save(update_fields=["sync_error", "updated_at"])
    logger.warning("sync_smtp_bz_quota: не удалось получить данные от API")
    return {"error": "api_unavailable"}


# ---------------------------------------------------------------------------
# sync_smtp_bz_unsubscribes
# ---------------------------------------------------------------------------

@shared_task(name="mailer.tasks.sync_smtp_bz_unsubscribes", max_retries=0)
def sync_smtp_bz_unsubscribes():
    """
    Синхронизирует список отписок из smtp.bz.
    Запускается каждые 10 минут.
    """
    global_account = GlobalMailAccount.load()
    api_key = global_account.get_api_key()

    if not api_key:
        logger.debug("sync_smtp_bz_unsubscribes: API ключ не задан, пропускаем")
        return {"skipped": "no_api_key"}

    imported = 0
    offset = 0
    limit = 200
    now = timezone.now()

    for _ in range(SMTP_BZ_SYNC_MAX_PAGES):
        data = get_unsubscribers(api_key, limit=limit, offset=offset)
        if not data:
            break

        items = data.get("data") or []
        if not items:
            break

        for item in items:
            email = (item.get("address") or item.get("email") or "").strip().lower()
            if not email or "@" not in email:
                continue
            reason = (item.get("reason") or "").strip().lower()
            Unsubscribe.objects.update_or_create(
                email=email,
                defaults={
                    "source": "smtp_bz",
                    "reason": reason[:24] if reason else "",
                    "last_seen_at": now,
                },
            )
            imported += 1

        if len(items) < limit:
            break
        offset += limit

    logger.info("sync_smtp_bz_unsubscribes: импортировано/обновлено %s отписок", imported)
    return {"imported": imported}


# ---------------------------------------------------------------------------
# sync_smtp_bz_delivery_events
# ---------------------------------------------------------------------------

@shared_task(name="mailer.tasks.sync_smtp_bz_delivery_events", max_retries=0)
def sync_smtp_bz_delivery_events():
    """
    Синхронизирует события доставки из smtp.bz (bounce, return, cancel).
    Обновляет статусы получателей кампаний.
    Запускается каждые 10 минут.
    """
    global_account = GlobalMailAccount.load()
    api_key = global_account.get_api_key()

    if not api_key:
        logger.debug("sync_smtp_bz_delivery_events: API ключ не задан, пропускаем")
        return {"skipped": "no_api_key"}

    # Только проблемные статусы: bounce, return, cancel
    FAILED_STATUSES = ("return", "bounce", "cancel")
    updated = 0

    for smtp_status in FAILED_STATUSES:
        offset = 0
        limit = 100
        for _ in range(SMTP_BZ_SYNC_MAX_PAGES):
            data = get_message_logs(
                api_key, status=smtp_status, limit=limit, offset=offset
            )
            if not data:
                break

            items = data.get("data") or []
            if not items:
                break

            for item in items:
                message_id = (
                    item.get("id") or item.get("message_id") or ""
                ).strip()
                if not message_id:
                    continue

                # Ищем в SendLog по message_id
                send_log = (
                    SendLog.objects
                    .filter(message_id=message_id, status=SendLog.Status.SENT)
                    .select_related("recipient")
                    .first()
                )
                if send_log and send_log.recipient:
                    recip = send_log.recipient
                    if recip.status == CampaignRecipient.Status.SENT:
                        recip.status = CampaignRecipient.Status.FAILED
                        recip.last_error = f"smtp.bz: {smtp_status}"
                        recip.save(update_fields=["status", "last_error", "updated_at"])
                        updated += 1

            if len(items) < limit:
                break
            offset += limit

    logger.info("sync_smtp_bz_delivery_events: обновлено %s получателей", updated)
    return {"updated": updated}


# ---------------------------------------------------------------------------
# reconcile_campaign_queue
# ---------------------------------------------------------------------------

@shared_task(name="mailer.tasks.reconcile_campaign_queue", max_retries=0)
def reconcile_campaign_queue():
    """
    Находит зависшие PROCESSING записи (старше STUCK_CAMPAIGN_TIMEOUT_MINUTES)
    и сбрасывает их обратно в PENDING.
    Запускается каждые 5 минут.
    """
    cutoff = timezone.now() - timedelta(minutes=STUCK_CAMPAIGN_TIMEOUT_MINUTES)

    stuck_qs = CampaignQueue.objects.filter(
        status=CampaignQueue.Status.PROCESSING,
        started_at__lt=cutoff,
    )
    count = stuck_qs.count()
    if count == 0:
        return {"reconciled": 0}

    logger.warning(
        "reconcile_campaign_queue: обнаружено %s зависших записей (> %s мин), сбрасываем в PENDING",
        count, STUCK_CAMPAIGN_TIMEOUT_MINUTES,
    )

    # Сбрасываем зависшие записи
    stuck_ids = list(stuck_qs.values_list("id", flat=True))
    CampaignQueue.objects.filter(id__in=stuck_ids).update(
        status=CampaignQueue.Status.PENDING,
        started_at=None,
    )

    # Синхронизируем статус Campaign.status → PAUSED для зависших
    for entry in CampaignQueue.objects.filter(id__in=stuck_ids).select_related("campaign"):
        if entry.campaign.status == Campaign.Status.SENDING:
            Campaign.objects.filter(id=entry.campaign.id).update(
                status=Campaign.Status.PAUSED
            )

    logger.info("reconcile_campaign_queue: сброшено %s зависших записей", count)
    return {"reconciled": count}
