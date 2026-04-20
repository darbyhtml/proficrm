"""
Views для управления отправкой кампаний: старт, пауза, возобновление, тестовая отправка.
"""

from __future__ import annotations

import logging

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect
from django.utils import timezone

from accounts.models import User
from audit.models import ActivityEvent
from audit.service import log_event
from mailer.constants import PER_USER_DAILY_LIMIT_DEFAULT, DEFER_REASON_DAILY_LIMIT
from mailer.forms import CampaignRecipientAddForm
from mailer.models import Campaign, CampaignQueue, CampaignRecipient, GlobalMailAccount, SendLog
from mailer.throttle import is_user_throttled
from mailer.utils import msk_day_bounds, get_next_send_window_start, html_to_text
from mailer.mail_content import apply_signature
from notifications.service import notify
from notifications.models import Notification
from policy.engine import enforce
from mailer.views._helpers import _can_manage_campaign

logger = logging.getLogger(__name__)


@login_required
def campaign_start(request: HttpRequest, campaign_id) -> HttpResponse:
    """Запуск автоматической рассылки кампании."""
    if request.method != "POST":
        return redirect("campaign_detail", campaign_id=campaign_id)

    user: User = request.user
    enforce(
        user=request.user,
        resource_type="action",
        resource="ui:mail:campaigns:start",
        context={"path": request.path, "method": request.method},
    )
    camp = get_object_or_404(Campaign, id=campaign_id)
    if not _can_manage_campaign(user, camp):
        messages.error(request, "Доступ запрещён.")
        return redirect("campaigns")

    smtp_cfg = GlobalMailAccount.load()
    if not smtp_cfg.is_enabled:
        messages.error(request, "SMTP не настроен администратором (Почта → Настройки).")
        return redirect("campaign_detail", campaign_id=camp.id)

    if not ((camp.created_by.email if camp.created_by else "") or "").strip():
        messages.error(
            request,
            "У создателя кампании не задан email (Reply-To). Укажите email в профиле создателя кампании.",
        )
        return redirect("campaign_detail", campaign_id=camp.id)

    pending_count = camp.recipients.filter(status=CampaignRecipient.Status.PENDING).count()
    if pending_count == 0:
        messages.error(request, "Нет писем в очереди для отправки.")
        return redirect("campaign_detail", campaign_id=camp.id)

    from django.conf import settings

    throttle_limit = getattr(settings, "MAILER_THROTTLE_CAMPAIGN_START_PER_HOUR", 10)
    is_throttled, current_count, throttle_reason = is_user_throttled(
        user.id, "campaign_start", max_requests=throttle_limit, window_seconds=3600
    )
    if is_throttled:
        if throttle_reason == "throttle_backend_unavailable":
            messages.error(request, "Сервис временно недоступен. Попробуйте позже.")
        else:
            messages.error(
                request,
                f"Превышен лимит запуска кампаний ({throttle_limit} запусков в час). "
                f"Текущее количество: {current_count}. Попробуйте позже.",
            )
        return redirect("campaign_detail", campaign_id=camp.id)

    from mailer.constants import get_max_campaign_recipients

    max_recipients = get_max_campaign_recipients()
    total_recipients = camp.recipients.count()
    if total_recipients > max_recipients:
        messages.error(
            request,
            f"Кампания слишком большая ({total_recipients} получателей). "
            f"Максимум: {max_recipients} получателей. "
            f"Разбейте кампанию на несколько частей.",
        )
        return redirect("campaign_detail", campaign_id=camp.id)

    from mailer.services.rate_limiter import get_effective_quota_available

    emails_available = get_effective_quota_available()
    if emails_available <= 0:
        messages.warning(
            request,
            f"Внимание: глобальная квота исчерпана ({emails_available}). "
            f"Кампания будет отложена до пополнения квоты.",
        )

    if camp.status in (Campaign.Status.DRAFT, Campaign.Status.PAUSED, Campaign.Status.STOPPED):
        camp.status = Campaign.Status.READY
        camp.save(update_fields=["status", "updated_at"])

        from mailer.tasks import _is_working_hours

        is_working = _is_working_hours()

        active_queues = (
            CampaignQueue.objects.filter(
                status__in=(CampaignQueue.Status.PENDING, CampaignQueue.Status.PROCESSING),
                campaign__status__in=(Campaign.Status.READY, Campaign.Status.SENDING),
            )
            .exclude(campaign=camp)
            .count()
        )

        queue_entry, created = CampaignQueue.objects.get_or_create(
            campaign=camp, defaults={"status": CampaignQueue.Status.PENDING, "priority": 0}
        )
        if not created and queue_entry.status != CampaignQueue.Status.PENDING:
            queue_entry.status = CampaignQueue.Status.PENDING
            queue_entry.queued_at = timezone.now()
            queue_entry.started_at = None
            queue_entry.completed_at = None
            queue_entry.save(update_fields=["status", "queued_at", "started_at", "completed_at"])

        queue_position = None
        if queue_entry.status == CampaignQueue.Status.PENDING:
            queue_list = list(
                CampaignQueue.objects.filter(
                    status=CampaignQueue.Status.PENDING,
                    campaign__status__in=(Campaign.Status.READY, Campaign.Status.SENDING),
                )
                .order_by("-priority", "queued_at")
                .values_list("campaign_id", flat=True)
            )
            if camp.id in queue_list:
                queue_position = queue_list.index(camp.id) + 1

        if active_queues == 0 and is_working:
            messages.success(request, "Рассылка поставлена в очередь и начнётся в ближайшее время.")
        else:
            if queue_position:
                messages.success(
                    request,
                    f"Рассылка поставлена в очередь. Ваша позиция: {queue_position}. Вы получите уведомление, когда начнется отправка.",
                )
            else:
                messages.success(
                    request,
                    "Рассылка поставлена в очередь. Вы получите уведомление, когда начнется отправка.",
                )
            notify(
                user=user,
                kind=Notification.Kind.SYSTEM,
                title="Рассылка в очереди",
                body=f"Кампания '{camp.name}' поставлена в очередь"
                + (f" (позиция: {queue_position})" if queue_position else "")
                + ". Вы получите уведомление, когда начнется отправка.",
                url=f"/mail/campaigns/{camp.id}/",
                dedupe_seconds=900,
            )

        log_event(
            actor=user,
            verb=ActivityEvent.Verb.UPDATE,
            entity_type="campaign",
            entity_id=camp.id,
            message="Запущена автоматическая рассылка",
        )

    return redirect("campaign_detail", campaign_id=camp.id)


