from __future__ import annotations

import time

from django.core.management.base import BaseCommand
from django.db.models import Q
from django.utils import timezone

from mailer.models import Campaign, CampaignRecipient, MailAccount, GlobalMailAccount, SendLog, Unsubscribe
from mailer.smtp_sender import build_message, send_via_smtp
from mailer.utils import html_to_text


class Command(BaseCommand):
    help = "Фоновый воркер для отправки рассылок (обрабатывает pending получателей)."

    def add_arguments(self, parser):
        parser.add_argument("--sleep", type=float, default=1.0, help="Пауза между итерациями (сек).")
        parser.add_argument("--batch", type=int, default=50, help="Максимум писем за итерацию на кампанию.")
        parser.add_argument("--once", action="store_true", help="Сделать один проход и выйти.")

    def handle(self, *args, **opts):
        sleep_s = float(opts["sleep"])
        batch_size = int(opts["batch"])
        once = bool(opts["once"])

        self.stdout.write(self.style.SUCCESS("mailer_worker started"))
        while True:
            did_work = False
            # берём кампании с pending
            camps = Campaign.objects.filter(recipients__status=CampaignRecipient.Status.PENDING).distinct().order_by("created_at")[:20]
            for camp in camps:
                user = camp.created_by
                if not user:
                    continue
                smtp_cfg = GlobalMailAccount.load()
                if not smtp_cfg.is_enabled:
                    continue

                now = timezone.now()
                sent_last_min = SendLog.objects.filter(provider="smtp_global", status="sent", created_at__gte=now - timezone.timedelta(minutes=1)).count()
                sent_today = SendLog.objects.filter(provider="smtp_global", status="sent", created_at__date=now.date()).count()
                if sent_today >= smtp_cfg.rate_per_day or sent_last_min >= smtp_cfg.rate_per_minute:
                    continue

                allowed = max(1, min(batch_size, smtp_cfg.rate_per_minute - sent_last_min, smtp_cfg.rate_per_day - sent_today))
                batch = list(camp.recipients.filter(status=CampaignRecipient.Status.PENDING)[:allowed])
                if not batch:
                    continue

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

            if once:
                break
            if not did_work:
                time.sleep(sleep_s)

        self.stdout.write(self.style.SUCCESS("mailer_worker stopped"))


