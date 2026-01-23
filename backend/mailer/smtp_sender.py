from __future__ import annotations

import smtplib
import ssl
import re
import base64
from email.message import EmailMessage
from email.utils import formataddr, make_msgid

from django.conf import settings

from typing import Optional, Protocol

from mailer.models import MailAccount


class _SmtpAccountLike(Protocol):
    smtp_host: str
    smtp_port: int
    use_starttls: bool
    smtp_username: str
    is_enabled: bool

    def get_password(self) -> str: ...


_RE_DATA_IMG = re.compile(
    r"""(<img\b[^>]*?\bsrc\s*=\s*)(["'])(data:image/[^;"']+;base64,[^"']+)\2""",
    re.IGNORECASE,
)


def _inline_data_images(body_html: str) -> tuple[str, list[tuple[str, bytes, str]]]:
    """
    Конвертирует <img src="data:image/...;base64,..."> в cid: и возвращает список inline-вложений.

    Returns:
        (html_with_cid, images) где images = [(cid_without_brackets, bytes, mime_subtype)]
    """
    if not body_html:
        return body_html, []

    images: list[tuple[str, bytes, str]] = []
    html_out = body_html

    # Идём по совпадениям и заменяем по одной, чтобы не сломать индексы.
    # Ограничения: не более 30 картинок и не более ~10MB суммарно (страховка).
    total = 0
    count = 0

    def _replace(m: re.Match) -> str:
        nonlocal total, count, images
        if count >= 30:
            return m.group(0)
        prefix = m.group(1)
        quote = m.group(2)
        data_url = m.group(3) or ""
        try:
            header, b64 = data_url.split(",", 1)
            # header: data:image/png;base64
            mime = header.split(";", 1)[0].split(":", 1)[-1].strip().lower()
            subtype = "png"
            if "/" in mime:
                _mt, st = mime.split("/", 1)
                subtype = st or "png"
            raw = base64.b64decode(b64.strip(), validate=False)
            if not raw:
                return m.group(0)
            total += len(raw)
            if total > 10 * 1024 * 1024:
                return m.group(0)
            cid_full = make_msgid(domain=None)  # <...>
            cid = cid_full[1:-1]
            images.append((cid, raw, subtype))
            count += 1
            return f"{prefix}{quote}cid:{cid}{quote}"
        except Exception:
            return m.group(0)

    html_out = _RE_DATA_IMG.sub(_replace, html_out)
    return html_out, images


def build_message(
    *,
    account: MailAccount,
    to_email: str,
    subject: str,
    body_text: str,
    body_html: str,
    from_email: Optional[str] = None,
    from_name: Optional[str] = None,
    reply_to: Optional[str] = None,
    attachment: Optional[any] = None,
    attachment_content: Optional[bytes] = None,
    attachment_filename: Optional[str] = None,
) -> EmailMessage:
    msg = EmailMessage()
    msg["Subject"] = subject
    _from_email = (from_email or account.from_email or account.smtp_username or "").strip()
    _from_name = (from_name or account.from_name or "").strip()
    msg["From"] = formataddr((_from_name, _from_email)) if _from_name else _from_email
    msg["To"] = to_email
    _reply_to = (reply_to or account.reply_to or "").strip()
    if _reply_to:
        msg["Reply-To"] = _reply_to

    msg_id = make_msgid(domain=None)
    msg["Message-ID"] = msg_id

    if body_html:
        msg.set_content(body_text or " ", subtype="plain", charset="utf-8")
        # Важно: inline base64-картинки из буфера (например, из Яндекса) многие почтовики режут/ломают.
        # Конвертим data:image/... в cid: и прикладываем как multipart/related.
        html_fixed, inline_imgs = _inline_data_images(body_html)
        html_part = msg.add_alternative(html_fixed, subtype="html", charset="utf-8")
        # Привязываем inline-картинки к HTML-части.
        for cid, content, subtype in inline_imgs:
            try:
                # filename не задаём, чтобы почтовики реже показывали картинку как "вложение сверху"
                html_part.add_related(
                    content,
                    maintype="image",
                    subtype=subtype,
                    cid=f"<{cid}>",
                    filename=None,
                    disposition="inline",
                )
            except Exception:
                # если не получилось — письмо всё равно должно уйти
                continue
    else:
        msg.set_content(body_text or " ", subtype="plain", charset="utf-8")

    # Добавляем вложение, если оно есть (поддерживаем кеширование bytes на уровне батча)
    if attachment_content is not None:
        import mimetypes
        fname = (attachment_filename or "attachment").strip()
        mime_type, _ = mimetypes.guess_type(fname)
        if mime_type:
            maintype, subtype = mime_type.split("/", 1)
        else:
            maintype, subtype = "application", "octet-stream"
        msg.add_attachment(
            attachment_content,
            maintype=maintype,
            subtype=subtype,
            filename=fname,
        )
    elif attachment:
        import mimetypes
        attachment.open()
        try:
            content = attachment.read()
            fname = getattr(attachment, "name", None) or "attachment"
            mime_type, _ = mimetypes.guess_type(fname)
            if mime_type:
                maintype, subtype = mime_type.split("/", 1)
            else:
                maintype, subtype = "application", "octet-stream"
            msg.add_attachment(
                content,
                maintype=maintype,
                subtype=subtype,
                filename=fname,
            )
        finally:
            attachment.close()

    return msg


