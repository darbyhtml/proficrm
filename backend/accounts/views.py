"""
Кастомные views для аутентификации с защитой от брутфорса.
"""
from __future__ import annotations

from django.contrib.auth import views as auth_views
from django.contrib.auth import authenticate, login
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import redirect, render, get_object_or_404
from django.views.decorators.csrf import csrf_exempt
from django.utils.decorators import method_decorator
from django.views.decorators.http import require_http_methods
from django.utils import timezone
from django.conf import settings
import hashlib

from accounts.models import MagicLinkToken, User
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
    
    def dispatch(self, request: HttpRequest, *args, **kwargs) -> HttpResponse:
        """Редиректим на главную, если пользователь уже авторизован."""
        if request.user.is_authenticated:
            from django.shortcuts import redirect
            from django.conf import settings
            return redirect(settings.LOGIN_REDIRECT_URL)
        return super().dispatch(request, *args, **kwargs)
    
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
        # Проверка, включён ли режим "magic link only"
        context["magic_link_only"] = getattr(settings, "MAGIC_LINK_ONLY", False)
        return context
    
    def post(self, request: HttpRequest, *args, **kwargs) -> HttpResponse:
        # Если включён режим "magic link only", не принимаем пароли
        if getattr(settings, "MAGIC_LINK_ONLY", False):
            return self.render_to_response(
                self.get_context_data(
                    error_message="Вход только по одноразовой ссылке. Обратитесь к администратору для получения ссылки входа."
                )
            )
        
        # Определяем тип входа
        login_type = request.POST.get("login_type", "access_key")
        access_key = request.POST.get("access_key", "").strip()
        username = request.POST.get("username", "").strip()
        password = request.POST.get("password", "").strip()
        
        # Вход по ключу доступа (без логина)
        if login_type == "access_key" and access_key:
            ip = get_client_ip(request)
            user_agent = request.META.get("HTTP_USER_AGENT", "")[:255]
            
            # Rate limiting: не чаще 5 попыток в минуту с одного IP
            if is_ip_rate_limited(ip, "access_key_login", 5, 60):
                return self.render_to_response(
                    self.get_context_data(
                        error_message="Слишком много попыток входа. Попробуйте через минуту."
                    )
                )
            
            # Ищем валидный токен по хэшу (без логина)
            import hashlib
            try:
                token_hash = hashlib.sha256(access_key.encode()).hexdigest()
            except Exception:
                record_failed_login_attempt("", ip, "invalid_token_format")
                return self.render_to_response(
                    self.get_context_data(
                        error_message="Неверный формат ключа доступа."
                    )
                )
            
            try:
                magic_link = MagicLinkToken.objects.get(token_hash=token_hash)
            except MagicLinkToken.DoesNotExist:
                record_failed_login_attempt("", ip, "token_not_found")
                return self.render_to_response(
                    self.get_context_data(
                        error_message="Неверный ключ доступа."
                    )
                )
            
            user = magic_link.user
            
            # Проверяем валидность токена
            if not magic_link.is_valid():
                reason = "истёк" if timezone.now() >= magic_link.expires_at else "уже использован"
                record_failed_login_attempt(user.username if user else "", ip, f"token_{reason}")
                return self.render_to_response(
                    self.get_context_data(
                        error_message=f"Ключ доступа {reason}. Обратитесь к администратору для получения нового ключа."
                    )
                )
            
            # Проверяем, что пользователь активен
            if not user.is_active:
                record_failed_login_attempt(user.username, ip, "user_inactive")
                return self.render_to_response(
                    self.get_context_data(
                        error_message="Аккаунт неактивен."
                    )
                )
            
            # Вход успешен
            login(request, user)
            magic_link.mark_as_used(ip_address=ip, user_agent=user_agent)
            clear_login_attempts(user.username)
            
            # Логируем успешный вход
            try:
                log_event(
                    actor=user,
                    verb=ActivityEvent.Verb.UPDATE,
                    entity_type="security",
                    entity_id=f"access_key_login_success:{user.id}",
                    message="Успешный вход по ключу доступа",
                    meta={"ip": ip, "user_agent": user_agent[:100]},
                )
            except Exception:
                pass
            
            # Редирект
            redirect_to = request.POST.get("next") or settings.LOGIN_REDIRECT_URL
            return redirect(redirect_to)
        
        # Вход по логину и паролю (только для администраторов)
        if login_type == "password" and username and password:
            ip = get_client_ip(request)
            
            # Rate limiting: не чаще 5 попыток в минуту с одного IP
            if is_ip_rate_limited(ip, "password_login", 5, 60):
                return self.render_to_response(
                    self.get_context_data(
                        error_message="Слишком много попыток входа. Попробуйте через минуту."
                    )
                )
            
            # Аутентифицируем пользователя
            user = authenticate(request, username=username, password=password)
            
            if user is None:
                record_failed_login_attempt(username, ip, "invalid_credentials")
                return self.render_to_response(
                    self.get_context_data(
                        error_message="Неверный логин или пароль."
                    )
                )
            
            # Проверяем, что пользователь - администратор
            if user.role != User.Role.ADMIN:
                record_failed_login_attempt(username, ip, "non_admin_password_login")
                return self.render_to_response(
                    self.get_context_data(
                        error_message="Вход по логину и паролю доступен только для администраторов. Для входа используйте ключ доступа, обратитесь к администратору."
                    )
                )
            
            # Проверяем, что пользователь активен
            if not user.is_active:
                record_failed_login_attempt(username, ip, "user_inactive")
                return self.render_to_response(
                    self.get_context_data(
                        error_message="Аккаунт неактивен."
                    )
                )
            
            # Вход успешен
            login(request, user)
            clear_login_attempts(user.username)
            
            # Логируем успешный вход
            try:
                log_event(
                    actor=user,
                    verb=ActivityEvent.Verb.UPDATE,
                    entity_type="security",
                    entity_id=f"password_login_success:{user.id}",
                    message="Успешный вход по логину и паролю (администратор)",
                    meta={"ip": ip},
                )
            except Exception:
                pass
            
            # Редирект
            redirect_to = request.POST.get("next") or settings.LOGIN_REDIRECT_URL
            return redirect(redirect_to)
        
        # Если не указан тип входа или другие случаи, возвращаем ошибку
        return self.render_to_response(
            self.get_context_data(
                error_message="Неверный тип входа."
            )
        )


