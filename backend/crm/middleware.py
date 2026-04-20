"""
Дополнительные middleware для безопасности и логирования ошибок.
"""

import secrets

from django.conf import settings
from django.core.exceptions import PermissionDenied
from django.http import Http404
from django.utils.deprecation import MiddlewareMixin


class SecurityHeadersMiddleware(MiddlewareMixin):
    """
    Добавляет дополнительные security headers, включая CSP.
    Генерирует CSP nonce per-request и сохраняет его в request.csp_nonce.
    """

    def process_request(self, request):
        request.csp_nonce = secrets.token_urlsafe(16)

    def process_response(self, request, response):
        # Пропускаем CSP для страниц, помеченных _skip_csp (напр. widget-test)
        if getattr(response, "_skip_csp", False):
            return response

        # Добавляем CSP только в production
        # NB: nonce генерируется (request.csp_nonce), но пока не встраивается
        # в CSP-заголовок, т.к. при наличии nonce браузер игнорирует
        # unsafe-inline, а шаблоны содержат inline onclick=/style=.
        # Рефакторинг шаблонов → Фаза 6 improvement-plan.
        if not settings.DEBUG and getattr(settings, "CSP_HEADER", None):
            response["Content-Security-Policy"] = settings.CSP_HEADER

        # Permissions-Policy (ограничение доступа к браузерным API)
        if not settings.DEBUG:
            response["Permissions-Policy"] = (
                "geolocation=(), " "microphone=(), " "camera=(), " "payment=(), " "usb=()"
            )

        # API version header — позволяет мобильному приложению определять версию API
        if request.path.startswith("/api/"):
            response["X-API-Version"] = "1"

        return response


class ErrorLoggingMiddleware(MiddlewareMixin):
    """
    Middleware для перехвата и логирования ошибок в БД.
    Аналогично error_log в MODX CMS.
    """

    def process_exception(self, request, exception):
        """
        Перехватывает исключения и сохраняет их в БД.
        """
        try:
            from audit.models import ErrorLog

            # Http404 и PermissionDenied — штатные ситуации, не ошибки
            if isinstance(exception, (Http404, PermissionDenied, SystemExit, KeyboardInterrupt)):
                return None

            # Определяем уровень ошибки
            level = ErrorLog.Level.EXCEPTION
            if isinstance(exception, (ValueError, TypeError, AttributeError)):
                level = ErrorLog.Level.ERROR

            # Логируем ошибку
            ErrorLog.log_error(exception=exception, request=request, level=level)
        except Exception:
            # Если не удалось сохранить ошибку, логируем в stderr
            import logging

            logging.getLogger("crm.error_logging").exception("Failed to persist ErrorLog entry")

        # Возвращаем None, чтобы Django продолжил стандартную обработку ошибки
        return None
