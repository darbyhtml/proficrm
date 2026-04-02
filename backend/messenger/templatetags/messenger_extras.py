from __future__ import annotations

import html
import re

from django import template
from django.utils.safestring import mark_safe


register = template.Library()


@register.filter(name="messenger_format")
def messenger_format(value: str | None) -> str:
    """
    Простейшее форматирование для сообщений messenger:
    - экранирует HTML;
    - **жирный** → <strong>;
    - ссылки http(s):// → <a>;
    - переводы строк → <br>.

    Сделано максимально консервативно, без поддержки сырых HTML-тегов.
    """
    if not value:
        return ""

    text = html.escape(str(value))

    # Жирный текст: **text**
    text = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", text)

    # Ссылки http(s)://...
    url_pattern = re.compile(r"(https?://[^\s<]+)")

    def _link_repl(match: re.Match) -> str:
        url = match.group(1)
        return f'<a href="{url}" target="_blank" rel="noopener">{url}</a>'

    text = url_pattern.sub(_link_repl, text)

    # Переводы строк
    text = text.replace("\r\n", "\n").replace("\n", "<br>")

    return mark_safe(text)

