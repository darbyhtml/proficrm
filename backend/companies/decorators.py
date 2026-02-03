"""
Декораторы для проверки доступа к компании.

Используются в UI-views и любых обработчиках, где по company_id
нужно гарантировать, что пользователь имеет право видеть компанию (can_view_company).
"""
from __future__ import annotations

import functools
import uuid
from typing import Any, Callable

from django.core.exceptions import PermissionDenied
from django.http import Http404, HttpRequest, HttpResponse, HttpResponseBadRequest, HttpResponseForbidden
from django.shortcuts import get_object_or_404

from accounts.models import User

from .models import Company, CompanyNote
from .policy import can_view_company


def require_can_view_company(
    view_func: Callable[..., HttpResponse],
) -> Callable[..., HttpResponse]:
    """
    Декоратор: проверяет can_view_company для company_id из kwargs или request.POST.
    Если company_id нет в kwargs — пробует request.POST.get('company_id').
    Если пользователь не может видеть компанию — PermissionDenied.

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
        if company_id is None and request.method == "POST":
            raw = request.POST.get("company_id")
            if raw:
                try:
                    company_id = uuid.UUID(str(raw).strip())
                except (ValueError, TypeError, AttributeError):
                    company_id = None
            if company_id is not None:
                kwargs = dict(kwargs, company_id=company_id)
        if company_id is None:
            return HttpResponseBadRequest("Missing company_id")
        user: User = request.user
        if not user.is_authenticated:
            raise PermissionDenied("Требуется авторизация")
        try:
            company = Company.objects.only("id").get(id=company_id)
        except Company.DoesNotExist:
            raise Http404()
        if not can_view_company(user, company):
            raise PermissionDenied("Нет доступа к этой компании")
        request.company = company  # Явно сохранить в request
        return view_func(request, *args, **kwargs)

    return wrapper


def require_can_view_note_company(
    view_func: Callable[..., HttpResponse],
) -> Callable[..., HttpResponse]:
    """
    Декоратор: по note_id из kwargs загружает Note с select_related('company')
    и проверяет can_view_company(user, note.company).
    """
    @functools.wraps(view_func)
    def wrapper(request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        note_id = kwargs.get("note_id")
        if note_id is None:
            raise PermissionDenied("Не указана заметка")
        user: User = request.user
        if not user.is_authenticated:
            raise PermissionDenied("Требуется авторизация")
        note = get_object_or_404(
            CompanyNote.objects.select_related("company").only("id", "company_id"),
            id=note_id,
        )
        if not can_view_company(user, note.company):
            return HttpResponseForbidden("Нет доступа к компании заметки")
        return view_func(request, *args, **kwargs)

    return wrapper
