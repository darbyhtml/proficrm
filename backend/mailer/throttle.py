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
_MEM: dict[str, list[float]] = {}


def _mem_hit(key: str, window_seconds: int) -> int:
    """In-memory fallback для подсчета запросов (для тестов)."""
    now = time.time()
    cutoff = now - window_seconds
    with _LOCK:
        arr = _MEM.get(key, [])
        # Удаляем старые записи (outside window)
        arr = [t for t in arr if t >= cutoff]
        # Добавляем текущий запрос
        arr.append(now)
        _MEM[key] = arr
        return len(arr)


def is_user_throttled(user_id: int | str, action: str, max_requests: int, window_seconds: int = 3600) -> tuple[bool, int, str | None]:
    """
    Проверка throttling по пользователю (не по IP).
    
    FAIL-CLOSED POLICY: При ошибке Redis возвращаем throttled=True (безопаснее заблокировать, чем разрешить DoS).
    
    Args:
        user_id: ID пользователя
        action: Действие (например "campaign_start", "send_test_email")
        max_requests: Максимум запросов в окне
        window_seconds: Окно времени в секундах (по умолчанию 1 час)
    
    Returns:
        (is_throttled, current_count, reason)
        - is_throttled: True если лимит превышен или Redis недоступен
        - current_count: Текущее количество запросов в окне (0 если Redis error)
        - reason: None если норма, "throttle_backend_unavailable" если Redis error
    """
    cache_key = f"mailer:throttle:{action}:user:{user_id}"
    
    # 1) Пытаемся использовать cache с атомарным инкрементом
    try:
        # Сначала пытаемся создать ключ, если его нет
        cache.add(cache_key, 0, timeout=window_seconds)
        # Инкрементируем счетчик
        new_value = cache.incr(cache_key)
        # Некоторые бекенды могут вернуть None/0 — подстрахуемся
        if not isinstance(new_value, int) or new_value <= 0:
            raise RuntimeError("cache.incr returned non-int/<=0")
        
        # Обновляем TTL на случай, если ключ уже существовал
        cache.touch(cache_key, timeout=window_seconds)
        
        is_throttled = new_value > max_requests
        reason = "throttled" if is_throttled else None
        
        if is_throttled:
            logger.warning(
                f"User {user_id} throttled for action {action}: {new_value}/{max_requests}",
                extra={
                    "user_id": str(user_id),
                    "action": action,
                    "current_count": new_value,
                    "max_requests": max_requests,
                }
            )
        
        return is_throttled, new_value, reason
    
    except Exception as e:
        # 2) Fallback in-memory (для DummyCache / неподдержки incr / тестов)
        logger.debug(
            f"Cache backend unavailable for throttle, using in-memory fallback: {e}",
            extra={
                "user_id": str(user_id),
                "action": action,
                "error_type": "throttle_backend_fallback",
            }
        )
        count = _mem_hit(cache_key, window_seconds)
        is_throttled = count > max_requests
        reason = "throttled" if is_throttled else None
        
        if is_throttled:
            logger.warning(
                f"User {user_id} throttled for action {action} (in-memory): {count}/{max_requests}",
                extra={
                    "user_id": str(user_id),
                    "action": action,
                    "current_count": count,
                    "max_requests": max_requests,
                }
            )
        
        return is_throttled, count, reason