@login_required
def campaign_pause(request: HttpRequest, campaign_id) -> HttpResponse:
    """Постановка кампании на паузу."""
    if request.method != "POST":
        return redirect("campaign_detail", campaign_id=campaign_id)

    user: User = request.user
    enforce(
        user=request.user,
        resource_type="action",
        resource="ui:mail:campaigns:pause",
        context={"path": request.path, "method": request.method},
    )
    camp = get_object_or_404(Campaign, id=campaign_id)
    if not _can_manage_campaign(user, camp):
        messages.error(request, "Доступ запрещён.")
        return redirect("campaigns")

    if camp.status in (Campaign.Status.SENDING, Campaign.Status.READY):
        camp.status = Campaign.Status.PAUSED
        camp.save(update_fields=["status", "updated_at"])

        queue_entry = getattr(camp, "queue_entry", None)
        if queue_entry:
            if queue_entry.status in (
                CampaignQueue.Status.PROCESSING,
                CampaignQueue.Status.PENDING,
            ):
                queue_entry.status = CampaignQueue.Status.CANCELLED
                queue_entry.completed_at = timezone.now()
                queue_entry.save(update_fields=["status", "completed_at"])

        messages.success(
            request, "Рассылка поставлена на паузу. Очередь перешла на следующую кампанию."
        )
        log_event(
            actor=user,
            verb=ActivityEvent.Verb.UPDATE,
            entity_type="campaign",
            entity_id=camp.id,
            message="Рассылка поставлена на паузу вручную",
        )

    return redirect("campaign_detail", campaign_id=camp.id)


