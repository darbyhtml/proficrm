"""Оркестратор автоназначения нового диалога на менеджера филиала."""

from dataclasses import dataclass
from typing import Optional

from accounts.models import Branch, User
from messenger.assignment_services.branch_load_balancer import BranchLoadBalancer
from messenger.assignment_services.region_router import MultiBranchRouter
from messenger.models import Conversation


@dataclass
class AutoAssignResult:
    """Результат работы оркестратора автоназначения."""

    assigned: bool
    branch: Branch | None
    user: User | None

    def __getitem__(self, key):
        # Позволяет обращаться как к словарю: result["assigned"].
        return getattr(self, key)


def auto_assign_conversation(conversation: Conversation) -> AutoAssignResult:
    """Назначить новый диалог на филиал и свободного менеджера.

    Порядок:
    1. MultiBranchRouter определяет филиал по client_region.
    2. BranchLoadBalancer выбирает наименее загруженного онлайн-менеджера.
    3. Филиал проставляется всегда (даже без менеджера — чтобы РОП филиала
       видел диалог в общем пуле). Менеджер — если найден.

    Обновление идёт через queryset.update(), чтобы обойти инвариант
    Conversation.save(), запрещающий менять branch существующего диалога.
    После update вызываем refresh_from_db() — чтобы in-memory объект
    отражал актуальное состояние.
    """
    branch = MultiBranchRouter().route(conversation)
    if branch is None:
        return AutoAssignResult(assigned=False, branch=None, user=None)

    user = BranchLoadBalancer().pick(branch)

    update_fields = {"branch": branch}
    if user is not None:
        update_fields["assignee"] = user

    Conversation.objects.filter(pk=conversation.pk).update(**update_fields)
    conversation.refresh_from_db()

    return AutoAssignResult(
        assigned=user is not None,
        branch=branch,
        user=user,
    )
