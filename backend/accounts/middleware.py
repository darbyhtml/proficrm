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
    
    # Пути с защитой от брутфорса (строгий лимит для логина/токенов)
    PROTECTED_AUTH_PATHS = [
        "/login/",
        "/api/token/",
        "/api/token/refresh/",
    ]
    
    # Phone API (Android приложение) — более мягкий лимит, отдельный бакет
    PHONE_API_PATH = "/api/phone/"
    
    def process_request(self, request):
        # Пропускаем статические файлы
        path = request.path
        for exempt_path in self.EXEMPT_PATHS:
            if path.startswith(exempt_path):
                return None
        
        ip = get_client_ip(request)
        
        # Проверяем Phone API отдельно (более мягкий лимит)
        if path.startswith(self.PHONE_API_PATH):
            # Для телефонного API используем более мягкий лимит (60 запросов в минуту)
            # Это позволяет приложению делать частые polling запросы
            if is_ip_rate_limited(ip, "phone_api", RATE_LIMIT_API_PER_MINUTE, 60):
                return JsonResponse(
                    {"detail": "Превышен лимит запросов. Попробуйте позже."},
                    status=429
                )
            return None
        
        # Проверяем защищенные пути авторизации (строгий лимит только для попыток входа)
        # GET/HEAD к /login/ не считаем — иначе после выхода редирект на страницу входа даёт 429
        is_auth_protected = any(path.startswith(p) for p in self.PROTECTED_AUTH_PATHS)
        if is_auth_protected and request.method not in ("GET", "HEAD", "OPTIONS"):
            # Для POST к логину/токенам — строгий лимит (10 попыток в минуту)
            if is_ip_rate_limited(ip, "auth", 10, 60):
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

