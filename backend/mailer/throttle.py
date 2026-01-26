"""
ENTERPRISE: Throttling для mailer endpoints.
Защита от abuse и случайных массовых действий.

FAIL-CLOSED POLICY: При ошибке Redis возвращаем throttled=True (безопаснее заблокировать, чем разрешить DoS).
"""
from __future__ import annotations

import logging
from django.core.cache import cache
from django.utils import timezone

logger = logging.getLogger(__name__)


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
    
    try:
        current = cache.get(cache_key, 0)
        
        if current >= max_requests:
            logger.warning(
                f"User {user_id} throttled for action {action}: {current}/{max_requests}",
                extra={
                    "user_id": str(user_id),
                    "action": action,
                    "current_count": current,
                    "max_requests": max_requests,
                }
            )
            return True, current, None
        
        # Атомарно увеличиваем счетчик
        try:
            new_value = cache.incr(cache_key)
            if new_value == 1:
                # Первый запрос - устанавливаем TTL
                cache.touch(cache_key, timeout=window_seconds)
            else:
                # Обновляем TTL на случай, если ключ уже существовал
                cache.touch(cache_key, timeout=window_seconds)
        except (ValueError, AttributeError):
            # Fallback для backends, которые не поддерживают incr
            if cache.add(cache_key, 1, timeout=window_seconds):
                new_value = 1
            else:
                new_value = cache.incr(cache_key)
                cache.touch(cache_key, timeout=window_seconds)
        
        return False, new_value, None
    
    except Exception as e:
        # ENTERPRISE: FAIL-CLOSED при ошибке Redis (безопаснее заблокировать, чем разрешить DoS)
        logger.error(
            f"Throttle backend (Redis) unavailable for action {action}, user {user_id}: {e}",
            exc_info=True,
            extra={
                "user_id": str(user_id),
                "action": action,
                "error_type": "throttle_backend_error",
            }
        )
        # Возвращаем throttled=True для безопасности
        return True, 0, "throttle_backend_unavailable"