@login_required
def campaign_resume(request: HttpRequest, campaign_id) -> HttpResponse:
    """Продолжение рассылки кампании после паузы."""
    if request.method != "POST":
        return redirect("campaign_detail", campaign_id=campaign_id)

    user: User = request.user
    enforce(
        user=request.user,
        resource_type="action",
        resource="ui:mail:campaigns:resume",
        context={"path": request.path, "method": request.method},
    )
    camp = get_object_or_404(Campaign, id=campaign_id)
    if not _can_manage_campaign(user, camp):
        messages.error(request, "Доступ запрещён.")
        return redirect("campaigns")

    if camp.status == Campaign.Status.PAUSED:
        pending_count = camp.recipients.filter(status=CampaignRecipient.Status.PENDING).count()
        if pending_count > 0:
            if not ((camp.created_by.email if camp.created_by else "") or "").strip():
                messages.error(
                    request,
                    "У создателя кампании не задан email (Reply-To). Укажите email в профиле создателя кампании.",
                )
                return redirect("campaign_detail", campaign_id=camp.id)

            smtp_cfg = GlobalMailAccount.load()
            per_user_daily_limit = smtp_cfg.per_user_daily_limit or PER_USER_DAILY_LIMIT_DEFAULT
            start_day_utc, end_day_utc, _ = msk_day_bounds(timezone.now())
            sent_today_user = SendLog.objects.filter(
                provider="smtp_global",
                status="sent",
                campaign__created_by=camp.created_by,
                created_at__gte=start_day_utc,
                created_at__lt=end_day_utc,
            ).count()
            if per_user_daily_limit and sent_today_user >= per_user_daily_limit:
                next_run = get_next_send_window_start(always_tomorrow=True)
                next_run_str = next_run.strftime("%H:%M")
                next_run_date = next_run.strftime("%d.%m")
                queue_entry, _ = CampaignQueue.objects.get_or_create(
                    campaign=camp,
                    defaults={"status": CampaignQueue.Status.PENDING, "priority": 0},
                )
                queue_entry.status = CampaignQueue.Status.PENDING
                queue_entry.started_at = None
                queue_entry.completed_at = None
                queue_entry.queued_at = timezone.now()
                queue_entry.deferred_until = next_run
                queue_entry.defer_reason = DEFER_REASON_DAILY_LIMIT
                queue_entry.save(
                    update_fields=[
                        "status",
                        "started_at",
                        "completed_at",
                        "queued_at",
                        "deferred_until",
                        "defer_reason",
                    ]
                )
                camp.status = Campaign.Status.READY
                camp.save(update_fields=["status", "updated_at"])
                messages.info(
                    request,
                    f"Сегодня лимит исчерпан ({sent_today_user}/{per_user_daily_limit}). "
                    f"Продолжим завтра в {next_run_str} ({next_run_date}).",
                )
                log_event(
                    actor=user,
                    verb=ActivityEvent.Verb.UPDATE,
                    entity_type="campaign",
                    entity_id=camp.id,
                    message="Resume: лимит исчерпан, отложено на завтра",
                )
                return redirect("campaign_detail", campaign_id=camp.id)

            camp.status = Campaign.Status.READY
            camp.save(update_fields=["status", "updated_at"])

            from mailer.tasks import _is_working_hours

            is_working = _is_working_hours()

            active_queues = (
                CampaignQueue.objects.filter(
                    status__in=(CampaignQueue.Status.PENDING, CampaignQueue.Status.PROCESSING),
                    campaign__status__in=(Campaign.Status.READY, Campaign.Status.SENDING),
                )
                .exclude(campaign=camp)
                .count()
            )

            queue_entry, created = CampaignQueue.objects.get_or_create(
                campaign=camp, defaults={"status": CampaignQueue.Status.PENDING, "priority": 0}
            )
            if not created:
                queue_entry.status = CampaignQueue.Status.PENDING
                queue_entry.queued_at = timezone.now()
                queue_entry.started_at = None
                queue_entry.completed_at = None
                queue_entry.deferred_until = None
                queue_entry.defer_reason = ""
                queue_entry.consecutive_transient_errors = 0
                queue_entry.save(
                    update_fields=[
                        "status",
                        "queued_at",
                        "started_at",
                        "completed_at",
                        "deferred_until",
                        "defer_reason",
                        "consecutive_transient_errors",
                    ]
                )

            queue_position = None
            if queue_entry.status == CampaignQueue.Status.PENDING:
                queue_list = list(
                    CampaignQueue.objects.filter(
                        status=CampaignQueue.Status.PENDING,
                        campaign__status__in=(Campaign.Status.READY, Campaign.Status.SENDING),
                    )
                    .order_by("-priority", "queued_at")
                    .values_list("campaign_id", flat=True)
                )
                if camp.id in queue_list:
                    queue_position = queue_list.index(camp.id) + 1

            if active_queues == 0 and is_working:
                messages.success(
                    request,
                    "Рассылка возобновлена и поставлена в очередь. Старт — в ближайшее время.",
                )
            else:
                if queue_position:
                    messages.success(
                        request,
                        f"Рассылка поставлена в очередь. Ваша позиция: {queue_position}. Вы получите уведомление, когда начнется отправка.",
                    )
                else:
                    messages.success(
                        request,
                        "Рассылка поставлена в очередь. Вы получите уведомление, когда начнется отправка.",
                    )
                notify(
                    user=user,
                    kind=Notification.Kind.SYSTEM,
                    title="Рассылка в очереди",
                    body=f"Кампания '{camp.name}' поставлена в очередь"
                    + (f" (позиция: {queue_position})" if queue_position else "")
                    + ". Вы получите уведомление, когда начнется отправка.",
                    url=f"/mail/campaigns/{camp.id}/",
                    dedupe_seconds=900,
                )

            log_event(
                actor=user,
                verb=ActivityEvent.Verb.UPDATE,
                entity_type="campaign",
                entity_id=camp.id,
                message="Рассылка возобновлена",
            )
        else:
            camp.status = Campaign.Status.SENT
            camp.save(update_fields=["status", "updated_at"])
            messages.info(request, "Нет писем в очереди. Кампания завершена.")

    return redirect("campaign_detail", campaign_id=camp.id)


