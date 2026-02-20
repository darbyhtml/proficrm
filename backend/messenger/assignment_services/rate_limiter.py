"""
Rate Limiter для ограничения количества назначений операторам (по образцу Chatwoot).

Предотвращает перегрузку одного оператора множественными назначениями.
"""

from typing import Optional
from django.core.cache import cache
from django.utils import timezone
from datetime import timedelta


class AssignmentRateLimiter:
    """
    Rate Limiter для назначений операторов (по образцу Chatwoot).
    
    Ограничивает количество назначений оператору за период времени.
    """
    
    KEY_PREFIX = "messenger:assignment_rate_limit"
    DEFAULT_LIMIT = 10  # Максимум назначений
    DEFAULT_WINDOW = 60  # За период (в секундах)
    
    def __init__(self, limit: int = DEFAULT_LIMIT, window: int = DEFAULT_WINDOW):
        """
        Args:
            limit: Максимум назначений за период
            window: Период в секундах
        """
        self.limit = limit
        self.window = window
    
    def check_limit(self, user_id: int) -> bool:
        """
        Проверить, не превышен ли лимит назначений для оператора (по образцу Chatwoot).
        
        Args:
            user_id: ID оператора
        
        Returns:
            True если лимит не превышен (можно назначить), False если превышен
        
        Note:
            Проверяет количество назначений за период времени (window).
            Использует Redis для хранения счётчиков с TTL.
        """
        key = self._get_key(user_id)
        count = cache.get(key, 0)
        return count < self.limit
    
    def increment(self, user_id: int) -> None:
        """
        Увеличить счётчик назначений для оператора (по образцу Chatwoot).
        
        Args:
            user_id: ID оператора
        
        Note:
            Увеличивает счётчик назначений и устанавливает TTL равный window.
            Автоматически сбрасывается после истечения window.
        """
        key = self._get_key(user_id)
        count = cache.get(key, 0)
        cache.set(key, count + 1, timeout=self.window)
    
    def reset(self, user_id: int):
        """
        Сбросить счётчик назначений для оператора.
        
        Args:
            user_id: ID оператора
        """
        key = self._get_key(user_id)
        cache.delete(key)
    
    def _get_key(self, user_id: int) -> str:
        """Получить ключ Redis для оператора."""
        return f"{self.KEY_PREFIX}:{user_id}"


# Глобальный экземпляр с настройками по умолчанию
default_rate_limiter = AssignmentRateLimiter()
