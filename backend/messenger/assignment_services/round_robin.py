"""
Round-Robin сервис для автоназначения (по образцу Chatwoot).

Использует Redis список для хранения очереди операторов.
При назначении оператор перемещается в конец очереди.
"""

from typing import Optional, List
from django.core.cache import cache
from accounts.models import User
from messenger.models import Inbox, AgentProfile


class InboxRoundRobinService:
    """
    Round-Robin сервис для автоназначения (по образцу Chatwoot).
    
    Хранит очередь операторов в Redis как список.
    При назначении оператор перемещается в конец очереди.
    """
    
    ROUND_ROBIN_KEY_PREFIX = "messenger:rr:queue"
    TTL = 60 * 60 * 24 * 7  # 7 дней
    
    def __init__(self, inbox: Inbox):
        self.inbox = inbox
        self.round_robin_key = f"{self.ROUND_ROBIN_KEY_PREFIX}:{inbox.id}"
    
    def clear_queue(self):
        """Очистить очередь (при удалении inbox)."""
        cache.delete(self.round_robin_key)
    
    def add_agent_to_queue(self, user_id: int):
        """
        Добавить оператора в очередь (при добавлении в inbox).
        
        По образцу Chatwoot: LPUSH в начало списка.
        """
        queue = self._get_queue()
        if user_id not in queue:
            queue.append(user_id)
            self._save_queue(queue)
    
    def remove_agent_from_queue(self, user_id: int):
        """
        Удалить оператора из очереди (при удалении из inbox).
        
        По образцу Chatwoot: LREM из списка.
        """
        queue = self._get_queue()
        if user_id in queue:
            queue.remove(user_id)
            self._save_queue(queue)
    
    def reset_queue(self, member_ids: List[int]):
        """
        Сбросить очередь и заполнить новыми операторами.
        
        По образцу Chatwoot: очистить и заполнить список.
        """
        self.clear_queue()
        for user_id in member_ids:
            self.add_agent_to_queue(user_id)
    
    def available_agent(self, allowed_agent_ids: List[int]) -> Optional[User]:
        """
        Получить следующего доступного оператора из очереди (по образцу Chatwoot).
        
        Args:
            allowed_agent_ids: Список ID операторов, из которых можно выбирать
                              (например, только онлайн операторы)
        
        Returns:
            User или None, если нет доступных операторов
        
        Note:
            Алгоритм:
            1. Валидация очереди (проверка соответствия текущим членам inbox)
            2. Пересечение очереди и allowed_agent_ids
            3. POP первого из доступных
            4. PUSH в конец очереди (перемещение для round-robin)
            
            Если очередь не соответствует текущим членам, она автоматически пересоздаётся.
        """
        # Валидация очереди (по образцу Chatwoot)
        if not self._validate_queue():
            # Очередь не соответствует текущим членам — пересоздать
            member_ids = self._get_current_member_ids()
            self.reset_queue(member_ids)
        
        queue = self._get_queue()
        
        # Пересечение очереди и allowed_agent_ids (по образцу Chatwoot)
        available_ids = [uid for uid in queue if uid in allowed_agent_ids]
        
        if not available_ids:
            return None
        
        # Берём первого из доступных (POP из начала)
        user_id = available_ids[0]
        
        # Перемещаем в конец очереди (POP-PUSH по образцу Chatwoot)
        self._pop_push_to_queue(user_id)
        
        try:
            return User.objects.get(id=user_id)
        except User.DoesNotExist:
            return None
    
    def _get_queue(self) -> List[int]:
        """
        Получить очередь из Redis.
        
        По образцу Chatwoot: LRANGE всего списка.
        """
        queue = cache.get(self.round_robin_key, [])
        return [int(x) for x in queue] if queue else []
    
    def _save_queue(self, queue: List[int]):
        """
        Сохранить очередь в Redis.
        
        По образцу Chatwoot: сохраняем весь список.
        """
        cache.set(self.round_robin_key, queue, timeout=self.TTL)
    
    def _pop_push_to_queue(self, user_id: int):
        """
        Переместить оператора в конец очереди.
        
        По образцу Chatwoot: LREM из текущей позиции, LPUSH в конец.
        """
        queue = self._get_queue()
        if user_id in queue:
            queue.remove(user_id)
        queue.append(user_id)
        self._save_queue(queue)
    
    def _validate_queue(self) -> bool:
        """
        Проверить, соответствует ли очередь текущим членам inbox.
        
        По образцу Chatwoot: сравниваем множества ID.
        """
        current_member_ids = set(self._get_current_member_ids())
        queue_ids = set(self._get_queue())
        return current_member_ids == queue_ids
    
    def _get_current_member_ids(self) -> List[int]:
        """
        Получить список ID текущих членов inbox.
        
        По образцу Chatwoot: получаем всех активных операторов филиала.
        """
        if self.inbox.branch_id:
            return list(
                User.objects.filter(
                    branch_id=self.inbox.branch_id,
                    is_active=True,
                ).exclude(role=User.Role.ADMIN).values_list('id', flat=True)
            )
        return []
