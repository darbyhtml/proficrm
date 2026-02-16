from __future__ import annotations

from django.db import models
from django.db.models import QuerySet

from accounts.models import User
from .models import Inbox, Conversation, CannedResponse


def visible_inboxes_qs(user: User) -> QuerySet[Inbox]:
    """
    Inboxes, видимые пользователю.

    Логика безопасности:
    - неаутентифицированные/неактивные пользователи не видят ничего;
    - администраторы и суперпользователи видят все inbox'ы;
    - пользователи с data_scope=GLOBAL видят все активные inbox'ы;
    - пользователи с data_scope=BRANCH видят только inbox'ы своего филиала;
    - пользователи с data_scope=SELF видят inbox'ы своего филиала (для работы оператора внутри филиала).
    """
    qs = Inbox.objects.all()
    if not user or not user.is_authenticated or not user.is_active:
        return qs.none()

    # Полный доступ к inbox'ам только у ADMIN и superuser.
    if user.is_superuser or user.role == User.Role.ADMIN:
        return qs
    # Не-админы без филиала не видят inbox'ы вообще.
    if user.branch_id:
        return qs.filter(branch_id=user.branch_id, is_active=True)
    return qs.none()


def visible_conversations_qs(user: User) -> QuerySet[Conversation]:
    """
    Диалоги, видимые пользователю.

    Ключевой инвариант безопасности:
    - оператор/менеджер/директор видит только диалоги в рамках своего филиала (branch),
      либо только свои (при data_scope=SELF);
    - администраторы и суперпользователи видят все диалоги.
    """
    qs = Conversation.objects.select_related("inbox", "contact", "assignee", "branch", "region")

    if not user or not user.is_authenticated or not user.is_active:
        return qs.none()

    # Полный доступ только у ADMIN и superuser.
    if user.is_superuser or user.role == User.Role.ADMIN:
        return qs

    # Ограничение по data_scope
    if user.data_scope == User.DataScope.SELF:
        # Только диалоги, где пользователь назначен оператором
        return qs.filter(assignee_id=user.id)

    # Для не-админов GLOBAL не расширяет доступ: остаёмся в рамках филиала.
    if user.branch_id:
        return qs.filter(branch_id=user.branch_id)

    # Если филиал не задан, а пользователь не админ — ограничиваемся только своими диалогами.
    return qs.filter(assignee_id=user.id)


def visible_canned_responses_qs(user: User) -> QuerySet[CannedResponse]:
    """
    Шаблоны ответов, видимые пользователю.

    - Глобальные (branch is null) доступны всем.
    - Привязанные к филиалу — только пользователям этого филиала.
    """
    qs = CannedResponse.objects.select_related("branch", "created_by")

    if not user or not user.is_authenticated or not user.is_active:
        return qs.none()

    # Полный доступ к шаблонам только ADMIN и superuser.
    if user.is_superuser or user.role == User.Role.ADMIN:
        return qs

    if user.branch_id:
        return qs.filter(models.Q(branch__isnull=True) | models.Q(branch_id=user.branch_id))

    return qs.filter(branch__isnull=True)

