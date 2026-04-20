from __future__ import annotations

from django.db import models
from django.db.models import F, Q, QuerySet

from accounts.models import User

from .models import CannedResponse, Conversation, Inbox, Message


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

    # Тендерист не участвует в мессенджере вообще.
    if user.role == User.Role.TENDERIST:
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

    # Тендерист не участвует в мессенджере вообще.
    if user.role == User.Role.TENDERIST:
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


def get_messenger_unread_count(user: User) -> int:
    """
    Суммарное число непрочитанных входящих сообщений по всем диалогам (по образцу Chatwoot).

    Args:
        user: Пользователь для подсчёта непрочитанных сообщений

    Returns:
        Количество непрочитанных входящих сообщений

    Note:
        Непрочитанное = входящее (IN) сообщение с created_at > assignee_last_read_at
        (или все входящие, если assignee_last_read_at не задан).
        Использует кэширование для оптимизации частых запросов.
    """
    if not user or not user.is_authenticated or not user.is_active:
        return 0

    # Кэширование для оптимизации (по образцу Chatwoot)
    from django.conf import settings
    from django.core.cache import cache

    cache_key = f"messenger:unread_count:{user.id}"
    cache_timeout = getattr(settings, "MESSENGER_UNREAD_COUNT_CACHE_TIMEOUT", 30)  # 30 секунд

    cached_count = cache.get(cache_key)
    if cached_count is not None:
        return cached_count

    count = (
        Message.objects.filter(
            conversation__assignee_id=user.id,
            direction=Message.Direction.IN,
        )
        .filter(
            Q(conversation__assignee_last_read_at__isnull=True)
            | Q(created_at__gt=F("conversation__assignee_last_read_at"))
        )
        .count()
    )

    # Кэшируем результат
    cache.set(cache_key, count, timeout=cache_timeout)

    return count


def get_visible_conversations(user) -> QuerySet[Conversation]:
    """Возвращает queryset диалогов, видимых пользователю по его роли.

    Используется live-chat модулем для разграничения доступа к диалогам
    независимо от общего ``visible_conversations_qs`` (который привязан
    к ``data_scope``). Правила:

    - ADMIN / is_superuser — все диалоги;
    - BRANCH_DIRECTOR / SALES_HEAD (РОП) — все диалоги своего филиала
      (по ``branch_id`` диалога или по ``inbox.branch_id``);
    - GROUP_MANAGER — диалоги подчинённых (fallback: только свои, если
      в проекте нет поля иерархии ``group_manager`` у User);
    - MANAGER — свои назначенные + пул своего филиала
      (``assignee IS NULL AND branch == user.branch``);
    - Прочие роли — только свои назначенные.
    """
    if user.is_superuser or getattr(user, "role", None) == User.Role.ADMIN:
        return Conversation.objects.all()

    role = getattr(user, "role", None)
    branch_id = getattr(user, "branch_id", None)

    if role in (User.Role.BRANCH_DIRECTOR, User.Role.SALES_HEAD):
        if not branch_id:
            return Conversation.objects.none()
        return Conversation.objects.filter(
            Q(branch_id=branch_id) | Q(inbox__branch_id=branch_id)
        ).distinct()

    if role == User.Role.GROUP_MANAGER:
        if hasattr(User, "group_manager"):
            sub_ids = list(User.objects.filter(group_manager=user).values_list("id", flat=True))
            sub_ids.append(user.id)
            return Conversation.objects.filter(assignee_id__in=sub_ids)
        return Conversation.objects.filter(assignee=user)

    if role == User.Role.MANAGER:
        if not branch_id:
            return Conversation.objects.filter(assignee=user)
        return Conversation.objects.filter(
            Q(assignee=user) | Q(assignee__isnull=True, branch_id=branch_id)
        ).distinct()

    return Conversation.objects.filter(assignee=user)
