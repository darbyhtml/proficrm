"""
Celery tasks для модуля mailer.
"""
from __future__ import annotations

import logging
import re
from pathlib import Path
from celery import shared_task
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from zoneinfo import ZoneInfo
from django.core.cache import cache

from mailer.models import Campaign, CampaignRecipient, MailAccount, GlobalMailAccount, SendLog, Unsubscribe, SmtpBzQuota, CampaignQueue, UserDailyLimitStatus
from mailer.smtp_sender import build_message, open_smtp_connection, send_via_smtp
from mailer.utils import html_to_text, msk_day_bounds
from mailer.smtp_bz_api import get_quota_info, get_message_info, get_message_logs
from mailer.constants import PER_USER_DAILY_LIMIT_DEFAULT, WORKING_HOURS_START, WORKING_HOURS_END
from mailer.mail_content import apply_signature, append_unsubscribe_footer, build_unsubscribe_url, ensure_unsubscribe_tokens

logger = logging.getLogger(__name__)


def _is_transient_send_error(err: str) -> bool:
    """
    Пытаемся отличить временные/системные ошибки от постоянных (битый ящик и т.п.).
    Временные ошибки НЕ должны превращать весь список получателей в FAILED.
    """
    e = (err or "").strip().lower()
    if not e:
        return False

    # SMTP коды, которые чаще всего означают "временно"
    transient_codes = ("421", "450", "451", "452")
    for code in transient_codes:
        if f"(код {code})" in e or f" код {code}" in e:
            return True

    # Типичные временные признаки (по тексту из format_smtp_error / окружения)
    transient_hints = (
        "service unavailable",
        "try again later",
        "попробуйте позже",
        "таймаут",
        "timed out",
        "timeout",
        "temporary failure",
        "temporarily",
        "временно",
        "too many",
        "rate",
        "connection",
        "соединен",
        "dns",
        "no route to host",
        "network is unreachable",
    )
    return any(h in e for h in transient_hints)


def _smtp_bz_enrich_error(api_key: str, msg, campaign_id: str, recipient_id: str, err: str) -> str:
    """
    Пытаемся обогатить текст ошибки данными из smtp.bz API (log/message, log/message/{id}).
    Делается "best-effort": если API недоступен/ключ неверный — возвращаем исходную ошибку.
    """
    api_key = (api_key or "").strip()
    if not api_key:
        return err

    try:
        message_id = str(msg.get("Message-ID", "") or "").strip().strip("<>").strip()
        tag = f"camp:{campaign_id};rcpt:{recipient_id}"

        info = None
        if message_id:
            info = get_message_info(api_key, message_id)

        if not info:
            # Пробуем найти по X-Tag (самый точный вариант)
            today = timezone.now().strftime("%Y-%m-%d")
            logs = get_message_logs(api_key, tag=tag, limit=1, start_date=today, end_date=today)
            if isinstance(logs, dict):
                data = logs.get("data")
                if isinstance(data, list) and data:
                    info = data[0] if isinstance(data[0], dict) else None
            elif isinstance(logs, list) and logs:
                info = logs[0] if isinstance(logs[0], dict) else None

        if not isinstance(info, dict):
            return err

        status = str(info.get("status") or "").strip()
        reason = (
            info.get("bounce_reason")
            or info.get("bounceReason")
            or info.get("error")
            or info.get("reason")
            or ""
        )
        reason = str(reason or "").strip()

        if status and reason:
            return f"{err} | smtp.bz status={status}, reason={reason[:180]}"
        if status:
            return f"{err} | smtp.bz status={status}"
        return err
    except Exception:
        return err


_SMTP_BZ_TAG_RE = re.compile(r"camp:([0-9a-fA-F-]{32,36});rcpt:([0-9a-fA-F-]{32,36})")


