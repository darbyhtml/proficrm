from __future__ import annotations

from typing import Any

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
    ActivityEvent.objects.create(
        actor=actor,
        verb=verb,
        entity_type=entity_type,
        entity_id=str(entity_id),
        company_id=company_id,
        message=message or "",
        meta=meta or {},
    )


