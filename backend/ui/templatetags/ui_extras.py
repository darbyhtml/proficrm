import re
from functools import lru_cache
from zoneinfo import ZoneInfo

from django import template
from django.utils import timezone
from django.utils.html import format_html, conditional_escape
from django.utils.safestring import mark_safe

register = template.Library()

# SVG icons for task type badges
_BADGE_ICONS: dict[str, str] = {
    "phone": '<path d="M22 16.9v3a2 2 0 0 1-2.2 2 19.8 19.8 0 0 1-8.6-3.1 19.5 19.5 0 0 1-6-6A19.8 19.8 0 0 1 2.1 4.2 2 2 0 0 1 4.1 2h3a2 2 0 0 1 2 1.7c.1.9.3 1.8.6 2.6a2 2 0 0 1-.5 2.1L8 9.9a16 16 0 0 0 6 6l1.5-1.2a2 2 0 0 1 2.1-.5c.8.3 1.7.5 2.6.6a2 2 0 0 1 1.8 2.1z"/>',
    "mail": '<path d="M4 4h16v16H4z"/><path d="M4 7l8 5 8-5"/>',
    "document": '<path d="M7 2h8l4 4v16H7z"/><path d="M15 2v4h4"/>',
    "calendar": '<rect x="3" y="4" width="18" height="18" rx="2"/><path d="M16 2v4M8 2v4M3 10h18"/>',
    "question": '<path d="M12 17h.01"/><path d="M9.1 9a3 3 0 0 1 5.8 1c0 2-2 2.5-2 4"/><circle cx="12" cy="12" r="9"/>',
    "alert": '<path d="M12 8v5"/><path d="M12 16h.01"/><path d="M4.5 19h15L12 4z"/>',
    "education": '<path d="M4 10l8-4 8 4-8 4-8-4z"/><path d="M6 12v4l6 3 6-3v-4"/>',
    "send": '<path d="M4 4l16 8-16 8 4-8-4-8z"/>',
    "check": '<path d="M5 13l4 4L19 7"/>',
    "clock": '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 3"/>',
    "repeat": '<path d="M4 4h9a4 4 0 014 4v1"/><path d="M9 20H4a4 4 0 01-4-4v-1"/><path d="M8 4L4 0 0 4"/><path d="M16 24l4-4 4 4"/>',
    "target": '<circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="5"/><circle cx="12" cy="12" r="2"/>',
    "user": '<circle cx="12" cy="8" r="4"/><path d="M4 20a8 8 0 0116 0"/>',
    "team": '<circle cx="8" cy="9" r="3"/><circle cx="16" cy="9" r="3"/><path d="M2 20a6 6 0 0112 0"/><path d="M10 20a6 6 0 0112 0"/>',
    "money": '<rect x="3" y="5" width="18" height="14" rx="2"/><circle cx="12" cy="12" r="3"/>',
    "cart": '<circle cx="9" cy="21" r="1"/><circle cx="19" cy="21" r="1"/><path d="M3 3h2l3 12h11l2-8H8"/>',
    "chat": '<path d="M4 5h16v10H5l-3 3V5z"/>',
    "star": '<path d="M12 2.5l2.9 5.9 6.6.9-4.8 4.7 1.1 6.5L12 17.8 6.2 20.5l1.1-6.5-4.8-4.7 6.6-.9z"/>',
}

_COLOR_CLASSES: dict[str, str] = {
    "badge-blue": "bg-blue-100 text-blue-800",
    "badge-green": "bg-green-100 text-green-800",
    "badge-red": "bg-red-100 text-red-800",
    "badge-amber": "bg-amber-100 text-amber-800",
    "badge-orange": "bg-orange-100 text-orange-800",
    "badge-teal": "bg-teal-100 text-teal-800",
    "badge-indigo": "bg-indigo-100 text-indigo-800",
    "badge-purple": "bg-purple-100 text-purple-800",
    "badge-pink": "bg-pink-100 text-pink-800",
    "badge-gray": "bg-gray-100 text-gray-800",
}

