"""
Celery tasks для модуля mailer.
"""
from __future__ import annotations

import logging
from celery import shared_task
from django.db.models import Q
from django.utils import timezone
from zoneinfo import ZoneInfo

from mailer.models import Campaign, CampaignRecipient, MailAccount, GlobalMailAccount, SendLog, Unsubscribe
from mailer.smtp_sender import build_message, send_via_smtp
from mailer.utils import html_to_text

logger = logging.getLogger(__name__)

from .models import GlobalMailAccount


def _is_working_hours(now=None) -> bool:
    """
    Проверка, находится ли текущее время в рабочем времени (9:00-18:00 МСК).
    
    Args:
        now: datetime для проверки (по умолчанию timezone.now())
    
    Returns:
        True если время в рабочем диапазоне, False иначе
    """
    if now is None:
        now = timezone.now()
    
    # Конвертируем в московское время
    msk_tz = ZoneInfo("Europe/Moscow")
    msk_now = now.astimezone(msk_tz)
    current_hour = msk_now.hour
    
    # Рабочее время: 9:00-18:00 МСК
    return 9 <= current_hour < 18


@shared_task(name="mailer.tasks.send_pending_emails", bind=True, max_retries=3)
def send_pending_emails(self, batch_size: int = 50):
    """
    Отправка писем из очереди (заменяет mailer_worker management command).
    
    Args:
        batch_size: Максимум писем за итерацию на кампанию
    """
    try:
        did_work = False
        # Берём кампании с pending получателями в статусе READY или SENDING (исключаем на паузе и остановленные)
        camps = Campaign.objects.filter(
            recipients__status=CampaignRecipient.Status.PENDING,
            status__in=(Campaign.Status.READY, Campaign.Status.SENDING)
        ).distinct().order_by("created_at")[:20]
        
        # Проверка рабочего времени (9:00-18:00 МСК)
        if not _is_working_hours():
            logger.debug("Outside working hours (9:00-18:00 MSK), skipping email sending")
            # НЕ ставим на паузу автоматически - пользователь может запустить вручную,
            # а автоматическая отправка просто не будет происходить вне рабочего времени
            return {"processed": False, "campaigns": 0, "reason": "outside_working_hours"}
        
        for camp in camps:
            user = camp.created_by
            if not user:
                continue
            
            # Пропускаем кампании на паузе (дополнительная проверка)
            if camp.status == Campaign.Status.PAUSED:
                continue
                
            smtp_cfg = GlobalMailAccount.load()
            if not smtp_cfg.is_enabled:
                continue

            now = timezone.now()
            sent_last_min = SendLog.objects.filter(
                provider="smtp_global",
                status="sent",
                created_at__gte=now - timezone.timedelta(minutes=1)
            ).count()
            sent_today = SendLog.objects.filter(
                provider="smtp_global",
                status="sent",
                created_at__date=now.date()
            ).count()

            # Лимит писем/день на пользователя (создателя кампании)
            sent_today_user = SendLog.objects.filter(
                provider="smtp_global",
                status="sent",
                campaign__created_by=user,
                created_at__date=now.date(),
            ).count()
            per_user_daily_limit = smtp_cfg.per_user_daily_limit or 0
            if per_user_daily_limit and sent_today_user >= per_user_daily_limit:
                # Лимит пользователя достигнут - ставим на паузу
                if camp.status == Campaign.Status.SENDING:
                    camp.status = Campaign.Status.PAUSED
                    camp.save(update_fields=["status", "updated_at"])
                    logger.info(f"Campaign {camp.id} paused: user daily limit reached ({sent_today_user}/{per_user_daily_limit})")
                continue
            
            if sent_today >= smtp_cfg.rate_per_day:
                # Глобальный дневной лимит достигнут - ставим на паузу
                if camp.status == Campaign.Status.SENDING:
                    camp.status = Campaign.Status.PAUSED
                    camp.save(update_fields=["status", "updated_at"])
                    logger.info(f"Campaign {camp.id} paused: global daily limit reached ({sent_today}/{smtp_cfg.rate_per_day})")
                continue
            
            if sent_last_min >= smtp_cfg.rate_per_minute:
                # Лимит в минуту достигнут - пропускаем эту итерацию, но не ставим на паузу
                # (лимит в минуту восстанавливается быстро)
                continue

            allowed = max(1, min(
                batch_size,
                smtp_cfg.rate_per_minute - sent_last_min,
                smtp_cfg.rate_per_day - sent_today,
                (per_user_daily_limit - sent_today_user) if per_user_daily_limit else batch_size,
            ))
            
            batch = list(camp.recipients.filter(status=CampaignRecipient.Status.PENDING)[:allowed])
            if not batch:
                continue

            # Помечаем кампанию как отправляемую (если была READY)
            if camp.status == Campaign.Status.READY:
                camp.status = Campaign.Status.SENDING
                camp.save(update_fields=["status", "updated_at"])

            did_work = True
            for r in batch:
                try:
                    # Проверка на отписку
                    if Unsubscribe.objects.filter(email__iexact=r.email).exists():
                        r.status = CampaignRecipient.Status.UNSUBSCRIBED
                        r.save(update_fields=["status", "updated_at"])
                        continue
                    
                    auto_plain = html_to_text(camp.body_html or "")

                    # Нужен объект MailAccount только как "контейнер" полей для build_message
                    identity, _ = MailAccount.objects.get_or_create(user=user)
                    msg = build_message(
                        account=identity,
                        to_email=r.email,
                        subject=camp.subject,
                        body_text=(auto_plain or camp.body_text or ""),
                        body_html=(camp.body_html or ""),
                        from_email=((smtp_cfg.from_email or "").strip() or (smtp_cfg.smtp_username or "").strip()),
                        from_name=((camp.sender_name or "").strip() or (smtp_cfg.from_name or "CRM ПРОФИ").strip()),
                        reply_to=(user.email or "").strip(),
                        attachment=camp.attachment if camp.attachment else None,
                    )
                    
                    send_via_smtp(smtp_cfg, msg)
                    r.status = CampaignRecipient.Status.SENT
                    r.last_error = ""
                    r.save(update_fields=["status", "last_error", "updated_at"])
                    SendLog.objects.create(
                        campaign=camp,
                        recipient=r,
                        account=None,
                        provider="smtp_global",
                        status="sent",
                        message_id=str(msg["Message-ID"])
                    )
                    logger.info(f"Email sent: {r.email} (campaign: {camp.id})")
                    
                except Exception as ex:
                    logger.error(f"Failed to send email {r.email}: {ex}", exc_info=True)
                    r.status = CampaignRecipient.Status.FAILED
                    r.last_error = str(ex)[:255]
                    r.save(update_fields=["status", "last_error", "updated_at"])
                    SendLog.objects.create(
                        campaign=camp,
                        recipient=r,
                        account=None,
                        provider="smtp_global",
                        status="failed",
                        error=str(ex)
                    )

            # Если очередь пустая — помечаем как SENT (если уже было SENDING)
            if not camp.recipients.filter(status=CampaignRecipient.Status.PENDING).exists():
                if camp.status == Campaign.Status.SENDING:
                    camp.status = Campaign.Status.SENT
                    camp.save(update_fields=["status", "updated_at"])

        if did_work:
            logger.debug(f"Processed emails batch (campaigns: {len(camps)})")
        
        return {"processed": did_work, "campaigns": len(camps)}
        
    except Exception as exc:
        logger.error(f"Error in send_pending_emails task: {exc}", exc_info=True)
        # Повторяем задачу при ошибке
        raise self.retry(exc=exc, countdown=60)

