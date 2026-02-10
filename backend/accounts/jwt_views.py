"""
Кастомные JWT views с защитой от брутфорса.
"""
from __future__ import annotations

from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView
from rest_framework.response import Response
from rest_framework import status
from rest_framework import status as drf_status
import logging

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


logger = logging.getLogger(__name__)


class SecureTokenObtainPairView(TokenObtainPairView):
    """JWT Token view с защитой от брутфорса."""
    
    def post(self, request, *args, **kwargs):
        from accounts.models import User
        ip = get_client_ip(request)
        username = request.data.get("username", "").strip()
        
        # Проверка rate limiting по IP
        if is_ip_rate_limited(ip, "jwt_login", RATE_LIMIT_LOGIN_PER_MINUTE, 60):
            return Response(
                {"detail": "Превышен лимит попыток входа. Попробуйте через минуту."},
                status=status.HTTP_429_TOO_MANY_REQUESTS
            )
        
        # Проверка блокировки пользователя
        if username and is_user_locked_out(username):
            remaining = get_remaining_lockout_time(username)
            minutes = (remaining // 60) + 1 if remaining else 15
            return Response(
                {
                    "detail": f"Аккаунт временно заблокирован из-за множественных неудачных попыток входа. Попробуйте через {minutes} минут."
                },
                status=status.HTTP_423_LOCKED
            )
        
        # Вызываем родительский метод
        try:
            response = super().post(request, *args, **kwargs)
            
            # Если успешно - очищаем счетчики и добавляем is_admin в ответ
            if response.status_code == 200 and username:
                clear_login_attempts(username)
                
                # Логируем успешный вход и добавляем is_admin в ответ
                try:
                    user = User.objects.filter(username__iexact=username).first()
                    if user:
                        log_event(
                            actor=user,
                            verb=ActivityEvent.Verb.UPDATE,
                            entity_type="security",
                            entity_id=f"jwt_login_success:{user.id}",
                            message="Успешный вход через JWT API",
                            meta={"ip": ip, "username": username},
                        )
                        
                        # Добавляем is_admin в ответ
                        is_admin = bool(
                            user.is_superuser or 
                            (hasattr(user, "role") and user.role == User.Role.ADMIN)
                        )
                        response.data["is_admin"] = is_admin
                except Exception:
                    pass
            
            return response
            
        except Exception as e:
            # Неудачная попытка входа
            if username:
                record_failed_login_attempt(username, ip, "invalid_credentials")
            
            # Возвращаем общий ответ без деталей (защита от утечки информации)
            return Response(
                {"detail": "Неверные учетные данные."},
                status=status.HTTP_401_UNAUTHORIZED
            )
        

class LoggedTokenRefreshView(TokenRefreshView):
    """
    Обёртка над стандартным TokenRefreshView с логированием причин 401/403.
    Позволяет увидеть реальные источники "вылетов" по refresh без изменения поведения.
    """

    def post(self, request, *args, **kwargs):
        refresh_raw = (request.data.get("refresh") or "").strip()
        refresh_prefix = refresh_raw[:16] if refresh_raw else ""

        try:
            response = super().post(request, *args, **kwargs)
        except Exception as e:
            # На всякий случай логируем неожиданные ошибки, если они не преобразованы в Response.
            logger.error(
                "TokenRefreshView: unexpected error for refresh_prefix=%s…, path=%s, error=%s",
                refresh_prefix,
                request.path,
                str(e),
                exc_info=True,
            )
            raise

        if response.status_code in (drf_status.HTTP_401_UNAUTHORIZED, drf_status.HTTP_403_FORBIDDEN):
            detail = None
            try:
                detail = response.data.get("detail")
            except Exception:
                detail = None

            # Если у вас есть middleware с request_id — его тоже можно залогировать из request.META.
            request_id = request.META.get("HTTP_X_REQUEST_ID") or request.META.get("X_REQUEST_ID")

            logger.warning(
                "TokenRefreshView: status=%s, detail=%s, refresh_prefix=%s…, path=%s, request_id=%s",
                response.status_code,
                detail,
                refresh_prefix if refresh_prefix else "empty",
                request.path,
                request_id,
            )

        return response