def format_smtp_error(error: Exception, account: _SmtpAccountLike) -> str:
    """
    Форматирует SMTP ошибку в понятное сообщение для пользователя.
    
    Args:
        error: Исключение, возникшее при отправке
        account: SMTP аккаунт
        
    Returns:
        Понятное сообщение об ошибке
    """
    error_str = str(error)
    error_type = type(error).__name__
    
    # Ошибки аутентификации
    if "authentication failed" in error_str.lower() or "535" in error_str or "535-" in error_str:
        return "Ошибка аутентификации: неверный логин или пароль SMTP. Проверьте настройки в Почта → Настройки."
    
    # Ошибки подключения
    if isinstance(error, (smtplib.SMTPConnectError, ConnectionError, OSError)):
        if "Connection refused" in error_str or "Connection refused" in str(error):
            return f"Не удалось подключиться к SMTP серверу {account.smtp_host}:{account.smtp_port}. Проверьте настройки подключения."
        if "timeout" in error_str.lower() or "timed out" in error_str.lower():
            return f"Таймаут подключения к SMTP серверу {account.smtp_host}:{account.smtp_port}. Сервер не отвечает."
        if "Name or service not known" in error_str or "getaddrinfo failed" in error_str:
            return f"Не удалось найти SMTP сервер {account.smtp_host}. Проверьте правильность адреса сервера."
        return f"Ошибка подключения к SMTP серверу: {error_str[:200]}"
    
    # Ошибки STARTTLS
    if "STARTTLS" in error_str or "starttls" in error_str.lower():
        return f"Ошибка установки защищенного соединения (STARTTLS) с {account.smtp_host}:{account.smtp_port}. Попробуйте отключить STARTTLS или проверьте настройки."
    
    # Ошибки отправки
    if isinstance(error, smtplib.SMTPRecipientsRefused):
        return f"Получатель отклонен сервером: {error_str[:200]}"
    
    if isinstance(error, smtplib.SMTPSenderRefused):
        return f"Отправитель отклонен сервером: {error_str[:200]}"
    
    if isinstance(error, smtplib.SMTPDataError):
        return f"Ошибка данных письма: {error_str[:200]}"
    
    if isinstance(error, smtplib.SMTPException):
        # Парсим код ошибки SMTP (например, "550 5.1.1 User unknown")
        code_match = re.search(r'(\d{3})\s+([\d\.]+)?\s*(.+)', error_str)
        if code_match:
            code = code_match.group(1)
            message = code_match.group(3) if code_match.group(3) else error_str
            
            # Известные коды ошибок
            error_codes = {
                "550": "Почтовый ящик не существует или недоступен",
                "551": "Пользователь не найден",
                "552": "Превышен лимит размера почтового ящика",
                "553": "Недопустимый адрес получателя",
                "554": "Транзакция не удалась",
                "421": "Сервис недоступен, попробуйте позже",
                "450": "Почтовый ящик временно недоступен",
                "451": "Ошибка обработки, попробуйте позже",
                "452": "Недостаточно места на сервере",
            }
            
            if code in error_codes:
                return f"{error_codes[code]} (код {code}): {message[:150]}"
            else:
                return f"Ошибка SMTP (код {code}): {message[:150]}"
        
        return f"Ошибка SMTP: {error_str[:200]}"
    
    # Общие ошибки
    if "RuntimeError" in error_type:
        return error_str  # RuntimeError уже содержит понятное сообщение
    
    # Если не удалось определить тип ошибки, возвращаем исходное сообщение
    return f"Ошибка отправки: {error_str[:200]}"


