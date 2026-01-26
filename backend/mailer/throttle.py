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


def _hit(key: str, window_seconds: int) -> int:
    """
    Выполняет hit (инкремент счетчика) и возвращает новое значение.
    Пробует cache, при ошибке переключается на in-memory fallback.
    
    Returns:
        Новое значение счетчика (1, 2, 3, ...)
    """
    try:
        # Сначала пытаемся создать ключ, если его нет
        cache.add(key, 0, timeout=window_seconds)
        
        # Инкрементируем счетчик
        try:
            count = cache.incr(key)
        except ValueError:
            # Ключ не найден (хотя мы делали add) - создаем через set
            cache.set(key, 1, timeout=window_seconds)
            count = 1
        
        # ВАЖНО: некоторые бекенды "успешно" возвращают 0/None — это считаем нерабочим cache
        # count <= 0 должен форсировать fallback
        if not isinstance(count, int) or count <= 0:
            raise RuntimeError(f"cache.incr returned invalid count={count!r}")
        
        # Обновляем TTL на случай, если ключ уже существовал
        cache.touch(key, timeout=window_seconds)
        
        return count
    
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
        - current_count: Текущее количество запросов в окне (1, 2, 3, ...)
        - reason: None если норма, "throttled" если лимит превышен
    """
    cache_key = f"mailer:throttle:{action}:user:{user_id}"
    
    # 1) Сначала увеличиваем счётчик (hit) - всегда выполняется
    count = _hit(cache_key, window_seconds)
    
    # 2) Потом проверяем, превышен ли лимит
    is_throttled = count > max_requests
    reason = "throttled" if is_throttled else None
    
    if is_throttled:
        logger.warning(
            f"User {user_id} throttled for action {action}: {count}/{max_requests}",
            extra={
                "user_id": str(user_id),
                "action": action,
                "current_count": count,
                "max_requests": max_requests,
            }
        )
    
    return is_throttled, count, reason
