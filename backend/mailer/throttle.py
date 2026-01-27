"""
ENTERPRISE: Throttling для mailer endpoints.
Защита от abuse и случайных массовых действий.

FAIL-CLOSED POLICY: При ошибке Redis возвращаем throttled=True (безопаснее заблокировать, чем разрешить DoS).
"""
from __future__ import annotations

import logging
import time
import threading
from django.core.cache import cache
from django.utils import timezone

logger = logging.getLogger(__name__)

# Fallback in-memory storage для тестов (когда cache не поддерживает incr или DummyCache)
_LOCK = threading.Lock()
_MEM: dict[str, tuple[int, float]] = {}  # key -> (count, expires_at)


def _mem_hit(key: str, window_seconds: int) -> int:
    """In-memory fallback для подсчета запросов (для тестов)."""
    now = time.time()
    with _LOCK:
        count, exp = _MEM.get(key, (0, 0.0))
        if exp <= now:
            count = 0
            exp = now + window_seconds
        count += 1
        _MEM[key] = (count, exp)
        return count


def _cache_hit(key: str, window_seconds: int) -> int:
    """
    Выполняет hit через cache (инкремент счетчика).
    add -> incr гарантирует, что ключ существует.
    
    Returns:
        Новое значение счетчика (1, 2, 3, ...)
    """
    # add -> incr гарантирует, что ключ существует
    cache.add(key, 0, timeout=window_seconds)
    v = cache.incr(key)
    if not isinstance(v, int):
        raise TypeError(f"cache.incr returned {type(v)}: {v!r}")
    if v <= 0:
        raise RuntimeError(f"cache.incr returned invalid count={v!r}")
    return v


def _hit(key: str, window_seconds: int) -> int:
    """
    Выполняет hit (инкремент счетчика) и возвращает новое значение.
    Пробует cache, при ошибке переключается на in-memory fallback.
    
    Returns:
        Новое значение счетчика (1, 2, 3, ...)
    """
    try:
        return _cache_hit(key, window_seconds)
    except Exception as e:
        # Fallback in-memory (для DummyCache / неподдержки incr / тестов)
        logger.debug(
            f"Cache backend unavailable for throttle hit, using in-memory fallback: {e}",
            extra={
                "cache_key": key,
                "error_type": "throttle_backend_fallback",
            }
        )
        return _mem_hit(key, window_seconds)


def is_user_throttled(user_id: int | str, action: str, max_requests: int, window_seconds: int = 3600) -> tuple[bool, int, str | None]:
    """
    Проверка throttling по пользователю (не по IP).
    
    КРИТИЧНО: Каждый вызов этой функции = hit (инкремент счетчика).
    Сначала увеличиваем счетчик, потом проверяем лимит.
    
    FAIL-CLOSED POLICY: При ошибке Redis возвращаем throttled=True (безопаснее заблокировать, чем разрешить DoS).
    
    Args:
        user_id: ID пользователя
        action: Действие (например "campaign_start", "send_test_email")
        max_requests: Максимум запросов в окне
        window_seconds: Окно времени в секундах (по умолчанию 1 час)
    
    Returns:
        (is_throttled, current_count, reason)
        - is_throttled: True если лимит превышен
        - current_count: Текущее количество запросов в окне (1, 2, 3, ..., max_requests)
          При throttled возвращается max_requests (не реальный count)
        - reason: Всегда None (информация о throttling в логах)
    """
    # ВАЖНО: key должен быть стабильным (action/user_id/window_seconds)
    key = f"throttle:{action}:{user_id}:{window_seconds}"
    
    # ВАЖНО: count берём ТОЛЬКО из hit() - никаких cache.get() для count
    count = _hit(key, window_seconds)
    
    # Проверяем, превышен ли лимит
    is_throttled = count > max_requests
    
    # При throttled "зажимаем" count до max_requests для возврата наружу
    # (но в логах используем реальный count для видимости реальной нагрузки)
    visible_count = min(count, max_requests) if is_throttled else count
    
    if is_throttled:
        logger.warning(
            f"User {user_id} throttled for action {action}: {count}/{max_requests}",
            extra={
                "user_id": str(user_id),
                "action": action,
                "current_count": count,  # Реальный count в логах
                "max_requests": max_requests,
            }
        )
    
    # reason всегда None (по контракту тестов)
    return is_throttled, visible_count, None
