"""
Дополнительные middleware для безопасности.
"""
from django.conf import settings
from django.utils.deprecation import MiddlewareMixin


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

