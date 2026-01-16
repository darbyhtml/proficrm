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


@register.filter(name="format_phone")
def format_phone(value: str) -> str:
    """
    Форматирует номер телефона в формат: +7 (912) 345-6789
    Убирает все нецифровые символы, кроме + в начале, затем форматирует.
    """
    if value is None:
        return ""
    s = str(value).strip()
    if not s:
        return ""
    
    # Убираем все символы, кроме цифр и + в начале
    digits = re.sub(r"[^\d+]", "", s)
    
    # Если начинается с +7, обрабатываем как российский номер
    if digits.startswith("+7"):
        digits = digits[2:]  # Убираем +7
        # Если после +7 идет 8, убираем её (например +78XXXXXXXXX -> +7XXXXXXXXX)
        if digits.startswith("8") and len(digits) > 10:
            digits = digits[1:]
    elif digits.startswith("8") and len(digits) >= 11:
        digits = digits[1:]  # Убираем 8
    elif digits.startswith("7") and len(digits) >= 11:
        digits = digits[1:]  # Убираем 7
    
    # Если осталось 10 цифр, форматируем как +7 (XXX) XXX-XXXX
    if len(digits) == 10:
        return f"+7 ({digits[0:3]}) {digits[3:6]}-{digits[6:10]}"
    
    # Если осталось 11 цифр (возможно с лишней 7 или 8), берем последние 10
    if len(digits) == 11:
        digits = digits[-10:]
        return f"+7 ({digits[0:3]}) {digits[3:6]}-{digits[6:10]}"
    
    # Если не подходит под формат, возвращаем как есть
    return s


@register.filter(name="format_call_direction")
def format_call_direction(value):
    """
    Форматирует направление звонка в человеческий текст.
    ЭТАП 4: Безопасное отображение direction.
    """
    if not value:
        return "—"
    
    direction_map = {
        "outgoing": "Исходящий",
        "incoming": "Входящий",
        "missed": "Пропущенный",
        "unknown": "—",
    }
    
    return direction_map.get(value, "—")


@register.filter(name="format_action_source")
def format_action_source(value):
    """
    Форматирует источник действия в человеческий текст.
    ЭТАП 4: Безопасное отображение action_source.
    """
    if not value:
        return "—"
    
    source_map = {
        "crm_ui": "CRM",
        "notification": "Уведомление",
        "history": "История",
        "unknown": "—",
    }
    
    return source_map.get(value, "—")


@register.filter(name="format_resolve_method")
def format_resolve_method(value):
    """
    Форматирует метод определения результата в человеческий текст.
    ЭТАП 4: Безопасное отображение resolve_method (скрыто от менеджеров).
    """
    if not value:
        return "—"
    
    # Для менеджеров показываем общий текст, технические детали скрыты
    if value in ("observer", "retry"):
        return "Определено автоматически"
    
    return "—"

