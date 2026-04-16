"""
Middleware для добавления request_id к каждому запросу для корреляции логов.
"""
from __future__ import annotations

import logging
import uuid
import threading

from django.utils.deprecation import MiddlewareMixin

logger = logging.getLogger(__name__)

# Thread-local storage для request_id
_thread_local = threading.local()


class RequestIdMiddleware(MiddlewareMixin):
    """
    Middleware для добавления уникального request_id к каждому запросу.
    
    Добавляет request_id в:
    - request.request_id (для использования в views)
    - Thread-local storage (для использования в логах)
    - Заголовок ответа X-Request-ID
    """
    
    def process_request(self, request):
        """Добавляет request_id к запросу."""
        # Генерируем уникальный ID для запроса (первые 8 символов UUID)
        request_id = str(uuid.uuid4())[:8]
        request.request_id = request_id
        
        # Сохраняем в thread-local storage для доступа в логах
        _thread_local.request_id = request_id
        
        return None
    
    def process_response(self, request, response):
        """Добавляет request_id в заголовок ответа для отладки."""
        if hasattr(request, "request_id"):
            response["X-Request-ID"] = request.request_id
        
        # Очищаем thread-local storage после обработки запроса
        if hasattr(_thread_local, "request_id"):
            delattr(_thread_local, "request_id")
        
        return response


class RequestIdLoggingFilter(logging.Filter):
    """
    Filter для автоматического добавления request_id в логи.
    Использует thread-local storage для получения request_id.
    """
    
    def filter(self, record):
        """Добавляет request_id к записи лога, если он доступен."""
        try:
            if hasattr(_thread_local, "request_id"):
                record.request_id = _thread_local.request_id
            else:
                record.request_id = None
        except Exception:
            record.request_id = None
        
        return True


def get_request_id():
    """Получить текущий request_id из thread-local storage."""
    return getattr(_thread_local, "request_id", None)
