"""
Бизнес-логика messenger.

На Этапе 1 оставляем только заготовки, чтобы структурировать кодовую базу.
Полная реализация (routing, assign_conversation, обработка входящих сообщений)
будет добавлена на этапах 4–5.
"""

from __future__ import annotations

from typing import Optional

from django.utils import timezone

from accounts.models import User
from .models import Conversation, Message, Contact
from .integrations import notify_message


def create_or_get_contact(
    *,
    external_id: str | None = None,
    email: str | None = None,
    phone: str | None = None,
    name: str | None = None,
    update_if_exists: bool = True,
) -> Contact:
    """
    Создаёт или получает Contact, при необходимости обновляя поля.

    Args:
        external_id: Внешний идентификатор (visitor_id)
        email: Email контакта
        phone: Телефон контакта
        name: Имя контакта
        update_if_exists: Если True, обновляет поля существующего контакта новыми значениями (не перетирает на None)

    Returns:
        Contact instance
    """
    qs = Contact.objects.all()
    contact = None

    # Ищем по external_id в первую очередь
    if external_id:
        contact = qs.filter(external_id=external_id).first()
        if contact:
            if update_if_exists:
                # Обновляем поля только если переданы новые значения (не перетираем на None)
                update_fields = []
                if name and name != contact.name:
                    contact.name = name
                    update_fields.append("name")
                if email and email != contact.email:
                    contact.email = email
                    update_fields.append("email")
                if phone and phone != contact.phone:
                    contact.phone = phone
                    update_fields.append("phone")
                if update_fields:
                    contact.save(update_fields=update_fields)
            return contact

    # Если не нашли по external_id, ищем по email
    if email:
        contact = qs.filter(email=email).first()
        if contact:
            if update_if_exists:
                # Обновляем external_id и другие поля, если переданы
                update_fields = []
                if external_id and external_id != contact.external_id:
                    contact.external_id = external_id
                    update_fields.append("external_id")
                if name and name != contact.name:
                    contact.name = name
                    update_fields.append("name")
                if phone and phone != contact.phone:
                    contact.phone = phone
                    update_fields.append("phone")
                if update_fields:
                    contact.save(update_fields=update_fields)
            return contact

    # Если не нашли по email, ищем по phone
    if phone:
        contact = qs.filter(phone=phone).first()
        if contact:
            if update_if_exists:
                update_fields = []
                if external_id and external_id != contact.external_id:
                    contact.external_id = external_id
                    update_fields.append("external_id")
                if name and name != contact.name:
                    contact.name = name
                    update_fields.append("name")
                if email and email != contact.email:
                    contact.email = email
                    update_fields.append("email")
                if update_fields:
                    contact.save(update_fields=update_fields)
            return contact

    # Контакт не найден - создаём новый
    contact = Contact.objects.create(
        external_id=external_id or "",
        email=email or "",
        phone=phone or "",
        name=name or "",
    )
    return contact


def record_message(
    *,
    conversation: Conversation,
    direction: str,
    body: str,
    sender_user: Optional[User] = None,
    sender_contact: Optional[Contact] = None,
) -> Message:
    """
    Создаёт сообщение в диалоге и обновляет last_message_at.
    """
    msg = Message.objects.create(
        conversation=conversation,
        direction=direction,
        body=body,
        sender_user=sender_user,
        sender_contact=sender_contact,
    )
    Conversation.objects.filter(pk=conversation.pk).update(last_message_at=timezone.now())
    try:
        notify_message(msg)
    except Exception:
        import logging as _logging

        _logger = _logging.getLogger("messenger.integrations")
        _logger.warning(
            "Webhook notify_message failed from record_message",
            exc_info=True,
            extra={"conversation_id": conversation.id, "message_id": msg.id, "direction": direction},
        )
    return msg


def assign_conversation(conversation: Conversation, user) -> None:
    """
    Назначить диалог оператору.
    Обновляет assignee_assigned_at и сбрасывает assignee_opened_at (для эскалации).
    """
    now = timezone.now()
    conversation.assignee = user
    conversation.assignee_assigned_at = now
    conversation.assignee_opened_at = None
    conversation.save(update_fields=["assignee", "assignee_assigned_at", "assignee_opened_at"])


RR_CACHE_KEY_PREFIX = "messenger:rr"
RR_CACHE_TTL = 60 * 60 * 24 * 7  # 7 дней (индекс не критичен к сбросу)


