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
    Применяется ко всем запросам, кроме статических файлов.
    """
    
    # Пути, которые не требуют rate limiting
    EXEMPT_PATHS = [
        "/static/",
        "/media/",
        "/favicon.ico",
    ]
    
    # Пути с более строгим лимитом
    STRICT_PATHS = [
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
        
        ip = get_client_ip(request)
        
        # Для строгих путей (логин, токены) используем более жесткий лимит
        is_strict = any(path.startswith(p) for p in self.STRICT_PATHS)
        max_requests = 10 if is_strict else RATE_LIMIT_API_PER_MINUTE
        
        # Проверяем rate limit
        if is_ip_rate_limited(ip, "general", max_requests, 60):
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

