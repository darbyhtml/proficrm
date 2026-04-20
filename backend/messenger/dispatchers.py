"""
Event Dispatcher для messenger (по образцу Chatwoot).

Централизованная система событий для real-time обновлений, webhooks, уведомлений.
Все события отправляются через единый dispatcher для консистентности.

Асинхронные слушатели выполняются через Celery task для надёжности.
"""

from typing import Dict, Any, Callable, List
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


# Реестр именованных async-слушателей (имя → import path)
_ASYNC_LISTENER_REGISTRY: Dict[str, List[str]] = {}


class EventDispatcher:
    """
    Event Dispatcher (по образцу Chatwoot).

    Централизованная система событий для real-time обновлений, webhooks, уведомлений.
    Синхронные слушатели вызываются в текущем потоке.
    Асинхронные слушатели — через Celery task (надёжно, с retry и логированием).
    """

    def __init__(self):
        self._sync_listeners: Dict[str, List[Callable]] = {}
        self._async_listeners: Dict[str, List[Callable]] = {}

    def dispatch(
        self,
        event_name: str,
        timestamp: datetime,
        data: Dict[str, Any],
        run_async: bool = False,
    ) -> None:
        """
        Отправить событие всем подписанным слушателям.

        Синхронные слушатели вызываются всегда.
        Асинхронные — через Celery task при run_async=True.
        Ошибки в слушателях логируются, но не прерывают выполнение.
        """
        # Синхронные слушатели — всегда
        for listener in self._sync_listeners.get(event_name, []):
            try:
                listener(event_name, timestamp, data)
            except Exception:
                logger.error(
                    "Error in sync event listener for %s",
                    event_name,
                    exc_info=True,
                    extra={"event": event_name},
                )

        # Асинхронные слушатели — через Celery
        if run_async:
            async_listeners = self._async_listeners.get(event_name, [])
            if async_listeners:
                from messenger.tasks import dispatch_async_listeners

                # Сериализуем данные для Celery (datetime → ISO string)
                serializable_data = _serialize_for_celery(data)
                dispatch_async_listeners.delay(
                    event_name=event_name,
                    timestamp_iso=timestamp.isoformat(),
                    data=serializable_data,
                )

    def subscribe(
        self,
        event_name: str,
        listener: Callable[[str, datetime, Dict[str, Any]], None],
        run_async: bool = False,
    ) -> None:
        """Подписаться на событие."""
        if run_async:
            self._async_listeners.setdefault(event_name, []).append(listener)
            # Регистрируем в реестре для Celery десериализации
            listener_path = f"{listener.__module__}.{listener.__qualname__}"
            _ASYNC_LISTENER_REGISTRY.setdefault(event_name, [])
            if listener_path not in _ASYNC_LISTENER_REGISTRY[event_name]:
                _ASYNC_LISTENER_REGISTRY[event_name].append(listener_path)
        else:
            self._sync_listeners.setdefault(event_name, []).append(listener)

    def unsubscribe(self, event_name: str, listener: Callable, run_async: bool = False):
        """Отписаться от события."""
        target = self._async_listeners if run_async else self._sync_listeners
        if event_name in target and listener in target[event_name]:
            target[event_name].remove(listener)


def _serialize_for_celery(data: Dict[str, Any]) -> Dict[str, Any]:
    """Преобразовать данные для передачи через Celery (JSON-сериализуемые)."""
    result = {}
    for key, value in data.items():
        if isinstance(value, datetime):
            result[key] = value.isoformat()
        elif hasattr(value, "pk"):
            # Django model → передаём pk и тип
            result[key] = {
                "_model": f"{value.__class__.__module__}.{value.__class__.__name__}",
                "_pk": value.pk,
            }
        else:
            try:
                import json

                json.dumps(value)
                result[key] = value
            except (TypeError, ValueError):
                result[key] = str(value)
    return result


# Глобальный экземпляр (Singleton)
_dispatcher = EventDispatcher()


def get_dispatcher() -> EventDispatcher:
    """Получить глобальный Event Dispatcher."""
    return _dispatcher


def get_async_listener_registry() -> Dict[str, List[str]]:
    """Получить реестр async-слушателей (для Celery task)."""
    return _ASYNC_LISTENER_REGISTRY
