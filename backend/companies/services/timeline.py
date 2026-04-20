"""
Timeline service — сборка единой ленты событий карточки компании.

До 2026-04-20 этот код был продублирован в двух местах
`ui/views/company_detail.py`:
- функция `company_detail` (inline, строки ~214-268)
- функция `company_timeline_items` (inline, строки ~2828-2870)

Оба блока полностью идентичны по логике (7 источников, сортировка по
дате, merge в список dict'ов). Дублирование делало рефактор невозможным
без синхронного правления обоих мест.

Выделено в service как **Phase 1** плана рефакторинга company_detail
(см. refactoring-specialist plan 2026-04-20; Phase 0 — создание пакета
`companies.services/` — уже завершён коммитом `2048f4ef`).

Обе view-функции теперь вызывают `build_company_timeline()`.
"""

from __future__ import annotations

from typing import Any

from companies.models import Company, CompanyDeal, CompanyDeletionRequest, CompanyNote


def build_company_timeline(
    *,
    company: Company,
) -> list[dict[str, Any]]:
    """Собрать единую ленту из 7 источников событий карточки компании.

    Каждый элемент списка — dict со structure:
    ``{"date": datetime, "kind": str, "obj": model_instance}``.

    Виды событий (поле ``kind``):
    - ``note`` — CompanyNote (включая заметки-комментарии звонков/писем);
    - ``event`` — CompanyHistoryEvent (создание карточки, передвижения);
    - ``task_created`` / ``task_done`` — задачи (одна задача может дать 2 события);
    - ``deal`` — CompanyDeal;
    - ``call`` — CallRequest (запросы на звонок из CRM);
    - ``mailing`` — CampaignRecipient (успешно отправленные рассылки);
    - ``delreq_created`` / ``delreq_decided`` — заявки на удаление карточки.

    Сортировка: от новых к старым. Лимит — без отдельного, применяется
    жёсткая обрезка на уровне SQL-подзапросов (2000 для заметок, 500 для
    остальных, 100 для deletion requests) — предотвращает OOM на
    "исторических" компаниях. Пагинация — на уровне вызывающего кода.

    Args:
        company: объект ``Company``, для которого строим ленту.

    Returns:
        Список dict-элементов, отсортированный от новых к старым.
    """
    # Ленивый импорт CallRequest / CampaignRecipient — избегаем циклов при
    # старте приложения (phonebridge и mailer импортируют companies.*)
    from mailer.models import CampaignRecipient
    from phonebridge.models import CallRequest
    from tasksapp.models import Task

    timeline_notes = list(
        CompanyNote.objects.filter(company=company)
        .select_related("author")
        .order_by("-created_at")[:2000]
    )
    timeline_events = list(
        company.history_events.select_related("actor", "from_user", "to_user").order_by(
            "-occurred_at"
        )[:500]
    )
    timeline_tasks = list(
        Task.objects.filter(company=company)
        .select_related("created_by", "assigned_to", "type")
        .order_by("-created_at")[:500]
    )
    timeline_deals = list(
        CompanyDeal.objects.filter(company=company)
        .select_related("created_by")
        .order_by("-created_at")[:500]
    )
    timeline_calls = list(
        CallRequest.objects.filter(company=company)
        .select_related("created_by")
        .order_by("-created_at")[:500]
    )
    timeline_mailings = list(
        CampaignRecipient.objects.filter(company=company, status="sent")
        .select_related("campaign")
        .order_by("-updated_at")[:500]
    )
    timeline_delreqs = list(
        CompanyDeletionRequest.objects.filter(company=company)
        .select_related("requested_by", "decided_by")
        .order_by("-created_at")[:100]
    )

    all_items = sorted(
        [{"date": n.created_at, "kind": "note", "obj": n} for n in timeline_notes]
        + [{"date": e.occurred_at, "kind": "event", "obj": e} for e in timeline_events]
        + [{"date": t.created_at, "kind": "task_created", "obj": t} for t in timeline_tasks]
        + [
            {"date": t.completed_at, "kind": "task_done", "obj": t}
            for t in timeline_tasks
            if t.completed_at
        ]
        + [{"date": d.created_at, "kind": "deal", "obj": d} for d in timeline_deals]
        + [{"date": c.created_at, "kind": "call", "obj": c} for c in timeline_calls]
        + [{"date": m.updated_at, "kind": "mailing", "obj": m} for m in timeline_mailings]
        + [{"date": r.created_at, "kind": "delreq_created", "obj": r} for r in timeline_delreqs]
        + [
            {"date": r.decided_at, "kind": "delreq_decided", "obj": r}
            for r in timeline_delreqs
            if r.decided_at
        ],
        key=lambda x: x["date"],
        reverse=True,
    )
    return all_items
