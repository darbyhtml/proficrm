from __future__ import annotations

import html
import re


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


