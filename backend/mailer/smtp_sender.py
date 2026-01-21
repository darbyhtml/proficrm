from __future__ import annotations

import smtplib
import ssl
import re
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
        msg.add_alternative(body_html, subtype="html", charset="utf-8")
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
            smtp.send_message(msg)
            return

        # Одиночная отправка (открыть/закрыть соединение внутри)
        smtp_local = open_smtp_connection(account)
        try:
            smtp_local.send_message(msg)
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


