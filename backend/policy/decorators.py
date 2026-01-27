"""
Декораторы для проверки прав доступа через policy engine.
"""
from __future__ import annotations

import functools
import logging
from typing import Any, Callable

from django.core.exceptions import PermissionDenied
from django.http import HttpRequest, HttpResponse

from accounts.models import User
from .engine import enforce
from .resources import ResourceType

logger = logging.getLogger(__name__)


def policy_required(
    *,
    resource_type: ResourceType,
    resource: str,
    extract_context: Callable[[HttpRequest], dict[str, Any]] | None = None,
) -> Callable:
    """
    Декоратор для проверки прав доступа через policy engine.
    
    Использование:
        @login_required
        @policy_required(resource_type="page", resource="ui:dashboard")
        def dashboard(request):
            ...
    
    Args:
        resource_type: Тип ресурса ("page" или "action")
        resource: Имя ресурса (например, "ui:dashboard", "ui:companies:list")
        extract_context: Опциональная функция для извлечения дополнительного контекста
                        из request (например, company_id, task_id)
    
    Returns:
        Декорированная функция, которая проверяет права перед выполнением
    """
    def decorator(view_func: Callable) -> Callable:
        @functools.wraps(view_func)
        def wrapper(request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
            user: User = request.user
            
            # Базовый контекст из request
            context: dict[str, Any] = {
                "path": request.path,
                "method": request.method,
            }
            
            # Добавляем параметры из URL (company_id, task_id и т.д.)
            if kwargs:
                # Извлекаем важные ID из kwargs
                for key in ["company_id", "task_id", "contact_id", "user_id", "note_id"]:
                    if key in kwargs:
                        context[key] = str(kwargs[key])
            
            # Добавляем параметры из query string (для фильтров)
            if request.GET:
                # Берем только важные параметры, чтобы не перегружать контекст
                important_params = ["filter", "search", "status", "responsible", "branch"]
                for param in important_params:
                    if param in request.GET:
                        context[f"param_{param}"] = request.GET[param]
            
            # Дополнительный контекст из пользовательской функции
            if extract_context:
                try:
                    extra_context = extract_context(request)
                    if extra_context:
                        context.update(extra_context)
                except Exception as e:
                    logger.warning(
                        f"Ошибка при извлечении контекста для {resource}: {e}",
                        exc_info=True,
                    )
            
            # Проверяем права
            try:
                enforce(
                    user=user,
                    resource_type=resource_type,
                    resource=resource,
                    context=context,
                )
            except PermissionDenied:
                # Перебрасываем исключение дальше
                raise
            except Exception as e:
                # Логируем неожиданные ошибки, но не блокируем запрос
                logger.exception(
                    f"Неожиданная ошибка при проверке прав для {resource}: {e}",
                )
                # В режиме observe_only можем продолжить, но логируем
                # В режиме enforce это не должно происходить
                raise
            
            return view_func(request, *args, **kwargs)
        
        return wrapper
    
    return decorator
