"""
Company deletion workflow — единая точка для удаления карточки компании.

До 2026-04-20 логика удаления компании была продублирована в двух view:
- ``company_delete_direct`` (строки ~550-612 в company_detail.py)
- ``company_delete_request_approve`` (строки ~475-547)

Одинаковый блок из 7 операций в ``transaction.atomic()``:
1. Удалить ``CompanySearchIndex`` (защита от IntegrityError на каскаде).
2. ``_detach_client_branches`` — головная компания → дочки автономны.
3. ``_notify_head_deleted_with_branches`` — уведомить менеджеров дочек.
4. Явно удалить ``Task.objects.filter(company_id=...)`` — Task.company
   on_delete=SET_NULL, а нам нужно удалить.
5. ``log_event`` с актом DELETE.
6. ``company.delete()`` — собственно каскад.
7. ``except IntegrityError`` — сбой индекса поиска → ошибка пользователю.

Выделено в **phase 3** плана рефакторинга company_detail (2026-04-20).
Одна ошибка в этой цепочке — риск потерять данные, поэтому логика
централизована и покрывается тестами.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from django.db import IntegrityError, transaction

from accounts.models import User
from audit.models import ActivityEvent
from companies.models import Company, CompanySearchIndex

logger = logging.getLogger(__name__)


class CompanyDeletionError(Exception):
    """Бросается, если компанию не удалось удалить целиком (IntegrityError etc.)."""


def execute_company_deletion(
    *,
    company: Company,
    actor: User,
    reason: str = "",
    source: str = "direct",
    extra_meta: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Единый workflow удаления компании со всеми побочными эффектами.

    Args:
        company: объект ``Company`` для удаления (должен быть свежий SELECT).
        actor: пользователь, инициирующий удаление.
        reason: человеко-читаемая причина (из request.POST["reason"] или note).
        source: ``"direct"`` (обычное удаление) или ``"approve_request"``
            (подтверждение ранее созданного запроса). Влияет только на
            ``log_event`` message, не на саму логику.
        extra_meta: любые дополнительные поля для ``log_event.meta``
            (например, ``{"request_id": req.id}``).

    Returns:
        ``{"company_pk": <uuid>, "detached_count": int, "branches_notified": int,
            "tasks_deleted_count": int}``.

    Raises:
        CompanyDeletionError: если удаление не удалось (IntegrityError на индексе).
            Вызывающий должен показать messages.error пользователю.
    """
    # Локальный импорт, чтобы избежать цикла между companies.services и ui.views._base.
    from ui.views._base import (
        _detach_client_branches,
        _notify_head_deleted_with_branches,
        log_event,
    )
    from tasksapp.models import Task

    # Сохраняем pk ДО delete() — после каскада ``company.pk`` становится None,
    # а IntegrityError может возникнуть уже на COMMIT.
    company_pk = company.id
    extra_meta = dict(extra_meta or {})

    try:
        with transaction.atomic():
            # (1) Индекс поиска — сносим явно до каскада.
            CompanySearchIndex.objects.filter(company_id=company_pk).delete()

            # (2) Если удаляем "головную" — дочки становятся самостоятельными.
            detached = _detach_client_branches(head_company=company)

            # (3) Уведомляем менеджеров отцепившихся дочек.
            branches_notified = _notify_head_deleted_with_branches(
                actor=actor,
                head_company=company,
                detached=detached,
            )

            # (4) Hotfix 2026-04-18: явно удаляем задачи ДО company.delete().
            # Task.company on_delete=SET_NULL — без этой строки задачи
            # остаются «висящие» без компании, пустые фильтры возвращают их.
            tasks_deleted_count = Task.objects.filter(company_id=company_pk).delete()[0]

            # (5) Audit event.
            log_meta: dict[str, Any] = {
                "reason": (reason or "")[:500],
                "detached_branches": [str(c.id) for c in detached[:50]],
                "detached_count": len(detached),
                "branches_notified": branches_notified,
                "tasks_deleted_count": tasks_deleted_count,
            }
            log_meta.update(extra_meta)
            log_event(
                actor=actor,
                verb=ActivityEvent.Verb.DELETE,
                entity_type="company",
                entity_id=str(company_pk),
                company_id=company_pk,
                message=(
                    "Компания удалена (по запросу)"
                    if source == "approve_request"
                    else "Компания удалена"
                ),
                meta=log_meta,
            )

            # (6) Каскадное удаление.
            company.delete()
    except IntegrityError as exc:
        logger.exception(
            "Failed to delete company %s (source=%s): CompanySearchIndex integrity error",
            company_pk,
            source,
        )
        raise CompanyDeletionError(
            "Не удалось полностью удалить компанию из-за проблем с индексом поиска. "
            "Обратитесь к администратору."
        ) from exc

    return {
        "company_pk": company_pk,
        "detached_count": len(detached),
        "branches_notified": branches_notified,
        "tasks_deleted_count": tasks_deleted_count,
    }
