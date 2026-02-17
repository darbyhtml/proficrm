from __future__ import annotations

import secrets
from dataclasses import dataclass
from typing import Any, Optional

from django.conf import settings
from django.core.cache import cache
from django.http import Http404
from django.utils import timezone
from rest_framework import exceptions
from urllib.parse import urlparse
import random
import threading
import time as _time


# ---------------------------------------------------------------------------
# Fallback cache (на случай падения Redis/cache backend)
# ---------------------------------------------------------------------------

_FALLBACK_LOCK = threading.Lock()
_FALLBACK_STORE: dict[str, tuple[float, Any]] = {}


def _fallback_get(key: str):
    now = _time.time()
    with _FALLBACK_LOCK:
        item = _FALLBACK_STORE.get(key)
        if not item:
            return None
        exp, val = item
        if exp and exp < now:
            _FALLBACK_STORE.pop(key, None)
            return None
        return val


def _fallback_set(key: str, value: Any, timeout: int | None = None):
    exp = 0.0
    if timeout and timeout > 0:
        exp = _time.time() + float(timeout)
    with _FALLBACK_LOCK:
        _FALLBACK_STORE[key] = (exp, value)


def _fallback_delete(key: str):
    with _FALLBACK_LOCK:
        _FALLBACK_STORE.pop(key, None)


def safe_cache_get(key: str, default=None):
    try:
        return cache.get(key, default)
    except Exception:
        v = _fallback_get(key)
        return default if v is None else v


def safe_cache_set(key: str, value: Any, timeout: int | None = None):
    try:
        cache.set(key, value, timeout=timeout)
        return True
    except Exception:
        _fallback_set(key, value, timeout=timeout)
        return False


def safe_cache_delete(key: str):
    try:
        cache.delete(key)
    except Exception:
        _fallback_delete(key)


def ensure_messenger_enabled_api():
    """
    Единая проверка feature-флага для DRF/Widget API.

    Если messenger отключён, выбрасывает DRF-исключение 404/disabled,
    не нарушая стабильность маршрутов.
    """
    if not getattr(settings, "MESSENGER_ENABLED", False):
        raise exceptions.NotFound(detail="Messenger disabled")


def ensure_messenger_enabled_view():
    """
    Единая проверка feature-флага для Django views (UI).
    """
    if not getattr(settings, "MESSENGER_ENABLED", False):
        raise Http404("Messenger disabled")


def is_messenger_enabled() -> bool:
    return bool(getattr(settings, "MESSENGER_ENABLED", False))


# ---------------------------------------------------------------------------
# Widget session tokens (для защиты публичного widget API)
# ---------------------------------------------------------------------------

WIDGET_SESSION_TTL_SECONDS = 60 * 60 * 24  # 24 часа по умолчанию


@dataclass
class WidgetSession:
    token: str
    inbox_id: int
    conversation_id: int
    contact_id: str


def _widget_session_cache_key(token: str) -> str:
    return f"messenger:widget_session:{token}"


def create_widget_session(*, inbox_id: int, conversation_id: int, contact_id: str) -> WidgetSession:
    """
    Создаёт widget_session_token и сохраняет минимальный контекст в cache/Redis.

    Используется Widget API:
    - /api/widget/bootstrap/ создаёт и возвращает token;
    - /api/widget/send/ и /api/widget/poll/ принимают token и валидируют его.
    """
    token = secrets.token_urlsafe(32)
    data = {
        "inbox_id": inbox_id,
        "conversation_id": conversation_id,
        "contact_id": contact_id,
    }
    safe_cache_set(_widget_session_cache_key(token), data, timeout=WIDGET_SESSION_TTL_SECONDS)
    return WidgetSession(token=token, **data)


def get_widget_session(token: str) -> Optional[WidgetSession]:
    if not token:
        return None
    data = safe_cache_get(_widget_session_cache_key(token))
    if not data:
        return None
    return WidgetSession(token=token, **data)


def delete_widget_session(token: str) -> None:
    if not token:
        return
    safe_cache_delete(_widget_session_cache_key(token))


# ---------------------------------------------------------------------------
# Рабочие часы (для автоназначения и сообщения в виджете)
# ---------------------------------------------------------------------------

