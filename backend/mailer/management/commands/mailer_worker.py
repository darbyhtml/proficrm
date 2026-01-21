from __future__ import annotations

import logging
import time

from django.core.management.base import BaseCommand
from django.db.models import Q
from django.utils import timezone

from mailer.models import Campaign, CampaignRecipient, MailAccount, GlobalMailAccount, SendLog, Unsubscribe, CampaignQueue
from mailer.smtp_sender import build_message, send_via_smtp
from mailer.utils import html_to_text, msk_day_bounds

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Фоновый воркер для отправки рассылок (обрабатывает pending получателей)."

    def add_arguments(self, parser):
        parser.add_argument("--sleep", type=float, default=1.0, help="Пауза между итерациями (сек).")
        parser.add_argument("--batch", type=int, default=50, help="Максимум писем за итерацию на кампанию.")
        parser.add_argument("--once", action="store_true", help="Сделать один проход и выйти.")
        parser.add_argument(
            "--force",
            action="store_true",
            help="Разрешить запуск устаревшего воркера (НЕ рекомендуется; используйте Celery).",
        )

    def handle(self, *args, **opts):
        # Celery-only: этот воркер оставлен только для диагностики/аварийных случаев.
        # В проде отправку делает mailer.tasks.send_pending_emails.
        if not bool(opts.get("force")):
            self.stdout.write(self.style.ERROR("mailer_worker устарел. Используйте Celery: mailer.tasks.send_pending_emails (celery + beat)."))
            self.stdout.write(self.style.ERROR("Если вы понимаете риск и всё равно хотите запустить: добавьте флаг --force"))
            return

        sleep_s = float(opts["sleep"])
        batch_size = int(opts["batch"])
        once = bool(opts["once"])

        self.stdout.write(self.style.SUCCESS("mailer_worker started"))
        while True:
            did_work = False
            # Рабочее время (9:00-18:00 МСК) — чтобы письма не уходили ночью
            from mailer.tasks import _is_working_hours
            if not _is_working_hours():
                if once:
                    break
                time.sleep(max(5.0, sleep_s))
                continue

            # Если используется очередь CampaignQueue — обрабатываем строго по ней (1 кампания за раз)
            processing_queue = CampaignQueue.objects.filter(
                status=CampaignQueue.Status.PROCESSING
            ).select_related("campaign").first()

            if processing_queue:
                camps = [processing_queue.campaign]
            else:
                next_queue = CampaignQueue.objects.filter(
                    status=CampaignQueue.Status.PENDING,
                    campaign__status__in=(Campaign.Status.READY, Campaign.Status.SENDING),
                    campaign__recipients__status=CampaignRecipient.Status.PENDING,
                ).select_related("campaign").order_by("-priority", "queued_at").first()

                if next_queue:
                    next_queue.status = CampaignQueue.Status.PROCESSING
                    next_queue.started_at = timezone.now()
                    next_queue.save(update_fields=["status", "started_at"])
                    camps = [next_queue.campaign]
                else:
                    # Celery-only: без CampaignQueue не отправляем
                    camps = []

            for camp in camps:
                user = camp.created_by
                if not user:
                    continue
                smtp_cfg = GlobalMailAccount.load()
                if not smtp_cfg.is_enabled:
                    continue

                # Пропускаем кампании на паузе
                if camp.status == Campaign.Status.PAUSED:
                    continue

                now = timezone.now()
                start_day_utc, end_day_utc, _now_msk = msk_day_bounds(now)
                sent_last_min = SendLog.objects.filter(provider="smtp_global", status="sent", created_at__gte=now - timezone.timedelta(minutes=1)).count()
                sent_today = SendLog.objects.filter(provider="smtp_global", status="sent", created_at__gte=start_day_utc, created_at__lt=end_day_utc).count()
                if sent_today >= smtp_cfg.rate_per_day or sent_last_min >= smtp_cfg.rate_per_minute:
                    continue

                allowed = max(1, min(batch_size, smtp_cfg.rate_per_minute - sent_last_min, smtp_cfg.rate_per_day - sent_today))
                batch = list(camp.recipients.filter(status=CampaignRecipient.Status.PENDING)[:allowed])
                if not batch:
                    # Если pending пусто — закрываем кампанию и очередь (могли быть отправлены другим воркером)
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
                    if Unsubscribe.objects.filter(email__iexact=r.email).exists():
                        r.status = CampaignRecipient.Status.UNSUBSCRIBED
                        r.save(update_fields=["status", "updated_at"])
                        continue
                    auto_plain = html_to_text(camp.body_html or "")

                    # Нужен объект MailAccount только как "контейнер" полей для build_message; заголовки задаём явно.
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
                    try:
                        send_via_smtp(smtp_cfg, msg)
                        r.status = CampaignRecipient.Status.SENT
                        r.last_error = ""
                        r.save(update_fields=["status", "last_error", "updated_at"])
                        SendLog.objects.create(campaign=camp, recipient=r, account=None, provider="smtp_global", status="sent", message_id=str(msg["Message-ID"]))
                    except Exception as ex:
                        # Получаем понятное сообщение об ошибке
                        error_message = str(ex)
                        if hasattr(ex, 'original_error'):
                            error_message = str(ex)
                            logger.error(f"Failed to send email {r.email}: {ex.original_error}", exc_info=True)
                        else:
                            logger.error(f"Failed to send email {r.email}: {ex}", exc_info=True)
                        
                        # Пытаемся получить детальную информацию через API smtp.bz, если доступно
                        detailed_error = error_message
                        if smtp_cfg.smtp_bz_api_key:
                            try:
                                from mailer.smtp_bz_api import get_message_info, get_message_logs
                                from django.utils import timezone as _tz
                                import time
                                
                                # Небольшая задержка для обработки на стороне smtp.bz
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
                        SendLog.objects.create(campaign=camp, recipient=r, account=None, provider="smtp_global", status="failed", error=detailed_error[:500])

                # Если все отправлено — закрываем кампанию и очередь
                if not camp.recipients.filter(status=CampaignRecipient.Status.PENDING).exists():
                    if camp.status == Campaign.Status.SENDING:
                        camp.status = Campaign.Status.SENT
                        camp.save(update_fields=["status", "updated_at"])
                    queue_entry = getattr(camp, "queue_entry", None)
                    if queue_entry and queue_entry.status == CampaignQueue.Status.PROCESSING:
                        queue_entry.status = CampaignQueue.Status.COMPLETED
                        queue_entry.completed_at = timezone.now()
                        queue_entry.save(update_fields=["status", "completed_at"])

            if once:
                break
            if not did_work:
                time.sleep(sleep_s)

        self.stdout.write(self.style.SUCCESS("mailer_worker stopped"))