def _smtp_bz_extract_tag(row: dict) -> str:
    """
    В разных реализациях API/проксей поле tag может называться по-разному.
    Документация говорит о query-параметре `tag` (идентификатор X-Tag).
    """
    if not isinstance(row, dict):
        return ""
    tag = (
        row.get("tag")
        or row.get("x_tag")
        or row.get("xTag")
        or row.get("X-Tag")
        or row.get("X_Tag")
        or ""
    )
    return str(tag or "").strip()


def _smtp_bz_parse_campaign_recipient_from_tag(tag: str) -> tuple[str | None, str | None]:
    tag = (tag or "").strip()
    m = _SMTP_BZ_TAG_RE.search(tag)
    if not m:
        return None, None
    return m.group(1), m.group(2)


@shared_task(name="mailer.tasks.sync_smtp_bz_delivery_events")
def sync_smtp_bz_delivery_events():
    """
    Безопасная синхронизация "пост-фактум" статусов доставки из smtp.bz:
    по документации `GET /log/message` возвращает `status` = sent|resent|return|bounce|cancel.

    Мы используем уникальный X-Tag (camp:{camp_id};rcpt:{recipient_id}) на каждое письмо,
    поэтому можем привязать bounce/return/cancel к конкретному получателю и пометить его FAILED.

    Важно: это best-effort и не ломает отправку; если API недоступен — просто пропускаем.
    """
    smtp_cfg = GlobalMailAccount.load()
    api_key = (smtp_cfg.smtp_bz_api_key or "").strip()
    if not api_key:
        return {"status": "skipped", "reason": "no_api_key"}

    # Берём текущий день (в логах smtp.bz даты идут без времени; захватить "поздние" события можно увеличением окна при нужде)
    today = timezone.now().strftime("%Y-%m-%d")
    statuses = ("bounce", "return", "cancel")

    updated = 0
    seen = 0
    now = timezone.now()
    logs_to_create: list[SendLog] = []

    for st in statuses:
        offset = 0
        limit = 200
        # Ограничиваемся разумным числом страниц за запуск
        for _page in range(0, 10):
            resp = get_message_logs(api_key, status=st, limit=limit, offset=offset, start_date=today, end_date=today)
            if not resp:
                break
            rows = resp.get("data", []) if isinstance(resp, dict) else (resp if isinstance(resp, list) else [])
            if not rows:
                break

            to_update: list[CampaignRecipient] = []
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
                    # Не трогаем явные отписки и НЕ ухудшаем PENDING: события доставки имеют смысл только для уже отправленных.
                    if r.status in (CampaignRecipient.Status.UNSUBSCRIBED, CampaignRecipient.Status.PENDING):
                        continue

                    msg = f"smtp.bz status={st_i}"
                    if reason_i:
                        msg += f", reason={reason_i[:180]}"

                    # Пишем FAILED только если раньше было SENT (переход "отправлено" -> "недоставлено")
                    if r.status == CampaignRecipient.Status.SENT:
                        r.status = CampaignRecipient.Status.FAILED
                        r.last_error = msg[:255]
                        r.updated_at = now
                        to_update.append(r)
                        logs_to_create.append(
                            SendLog(
                                campaign=r.campaign,
                                recipient=r,
                                account=None,
                                provider="smtp_global",
                                status="failed",
                                error=msg[:500],
                            )
                        )
                    # Если уже FAILED — просто обновим last_error, если было пусто/другое
                    elif r.status == CampaignRecipient.Status.FAILED:
                        if (r.last_error or "").strip() != msg[:255]:
                            r.last_error = msg[:255]
                            r.updated_at = now
                            to_update.append(r)

                if to_update:
                    CampaignRecipient.objects.bulk_update(to_update, ["status", "last_error", "updated_at"])
                    updated += len(to_update)

            offset += limit

    if logs_to_create:
        # Создаем логи одним батчем; это не влияет на отправку, но улучшает аудит/UX.
        SendLog.objects.bulk_create(logs_to_create)

    return {"status": "success", "seen": seen, "updated": updated, "logs_created": len(logs_to_create)}

