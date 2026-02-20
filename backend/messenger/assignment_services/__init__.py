"""
Сервисы для автоназначения операторов (по образцу Chatwoot).

Этот модуль содержит специализированные сервисы для автоназначения:
- Round-Robin через Redis список
- Rate Limiter для ограничения назначений

Основные функции находятся в messenger.services (services.py).
"""

# Импортируем сервисы автоназначения
from .round_robin import InboxRoundRobinService
from .rate_limiter import AssignmentRateLimiter, default_rate_limiter

__all__ = [
    'InboxRoundRobinService',
    'AssignmentRateLimiter',
    'default_rate_limiter',
]
