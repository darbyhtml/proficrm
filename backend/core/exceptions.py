"""
Кастомный обработчик исключений для DRF с защитой от утечки информации.
"""

from rest_framework.views import exception_handler
from rest_framework.response import Response
from django.conf import settings


def custom_exception_handler(exc, context):
    """
    Кастомный обработчик исключений, который скрывает детали ошибок в production.
    """
    # Вызываем стандартный обработчик
    response = exception_handler(exc, context)

    if response is not None:
        # В production не показываем детали ошибок для серверных ошибок (5xx),
        # но валидационные ошибки (400) и прочие клиентские ошибки (401, 403, 404)
        # возвращаем как есть — клиенту нужна информация для исправления запроса.
        if not settings.DEBUG and response.status_code >= 500:
            response.data = {"detail": "Произошла ошибка. Обратитесь к администратору."}

    return response
