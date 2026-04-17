"""Сигналы мессенджера."""

import logging

from django.db.models.signals import post_save
from django.dispatch import receiver

from messenger.models import Conversation

logger = logging.getLogger("messenger.auto_assign")


@receiver(post_save, sender=Conversation)
def auto_assign_new_conversation(sender, instance: Conversation, created: bool, **kwargs):
    """Автоназначение только что созданного диалога на менеджера филиала.

    F5 Round 1 (unify): единый путь назначения через services.auto_assign_conversation
    (Chatwoot-style round-robin + rate-limiter + select_for_update). Раньше были две
    параллельные реализации (assignment_services/auto_assign.py делал own routing
    через MultiBranchRouter + BranchLoadBalancer), что создавало race condition:
    widget_api сначала делал routing+save, затем сигнал перезаписывал назначение.

    Новое поведение:
    1. Срабатывает только на create + status=OPEN.
    2. refresh_from_db() — видим актуальное состояние, созданное в transaction widget_api.
    3. Skip если assignee_id уже проставлен (widget_api уже назначил).
    4. Если branch_id не задан — вызываем routing MultiBranchRouter (Conversation создан
       через admin/ручную операцию без geo-routing).
    5. Вызываем services.auto_assign_conversation — он сам делает select_for_update,
       round-robin по inbox, rate-limiter, idempotent.
    """
    if not created:
        return
    if instance.status != Conversation.Status.OPEN:
        return

    # Защита от race: refresh_from_db на копии объекта. widget_api в той же
    # транзакции может уже назначить assignee через services.auto_assign —
    # тогда сигнал пропускает работу.
    # ВАЖНО: не используем .only(...) здесь — MultiBranchRouter читает полный
    # объект (client_region, region, branch), частичная загрузка полей вызовет
    # extra-queries или несогласованность.
    try:
        fresh = Conversation.objects.filter(pk=instance.pk).first()
    except Exception:
        fresh = None
    if fresh is None:
        return
    if fresh.assignee_id:
        # Уже назначен — в том числе через widget_api.services.auto_assign_conversation
        return

    # Ленивые импорты — избегаем circular при старте приложения.
    from messenger import services as messenger_services

    try:
        # Routing по региону клиента: Conversation.save() авто-ставит branch
        # из inbox.branch_id для non-global inbox. Но при client_region из
        # другого подразделения (например inbox=ekb, client_region="Томская")
        # routing должен переопределить branch на tmn. Поэтому вызываем
        # MultiBranchRouter ВСЕГДА при create — он сам решит, оставить
        # текущий branch или переопределить.
        from messenger.assignment_services.region_router import MultiBranchRouter
        routed_branch = MultiBranchRouter().route(fresh)
        if routed_branch is not None and routed_branch.id != fresh.branch_id:
            # Conversation.save() блокирует смену branch после create, поэтому
            # используем queryset.update() для обхода инварианта.
            Conversation.objects.filter(pk=fresh.pk).update(branch=routed_branch)
            fresh.refresh_from_db()
            # Обновляем in-memory instance тоже — чтобы внешний код видел branch
            instance.branch_id = fresh.branch_id
            instance.branch = fresh.branch

        # Единый путь назначения (Chatwoot-style).
        if fresh.branch_id:
            assigned_user = messenger_services.auto_assign_conversation(fresh)
            if assigned_user is not None:
                # Обновим in-memory instance для consumer'а сигнала (тесты и т.п.)
                instance.assignee_id = assigned_user.id
                instance.assignee = assigned_user
    except Exception:
        logger.exception("auto_assign failed for conversation %s", instance.pk)


@receiver(post_save, sender=Conversation)
def autolink_company_on_create(sender, instance: Conversation, created: bool, **kwargs):
    """Автосвязка диалога с компанией по контакту (email domain / phone).

    Срабатывает только при создании диалога и только если company ещё не
    установлена. Сам сервис использует queryset.update(), чтобы не вызывать
    рекурсию сигнала.
    """
    if not created:
        return
    if instance.company_id:
        return
    if not instance.contact_id:
        return

    # Ленивый импорт — избегаем циклов при старте приложения.
    from messenger.company_autolink import autolink_conversation_company

    try:
        autolink_conversation_company(instance)
    except Exception:
        logger.exception("company autolink failed for conversation %s", instance.pk)
