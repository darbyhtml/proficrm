"""
Event Dispatcher для messenger (по образцу Chatwoot).

Централизованная система событий для real-time обновлений, webhooks, уведомлений.
Все события отправляются через единый dispatcher для консистентности.
"""

from typing import Dict, Any, Callable, List, Optional
from django.utils import timezone
from datetime import datetime
import logging

logger = logging.getLogger(__name__)


# События (по образцу Chatwoot)
class Events:
    """Константы событий для Event Dispatcher."""
    
    # Conversation события
    CONVERSATION_CREATED = "conversation.created"
    CONVERSATION_UPDATED = "conversation.updated"
    CONVERSATION_OPENED = "conversation.opened"
    CONVERSATION_RESOLVED = "conversation.resolved"
    CONVERSATION_CLOSED = "conversation.closed"
    CONVERSATION_STATUS_CHANGED = "conversation.status_changed"
    ASSIGNEE_CHANGED = "assignee.changed"
    CONVERSATION_TYPING_STARTED = "conversation.typing_started"
    CONVERSATION_TYPING_STOPPED = "conversation.typing_stopped"
    
    # Message события
    MESSAGE_CREATED = "message.created"
    MESSAGE_UPDATED = "message.updated"
    FIRST_REPLY_CREATED = "first_reply.created"
    REPLY_CREATED = "reply.created"
    
    # Contact события
    CONTACT_CREATED = "contact.created"
    CONTACT_UPDATED = "contact.updated"
    
    # Agent события
    AGENT_STATUS_CHANGED = "agent.status_changed"


class EventDispatcher:
    """
    Event Dispatcher (по образцу Chatwoot).
    
    Централизованная система событий для real-time обновлений, webhooks, уведомлений.
    Поддерживает синхронные и асинхронные слушатели.
    """
    
    def __init__(self):
        self._sync_listeners: Dict[str, List[Callable]] = {}
        self._async_listeners: Dict[str, List[Callable]] = {}
    
    def dispatch(
        self, 
        event_name: str, 
        timestamp: datetime, 
        data: Dict[str, Any], 
        async: bool = False
    ) -> None:
        """
        Отправить событие всем подписанным слушателям (по образцу Chatwoot).
        
        Args:
            event_name: Имя события (из Events)
            timestamp: Время события
            data: Данные события (словарь с объектами/данными)
            async: Асинхронная обработка (через Celery, если будет)
        
        Note:
            Синхронные слушатели вызываются всегда.
            Асинхронные слушатели вызываются только если async=True.
            Ошибки в слушателях логируются, но не прерывают выполнение.
        """
        # Синхронные слушатели всегда вызываются
        sync_listeners = self._sync_listeners.get(event_name, [])
        for listener in sync_listeners:
            try:
                listener(event_name, timestamp, data)
            except Exception as e:
                logger.error(
                    f"Error in sync event listener for {event_name}",
                    exc_info=True,
                    extra={"event": event_name, "data": data}
                )
        
        # Асинхронные слушатели (если async=True)
        if async:
            async_listeners = self._async_listeners.get(event_name, [])
            for listener in async_listeners:
                try:
                    # TODO: Интеграция с Celery для асинхронной обработки
                    listener(event_name, timestamp, data)
                except Exception as e:
                    logger.error(
                        f"Error in async event listener for {event_name}",
                        exc_info=True,
                        extra={"event": event_name, "data": data}
                    )
    
    def subscribe(
        self, 
        event_name: str, 
        listener: Callable[[str, datetime, Dict[str, Any]], None], 
        async: bool = False
    ) -> None:
        """
        Подписаться на событие (по образцу Chatwoot).
        
        Args:
            event_name: Имя события (из Events)
            listener: Функция-обработчик с сигнатурой (event_name, timestamp, data) -> None
            async: Асинхронная обработка (через Celery, если будет)
        
        Note:
            Один и тот же listener можно подписать на несколько событий.
            Порядок вызова слушателей не гарантируется.
        """
        if async:
            if event_name not in self._async_listeners:
                self._async_listeners[event_name] = []
            self._async_listeners[event_name].append(listener)
        else:
            if event_name not in self._sync_listeners:
                self._sync_listeners[event_name] = []
            self._sync_listeners[event_name].append(listener)
    
    def unsubscribe(self, event_name: str, listener: Callable, async: bool = False):
        """
        Отписаться от события.
        
        Args:
            event_name: Имя события
            listener: Функция-обработчик для удаления
            async: Асинхронный слушатель
        """
        if async:
            if event_name in self._async_listeners and listener in self._async_listeners[event_name]:
                self._async_listeners[event_name].remove(listener)
        else:
            if event_name in self._sync_listeners and listener in self._sync_listeners[event_name]:
                self._sync_listeners[event_name].remove(listener)


# Глобальный экземпляр (Singleton по образцу Chatwoot)
_dispatcher = EventDispatcher()


def get_dispatcher() -> EventDispatcher:
    """
    Получить глобальный Event Dispatcher.
    
    По образцу Chatwoot: единый экземпляр для всего приложения.
    """
    return _dispatcher
