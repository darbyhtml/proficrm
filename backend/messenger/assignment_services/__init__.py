"""
Сервисы для автоназначения операторов (по образцу Chatwoot).

Этот модуль содержит специализированные сервисы для автоназначения:
- Round-Robin через Redis список
- Rate Limiter для ограничения назначений

Основные функции находятся в messenger.services (services.py).
"""

# Импортируем сервисы автоназначения
from .rate_limiter import AssignmentRateLimiter, default_rate_limiter
from .region_router import MultiBranchRouter
from .round_robin import InboxRoundRobinService

__all__ = [
    "AssignmentRateLimiter",
    "InboxRoundRobinService",
    "MultiBranchRouter",
    "default_rate_limiter",
]
