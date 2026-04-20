"""BranchLoadBalancer — выбор онлайн-менеджера филиала с минимальной нагрузкой."""

from typing import Optional

from django.db.models import Count, Q

from accounts.models import Branch, User
from messenger.models import Conversation


class BranchLoadBalancer:
    """Выбирает наименее загруженного онлайн-менеджера указанного филиала.

    Кандидаты: активные пользователи филиала с ролью MANAGER и статусом
    messenger_online=True. Нагрузкой считается число открытых (status=OPEN)
    диалогов, назначенных на пользователя. При равенстве нагрузки — случайный.
    """

    def pick(self, branch: Branch) -> Optional[User]:
        candidates = (
            User.objects.filter(
                branch=branch,
                role=User.Role.MANAGER,
                is_active=True,
                messenger_online=True,
            )
            .annotate(
                active_count=Count(
                    "assigned_conversations",
                    filter=Q(assigned_conversations__status=Conversation.Status.OPEN),
                    distinct=True,
                )
            )
            .order_by("active_count", "?")
        )
        return candidates.first()
