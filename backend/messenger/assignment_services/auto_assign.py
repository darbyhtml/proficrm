"""Legacy-обёртка автоназначения. DEPRECATED в F5 Round 1 (2026-04-18).

Основной путь — `messenger.services.auto_assign_conversation` (Chatwoot-style
round-robin + rate-limiter + select_for_update), вызываемый из signals.py
и widget_api. Этот файл остаётся ради backward compatibility тестов.

Функция делегирует:
1. routing → MultiBranchRouter (если branch не задан)
2. назначение оператора → services.auto_assign_conversation (один путь)

Старый BranchLoadBalancer больше не используется — он выбирал по
`messenger_online`, что рассинхронизировано с `AgentProfile.Status`
(см. chat-audit P0-2). services.auto_assign_conversation использует
только AgentProfile.Status как единый источник правды.
"""

from dataclasses import dataclass
from typing import Optional

from accounts.models import Branch, User
from messenger.assignment_services.region_router import MultiBranchRouter
from messenger.models import Conversation


@dataclass
class AutoAssignResult:
    """Результат работы оркестратора автоназначения."""

    assigned: bool
    branch: Optional[Branch]
    user: Optional[User]

    def __getitem__(self, key):
        # Позволяет обращаться как к словарю: result["assigned"].
        return getattr(self, key)


def auto_assign_conversation(conversation: Conversation) -> AutoAssignResult:
    """Назначить новый диалог на филиал и свободного менеджера.

    F5 Round 1 (unify 2026-04-18): делегирует в services.auto_assign_conversation,
    устраняя двойную реализацию и race condition между сигналом и widget_api.

    Порядок:
    1. Если branch не задан → MultiBranchRouter (по client_region).
    2. services.auto_assign_conversation выбирает оператора через round-robin
       + rate-limiter + select_for_update.
    """
    # Routing: если branch ещё не определён
    branch = None
    if conversation.branch_id:
        branch = conversation.branch
    else:
        branch = MultiBranchRouter().route(conversation)
        if branch is None:
            return AutoAssignResult(assigned=False, branch=None, user=None)
        Conversation.objects.filter(pk=conversation.pk).update(branch=branch)
        conversation.refresh_from_db()

    # Единый путь назначения оператора
    from messenger import services as messenger_services
    assignee = messenger_services.auto_assign_conversation(conversation)
    conversation.refresh_from_db()

    return AutoAssignResult(
        assigned=assignee is not None,
        branch=branch,
        user=assignee,
    )
