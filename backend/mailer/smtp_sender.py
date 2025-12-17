from __future__ import annotations

import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formataddr, make_msgid

from django.conf import settings

from mailer.models import MailAccount


def build_message(
    *,
    account: MailAccount,
    to_email: str,
    subject: str,
    body_text: str,
    body_html: str,
    unsubscribe_url: str,
) -> EmailMessage:
    msg = EmailMessage()
    msg["Subject"] = subject
    from_email = account.from_email or account.smtp_username
    from_name = account.from_name or ""
    msg["From"] = formataddr((from_name, from_email)) if from_name else from_email
    msg["To"] = to_email
    if account.reply_to:
        msg["Reply-To"] = account.reply_to

    # Помогает с доставляемостью/спамом: стандартный заголовок отписки
    msg["List-Unsubscribe"] = f"<{unsubscribe_url}>"
    msg["List-Unsubscribe-Post"] = "List-Unsubscribe=One-Click"

    msg_id = make_msgid(domain=None)
    msg["Message-ID"] = msg_id

    if body_html:
        msg.set_content(body_text or " ", subtype="plain", charset="utf-8")
        msg.add_alternative(body_html, subtype="html", charset="utf-8")
    else:
        msg.set_content(body_text or " ", subtype="plain", charset="utf-8")

    return msg


def send_via_smtp(account: MailAccount, msg: EmailMessage) -> None:
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