@login_required
def campaign_test_send(request: HttpRequest, campaign_id) -> HttpResponse:
    """
    Отправка тестового письма кампании на себя.

    ВАЖНО: Тестовое письмо НЕ должно менять статус получателей (CampaignRecipient.status).
    """
    user: User = request.user
    enforce(
        user=request.user,
        resource_type="action",
        resource="ui:mail:campaigns:test_send",
        context={"path": request.path, "method": request.method},
    )
    camp = get_object_or_404(Campaign, id=campaign_id)
    if not _can_manage_campaign(user, camp):
        messages.error(request, "Доступ запрещён.")
        return redirect("campaigns")

    smtp_cfg = GlobalMailAccount.load()
    if not smtp_cfg.is_enabled:
        messages.error(request, "SMTP не настроен администратором (Почта → Настройки).")
        return redirect("campaign_detail", campaign_id=camp.id)

    creator = getattr(camp, "created_by", None)
    creator_email = ((creator.email if creator else "") or "").strip()
    if not creator or not creator_email:
        messages.error(
            request,
            "У создателя кампании не задан email (Reply-To) или не найден создатель. Укажите email в профиле создателя и повторите попытку.",
        )
        return redirect("campaign_detail", campaign_id=camp.id)

    to_email = (user.email or "").strip()
    if not to_email:
        messages.error(request, "Некуда отправить тест (не задан email).")
        return redirect("campaign_detail", campaign_id=camp.id)

    from django.conf import settings

    throttle_limit = getattr(settings, "MAILER_THROTTLE_TEST_EMAIL_PER_HOUR", 5)
    is_throttled, current_count, throttle_reason = is_user_throttled(
        user.id, "send_test_email", max_requests=throttle_limit, window_seconds=3600
    )
    if is_throttled:
        if throttle_reason == "throttle_backend_unavailable":
            messages.error(request, "Сервис временно недоступен. Попробуйте позже.")
        else:
            messages.error(
                request,
                f"Превышен лимит отправки тестовых писем ({throttle_limit} писем в час). "
                f"Текущее количество: {current_count}. Попробуйте позже.",
            )
        return redirect("campaign_detail", campaign_id=camp.id)

    # Страховка: сохраняем статусы получателей до отправки
    recipients_before = list(camp.recipients.values_list("id", "status"))

    base_html, base_text = apply_signature(
        user=creator,
        body_html=(camp.body_html or ""),
        body_text=(html_to_text(camp.body_html or "") or camp.body_text or ""),
    )

    from mailer.mail_content import (
        ensure_unsubscribe_tokens,
        build_unsubscribe_url,
        append_unsubscribe_footer,
    )

    token = ensure_unsubscribe_tokens([to_email]).get(to_email.strip().lower(), "")
    unsub_url = build_unsubscribe_url(token) if token else ""
    if unsub_url:
        base_html, base_text = append_unsubscribe_footer(
            body_html=base_html, body_text=base_text, unsubscribe_url=unsub_url
        )

    from mailer.tasks import send_test_email

    attachment_path = None
    attachment_original_name = None
    if camp.attachment:
        attachment_path = camp.attachment.name
        attachment_original_name = (
            camp.attachment_original_name or camp.attachment.name.split("/")[-1]
        )

    result = send_test_email.delay(
        to_email=to_email,
        subject=f"[ТЕСТ] {camp.subject}",
        body_html=base_html,
        body_text=base_text,
        from_email=((smtp_cfg.from_email or "").strip() or (smtp_cfg.smtp_username or "").strip()),
        from_name=((camp.sender_name or "").strip() or (smtp_cfg.from_name or "CRM ПРОФИ").strip()),
        reply_to=creator_email,
        x_tag=f"test:campaign:{camp.id}",
        campaign_id=str(camp.id),
        attachment_path=attachment_path,
        attachment_original_name=attachment_original_name,
    )

    messages.success(
        request,
        f"Тестовое письмо поставлено в очередь (task_id={result.id}). Проверьте {to_email} через несколько секунд.",
    )

    # Доп. страховка: убеждаемся, что этот endpoint не меняет статусы получателей синхронно
    recipients_after = list(camp.recipients.values_list("id", "status"))
    if recipients_before != recipients_after:
        logger.error(
            f"CRITICAL: recipient statuses changed during test enqueue! Campaign: {camp.id}"
        )
        for r_id, old_status in recipients_before:
            CampaignRecipient.objects.filter(id=r_id).update(status=old_status)

    return redirect("campaign_detail", campaign_id=camp.id)
