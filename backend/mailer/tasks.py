"""
Celery tasks для модуля mailer.
"""
from __future__ import annotations

import logging
from celery import shared_task
from django.db.models import Q
from django.utils import timezone
from zoneinfo import ZoneInfo

from mailer.models import Campaign, CampaignRecipient, MailAccount, GlobalMailAccount, SendLog, Unsubscribe, SmtpBzQuota, CampaignQueue, UserDailyLimitStatus
from mailer.smtp_sender import build_message, send_via_smtp
from mailer.utils import html_to_text
from mailer.smtp_bz_api import get_quota_info
from mailer.views import PER_USER_DAILY_LIMIT

logger = logging.getLogger(__name__)


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

        # Авто-очистка "зависших" записей очереди:
        # если у кампании больше нет pending-получателей, но она осталась в очереди (pending/processing),
        # помечаем CampaignQueue как completed и (если нужно) кампанию как sent.
        stale_qs = (
            CampaignQueue.objects.filter(status__in=(CampaignQueue.Status.PENDING, CampaignQueue.Status.PROCESSING))
            .exclude(campaign__recipients__status=CampaignRecipient.Status.PENDING)
            .select_related("campaign")
        )
        if stale_qs.exists():
            now = timezone.now()
            for q in stale_qs:
                camp = q.campaign
                if camp and camp.status in (Campaign.Status.READY, Campaign.Status.SENDING):
                    camp.status = Campaign.Status.SENT
                    camp.save(update_fields=["status", "updated_at"])
                q.status = CampaignQueue.Status.COMPLETED
                q.completed_at = now
                q.save(update_fields=["status", "completed_at"])
        
        # Работа с очередью: берем только одну кампанию из очереди за раз
        # Сначала ищем кампанию, которая уже обрабатывается
        processing_queue = CampaignQueue.objects.filter(
            status=CampaignQueue.Status.PROCESSING
        ).select_related("campaign").first()
        
        if processing_queue:
            # Продолжаем обработку текущей кампании,
            # но если pending уже нет (например, письма ушли другим воркером) — закрываем очередь.
            camp = processing_queue.campaign
            if not camp.recipients.filter(status=CampaignRecipient.Status.PENDING).exists():
                # Ставим статус кампании SENT (если была в процессе/готова) и закрываем очередь
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

        if not processing_queue:
            # Берем следующую кампанию из очереди
            next_queue = CampaignQueue.objects.filter(
                status=CampaignQueue.Status.PENDING,
                campaign__status__in=(Campaign.Status.READY, Campaign.Status.SENDING),
                campaign__recipients__status=CampaignRecipient.Status.PENDING
            ).select_related("campaign").order_by("-priority", "queued_at").first()
            
            if next_queue:
                # Помечаем как обрабатываемую
                next_queue.status = CampaignQueue.Status.PROCESSING
                next_queue.started_at = timezone.now()
                next_queue.save(update_fields=["status", "started_at"])
                camps = [next_queue.campaign]
                
                # Отправляем уведомление создателю кампании о начале рассылки
                if next_queue.campaign.created_by:
                    from notifications.service import notify
                    from notifications.models import Notification
                    notify(
                        user=next_queue.campaign.created_by,
                        kind=Notification.Kind.SYSTEM,
                        title="Рассылка началась",
                        body=f"Кампания '{next_queue.campaign.name}' начала отправку писем.",
                        url=f"/mail/campaigns/{next_queue.campaign.id}/"
                    )
            else:
                # Если очереди нет, работаем со старым способом (для обратной совместимости)
                camps = Campaign.objects.filter(
                    recipients__status=CampaignRecipient.Status.PENDING,
                    status__in=(Campaign.Status.READY, Campaign.Status.SENDING)
                ).distinct().order_by("created_at")[:1]
        
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

            # Получаем лимиты из API smtp.bz
            quota = SmtpBzQuota.load()
            
            # Если квота не синхронизирована, используем дефолтные значения
            if quota.last_synced_at and not quota.sync_error:
                # Лимиты из API
                max_per_hour = quota.max_per_hour or 100
                emails_available = quota.emails_available or 0
                emails_limit = quota.emails_limit or 15000
                per_user_daily_limit = 100  # По умолчанию 100 на пользователя
            else:
                # Дефолтные значения, если API не подключено
                max_per_hour = 100
                emails_available = 15000
                emails_limit = 15000
                per_user_daily_limit = 100

            now = timezone.now()
            sent_last_hour = SendLog.objects.filter(
                provider="smtp_global",
                status="sent",
                created_at__gte=now - timezone.timedelta(hours=1)
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
            
            # Отслеживание лимита для уведомлений
            today_date = now.date()
            limit_status, _ = UserDailyLimitStatus.objects.get_or_create(user=user)
            
            # Если лимит достигнут сегодня, сохраняем дату
            if per_user_daily_limit and sent_today_user >= per_user_daily_limit:
                if limit_status.last_limit_reached_date != today_date:
                    limit_status.last_limit_reached_date = today_date
                    limit_status.save(update_fields=["last_limit_reached_date"])
            # Если лимит НЕ достигнут, но ранее был достигнут в другой день - отправляем уведомление
            elif limit_status.last_limit_reached_date and limit_status.last_limit_reached_date < today_date:
                # Лимит снова доступен (новый день) - отправляем уведомление, если еще не отправляли сегодня
                if not limit_status.last_notified_date or limit_status.last_notified_date < today_date:
                    from notifications.service import notify
                    from notifications.models import Notification
                    notify(
                        user=user,
                        kind=Notification.Kind.SYSTEM,
                        title="Лимит отправки обновлен",
                        body=f"Дневной лимит отправки ({per_user_daily_limit} писем) снова доступен. Вы можете продолжить рассылку.",
                        url="/mail/campaigns/"
                    )
                    limit_status.last_notified_date = today_date
                    limit_status.last_limit_reached_date = None  # Сбрасываем, так как лимит снова доступен
                    limit_status.save(update_fields=["last_notified_date", "last_limit_reached_date"])
            
            # Проверка лимитов
            # НЕ ставим на паузу автоматически - просто пропускаем отправку, кампания остается в очереди
            if per_user_daily_limit and sent_today_user >= per_user_daily_limit:
                logger.info(f"Campaign {camp.id} skipped: user daily limit reached ({sent_today_user}/{per_user_daily_limit}), staying in queue")
                continue
            
            # Проверка доступных писем из квоты
            # НЕ ставим на паузу автоматически - просто пропускаем отправку, кампания остается в очереди
            if emails_available <= 0:
                logger.info(f"Campaign {camp.id} skipped: quota exhausted ({emails_available}/{emails_limit}), staying in queue")
                continue
            
            if sent_last_hour >= max_per_hour:
                # Лимит в час достигнут - пропускаем эту итерацию
                continue

            # Вычисляем, сколько писем можно отправить
            allowed = max(1, min(
                batch_size,
                max_per_hour - sent_last_hour,
                emails_available,
                (per_user_daily_limit - sent_today_user) if per_user_daily_limit else batch_size,
            ))
            
            batch = list(camp.recipients.filter(status=CampaignRecipient.Status.PENDING)[:allowed])
            if not batch:
                # Если pending нет — закрываем кампанию и очередь (важно для случаев,
                # когда письма могли быть отправлены не этим воркером).
                if not camp.recipients.filter(status=CampaignRecipient.Status.PENDING).exists():
                    if camp.status in (Campaign.Status.READY, Campaign.Status.SENDING):
                        camp.status = Campaign.Status.SENT
                        camp.save(update_fields=["status", "updated_at"])

                    queue_entry = getattr(camp, "queue_entry", None)
                    if queue_entry and queue_entry.status in (CampaignQueue.Status.PROCESSING, CampaignQueue.Status.PENDING):
                        queue_entry.status = CampaignQueue.Status.COMPLETED
                        queue_entry.completed_at = timezone.now()
                        queue_entry.save(update_fields=["status", "completed_at"])
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
                    # Получаем понятное сообщение об ошибке
                    error_message = str(ex)
                    if hasattr(ex, 'original_error'):
                        # Если ошибка была отформатирована, используем форматированное сообщение
                        error_message = str(ex)
                        original = ex.original_error
                        logger.error(f"Failed to send email {r.email}: {original}", exc_info=True)
                    else:
                        logger.error(f"Failed to send email {r.email}: {ex}", exc_info=True)
                    
                    # Пытаемся получить детальную информацию через API smtp.bz, если доступно
                    detailed_error = error_message
                    if smtp_cfg.smtp_bz_api_key:
                        try:
                            from mailer.smtp_bz_api import get_message_info, get_message_logs
                            from django.utils import timezone as _tz
                            import time
                            
                            # Небольшая задержка, чтобы письмо успело обработаться на стороне smtp.bz
                            time.sleep(2)
                            
                            # Сначала пробуем найти по Message-ID
                            message_id = str(msg.get("Message-ID", "")).strip("<>")
                            msg_info = None
                            
                            if message_id:
                                msg_info = get_message_info(smtp_cfg.smtp_bz_api_key, message_id)
                            
                            # Если не нашли по Message-ID, пробуем найти по email получателя и дате
                            if not msg_info:
                                # Извлекаем чистый email из заголовков (может быть в формате "Name <email@example.com>")
                                to_header = msg.get("To", "")
                                from_header = msg.get("From", "")
                                
                                # Парсим email из заголовка
                                import re
                                email_pattern = r'[\w\.-]+@[\w\.-]+\.\w+'
                                to_email_match = re.search(email_pattern, to_header)
                                from_email_match = re.search(email_pattern, from_header)
                                
                                to_email = to_email_match.group(0) if to_email_match else ""
                                from_email = from_email_match.group(0) if from_email_match else ""
                                
                                # Ищем письма за последние 5 минут
                                start_date = (_tz.now() - _tz.timedelta(minutes=5)).strftime("%Y-%m-%d")
                                end_date = _tz.now().strftime("%Y-%m-%d")
                                
                                logs = get_message_logs(
                                    smtp_cfg.smtp_bz_api_key,
                                    to_email=to_email,
                                    from_email=from_email,
                                    status="bounce",  # Ищем только отскоки
                                    limit=10,
                                    start_date=start_date,
                                    end_date=end_date,
                                )
                                
                                if logs:
                                    # Берем первое найденное письмо
                                    messages = logs.get("data", []) if isinstance(logs, dict) else logs if isinstance(logs, list) else []
                                    if messages:
                                        msg_info = messages[0] if isinstance(messages[0], dict) else None
                            
                            if msg_info:
                                status = msg_info.get("status", "")
                                bounce_reason = msg_info.get("bounce_reason") or msg_info.get("error") or msg_info.get("reason") or msg_info.get("bounceReason", "")
                                if status in ("bounce", "return", "cancel") and bounce_reason:
                                    detailed_error = f"{error_message} | Статус: {status}, Причина: {bounce_reason[:150]}"
                                elif status:
                                    detailed_error = f"{error_message} | Статус: {status}"
                        except Exception as api_error:
                            logger.debug(f"Failed to get message info from smtp.bz API: {api_error}")
                    
                    r.status = CampaignRecipient.Status.FAILED
                    r.last_error = detailed_error[:255]
                    r.save(update_fields=["status", "last_error", "updated_at"])
                    SendLog.objects.create(
                        campaign=camp,
                        recipient=r,
                        account=None,
                        provider="smtp_global",
                        status="failed",
                        error=detailed_error[:500]  # В SendLog можно больше символов
                    )

            # Если очередь пустая — помечаем как SENT (если уже было SENDING)
            if not camp.recipients.filter(status=CampaignRecipient.Status.PENDING).exists():
                if camp.status == Campaign.Status.SENDING:
                    camp.status = Campaign.Status.SENT
                    camp.save(update_fields=["status", "updated_at"])
                
                # Обновляем статус в очереди
                queue_entry = getattr(camp, "queue_entry", None)
                if queue_entry and queue_entry.status == CampaignQueue.Status.PROCESSING:
                    queue_entry.status = CampaignQueue.Status.COMPLETED
                    queue_entry.completed_at = timezone.now()
                    queue_entry.save(update_fields=["status", "completed_at"])

        if did_work:
            logger.debug(f"Processed emails batch (campaigns: {len(camps)})")
        
        return {"processed": did_work, "campaigns": len(camps)}
        
    except Exception as exc:
        logger.error(f"Error in send_pending_emails task: {exc}", exc_info=True)
        # Повторяем задачу при ошибке
        raise self.retry(exc=exc, countdown=60)


@shared_task(name="mailer.tasks.sync_smtp_bz_quota")
def sync_smtp_bz_quota():
    """
    Синхронизация информации о тарифе и квоте smtp.bz через API.
    Выполняется периодически (например, каждые 30 минут).
    """
    try:
        smtp_cfg = GlobalMailAccount.load()
        if not smtp_cfg.smtp_bz_api_key:
            logger.debug("smtp.bz API key not configured, skipping quota sync")
            return {"status": "skipped", "reason": "no_api_key"}
        
        quota_info = get_quota_info(smtp_cfg.smtp_bz_api_key)
        if not quota_info:
            quota = SmtpBzQuota.load()
            quota.sync_error = "Не удалось получить данные через API. Проверьте правильность API ключа в личном кабинете smtp.bz и убедитесь, что API включен для вашего аккаунта."
            quota.save(update_fields=["sync_error", "updated_at"])
            logger.warning("Failed to fetch smtp.bz quota info - check API key and account settings")
            return {"status": "error", "reason": "api_failed"}
        
        # Обновляем информацию о квоте
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
        
        logger.info(f"smtp.bz quota synced: {quota.emails_available}/{quota.emails_limit} emails available")
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

