"""F4 R3 (2026-04-18): preview 3 вариантов редизайна карточки компании.

Живёт параллельно с классическим /companies/<id>/. Existing не трогаем —
пользователь выбирает вариант, потом финализируем.

URL: /companies/<uuid>/v3/<a|b|c>/
"""
from __future__ import annotations

import logging
from datetime import timedelta

from django.contrib.auth.decorators import login_required
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, render
from django.utils import timezone

from companies.models import Company, CompanyDeal, CompanyNote, Contact
from tasksapp.models import Task

logger = logging.getLogger(__name__)

_VALID_VARIANTS = {"a", "b", "c"}


@login_required
def company_detail_v3_preview(
    request: HttpRequest, company_id, variant: str
) -> HttpResponse:
    """Preview-страница редизайна. variant ∈ {a,b,c} → разный layout,
    одинаковые данные.
    """
    if variant not in _VALID_VARIANTS:
        raise Http404("Unknown variant")

    company = get_object_or_404(
        Company.objects
        .select_related("responsible", "branch", "status", "contract_type")
        .prefetch_related("phones", "emails", "spheres"),
        id=company_id,
    )

    # Контактные лица (ЛПР) — с prefetch phones/emails
    contacts = list(
        Contact.objects.filter(company=company, is_archived=False)
        .prefetch_related("phones", "emails")
        .order_by("-created_at")[:6]
    )

    # Топ-5 открытых задач (NEW + IN_PROGRESS), по due_at возрастанию
    open_tasks = list(
        Task.objects.filter(
            company=company,
            status__in=[Task.Status.NEW, Task.Status.IN_PROGRESS],
        )
        .select_related("assigned_to", "type")
        .order_by("due_at", "-created_at")[:5]
    )

    # Последние задачи (включая выполненные/отменённые), для «истории задач»
    recent_tasks_done = list(
        Task.objects.filter(
            company=company,
            status__in=[Task.Status.DONE, Task.Status.CANCELLED],
        )
        .select_related("assigned_to", "type")
        .order_by("-updated_at")[:5]
    )

    # Заметки (последние)
    recent_notes = list(
        CompanyNote.objects.filter(company=company)
        .select_related("author")
        .order_by("-created_at")[:8]
    )

    # Сделки
    deals = list(
        CompanyDeal.objects.filter(company=company)
        .select_related("created_by")
        .order_by("-created_at")[:5]
    )

    # Унифицированный timeline top-5 (самое свежее из всех источников)
    timeline_raw = []
    for n in recent_notes[:5]:
        timeline_raw.append({
            "kind": "note",
            "icon": "📝",
            "title": (n.text or "").strip().split("\n")[0][:120] or "Заметка",
            "meta": n.author.get_full_name() if n.author_id else "",
            "at": n.created_at,
            "obj": n,
        })
    for t in open_tasks[:3]:
        timeline_raw.append({
            "kind": "task",
            "icon": "✓",
            "title": t.title or (t.type.name if t.type_id else "Задача"),
            "meta": t.assigned_to.get_full_name() if t.assigned_to_id else "—",
            "at": t.created_at,
            "obj": t,
        })
    for d in deals[:3]:
        program = (d.program or "").strip() or "Сделка"
        timeline_raw.append({
            "kind": "deal",
            "icon": "💰",
            "title": program[:120],
            "meta": d.created_by.get_full_name() if d.created_by_id else "",
            "at": d.created_at,
            "obj": d,
        })
    timeline_raw.sort(key=lambda x: x["at"], reverse=True)
    timeline = timeline_raw[:5]

    # Договор: дней до истечения
    contract_days_left = None
    contract_level = None  # danger / warn / ok / expired / none
    if company.contract_until:
        today = timezone.localdate()
        delta = (company.contract_until - today).days
        contract_days_left = delta
        if delta < 0:
            contract_level = "expired"
        elif delta <= 7:
            contract_level = "danger"
        elif delta <= 30:
            contract_level = "warn"
        else:
            contract_level = "ok"

    ctx = {
        "company": company,
        "variant": variant,
        "contacts": contacts,
        "open_tasks": open_tasks,
        "recent_tasks_done": recent_tasks_done,
        "recent_notes": recent_notes,
        "deals": deals,
        "timeline": timeline,
        "contract_days_left": contract_days_left,
        "contract_level": contract_level,
        "classic_url": f"/companies/{company.id}/",
    }

    template_map = {
        "a": "ui/company_detail_v3/a.html",
        "b": "ui/company_detail_v3/b.html",
        "c": "ui/company_detail_v3/c.html",
    }
    return render(request, template_map[variant], ctx)
