import re

from django import template

register = template.Library()

_HAS_SCHEME_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.-]*://")


@register.filter(name="external_url")
def external_url(value: str) -> str:
    """
    Normalizes user-entered URL so it becomes an absolute URL for <a href>.
    - "example.com" -> "https://example.com"
    - "//example.com" -> "https://example.com"
    - "http://example.com" stays as-is
    """
    if value is None:
        return ""
    s = str(value).strip()
    if not s:
        return ""
    if s.startswith("//"):
        return "https:" + s
    if _HAS_SCHEME_RE.match(s):
        return s
    return "https://" + s


