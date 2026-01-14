"""
Модуль безопасности: защита от брутфорса, rate limiting, логирование подозрительной активности.
"""
from __future__ import annotations

import time
import logging
from datetime import datetime, timedelta
from typing import Optional

from django.core.cache import cache
from django.contrib.auth import get_user_model
from django.utils import timezone
from django.conf import settings

from audit.models import ActivityEvent
from audit.service import log_event

User = get_user_model()
logger = logging.getLogger(__name__)

# Настройки защиты от брутфорса
MAX_LOGIN_ATTEMPTS = 5  # Максимум неудачных попыток
LOCKOUT_DURATION_SECONDS = 900  # 15 минут блокировки
RATE_LIMIT_LOGIN_PER_MINUTE = 5  # Максимум попыток входа в минуту с одного IP
RATE_LIMIT_API_PER_MINUTE = 60  # Максимум API запросов в минуту с одного IP


def _is_valid_ip(ip: str) -> bool:
    """
    Проверка валидности IPv4 или IPv6 адреса.
    """
    import ipaddress
    try:
        ipaddress.ip_address(ip.strip())
        return True
    except (ValueError, AttributeError):
        return False


def get_client_ip(request) -> str:
    """
    Получить IP адрес клиента с учетом прокси.
    Безопасно: доверяет X-Forwarded-For только если REMOTE_ADDR принадлежит нашим прокси (allowlist).
    Валидирует IPv4/IPv6, иначе fallback на REMOTE_ADDR.
    
    Args:
        request: Django HttpRequest объект
        
    Returns:
        IP адрес клиента (валидный IPv4/IPv6) или "unknown"
    """
    remote_addr = request.META.get("REMOTE_ADDR", "").strip()
    
    # Получаем список доверенных IP прокси из settings
    # В production: установить через DJANGO_PROXY_IPS (через запятую)
    proxy_ips = getattr(settings, "PROXY_IPS", [])
    if isinstance(proxy_ips, str):
        proxy_ips = [ip.strip() for ip in proxy_ips.split(",") if ip.strip()]
    
    # Если REMOTE_ADDR принадлежит нашим прокси - доверяем X-Forwarded-For
    if remote_addr in proxy_ips:
        x_forwarded_for = request.META.get("HTTP_X_FORWARDED_FOR", "").strip()
        if x_forwarded_for:
            # Берем первый IP из цепочки (клиент)
            # Формат: "1.2.3.4, 10.0.0.1" или "1.2.3.4,10.0.0.1"
            first_ip = x_forwarded_for.split(",")[0].strip()
            if first_ip and _is_valid_ip(first_ip):
                return first_ip
            # Если первый IP невалидный - пробуем следующие
            for ip_candidate in x_forwarded_for.split(",")[1:]:
                ip_candidate = ip_candidate.strip()
                if ip_candidate and _is_valid_ip(ip_candidate):
                    return ip_candidate
    
    # Иначе используем REMOTE_ADDR (защита от spoofing)
    # Валидируем REMOTE_ADDR перед возвратом
    if remote_addr and _is_valid_ip(remote_addr):
        return remote_addr
    
    return "unknown"


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
    except Exception as e:
        logger.warning(f"Failed to log failed login attempt for {username_lower}: {e}", exc_info=True)
    
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
        except Exception as e:
            logger.warning(f"Failed to log account lockout for {username_lower}: {e}", exc_info=True)


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

