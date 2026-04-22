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

    W2.3 Phase 1 (2026-04-22): middleware теперь emits 2 parallel CSP headers:
    - `Content-Security-Policy` (enforce): оставляет 'unsafe-inline' как
      safety net — 66 inline event handlers остаются legit до Phase 2
      cleanup. Также injects nonce для 91 `<script nonce=...>` templates.
    - `Content-Security-Policy-Report-Only` (shadow strict): same directives
      но БЕЗ 'unsafe-inline' на script-src. Browsers логируют violations
      в /csp-report/, НЕ блокируют rendering. Monitoring mode для Phase 2.

    После Phase 2 (inline handlers extracted) + 48h clean monitoring,
    Phase 3 swaps: strict становится enforce, old enforce removed.
    """

    def process_request(self, request):
        request.csp_nonce = secrets.token_urlsafe(16)

    def process_response(self, request, response):
        # Пропускаем CSP для страниц, помеченных _skip_csp (напр. widget-test)
        if getattr(response, "_skip_csp", False):
            return response

        # Добавляем CSP только в production
        if not settings.DEBUG:
            nonce = getattr(request, "csp_nonce", "")
            # Enforce policy (safety net, preserves 'unsafe-inline' Phase 1).
            enforce_template = getattr(settings, "CSP_HEADER_ENFORCE_TEMPLATE", None)
            if enforce_template:
                response["Content-Security-Policy"] = enforce_template.format(nonce=nonce)
            # Shadow strict policy (report-only, no 'unsafe-inline' script-src).
            strict_template = getattr(settings, "CSP_HEADER_STRICT_TEMPLATE", None)
            if strict_template:
                response["Content-Security-Policy-Report-Only"] = strict_template.format(
                    nonce=nonce
                )

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
