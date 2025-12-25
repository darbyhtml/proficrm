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
        # В production не показываем детали ошибок
        if not settings.DEBUG:
            # Сохраняем оригинальное сообщение для логирования
            original_detail = response.data.get('detail', 'Произошла ошибка')
            
            # Заменяем детали на общее сообщение
            if isinstance(response.data, dict):
                response.data = {
                    'detail': 'Произошла ошибка. Обратитесь к администратору.'
                }
            else:
                response.data = {'detail': 'Произошла ошибка. Обратитесь к администратору.'}
    
    return response

