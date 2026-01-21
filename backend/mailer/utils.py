from __future__ import annotations

import html
import re
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from django.utils import timezone as dj_timezone


_RE_SCRIPT = re.compile(r"(?is)<(script|style).*?>.*?</\\1>")
_RE_TAGS = re.compile(r"(?is)<[^>]+>")
_RE_WS = re.compile(r"[ \\t\\r\\f\\v]+")


def html_to_text(value: str) -> str:
    """
    Очень простой конвертер HTML -> plain text (без внешних зависимостей).
    Нужен для multipart (deliverability), когда в UI заполняют только HTML.
    """
    if not value:
        return ""
    s = _RE_SCRIPT.sub(" ", value)
    # br/p -> перенос
    s = re.sub(r"(?i)<\\s*br\\s*/?>", "\\n", s)
    s = re.sub(r"(?i)</\\s*p\\s*>", "\\n\\n", s)
    s = _RE_TAGS.sub(" ", s)
    s = html.unescape(s)
    s = _RE_WS.sub(" ", s)
    # нормализуем пустые строки
    s = re.sub(r"\\n{3,}", "\\n\\n", s)
    return s.strip()


def msk_day_bounds(now: datetime | None = None) -> tuple[datetime, datetime, datetime]:
    """
    Возвращает границы "сегодня" по МСК в UTC:
      (start_utc, end_utc, now_msk)
    """
    if now is None:
        now = dj_timezone.now()
    msk_tz = ZoneInfo("Europe/Moscow")
    now_msk = now.astimezone(msk_tz)
    start_msk = datetime.combine(now_msk.date(), time.min, tzinfo=msk_tz)
    end_msk = start_msk + timedelta(days=1)
    return start_msk.astimezone(ZoneInfo("UTC")), end_msk.astimezone(ZoneInfo("UTC")), now_msk


_RE_SCRIPT_TAG = re.compile(r"<\s*script\b[^>]*>[\s\S]*?<\s*/\s*script\s*>", re.IGNORECASE)
_RE_ON_ATTR_DQ = re.compile(r"""\son\w+\s*=\s*"[^"]*" """, re.IGNORECASE)
_RE_ON_ATTR_SQ = re.compile(r"""\son\w+\s*=\s*'[^']*' """, re.IGNORECASE)
_RE_JS_PROTO = re.compile(r"""(\b(?:href|src)\s*=\s*["']?)\s*javascript:[^"'>\s]+""", re.IGNORECASE)


def sanitize_email_html(value: str) -> str:
    """
    Минимальная серверная санитизация HTML (защита от stored-XSS при отображении в UI).
    Email-клиенты обычно режут скрипты сами, но мы защищаем и CRM UI.
    """
    s = value or ""
    s = _RE_SCRIPT_TAG.sub("", s)
    # Удаляем inline обработчики событий
    s = _RE_ON_ATTR_DQ.sub(" ", s)
    s = _RE_ON_ATTR_SQ.sub(" ", s)
    # Убираем javascript: в href/src
    s = _RE_JS_PROTO.sub(r"\1#", s)
    return s