def is_within_working_hours(inbox: "Inbox") -> bool:
    """
    Проверяет, попадает ли текущее время в рабочие часы inbox.

    Настройки в inbox.settings["working_hours"]:
    - enabled: bool — включена ли проверка
    - tz: str — часовой пояс (например "Europe/Moscow")
    - 1..7: список [start, end] в формате "HH:MM" (1=пн, 7=вс); отсутствие или null = выходной

    Returns:
        True, если проверка выключена или текущее время в расписании.
    """
    from zoneinfo import ZoneInfo

    wh = (inbox.settings or {}).get("working_hours") or {}
    if not wh.get("enabled"):
        return True

    tz_name = wh.get("tz", "UTC")
    try:
        tz = ZoneInfo(tz_name)
    except Exception:
        tz = timezone.get_current_timezone()

    now = timezone.now().astimezone(tz)
    # Python: 0=Monday, 6=Sunday → ключи 1..7 в настройках
    day_key = str(now.isoweekday())  # 1=Monday, 7=Sunday
    schedule = wh.get("schedule") or wh
    day_slots = schedule.get(day_key) if isinstance(schedule, dict) else None
    if not day_slots or not isinstance(day_slots, (list, tuple)) or len(day_slots) < 2:
        return False

    try:
        start_str, end_str = day_slots[0], day_slots[1]
        start_parts = start_str.strip().split(":")
        end_parts = end_str.strip().split(":")
        start_min = int(start_parts[0]) * 60 + (int(start_parts[1]) if len(start_parts) > 1 else 0)
        end_min = int(end_parts[0]) * 60 + (int(end_parts[1]) if len(end_parts) > 1 else 0)
    except (ValueError, TypeError, IndexError):
        return False

    now_min = now.hour * 60 + now.minute
    if start_min <= end_min:
        return start_min <= now_min < end_min
    # через полночь
    return now_min >= start_min or now_min < end_min


# ---------------------------------------------------------------------------
# Вложения виджета: лимиты и валидация
# ---------------------------------------------------------------------------

DEFAULT_ATTACHMENT_MAX_SIZE_BYTES = 5 * 1024 * 1024  # 5 MB
DEFAULT_ATTACHMENT_ALLOWED_TYPES = [
    "image/jpeg",
    "image/png",
    "image/gif",
    "image/webp",
    "application/pdf",
]


def get_attachment_settings(inbox) -> dict:
    """
    Настройки вложений из inbox.settings.attachments или из settings/ env.
    Возвращает: enabled, max_file_size_bytes, allowed_content_types (list).
    """
    cfg = (inbox.settings or {}).get("attachments") or {}
    enabled = cfg.get("enabled", True)
    max_mb = cfg.get("max_file_size_mb")
    if max_mb is None:
        max_bytes = getattr(
            settings, "MESSENGER_ATTACHMENT_MAX_SIZE_BYTES", DEFAULT_ATTACHMENT_MAX_SIZE_BYTES
        )
    else:
        try:
            max_bytes = int(float(max_mb) * 1024 * 1024)
        except (TypeError, ValueError):
            max_bytes = DEFAULT_ATTACHMENT_MAX_SIZE_BYTES
    allowed = cfg.get("allowed_content_types") or cfg.get("allowed_types")
    if allowed is None:
        allowed = getattr(
            settings,
            "MESSENGER_ATTACHMENT_ALLOWED_TYPES",
            DEFAULT_ATTACHMENT_ALLOWED_TYPES,
        )
    if not isinstance(allowed, (list, tuple)):
        allowed = list(allowed) if allowed else DEFAULT_ATTACHMENT_ALLOWED_TYPES
    return {
        "enabled": bool(enabled),
        "max_file_size_bytes": max(0, max_bytes),
        "allowed_content_types": list(allowed),
    }


def is_content_type_allowed(content_type: str, allowed_list: list) -> bool:
    """
    Проверка MIME: точное совпадение или wildcard image/*.
    """
    if not content_type:
        return False
    ct = (content_type or "").strip().lower().split(";")[0].strip()
    for allowed in allowed_list:
        a = (allowed or "").strip().lower()
        if a == ct:
            return True
        if a == "image/*" and ct.startswith("image/"):
            return True
    return False


def build_message_attachments_payload(message, request, widget_token: str, widget_session_token: str) -> list:
    """
    Список вложений сообщения для ответа виджета (poll/bootstrap).
    Каждый элемент: id, url, original_name, content_type, size.
    """
    from urllib.parse import urlencode

    result = []
    for att in message.attachments.all():
        path = f"/api/widget/attachment/{att.id}/"
        qs = urlencode({"widget_token": widget_token, "widget_session_token": widget_session_token})
        url = request.build_absolute_uri(path) + "?" + qs if request else (path + "?" + qs)
        result.append({
            "id": att.id,
            "url": url,
            "original_name": att.original_name or "",
            "content_type": (att.content_type or "")[:120],
            "size": att.size or 0,
        })
    return result


# ---------------------------------------------------------------------------
# Безопасность виджета: allowlist доменов (Origin/Referer)
# ---------------------------------------------------------------------------


def _normalize_allowed_domain(raw: str) -> str:
    v = (raw or "").strip().lower()
    if not v:
        return ""
    # Разрешаем ввод вида https://example.com — берём hostname
    if "://" in v:
        try:
            v = (urlparse(v).hostname or "").strip().lower()
        except Exception:
            pass
    # Срезаем порт, если ввели example.com:443
    if ":" in v and not v.startswith("*."):
        v = v.split(":", 1)[0].strip()
    return v


