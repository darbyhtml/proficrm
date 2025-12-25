"""
Кастомные views для аутентификации с защитой от брутфорса.
"""
from __future__ import annotations

from django.contrib.auth import views as auth_views
from django.contrib.auth import authenticate, login
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import redirect, render
from django.views.decorators.csrf import csrf_exempt
from django.utils.decorators import method_decorator
from django.views.decorators.http import require_http_methods
from django.utils import timezone

from accounts.security import (
    get_client_ip,
    is_user_locked_out,
    is_ip_rate_limited,
    record_failed_login_attempt,
    clear_login_attempts,
    get_remaining_lockout_time,
    RATE_LIMIT_LOGIN_PER_MINUTE,
)
from audit.service import log_event
from audit.models import ActivityEvent


class SecureLoginView(auth_views.LoginView):
    """Кастомный LoginView с защитой от брутфорса."""
    
    template_name = "registration/login.html"
    
    def post(self, request: HttpRequest, *args, **kwargs) -> HttpResponse:
        ip = get_client_ip(request)
        username = request.POST.get("username", "").strip()
        
        # Проверка rate limiting по IP
        if is_ip_rate_limited(ip, "login", RATE_LIMIT_LOGIN_PER_MINUTE, 60):
            return self.render_to_response(
                self.get_context_data(
                    error_message="Слишком много попыток входа. Попробуйте через минуту."
                )
            )
        
        # Проверка блокировки пользователя
        if username and is_user_locked_out(username):
            remaining = get_remaining_lockout_time(username)
            minutes = (remaining // 60) + 1 if remaining else 15
            return self.render_to_response(
                self.get_context_data(
                    error_message=f"Аккаунт временно заблокирован из-за множественных неудачных попыток входа. Попробуйте через {minutes} минут."
                )
            )
        
        # Вызываем родительский метод для аутентификации
        response = super().post(request, *args, **kwargs)
        
        # Проверяем результат аутентификации
        if request.user.is_authenticated:
            # Успешный вход - очищаем счетчики
            clear_login_attempts(username)
            
            # Логируем успешный вход
            try:
                log_event(
                    actor=request.user,
                    verb=ActivityEvent.Verb.UPDATE,
                    entity_type="security",
                    entity_id=f"login_success:{request.user.id}",
                    message="Успешный вход в систему",
                    meta={"ip": ip, "username": username},
                )
            except Exception:
                pass
        else:
            # Неудачная попытка входа
            record_failed_login_attempt(username, ip, "invalid_credentials")
        
        return response
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        if "error_message" in kwargs:
            context["error_message"] = kwargs["error_message"]
        return context