def open_smtp_connection(account: _SmtpAccountLike) -> smtplib.SMTP:
    """
    Открывает SMTP соединение (для reuse на батч).
    Возвращает залогиненный smtplib.SMTP. Закрытие — на вызывающей стороне (smtp.quit()).
    """
    password = account.get_password()
    if not account.is_enabled:
        raise RuntimeError("Почтовый аккаунт отключён.")
    if not account.smtp_username or not password:
        raise RuntimeError("Не заполнены SMTP логин/пароль.")

    try:
        if account.use_starttls:
            context = ssl.create_default_context()
            smtp = smtplib.SMTP(account.smtp_host, account.smtp_port, timeout=30)
            smtp.ehlo()
            smtp.starttls(context=context)
            smtp.ehlo()
            smtp.login(account.smtp_username, password)
            return smtp

        smtp = smtplib.SMTP(account.smtp_host, account.smtp_port, timeout=30)
        smtp.login(account.smtp_username, password)
        return smtp
    except Exception as e:
        formatted_error = format_smtp_error(e, account)
        formatted_error_obj = RuntimeError(formatted_error)
        formatted_error_obj.original_error = e
        raise formatted_error_obj from e


def send_via_smtp(account: _SmtpAccountLike, msg: EmailMessage, *, smtp: Optional[smtplib.SMTP] = None) -> None:
    """
    Отправляет письмо через SMTP с улучшенной обработкой ошибок.
    
    Raises:
        RuntimeError: С понятным сообщением об ошибке
    """
    try:
        if smtp is not None:
            refused = smtp.send_message(msg)
            # smtplib.send_message может вернуть dict отказанных получателей без исключения
            if refused:
                # формат: {email: (code, resp)} или {email: resp}
                first_email = next(iter(refused.keys()))
                detail = refused.get(first_email)
                if isinstance(detail, tuple) and len(detail) >= 2:
                    code, resp = detail[0], detail[1]
                    raise RuntimeError(f"Получатель отклонён сервером: {first_email} ({code}) {str(resp)[:200]}")
                raise RuntimeError(f"Получатель отклонён сервером: {first_email}")
            return

        # Одиночная отправка (открыть/закрыть соединение внутри)
        smtp_local = open_smtp_connection(account)
        try:
            refused = smtp_local.send_message(msg)
            if refused:
                first_email = next(iter(refused.keys()))
                detail = refused.get(first_email)
                if isinstance(detail, tuple) and len(detail) >= 2:
                    code, resp = detail[0], detail[1]
                    raise RuntimeError(f"Получатель отклонён сервером: {first_email} ({code}) {str(resp)[:200]}")
                raise RuntimeError(f"Получатель отклонён сервером: {first_email}")
        finally:
            try:
                smtp_local.quit()
            except Exception:
                pass
    except Exception as e:
        # Форматируем ошибку в понятное сообщение
        formatted_error = format_smtp_error(e, account)
        # Сохраняем оригинальную ошибку в атрибуте для логирования
        formatted_error_obj = RuntimeError(formatted_error)
        formatted_error_obj.original_error = e
        raise formatted_error_obj from e


