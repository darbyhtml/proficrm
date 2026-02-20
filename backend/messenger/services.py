"""
Бизнес-логика messenger (по образцу Chatwoot).

Сервисы для работы с диалогами, сообщениями, контактами и автоназначением.
Все функции оптимизированы и защищены от race condition.
"""

from __future__ import annotations

from typing import Optional

from django.conf import settings
from django.core.cache import cache
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
    content_attributes: Optional[dict] = None,
    source_id: Optional[str] = None,
) -> Message:
    """
    Создаёт сообщение в диалоге и обновляет last_activity_at (по образцу Chatwoot).
    
    Args:
        conversation: Диалог
        direction: Направление (IN, OUT, INTERNAL)
        body: Текст сообщения
        sender_user: Пользователь-отправитель (для OUT/INTERNAL)
        sender_contact: Контакт-отправитель (для IN)
        content_attributes: Атрибуты контента (in_reply_to, deleted и т.д.)
        source_id: ID источника для дедупликации
    """
    # Дедупликация через source_id (по образцу Chatwoot)
    # Используем select_for_update для защиты от race condition при проверке дубликатов
    if source_id:
        from django.db import transaction
        with transaction.atomic():
            existing = Message.objects.select_for_update().filter(
                conversation=conversation,
                source_id=source_id
            ).first()
            if existing:
                return existing  # Дубликат
    
    msg = Message.objects.create(
        conversation=conversation,
        direction=direction,
        body=body,
        sender_user=sender_user,
        sender_contact=sender_contact,
        content_attributes=content_attributes or {},
        source_id=source_id or "",
    )
    
    # Обновление last_activity_at происходит в Message.save() через update_columns
    # (по образцу Chatwoot: conversation.update_columns(last_activity_at: created_at))
    
    # Обновить last_activity_at контакта при входящем сообщении (по образцу Chatwoot)
    if sender_contact:
        Contact.objects.filter(pk=sender_contact.pk).update(
            last_activity_at=timezone.now()
        )
    
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


def assign_conversation(conversation: Conversation, user: User) -> None:
    """
    Назначить диалог оператору с защитой от race condition (по образцу Chatwoot).
    
    Args:
        conversation: Диалог для назначения
        user: Оператор для назначения
    
    Raises:
        ValueError: Если диалог уже назначен другому оператору
    
    Note:
        Обновляет assignee_assigned_at и сбрасывает assignee_opened_at (для эскалации).
        Использует select_for_update для предотвращения одновременного назначения разным операторам.
    """
    from django.db import transaction
    
    with transaction.atomic():
        # Блокируем запись для обновления (по образцу Chatwoot)
        conv = Conversation.objects.select_for_update().get(pk=conversation.pk)
        
        # Проверяем, что диалог не назначен другому оператору
        if conv.assignee_id and conv.assignee_id != user.id:
            raise ValueError("Conversation already assigned to another agent")
        
        now = timezone.now()
        conv.assignee = user
        conv.assignee_assigned_at = now
        conv.assignee_opened_at = None
        conv.waiting_since = conv.waiting_since or now
        conv.save(update_fields=[
            "assignee", 
            "assignee_assigned_at", 
            "assignee_opened_at",
            "waiting_since"
        ])


def auto_assign_conversation(conversation: Conversation) -> Optional[User]:
    """
    Автоназначение диалога оператору филиала через Round-Robin список (по образцу Chatwoot).

    Args:
        conversation: Диалог для автоназначения
    
    Returns:
        Назначенный User или None, если кандидатов нет
    
    Note:
        Кандидаты: активные пользователи того же branch (ADMIN исключаем),
        только со статусом «онлайн». Используется Round-Robin список в Redis
        для равномерного распределения с учётом Rate Limiter.
        Защищено от race condition через select_for_update.
    """
    from django.db.models import Q, Count
    from .models import AgentProfile, Inbox
    from messenger.assignment_services.round_robin import InboxRoundRobinService
    from messenger.assignment_services.rate_limiter import default_rate_limiter

    branch_id = conversation.branch_id
    inbox_id = conversation.inbox_id
    open_statuses = [Conversation.Status.OPEN, Conversation.Status.PENDING]

    # Получаем inbox для Round-Robin сервиса
    try:
        inbox = Inbox.objects.get(id=inbox_id)
    except Inbox.DoesNotExist:
        return None

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

    # Round-Robin сервис (по образцу Chatwoot)
    round_robin_service = InboxRoundRobinService(inbox)
    
    # Фильтруем кандидатов по Rate Limiter (по образцу Chatwoot)
    allowed_agent_ids = [
        user_id for user_id in candidates
        if default_rate_limiter.check_limit(user_id)
    ]
    
    # Если все операторы превысили лимит, используем всех кандидатов
    if not allowed_agent_ids:
        allowed_agent_ids = candidates
    
    # Получаем следующего оператора из Round-Robin очереди
    assignee = round_robin_service.available_agent(allowed_agent_ids)
    
    if not assignee:
        return None
    
    # Увеличиваем счётчик Rate Limiter (по образцу Chatwoot)
    default_rate_limiter.increment(assignee.id)
    
    # Назначаем диалог с защитой от race condition (по образцу Chatwoot)
    from django.db import transaction
    
    with transaction.atomic():
        # Блокируем запись для обновления
        conv = Conversation.objects.select_for_update().get(pk=conversation.pk)
        
        # Проверяем, что диалог не был назначен другому оператору пока мы выбирали кандидата
        if conv.assignee_id and conv.assignee_id != assignee.id:
            # Диалог уже назначен - возвращаем None (или можно вернуть текущего assignee)
            return None
        
        now = timezone.now()
        conv.assignee_id = assignee.id
        conv.assignee_assigned_at = now
        conv.assignee_opened_at = None
        conv.waiting_since = None  # Очищаем waiting_since при назначении
        conv.save(update_fields=[
            "assignee_id", 
            "assignee_assigned_at", 
            "assignee_opened_at",
            "waiting_since"
        ])
    
    return assignee