def get_widget_allowed_domains(inbox) -> list[str]:
    cfg = (inbox.settings or {}).get("security") or {}
    domains = cfg.get("allowed_domains") or []
    if not isinstance(domains, (list, tuple)):
        return []
    result: list[str] = []
    for d in domains:
        nd = _normalize_allowed_domain(str(d))
        if nd:
            result.append(nd)
    # уникализируем, сохраняя порядок
    seen = set()
    out = []
    for d in result:
        if d not in seen:
            out.append(d)
            seen.add(d)
    return out


def _extract_request_origin_host(request) -> str:
    origin = (request.META.get("HTTP_ORIGIN") or "").strip()
    referer = (request.META.get("HTTP_REFERER") or "").strip()
    src = origin or referer
    if not src:
        return ""
    try:
        host = (urlparse(src).hostname or "").strip().lower()
    except Exception:
        host = ""
    return host


def is_origin_allowed(origin_host: str, allowed_domains: list[str]) -> bool:
    if not allowed_domains:
        return True
    if not origin_host:
        return False
    host = origin_host.strip().lower()
    for allowed in allowed_domains:
        a = (allowed or "").strip().lower()
        if not a:
            continue
        if a.startswith("*."):
            suffix = a[1:]  # ".example.com"
            if host.endswith(suffix) and host != suffix.lstrip("."):
                return True
        else:
            if host == a:
                return True
    return False


def enforce_widget_origin_allowed(request, inbox) -> None:
    """
    Если в inbox.settings.security.allowed_domains задан allowlist,
    блокируем запросы виджета, пришедшие с другого домена.
    """
    allowed = get_widget_allowed_domains(inbox)
    if not allowed:
        return
    origin_host = _extract_request_origin_host(request)
    if not is_origin_allowed(origin_host, allowed):
        raise exceptions.PermissionDenied(detail="Widget domain is not allowed.")


# ---------------------------------------------------------------------------
# Anti-spam CAPTCHA (math) for widget
# ---------------------------------------------------------------------------

CAPTCHA_TTL_SECONDS = 10 * 60  # 10 минут
CAPTCHA_IP_WINDOW_SECONDS = 10 * 60
CAPTCHA_IP_THRESHOLD = 60  # после N bootstrap/send за окно — требуем капчу


def get_client_ip(request) -> str:
    xff = (request.META.get("HTTP_X_FORWARDED_FOR") or "").split(",")[0].strip()
    return xff or (request.META.get("REMOTE_ADDR") or "").strip()


def _ip_counter_key(ip: str) -> str:
    return f"messenger:captcha:ip:{ip}"


def _captcha_key(token: str) -> str:
    return f"messenger:captcha:token:{token}"


def _captcha_passed_key(session_token: str) -> str:
    return f"messenger:captcha:passed:{session_token}"


def mark_ip_activity_for_captcha(ip: str) -> int:
    if not ip:
        return 0
    key = _ip_counter_key(ip)
    try:
        current = safe_cache_get(key) or 0
        current = int(current) + 1
        safe_cache_set(key, current, timeout=CAPTCHA_IP_WINDOW_SECONDS)
        return current
    except Exception:
        return 0


def should_require_captcha(ip: str) -> bool:
    if not ip:
        return False
    try:
        current = int(safe_cache_get(_ip_counter_key(ip)) or 0)
        return current >= CAPTCHA_IP_THRESHOLD
    except Exception:
        return False


def create_math_captcha() -> tuple[str, str]:
    """
    Возвращает (token, question). Ответ хранится в cache.
    """
    a = random.randint(2, 9)
    b = random.randint(2, 9)
    op = random.choice(["+", "-"])
    if op == "-" and b > a:
        a, b = b, a
    answer = a + b if op == "+" else a - b
    token = secrets.token_urlsafe(16)
    safe_cache_set(_captcha_key(token), str(answer), timeout=CAPTCHA_TTL_SECONDS)
    return token, f"{a} {op} {b} = ?"


def verify_math_captcha(token: str, answer: str) -> bool:
    if not token or answer is None:
        return False
    try:
        expected = safe_cache_get(_captcha_key(token))
        if expected is None:
            return False
        ok = str(answer).strip() == str(expected).strip()
        if ok:
            safe_cache_delete(_captcha_key(token))
        return ok
    except Exception:
        return False


def mark_captcha_passed(session_token: str) -> None:
    if not session_token:
        return
    try:
        safe_cache_set(_captcha_passed_key(session_token), "1", timeout=WIDGET_SESSION_TTL_SECONDS)
    except Exception:
        pass


def is_captcha_passed(session_token: str) -> bool:
    if not session_token:
        return False
    try:
        return safe_cache_get(_captcha_passed_key(session_token)) == "1"
    except Exception:
        return False

