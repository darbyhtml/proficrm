from __future__ import annotations

import secrets
from typing import Iterable

from django.conf import settings

from accounts.models import User
from mailer.models import UnsubscribeToken
from mailer.utils import html_to_text


def apply_signature(*, user: User, body_html: str, body_text: str) -> tuple[str, str]:
    """
    Добавляет HTML подпись пользователя к письму.
    """
    sig_html = (getattr(user, "email_signature_html", "") or "").strip()
    if not sig_html:
        return body_html, body_text
    sig_text = (html_to_text(sig_html or "") or "").strip()
    new_html = (body_html or "")
    if new_html:
        new_html = new_html + "<br><br>" + sig_html
    else:
        new_html = sig_html
    new_text = (body_text or "")
    if sig_text:
        new_text = (new_text + "\n\n" + sig_text) if new_text else sig_text
    return new_html, new_text


def build_unsubscribe_url(token: str) -> str:
    base = (getattr(settings, "PUBLIC_BASE_URL", "") or "").strip().rstrip("/")
    # Если PUBLIC_BASE_URL не задан, вернем относительный URL (List-Unsubscribe может не работать,
    # но ссылка в тексте будет вести в пределах сайта при открытии из браузера).
    path = f"/unsubscribe/{token}/"
    return (base + path) if base else path


def ensure_unsubscribe_tokens(emails: Iterable[str]) -> dict[str, str]:
    """
    Для списка email возвращает маппинг email->token, создавая недостающие токены.
    Стараемся переиспользовать один токен на email.
    """
    emails_norm = [e.strip().lower() for e in emails if (e or "").strip()]
    emails_norm = list(dict.fromkeys(emails_norm))  # unique preserving order
    if not emails_norm:
        return {}

    existing = {
        (t.email or "").strip().lower(): t.token
        for t in UnsubscribeToken.objects.filter(email__in=emails_norm).only("email", "token")
    }
    missing = [e for e in emails_norm if e not in existing]
    if missing:
        to_create = []
        for e in missing:
            token = secrets.token_urlsafe(32)[:64]
            to_create.append(UnsubscribeToken(email=e, token=token))
            existing[e] = token
        # token уникальный, но вероятность коллизии мала; если коллизия случится — DB выбросит ошибку.
        UnsubscribeToken.objects.bulk_create(to_create, ignore_conflicts=True)
        # на всякий случай перечитываем, чтобы гарантировать token (если была коллизия)
        existing = {
            (t.email or "").strip().lower(): t.token
            for t in UnsubscribeToken.objects.filter(email__in=emails_norm).only("email", "token")
        }

    return existing


def append_unsubscribe_footer(*, body_html: str, body_text: str, unsubscribe_url: str) -> tuple[str, str]:
    """
    Добавляет ссылку отписки в HTML и text.
    """
    url = (unsubscribe_url or "").strip()
    if not url:
        return body_html, body_text

    footer_text = f"\n\n---\nОтписаться: {url}\n"
    footer_html = (
        '<br><br><hr style="border:none;border-top:1px solid #e5e7eb;margin:16px 0;">'
        f'<div style="font-size:12px;color:#6b7280">Отписаться: <a href="{url}">{url}</a></div>'
    )

    out_html = (body_html or "")
    out_text = (body_text or "")
    out_html = out_html + footer_html if out_html else footer_html
    out_text = (out_text + footer_text) if out_text else footer_text.strip()
    return out_html, out_text