@require_http_methods(["GET"])
def magic_link_login(request: HttpRequest, token: str) -> HttpResponse:
    """
    Вход в систему по одноразовому токену.
    URL: /auth/magic/<token>/
    """
    ip = get_client_ip(request)
    user_agent = request.META.get("HTTP_USER_AGENT", "")[:255]
    
    # Rate limiting: не чаще 5 попыток в минуту с одного IP
    if is_ip_rate_limited(ip, "magic_link_login", 5, 60):
        return render(
            request,
            "registration/magic_link_error.html",
            {
                "error": "Слишком много попыток входа. Попробуйте через минуту.",
            },
            status=429,
        )
    
    # Вычисляем хэш токена
    try:
        token_hash = hashlib.sha256(token.encode()).hexdigest()
    except Exception:
        token_hash = None
    
    if not token_hash:
        return render(
            request,
            "registration/magic_link_error.html",
            {
                "error": "Неверный формат токена.",
            },
            status=400,
        )
    
    # Ищем токен
    try:
        magic_link = MagicLinkToken.objects.get(token_hash=token_hash)
    except MagicLinkToken.DoesNotExist:
        # Логируем неудачную попытку
        try:
            log_event(
                actor=None,
                verb=ActivityEvent.Verb.UPDATE,
                entity_type="security",
                entity_id="magic_link_failed",
                message="Неудачная попытка входа по magic link (токен не найден)",
                meta={"ip": ip, "user_agent": user_agent[:100]},
            )
        except Exception:
            pass
        return render(
            request,
            "registration/magic_link_error.html",
            {
                "error": "Токен не найден или недействителен.",
            },
            status=404,
        )
    
    # Проверяем валидность
    if not magic_link.is_valid():
        reason = "истёк" if timezone.now() >= magic_link.expires_at else "уже использован"
        try:
            log_event(
                actor=None,
                verb=ActivityEvent.Verb.UPDATE,
                entity_type="security",
                entity_id=f"magic_link_failed:{magic_link.user_id}",
                message=f"Неудачная попытка входа по magic link (токен {reason})",
                meta={"ip": ip, "user_agent": user_agent[:100], "user_id": magic_link.user_id},
            )
        except Exception:
            pass
        return render(
            request,
            "registration/magic_link_error.html",
            {
                "error": f"Токен {reason}. Обратитесь к администратору для получения новой ссылки.",
            },
            status=400,
        )
    
    # Вход успешен
    # Создаём сессию
    login(request, magic_link.user)
    
    # Помечаем токен как использованный
    magic_link.mark_as_used(ip_address=ip, user_agent=user_agent)
    
    # Логируем успешный вход
    try:
        log_event(
            actor=magic_link.user,
            verb=ActivityEvent.Verb.UPDATE,
            entity_type="security",
            entity_id=f"magic_link_success:{magic_link.user.id}",
            message="Успешный вход по magic link",
            meta={"ip": ip, "user_agent": user_agent[:100], "created_by": str(magic_link.created_by) if magic_link.created_by else None},
        )
    except Exception:
        pass
    
    # Редирект в кабинет
    return redirect("dashboard")