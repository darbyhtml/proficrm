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

    # Защита от race: прочитаем актуальное состояние из БД. widget_api
    # может в той же транзакции уже назначить assignee через services.auto_assign —
    # тогда сигнал пропускает работу.
    try:
        fresh = Conversation.objects.filter(pk=instance.pk).only(
            "id", "assignee_id", "branch_id", "status", "inbox_id", "client_region"
        ).first()
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
        # Если branch не проставлен — делаем routing через MultiBranchRouter.
        # Это покрывает сценарий создания Conversation через admin/API оператора,
        # где routing мог не быть выполнен до save().
        if not fresh.branch_id:
            from messenger.assignment_services.region_router import MultiBranchRouter
            branch = MultiBranchRouter().route(fresh)
            if branch is not None:
                Conversation.objects.filter(pk=fresh.pk).update(branch=branch)
                fresh.branch_id = branch.id

        # Единый путь назначения (Chatwoot-style).
        if fresh.branch_id:
            messenger_services.auto_assign_conversation(fresh)
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
