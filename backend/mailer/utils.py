from __future__ import annotations

import html
import re
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from django.utils import timezone as dj_timezone


_RE_SCRIPT = re.compile(r"(?is)<(script|style)[^>]*>.*?</\1\s*>")
_RE_TAGS = re.compile(r"(?is)<[^>]+>")
_RE_WS = re.compile(r"[ \t\r\f\v]+")


def html_to_text(value: str) -> str:
    """
    Очень простой конвертер HTML -> plain text (без внешних зависимостей).
    Нужен для multipart (deliverability), когда в UI заполняют только HTML.
    """
    if not value:
        return ""
    s = _RE_SCRIPT.sub(" ", value)
    # br/p -> перенос
    s = re.sub(r"(?i)<\s*br\s*/?>", "\n", s)
    s = re.sub(r"(?i)</\s*p\s*>", "\n\n", s)
    s = _RE_TAGS.sub(" ", s)
    s = html.unescape(s)
    s = _RE_WS.sub(" ", s)
    # нормализуем пустые строки
    s = re.sub(r"\n{3,}", "\n\n", s)
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


def get_next_send_window_start(
    now: datetime | None = None,
    *,
    use_working_hours: bool = True,
    always_tomorrow: bool = False,
) -> datetime:
    """
    Начало следующего окна отправки (timezone-aware).

    - use_working_hours=True: 9–18 МСК. always_tomorrow=False — 09:00 сегодня если
      сейчас до 09:00, иначе 09:00 завтра. always_tomorrow=True — всегда 09:00 завтра.
    - use_working_hours=False: 00:05 завтра в TIME_ZONE проекта.

    Для дневного лимита: always_tomorrow=True (продолжим завтра).
    Для "вне рабочего времени": always_tomorrow=False (следующее 09:00).
    """
    if now is None:
        now = dj_timezone.now()
    try:
        from django.conf import settings

        tz = ZoneInfo(getattr(settings, "TIME_ZONE", "Europe/Moscow"))
    except Exception:
        tz = ZoneInfo("Europe/Moscow")
    now_local = now.astimezone(tz)

    if use_working_hours:
        from mailer.constants import WORKING_HOURS_START

        if always_tomorrow:
            return (now_local + timedelta(days=1)).replace(
                hour=WORKING_HOURS_START, minute=0, second=0, microsecond=0
            )
        today_start = now_local.replace(hour=WORKING_HOURS_START, minute=0, second=0, microsecond=0)
        if now_local < today_start:
            return today_start
        return (now_local + timedelta(days=1)).replace(
            hour=WORKING_HOURS_START, minute=0, second=0, microsecond=0
        )
    next_day = (now_local + timedelta(days=1)).replace(hour=0, minute=5, second=0, microsecond=0)
    return next_day


# ---------------------------------------------------------------------------
# Email HTML санитизация (nh3 — Rust-based HTML parser whitelist)
# ---------------------------------------------------------------------------

_RE_BODY = re.compile(r"(?is)<\s*body\b[^>]*>(.*?)<\s*/\s*body\s*>")
_RE_HTML_WRAPPERS = re.compile(r"(?is)<\s*/?\s*(html|head)\b[^>]*>")
_RE_CK_SAVED_ATTR = re.compile(r"(?i)\sdata-cke-saved-(href|src)\s*=\s*(['\"]).*?\2")
_RE_IMG_TAG = re.compile(r"(?is)<\s*img\b[^>]*>")
_RE_IMG_STYLE = re.compile(r"(?is)\sstyle\s*=\s*(['\"])(.*?)\1")

# Разрешённые HTML теги в теле письма (email-safe whitelist)
_NH3_ALLOWED_TAGS = frozenset(
    {
        "a",
        "b",
        "blockquote",
        "br",
        "caption",
        "center",
        "code",
        "col",
        "colgroup",
        "div",
        "em",
        "figure",
        "figcaption",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "hr",
        "i",
        "img",
        "li",
        "ol",
        "p",
        "pre",
        "s",
        "small",
        "span",
        "strike",
        "strong",
        "sub",
        "sup",
        "table",
        "tbody",
        "td",
        "tfoot",
        "th",
        "thead",
        "tr",
        "u",
        "ul",
    }
)

# Общие атрибуты разрешены на всех тегах
_COMMON_ATTRS = frozenset({"class", "id", "style", "align", "valign", "dir"})

# Разрешённые атрибуты по тегу
_NH3_ALLOWED_ATTRS: dict[str, frozenset[str]] = {
    "a": _COMMON_ATTRS | frozenset({"href", "target", "rel", "name", "title"}),
    "img": _COMMON_ATTRS | frozenset({"src", "alt", "title", "width", "height", "border"}),
    "table": _COMMON_ATTRS
    | frozenset({"width", "height", "border", "cellpadding", "cellspacing", "bgcolor", "summary"}),
    "td": _COMMON_ATTRS | frozenset({"width", "height", "colspan", "rowspan", "bgcolor", "nowrap"}),
    "th": _COMMON_ATTRS | frozenset({"width", "height", "colspan", "rowspan", "bgcolor", "scope"}),
    "col": _COMMON_ATTRS | frozenset({"width", "span"}),
    "colgroup": _COMMON_ATTRS | frozenset({"width", "span"}),
    "figure": _COMMON_ATTRS,
    "figcaption": _COMMON_ATTRS,
    "blockquote": _COMMON_ATTRS | frozenset({"cite"}),
}
# Для тегов без специальных атрибутов — только общие
for _tag in _NH3_ALLOWED_TAGS:
    if _tag not in _NH3_ALLOWED_ATTRS:
        _NH3_ALLOWED_ATTRS[_tag] = _COMMON_ATTRS

# Разрешённые URL-схемы (mailto и data: нужны для email)
_NH3_URL_SCHEMES = frozenset({"http", "https", "mailto", "data", "cid"})


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
            return _RE_IMG_STYLE.sub(f" style={q}{style}{q}", tag, count=1)

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
    Серверная санитизация HTML через nh3 (Rust-based parser, whitelist-подход).
    Защищает от XSS при отображении в CRM UI и в email-клиентах.

    Использует строгий whitelist тегов и атрибутов вместо regex-замен.
    Regex-подход обходился через <scri pt>, entity encoding, STYLE-инъекции.
    """
    import nh3

    s = value or ""

    # Если прилетела обертка целого письма из почтовика — вытаскиваем только body
    m = _RE_BODY.search(s)
    if m:
        s = m.group(1) or ""

    # Убираем <html>/<head> если остались
    s = _RE_HTML_WRAPPERS.sub(" ", s)

    # Убираем CKEditor-артефакты data-cke-saved-*
    s = _RE_CK_SAVED_ATTR.sub(" ", s)

    # Очищаем через nh3 с whitelist (надёжнее regex)
    s = nh3.clean(
        s,
        tags=_NH3_ALLOWED_TAGS,
        attributes=_NH3_ALLOWED_ATTRS,
        url_schemes=_NH3_URL_SCHEMES,
        strip_comments=True,
        link_rel=None,  # Не добавляем rel="noopener" — в email ссылках это не нужно
    )

    # Подправляем <img> для предсказуемой верстки в почтовиках
    s = _normalize_email_img_tags(s)
    return s
