"""
Модуль безопасности: защита от брутфорса, rate limiting, логирование подозрительной активности.
"""
from __future__ import annotations

import time
from datetime import datetime, timedelta
from typing import Optional

from django.core.cache import cache
from django.contrib.auth import get_user_model
from django.utils import timezone
from django.conf import settings

from audit.models import ActivityEvent
from audit.service import log_event

User = get_user_model()

# Настройки защиты от брутфорса
MAX_LOGIN_ATTEMPTS = 5  # Максимум неудачных попыток
LOCKOUT_DURATION_SECONDS = 900  # 15 минут блокировки
RATE_LIMIT_LOGIN_PER_MINUTE = 5  # Максимум попыток входа в минуту с одного IP
RATE_LIMIT_API_PER_MINUTE = 60  # Максимум API запросов в минуту с одного IP


def get_client_ip(request) -> str:
    """Получить IP адрес клиента с учетом прокси."""
    x_forwarded_for = request.META.get("HTTP_X_FORWARDED_FOR")
    if x_forwarded_for:
        ip = x_forwarded_for.split(",")[0].strip()
    else:
        ip = request.META.get("REMOTE_ADDR", "unknown")
    return ip


def is_ip_rate_limited(ip: str, key_prefix: str, max_requests: int, window_seconds: int = 60) -> bool:
    """
    Проверка rate limiting по IP адресу.
    
    Args:
        ip: IP адрес клиента
        key_prefix: Префикс ключа в кеше
        max_requests: Максимум запросов
        window_seconds: Окно времени в секундах
    
    Returns:
        True если лимит превышен, False если можно продолжить
    """
    cache_key = f"rate_limit:{key_prefix}:{ip}"
    current = cache.get(cache_key, 0)
    
    if current >= max_requests:
        return True
    
    cache.set(cache_key, current + 1, window_seconds)
    return False


def get_user_lockout_key(username: str) -> str:
    """Ключ кеша для блокировки пользователя."""
    return f"login_lockout:{username.lower()}"


def is_user_locked_out(username: str) -> bool:
    """Проверить, заблокирован ли пользователь из-за неудачных попыток входа."""
    lockout_key = get_user_lockout_key(username)
    return cache.get(lockout_key, False)


def record_failed_login_attempt(username: str, ip: str, reason: str = "invalid_credentials") -> None:
    """
    Записать неудачную попытку входа и заблокировать пользователя при превышении лимита.
    
    Args:
        username: Имя пользователя
        ip: IP адрес
        reason: Причина неудачи (invalid_credentials, locked_out, rate_limited)
    """
    username_lower = username.lower()
    attempts_key = f"login_attempts:{username_lower}"
    
    # Получаем текущее количество попыток
    attempts = cache.get(attempts_key, 0)
    attempts += 1
    
    # Сохраняем количество попыток
    cache.set(attempts_key, attempts, LOCKOUT_DURATION_SECONDS)
    
    # Логируем попытку
    try:
        user = User.objects.filter(username__iexact=username).first()
        log_event(
            actor=None,
            verb=ActivityEvent.Verb.UPDATE,
            entity_type="security",
            entity_id=f"login_failed:{username_lower}",
            message=f"Неудачная попытка входа: {reason}",
            meta={
                "username": username_lower,
                "ip": ip,
                "attempts": attempts,
                "reason": reason,
            },
        )
    except Exception:
        pass  # Не падаем, если логирование не удалось
    
    # Блокируем пользователя при превышении лимита
    if attempts >= MAX_LOGIN_ATTEMPTS:
        lockout_key = get_user_lockout_key(username_lower)
        cache.set(lockout_key, True, LOCKOUT_DURATION_SECONDS)
        
        # Логируем блокировку
        try:
            log_event(
                actor=None,
                verb=ActivityEvent.Verb.UPDATE,
                entity_type="security",
                entity_id=f"account_locked:{username_lower}",
                message=f"Аккаунт заблокирован из-за {attempts} неудачных попыток входа",
                meta={
                    "username": username_lower,
                    "ip": ip,
                    "attempts": attempts,
                    "lockout_duration": LOCKOUT_DURATION_SECONDS,
                },
            )
        except Exception:
            pass


def clear_login_attempts(username: str) -> None:
    """Очистить счетчик неудачных попыток после успешного входа."""
    username_lower = username.lower()
    attempts_key = f"login_attempts:{username_lower}"
    lockout_key = get_user_lockout_key(username_lower)
    cache.delete(attempts_key)
    cache.delete(lockout_key)


def get_remaining_lockout_time(username: str) -> Optional[int]:
    """Получить оставшееся время блокировки в секундах."""
    lockout_key = get_user_lockout_key(username.lower())
    ttl = cache.ttl(lockout_key)
    return ttl if ttl > 0 else None