_SVG_STROKE_ICONS = {k for k in _BADGE_ICONS if k != "star"}


@register.simple_tag
def task_type_badge(task_type):
    """
    Renders a task type badge as a safe HTML string.
    Replaces {% include "ui/partials/task_type_badge.html" %} in loops.
    No sub-template rendering — avoids Django template stack overhead.

    Usage: {% task_type_badge task.type %}
    """
    if task_type is None:
        return mark_safe(
            '<span class="inline-flex items-center gap-1 text-sm px-2 py-0.5 rounded-full '
            'bg-gray-100 text-gray-700">Без статуса</span>'
        )

    color = task_type.color or "badge-gray"
    color_cls = _COLOR_CLASSES.get(color, "bg-gray-100 text-gray-800")
    icon = task_type.icon or ""
    name = conditional_escape(task_type.name)

    icon_html = ""
    if icon and icon in _BADGE_ICONS:
        paths = _BADGE_ICONS[icon]
        if icon in _SVG_STROKE_ICONS:
            svg_attrs = 'fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"'
        else:
            svg_attrs = 'fill="currentColor" stroke="none"'
        icon_html = (
            f'<span class="inline-flex items-center justify-center w-3.5 h-3.5">'
            f'<svg viewBox="0 0 24 24" width="14" height="14" {svg_attrs}>{paths}</svg>'
            f'</span>'
        )

    return mark_safe(
        f'<span class="inline-flex items-center gap-1 text-sm px-2 py-0.5 rounded-full {color_cls}">'
        f'{icon_html}<span>{name}</span></span>'
    )

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


@register.filter(name="split_inns")
def split_inns(value: str) -> list[str]:
    """
    Возвращает список ИНН из строки (те же правила, что в companies.inn_utils.parse_inns: 10/12 + fallback 8–12 цифр).
    Нужно для красивого отображения множественных ИНН в шаблонах.
    """
    if value is None:
        return []
    from companies.inn_utils import parse_inns
    return parse_inns(value)


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


_TZ_LABELS: dict[str, str] = {
    # Частые TZ в РФ/CRM (чтобы менеджерам было понятно с первого взгляда)
    "Europe/Moscow": "МСК",
    "Asia/Yekaterinburg": "ЕКБ",
    "Asia/Krasnoyarsk": "КРС",
    "Asia/Novosibirsk": "НСК",
    "Asia/Omsk": "ОМС",
    "Asia/Irkutsk": "ИРК",
    "Asia/Vladivostok": "ВЛД",
    "Asia/Sakhalin": "СХЛ",
    "Asia/Kamchatka": "КМЧ",
}

from ui.timezone_utils import RUS_TZ_CHOICES, guess_ru_timezone_from_address


@lru_cache(maxsize=128)
def _zoneinfo(name: str) -> ZoneInfo:
    return ZoneInfo(name)


