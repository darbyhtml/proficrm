"""
Вспомогательные функции для Celery-задач модуля mailer.
Вынесены сюда для удобства тестирования и переиспользования.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

from django.utils import timezone
from zoneinfo import ZoneInfo

from mailer.constants import (
    WORKING_HOURS_START,
    WORKING_HOURS_END,
    SEND_BATCH_SIZE_DEFAULT,
    SEND_TASK_LOCK_TIMEOUT,
    MAX_ERROR_MESSAGE_LENGTH,
    BULK_UPDATE_BATCH_SIZE,
    DEFER_REASON_RATE_HOUR,
)
from mailer.services import rate_limiter
from mailer.smtp_sender import build_message, send_via_smtp

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Rate-limit wrappers (позволяют патчить в тестах через mailer.tasks.*)
# ---------------------------------------------------------------------------


def reserve_rate_limit_token(*args, **kwargs):
    return rate_limiter.reserve_rate_limit_token(*args, **kwargs)


def get_effective_quota_available(*args, **kwargs):
    return rate_limiter.get_effective_quota_available(*args, **kwargs)


# ---------------------------------------------------------------------------
# Определение типа ошибки SMTP
# ---------------------------------------------------------------------------


def _is_transient_send_error(err: str) -> bool:
    """
    Отличает временные ошибки SMTP от постоянных (битый ящик и т.п.).
    Временные ошибки НЕ должны превращать весь список получателей в FAILED.

    Критерий:
    - SMTP-коды 421, 450, 451, 452 (встречаются в тексте как «код XXX»)
    - Ключевые слова-признаки временности

    Намеренно НЕ включены:
    - «connection» / «соединен» — "connection refused" — постоянная ошибка (неверный хост/порт)
    """
    e = (err or "").strip().lower()
    if not e:
        return False

    # SMTP-коды, которые означают «временно»
    transient_codes = ("421", "450", "451", "452")
    for code in transient_codes:
        if f"(код {code})" in e or f" код {code}" in e:
            return True

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
        "rate limit",
        "network is unreachable",
    )
    return any(h in e for h in transient_hints)


# ---------------------------------------------------------------------------
# smtp.bz helpers
# ---------------------------------------------------------------------------

_SMTP_BZ_TAG_RE = re.compile(r"camp:([0-9a-fA-F-]{32,36});rcpt:([0-9a-fA-F-]{32,36})")


def _smtp_bz_extract_tag(row: dict) -> str:
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


def _smtp_bz_enrich_error(api_key: str, msg, campaign_id: str, recipient_id: str, err: str) -> str:
    """
    Пытаемся обогатить текст ошибки данными из smtp.bz API.
    Best-effort: если API недоступен — возвращаем исходную ошибку.
    """
    from mailer.smtp_bz_api import get_message_info, get_message_logs

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


# ---------------------------------------------------------------------------
# Вложение кампании
# ---------------------------------------------------------------------------


def _get_campaign_attachment_bytes(camp) -> tuple[bytes | None, str | None, str | None]:
    """
    Безопасно читает вложение кампании.
    При FileNotFoundError пытается найти файл в директории case-insensitively.

    Returns:
        (bytes, filename, error_message) — error_message=None если успешно
    """
    if not camp.attachment:
        return None, None, None

    try:
        camp.attachment.open()
        try:
            content = camp.attachment.read()
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
    except (FileNotFoundError, OSError):
        pass

    # Fallback: ищем файл в папке по имени без учёта регистра
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

    try:
        rel = found.relative_to(Path(str(storage_location)))
        camp.attachment.name = str(rel).replace("\\", "/")
        camp.save(update_fields=["attachment", "updated_at"])
    except Exception:
        pass

    original = (getattr(camp, "attachment_original_name", None) or "").strip()
    if original:
        return content, original[:255], None
    return content, found.name[:255], None


# ---------------------------------------------------------------------------
# Санитизация email-заголовков
# ---------------------------------------------------------------------------

_HEADER_CRLF_RE = re.compile(r"[\r\n\x00]+")


def _sanitize_header_value(value: str) -> str:
    """
    Удаляет CRLF и нулевые байты из строки, которая используется как email-заголовок.
    Предотвращает email header injection через sender_name / from_email.
    """
    return _HEADER_CRLF_RE.sub(" ", (value or "")).strip()


# ---------------------------------------------------------------------------
# Рабочее время
# ---------------------------------------------------------------------------


def _is_working_hours(now=None) -> bool:
    """Проверяет, что текущее время МСК в диапазоне 9:00–18:00."""
    if now is None:
        now = timezone.now()
    msk_tz = ZoneInfo("Europe/Moscow")
    msk_now = now.astimezone(msk_tz)
    return WORKING_HOURS_START <= msk_now.hour < WORKING_HOURS_END


# ---------------------------------------------------------------------------
# Уведомления о жизненном цикле кампании
# ---------------------------------------------------------------------------


def _notify_campaign_started(user, camp) -> None:
    """Отправляет уведомление «Рассылка началась» в интерфейс пользователя."""
    try:
        from notifications.service import notify
        from notifications.models import Notification

        notify(
            user=user,
            kind=Notification.Kind.SYSTEM,
            title="Рассылка началась",
            body=f"Кампания «{camp.name}» начала отправку.",
            url=f"/mail/campaigns/{camp.id}/",
            dedupe_seconds=3600,
        )
    except Exception:
        logger.warning(
            "Не удалось отправить уведомление 'кампания началась' для %s", camp.id, exc_info=True
        )


def _notify_campaign_finished(
    user, camp, *, sent_count: int, failed_count: int, total_count: int
) -> None:
    """Отправляет уведомление о завершении рассылки."""
    try:
        from notifications.service import notify
        from notifications.models import Notification

        if failed_count > 0:
            title = "Рассылка завершена с ошибками"
            body = (
                f"«{camp.name}»: отправлено {sent_count} из {total_count}, ошибки у {failed_count}."
            )
        else:
            title = "Рассылка завершена"
            body = f"«{camp.name}»: успешно отправлено {sent_count} писем."
        notify(
            user=user,
            kind=Notification.Kind.SYSTEM,
            title=title,
            body=body,
            url=f"/mail/campaigns/{camp.id}/",
        )
    except Exception:
        logger.warning(
            "Не удалось отправить уведомление 'кампания завершена' для %s", camp.id, exc_info=True
        )


def _notify_circuit_breaker_tripped(user, camp, *, error_count: int) -> None:
    """Уведомление о срабатывании circuit breaker (повторяющиеся ошибки SMTP)."""
    try:
        from notifications.service import notify
        from notifications.models import Notification

        notify(
            user=user,
            kind=Notification.Kind.SYSTEM,
            title="Рассылка приостановлена",
            body=(
                f"Кампания «{camp.name}» приостановлена из-за повторяющихся "
                f"ошибок SMTP ({error_count} подряд)."
            ),
            url=f"/mail/campaigns/{camp.id}/",
            dedupe_seconds=3600,
        )
    except Exception:
        logger.warning(
            "Не удалось отправить уведомление circuit breaker для %s", camp.id, exc_info=True
        )


def _notify_attachment_error(camp, *, error: str) -> None:
    """Уведомление о проблеме с вложением кампании."""
    if not camp.created_by:
        return
    try:
        from notifications.service import notify
        from notifications.models import Notification

        notify(
            user=camp.created_by,
            kind=Notification.Kind.SYSTEM,
            title="Рассылка остановлена: проблема с вложением",
            body=f"Кампания «{camp.name}» остановлена: {error}.",
            url=f"/mail/campaigns/{camp.id}/",
        )
    except Exception:
        logger.warning(
            "Не удалось отправить уведомление об ошибке вложения для %s", camp.id, exc_info=True
        )


# ---------------------------------------------------------------------------
# Обработка батча получателей
# ---------------------------------------------------------------------------


def _process_batch_recipients(
    *,
    batch: list,
    camp,
    queue_entry,
    smtp_cfg,
    max_per_hour: int,
    tokens: dict,
    unsub_set: set,
    base_html: str,
    base_text: str,
    attachment_bytes,
    attachment_name,
    identity,
    user,
) -> tuple[bool, bool]:
    """
    Отправляет батч получателей через SMTP и записывает результаты в БД.

    Returns:
        (transient_blocked, rate_limited) — флаги для управления circuit breaker и defer.
    """
    from django.db import transaction

    from mailer.logging_utils import get_pii_log_fields
    from mailer.mail_content import append_unsubscribe_footer, build_unsubscribe_url
    from mailer.models import CampaignRecipient, SendLog
    from mailer.services.queue import defer_queue

    now_ts = timezone.now()
    recipients_to_update = []
    logs_to_create = []
    transient_blocked = False
    rate_limited = False

    for r in batch:
        email_norm = (r.email or "").strip().lower()
        if not email_norm:
            continue

        if email_norm in unsub_set:
            r.status = CampaignRecipient.Status.UNSUBSCRIBED
            r.updated_at = now_ts
            recipients_to_update.append(r)
            continue

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
            from_email=_sanitize_header_value(
                (smtp_cfg.from_email or "").strip() or (smtp_cfg.smtp_username or "").strip()
            ),
            from_name=_sanitize_header_value(
                (camp.sender_name or "").strip() or (smtp_cfg.from_name or "CRM ПРОФИ").strip()
            ),
            reply_to=_sanitize_header_value((user.email or "").strip()),
            attachment_content=attachment_bytes,
            attachment_filename=attachment_name,
        )

        if unsub_url:
            msg["List-Unsubscribe"] = f"<{unsub_url}>"
            msg["List-Unsubscribe-Post"] = "List-Unsubscribe=One-Click"
        msg["Precedence"] = "bulk"
        msg["Auto-Submitted"] = "auto-generated"
        msg["X-Tag"] = f"camp:{camp.id};rcpt:{r.id}"

        # Idempotency: если письмо уже было отправлено (воркер упал после send, но
        # до bulk_update) — не отправляем повторно, просто синхронизируем статус.
        if SendLog.objects.filter(campaign=camp, recipient=r, status=SendLog.Status.SENT).exists():
            r.status = CampaignRecipient.Status.SENT
            r.last_error = ""
            r.updated_at = now_ts
            recipients_to_update.append(r)
            continue

        try:
            token_reserved, token_count, rate_reset_at = reserve_rate_limit_token(max_per_hour)
            if not token_reserved:
                defer_queue(queue_entry, DEFER_REASON_RATE_HOUR, rate_reset_at, notify=True)
                rate_limited = True
                break

            send_start = timezone.now()
            send_via_smtp(smtp_cfg, msg)
            send_ms = int((timezone.now() - send_start).total_seconds() * 1000)

            r.status = CampaignRecipient.Status.SENT
            r.last_error = ""
            r.updated_at = timezone.now()
            recipients_to_update.append(r)
            message_id = str(msg.get("Message-ID", ""))
            logs_to_create.append(
                SendLog(
                    campaign=camp,
                    recipient=r,
                    account=None,
                    provider="smtp_global",
                    status=SendLog.Status.SENT,
                    message_id=message_id,
                )
            )
            logger.info(
                "Email sent",
                extra={
                    "campaign_id": str(camp.id),
                    "recipient_id": str(r.id),
                    **get_pii_log_fields(r.email, log_level=logging.INFO),
                    "took_ms": send_ms,
                },
            )
            if queue_entry and queue_entry.consecutive_transient_errors > 0:
                queue_entry.consecutive_transient_errors = 0
                queue_entry.save(update_fields=["consecutive_transient_errors"])

        except Exception as ex:
            err = str(ex)
            logger.error(
                "Failed to send email",
                exc_info=True,
                extra={
                    "campaign_id": str(camp.id),
                    "recipient_id": str(r.id),
                    **get_pii_log_fields(r.email, log_level=logging.ERROR),
                },
            )

            if _is_transient_send_error(err):
                r.status = CampaignRecipient.Status.PENDING
                r.last_error = (err or "Временная ошибка")[:MAX_ERROR_MESSAGE_LENGTH]
                r.updated_at = timezone.now()
                recipients_to_update.append(r)
                logs_to_create.append(
                    SendLog(
                        campaign=camp,
                        recipient=r,
                        account=None,
                        provider="smtp_global",
                        status=SendLog.Status.FAILED,
                        error=(err or "Временная ошибка")[:MAX_ERROR_MESSAGE_LENGTH],
                    )
                )
                transient_blocked = True
                break

            # Обогащение ошибки через smtp.bz API
            if smtp_cfg.smtp_bz_api_key:
                err = _smtp_bz_enrich_error(
                    api_key=smtp_cfg.smtp_bz_api_key,
                    msg=msg,
                    campaign_id=str(camp.id),
                    recipient_id=str(r.id),
                    err=err,
                )

            r.status = CampaignRecipient.Status.FAILED
            r.last_error = (err or "Ошибка отправки")[:MAX_ERROR_MESSAGE_LENGTH]
            r.updated_at = timezone.now()
            recipients_to_update.append(r)
            logs_to_create.append(
                SendLog(
                    campaign=camp,
                    recipient=r,
                    account=None,
                    provider="smtp_global",
                    status=SendLog.Status.FAILED,
                    error=(err or "Ошибка отправки")[:MAX_ERROR_MESSAGE_LENGTH],
                )
            )

    if recipients_to_update or logs_to_create:
        with transaction.atomic():
            if recipients_to_update:
                CampaignRecipient.objects.bulk_update(
                    recipients_to_update,
                    ["status", "last_error", "updated_at"],
                    batch_size=BULK_UPDATE_BATCH_SIZE,
                )
            if logs_to_create:
                SendLog.objects.bulk_create(logs_to_create, ignore_conflicts=True)

    return transient_blocked, rate_limited