def _get_campaign_attachment_bytes(camp: Campaign) -> tuple[bytes | None, str | None, str | None]:
    """
    Безопасно читает вложение кампании.
    Если файл отсутствует (часто из-за регистрозависимости на Linux), пытается найти его в директории
    по case-insensitive совпадению имени и (если нашёл) обновляет путь в БД.

    Returns:
        (attachment_bytes, attachment_name, error_message)
    """
    if not camp.attachment:
        return None, None, None

    try:
        camp.attachment.open()
        try:
            content = camp.attachment.read()
            # Для отправки важно оригинальное имя (чтобы у получателя файл не "переименовывался").
            original = (getattr(camp, "attachment_original_name", None) or "").strip()
            if original:
                return content, original[:255], None
            name = getattr(camp.attachment, "name", None) or "attachment"
            base = name.split("/")[-1] if "/" in name else name
            return content, base[:255], None
        finally:
            try:
                camp.attachment.close()
            except Exception:
                pass
    except FileNotFoundError:
        pass
    except OSError:
        # может прилететь как OSError: [Errno 2] No such file...
        pass

    # Fallback: пробуем найти файл в той же папке по имени без учёта регистра
    stored_name = (getattr(camp.attachment, "name", None) or "").strip()
    if not stored_name:
        return None, None, "Файл вложения не найден (пустой путь в БД)."

    storage_location = getattr(camp.attachment.storage, "location", None)
    if not storage_location:
        return None, None, f"Файл вложения не найден: {stored_name}"

    stored_path = Path(str(storage_location)) / stored_name
    parent = stored_path.parent
    target_name = stored_path.name
    if not parent.exists() or not parent.is_dir():
        return None, None, f"Файл вложения не найден: {stored_name}"

    target_cf = target_name.casefold()
    matches = [p for p in parent.iterdir() if p.is_file() and p.name.casefold() == target_cf]
    if len(matches) != 1:
        return None, None, f"Файл вложения не найден: {stored_name}"

    found = matches[0]
    try:
        content = found.read_bytes()
    except Exception:
        return None, None, f"Файл вложения не найден: {stored_name}"

    # Обновляем путь в БД на реально существующий (исправляет проблемы регистра)
    try:
        rel = found.relative_to(Path(str(storage_location)))
        camp.attachment.name = str(rel).replace("\\", "/")
        camp.save(update_fields=["attachment", "updated_at"])
    except Exception:
        # не критично, главное что вложение прочитали
        pass

    original = (getattr(camp, "attachment_original_name", None) or "").strip()
    if original:
        return content, original[:255], None
    return content, found.name[:255], None


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
    return WORKING_HOURS_START <= current_hour < WORKING_HOURS_END


