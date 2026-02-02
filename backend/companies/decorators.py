"""
Декораторы для проверки доступа к компании.

Используются в UI-views и любых обработчиках, где по company_id
нужно гарантировать, что пользователь имеет право видеть компанию (can_view_company).
"""
from __future__ import annotations

import functools
from typing import Any, Callable

from django.core.exceptions import PermissionDenied
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404

from accounts.models import User

from .models import Company
from .policy import can_view_company


def require_can_view_company(
    view_func: Callable[..., HttpResponse],
) -> Callable[..., HttpResponse]:
    """
    Декоратор: проверяет can_view_company для company_id из kwargs.
    Если company_id нет в kwargs или пользователь не может видеть компанию — PermissionDenied.

    Использование:
        @login_required
        @policy_required(...)
        @require_can_view_company
        def company_edit(request, company_id):
            ...
    """
    @functools.wraps(view_func)
    def wrapper(request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        company_id = kwargs.get("company_id")
        if company_id is None:
            return view_func(request, *args, **kwargs)
        user: User = request.user
        if not user.is_authenticated:
            raise PermissionDenied("Требуется авторизация")
        company = get_object_or_404(Company.objects.only("id"), id=company_id)
        if not can_view_company(user, company):
            raise PermissionDenied("Нет доступа к этой компании")
        return view_func(request, *args, **kwargs)

    return wrapper
