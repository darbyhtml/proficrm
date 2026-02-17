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
    return msg


def assign_conversation(conversation: Conversation, user) -> None:
    """
    Назначить диалог оператору.
    """
    conversation.assignee = user
    conversation.save(update_fields=["assignee"])


RR_CACHE_KEY_PREFIX = "messenger:rr"
RR_CACHE_TTL = 60 * 60 * 24 * 7  # 7 дней (индекс не критичен к сбросу)


def auto_assign_conversation(conversation: Conversation) -> Optional[User]:
    """
    Автоназначение диалога оператору филиала по round-robin.

    Кандидаты: активные пользователи того же branch (ADMIN по умолчанию исключаем).
    Указатель round-robin хранится в Redis: messenger:rr:<branch_id>:<inbox_id>.

    Returns:
        Назначенный User или None, если кандидатов нет.
    """
    from django.core.cache import cache

    branch_id = conversation.branch_id
    inbox_id = conversation.inbox_id

    # Кандидаты: активные пользователи филиала, кроме ADMIN (операторы)
    candidates = list(
        User.objects.filter(
            branch_id=branch_id,
            is_active=True,
        )
        .exclude(role=User.Role.ADMIN)
        .order_by("id")
        .values_list("id", flat=True)
    )

    if not candidates:
        return None

    cache_key = f"{RR_CACHE_KEY_PREFIX}:{branch_id}:{inbox_id}"
    try:
        idx = cache.get(cache_key, 0)
    except Exception:
        idx = 0

    idx = idx % len(candidates)
    next_idx = (idx + 1) % len(candidates)
    try:
        cache.set(cache_key, next_idx, timeout=RR_CACHE_TTL)
    except Exception:
        pass

    assignee_id = candidates[idx]
    conversation.assignee_id = assignee_id
    conversation.save(update_fields=["assignee_id"])
    return User.objects.get(id=assignee_id)


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

