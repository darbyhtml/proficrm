from __future__ import annotations

import smtplib
import ssl
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

    return msg


def send_via_smtp(account: _SmtpAccountLike, msg: EmailMessage) -> None:
    password = account.get_password()
    if not account.is_enabled:
        raise RuntimeError("Почтовый аккаунт отключён.")
    if not account.smtp_username or not password:
        raise RuntimeError("Не заполнены SMTP логин/пароль.")

    if account.use_starttls:
        context = ssl.create_default_context()
        with smtplib.SMTP(account.smtp_host, account.smtp_port, timeout=30) as smtp:
            smtp.ehlo()
            smtp.starttls(context=context)
            smtp.ehlo()
            smtp.login(account.smtp_username, password)
            smtp.send_message(msg)
    else:
        with smtplib.SMTP(account.smtp_host, account.smtp_port, timeout=30) as smtp:
            smtp.login(account.smtp_username, password)
            smtp.send_message(msg)


