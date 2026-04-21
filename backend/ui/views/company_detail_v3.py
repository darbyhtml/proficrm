"""F4 R3 (2026-04-18): preview 3 вариантов редизайна карточки компании.

Живёт параллельно с классическим /companies/<id>/. Existing не трогаем —
пользователь выбирает вариант, потом финализируем.

URL: /companies/<uuid>/v3/<a|b|c>/
"""

from __future__ import annotations

import json
import logging
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.db import transaction as db_tx
from django.http import Http404, HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from accounts.models import Branch
from audit.models import ActivityEvent
from audit.service import log_event
from companies.models import (
    Company,
    CompanyDeal,
    CompanyDeletionRequest,
    CompanyNote,
    CompanySphere,
    CompanyStatus,
    Contact,
    ContactEmail,
    ContactPhone,
    ContractType,
    Region,
)
from tasksapp.models import Task
from ui.views._base import _safe_next_v3, policy_required, require_can_view_company

User = get_user_model()

logger = logging.getLogger(__name__)

_VALID_VARIANTS = {"a", "b", "c"}


@login_required
@policy_required(resource_type="page", resource="ui:companies:detail")
@require_can_view_company
def company_detail_v3_preview(request: HttpRequest, company_id, variant: str) -> HttpResponse:
    """Preview-страница редизайна. variant ∈ {a,b,c} → разный layout,
    одинаковые данные.
    """
    if variant not in _VALID_VARIANTS:
        raise Http404("Unknown variant")

    company = get_object_or_404(
        Company.objects.select_related(
            "responsible", "branch", "status", "contract_type", "head_company", "region"
        ).prefetch_related("phones", "emails", "spheres"),
        id=company_id,
    )

    # P0: Pending delete request — предупреждение в шапке (classic-паттерн)
    delete_req = (
        CompanyDeletionRequest.objects.filter(company_id=company.id, status="pending")
        .select_related("requested_by")
        .first()
    )

    # F4 Этап 4: группа компаний — head_company + client_branches (топ-5)
    client_branches = list(
        Company.objects.filter(head_company=company)
        .only("id", "name", "inn", "status_id", "branch_id")
        .select_related("status", "branch")
        .order_by("name")[:5]
    )
    client_branches_total = Company.objects.filter(head_company=company).count()

    # F4 Этап 5: ActivityEvent для служебного аккордеона — топ-10 событий.
    try:
        from audit.models import ActivityEvent

        activity_events = list(
            ActivityEvent.objects.filter(company_id=company.id)
            .select_related("actor")
            .order_by("-created_at")[:10]
        )
    except Exception:
        activity_events = []

    # Контактные лица (ЛПР) — с prefetch phones/emails
    contacts = list(
        Contact.objects.filter(company=company)
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

    # Заметки (последние, с вложениями)
    recent_notes = list(
        CompanyNote.objects.filter(company=company)
        .select_related("author")
        .prefetch_related("note_attachments")
        .order_by("-is_pinned", "-created_at")[:8]
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
        timeline_raw.append(
            {
                "kind": "note",
                "icon": "📝",
                "title": (n.text or "").strip().split("\n")[0][:120] or "Заметка",
                "meta": n.author.get_full_name() if n.author_id else "",
                "at": n.created_at,
                "obj": n,
            }
        )
    for t in open_tasks[:3]:
        timeline_raw.append(
            {
                "kind": "task",
                "icon": "✓",
                "title": t.title or (t.type.name if t.type_id else "Задача"),
                "meta": t.assigned_to.get_full_name() if t.assigned_to_id else "—",
                "at": t.created_at,
                "obj": t,
            }
        )
    for d in deals[:3]:
        program = (d.program or "").strip() or "Сделка"
        timeline_raw.append(
            {
                "kind": "deal",
                "icon": "💰",
                "title": program[:120],
                "meta": d.created_by.get_full_name() if d.created_by_id else "",
                "at": d.created_at,
                "obj": d,
            }
        )
    timeline_raw.sort(key=lambda x: x["at"], reverse=True)
    timeline = timeline_raw[:5]

    # Договор: единая точка правды — берём бейдж/уровень из companies.services,
    # чтобы логика совпадала с дашбордом и classic.
    from companies.services import _get_annual_contract_alert, get_contract_alert

    contract_days_left = None
    contract_level = None  # danger / warn / ok / expired / none
    contract_is_annual = bool(company.contract_type and company.contract_type.is_annual)
    if contract_is_annual:
        # Годовые: алерт по сумме
        annual_level = _get_annual_contract_alert(company.contract_amount, company.contract_type)
        if annual_level in ("danger", "warn"):
            contract_level = annual_level
        else:
            contract_level = "ok" if company.contract_amount is not None else None
    else:
        # Не-годовые: алерт по дате
        if company.contract_until:
            today = timezone.localdate()
            delta = (company.contract_until - today).days
            contract_days_left = delta
            level, _ = get_contract_alert(company)
            if delta < 0:
                contract_level = "expired"
            elif level:
                contract_level = level
            else:
                contract_level = "ok"

    # Справочники для combobox'ов (JSON-сериализуем прямо тут, чтобы шаблон
    # мог встроить их в data-edit-options)
    status_options = [
        {"id": str(s.id), "label": s.name} for s in CompanyStatus.objects.order_by("name")
    ]
    sphere_options = [
        {"id": str(s.id), "label": s.name} for s in CompanySphere.objects.order_by("name")
    ]
    contract_type_options = [
        {"id": str(ct.id), "label": ct.name} for ct in ContractType.objects.order_by("name")
    ]
    # P0: регионы — 77.6% компаний на проде имеют регион; 334 правки/период
    region_options = [{"id": str(r.id), "label": r.name} for r in Region.objects.order_by("name")]
    # P1: рабочие часовые пояса (186 правок/период на проде)
    from core.timezone_utils import RUS_TZ_CHOICES

    timezone_options = [{"id": v, "label": lbl} for v, lbl in RUS_TZ_CHOICES]
    branch_options = [{"id": str(b.id), "label": b.name} for b in Branch.objects.order_by("name")]
    # Менеджеры для «Ответственный» — активные, с группировкой по подразделению
    resp_qs = (
        User.objects.filter(is_active=True)
        .select_related("branch")
        .exclude(role=User.Role.ADMIN)
        .order_by("branch__name", "last_name", "first_name")
    )
    responsible_options = [
        {
            "id": str(u.id),
            "label": u.get_full_name() or u.username,
            "group": u.branch.name if u.branch_id else "Без подразделения",
        }
        for u in resp_qs
    ]

    # Флаг админа (нужен для условного показа «Снять холодный» в popup-меню)
    is_admin_user = bool(
        request.user.is_superuser or getattr(request.user, "role", None) == User.Role.ADMIN
    )

    ctx = {
        "company": company,
        "variant": variant,
        "is_admin_user": is_admin_user,
        "contacts": contacts,
        "open_tasks": open_tasks,
        "recent_tasks_done": recent_tasks_done,
        "recent_notes": recent_notes,
        "deals": deals,
        "timeline": timeline,
        "contract_days_left": contract_days_left,
        "contract_level": contract_level,
        "contract_is_annual": contract_is_annual,
        "classic_url": f"/companies/{company.id}/",
        "client_branches": client_branches,
        "client_branches_total": client_branches_total,
        "activity_events": activity_events,
        # JSON-опции для data-edit-options (dumps с ensure_ascii=False для кириллицы)
        "status_options_json": json.dumps(status_options, ensure_ascii=False),
        "sphere_options_json": json.dumps(sphere_options, ensure_ascii=False),
        "contract_type_options_json": json.dumps(contract_type_options, ensure_ascii=False),
        "region_options_json": json.dumps(region_options, ensure_ascii=False),
        "timezone_options_json": json.dumps(timezone_options, ensure_ascii=False),
        "delete_req": delete_req,
        "branch_options_json": json.dumps(branch_options, ensure_ascii=False),
        "responsible_options_json": json.dumps(responsible_options, ensure_ascii=False),
    }

    template_map = {
        "a": "ui/company_detail_v3/a.html",
        "b": "ui/company_detail_v3/b.html",
        "c": "ui/company_detail_v3/c.html",
    }
    return render(request, template_map[variant], ctx)


@login_required
@require_POST
@policy_required(resource_type="action", resource="ui:companies:update")
@require_can_view_company
def contact_quick_create(request: HttpRequest, company_id) -> HttpResponse:
    """POST /companies/<id>/contacts/quick-create/
    Принимает простые поля: name (или first_name+last_name), position,
    phone, email. Создаёт Contact + ContactPhone + ContactEmail в одной
    транзакции. Облегчённая альтернатива полному contact_create с
    FormSet'ами — для inline-форм в v3-карточке.

    Возвращает: 302 redirect на карточку v3/b/ (чтобы после submit форма
    обновилась с новым контактом).
    """
    from ui.views._base import (
        _can_edit_company,
    )  # reuse permission check (W1.2: из helpers/companies через _base)

    user = request.user
    company = get_object_or_404(
        Company.objects.select_related("responsible", "branch"), id=company_id
    )
    if not _can_edit_company(user, company):
        return JsonResponse({"ok": False, "error": "Нет прав"}, status=403)

    name_raw = (request.POST.get("name") or "").strip()
    first_name = (request.POST.get("first_name") or "").strip()
    last_name = (request.POST.get("last_name") or "").strip()

    # Если передан один name — пробуем разобрать: "Иванов Иван Иванович"
    if name_raw and not (first_name or last_name):
        parts = name_raw.split(None, 1)
        last_name = parts[0] if parts else ""
        first_name = parts[1] if len(parts) > 1 else ""

    if not (first_name or last_name):
        return JsonResponse({"ok": False, "error": "Укажите ФИО"}, status=400)

    position = (request.POST.get("position") or "").strip()
    phone = (request.POST.get("phone") or "").strip()
    email = (request.POST.get("email") or "").strip()

    # Валидация email — Django EmailValidator
    if email:
        from django.core.exceptions import ValidationError
        from django.core.validators import validate_email

        try:
            validate_email(email)
        except ValidationError:
            return JsonResponse({"ok": False, "error": f"Невалидный email: {email}"}, status=400)

    # Валидация phone — только цифры, +, пробелы, скобки, дефис
    # Блокируем кириллицу и не-ASCII символы (защита от copy-paste из Word)
    if phone:
        import re

        if re.search(r"[^\d+\s\-()]", phone):
            return JsonResponse(
                {"ok": False, "error": "Телефон содержит недопустимые символы"},
                status=400,
            )
        # Нормализуем: убираем пробелы/скобки/дефисы, если начинается с 8 → +7
        digits = re.sub(r"[^\d]", "", phone)
        if digits.startswith("8") and len(digits) == 11:
            phone = "+7" + digits[1:]
        elif digits.startswith("7") and len(digits) == 11:
            phone = "+7" + digits[1:]
        elif len(digits) == 10:
            phone = "+7" + digits
        elif phone.startswith("+"):
            phone = "+" + digits
        else:
            phone = "+" + digits if digits else phone

    # Null-byte / control-chars защита (PostgreSQL отклоняет NUL)
    for fld_name, fld_val in (
        ("ФИО", f"{first_name} {last_name}"),
        ("Должность", position),
        ("Email", email),
        ("Телефон", phone),
    ):
        if "\x00" in (fld_val or ""):
            return JsonResponse(
                {"ok": False, "error": f"Поле «{fld_name}» содержит недопустимые символы."},
                status=400,
            )
    try:
        with db_tx.atomic():
            contact = Contact.objects.create(
                company=company,
                first_name=first_name[:120],
                last_name=last_name[:120],
                position=position[:255],
            )
            if phone:
                ContactPhone.objects.create(contact=contact, value=phone[:50])
            if email:
                ContactEmail.objects.create(contact=contact, value=email[:254])
    except Exception:
        logger.exception("contact_quick_create failed")
        return JsonResponse(
            {"ok": False, "error": "Не удалось сохранить контакт. Попробуйте позже."},
            status=400,
        )

    try:
        log_event(
            actor=user,
            verb=ActivityEvent.Verb.CREATE,
            entity_type="contact",
            entity_id=contact.id,
            company_id=company.id,
            message=f"Добавлен контакт: {contact}",
        )
    except Exception:
        pass

    # Redirect на next (если безопасный) или на v3/b/ по умолчанию
    from django.shortcuts import redirect

    nxt = _safe_next_v3(request, company.id)
    return redirect(nxt or f"/companies/{company.id}/v3/b/")
