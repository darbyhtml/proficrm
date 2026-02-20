"""
Online Status Tracker для операторов (по образцу Chatwoot).

Отслеживает онлайн статус операторов в Redis для быстрого доступа.
Используется для фильтрации доступных операторов при автоназначении.
"""

from typing import Dict, Set, Optional
from django.core.cache import cache
from django.utils import timezone
from datetime import timedelta
from accounts.models import User
from messenger.models import AgentProfile


class OnlineStatusTracker:
    """
    Online Status Tracker (по образцу Chatwoot).
    
    Отслеживает онлайн статус операторов в Redis для быстрого доступа.
    Используется для фильтрации доступных операторов при автоназначении.
    """
    
    KEY_PREFIX = "messenger:agent:online"
    STATUS_KEY_PREFIX = "messenger:agent:status"
    TTL = 60 * 5  # 5 минут (оператор считается онлайн если был активен за последние 5 минут)
    
    @classmethod
    def get_available_users(cls, branch_id: Optional[int] = None) -> Dict[int, str]:
        """
        Получить словарь доступных операторов (по образцу Chatwoot).
        
        Args:
            branch_id: ID филиала (опционально, для фильтрации)
        
        Returns:
            Словарь {user_id: status}, где status = 'online', 'away', 'busy', 'offline'
        
        Note:
            Использует кэширование в Redis для быстрого доступа.
            Результат кэшируется на TTL секунд (по умолчанию 5 минут).
        """
        # Получаем всех операторов с активными профилями
        qs = User.objects.filter(
            is_active=True,
            agent_profile__isnull=False
        )
        
        if branch_id:
            qs = qs.filter(branch_id=branch_id)
        
        available_users = {}
        
        for user in qs.select_related('agent_profile'):
            status = cls.get_status(user.id)
            if status:
                available_users[user.id] = status
        
        return available_users
    
    @classmethod
    def get_status(cls, user_id: int) -> Optional[str]:
        """
        Получить статус оператора.
        
        По образцу Chatwoot: проверяет Redis и AgentProfile.
        
        Args:
            user_id: ID оператора
        
        Returns:
            Статус: 'online', 'away', 'busy', 'offline' или None
        """
        # Сначала проверяем Redis (быстрый доступ)
        redis_key = f"{cls.STATUS_KEY_PREFIX}:{user_id}"
        cached_status = cache.get(redis_key)
        
        if cached_status:
            return cached_status
        
        # Если нет в Redis, проверяем AgentProfile
        try:
            profile = AgentProfile.objects.get(user_id=user_id)
            status = profile.status
            
            # Кэшируем в Redis
            cache.set(redis_key, status, timeout=cls.TTL)
            
            return status
        except AgentProfile.DoesNotExist:
            return None
    
    @classmethod
    def set_status(cls, user_id: int, status: str):
        """
        Установить статус оператора.
        
        По образцу Chatwoot: обновляет AgentProfile и кэширует в Redis.
        
        Args:
            user_id: ID оператора
            status: Статус ('online', 'away', 'busy', 'offline')
        """
        # Обновляем AgentProfile
        try:
            profile = AgentProfile.objects.get(user_id=user_id)
            profile.status = status
            profile.save(update_fields=['status'])
        except AgentProfile.DoesNotExist:
            # Создаём профиль если его нет
            AgentProfile.objects.create(user_id=user_id, status=status)
        
        # Кэшируем в Redis
        redis_key = f"{cls.STATUS_KEY_PREFIX}:{user_id}"
        cache.set(redis_key, status, timeout=cls.TTL)
        
        # Отправляем событие через Event Dispatcher
        from .dispatchers import get_dispatcher, Events
        
        dispatcher = get_dispatcher()
        dispatcher.dispatch(
            Events.AGENT_STATUS_CHANGED,
            timezone.now(),
            {"user_id": user_id, "status": status}
        )
    
    @classmethod
    def mark_online(cls, user_id: int):
        """Отметить оператора как онлайн."""
        cls.set_status(user_id, AgentProfile.Status.ONLINE)
    
    @classmethod
    def mark_away(cls, user_id: int):
        """Отметить оператора как отошёл."""
        cls.set_status(user_id, AgentProfile.Status.AWAY)
    
    @classmethod
    def mark_busy(cls, user_id: int):
        """Отметить оператора как занят."""
        cls.set_status(user_id, AgentProfile.Status.BUSY)
    
    @classmethod
    def mark_offline(cls, user_id: int):
        """Отметить оператора как офлайн."""
        cls.set_status(user_id, AgentProfile.Status.OFFLINE)
    
    @classmethod
    def is_online(cls, user_id: int) -> bool:
        """Проверить, онлайн ли оператор."""
        status = cls.get_status(user_id)
        return status == AgentProfile.Status.ONLINE
    
    @classmethod
    def get_online_user_ids(cls, branch_id: Optional[int] = None) -> Set[int]:
        """
        Получить множество ID онлайн операторов.
        
        По образцу Chatwoot: возвращает только онлайн операторов.
        
        Args:
            branch_id: ID филиала (опционально)
        
        Returns:
            Множество ID онлайн операторов
        """
        available_users = cls.get_available_users(branch_id)
        return {
            user_id for user_id, status in available_users.items()
            if status == AgentProfile.Status.ONLINE
        }
