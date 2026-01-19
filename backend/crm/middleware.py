"""
Дополнительные middleware для безопасности и логирования ошибок.
"""
from django.conf import settings
from django.utils.deprecation import MiddlewareMixin
from django.core.exceptions import PermissionDenied
from django.http import Http404


class SecurityHeadersMiddleware(MiddlewareMixin):
    """
    Добавляет дополнительные security headers, включая CSP.
    """
    
    def process_response(self, request, response):
        # Добавляем CSP только в production
        if not settings.DEBUG and hasattr(settings, 'CSP_HEADER'):
            response['Content-Security-Policy'] = settings.CSP_HEADER
        
        # Permissions-Policy (ограничение доступа к браузерным API)
        if not settings.DEBUG:
            response['Permissions-Policy'] = (
                'geolocation=(), '
                'microphone=(), '
                'camera=(), '
                'payment=(), '
                'usb=()'
            )
        
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
            
            # Определяем уровень ошибки
            level = ErrorLog.Level.EXCEPTION
            if isinstance(exception, (ValueError, TypeError, AttributeError)):
                level = ErrorLog.Level.ERROR
            elif isinstance(exception, (PermissionDenied, Http404)):
                level = ErrorLog.Level.WARNING
            elif isinstance(exception, (SystemExit, KeyboardInterrupt)):
                # Не логируем системные исключения
                return None
            
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