@shared_task(name="mailer.tasks.send_pending_emails", bind=True, max_retries=3)
def send_pending_emails(self, batch_size: int = 50):
    """
    Отправка писем из очереди (заменяет mailer_worker management command).
    
    Args:
        batch_size: Максимум писем за итерацию на кампанию
    """
    # Глобальный lock: не допускаем параллельных запусков send_pending_emails,
    # иначе возможны гонки по очереди и дубль-отправки.
    lock_key = "mailer:send_pending_emails:lock"
    lock_val = str(timezone.now().timestamp())
    # timeout с запасом: отправка батча + сетевые таймауты SMTP
    if not cache.add(lock_key, lock_val, timeout=600):
        return {"processed": False, "campaigns": 0, "reason": "locked"}

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
            # Вне рабочего времени не начинаем обработку очереди (и не шлём уведомления).
            if not _is_working_hours():
                logger.debug("Outside working hours (9:00-18:00 MSK), skipping email sending")
                CampaignQueue.objects.filter(status=CampaignQueue.Status.PROCESSING).update(
                    status=CampaignQueue.Status.PENDING,
                    started_at=None,
                )

                # UX: единоразовое уведомление пользователям, что рассылка на паузе до 09:00 МСК.
                # Не спамим: троттлим через cache и доп. дедупликацию в notify().
                try:
                    msk_now = timezone.now().astimezone(ZoneInfo("Europe/Moscow"))
                    next_start = msk_now.replace(hour=WORKING_HOURS_START, minute=0, second=0, microsecond=0)
                    if msk_now.hour >= WORKING_HOURS_END:
                        next_start = next_start + timezone.timedelta(days=1)
                    if msk_now.hour < WORKING_HOURS_START:
                        # сегодня в 09:00
                        pass

                    throttle_key = f"mailer:notify:outside_hours:{msk_now.date().isoformat()}"
                    if cache.add(throttle_key, "1", timeout=60 * 60):  # максимум раз в час
                        # Берём кампании в очереди (ограниченно), группируем по создателю
                        qs = (
                            CampaignQueue.objects.filter(
                                status=CampaignQueue.Status.PENDING,
                                campaign__status__in=(Campaign.Status.READY, Campaign.Status.SENDING),
                                campaign__recipients__status=CampaignRecipient.Status.PENDING,
                            )
                            .select_related("campaign", "campaign__created_by")
                            .order_by("-priority", "queued_at")
                        )

                        per_user: dict[int, dict[str, object]] = {}
                        for q in qs[:200]:
                            u = getattr(getattr(q, "campaign", None), "created_by", None)
                            if not u:
                                continue
                            uid = int(u.id)
                            d = per_user.get(uid)
                            if not d:
                                per_user[uid] = {"user": u, "count": 1, "first_name": q.campaign.name, "first_id": q.campaign.id}
                            else:
                                d["count"] = int(d.get("count", 0)) + 1

                        if per_user:
                            from notifications.service import notify
                            from notifications.models import Notification

                            time_str = next_start.strftime("%H:%M")
                            date_str = next_start.strftime("%d.%m")
                            for d in per_user.values():
                                u = d["user"]
                                n = int(d["count"])
                                first_name = str(d.get("first_name") or "—")
                                body = (
                                    f"Сейчас вне рабочего времени. Отправка возобновится в {time_str} МСК ({date_str}). "
                                    f"В очереди: {n} камп."
                                )
                                # Если кампаний мало — покажем название первой
                                if first_name and first_name != "—":
                                    body += f" Первая: «{first_name}»."

                                notify(
                                    user=u,
                                    kind=Notification.Kind.SYSTEM,
                                    title="Рассылка на паузе: вне рабочего времени",
                                    body=body,
                                    url="/mail/campaigns/",
                                    dedupe_seconds=6 * 3600,
                                )
                except Exception:
                    pass

                return {"processed": False, "campaigns": 0, "reason": "outside_working_hours"}

            # Берем следующую кампанию из очереди
            next_queue = None
            # Берём следующую кампанию атомарно: защита от гонок между несколькими celery-процессами.
            with transaction.atomic():
                next_queue = (
                    CampaignQueue.objects.select_for_update(skip_locked=True)
                    .filter(
                        status=CampaignQueue.Status.PENDING,
                        campaign__status__in=(Campaign.Status.READY, Campaign.Status.SENDING),
                        campaign__recipients__status=CampaignRecipient.Status.PENDING,
                    )
                    .select_related("campaign")
                    .order_by("-priority", "queued_at")
                    .first()
                )
                if next_queue:
                    next_queue.status = CampaignQueue.Status.PROCESSING
                    next_queue.started_at = timezone.now()
                    next_queue.save(update_fields=["status", "started_at"])
            
            if next_queue:
                camps = [next_queue.campaign]
            else:
                # Celery-only: отправка возможна только через CampaignQueue
                return {"processed": False, "campaigns": 0, "reason": "no_queue"}
        
        for camp in camps:
            user = camp.created_by
            if not user:
                continue
            
            # Пропускаем кампании на паузе (дополнительная проверка)
            if camp.status == Campaign.Status.PAUSED:
                queue_entry = getattr(camp, "queue_entry", None)
                if queue_entry and queue_entry.status in (CampaignQueue.Status.PROCESSING, CampaignQueue.Status.PENDING):
                    queue_entry.status = CampaignQueue.Status.CANCELLED
                    queue_entry.completed_at = timezone.now()
                    queue_entry.save(update_fields=["status", "completed_at"])
                continue
                
            smtp_cfg = GlobalMailAccount.load()
            if not smtp_cfg.is_enabled:
                # SMTP отключен: не держим кампанию в очереди "в обработке"
                queue_entry = getattr(camp, "queue_entry", None)
                if queue_entry and queue_entry.status in (CampaignQueue.Status.PROCESSING, CampaignQueue.Status.PENDING):
                    queue_entry.status = CampaignQueue.Status.CANCELLED
                    queue_entry.completed_at = timezone.now()
                    queue_entry.save(update_fields=["status", "completed_at"])
                try:
                    camp.status = Campaign.Status.PAUSED
                    camp.save(update_fields=["status", "updated_at"])
                except Exception:
                    pass
                continue

            # Получаем лимиты из API smtp.bz
            quota = SmtpBzQuota.load()
            
            # Если квота не синхронизирована, используем дефолтные значения
            if quota.last_synced_at and not quota.sync_error:
                # Лимиты из API
                max_per_hour = quota.max_per_hour or 100
                emails_available = quota.emails_available or 0
                emails_limit = quota.emails_limit or 15000
            else:
                # Дефолтные значения, если API не подключено
                max_per_hour = 100
                emails_available = 15000
                emails_limit = 15000

            # Лимит писем/день на пользователя — из глобальных настроек (или дефолт)
            per_user_daily_limit = smtp_cfg.per_user_daily_limit or PER_USER_DAILY_LIMIT_DEFAULT

            now = timezone.now()
            start_day_utc, end_day_utc, now_msk = msk_day_bounds(now)
            sent_last_hour = SendLog.objects.filter(
                provider="smtp_global",
                status="sent",
                created_at__gte=now - timezone.timedelta(hours=1)
            ).count()
            sent_today = SendLog.objects.filter(
                provider="smtp_global",
                status="sent",
                created_at__gte=start_day_utc,
                created_at__lt=end_day_utc,
            ).count()

            # Лимит писем/день на пользователя (создателя кампании)
            sent_today_user = SendLog.objects.filter(
                provider="smtp_global",
                status="sent",
                campaign__created_by=user,
                created_at__gte=start_day_utc,
                created_at__lt=end_day_utc,
            ).count()
            
            # Отслеживание лимита для уведомлений
            today_date = now_msk.date()
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
            if per_user_daily_limit and sent_today_user >= per_user_daily_limit:
                logger.info(f"Campaign {camp.id} skipped: user daily limit reached ({sent_today_user}/{per_user_daily_limit}), pausing campaign")
                # Чтобы кампания не блокировала очередь, ставим её на паузу и убираем из очереди.
                try:
                    camp.status = Campaign.Status.PAUSED
                    camp.save(update_fields=["status", "updated_at"])
                except Exception:
                    pass
                queue_entry = getattr(camp, "queue_entry", None)
                if queue_entry and queue_entry.status in (CampaignQueue.Status.PROCESSING, CampaignQueue.Status.PENDING):
                    queue_entry.status = CampaignQueue.Status.CANCELLED
                    queue_entry.completed_at = timezone.now()
                    queue_entry.save(update_fields=["status", "completed_at"])
                try:
                    from notifications.service import notify
                    from notifications.models import Notification

                    notify(
                        user=user,
                        kind=Notification.Kind.SYSTEM,
                        title="Рассылка остановлена: дневной лимит",
                        body=f"Кампания '{camp.name}' поставлена на паузу: достигнут дневной лимит ({sent_today_user}/{per_user_daily_limit}).",
                        url=f"/mail/campaigns/{camp.id}/",
                    )
                except Exception:
                    pass
                continue
            
            # Проверка доступных писем из квоты
            if emails_available <= 0:
                logger.info(f"Campaign {camp.id} skipped: quota exhausted ({emails_available}/{emails_limit}), staying in queue")
                # Глобальная блокировка: не держим PROCESSING, чтобы UI/очередь не выглядели "зависшими".
                CampaignQueue.objects.filter(status=CampaignQueue.Status.PROCESSING).update(
                    status=CampaignQueue.Status.PENDING,
                    started_at=None,
                )
                return {"processed": False, "campaigns": 0, "reason": "quota_exhausted"}
            
            if sent_last_hour >= max_per_hour:
                # Лимит в час достигнут - пропускаем эту итерацию
                CampaignQueue.objects.filter(status=CampaignQueue.Status.PROCESSING).update(
                    status=CampaignQueue.Status.PENDING,
                    started_at=None,
                )
                return {"processed": False, "campaigns": 0, "reason": "rate_per_hour_reached"}

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
                # Уведомляем только когда отправка действительно стартовала (READY -> SENDING)
                try:
                    from notifications.service import notify
                    from notifications.models import Notification

                    notify(
                        user=user,
                        kind=Notification.Kind.SYSTEM,
                        title="Рассылка началась",
                        body=f"Кампания '{camp.name}' начала отправку писем.",
                        url=f"/mail/campaigns/{camp.id}/",
                        dedupe_seconds=3600,
                    )
                except Exception:
                    pass

            # Готовим базовый контент письма один раз на кампанию
            auto_plain = html_to_text(camp.body_html or "")
            base_html, base_text = apply_signature(
                user=user,
                body_html=(camp.body_html or ""),
                body_text=(auto_plain or camp.body_text or ""),
            )

            # Токены отписки для батча (email -> token)
            tokens = ensure_unsubscribe_tokens([r.email for r in batch])

            did_work = True

            # Prefetch отписок одним запросом на батч
            batch_emails_norm = [(r.email or "").strip().lower() for r in batch if (r.email or "").strip()]
            unsub_set = set(
                Unsubscribe.objects.filter(email__in=batch_emails_norm).values_list("email", flat=True)
            )
            unsub_set = {e.strip().lower() for e in unsub_set if (e or "").strip()}

            # MailAccount нужен только как контейнер полей для build_message
            identity, _ = MailAccount.objects.get_or_create(user=user)

            # Вложение читаем один раз на батч
            attachment_bytes = None
            attachment_name = None
            if camp.attachment:
                attachment_bytes, attachment_name, att_err = _get_campaign_attachment_bytes(camp)
                if att_err:
                    # Не даём кампании "упасть" и превратиться в массовые FAILED из-за вложения.
                    # Ставим на паузу и освобождаем очередь, чтобы не блокировать остальные.
                    logger.error(f"Campaign {camp.id}: attachment missing: {att_err}")
                    try:
                        camp.status = Campaign.Status.PAUSED
                        camp.save(update_fields=["status", "updated_at"])
                    except Exception:
                        pass
                    queue_entry = getattr(camp, "queue_entry", None)
                    if queue_entry and queue_entry.status == CampaignQueue.Status.PROCESSING:
                        try:
                            queue_entry.status = CampaignQueue.Status.CANCELLED
                            queue_entry.completed_at = timezone.now()
                            queue_entry.save(update_fields=["status", "completed_at"])
                        except Exception:
                            pass
                    # Уведомляем создателя
                    try:
                        from notifications.service import notify
                        from notifications.models import Notification

                        if camp.created_by:
                            notify(
                                user=camp.created_by,
                                kind=Notification.Kind.SYSTEM,
                                title="Рассылка поставлена на паузу: проблема с вложением",
                                body=f"Кампания '{camp.name}' остановлена: {att_err}. Перезагрузите вложение и нажмите «Продолжить».",
                                url=f"/mail/campaigns/{camp.id}/",
                            )
                    except Exception:
                        pass
                    continue

            # Открываем SMTP соединение один раз на батч
            smtp = open_smtp_connection(smtp_cfg)
            try:
                now_ts = timezone.now()
                recipients_to_update = []
                logs_to_create = []
                transient_blocked = False

                for r in batch:
                    email_norm = (r.email or "").strip().lower()
                    if not email_norm:
                        continue

                    if email_norm in unsub_set:
                        r.status = CampaignRecipient.Status.UNSUBSCRIBED
                        r.updated_at = now_ts
                        recipients_to_update.append(r)
                        continue

                    # Добавляем отписку (уникальную для email)
                    token = tokens.get(email_norm, "")
                    unsub_url = build_unsubscribe_url(token) if token else ""
                    body_html, body_text = append_unsubscribe_footer(
                        body_html=base_html,
                        body_text=base_text,
                        unsubscribe_url=unsub_url,
                    )

                    msg = build_message(
                        account=identity,
                        to_email=r.email,
                        subject=camp.subject,
                        body_text=(body_text or ""),
                        body_html=(body_html or ""),
                        from_email=((smtp_cfg.from_email or "").strip() or (smtp_cfg.smtp_username or "").strip()),
                        from_name=((camp.sender_name or "").strip() or (smtp_cfg.from_name or "CRM ПРОФИ").strip()),
                        reply_to=(user.email or "").strip(),
                        attachment_content=attachment_bytes,
                        attachment_filename=attachment_name,
                    )

                    if unsub_url:
                        msg["List-Unsubscribe"] = f"<{unsub_url}>"
                        msg["List-Unsubscribe-Post"] = "List-Unsubscribe=One-Click"
                    # Deliverability/auto-replies: явные заголовки bulk-рассылки
                    msg["Precedence"] = "bulk"
                    msg["Auto-Submitted"] = "auto-generated"
                    msg["X-Tag"] = f"camp:{camp.id};rcpt:{r.id}"

                    try:
                        send_via_smtp(smtp_cfg, msg, smtp=smtp)
                        r.status = CampaignRecipient.Status.SENT
                        r.last_error = ""
                        r.updated_at = timezone.now()
                        recipients_to_update.append(r)
                        logs_to_create.append(
                            SendLog(
                                campaign=camp,
                                recipient=r,
                                account=None,
                                provider="smtp_global",
                                status="sent",
                                message_id=str(msg["Message-ID"]),
                            )
                        )
                    except Exception as ex:
                        err = str(ex)
                        if hasattr(ex, "original_error"):
                            logger.error(f"Failed to send email {r.email}: {ex.original_error}", exc_info=True)
                        else:
                            logger.error(f"Failed to send email {r.email}: {ex}", exc_info=True)

                        # Если ошибка похожа на временную/системную — не превращаем всю кампанию в FAILED.
                        # Ставим текущего получателя обратно в PENDING и выходим из батча (ретрай позже).
                        if _is_transient_send_error(err):
                            r.status = CampaignRecipient.Status.PENDING
                            r.last_error = (err or "Временная ошибка отправки")[:255]
                            r.updated_at = timezone.now()
                            recipients_to_update.append(r)
                            logs_to_create.append(
                                SendLog(
                                    campaign=camp,
                                    recipient=r,
                                    account=None,
                                    provider="smtp_global",
                                    status="failed",
                                    error=(err or "Временная ошибка отправки")[:500],
                                )
                            )
                            transient_blocked = True
                            break

                        # Не временная ошибка — попробуем уточнить её через smtp.bz API (если ключ задан)
                        if smtp_cfg.smtp_bz_api_key:
                            err = _smtp_bz_enrich_error(
                                api_key=smtp_cfg.smtp_bz_api_key,
                                msg=msg,
                                campaign_id=str(camp.id),
                                recipient_id=str(r.id),
                                err=err,
                            )

                        # Постоянная ошибка — помечаем получателя как FAILED
                        r.status = CampaignRecipient.Status.FAILED
                        r.last_error = (err or "Ошибка отправки")[:255]
                        r.updated_at = timezone.now()
                        recipients_to_update.append(r)
                        logs_to_create.append(
                            SendLog(
                                campaign=camp,
                                recipient=r,
                                account=None,
                                provider="smtp_global",
                                status="failed",
                                error=(err or "Ошибка отправки")[:500],
                            )
                        )

                if recipients_to_update:
                    CampaignRecipient.objects.bulk_update(recipients_to_update, ["status", "last_error", "updated_at"])
                if logs_to_create:
                    SendLog.objects.bulk_create(logs_to_create)
            finally:
                try:
                    smtp.quit()
                except Exception:
                    pass

            # Если упёрлись во временную ошибку — освобождаем очередь, чтобы не блокировать другие кампании.
            if transient_blocked:
                queue_entry = getattr(camp, "queue_entry", None)
                if queue_entry and queue_entry.status == CampaignQueue.Status.PROCESSING:
                    queue_entry.status = CampaignQueue.Status.PENDING
                    queue_entry.started_at = None
                    queue_entry.save(update_fields=["status", "started_at"])

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
    finally:
        # снимаем lock
        try:
            cache.delete(lock_key)
        except Exception:
            pass


