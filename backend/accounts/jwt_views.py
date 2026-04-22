"""
Кастомные JWT views с защитой от брутфорса.
"""

from __future__ import annotations

import hashlib
import logging

from rest_framework import status
from rest_framework import status as drf_status
from rest_framework.response import Response
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView

from accounts.security import (
    RATE_LIMIT_LOGIN_PER_MINUTE,
    clear_login_attempts,
    get_client_ip,
    get_remaining_lockout_time,
    is_ip_rate_limited,
    is_user_locked_out,
    record_failed_login_attempt,
)
from audit.models import ActivityEvent
from audit.service import log_event

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
                status=status.HTTP_429_TOO_MANY_REQUESTS,
            )

        # Проверка блокировки пользователя
        if username and is_user_locked_out(username):
            remaining = get_remaining_lockout_time(username)
            minutes = (remaining // 60) + 1 if remaining else 15
            return Response(
                {
                    "detail": f"Аккаунт временно заблокирован из-за множественных неудачных попыток входа. Попробуйте через {minutes} минут."
                },
                status=status.HTTP_423_LOCKED,
            )

        # Вызываем родительский метод
        try:
            response = super().post(request, *args, **kwargs)

            # Если успешно - очищаем счетчики и добавляем is_admin в ответ
            if response.status_code == 200 and username:
                # W2.6 (2026-04-22): role filter. /api/token/ password-flow
                # разрешён ТОЛЬКО для admin/superuser. Non-admin пользователи
                # должны входить по magic link (web) или через
                # /api/phone/qr/exchange/ (mobile app, не использует password).
                # Parallel SecureLoginView.post (views.py:187) уже блокирует
                # non-admin на /login/ — этот fix закрывает parallel JWT path.
                user = User.objects.filter(username__iexact=username).first()
                is_admin = bool(
                    user
                    and (
                        user.is_superuser
                        or (hasattr(user, "role") and user.role == User.Role.ADMIN)
                    )
                )
                if user and not is_admin:
                    # Audit log: блокировка (не инкрементит lockout counter)
                    try:
                        log_event(
                            actor=user,
                            verb=ActivityEvent.Verb.UPDATE,
                            entity_type="security",
                            entity_id=f"jwt_non_admin_blocked:{user.id}",
                            message="JWT login заблокирован для non-admin (W2.6)",
                            meta={
                                "ip": ip,
                                "username": username,
                                "role": getattr(user, "role", ""),
                            },
                        )
                    except Exception:
                        logger.exception(
                            "SecureTokenObtainPairView: log_event failed для jwt_non_admin_blocked"
                        )
                    # Blacklist refresh токен, если SimpleJWT его успел создать
                    # (super().post() вернул 200 → токены уже есть в response.data).
                    # Таким образом non-admin не сможет использовать issued
                    # токены даже если перехватит response между строк.
                    try:
                        from rest_framework_simplejwt.tokens import RefreshToken

                        raw_refresh = response.data.get("refresh")
                        if raw_refresh:
                            RefreshToken(raw_refresh).blacklist()
                    except Exception:
                        logger.warning(
                            "SecureTokenObtainPairView: не удалось blacklist refresh для user_id=%s",
                            user.id,
                            exc_info=True,
                        )
                    return Response(
                        {
                            "detail": (
                                "Вход по логину и паролю через JWT доступен только для "
                                "администраторов. Остальные пользователи должны войти по "
                                "одноразовой ссылке (magic link), полученной от администратора."
                            )
                        },
                        status=status.HTTP_403_FORBIDDEN,
                    )

                clear_login_attempts(username)

                # Логируем успешный вход и добавляем is_admin в ответ
                try:
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
                {"detail": "Неверные учетные данные."}, status=status.HTTP_401_UNAUTHORIZED
            )


class LoggedTokenRefreshView(TokenRefreshView):
    """
    Обёртка над стандартным TokenRefreshView с логированием причин 401/403.
    Позволяет увидеть реальные источники "вылетов" по refresh без изменения поведения.
    """

    def post(self, request, *args, **kwargs):
        refresh_raw = (request.data.get("refresh") or "").strip()
        # Безопасный fingerprint вместо префикса токена: не логируем сам токен, только хэш.
        refresh_fingerprint = ""
        if refresh_raw:
            refresh_fingerprint = hashlib.sha256(refresh_raw.encode("utf-8")).hexdigest()[:12]

        try:
            response = super().post(request, *args, **kwargs)
        except Exception as e:
            # На всякий случай логируем неожиданные ошибки, если они не преобразованы в Response.
            logger.error(
                "TokenRefreshView: unexpected error for refresh_fp=%s, path=%s, error=%s",
                refresh_fingerprint or "empty",
                request.path,
                str(e),
                exc_info=True,
            )
            raise

        if response.status_code in (
            drf_status.HTTP_401_UNAUTHORIZED,
            drf_status.HTTP_403_FORBIDDEN,
        ):
            detail = None
            try:
                detail = response.data.get("detail")
            except Exception:
                detail = None
            # detail может быть не строкой — приводим к строке для безопасного логирования.
            detail_str = str(detail) if detail is not None else None

            # Если у вас есть middleware с request_id — его тоже можно залогировать из request.META.
            request_id = request.META.get("HTTP_X_REQUEST_ID") or request.META.get("X_REQUEST_ID")
            ip = get_client_ip(request)
            user_agent = request.META.get("HTTP_USER_AGENT", "")

            logger.warning(
                "TokenRefreshView: status=%s, detail=%s, refresh_fp=%s, path=%s, request_id=%s, ip=%s, ua=%s",
                response.status_code,
                detail_str,
                refresh_fingerprint or "empty",
                request.path,
                request_id,
                ip,
                user_agent,
            )

        return response