def auto_assign_conversation(conversation: Conversation) -> Optional[User]:
    """
    Автоназначение диалога оператору филиала: равномерное распределение с учётом нагрузки.

    Кандидаты: активные пользователи того же branch (ADMIN исключаем),
    только со статусом «онлайн». Список сортируется по нагрузке (число открытых/ожидающих
    диалогов у оператора — меньше сначала), затем round-robin по этому списку.
    Указатель round-robin хранится в Redis: messenger:rr:<branch_id>:<inbox_id>.

    Returns:
        Назначенный User или None, если кандидатов нет.
    """
    from django.core.cache import cache
    from django.db.models import Q, Count
    from .models import AgentProfile

    branch_id = conversation.branch_id
    inbox_id = conversation.inbox_id
    open_statuses = [Conversation.Status.OPEN, Conversation.Status.PENDING]

    # Кандидаты: активные пользователи филиала, кроме ADMIN, только «онлайн»
    # + число назначенных открытых/ожидающих диалогов (нагрузка)
    candidates_qs = (
        User.objects.filter(
            branch_id=branch_id,
            is_active=True,
        )
        .exclude(role=User.Role.ADMIN)
        .exclude(
            Q(agent_profile__status=AgentProfile.Status.AWAY)
            | Q(agent_profile__status=AgentProfile.Status.BUSY)
            | Q(agent_profile__status=AgentProfile.Status.OFFLINE)
        )
        .annotate(
            open_count=Count(
                "assigned_conversations",
                filter=Q(assigned_conversations__status__in=open_statuses),
                distinct=True,
            )
        )
        .order_by("open_count", "id")
    )
    candidates = list(candidates_qs.values_list("id", flat=True))

    if not candidates:
        return None

    cache_key = f"{RR_CACHE_KEY_PREFIX}:{branch_id}:{inbox_id}"
    try:
        idx = cache.get(cache_key, 0)
    except Exception:
        idx = 0

    idx = int(idx) % len(candidates)
    next_idx = (idx + 1) % len(candidates)
    try:
        cache.set(cache_key, next_idx, timeout=RR_CACHE_TTL)
    except Exception:
        pass

    assignee_id = candidates[idx]
    now = timezone.now()
    conversation.assignee_id = assignee_id
    conversation.assignee_assigned_at = now
    conversation.assignee_opened_at = None
    conversation.save(update_fields=["assignee_id", "assignee_assigned_at", "assignee_opened_at"])
    return User.objects.get(id=assignee_id)


def has_online_operators_for_branch(branch_id: int, inbox_id: int) -> bool:
    """
    Есть ли в филиале хотя бы один «онлайн» оператор (кандидат для автоназначения).
    """
    from django.db.models import Q
    from .models import AgentProfile

    return (
        User.objects.filter(
            branch_id=branch_id,
            is_active=True,
        )
        .exclude(role=User.Role.ADMIN)
        .exclude(
            Q(agent_profile__status=AgentProfile.Status.AWAY)
            | Q(agent_profile__status=AgentProfile.Status.BUSY)
            | Q(agent_profile__status=AgentProfile.Status.OFFLINE)
        )
        .exists()
    )


def select_routing_rule(
    inbox: "models.Inbox",
    region: Optional["models.Region"] = None,
) -> Optional["models.RoutingRule"]:
    """
    Выбрать правило маршрутизации для inbox и region.
    
    Логика:
    1. Если region задан - ищем активное правило с этим region и inbox, сортируем по priority
    2. Если не найдено - ищем fallback правило для inbox
    3. Если ничего не найдено - возвращаем None
    
    Args:
        inbox: Inbox для маршрутизации
        region: Регион (может быть None)
    
    Returns:
        RoutingRule или None
    """
    from . import models
    
    # Ищем активные правила для этого inbox
    rules_qs = models.RoutingRule.objects.filter(
        inbox=inbox,
        is_active=True,
    ).select_related("branch").prefetch_related("regions")
    
    # Если region задан - ищем правило с этим region
    if region:
        matching_rules = [
            rule for rule in rules_qs
            if rule.regions.filter(id=region.id).exists()
        ]
        if matching_rules:
            # Сортируем по priority (меньше = выше приоритет)
            matching_rules.sort(key=lambda r: (r.priority, r.id))
            return matching_rules[0]
    
    # Если не найдено правило по region - ищем fallback
    fallback_rule = rules_qs.filter(is_fallback=True).first()
    if fallback_rule:
        return fallback_rule

    return None


