"""
Общие вспомогательные функции для view-модулей mailer.
Вынесены сюда чтобы избежать циклических импортов.
"""

from __future__ import annotations

import json
import logging

from django.contrib import messages
from django.core.cache import cache
from django.http import HttpRequest

from accounts.models import User
from mailer.constants import UNSUBSCRIBE_RATE_LIMIT_PER_HOUR
from mailer.models import Campaign, GlobalMailAccount, SmtpBzQuota
from mailer.throttle import is_user_throttled

logger = logging.getLogger(__name__)


def _smtp_bz_extract_total(resp) -> int | None:
    """
    Унифицирует "сколько записей" из ответа smtp.bz /log/message.
    В Swagger у ответа может быть total/count либо просто список.
    """
    if resp is None:
        return None
    if isinstance(resp, dict):
        for k in ("total", "count", "Total", "Count"):
            v = resp.get(k)
            if isinstance(v, (int, float)):
                return int(v)
            if isinstance(v, str) and v.isdigit():
                return int(v)
        data = resp.get("data")
        if isinstance(data, list):
            return len(data)
        return 0
    if isinstance(resp, list):
        return len(resp)
    return None


def _smtp_bz_today_stats_cached(*, api_key: str, today_str: str) -> dict:
    """
    Лёгкая аналитика из smtp.bz API (кешируем, чтобы не дергать API на каждый рендер).
    today_str: YYYY-MM-DD (по МСК).
    """
    cache_key = f"smtp_bz:stats:{today_str}"
    cached = cache.get(cache_key)
    if isinstance(cached, dict):
        return cached

    try:
        from mailer.smtp_bz_api import get_message_logs

        bounce = _smtp_bz_extract_total(
            get_message_logs(
                api_key, status="bounce", limit=1, start_date=today_str, end_date=today_str
            )
        )
        returned = _smtp_bz_extract_total(
            get_message_logs(
                api_key, status="return", limit=1, start_date=today_str, end_date=today_str
            )
        )
        cancelled = _smtp_bz_extract_total(
            get_message_logs(
                api_key, status="cancel", limit=1, start_date=today_str, end_date=today_str
            )
        )
        opened = _smtp_bz_extract_total(
            get_message_logs(
                api_key, is_open=True, limit=1, start_date=today_str, end_date=today_str
            )
        )
        unsub = _smtp_bz_extract_total(
            get_message_logs(
                api_key, is_unsubscribe=True, limit=1, start_date=today_str, end_date=today_str
            )
        )

        result = {
            "bounce": bounce,
            "return": returned,
            "cancel": cancelled,
            "opened": opened,
            "unsub": unsub,
        }
        cache.set(cache_key, result, timeout=60)
        return result
    except Exception as e:
        logger.debug(f"Failed to fetch smtp.bz stats: {e}")
        return {}


def _can_manage_campaign(user: User, camp: Campaign) -> bool:
    # ТЗ:
    # - менеджер: только свои кампании
    # - директор филиала/РОП: кампании филиала создателя
    # - управляющий/админ: все кампании
    if not user or not user.is_authenticated or not user.is_active:
        return False
    if user.is_superuser or user.role in (User.Role.ADMIN, User.Role.GROUP_MANAGER):
        return True
    if user.role == User.Role.MANAGER:
        return bool(camp.created_by_id and camp.created_by_id == user.id)
    if user.role in (User.Role.BRANCH_DIRECTOR, User.Role.SALES_HEAD):
        if not user.branch_id:
            return bool(camp.created_by_id and camp.created_by_id == user.id)
        if not camp.created_by_id:
            return False
        try:
            creator = getattr(camp, "created_by", None)
            creator_branch_id = getattr(creator, "branch_id", None) if creator else None
            if creator_branch_id is None:
                return False
            return bool(creator_branch_id == user.branch_id)
        except Exception:
            return False
    return False


def _contains_links(value: str) -> bool:
    v = (value or "").lower()
    return any(x in v for x in ("<a ", "href=", "http://", "https://", "www."))


def _dispatch_test_email(request: HttpRequest, cfg: GlobalMailAccount, x_tag: str) -> bool:
    """
    Ставит тестовое письмо в Celery-очередь на адрес текущего пользователя.
    Возвращает True если письмо поставлено, False если email не задан или превышен лимит.
    """
    to_email = (request.user.email or "").strip()
    if not to_email:
        messages.error(request, "В вашем профиле не задан email — некуда отправить тест.")
        return False

    from django.conf import settings as _dj_settings

    throttle_limit = getattr(_dj_settings, "MAILER_THROTTLE_TEST_EMAIL_PER_HOUR", 5)
    is_throttled, _current, _ = is_user_throttled(
        request.user.id, "send_test_email", max_requests=throttle_limit, window_seconds=3600
    )
    if is_throttled:
        messages.error(
            request, f"Превышен лимит тестовых писем ({throttle_limit}/час). Попробуйте позже."
        )
        return False

    from mailer.mail_content import (
        append_unsubscribe_footer,
        build_unsubscribe_url,
        ensure_unsubscribe_tokens,
    )
    from mailer.tasks import send_test_email

    try:
        token = ensure_unsubscribe_tokens([to_email]).get(to_email.lower(), "")
        unsub_url = build_unsubscribe_url(token) if token else ""
    except Exception:
        unsub_url = ""

    body_html = "<p>Тестовое письмо из CRM ПРОФИ.</p><p>Если вы это читаете — SMTP настроен.</p>"
    body_text = "Тестовое письмо из CRM ПРОФИ.\n\nЕсли вы это читаете — SMTP настроен.\n"
    if unsub_url:
        body_html, body_text = append_unsubscribe_footer(
            body_html=body_html, body_text=body_text, unsubscribe_url=unsub_url
        )

    send_test_email.delay(
        to_email=to_email,
        subject="CRM ПРОФИ: тест отправки",
        body_html=body_html,
        body_text=body_text,
        from_email=((cfg.from_email or "").strip() or (cfg.smtp_username or "").strip()),
        from_name=(cfg.from_name or "CRM ПРОФИ").strip(),
        reply_to=to_email,
        x_tag=x_tag,
    )
    messages.success(
        request,
        f"Тестовое письмо поставлено в очередь. Проверьте ящик {to_email} через несколько секунд.",
    )
    return True
