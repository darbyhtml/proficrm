"""
Middleware для защиты от DDoS и rate limiting.
"""
from __future__ import annotations

from django.http import HttpResponse, JsonResponse
from django.utils.deprecation import MiddlewareMixin

from accounts.security import get_client_ip, is_ip_rate_limited, RATE_LIMIT_API_PER_MINUTE


class RateLimitMiddleware(MiddlewareMixin):
    """
    Middleware для защиты от DDoS через rate limiting.
    Применяется только к критическим путям (логин, API токены).
    Обычная навигация по сайту не ограничивается.
    """
    
    # Пути, которые не требуют rate limiting
    EXEMPT_PATHS = [
        "/static/",
        "/media/",
        "/favicon.ico",
    ]
    
    # Пути с защитой от брутфорса (только эти пути защищаются)
    PROTECTED_PATHS = [
        "/login/",
        "/api/token/",
        "/api/token/refresh/",
    ]
    
    def process_request(self, request):
        # Пропускаем статические файлы
        path = request.path
        for exempt_path in self.EXEMPT_PATHS:
            if path.startswith(exempt_path):
                return None
        
        # Применяем rate limiting ТОЛЬКО к защищенным путям
        is_protected = any(path.startswith(p) for p in self.PROTECTED_PATHS)
        if not is_protected:
            return None  # Для остальных путей не применяем rate limiting
        
        ip = get_client_ip(request)
        
        # Для защищенных путей используем строгий лимит
        max_requests = 10  # 10 попыток в минуту для логина/токенов
        
        # Проверяем rate limit только для защищенных путей
        if is_ip_rate_limited(ip, "protected", max_requests, 60):
            if request.path.startswith("/api/"):
                return JsonResponse(
                    {"detail": "Превышен лимит запросов. Попробуйте позже."},
                    status=429
                )
            else:
                return HttpResponse(
                    "Превышен лимит запросов. Попробуйте позже.",
                    status=429
                )
        
        return None

