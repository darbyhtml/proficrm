from __future__ import annotations

from typing import Any

from django.utils import timezone

from audit.models import ActivityEvent


def log_event(
    *,
    actor,
    verb: str,
    entity_type: str,
    entity_id: str,
    company_id=None,
    message: str = "",
    meta: dict[str, Any] | None = None,
):
    """
    Универсальный хелпер для создания ActivityEvent.

    Дополнительно, если передан company_id (или событие относится к компании),
    обновляет Company.updated_at, чтобы в списках и фильтрах использовалось
    время последней активности по карточке компании.
    """
    # Приводим entity_id к строке один раз
    entity_id_str = str(entity_id)

    # Если company_id не передан явно, но событие относится к компании,
    # используем entity_id как идентификатор компании.
    company_id_for_update = company_id or (entity_id if entity_type == "company" else None)

    event = ActivityEvent.objects.create(
        actor=actor,
        verb=verb,
        entity_type=entity_type,
        entity_id=entity_id_str,
        company_id=company_id,
        message=message or "",
        meta=meta or {},
    )

    # Мягко обновляем updated_at компании: сбой здесь не должен ломать основной поток.
    if company_id_for_update:
        try:
            # Локальный импорт, чтобы избежать циклических зависимостей между приложениями.
            from companies.models import Company

            Company.objects.filter(id=company_id_for_update).update(updated_at=timezone.now())
        except Exception:
            # Ошибки при вспомогательном обновлении не должны мешать работе CRM.
            # При необходимости сюда можно добавить отдельное логирование.
            pass

    return event