@register.filter(name="tz_offset")
def tz_offset(tz_name: str) -> str:
    """
    Возвращает UTC-offset для таймзоны на текущий момент.
    Пример: "UTC+03", "UTC+05:30".
    """
    if tz_name is None:
        return ""
    name = str(tz_name).strip()
    if not name:
        return ""
    try:
        dt = timezone.now()
        z = _zoneinfo(name)
        off = dt.astimezone(z).utcoffset()
        if off is None:
            return ""
        total_minutes = int(off.total_seconds() // 60)
        sign = "+" if total_minutes >= 0 else "-"
        total_minutes = abs(total_minutes)
        hh = total_minutes // 60
        mm = total_minutes % 60
        if mm:
            return f"UTC{sign}{hh:02d}:{mm:02d}"
        return f"UTC{sign}{hh:02d}"
    except Exception:
        return ""


@register.filter(name="tz_label")
def tz_label(tz_name: str) -> str:
    """
    Человекочитаемая подпись таймзоны (для списков).
    Пример: "ЕКБ (UTC+05)" или "Yekaterinburg (UTC+05)".
    """
    if tz_name is None:
        return ""
    name = str(tz_name).strip()
    if not name:
        return ""
    label = _TZ_LABELS.get(name)
    if not label:
        # "Asia/Yekaterinburg" -> "Yekaterinburg"
        label = name.split("/")[-1].replace("_", " ")
    off = tz_offset(name)
    return f"{label} ({off})" if off else label


@register.filter(name="tz_now_hhmm")
def tz_now_hhmm(tz_name: str) -> str:
    """Текущее время в указанной таймзоне (HH:MM)."""
    if tz_name is None:
        return ""
    name = str(tz_name).strip()
    if not name:
        return ""
    try:
        dt = timezone.now()
        z = _zoneinfo(name)
        local_dt = dt.astimezone(z)
        return local_dt.strftime("%H:%M")
    except Exception:
        return ""


@register.filter(name="guess_ru_tz")
def guess_ru_tz(address: str) -> str:
    """IANA TZ по адресу (эвристика по РФ)."""
    try:
        return guess_ru_timezone_from_address(address)
    except Exception:
        return ""


_RUS_TZ_SET = {tz for tz, _label in (RUS_TZ_CHOICES or [])} | {
    # на всякий случай
    "Europe/Moscow",
    "Europe/Samara",
    "Europe/Kaliningrad",
}


try:
    import phonenumbers  # type: ignore
    from phonenumbers import geocoder as _pn_geocoder  # type: ignore
    from phonenumbers import timezone as _pn_tz  # type: ignore
except Exception:  # pragma: no cover
    phonenumbers = None
    _pn_geocoder = None
    _pn_tz = None


@lru_cache(maxsize=2048)
def _parse_phone(raw: str):
    if not raw:
        return None
    if phonenumbers is None:
        return None
    s = str(raw).strip()
    if not s:
        return None
    try:
        # По умолчанию считаем RU, чтобы "8..." и "9xx..." распознавались.
        return phonenumbers.parse(s, "RU")
    except Exception:
        return None


@register.filter(name="abs")
def abs_filter(value):
    """Возвращает абсолютное значение числа."""
    try:
        return abs(int(value))
    except (ValueError, TypeError):
        try:
            return abs(float(value))
        except (ValueError, TypeError):
            return value


@register.filter(name="phone_local_info")
def phone_local_info(raw_phone: str) -> str:
    """
    Подсказка под телефоном: текущее время и регион/город, определённые по номеру.
    Пример: "19:26 - Тюменская обл".
    """
    num = _parse_phone(str(raw_phone or "").strip())
    if not num or phonenumbers is None:
        return ""

    try:
        tz_name = None
        if _pn_tz is not None:
            tzs = _pn_tz.time_zones_for_number(num) or ()
            # Берём только РФ-таймзоны, чтобы не показывать Almaty и т.п.
            for z in tzs:
                if z in _RUS_TZ_SET:
                    tz_name = z
                    break

        region = ""
        if _pn_geocoder is not None:
            region = (_pn_geocoder.description_for_number(num, "ru") or "").strip()

        # Если регион/город не на кириллице — не показываем (будет fallback на TZ компании).
        if region and not re.search(r"[А-Яа-яЁё]", region):
            region = ""

        hhmm = ""
        if tz_name:
            try:
                hhmm = timezone.now().astimezone(_zoneinfo(tz_name)).strftime("%H:%M")
            except Exception:
                hhmm = ""

        # Если tz_name не РФ — вообще не показываем (иначе вводит в заблуждение).
        if not tz_name:
            return ""

        # Если регион не определился — показываем TZ-лейбл
        if not region:
            region = tz_label(tz_name)

        if not hhmm and not region:
            return ""
        if not region:
            return hhmm
        if not hhmm:
            return region
        return f"{hhmm} - {region}"
    except Exception:
        return ""