def has_online_operators_for_branch(branch_id: int, inbox_id: int) -> bool:
    """
    Проверить наличие онлайн операторов в филиале (по образцу Chatwoot).
    
    Args:
        branch_id: ID филиала
        inbox_id: ID inbox (не используется, но оставлен для совместимости)
    
    Returns:
        True если есть хотя бы один онлайн оператор, иначе False
    
    Note:
        Используется для проверки возможности автоназначения перед вызовом auto_assign_conversation.
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
    Получить филиал по умолчанию для глобального inbox (по образцу Chatwoot).
    
    Returns:
        Branch или None, если не настроен
    
    Note:
        Берётся из настроек MESSENGER_DEFAULT_BRANCH_ID (ID филиала).
        Используется когда ни одно правило маршрутизации не сработало.
        Результат кэшируется для оптимизации.
    """
    from django.conf import settings
    from accounts.models import Branch
    
    # Кэширование для оптимизации (по образцу Chatwoot)
    cache_key = "messenger:default_branch"
    cached_branch = cache.get(cache_key)
    if cached_branch is not None:
        return cached_branch
    
    branch_id = getattr(settings, "MESSENGER_DEFAULT_BRANCH_ID", None)
    if not branch_id:
        cache.set(cache_key, None, timeout=3600)  # Кэшируем None на 1 час
        return None
    
    try:
        branch = Branch.objects.get(pk=branch_id)
        cache.set(cache_key, branch, timeout=3600)  # Кэшируем на 1 час
        return branch
    except (Branch.DoesNotExist, ValueError, TypeError):
        cache.set(cache_key, None, timeout=3600)
        return None


# ---------------------------------------------------------------------------
# Эскалация по таймауту (п.3 дорожной карты)
# ---------------------------------------------------------------------------

def get_conversations_eligible_for_escalation(timeout_seconds: int = 240):
    """
    Получить диалоги, которые можно эскалировать (по образцу Chatwoot).
    
    Args:
        timeout_seconds: Таймаут в секундах (по умолчанию 240 = 4 минуты)
    
    Returns:
        QuerySet диалогов для эскалации
    
    Note:
        Критерии эскалации:
        - Назначен оператор (assignee_id не пуст)
        - Оператор ещё не открыл диалог (assignee_opened_at пуст)
        - Статус open/pending
        - С момента назначения прошло не менее timeout_seconds
        - Используется assignee_assigned_at, при его отсутствии — created_at
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
    Переназначить диалог следующему оператору через Round-Robin (по образцу Chatwoot).
    
    Args:
        conversation: Диалог для эскалации
    
    Returns:
        Новый назначенный User или None, если кандидатов нет
    
    Note:
        Исключает текущего назначенного оператора. Используется та же логика кандидатов,
        что и в auto_assign, но без текущего assignee.
        Защищено от race condition через select_for_update.
    """
    from django.db.models import Q, Count
    from .models import AgentProfile, Inbox
    from messenger.assignment_services.round_robin import InboxRoundRobinService
    from messenger.assignment_services.rate_limiter import default_rate_limiter

    branch_id = conversation.branch_id
    inbox_id = conversation.inbox_id
    current_assignee_id = conversation.assignee_id
    open_statuses = [Conversation.Status.OPEN, Conversation.Status.PENDING]

    # Получаем inbox для Round-Robin сервиса
    try:
        inbox = Inbox.objects.get(id=inbox_id)
    except Inbox.DoesNotExist:
        return None

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

    # Round-Robin сервис (по образцу Chatwoot)
    round_robin_service = InboxRoundRobinService(inbox)
    
    # Фильтруем кандидатов по Rate Limiter (по образцу Chatwoot)
    allowed_agent_ids = [
        user_id for user_id in candidates
        if default_rate_limiter.check_limit(user_id)
    ]
    
    # Если все операторы превысили лимит, используем всех кандидатов
    if not allowed_agent_ids:
        allowed_agent_ids = candidates
    
    # Получаем следующего оператора из Round-Robin очереди
    assignee = round_robin_service.available_agent(allowed_agent_ids)
    
    if not assignee:
        return None
    
    # Увеличиваем счётчик Rate Limiter (по образцу Chatwoot)
    default_rate_limiter.increment(assignee.id)
    
    # Переназначаем диалог с защитой от race condition (по образцу Chatwoot)
    from django.db import transaction
    
    with transaction.atomic():
        # Блокируем запись для обновления
        conv = Conversation.objects.select_for_update().get(pk=conversation.pk)
        
        # Проверяем, что диалог не был назначен другому оператору пока мы выбирали кандидата
        if conv.assignee_id and conv.assignee_id != current_assignee_id:
            # Диалог уже переназначен - возвращаем None
            return None
        
        # Проверяем, что текущий оператор всё ещё назначен (для эскалации)
        if conv.assignee_id != current_assignee_id:
            return None
        
        now = timezone.now()
        conv.assignee_id = assignee.id
        conv.assignee_assigned_at = now
        conv.assignee_opened_at = None
        conv.waiting_since = None  # Очищаем waiting_since при назначении
        conv.save(update_fields=[
            "assignee_id", 
            "assignee_assigned_at", 
            "assignee_opened_at",
            "waiting_since"
        ])
    
    return assignee


