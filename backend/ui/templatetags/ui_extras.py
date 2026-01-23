import re
from functools import lru_cache
from zoneinfo import ZoneInfo

from django import template
from django.utils import timezone

register = template.Library()

_HAS_SCHEME_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.-]*://")
_INN_RE = re.compile(r"\b(\d{10}|\d{12})\b")


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
    Возвращает список ИНН (10/12 цифр) из строки.
    Нужно для красивого отображения множественных ИНН в шаблонах.
    """
    if value is None:
        return []
    s = str(value).strip()
    if not s:
        return []
    out: list[str] = []
    seen = set()
    for m in _INN_RE.finditer(s):
        inn = m.group(1)
        if inn not in seen:
            out.append(inn)
            seen.add(inn)
    return out


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

