"""
Дополнительные middleware для безопасности и логирования ошибок.
"""
import secrets

from django.conf import settings
from django.utils.deprecation import MiddlewareMixin
from django.core.exceptions import PermissionDenied
from django.http import Http404


class SecurityHeadersMiddleware(MiddlewareMixin):
    """
    Добавляет дополнительные security headers, включая CSP.
    Генерирует CSP nonce per-request и сохраняет его в request.csp_nonce.
    """

    def process_request(self, request):
        request.csp_nonce = secrets.token_urlsafe(16)

    def process_response(self, request, response):
        # Добавляем CSP только в production
        if not settings.DEBUG and getattr(settings, 'CSP_HEADER', None):
            nonce = getattr(request, 'csp_nonce', None)
            if nonce:
                # Nonce заменяет 'unsafe-inline' только в script-src.
                # В style-src оставляем 'unsafe-inline' — нонс работает только
                # для <style nonce="...">, но не для атрибутов style="...".
                parts = settings.CSP_HEADER.split('; ')
                processed = []
                for part in parts:
                    if part.startswith('script-src '):
                        processed.append(part.replace("'unsafe-inline'", f"'nonce-{nonce}'"))
                    else:
                        processed.append(part)
                csp = '; '.join(processed)
            else:
                csp = settings.CSP_HEADER
            response['Content-Security-Policy'] = csp

        # Permissions-Policy (ограничение доступа к браузерным API)
        if not settings.DEBUG:
            response['Permissions-Policy'] = (
                'geolocation=(), '
                'microphone=(), '
                'camera=(), '
                'payment=(), '
                'usb=()'
            )

        # API version header — позволяет мобильному приложению определять версию API
        if request.path.startswith('/api/'):
            response['X-API-Version'] = '1'

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
            ErrorLog.log_error(
                exception=exception,
                request=request,
                level=level
            )
        except Exception:
            # Если не удалось сохранить ошибку, не прерываем обработку
            pass
        
        # Возвращаем None, чтобы Django продолжил стандартную обработку ошибки
        return None