# ---------------------------------------------------------------------------
# last_seen с троттлингом (по образцу Chatwoot)
# ---------------------------------------------------------------------------

LAST_SEEN_THROTTLE_SECONDS = getattr(
    settings,
    "MESSENGER_LAST_SEEN_THROTTLE_SECONDS",
    15,
)


def touch_assignee_last_seen(conversation: Conversation, user: User) -> timezone.datetime:
    """
    Обновить assignee_last_read_at/agent_last_seen_at с троттлингом (по образцу Chatwoot).
    
    Args:
        conversation: Диалог для обновления
        user: Оператор, который просматривает диалог
    
    Returns:
        Время обновления (может быть кэшированное, если троттлинг активен)
    
    Note:
        Не пишет в БД чаще, чем раз в MESSENGER_LAST_SEEN_THROTTLE_SECONDS секунд
        для каждой пары (оператор, диалог). Использует Redis для кэширования.
    """
    if not conversation.pk or user is None:
        return timezone.now()

    now = timezone.now()
    cache_key = f"messenger:last_seen:agent:{user.id}:{conversation.pk}"

    last_ts = cache.get(cache_key)
    if isinstance(last_ts, (int, float)):
        # last_ts хранится как timestamp
        if now.timestamp() - last_ts < LAST_SEEN_THROTTLE_SECONDS:
            # Слишком часто — просто возвращаем текущее время без записи в БД
            return now

    Conversation.objects.filter(pk=conversation.pk).update(
        assignee_last_read_at=now,
        agent_last_seen_at=now,
    )
    cache.set(cache_key, now.timestamp(), timeout=LAST_SEEN_THROTTLE_SECONDS)
    return now


def touch_contact_last_seen(conversation: Conversation, contact_id) -> timezone.datetime:
    """
    Обновить contact_last_seen_at/last_activity_at контакта с троттлингом (по образцу Chatwoot).
    
    Args:
        conversation: Диалог для обновления
        contact_id: ID контакта (UUID)
    
    Returns:
        Время обновления (может быть кэшированное, если троттлинг активен)
    
    Note:
        Не пишет в БД чаще, чем раз в MESSENGER_LAST_SEEN_THROTTLE_SECONDS секунд
        для каждой пары (контакт, диалог). Использует Redis для кэширования.
        Обновляет как contact_last_seen_at диалога, так и last_activity_at контакта.
    """
    if not conversation.pk or not contact_id:
        return timezone.now()

    now = timezone.now()
    cache_key = f"messenger:last_seen:contact:{contact_id}:{conversation.pk}"

    last_ts = cache.get(cache_key)
    if isinstance(last_ts, (int, float)):
        if now.timestamp() - last_ts < LAST_SEEN_THROTTLE_SECONDS:
            return now

    Conversation.objects.filter(pk=conversation.pk).update(
        contact_last_seen_at=now,
    )
    Contact.objects.filter(pk=contact_id).update(
        last_activity_at=now,
    )
    cache.set(cache_key, now.timestamp(), timeout=LAST_SEEN_THROTTLE_SECONDS)
    return now


def transfer_conversation_to_branch(conversation: Conversation, branch: "Branch") -> Optional[User]:
    """
    Перенести диалог в другой филиал с автоназначением (по образцу Chatwoot).
    
    Args:
        conversation: Диалог для переноса
        branch: Целевой филиал
    
    Returns:
        Назначенный оператор или None, если автоназначение не удалось
    
    Note:
        Обновляет branch диалога и пытается автоматически назначить оператора
        из нового филиала через auto_assign_conversation.
    """
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

