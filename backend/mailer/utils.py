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
_RE_ON_ATTR_DQ = re.compile(r'\son\w+\s*=\s*"[^"]*"',
                            re.IGNORECASE)
_RE_ON_ATTR_SQ = re.compile(r"\son\w+\s*=\s*'[^']*'",
                            re.IGNORECASE)
_RE_JS_PROTO = re.compile(r"""(\b(?:href|src)\s*=\s*["']?)\s*javascript:[^"'>\s]+""", re.IGNORECASE)
_RE_BODY = re.compile(r"(?is)<\s*body\b[^>]*>(.*?)<\s*/\s*body\s*>")
_RE_HTML_WRAPPERS = re.compile(r"(?is)<\s*/?\s*(html|head)\b[^>]*>")
_RE_CK_SAVED_ATTR = re.compile(r"(?i)\sdata-cke-saved-(href|src)\s*=\s*(['\"]).*?\2")
_RE_IMG_TAG = re.compile(r"(?is)<\s*img\b[^>]*>")
_RE_IMG_STYLE = re.compile(r"(?is)\sstyle\s*=\s*(['\"])(.*?)\1")


def _normalize_email_img_tags(html_value: str) -> str:
    """
    Делает <img> более "почтово-совместимыми" (Yandex/Gmail/Outlook):
    - display:block (убирает лишние пробелы/inline-gap)
    - max-width:100% + height:auto (не вылезает за контейнер)
    - border/outline/text-decoration:0 (визуальная чистота)
    """
    s = html_value or ""
    if "<img" not in s.lower():
        return s

    required_bits = [
        "display:block",
        "max-width:100%",
        "height:auto",
        "border:0",
        "outline:none",
        "text-decoration:none",
    ]

    def _fix_one(tag: str) -> str:
        if not tag:
            return tag
        m = _RE_IMG_STYLE.search(tag)
        if m:
            q = m.group(1)
            style = (m.group(2) or "").strip()
            style_l = style.lower()
            # добавляем недостающие кусочки
            for bit in required_bits:
                key = bit.split(":", 1)[0]
                if key and key in style_l:
                    continue
                if style and not style.endswith(";"):
                    style += ";"
                style += bit + ";"
                style_l = style.lower()
            return _RE_IMG_STYLE.sub(f' style={q}{style}{q}', tag, count=1)

        # style нет — добавим перед закрывающим ">"
        style = ";".join(required_bits) + ";"
        if tag.endswith("/>"):
            return tag[:-2] + f' style="{style}"/>'  # self-closing
        if tag.endswith(">"):
            return tag[:-1] + f' style="{style}">'
        return tag

    return _RE_IMG_TAG.sub(lambda m: _fix_one(m.group(0)), s)


def sanitize_email_html(value: str) -> str:
    """
    Минимальная серверная санитизация HTML (защита от stored-XSS при отображении в UI).
    Email-клиенты обычно режут скрипты сами, но мы защищаем и CRM UI.
    """
    s = value or ""
    # Если прилетела обертка целого письма из почтовика, вытаскиваем только body,
    # чтобы не было вложенных <body>/<html> в письме (это часто ломает верстку в почтовиках).
    m = _RE_BODY.search(s)
    if m:
        s = m.group(1) or ""
    # Убираем <html>/<head> если они остались (и "data-cke-saved-*" атрибуты)
    s = _RE_HTML_WRAPPERS.sub(" ", s)
    s = _RE_CK_SAVED_ATTR.sub(" ", s)
    s = _RE_SCRIPT_TAG.sub("", s)
    # Удаляем inline обработчики событий
    s = _RE_ON_ATTR_DQ.sub(" ", s)
    s = _RE_ON_ATTR_SQ.sub(" ", s)
    # Убираем javascript: в href/src
    s = _RE_JS_PROTO.sub(r"\1#", s)
    # Подправляем <img> для предсказуемой верстки в почтовиках
    s = _normalize_email_img_tags(s)
    return s