def get_default_branch_for_messenger():
    """
    Возвращает филиал по умолчанию для глобального inbox,
    когда ни одно правило маршрутизации не сработало.

    Берётся из настроек MESSENGER_DEFAULT_BRANCH_ID (ID филиала).
    """
    from django.conf import settings
    branch_id = getattr(settings, "MESSENGER_DEFAULT_BRANCH_ID", None)
    if not branch_id:
        return None
    from accounts.models import Branch
    try:
        return Branch.objects.get(pk=branch_id)
    except (Branch.DoesNotExist, ValueError, TypeError):
        return None


# ---------------------------------------------------------------------------
# Эскалация по таймауту (п.3 дорожной карты)
# ---------------------------------------------------------------------------

def get_conversations_eligible_for_escalation(timeout_seconds: int = 240):
    """
    Диалоги, которые можно эскалировать: назначен оператор, он ещё не открыл диалог,
    статус open/pending, с момента назначения прошло не менее timeout_seconds (по умолчанию 4 мин).

    Используется assignee_assigned_at, при его отсутствии — created_at.
    """
    from django.utils import timezone
    from datetime import timedelta
    threshold = timezone.now() - timedelta(seconds=timeout_seconds)
    qs = Conversation.objects.filter(
        assignee_id__isnull=False,
        assignee_opened_at__isnull=True,
        status__in=[Conversation.Status.OPEN, Conversation.Status.PENDING],
    )
    # assignee_assigned_at может быть пустым у старых записей
    from django.db.models import Q
    qs = qs.filter(
        Q(assignee_assigned_at__lte=threshold) | Q(assignee_assigned_at__isnull=True, created_at__lte=threshold)
    )
    return qs


def escalate_conversation(conversation: Conversation) -> Optional[User]:
    """
    Переназначить диалог следующему оператору (round-robin по тому же филиалу,
    исключая текущего назначенного). Используется та же логика кандидатов, что и в auto_assign,
    но без текущего assignee.

    Returns:
        Новый назначенный User или None, если кандидатов нет (например, один оператор в филиале).
    """
    from django.core.cache import cache
    from django.db.models import Q, Count
    from .models import AgentProfile

    branch_id = conversation.branch_id
    inbox_id = conversation.inbox_id
    current_assignee_id = conversation.assignee_id
    open_statuses = [Conversation.Status.OPEN, Conversation.Status.PENDING]

    candidates_qs = (
        User.objects.filter(
            branch_id=branch_id,
            is_active=True,
        )
        .exclude(role=User.Role.ADMIN)
        .exclude(id=current_assignee_id)
        .exclude(
            Q(agent_profile__status=AgentProfile.Status.AWAY)
            | Q(agent_profile__status=AgentProfile.Status.BUSY)
            | Q(agent_profile__status=AgentProfile.Status.OFFLINE)
        )
        .annotate(
            open_count=Count(
                "assigned_conversations",
                filter=Q(assigned_conversations__status__in=open_statuses),
                distinct=True,
            )
        )
        .order_by("open_count", "id")
    )
    candidates = list(candidates_qs.values_list("id", flat=True))

    if not candidates:
        return None

    cache_key = f"{RR_CACHE_KEY_PREFIX}:{branch_id}:{inbox_id}"
    try:
        idx = cache.get(cache_key, 0)
    except Exception:
        idx = 0
    idx = int(idx) % len(candidates)
    next_idx = (idx + 1) % len(candidates)
    try:
        cache.set(cache_key, next_idx, timeout=RR_CACHE_TTL)
    except Exception:
        pass

    assignee_id = candidates[idx]
    now = timezone.now()
    conversation.assignee_id = assignee_id
    conversation.assignee_assigned_at = now
    conversation.assignee_opened_at = None
    conversation.save(update_fields=["assignee_id", "assignee_assigned_at", "assignee_opened_at"])
    return User.objects.get(id=assignee_id)


def transfer_conversation_to_branch(conversation: Conversation, branch: "Branch") -> Optional[User]:
    """
    Передать диалог в другой филиал и назначить первому свободному оператору там.

    Допустимо только для глобального inbox (inbox.branch_id is None).
    Меняет conversation.branch на переданный филиал, сбрасывает назначение,
    затем вызывает автоназначение в новом филиале.

    Returns:
        Назначенный User или None, если в филиале нет подходящих операторов.
    """
    from accounts.models import Branch
    if conversation.inbox.branch_id is not None:
        return None
    if not isinstance(branch, Branch) or not branch.id:
        return None
    conversation.branch_id = branch.id
    conversation.assignee_id = None
    conversation.assignee_assigned_at = None
    conversation.assignee_opened_at = None
    conversation.save(update_fields=["branch_id", "assignee_id", "assignee_assigned_at", "assignee_opened_at"])
    return auto_assign_conversation(conversation)