@shared_task(name="mailer.tasks.reconcile_campaign_queue")
def reconcile_campaign_queue():
    """
    Лёгкая "сверка" очереди и статусов кампаний, чтобы не было зависаний и несостыковок:
    - READY/SENDING + pending recipients -> CampaignQueue должен существовать (PENDING)
    - Queue PENDING/PROCESSING, но pending recipients нет -> COMPLETED + campaign SENT
    - Queue PENDING/PROCESSING, но campaign не READY/SENDING -> CANCELLED
    - Несколько PROCESSING -> оставляем одну, остальные возвращаем в PENDING
    """
    now = timezone.now()

    # 1) Исправляем "несколько processing"
    processing = list(
        CampaignQueue.objects.filter(status=CampaignQueue.Status.PROCESSING)
        .order_by("started_at", "queued_at")
        .values_list("id", flat=True)
    )
    if len(processing) > 1:
        keep_id = processing[0]
        CampaignQueue.objects.filter(id__in=processing[1:]).update(status=CampaignQueue.Status.PENDING, started_at=None)
        logger.warning(f"Queue reconcile: multiple PROCESSING detected, kept {keep_id}, reset {len(processing)-1} to PENDING")

    # 2) Закрываем очереди, где pending уже нет / или кампания не должна быть в очереди
    for q in CampaignQueue.objects.filter(status__in=(CampaignQueue.Status.PENDING, CampaignQueue.Status.PROCESSING)).select_related("campaign").iterator():
        camp = q.campaign
        has_pending = camp.recipients.filter(status=CampaignRecipient.Status.PENDING).exists()

        if not has_pending:
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

    # 3) Гарантируем, что READY/SENDING кампании с pending попадают в очередь (Celery-only)
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
        CampaignQueue.objects.get_or_create(campaign=camp, defaults={"status": CampaignQueue.Status.PENDING, "priority": 0})
        created += 1

    return {"status": "success", "created_queue": created}


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


@shared_task(name="mailer.tasks.sync_smtp_bz_unsubscribes")
def sync_smtp_bz_unsubscribes():
    """
    Фоновая синхронизация отписок из smtp.bz в локальную таблицу Unsubscribe.
    Работает чанками по offset, хранит курсор в кеше.
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
            # закончили проход — начинаем сначала
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
                to_create.append(Unsubscribe(email=email, source="smtp_bz", reason=rsn or "", last_seen_at=now))

        if to_create:
            Unsubscribe.objects.bulk_create(to_create, ignore_conflicts=True)
        if to_update:
            Unsubscribe.objects.bulk_update(to_update, ["source", "reason", "last_seen_at"])

        cache.set(offset_key, offset + limit, timeout=None)
        return {"status": "success", "synced": len(items), "created": len(to_create), "updated": len(to_update), "offset": offset + limit}
    except Exception as e:
        logger.error(f"Error syncing smtp.bz unsubscribes: {e}", exc_info=True)
        return {"status": "error", "error": str(e)}

