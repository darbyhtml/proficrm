"""
Celery signals для observability — Wave 0.4.

Привязывает к каждой task'е:
- request_id (генерируется новый, если не переброшен из вызывающего кода)
- Sentry scope tags (task_name, task_id, request_id)

Подключается один раз в `backend/crm/celery.py` через `register_signals()`.
"""

from __future__ import annotations

import logging
import uuid

from celery.signals import task_postrun, task_prerun

from core.request_id import _thread_local

logger = logging.getLogger(__name__)


def register_signals() -> None:
    """Подключить сигналы. Вызывается один раз при старте Celery worker/beat."""
    task_prerun.connect(_before_task, weak=False)
    task_postrun.connect(_after_task, weak=False)


def _before_task(
    sender=None,
    task_id=None,
    task=None,
    args=None,
    kwargs=None,
    **extra,
) -> None:
    """Генерирует request_id для task, кладёт в thread-local + sentry scope."""
    # Если вызвавший код передал request_id через headers/kwargs — используем его.
    # Это позволяет cross-reference log'ов: web-request → task → dependent task.
    request_id = None
    if isinstance(kwargs, dict):
        request_id = kwargs.pop("_request_id", None)
    if not request_id:
        request_id = str(uuid.uuid4())[:8]

    _thread_local.request_id = request_id

    # Sentry scope.
    try:
        import sentry_sdk

        scope = sentry_sdk.Scope.get_current_scope()
        scope.set_tag("request_id", request_id)
        scope.set_tag("task_name", getattr(task, "name", "unknown") if task else "unknown")
        scope.set_tag("task_id", str(task_id) if task_id else "unknown")
    except ImportError:
        pass


def _after_task(
    sender=None,
    task_id=None,
    **extra,
) -> None:
    """Чистит thread-local после task (важно для потоков Celery pool)."""
    if hasattr(_thread_local, "request_id"):
        delattr(_thread_local, "request_id")
