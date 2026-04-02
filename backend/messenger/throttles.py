"""
Throttling классы для Widget API (защита от спама и перегруза).

Использует Redis cache для хранения счётчиков запросов.
"""

from django.conf import settings
from rest_framework.throttling import BaseThrottle
from django.core.cache import cache
from django.utils import timezone


class WidgetBootstrapThrottle(BaseThrottle):
    """
    Throttle для /api/widget/bootstrap/:
    - N запросов в минуту с одного IP
    - N запросов в минуту для одного widget_token
    """
    
    # Лимиты (по умолчанию; могут быть переопределены в settings)
    RATE_PER_IP = getattr(settings, "MESSENGER_WIDGET_BOOTSTRAP_RATE_PER_IP", 10)
    RATE_PER_TOKEN = getattr(settings, "MESSENGER_WIDGET_BOOTSTRAP_RATE_PER_TOKEN", 20)
    
    def get_cache_key(self, request, view):
        """Генерирует ключ кэша для throttle."""
        # IP-based key
        ip = self.get_ident(request)
        ip_key = f"messenger:throttle:bootstrap:ip:{ip}"
        
        # Token-based key (если есть widget_token в данных)
        token_key = None
        widget_token = None
        if request.method == "POST" and hasattr(request, "data"):
            widget_token = request.data.get("widget_token")
            if widget_token:
                token_key = f"messenger:throttle:bootstrap:token:{widget_token}"
        
        return ip_key, token_key, widget_token
    
    def allow_request(self, request, view):
        """
        Проверяет, разрешён ли запрос.
        
        Возвращает True если запрос разрешён, False если превышен лимит.
        """
        ip_key, token_key, widget_token = self.get_cache_key(request, view)
        
        # Проверка по IP
        ip_count = cache.get(ip_key, 0)
        if ip_count >= self.RATE_PER_IP:
            return False
        
        # Проверка по widget_token (если есть)
        if token_key:
            token_count = cache.get(token_key, 0)
            if token_count >= self.RATE_PER_TOKEN:
                return False
        
        # Увеличиваем счётчики
        cache.set(ip_key, ip_count + 1, timeout=60)  # TTL 60 секунд
        if token_key:
            cache.set(token_key, token_count + 1, timeout=60)
        
        return True
    
    def wait(self):
        """Возвращает количество секунд до следующего разрешённого запроса."""
        return 60  # Ждать до конца минуты


class WidgetSendThrottle(BaseThrottle):
    """
    Throttle для /api/widget/send/:
    - N запросов в минуту для одной сессии (widget_session_token)
    - N запросов в минуту с одного IP
    """
    
    RATE_PER_SESSION = getattr(settings, "MESSENGER_WIDGET_SEND_RATE_PER_SESSION", 30)
    RATE_PER_IP = getattr(settings, "MESSENGER_WIDGET_SEND_RATE_PER_IP", 60)
    
    def get_cache_key(self, request, view):
        """Генерирует ключ кэша для throttle."""
        # IP-based key
        ip = self.get_ident(request)
        ip_key = f"messenger:throttle:send:ip:{ip}"
        
        # Session-based key
        session_key = None
        widget_session_token = None
        if request.method == "POST" and hasattr(request, "data"):
            widget_session_token = request.data.get("widget_session_token")
            if widget_session_token:
                # Используем только первые 16 символов для ключа (безопасность)
                session_key = f"messenger:throttle:send:session:{widget_session_token[:16]}"
        
        return ip_key, session_key, widget_session_token
    
    def allow_request(self, request, view):
        """Проверяет, разрешён ли запрос."""
        ip_key, session_key, widget_session_token = self.get_cache_key(request, view)
        
        # Проверка по IP
        ip_count = cache.get(ip_key, 0)
        if ip_count >= self.RATE_PER_IP:
            return False
        
        # Проверка по сессии (если есть)
        if session_key:
            session_count = cache.get(session_key, 0)
            if session_count >= self.RATE_PER_SESSION:
                return False
        
        # Увеличиваем счётчики
        cache.set(ip_key, ip_count + 1, timeout=60)
        if session_key:
            cache.set(session_key, session_count + 1, timeout=60)
        
        return True
    
    def wait(self):
        """Возвращает количество секунд до следующего разрешённого запроса."""
        return 60


class WidgetPollThrottle(BaseThrottle):
    """
    Throttle для /api/widget/poll/:
    - N запросов в минуту для одной сессии (widget_session_token)
    - Минимальный интервал между запросами (опционально)
    """
    
    RATE_PER_SESSION = getattr(settings, "MESSENGER_WIDGET_POLL_RATE_PER_SESSION", 20)
    MIN_INTERVAL_SECONDS = getattr(
        settings,
        "MESSENGER_WIDGET_POLL_MIN_INTERVAL_SECONDS",
        2,
    )
    
    def get_cache_key(self, request, view):
        """Генерирует ключ кэша для throttle."""
        widget_session_token = request.query_params.get("widget_session_token")
        if not widget_session_token:
            return None, None
        
        # Ключ для счётчика запросов
        count_key = f"messenger:throttle:poll:count:{widget_session_token[:16]}"
        
        # Ключ для последнего запроса (для минимального интервала)
        last_key = f"messenger:throttle:poll:last:{widget_session_token[:16]}"
        
        return count_key, last_key
    
    def allow_request(self, request, view):
        """Проверяет, разрешён ли запрос."""
        count_key, last_key = self.get_cache_key(request, view)
        
        if not count_key:
            # Нет session_token - разрешаем (валидация будет в view)
            return True
        
        # Проверка минимального интервала
        if last_key:
            last_request_time = cache.get(last_key)
            if last_request_time:
                elapsed = (timezone.now().timestamp() - last_request_time)
                if elapsed < self.MIN_INTERVAL_SECONDS:
                    return False
        
        # Проверка счётчика запросов
        count = cache.get(count_key, 0)
        if count >= self.RATE_PER_SESSION:
            return False
        
        # Увеличиваем счётчик и обновляем время последнего запроса
        cache.set(count_key, count + 1, timeout=60)
        if last_key:
            cache.set(last_key, timezone.now().timestamp(), timeout=60)
        
        return True
    
    def wait(self):
        """Возвращает количество секунд до следующего разрешённого запроса."""
        return self.MIN_INTERVAL_SECONDS
