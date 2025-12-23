from __future__ import annotations

from datetime import datetime
from datetime import timedelta

from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.core.paginator import Paginator
from django.db.models import Exists, OuterRef, Q
from django.db.models import Count, Max
from django.http import HttpRequest, HttpResponse
from django.http import StreamingHttpResponse
from django.http import JsonResponse
from django.http import FileResponse, Http404
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from accounts.models import Branch, User
from audit.models import ActivityEvent
from audit.service import log_event
from companies.models import Company, CompanyNote, CompanySphere, CompanyStatus, Contact, CompanyDeletionRequest
from companies.permissions import can_edit_company as can_edit_company_perm, editable_company_qs as editable_company_qs_perm
from tasksapp.models import Task, TaskType
from notifications.models import Notification
from notifications.service import notify
from phonebridge.models import CallRequest
import mimetypes
from datetime import date as _date

from .forms import (
    CompanyCreateForm,
    CompanyQuickEditForm,
    CompanyContractForm,
    CompanyEditForm,
    CompanyNoteForm,
    ContactEmailFormSet,
    ContactForm,
    ContactPhoneFormSet,
    TaskForm,
    BranchForm,
    CompanySphereForm,
    CompanyStatusForm,
    TaskTypeForm,
    UserCreateForm,
    UserEditForm,
    ImportCompaniesForm,
    CompanyListColumnsForm,
)
from ui.models import UiGlobalConfig


def _dup_reasons(*, c: Company, inn: str, kpp: str, name: str, address: str) -> list[str]:
    reasons: list[str] = []
    if inn and (c.inn or "").strip() == inn:
        reasons.append("ИНН")
    if kpp and (c.kpp or "").strip() == kpp:
        reasons.append("КПП")
    if name:
        n = name.lower()
        if n in (c.name or "").lower() or n in (c.legal_name or "").lower():
            reasons.append("Название")
    if address:
        a = address.lower()
        if a in (c.address or "").lower():
            reasons.append("Адрес")
    return reasons


def _can_edit_company(user: User, company: Company) -> bool:
    return can_edit_company_perm(user, company)


def _editable_company_qs(user: User):
    return editable_company_qs_perm(user)


def _company_branch_id(company: Company):
    if getattr(company, "branch_id", None):
        return company.branch_id
    resp = getattr(company, "responsible", None)
    return getattr(resp, "branch_id", None)


def _can_delete_company(user: User, company: Company) -> bool:
    if not user or not user.is_authenticated or not user.is_active:
        return False
    if user.is_superuser or user.role in (User.Role.ADMIN, User.Role.GROUP_MANAGER):
        return True
    if user.role in (User.Role.SALES_HEAD, User.Role.BRANCH_DIRECTOR) and user.branch_id:
        return bool(_company_branch_id(company) == user.branch_id)
    return False


def _notify_branch_leads(*, branch_id, title: str, body: str, url: str, exclude_user_id=None):
    if not branch_id:
        return 0
    qs = User.objects.filter(is_active=True, branch_id=branch_id, role__in=[User.Role.SALES_HEAD, User.Role.BRANCH_DIRECTOR])
    if exclude_user_id:
        qs = qs.exclude(id=exclude_user_id)
    sent = 0
    for u in qs.iterator():
        notify(user=u, kind=Notification.Kind.COMPANY, title=title, body=body, url=url)
        sent += 1
    return sent


def _detach_client_branches(*, head_company: Company) -> list[Company]:
    """
    Если удаляется "головная организация" клиента, её дочерние карточки должны стать самостоятельными:
    head_company=NULL.
    Возвращает список "бывших филиалов" (до 200 для сообщений/логов).
    """
    children_qs = Company.objects.filter(head_company_id=head_company.id).select_related("responsible", "branch").order_by("name")
    children = list(children_qs[:200])
    if children:
        now_ts = timezone.now()
        Company.objects.filter(head_company_id=head_company.id).update(head_company=None, updated_at=now_ts)
    return children


def _notify_head_deleted_with_branches(*, actor: User, head_company: Company, detached: list[Company]):
    """
    Уведомление о том, что удалили головную компанию клиента, и её филиалы стали самостоятельными.
    По ТЗ уведомляем руководителей (РОП/директор) соответствующего внутреннего филиала.
    """
    if not detached:
        return 0
    branch_id = _company_branch_id(head_company)
    body = f"{head_company.name}: удалена головная организация. Филиалов стало головными: {len(detached)}."
    # В body добавим первые несколько названий (чтобы было понятно о чём речь)
    sample = ", ".join([c.name for c in detached[:5] if c.name])
    if sample:
        body = body + f" Примеры: {sample}."
    return _notify_branch_leads(
        branch_id=branch_id,
        title="Удалена головная организация (филиалы стали самостоятельными)",
        body=body,
        url="/companies/",
        exclude_user_id=actor.id,
    )


def _companies_with_overdue_flag(*, now):
    """
    Базовый QS компаний с вычисляемым флагом просроченных задач `has_overdue`.
    Используется в списке/экспорте/массовых операциях, чтобы фильтры работали одинаково.
    """
    overdue_tasks = (
        Task.objects.filter(company_id=OuterRef("pk"), due_at__lt=now)
        .exclude(status__in=[Task.Status.DONE, Task.Status.CANCELLED])
        .values("id")
    )
    cold_contacts = (
        Contact.objects.filter(company_id=OuterRef("pk"), is_cold_call=True)
        .values("id")
    )
    return Company.objects.all().annotate(
        has_overdue=Exists(overdue_tasks),
        has_cold_call_contact=Exists(cold_contacts),
    )


def _apply_company_filters(*, qs, params: dict):
    """
    Единые фильтры компаний для:
    - списка компаний
    - экспорта
    - массового переназначения (apply_mode=filtered)
    """
    q = (params.get("q") or "").strip()
    if q:
        qs = qs.filter(
            Q(name__icontains=q)
            | Q(inn__icontains=q)
            | Q(legal_name__icontains=q)
            | Q(address__icontains=q)
            | Q(phone__icontains=q)
            | Q(email__icontains=q)
            | Q(contact_name__icontains=q)
            | Q(contact_position__icontains=q)
            | Q(branch__name__icontains=q)
        )

    responsible = (params.get("responsible") or "").strip()
    if responsible:
        qs = qs.filter(responsible_id=responsible)

    status = (params.get("status") or "").strip()
    if status:
        qs = qs.filter(status_id=status)

    branch = (params.get("branch") or "").strip()
    if branch:
        qs = qs.filter(branch_id=branch)

    sphere = (params.get("sphere") or "").strip()
    if sphere:
        qs = qs.filter(spheres__id=sphere)

    contract_type = (params.get("contract_type") or "").strip()
    if contract_type:
        qs = qs.filter(contract_type=contract_type)

    cold_call = (params.get("cold_call") or "").strip()
    if cold_call == "1":
        qs = qs.filter(Q(primary_contact_is_cold_call=True) | Q(has_cold_call_contact=True))
    elif cold_call == "0":
        qs = qs.filter(primary_contact_is_cold_call=False, has_cold_call_contact=False)

    overdue = (params.get("overdue") or "").strip()
    if overdue == "1":
        qs = qs.filter(has_overdue=True)

    filter_active = any([q, responsible, status, branch, sphere, contract_type, cold_call, overdue == "1"])
    return {
        "qs": qs.distinct(),
        "q": q,
        "responsible": responsible,
        "status": status,
        "branch": branch,
        "sphere": sphere,
        "contract_type": contract_type,
        "cold_call": cold_call,
        "overdue": overdue,
        "filter_active": filter_active,
    }


@login_required
def dashboard(request: HttpRequest) -> HttpResponse:
    user: User = request.user
    now = timezone.now()
    # Важно: при USE_TZ=True timezone.now() в UTC. Для фильтров "сегодня/неделя" считаем границы по локальной TZ.
    local_now = timezone.localtime(now)
    today_date = timezone.localdate(now)
    today_start = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
    tomorrow_start = today_start + timedelta(days=1)

    tasks_today = (
        Task.objects.filter(assigned_to=user)
        .filter(due_at__gte=today_start, due_at__lt=tomorrow_start)
        .exclude(status__in=[Task.Status.DONE, Task.Status.CANCELLED])
        .select_related("company")
        .order_by("due_at")
    )

    overdue = (
        Task.objects.filter(assigned_to=user, due_at__lt=now)
        .exclude(status__in=[Task.Status.DONE, Task.Status.CANCELLED])
        .select_related("company")
        .order_by("due_at")[:20]
    )

    # На неделю вперёд, но без "на сегодня"
    week_start = tomorrow_start
    week_end = today_start + timedelta(days=8)  # exclusive: [завтра; завтра+7дней)
    tasks_week = (
        Task.objects.filter(assigned_to=user)
        .filter(due_at__gte=week_start, due_at__lt=week_end)
        .exclude(status__in=[Task.Status.DONE, Task.Status.CANCELLED])
        .select_related("company")
        .order_by("due_at")[:50]
    )

    # Новые задачи (назначено сотруднику): показываем последние, чтобы при входе было сразу видно.
    tasks_new = (
        Task.objects.filter(assigned_to=user, status=Task.Status.NEW)
        .exclude(status__in=[Task.Status.DONE, Task.Status.CANCELLED])
        .select_related("company", "created_by")
        .order_by("-created_at")[:20]
    )

    # Договоры, которые подходят по сроку (<= 30 дней) — только для ответственного
    contract_until_30 = today_date + timedelta(days=30)
    contracts_soon_qs = (
        Company.objects.filter(responsible=user, contract_until__isnull=False)
        .filter(contract_until__gte=today_date, contract_until__lte=contract_until_30)
        .only("id", "name", "contract_type", "contract_until")
        .order_by("contract_until", "name")[:50]
    )
    contracts_soon = []
    for c in contracts_soon_qs:
        days_left = (c.contract_until - today_date).days if c.contract_until else None
        level = "danger" if (days_left is not None and days_left < 14) else "warn"
        contracts_soon.append({"company": c, "days_left": days_left, "level": level})

    return render(
        request,
        "ui/dashboard.html",
        {
            "now": now,
            "tasks_new": tasks_new,
            "tasks_today": tasks_today,
            "overdue": overdue,
            "tasks_week": tasks_week,
            "contracts_soon": contracts_soon,
        },
    )


def _can_view_cold_call_reports(user: User) -> bool:
    if not user or not user.is_authenticated or not user.is_active:
        return False
    return bool(user.is_superuser or user.role in (User.Role.ADMIN, User.Role.GROUP_MANAGER, User.Role.BRANCH_DIRECTOR, User.Role.SALES_HEAD, User.Role.MANAGER))


def _month_start(d: _date) -> _date:
    return d.replace(day=1)


def _add_months(d: _date, delta_months: int) -> _date:
    # Возвращает первое число месяца, сдвинутого на delta_months.
    y = d.year
    m = d.month + int(delta_months)
    while m <= 0:
        y -= 1
        m += 12
    while m > 12:
        y += 1
        m -= 12
    return _date(y, m, 1)


def _month_label(d: _date) -> str:
    months = {
        1: "Январь", 2: "Февраль", 3: "Март", 4: "Апрель", 5: "Май", 6: "Июнь",
        7: "Июль", 8: "Август", 9: "Сентябрь", 10: "Октябрь", 11: "Ноябрь", 12: "Декабрь",
    }
    return f"{months.get(d.month, str(d.month))} {d.year}"


@login_required
def cold_calls_report_day(request: HttpRequest) -> JsonResponse:
    user: User = request.user
    if not _can_view_cold_call_reports(user):
        return JsonResponse({"ok": False, "detail": "forbidden"}, status=403)

    now = timezone.now()
    local_now = timezone.localtime(now)
    day_start = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
    day_end = day_start + timedelta(days=1)
    day_label = timezone.localdate(now).strftime("%d.%m.%Y")

    # Считаем "холодными" звонки:
    # - помеченные в момент клика (call.is_cold_call)
    # - или если контакт/основной контакт компании отмечены как холодные сейчас
    qs = (
        CallRequest.objects.filter(created_by=user, created_at__gte=day_start, created_at__lt=day_end)
        .filter(Q(is_cold_call=True) | Q(contact__is_cold_call=True) | Q(company__primary_contact_is_cold_call=True))
        .select_related("company", "contact")
        .order_by("created_at")
    )
    items = []
    lines = [f"Отчёт: холодные звонки за {day_label}", f"Всего: {qs.count()}", ""]
    i = 0
    # Дедупликация: если пользователь несколько раз подряд кликает "позвонить" на один и тот же номер/контакт,
    # скрываем повторы в отчёте.
    dedupe_window_s = 60
    last_seen = {}  # (phone, company_id, contact_id) -> created_at
    for call in qs:
        key = (call.phone_raw or "", str(call.company_id or ""), str(call.contact_id or ""))
        prev = last_seen.get(key)
        if prev and (call.created_at - prev).total_seconds() < dedupe_window_s:
            continue
        last_seen[key] = call.created_at

        i += 1
        t = timezone.localtime(call.created_at).strftime("%H:%M")
        company_name = getattr(call.company, "name", "") if call.company_id else ""
        if call.contact_id and call.contact:
            contact_name = str(call.contact) or ""
        else:
            contact_name = (getattr(call.company, "contact_name", "") or "").strip() if call.company_id else ""
        who = contact_name or "Контакт не указан"
        who2 = f"{who} ({company_name})" if company_name else who
        phone = call.phone_raw or ""
        items.append({"time": t, "phone": phone, "contact": who, "company": company_name})
        lines.append(f"{i}) {t} — {who2} — {phone}")

    return JsonResponse({"ok": True, "range": "day", "date": day_label, "count": len(items), "items": items, "text": "\n".join(lines)})


@login_required
def cold_calls_report_month(request: HttpRequest) -> JsonResponse:
    user: User = request.user
    if not _can_view_cold_call_reports(user):
        return JsonResponse({"ok": False, "detail": "forbidden"}, status=403)

    today = timezone.localdate(timezone.now())
    base = _month_start(today)
    candidates = [_month_start(_add_months(base, -2)), _month_start(_add_months(base, -1)), base]

    available = []
    for ms in candidates:
        me = _add_months(ms, 1)
        exists = (
            CallRequest.objects.filter(created_by=user, created_at__date__gte=ms, created_at__date__lt=me)
            .filter(Q(is_cold_call=True) | Q(contact__is_cold_call=True) | Q(company__primary_contact_is_cold_call=True))
            .exists()
        )
        if exists:
            available.append(ms)

    # Если вообще нет данных — показываем текущий месяц (пустой отчёт), чтобы кнопка не была "мертвой"
    if not available:
        available = [base]

    req_key = (request.GET.get("month") or "").strip()
    selected = available[-1]
    for ms in available:
        if req_key and req_key == ms.strftime("%Y-%m"):
            selected = ms
            break

    month_end = _add_months(selected, 1)
    qs = (
        CallRequest.objects.filter(created_by=user, created_at__date__gte=selected, created_at__date__lt=month_end)
        .filter(Q(is_cold_call=True) | Q(contact__is_cold_call=True) | Q(company__primary_contact_is_cold_call=True))
        .select_related("company", "contact")
        .order_by("created_at")
    )

    items = []
    lines = [f"Отчёт: холодные звонки за {_month_label(selected)}", f"Всего: {qs.count()}", ""]
    i = 0
    dedupe_window_s = 60
    last_seen = {}  # (phone, company_id, contact_id) -> created_at
    for call in qs:
        key = (call.phone_raw or "", str(call.company_id or ""), str(call.contact_id or ""))
        prev = last_seen.get(key)
        if prev and (call.created_at - prev).total_seconds() < dedupe_window_s:
            continue
        last_seen[key] = call.created_at

        i += 1
        dt = timezone.localtime(call.created_at)
        t = dt.strftime("%d.%m %H:%M")
        company_name = getattr(call.company, "name", "") if call.company_id else ""
        if call.contact_id and call.contact:
            contact_name = str(call.contact) or ""
        else:
            contact_name = (getattr(call.company, "contact_name", "") or "").strip() if call.company_id else ""
        who = contact_name or "Контакт не указан"
        who2 = f"{who} ({company_name})" if company_name else who
        phone = call.phone_raw or ""
        items.append({"time": t, "phone": phone, "contact": who, "company": company_name})
        lines.append(f"{i}) {t} — {who2} — {phone}")

    month_options = [{"key": ms.strftime("%Y-%m"), "label": _month_label(ms)} for ms in available]
    return JsonResponse(
        {
            "ok": True,
            "range": "month",
            "month": selected.strftime("%Y-%m"),
            "month_label": _month_label(selected),
            "available_months": month_options,
            "count": len(items),
            "items": items,
            "text": "\n".join(lines),
        }
    )


@login_required
def company_list(request: HttpRequest) -> HttpResponse:
    user: User = request.user
    now = timezone.now()
    # Просмотр компаний: всем доступна вся база (без ограничения по филиалу/scope).
    base_qs = Company.objects.all()
    companies_total = base_qs.order_by().count()
    qs = (
        _companies_with_overdue_flag(now=now)
        .select_related("responsible", "branch", "status")
        .prefetch_related("spheres")
    )
    f = _apply_company_filters(qs=qs, params=request.GET)
    qs = f["qs"]

    # Sorting (asc/desc)
    sort = (request.GET.get("sort") or "").strip() or "updated_at"
    direction = (request.GET.get("dir") or "").strip().lower() or "desc"
    direction = "asc" if direction == "asc" else "desc"
    sort_map = {
        "updated_at": "updated_at",
        "name": "name",
        "inn": "inn",
        "status": "status__name",
        "responsible": "responsible__last_name",
        "branch": "branch__name",
    }
    sort_field = sort_map.get(sort, "updated_at")
    if sort == "responsible":
        order = [sort_field, "responsible__first_name", "name"]
    else:
        order = [sort_field, "name"]
    if direction == "desc":
        order = [f"-{f}" for f in order]
    qs = qs.order_by(*order)

    companies_filtered = qs.order_by().count()
    filter_active = f["filter_active"]

    paginator = Paginator(qs, 25)
    page = paginator.get_page(request.GET.get("page"))
    ui_cfg = UiGlobalConfig.load()
    columns = ui_cfg.company_list_columns or ["name"]

    return render(
        request,
        "ui/company_list.html",
        {
            "page": page,
            "q": f["q"],
            "responsible": f["responsible"],
            "status": f["status"],
            "branch": f["branch"],
            "sphere": f["sphere"],
            "contract_type": f["contract_type"],
            "cold_call": f["cold_call"],
            "overdue": f["overdue"],
            "companies_total": companies_total,
            "companies_filtered": companies_filtered,
            "filter_active": filter_active,
            "sort": sort,
            "dir": direction,
            "responsibles": User.objects.order_by("last_name", "first_name"),
            "statuses": CompanyStatus.objects.order_by("name"),
            "spheres": CompanySphere.objects.order_by("name"),
            "branches": Branch.objects.order_by("name"),
            "contract_types": Company.ContractType.choices,
            "company_list_columns": columns,
            "transfer_targets": User.objects.filter(is_active=True, role__in=[User.Role.MANAGER, User.Role.BRANCH_DIRECTOR, User.Role.SALES_HEAD]).order_by("last_name", "first_name"),
        },
    )


@login_required
def company_bulk_transfer(request: HttpRequest) -> HttpResponse:
    """
    Массовое переназначение ответственного:
    - либо по выбранным company_ids[]
    - либо по текущему фильтру (apply_mode=filtered), чтобы быстро переназначить, например, все компании уволенного сотрудника.
    """
    if request.method != "POST":
        return redirect("company_list")

    user: User = request.user
    new_resp_id = (request.POST.get("responsible_id") or "").strip()
    apply_mode = (request.POST.get("apply_mode") or "selected").strip().lower()
    if not new_resp_id:
        messages.error(request, "Выберите нового ответственного.")
        return redirect("company_list")

    new_resp = get_object_or_404(User, id=new_resp_id, is_active=True)
    if new_resp.role not in (User.Role.MANAGER, User.Role.BRANCH_DIRECTOR, User.Role.SALES_HEAD):
        messages.error(request, "Нового ответственного можно выбрать только из: менеджер / директор филиала / РОП.")
        return redirect("company_list")

    # Базовый QS: все компании (просмотр всем) → сужаем до редактируемых пользователем
    editable_qs = _editable_company_qs(user)

    # режим "по фильтру" — переносим фильтры из скрытых полей формы
    if apply_mode == "filtered":
        now = timezone.now()
        qs = _companies_with_overdue_flag(now=now)
        f = _apply_company_filters(qs=qs, params=request.POST)
        qs = f["qs"]

        # ограничиваем до редактируемых пользователем
        qs = qs.filter(id__in=editable_qs.values_list("id", flat=True)).distinct()
        # safety cap
        cap = 5000
        ids = list(qs.values_list("id", flat=True)[:cap])
        if not ids:
            messages.error(request, "Нет компаний для переназначения (или нет прав).")
            return redirect("company_list")
        if len(ids) >= cap:
            messages.warning(request, f"Выбрано слишком много компаний (>{cap}). Сузьте фильтр и повторите.")
            return redirect("company_list")
    else:
        ids = request.POST.getlist("company_ids") or []
        ids = [i for i in ids if i]
        if not ids:
            messages.error(request, "Выберите хотя бы одну компанию (чекбоксы слева).")
            return redirect("company_list")

        # ограничиваем до редактируемых
        ids = list(editable_qs.filter(id__in=ids).values_list("id", flat=True))
        if not ids:
            messages.error(request, "Нет выбранных компаний, доступных для переназначения.")
            return redirect("company_list")

    # Ограничения директора филиала: переназначать можно только внутри филиала
    if user.role == User.Role.BRANCH_DIRECTOR and user.branch_id:
        if new_resp.branch_id != user.branch_id:
            messages.error(request, "Директор филиала может переназначать компании только внутри своего филиала.")
            return redirect("company_list")

    now_ts = timezone.now()
    with transaction.atomic():
        qs_to_update = Company.objects.filter(id__in=ids).select_related("responsible")
        # фиксируем "старых" ответственных для лога (первых 20)
        old_resps = list(qs_to_update.values_list("responsible_id", flat=True).distinct()[:20])
        updated = qs_to_update.update(responsible=new_resp, branch=new_resp.branch, updated_at=now_ts)

    messages.success(request, f"Переназначено компаний: {updated}. Новый ответственный: {new_resp}.")
    log_event(
        actor=user,
        verb=ActivityEvent.Verb.UPDATE,
        entity_type="company_bulk_transfer",
        entity_id=str(new_resp.id),
        message="Массовое переназначение компаний",
        meta={"count": updated, "to": str(new_resp), "old_responsible_ids_sample": old_resps, "mode": apply_mode},
    )
    if new_resp.id != user.id:
        notify(
            user=new_resp,
            kind=Notification.Kind.COMPANY,
            title="Вам передали компании",
            body=f"Количество: {updated}",
            url=f"/companies/?responsible={new_resp.id}",
        )
    return redirect("company_list")


@login_required
def company_export(request: HttpRequest) -> HttpResponse:
    """
    Экспорт компаний (по текущим фильтрам) в CSV.
    Доступ: только администратор.
    Экспорт: максимально полный (данные + контакты + заметки + задачи + статусы/сферы/филиалы и т.п.).
    """
    import csv

    user: User = request.user
    if not _require_admin(user):
        log_event(
            actor=user,
            verb=ActivityEvent.Verb.UPDATE,
            entity_type="export",
            entity_id="companies_csv",
            message="Попытка экспорта компаний (запрещено)",
            meta={
                "allowed": False,
                "ip": request.META.get("REMOTE_ADDR"),
                "user_agent": request.META.get("HTTP_USER_AGENT", "")[:200],
                "filters": {
                    "q": (request.GET.get("q") or "").strip(),
                    "responsible": (request.GET.get("responsible") or "").strip(),
                    "status": (request.GET.get("status") or "").strip(),
                    "branch": (request.GET.get("branch") or "").strip(),
                    "sphere": (request.GET.get("sphere") or "").strip(),
                    "overdue": (request.GET.get("overdue") or "").strip(),
                },
            },
        )
        messages.error(request, "Экспорт доступен только администратору.")
        return redirect("company_list")

    now = timezone.now()
    qs = (
        _companies_with_overdue_flag(now=now)
        .select_related("responsible", "branch", "status")
        .prefetch_related("spheres")
        .order_by("-updated_at")
    )
    f = _apply_company_filters(qs=qs, params=request.GET)
    qs = f["qs"]

    # Полный экспорт: данные компании + агрегированные связанные сущности.
    # Важно: для больших объёмов соединяем связанные сущности в одну ячейку (Excel-friendly).
    qs = qs.select_related("head_company").prefetch_related(
        "contacts__emails",
        "contacts__phones",
        "notes__author",
        "tasks__assigned_to",
        "tasks__created_by",
        "tasks__type",
    )

    def _contract_type_display(company: Company) -> str:
        try:
            return company.get_contract_type_display() if company.contract_type else ""
        except Exception:
            return company.contract_type or ""

    headers = [
        "ID",
        "Компания",
        "Юр.название",
        "ИНН",
        "КПП",
        "Адрес",
        "Сайт",
        "Вид деятельности",
        "Холодный звонок",
        "Вид договора",
        "Договор до",
        "Статус",
        "Сферы",
        "Ответственный",
        "Филиал",
        "Головная организация",
        "Создано",
        "Обновлено",
        "Контакт (ФИО) [из данных]",
        "Контакт (должность) [из данных]",
        "Телефон (осн.) [из данных]",
        "Email (осн.) [из данных]",
        "Контакты (добавленные)",
        "Заметки",
        "Задачи",
        "Есть просроченные задачи",
    ]

    def _fmt_dt(dt):
        if not dt:
            return ""
        try:
            return timezone.localtime(dt).strftime("%d.%m.%Y %H:%M")
        except Exception:
            return str(dt)

    def _fmt_date(d):
        if not d:
            return ""
        try:
            return d.strftime("%d.%m.%Y")
        except Exception:
            return str(d)

    def _join_nonempty(parts, sep="; "):
        parts = [p for p in parts if p]
        return sep.join(parts)

    def _contacts_blob(company: Company) -> str:
        items = []
        for c in getattr(company, "contacts", []).all():
            phones = ", ".join([p.value for p in c.phones.all()])
            emails = ", ".join([e.value for e in c.emails.all()])
            name = " ".join([c.last_name or "", c.first_name or ""]).strip()
            head = _join_nonempty([name, c.position or ""], " — ")
            tail = _join_nonempty(
                [
                    f"тел: {phones}" if phones else "",
                    f"email: {emails}" if emails else "",
                    f"прим: {c.note.strip()}" if (c.note or "").strip() else "",
                ],
                "; ",
            )
            if head or tail:
                items.append(_join_nonempty([head, tail], " | "))
        return " || ".join(items)

    def _notes_blob(company: Company) -> str:
        items = []
        for n in getattr(company, "notes", []).all().order_by("created_at"):
            txt = (n.text or "").strip()
            if n.attachment:
                txt = _join_nonempty([txt, f"файл: {n.attachment_name or 'file'}"], " | ")
            line = _join_nonempty([_fmt_dt(n.created_at), str(n.author) if n.author else "", txt], " — ")
            if line:
                items.append(line)
        return " || ".join(items)

    def _tasks_blob(company: Company) -> str:
        items = []
        for t in getattr(company, "tasks", []).all().order_by("created_at"):
            title = (t.title or "").strip()
            meta = _join_nonempty(
                [
                    f"статус: {t.get_status_display()}",
                    f"тип: {t.type.name}" if t.type else "",
                    f"кому: {t.assigned_to}" if t.assigned_to else "",
                    f"дедлайн: {_fmt_dt(t.due_at)}" if t.due_at else "",
                ],
                "; ",
            )
            line = _join_nonempty([title, meta], " | ")
            if line:
                items.append(line)
        return " || ".join(items)

    def row_for(company: Company):
        return [
            str(company.id),
            company.name or "",
            company.legal_name or "",
            company.inn or "",
            company.kpp or "",
            (company.address or "").replace("\n", " ").strip(),
            company.website or "",
            company.activity_kind or "",
            "Да" if (company.primary_contact_is_cold_call or bool(getattr(company, "has_cold_call_contact", False))) else "Нет",
            _contract_type_display(company),
            _fmt_date(company.contract_until),
            company.status.name if company.status else "",
            ", ".join([s.name for s in company.spheres.all()]),
            str(company.responsible) if company.responsible else "",
            str(company.branch) if company.branch else "",
            (company.head_company.name if company.head_company else ""),
            _fmt_dt(company.created_at),
            _fmt_dt(company.updated_at),
            company.contact_name or "",
            company.contact_position or "",
            company.phone or "",
            company.email or "",
            _contacts_blob(company),
            _notes_blob(company),
            _tasks_blob(company),
            "Да" if getattr(company, "has_overdue", False) else "Нет",
        ]

    import uuid
    export_id = str(uuid.uuid4())

    def stream():
        # BOM for Excel
        yield "\ufeff"
        import io
        buf = io.StringIO()
        writer = csv.writer(buf, delimiter=";")
        # "водяной знак" (первая строка CSV): кто/когда/IP/ID экспорта
        meta_row = ["EXPORT_ID=" + export_id, f"USER={user.username}", f"IP={request.META.get('REMOTE_ADDR','')}", f"TS={timezone.now().isoformat()}"]
        # подгоняем к количеству колонок
        while len(meta_row) < len(headers):
            meta_row.append("")
        writer.writerow(meta_row[: len(headers)])
        writer.writerow(headers)
        yield buf.getvalue()
        buf.seek(0)
        buf.truncate(0)

        for company in qs.iterator(chunk_size=1000):
            writer.writerow(row_for(company))
            yield buf.getvalue()
            buf.seek(0)
            buf.truncate(0)

    # Аудит экспорта (успешный старт)
    try:
        row_count = qs.count()
    except Exception:
        row_count = None
    log_event(
        actor=user,
        verb=ActivityEvent.Verb.UPDATE,
        entity_type="export",
        entity_id="companies_csv",
        message="Экспорт компаний (CSV)",
        meta={
            "allowed": True,
            "export_id": export_id,
            "ip": request.META.get("REMOTE_ADDR"),
            "user_agent": request.META.get("HTTP_USER_AGENT", "")[:200],
            "filters": {
                "q": f["q"],
                "responsible": f["responsible"],
                "status": f["status"],
                "branch": f["branch"],
                "sphere": f["sphere"],
                "contract_type": f["contract_type"],
                "cold_call": f["cold_call"],
                "overdue": f["overdue"],
            },
            "row_count": row_count,
        },
    )

    filename = f"companies_{timezone.now().date().isoformat()}.csv"
    resp = StreamingHttpResponse(stream(), content_type="text/csv; charset=utf-8")
    resp["Content-Disposition"] = f'attachment; filename="{filename}"'
    return resp


@login_required
def company_create(request: HttpRequest) -> HttpResponse:
    user: User = request.user

    if request.method == "POST":
        form = CompanyCreateForm(request.POST)
        if form.is_valid():
            company: Company = form.save(commit=False)

            # Менеджер создаёт компанию только на себя; филиал подтягиваем от пользователя.
            company.created_by = user
            company.responsible = user
            company.branch = user.branch
            company.save()
            form.save_m2m()
            messages.success(request, "Компания создана.")
            log_event(
                actor=user,
                verb=ActivityEvent.Verb.CREATE,
                entity_type="company",
                entity_id=company.id,
                company_id=company.id,
                message=f"Создана компания: {company.name}",
            )
            return redirect("company_detail", company_id=company.id)
    else:
        form = CompanyCreateForm()

    return render(request, "ui/company_create.html", {"form": form})


@login_required
def company_duplicates(request: HttpRequest) -> HttpResponse:
    """
    JSON: подсказки дублей при создании компании.
    Проверяем по ИНН/КПП/названию/адресу и возвращаем только то, что пользователь может видеть.
    """
    user: User = request.user
    inn = (request.GET.get("inn") or "").strip()
    kpp = (request.GET.get("kpp") or "").strip()
    name = (request.GET.get("name") or "").strip()
    address = (request.GET.get("address") or "").strip()

    q = Q()
    reasons = []
    if inn:
        q |= Q(inn=inn)
        reasons.append("ИНН")
    if kpp:
        q |= Q(kpp=kpp)
        reasons.append("КПП")
    if name:
        q |= Q(name__icontains=name) | Q(legal_name__icontains=name)
        reasons.append("Название")
    if address:
        q |= Q(address__icontains=address)
        reasons.append("Адрес")

    if not q:
        return JsonResponse({"items": [], "hidden_count": 0, "reasons": []})

    qs_all = Company.objects.all()
    qs_match = qs_all.filter(q).select_related("responsible", "branch").order_by("-updated_at")
    visible = list(qs_match[:10])
    hidden_count = max(0, qs_match.count() - len(visible))

    items = []
    for c in visible:
        match = _dup_reasons(c=c, inn=inn, kpp=kpp, name=name, address=address)
        items.append(
            {
                "id": str(c.id),
                "name": c.name,
                "inn": c.inn or "",
                "kpp": c.kpp or "",
                "address": c.address or "",
                "branch": str(c.branch) if c.branch else "",
                "responsible": str(c.responsible) if c.responsible else "",
                "url": f"/companies/{c.id}/",
                "match": match,
            }
        )
    return JsonResponse({"items": items, "hidden_count": hidden_count, "reasons": reasons})


@login_required
def company_detail(request: HttpRequest, company_id) -> HttpResponse:
    user: User = request.user
    company = get_object_or_404(Company.objects.select_related("responsible", "branch", "status", "head_company"), id=company_id)
    can_edit_company = _can_edit_company(user, company)
    can_view_activity = bool(user.is_superuser or user.role in (User.Role.ADMIN, User.Role.GROUP_MANAGER, User.Role.BRANCH_DIRECTOR, User.Role.SALES_HEAD))
    can_delete_company = _can_delete_company(user, company)
    can_request_delete = bool(user.role == User.Role.MANAGER and company.responsible_id == user.id)
    delete_req = (
        CompanyDeletionRequest.objects.filter(company=company, status=CompanyDeletionRequest.Status.PENDING)
        .select_related("requested_by", "decided_by")
        .order_by("-created_at")
        .first()
    )

    # "Организация" (головная карточка) и "филиалы" (дочерние карточки клиента)
    head = company.head_company or company
    org_head = Company.objects.select_related("responsible", "branch").filter(id=head.id).first()
    org_branches = (
        Company.objects.select_related("responsible", "branch")
        .filter(head_company_id=head.id)
        .order_by("name")[:200]
    )

    contacts = Contact.objects.filter(company=company).prefetch_related("emails", "phones").order_by("last_name", "first_name")[:200]
    has_cold_call = bool(company.primary_contact_is_cold_call or Contact.objects.filter(company=company, is_cold_call=True).exists())
    pinned_note = (
        CompanyNote.objects.filter(company=company, is_pinned=True)
        .select_related("author", "pinned_by")
        .order_by("-pinned_at", "-created_at")
        .first()
    )
    notes = (
        CompanyNote.objects.filter(company=company)
        .select_related("author", "pinned_by")
        .order_by("-is_pinned", "-pinned_at", "-created_at")[:60]
    )
    tasks = (
        Task.objects.filter(company=company)
        .select_related("assigned_to", "type")
        .order_by("-created_at")[:25]
    )

    note_form = CompanyNoteForm()
    activity = []
    if can_view_activity:
        activity = ActivityEvent.objects.filter(company_id=company.id).select_related("actor")[:50]
    quick_form = CompanyQuickEditForm(instance=company)
    contract_form = CompanyContractForm(instance=company)

    transfer_targets = User.objects.filter(is_active=True, role__in=[User.Role.MANAGER, User.Role.BRANCH_DIRECTOR, User.Role.SALES_HEAD]).order_by("last_name", "first_name")

    # Подсветка договора: оранжевый <= 30 дней, красный < 14 дней (ежедневно)
    contract_alert = ""
    contract_days_left = None
    if company.contract_until:
        today_date = timezone.localdate(timezone.now())
        contract_days_left = (company.contract_until - today_date).days
        if contract_days_left < 14:
            contract_alert = "danger"
        elif contract_days_left <= 30:
            contract_alert = "warn"

    return render(
        request,
        "ui/company_detail.html",
        {
            "company": company,
            "org_head": org_head,
            "org_branches": org_branches,
            "can_edit_company": can_edit_company,
            "contacts": contacts,
            "has_cold_call": has_cold_call,
            "notes": notes,
            "pinned_note": pinned_note,
            "note_form": note_form,
            "tasks": tasks,
            "activity": activity,
            "can_view_activity": can_view_activity,
            "can_delete_company": can_delete_company,
            "can_request_delete": can_request_delete,
            "delete_req": delete_req,
            "quick_form": quick_form,
            "contract_form": contract_form,
            "transfer_targets": transfer_targets,
            "contract_alert": contract_alert,
            "contract_days_left": contract_days_left,
        },
    )


@login_required
def company_delete_request_create(request: HttpRequest, company_id) -> HttpResponse:
    if request.method != "POST":
        return redirect("company_detail", company_id=company_id)
    user: User = request.user
    company = get_object_or_404(Company.objects.select_related("responsible", "branch"), id=company_id)
    if not (user.role == User.Role.MANAGER and company.responsible_id == user.id):
        messages.error(request, "Запрос на удаление может отправить только ответственный менеджер.")
        return redirect("company_detail", company_id=company.id)
    existing = CompanyDeletionRequest.objects.filter(company=company, status=CompanyDeletionRequest.Status.PENDING).first()
    if existing:
        messages.info(request, "Запрос на удаление уже отправлен и ожидает решения.")
        return redirect("company_detail", company_id=company.id)
    note = (request.POST.get("note") or "").strip()
    req = CompanyDeletionRequest.objects.create(
        company=company,
        company_id_snapshot=company.id,
        company_name_snapshot=company.name or "",
        requested_by=user,
        requested_by_branch=user.branch,
        note=note,
        status=CompanyDeletionRequest.Status.PENDING,
    )
    branch_id = _company_branch_id(company)
    sent = _notify_branch_leads(
        branch_id=branch_id,
        title="Запрос на удаление компании",
        body=f"{company.name}: {(note[:180] + '…') if len(note) > 180 else note or 'без комментария'}",
        url=f"/companies/{company.id}/",
        exclude_user_id=user.id,
    )
    messages.success(request, f"Запрос отправлен на рассмотрение. Уведомлено руководителей: {sent}.")
    log_event(
        actor=user,
        verb=ActivityEvent.Verb.CREATE,
        entity_type="company_delete_request",
        entity_id=str(req.id),
        company_id=company.id,
        message="Запрос на удаление компании",
        meta={"note": note[:500], "notified": sent},
    )
    return redirect("company_detail", company_id=company.id)


@login_required
def company_delete_request_cancel(request: HttpRequest, company_id, req_id: int) -> HttpResponse:
    if request.method != "POST":
        return redirect("company_detail", company_id=company_id)
    user: User = request.user
    company = get_object_or_404(Company.objects.select_related("responsible", "branch"), id=company_id)
    if not _can_delete_company(user, company):
        messages.error(request, "Нет прав на обработку запросов удаления по этой компании.")
        return redirect("company_detail", company_id=company.id)
    req = get_object_or_404(CompanyDeletionRequest.objects.select_related("requested_by"), id=req_id, company_id_snapshot=company.id)
    if req.status != CompanyDeletionRequest.Status.PENDING:
        messages.info(request, "Запрос уже обработан.")
        return redirect("company_detail", company_id=company.id)
    decision_note = (request.POST.get("decision_note") or "").strip()
    if not decision_note:
        messages.error(request, "Укажите причину отмены запроса.")
        return redirect("company_detail", company_id=company.id)
    req.status = CompanyDeletionRequest.Status.CANCELLED
    req.decided_by = user
    req.decision_note = decision_note
    req.decided_at = timezone.now()
    req.save(update_fields=["status", "decided_by", "decision_note", "decided_at"])
    if req.requested_by_id:
        notify(
            user=req.requested_by,
            kind=Notification.Kind.COMPANY,
            title="Запрос на удаление отклонён",
            body=f"{company.name}: {decision_note}",
            url=f"/companies/{company.id}/",
        )
    messages.success(request, "Запрос отклонён. Менеджер уведомлён.")
    log_event(
        actor=user,
        verb=ActivityEvent.Verb.UPDATE,
        entity_type="company_delete_request",
        entity_id=str(req.id),
        company_id=company.id,
        message="Отклонён запрос на удаление компании",
        meta={"decision_note": decision_note[:500]},
    )
    return redirect("company_detail", company_id=company.id)


@login_required
def company_delete_request_approve(request: HttpRequest, company_id, req_id: int) -> HttpResponse:
    if request.method != "POST":
        return redirect("company_detail", company_id=company_id)
    user: User = request.user
    company = get_object_or_404(Company.objects.select_related("responsible", "branch"), id=company_id)
    if not _can_delete_company(user, company):
        messages.error(request, "Нет прав на удаление этой компании.")
        return redirect("company_detail", company_id=company.id)
    req = get_object_or_404(CompanyDeletionRequest.objects.select_related("requested_by"), id=req_id, company_id_snapshot=company.id)
    if req.status != CompanyDeletionRequest.Status.PENDING:
        messages.info(request, "Запрос уже обработан.")
        return redirect("company_detail", company_id=company.id)
    req.status = CompanyDeletionRequest.Status.APPROVED
    req.decided_by = user
    req.decided_at = timezone.now()
    req.save(update_fields=["status", "decided_by", "decided_at"])

    # Если удаляем "головную" компанию клиента — дочерние карточки становятся самостоятельными.
    detached = _detach_client_branches(head_company=company)
    branches_notified = _notify_head_deleted_with_branches(actor=user, head_company=company, detached=detached)

    if req.requested_by_id:
        notify(
            user=req.requested_by,
            kind=Notification.Kind.COMPANY,
            title="Запрос на удаление подтверждён",
            body=f"{company.name}: компания удалена",
            url="/companies/",
        )
    log_event(
        actor=user,
        verb=ActivityEvent.Verb.DELETE,
        entity_type="company",
        entity_id=str(company.id),
        company_id=company.id,
        message="Компания удалена (по запросу)",
        meta={
            "request_id": req.id,
            "detached_branches": [str(c.id) for c in detached[:50]],
            "detached_count": len(detached),
            "branches_notified": branches_notified,
        },
    )
    company.delete()
    messages.success(request, "Компания удалена.")
    return redirect("company_list")


@login_required
def company_delete_direct(request: HttpRequest, company_id) -> HttpResponse:
    if request.method != "POST":
        return redirect("company_detail", company_id=company_id)
    user: User = request.user
    company = get_object_or_404(Company.objects.select_related("responsible", "branch"), id=company_id)
    if not _can_delete_company(user, company):
        messages.error(request, "Нет прав на удаление этой компании.")
        return redirect("company_detail", company_id=company.id)
    reason = (request.POST.get("reason") or "").strip()
    detached = _detach_client_branches(head_company=company)
    branches_notified = _notify_head_deleted_with_branches(actor=user, head_company=company, detached=detached)
    log_event(
        actor=user,
        verb=ActivityEvent.Verb.DELETE,
        entity_type="company",
        entity_id=str(company.id),
        company_id=company.id,
        message="Компания удалена",
        meta={
            "reason": reason[:500],
            "detached_branches": [str(c.id) for c in detached[:50]],
            "detached_count": len(detached),
            "branches_notified": branches_notified,
        },
    )
    company.delete()
    messages.success(request, "Компания удалена.")
    return redirect("company_list")


@login_required
def company_contract_update(request: HttpRequest, company_id) -> HttpResponse:
    if request.method != "POST":
        return redirect("company_detail", company_id=company_id)

    user: User = request.user
    company = get_object_or_404(Company.objects.select_related("responsible", "branch"), id=company_id)
    if not _can_edit_company(user, company):
        messages.error(request, "Нет прав на изменение договора по этой компании.")
        return redirect("company_detail", company_id=company.id)

    form = CompanyContractForm(request.POST, instance=company)
    if not form.is_valid():
        messages.error(request, "Проверьте поля договора.")
        return redirect("company_detail", company_id=company.id)

    form.save()
    messages.success(request, "Данные договора обновлены.")
    log_event(
        actor=user,
        verb=ActivityEvent.Verb.UPDATE,
        entity_type="company",
        entity_id=company.id,
        company_id=company.id,
        message="Обновлены данные договора",
    )
    return redirect("company_detail", company_id=company.id)


@login_required
def company_cold_call_toggle(request: HttpRequest, company_id) -> HttpResponse:
    if request.method != "POST":
        return redirect("company_detail", company_id=company_id)

    user: User = request.user
    company = get_object_or_404(Company.objects.select_related("responsible", "branch"), id=company_id)
    if not _can_edit_company(user, company):
        messages.error(request, "Нет прав на изменение признака 'Холодный звонок'.")
        return redirect("company_detail", company_id=company.id)

    company.primary_contact_is_cold_call = not bool(company.primary_contact_is_cold_call)
    company.save(update_fields=["primary_contact_is_cold_call", "updated_at"])

    messages.success(request, "Отметка 'Холодный звонок' (основной контакт) обновлена.")
    log_event(
        actor=user,
        verb=ActivityEvent.Verb.UPDATE,
        entity_type="company",
        entity_id=company.id,
        company_id=company.id,
        message=("Отмечено: холодный звонок (осн. контакт)" if company.primary_contact_is_cold_call else "Снято: холодный звонок (осн. контакт)"),
    )
    return redirect("company_detail", company_id=company.id)


@login_required
def contact_cold_call_toggle(request: HttpRequest, contact_id) -> HttpResponse:
    if request.method != "POST":
        return redirect("dashboard")
    user: User = request.user
    contact = get_object_or_404(Contact.objects.select_related("company"), id=contact_id)
    company = contact.company
    if not company:
        messages.error(request, "Контакт не привязан к компании.")
        return redirect("dashboard")
    if not _can_edit_company(user, company):
        messages.error(request, "Нет прав на изменение контактов этой компании.")
        return redirect("company_detail", company_id=company.id)

    contact.is_cold_call = not bool(contact.is_cold_call)
    contact.save(update_fields=["is_cold_call", "updated_at"])
    messages.success(request, "Отметка 'Холодный звонок' по контакту обновлена.")
    log_event(
        actor=user,
        verb=ActivityEvent.Verb.UPDATE,
        entity_type="contact",
        entity_id=str(contact.id),
        company_id=company.id,
        message=("Отмечено: холодный звонок (контакт)" if contact.is_cold_call else "Снято: холодный звонок (контакт)"),
        meta={"contact_id": str(contact.id)},
    )
    return redirect("company_detail", company_id=company.id)

@login_required
def company_note_pin_toggle(request: HttpRequest, company_id, note_id: int) -> HttpResponse:
    if request.method != "POST":
        return redirect("company_detail", company_id=company_id)

    user: User = request.user
    company = get_object_or_404(Company.objects.select_related("responsible", "branch"), id=company_id)
    if not _can_edit_company(user, company):
        messages.error(request, "Нет прав на закрепление заметок по этой компании.")
        return redirect("company_detail", company_id=company.id)

    note = get_object_or_404(CompanyNote.objects.select_related("company"), id=note_id, company_id=company.id)
    now = timezone.now()

    if note.is_pinned:
        note.is_pinned = False
        note.pinned_at = None
        note.pinned_by = None
        note.save(update_fields=["is_pinned", "pinned_at", "pinned_by"])
        messages.success(request, "Заметка откреплена.")
        log_event(
            actor=user,
            verb=ActivityEvent.Verb.UPDATE,
            entity_type="note",
            entity_id=str(note.id),
            company_id=company.id,
            message="Откреплена заметка",
        )
        return redirect("company_detail", company_id=company.id)

    # Закрепляем: снимаем закрепление с других заметок (одна закреплённая на компанию)
    CompanyNote.objects.filter(company=company, is_pinned=True).exclude(id=note.id).update(is_pinned=False, pinned_at=None, pinned_by=None)
    note.is_pinned = True
    note.pinned_at = now
    note.pinned_by = user
    note.save(update_fields=["is_pinned", "pinned_at", "pinned_by"])

    messages.success(request, "Заметка закреплена.")
    log_event(
        actor=user,
        verb=ActivityEvent.Verb.UPDATE,
        entity_type="note",
        entity_id=str(note.id),
        company_id=company.id,
        message="Закреплена заметка",
    )
    return redirect("company_detail", company_id=company.id)

@login_required
def company_note_attachment_open(request: HttpRequest, company_id, note_id: int) -> HttpResponse:
    """
    Открыть вложение заметки в новом окне (inline). Доступ: всем пользователям (как просмотр компании).
    """
    company = get_object_or_404(Company.objects.all(), id=company_id)
    note = get_object_or_404(CompanyNote.objects.select_related("company"), id=note_id, company_id=company.id)
    if not note.attachment:
        raise Http404("Файл не найден")
    path = getattr(note.attachment, "path", None)
    if not path:
        raise Http404("Файл недоступен")
    ctype = (note.attachment_content_type or "").strip()
    if not ctype:
        ctype = mimetypes.guess_type(note.attachment_name or note.attachment.name)[0] or "application/octet-stream"
    try:
        return FileResponse(open(path, "rb"), as_attachment=False, filename=(note.attachment_name or "file"), content_type=ctype)
    except FileNotFoundError:
        raise Http404("Файл не найден")


@login_required
def company_note_attachment_download(request: HttpRequest, company_id, note_id: int) -> HttpResponse:
    """
    Скачать вложение заметки (attachment). Доступ: всем пользователям (как просмотр компании).
    """
    company = get_object_or_404(Company.objects.all(), id=company_id)
    note = get_object_or_404(CompanyNote.objects.select_related("company"), id=note_id, company_id=company.id)
    if not note.attachment:
        raise Http404("Файл не найден")
    path = getattr(note.attachment, "path", None)
    if not path:
        raise Http404("Файл недоступен")
    ctype = (note.attachment_content_type or "").strip()
    if not ctype:
        ctype = mimetypes.guess_type(note.attachment_name or note.attachment.name)[0] or "application/octet-stream"
    try:
        return FileResponse(open(path, "rb"), as_attachment=True, filename=(note.attachment_name or "file"), content_type=ctype)
    except FileNotFoundError:
        raise Http404("Файл не найден")


@login_required
def company_edit(request: HttpRequest, company_id) -> HttpResponse:
    user: User = request.user
    company = get_object_or_404(Company.objects.select_related("responsible", "branch", "status"), id=company_id)
    if not _can_edit_company(user, company):
        messages.error(request, "Нет прав на редактирование данных компании.")
        return redirect("company_detail", company_id=company.id)

    if request.method == "POST":
        form = CompanyEditForm(request.POST, instance=company)
        if form.is_valid():
            form.save()
            messages.success(request, "Данные компании обновлены.")
            log_event(
                actor=user,
                verb=ActivityEvent.Verb.UPDATE,
                entity_type="company",
                entity_id=company.id,
                company_id=company.id,
                message="Обновлены данные компании",
            )
            return redirect("company_detail", company_id=company.id)
    else:
        form = CompanyEditForm(instance=company)

    return render(request, "ui/company_edit.html", {"company": company, "form": form})


@login_required
def company_transfer(request: HttpRequest, company_id) -> HttpResponse:
    if request.method != "POST":
        return redirect("company_detail", company_id=company_id)

    user: User = request.user
    company = get_object_or_404(Company.objects.select_related("responsible", "branch"), id=company_id)
    if not _can_edit_company(user, company):
        messages.error(request, "Нет прав на передачу компании.")
        return redirect("company_detail", company_id=company.id)

    new_resp_id = (request.POST.get("responsible_id") or "").strip()
    if not new_resp_id:
        messages.error(request, "Выберите ответственного.")
        return redirect("company_detail", company_id=company.id)

    new_resp = get_object_or_404(User, id=new_resp_id, is_active=True)
    if new_resp.role not in (User.Role.MANAGER, User.Role.BRANCH_DIRECTOR, User.Role.SALES_HEAD):
        messages.error(request, "Назначить ответственным можно только менеджера, директора филиала или РОП.")
        return redirect("company_detail", company_id=company.id)

    old_resp = company.responsible
    company.responsible = new_resp
    # При передаче обновляем филиал компании под филиал нового ответственного (может быть другой регион).
    company.branch = new_resp.branch
    company.save()

    messages.success(request, f"Ответственный обновлён: {new_resp}.")
    log_event(
        actor=user,
        verb=ActivityEvent.Verb.UPDATE,
        entity_type="company",
        entity_id=company.id,
        company_id=company.id,
        message="Изменён ответственный компании",
        meta={"from": str(old_resp) if old_resp else "", "to": str(new_resp)},
    )
    if new_resp.id != user.id:
        notify(
            user=new_resp,
            kind=Notification.Kind.COMPANY,
            title="Вам передали компанию",
            body=f"{company.name}",
            url=f"/companies/{company.id}/",
        )
    return redirect("company_detail", company_id=company.id)


@login_required
def company_update(request: HttpRequest, company_id) -> HttpResponse:
    if request.method != "POST":
        return redirect("company_detail", company_id=company_id)

    user: User = request.user
    company = get_object_or_404(Company.objects.select_related("responsible", "branch"), id=company_id)
    if not _can_edit_company(user, company):
        messages.error(request, "Редактирование доступно только создателю/ответственному/директору филиала/управляющему.")
        return redirect("company_detail", company_id=company.id)

    form = CompanyQuickEditForm(request.POST, instance=company)
    if form.is_valid():
        form.save()
        messages.success(request, "Карточка компании обновлена.")
        log_event(
            actor=user,
            verb=ActivityEvent.Verb.UPDATE,
            entity_type="company",
            entity_id=company.id,
            company_id=company.id,
            message="Изменены статус/сферы компании",
        )
    else:
        messages.error(request, "Не удалось обновить компанию. Проверь поля.")
    return redirect("company_detail", company_id=company.id)


@login_required
def contact_create(request: HttpRequest, company_id) -> HttpResponse:
    user: User = request.user
    company = get_object_or_404(Company.objects.select_related("responsible", "branch"), id=company_id)
    if not _can_edit_company(user, company):
        messages.error(request, "Нет прав на добавление контактов в эту компанию.")
        return redirect("company_detail", company_id=company.id)

    contact = Contact(company=company)

    if request.method == "POST":
        form = ContactForm(request.POST, instance=contact)
        email_fs = ContactEmailFormSet(request.POST, instance=contact, prefix="emails")
        phone_fs = ContactPhoneFormSet(request.POST, instance=contact, prefix="phones")
        if form.is_valid() and email_fs.is_valid() and phone_fs.is_valid():
            contact = form.save()
            email_fs.instance = contact
            phone_fs.instance = contact
            email_fs.save()
            phone_fs.save()
            messages.success(request, "Контакт добавлен.")
            log_event(
                actor=user,
                verb=ActivityEvent.Verb.CREATE,
                entity_type="contact",
                entity_id=contact.id,
                company_id=company.id,
                message=f"Добавлен контакт: {contact}",
            )
            return redirect("company_detail", company_id=company.id)
    else:
        form = ContactForm(instance=contact)
        email_fs = ContactEmailFormSet(instance=contact, prefix="emails")
        phone_fs = ContactPhoneFormSet(instance=contact, prefix="phones")

    return render(
        request,
        "ui/contact_form.html",
        {"company": company, "form": form, "email_fs": email_fs, "phone_fs": phone_fs, "mode": "create"},
    )


@login_required
def contact_edit(request: HttpRequest, contact_id) -> HttpResponse:
    user: User = request.user
    contact = get_object_or_404(Contact.objects.select_related("company", "company__responsible", "company__branch"), id=contact_id)
    company = contact.company
    if not company:
        messages.error(request, "Контакт не привязан к компании.")
        return redirect("company_list")
    if not _can_edit_company(user, company):
        messages.error(request, "Нет прав на редактирование контактов этой компании.")
        return redirect("company_detail", company_id=company.id)

    if request.method == "POST":
        form = ContactForm(request.POST, instance=contact)
        email_fs = ContactEmailFormSet(request.POST, instance=contact, prefix="emails")
        phone_fs = ContactPhoneFormSet(request.POST, instance=contact, prefix="phones")
        if form.is_valid() and email_fs.is_valid() and phone_fs.is_valid():
            form.save()
            email_fs.save()
            phone_fs.save()
            messages.success(request, "Контакт обновлён.")
            log_event(
                actor=user,
                verb=ActivityEvent.Verb.UPDATE,
                entity_type="contact",
                entity_id=contact.id,
                company_id=company.id,
                message=f"Обновлён контакт: {contact}",
            )
            return redirect("company_detail", company_id=company.id)
    else:
        form = ContactForm(instance=contact)
        email_fs = ContactEmailFormSet(instance=contact, prefix="emails")
        phone_fs = ContactPhoneFormSet(instance=contact, prefix="phones")

    return render(
        request,
        "ui/contact_form.html",
        {"company": company, "contact": contact, "form": form, "email_fs": email_fs, "phone_fs": phone_fs, "mode": "edit"},
    )


@login_required
def company_note_add(request: HttpRequest, company_id) -> HttpResponse:
    if request.method != "POST":
        return redirect("company_detail", company_id=company_id)

    user: User = request.user
    company = get_object_or_404(Company.objects.select_related("responsible", "branch"), id=company_id)

    # Заметки по карточке: доступно всем, кто имеет доступ к просмотру карточки (в проекте это все пользователи).
    form = CompanyNoteForm(request.POST, request.FILES)
    if form.is_valid():
        note: CompanyNote = form.save(commit=False)
        note.company = company
        note.author = user
        if note.attachment:
            try:
                note.attachment_name = (getattr(note.attachment, "name", "") or "").split("/")[-1].split("\\")[-1]
                note.attachment_ext = (note.attachment_name.rsplit(".", 1)[-1].lower() if "." in note.attachment_name else "")[:16]
                note.attachment_size = int(getattr(note.attachment, "size", 0) or 0)
                note.attachment_content_type = (getattr(note.attachment, "content_type", "") or "").strip()[:120]
            except Exception:
                pass
        note.save()
        log_event(
            actor=user,
            verb=ActivityEvent.Verb.COMMENT,
            entity_type="note",
            entity_id=note.id,
            company_id=company.id,
            message="Добавлена заметка",
        )
        # уведомление ответственному (если это не он)
        if company.responsible_id and company.responsible_id != user.id:
            notify(
                user=company.responsible,
                kind=Notification.Kind.COMPANY,
                title="Новая заметка по компании",
                body=f"{company.name}: {(note.text or '').strip()[:180] or 'Вложение'}",
                url=f"/companies/{company.id}/",
            )

    return redirect("company_detail", company_id=company_id)


@login_required
def company_note_edit(request: HttpRequest, company_id, note_id: int) -> HttpResponse:
    if request.method != "POST":
        return redirect("company_detail", company_id=company_id)

    user: User = request.user
    company = get_object_or_404(Company.objects.all(), id=company_id)

    # Редактировать заметки:
    # - админ/суперпользователь/управляющий: любые
    # - остальные: только свои
    if user.is_superuser or user.role in (User.Role.ADMIN, User.Role.GROUP_MANAGER):
        note = get_object_or_404(CompanyNote.objects.select_related("author"), id=note_id, company_id=company.id)
    else:
        note = get_object_or_404(CompanyNote.objects.select_related("author"), id=note_id, company_id=company.id, author_id=user.id)

    text = (request.POST.get("text") or "").strip()
    remove_attachment = (request.POST.get("remove_attachment") or "").strip() == "1"
    new_file = request.FILES.get("attachment")

    old_file = note.attachment  # storage object
    old_name = getattr(old_file, "name", "") if old_file else ""

    # Если попросили удалить файл — удалим привязку (и сам файл ниже)
    if remove_attachment and note.attachment:
        note.attachment = None
        note.attachment_name = ""
        note.attachment_ext = ""
        note.attachment_size = 0
        note.attachment_content_type = ""

    # Если загрузили новый файл — заменяем
    if new_file:
        note.attachment = new_file
        try:
            note.attachment_name = (getattr(new_file, "name", "") or "").split("/")[-1].split("\\")[-1]
            note.attachment_ext = (note.attachment_name.rsplit(".", 1)[-1].lower() if "." in note.attachment_name else "")[:16]
            note.attachment_size = int(getattr(new_file, "size", 0) or 0)
            note.attachment_content_type = (getattr(new_file, "content_type", "") or "").strip()[:120]
        except Exception:
            pass

    # Не даём превратить заметку в пустую (без текста и без файла)
    if not text and not note.attachment:
        messages.error(request, "Заметка не может быть пустой (нужен текст или файл).")
        return redirect("company_detail", company_id=company.id)

    note.text = text
    note.edited_at = timezone.now()
    note.save()

    # Удаляем старый файл из storage, если он был удалён/заменён
    try:
        new_name = getattr(note.attachment, "name", "") if note.attachment else ""
        should_delete_old = bool(old_file and old_name and (remove_attachment or (new_file is not None)) and old_name != new_name)
        if should_delete_old:
            old_file.delete(save=False)
    except Exception:
        pass

    messages.success(request, "Заметка обновлена.")
    log_event(
        actor=user,
        verb=ActivityEvent.Verb.UPDATE,
        entity_type="note",
        entity_id=str(note.id),
        company_id=company.id,
        message="Изменена заметка",
    )
    return redirect("company_detail", company_id=company.id)


@login_required
def company_note_delete(request: HttpRequest, company_id, note_id: int) -> HttpResponse:
    if request.method != "POST":
        return redirect("company_detail", company_id=company_id)

    user: User = request.user
    company = get_object_or_404(Company.objects.all(), id=company_id)

    # Удалять заметки:
    # - админ/суперпользователь/управляющий: любые
    # - остальные: только свои
    if user.is_superuser or user.role in (User.Role.ADMIN, User.Role.GROUP_MANAGER):
        note = get_object_or_404(CompanyNote.objects.select_related("author"), id=note_id, company_id=company.id)
    else:
        note = get_object_or_404(CompanyNote.objects.select_related("author"), id=note_id, company_id=company.id, author_id=user.id)
    # Удаляем вложенный файл из storage, затем запись
    try:
        if note.attachment:
            note.attachment.delete(save=False)
    except Exception:
        pass
    note.delete()

    messages.success(request, "Заметка удалена.")
    log_event(
        actor=user,
        verb=ActivityEvent.Verb.DELETE,
        entity_type="note",
        entity_id=str(note_id),
        company_id=company.id,
        message="Удалена заметка",
    )
    return redirect("company_detail", company_id=company.id)


@login_required
def phone_call_create(request: HttpRequest) -> HttpResponse:
    """
    UI endpoint: создать "команду на звонок" для телефона текущего пользователя.
    Android-приложение (APK) забирает команду через polling /api/phone/calls/pull/.
    """
    if request.method != "POST":
        return JsonResponse({"ok": False, "detail": "method not allowed"}, status=405)

    user: User = request.user
    phone = (request.POST.get("phone") or "").strip()
    company_id = (request.POST.get("company_id") or "").strip()
    contact_id = (request.POST.get("contact_id") or "").strip()

    if not phone:
        return JsonResponse({"ok": False, "detail": "phone is required"}, status=400)

    # минимальная нормализация для tel: (Android ACTION_DIAL)
    normalized = phone.replace(" ", "").replace("-", "").replace("(", "").replace(")", "")

    # Дедупликация на сервере: если пользователь несколько раз подряд нажимает "позвонить" на тот же номер/контакт,
    # не создаём новые записи (иначе отчёты раздуваются).
    now = timezone.now()
    recent = now - timedelta(seconds=60)
    existing = (
        CallRequest.objects.filter(created_by=user, phone_raw=normalized, created_at__gte=recent)
        .exclude(status=CallRequest.Status.CANCELLED)
    )
    if company_id:
        existing = existing.filter(company_id=company_id)
    else:
        existing = existing.filter(company__isnull=True)
    if contact_id:
        existing = existing.filter(contact_id=contact_id)
    else:
        existing = existing.filter(contact__isnull=True)
    prev_call = existing.order_by("-created_at").first()
    if prev_call:
        return JsonResponse({"ok": True, "id": str(prev_call.id), "phone": normalized, "dedup": True})

    call = CallRequest.objects.create(
        user=user,
        created_by=user,
        company_id=company_id or None,
        contact_id=contact_id or None,
        phone_raw=normalized,
        note="UI click",
    )
    # Пометка "холодный звонок" на уровне контакта (или основного контакта компании)
    try:
        is_cold = False
        if contact_id:
            c = Contact.objects.filter(id=contact_id).only("id", "is_cold_call").first()
            is_cold = bool(getattr(c, "is_cold_call", False))
        elif company_id:
            c0 = Company.objects.filter(id=company_id).only("id", "primary_contact_is_cold_call").first()
            is_cold = bool(getattr(c0, "primary_contact_is_cold_call", False))
        if is_cold:
            call.is_cold_call = True
            call.save(update_fields=["is_cold_call"])
    except Exception:
        pass
    log_event(
        actor=user,
        verb=ActivityEvent.Verb.CREATE,
        entity_type="call_request",
        entity_id=str(call.id),
        company_id=company_id or None,
        message="Запрос на звонок с телефона",
        meta={"phone": normalized, "contact_id": contact_id or None},
    )
    return JsonResponse({"ok": True, "id": str(call.id), "phone": normalized})


@login_required
def task_list(request: HttpRequest) -> HttpResponse:
    user: User = request.user
    now = timezone.now()
    local_now = timezone.localtime(now)
    today_start = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
    tomorrow_start = today_start + timedelta(days=1)

    qs = Task.objects.select_related("company", "assigned_to", "created_by", "type").order_by("-created_at")

    # Просмотр задач: всем доступны все задачи (без ограничения по компаниям/филиалам).
    qs = qs.distinct()

    status = (request.GET.get("status") or "").strip()
    if status:
        qs = qs.filter(status=status)

    mine = (request.GET.get("mine") or "").strip()
    if mine == "1":
        qs = qs.filter(assigned_to=user)

    overdue = (request.GET.get("overdue") or "").strip()
    if overdue == "1":
        qs = qs.filter(due_at__lt=now).exclude(status__in=[Task.Status.DONE, Task.Status.CANCELLED])

    today = (request.GET.get("today") or "").strip()
    if today == "1":
        qs = qs.filter(due_at__gte=today_start, due_at__lt=tomorrow_start).exclude(status__in=[Task.Status.DONE, Task.Status.CANCELLED])

    paginator = Paginator(qs, 25)
    page = paginator.get_page(request.GET.get("page"))

    # Для шаблона: не делаем сложные выражения в {% if %}, чтобы не ловить TemplateSyntaxError.
    # Проставим флаг прямо в объекты текущей страницы.
    for t in page.object_list:
        t.can_manage_status = _can_manage_task_status_ui(user, t)  # type: ignore[attr-defined]

    return render(request, "ui/task_list.html", {"now": now, "page": page, "status": status, "mine": mine, "overdue": overdue, "today": today})


@login_required
def task_create(request: HttpRequest) -> HttpResponse:
    user: User = request.user

    if request.method == "POST":
        form = TaskForm(request.POST)
        if form.is_valid():
            task: Task = form.save(commit=False)
            task.created_by = user
            apply_to_org = bool(form.cleaned_data.get("apply_to_org_branches"))
            comp = None
            if task.company_id:
                comp = Company.objects.select_related("responsible", "branch", "head_company").filter(id=task.company_id).first()
                if comp and not _can_edit_company(user, comp):
                    messages.error(request, "Нет прав на постановку задач по этой компании.")
                    return redirect("company_detail", company_id=comp.id)

            # RBAC как в API:
            if user.role == User.Role.MANAGER:
                task.assigned_to = user
            else:
                if not task.assigned_to:
                    task.assigned_to = user
                if user.role in (User.Role.BRANCH_DIRECTOR, User.Role.SALES_HEAD) and user.branch_id:
                    if task.assigned_to and task.assigned_to.branch_id and task.assigned_to.branch_id != user.branch_id:
                        messages.error(request, "Можно назначать задачи только сотрудникам своего филиала.")
                        return redirect("task_create")

            # Если включено "на все филиалы организации" — создаём копии по всем карточкам организации
            if apply_to_org and comp:
                head = comp.head_company or comp
                org_companies = list(
                    Company.objects.select_related("responsible", "branch", "head_company")
                    .filter(Q(id=head.id) | Q(head_company_id=head.id))
                    .distinct()
                )
                created = 0
                skipped = 0
                for c in org_companies:
                    if not _can_edit_company(user, c):
                        skipped += 1
                        continue
                    t = Task(
                        created_by=user,
                        assigned_to=task.assigned_to,
                        company=c,
                        type=task.type,
                        title=task.title,
                        description=task.description,
                        due_at=task.due_at,
                        recurrence_rrule=task.recurrence_rrule,
                        status=Task.Status.NEW,
                    )
                    t.save()
                    created += 1
                    log_event(
                        actor=user,
                        verb=ActivityEvent.Verb.CREATE,
                        entity_type="task",
                        entity_id=t.id,
                        company_id=c.id,
                        message=f"Создана задача (по организации): {t.title}",
                    )
                if created:
                    messages.success(request, f"Задача создана по организации: {created} карточек. Пропущено (нет прав): {skipped}.")
                else:
                    messages.error(request, "Не удалось создать задачу по организации (нет прав).")
                # уведомление назначенному (если это не создатель)
                if task.assigned_to_id and task.assigned_to_id != user.id and created:
                    notify(
                        user=task.assigned_to,
                        kind=Notification.Kind.TASK,
                        title="Вам назначили задачи",
                        body=f"{task.title} (по организации) · {created} компаний",
                        url="/tasks/?mine=1",
                    )
                return redirect("task_list")

            # обычное создание
            task.save()
            form.save_m2m()
            # уведомление назначенному (если это не создатель)
            if task.assigned_to_id and task.assigned_to_id != user.id:
                notify(
                    user=task.assigned_to,
                    kind=Notification.Kind.TASK,
                    title="Вам назначили задачу",
                    body=f"{task.title}",
                    url="/tasks/",
                )
            if task.company_id:
                log_event(
                    actor=user,
                    verb=ActivityEvent.Verb.CREATE,
                    entity_type="task",
                    entity_id=task.id,
                    company_id=task.company_id,
                    message=f"Создана задача: {task.title}",
                )
            return redirect("task_list")
    else:
        initial = {"assigned_to": user}
        company_id = (request.GET.get("company") or "").strip()
        if company_id:
            comp = Company.objects.select_related("responsible", "branch", "head_company").filter(id=company_id).first()
            if comp and _can_edit_company(user, comp):
                initial["company"] = company_id
                # если есть организация (головная или филиалы), включим флажок по умолчанию
                head = (comp.head_company or comp)
                has_org = Company.objects.filter(Q(id=head.id) | Q(head_company_id=head.id)).exclude(id=comp.id).exists()
                if has_org:
                    initial["apply_to_org_branches"] = True
            else:
                messages.warning(request, "Нет прав на постановку задач по этой компании.")
        form = TaskForm(initial=initial)

    # Выбор компании: только те, которые пользователь может редактировать
    form.fields["company"].queryset = _editable_company_qs(user).order_by("name")

    # Ограничить назначаемых
    if user.role == User.Role.MANAGER:
        form.fields["assigned_to"].queryset = User.objects.filter(id=user.id)
    elif user.role in (User.Role.BRANCH_DIRECTOR, User.Role.SALES_HEAD) and user.branch_id:
        form.fields["assigned_to"].queryset = User.objects.filter(Q(id=user.id) | Q(branch_id=user.branch_id, role=User.Role.MANAGER)).order_by("last_name", "first_name")
    else:
        form.fields["assigned_to"].queryset = User.objects.order_by("last_name", "first_name")

    return render(request, "ui/task_create.html", {"form": form})

def _can_manage_task_status_ui(user: User, task: Task) -> bool:
    if not user or not user.is_authenticated or not user.is_active:
        return False
    if user.is_superuser or user.role in (User.Role.ADMIN, User.Role.GROUP_MANAGER):
        return True
    if task.assigned_to_id and task.assigned_to_id == user.id:
        return True
    if user.role in (User.Role.BRANCH_DIRECTOR, User.Role.SALES_HEAD) and user.branch_id:
        branch_id = None
        if task.company_id and getattr(task, "company", None):
            branch_id = getattr(task.company, "branch_id", None)
        if not branch_id and getattr(task, "assigned_to", None):
            branch_id = getattr(task.assigned_to, "branch_id", None)
        return bool(branch_id and branch_id == user.branch_id)
    return False


@login_required
def task_set_status(request: HttpRequest, task_id) -> HttpResponse:
    if request.method != "POST":
        return redirect("task_list")

    user: User = request.user
    task = get_object_or_404(Task.objects.select_related("company", "company__responsible", "company__branch", "assigned_to"), id=task_id)

    if not _can_manage_task_status_ui(user, task):
        messages.error(request, "Нет прав на изменение статуса этой задачи.")
        return redirect("task_list")

    new_status = (request.POST.get("status") or "").strip()
    if new_status not in {s for s, _ in Task.Status.choices}:
        messages.error(request, "Некорректный статус.")
        return redirect("task_list")

    # Менеджер может менять статус только своих задач (явно, на случай будущих изменений правил)
    if user.role == User.Role.MANAGER and task.assigned_to_id != user.id:
        messages.error(request, "Менеджер может менять статус только своих задач.")
        return redirect("task_list")

    task.status = new_status
    if new_status == Task.Status.DONE:
        task.completed_at = timezone.now()
    task.save(update_fields=["status", "completed_at", "updated_at"])

    messages.success(request, "Статус задачи обновлён.")
    # уведомление создателю (если не он меняет)
    if task.created_by_id and task.created_by_id != user.id:
        notify(
            user=task.created_by,
            kind=Notification.Kind.TASK,
            title="Статус задачи изменён",
            body=f"{task.title}: {task.get_status_display()}",
            url="/tasks/",
        )
    if task.company_id:
        log_event(
            actor=user,
            verb=ActivityEvent.Verb.STATUS,
            entity_type="task",
            entity_id=task.id,
            company_id=task.company_id,
            message=f"Статус задачи: {task.get_status_display()}",
            meta={"status": new_status},
        )
    return redirect(request.META.get("HTTP_REFERER") or "/tasks/")


def _require_admin(user: User) -> bool:
    return bool(user.is_authenticated and user.is_active and (user.is_superuser or user.role == User.Role.ADMIN))


@login_required
def settings_dashboard(request: HttpRequest) -> HttpResponse:
    if not _require_admin(request.user):
        messages.error(request, "Доступ запрещён.")
        return redirect("dashboard")
    return render(request, "ui/settings/dashboard.html", {})


@login_required
def settings_branches(request: HttpRequest) -> HttpResponse:
    if not _require_admin(request.user):
        messages.error(request, "Доступ запрещён.")
        return redirect("dashboard")
    branches = Branch.objects.order_by("name")
    return render(request, "ui/settings/branches.html", {"branches": branches})


@login_required
def settings_branch_create(request: HttpRequest) -> HttpResponse:
    if not _require_admin(request.user):
        messages.error(request, "Доступ запрещён.")
        return redirect("dashboard")
    if request.method == "POST":
        form = BranchForm(request.POST)
        if form.is_valid():
            form.save()
            messages.success(request, "Филиал создан.")
            return redirect("settings_branches")
    else:
        form = BranchForm()
    return render(request, "ui/settings/branch_form.html", {"form": form, "mode": "create"})


@login_required
def settings_branch_edit(request: HttpRequest, branch_id: int) -> HttpResponse:
    if not _require_admin(request.user):
        messages.error(request, "Доступ запрещён.")
        return redirect("dashboard")
    branch = get_object_or_404(Branch, id=branch_id)
    if request.method == "POST":
        form = BranchForm(request.POST, instance=branch)
        if form.is_valid():
            form.save()
            messages.success(request, "Филиал обновлён.")
            return redirect("settings_branches")
    else:
        form = BranchForm(instance=branch)
    return render(request, "ui/settings/branch_form.html", {"form": form, "mode": "edit", "branch": branch})


@login_required
def settings_users(request: HttpRequest) -> HttpResponse:
    if not _require_admin(request.user):
        messages.error(request, "Доступ запрещён.")
        return redirect("dashboard")
    users = User.objects.select_related("branch").order_by("username")
    return render(request, "ui/settings/users.html", {"users": users})


@login_required
def settings_user_create(request: HttpRequest) -> HttpResponse:
    if not _require_admin(request.user):
        messages.error(request, "Доступ запрещён.")
        return redirect("dashboard")
    if request.method == "POST":
        form = UserCreateForm(request.POST)
        if form.is_valid():
            form.save()
            messages.success(request, "Пользователь создан.")
            return redirect("settings_users")
    else:
        form = UserCreateForm()
    return render(request, "ui/settings/user_form.html", {"form": form, "mode": "create"})


@login_required
def settings_user_edit(request: HttpRequest, user_id: int) -> HttpResponse:
    if not _require_admin(request.user):
        messages.error(request, "Доступ запрещён.")
        return redirect("dashboard")
    u = get_object_or_404(User, id=user_id)
    if request.method == "POST":
        form = UserEditForm(request.POST, instance=u)
        if form.is_valid():
            form.save()
            messages.success(request, "Пользователь обновлён.")
            return redirect("settings_users")
    else:
        form = UserEditForm(instance=u)
    return render(request, "ui/settings/user_form.html", {"form": form, "mode": "edit", "u": u})


@login_required
def settings_dicts(request: HttpRequest) -> HttpResponse:
    if not _require_admin(request.user):
        messages.error(request, "Доступ запрещён.")
        return redirect("dashboard")
    return render(
        request,
        "ui/settings/dicts.html",
        {
            "company_statuses": CompanyStatus.objects.order_by("name"),
            "company_spheres": CompanySphere.objects.order_by("name"),
            "task_types": TaskType.objects.order_by("name"),
        },
    )


@login_required
def settings_company_status_create(request: HttpRequest) -> HttpResponse:
    if not _require_admin(request.user):
        messages.error(request, "Доступ запрещён.")
        return redirect("dashboard")
    if request.method == "POST":
        form = CompanyStatusForm(request.POST)
        if form.is_valid():
            form.save()
            messages.success(request, "Статус добавлен.")
            return redirect("settings_dicts")
    else:
        form = CompanyStatusForm()
    return render(request, "ui/settings/dict_form.html", {"form": form, "title": "Новый статус компании"})


@login_required
def settings_company_sphere_create(request: HttpRequest) -> HttpResponse:
    if not _require_admin(request.user):
        messages.error(request, "Доступ запрещён.")
        return redirect("dashboard")
    if request.method == "POST":
        form = CompanySphereForm(request.POST)
        if form.is_valid():
            form.save()
            messages.success(request, "Сфера добавлена.")
            return redirect("settings_dicts")
    else:
        form = CompanySphereForm()
    return render(request, "ui/settings/dict_form.html", {"form": form, "title": "Новая сфера компании"})


@login_required
def settings_task_type_create(request: HttpRequest) -> HttpResponse:
    if not _require_admin(request.user):
        messages.error(request, "Доступ запрещён.")
        return redirect("dashboard")
    if request.method == "POST":
        form = TaskTypeForm(request.POST)
        if form.is_valid():
            form.save()
            messages.success(request, "Тип задачи добавлен.")
            return redirect("settings_dicts")
    else:
        form = TaskTypeForm()
    return render(request, "ui/settings/dict_form.html", {"form": form, "title": "Новый тип задачи"})


@login_required
def settings_activity(request: HttpRequest) -> HttpResponse:
    if not _require_admin(request.user):
        messages.error(request, "Доступ запрещён.")
        return redirect("dashboard")
    events = ActivityEvent.objects.select_related("actor").order_by("-created_at")[:500]
    return render(request, "ui/settings/activity.html", {"events": events})


@login_required
def settings_import(request: HttpRequest) -> HttpResponse:
    if not _require_admin(request.user):
        messages.error(request, "Доступ запрещён.")
        return redirect("dashboard")

    result = None
    if request.method == "POST":
        form = ImportCompaniesForm(request.POST, request.FILES)
        if form.is_valid():
            import tempfile
            from pathlib import Path

            f = form.cleaned_data["csv_file"]
            limit_companies = int(form.cleaned_data["limit_companies"])
            dry_run = bool(form.cleaned_data.get("dry_run"))

            # Сохраняем во временный файл, чтобы использовать общий импортёр
            fd, tmp_path = tempfile.mkstemp(suffix=".csv")
            Path(tmp_path).write_bytes(f.read())

            try:
                from companies.importer import import_amo_csv

                result = import_amo_csv(
                    csv_path=tmp_path,
                    encoding="utf-8-sig",
                    dry_run=dry_run,
                    companies_only=True,
                    limit_companies=limit_companies,
                )
                if dry_run:
                    messages.success(request, "Проверка (dry-run) выполнена.")
                else:
                    messages.success(request, f"Импорт выполнен: добавлено {result.created_companies}, обновлено {result.updated_companies}.")
            finally:
                try:
                    Path(tmp_path).unlink(missing_ok=True)
                except Exception:
                    pass
    else:
        form = ImportCompaniesForm()

    return render(request, "ui/settings/import.html", {"form": form, "result": result})

# UI settings (admin only)
@login_required
def settings_company_columns(request: HttpRequest) -> HttpResponse:
    if not _require_admin(request.user):
        messages.error(request, "Доступ запрещён.")
        return redirect("dashboard")

    cfg = UiGlobalConfig.load()
    if request.method == "POST":
        form = CompanyListColumnsForm(request.POST)
        if form.is_valid():
            cfg.company_list_columns = form.cleaned_data["columns"]
            cfg.save(update_fields=["company_list_columns", "updated_at"])
            messages.success(request, "Колонки списка компаний обновлены.")
            return redirect("settings_company_columns")
    else:
        form = CompanyListColumnsForm(initial={"columns": cfg.company_list_columns or ["name"]})

    return render(request, "ui/settings/company_columns.html", {"form": form, "cfg": cfg})


@login_required
def settings_security(request: HttpRequest) -> HttpResponse:
    if not _require_admin(request.user):
        messages.error(request, "Доступ запрещён.")
        return redirect("dashboard")

    # Последние события экспорта (успешные и запрещённые)
    exports = (
        ActivityEvent.objects.filter(entity_type="export")
        .select_related("actor")
        .order_by("-created_at")[:200]
    )

    # Статистика по пользователям: кто чаще всего пытался/делал экспорт
    export_stats = (
        ActivityEvent.objects.filter(entity_type="export")
        .values("actor_id", "actor__first_name", "actor__last_name")
        .annotate(
            total=Count("id"),
            denied=Count("id", filter=Q(meta__allowed=False)),
            last=Max("created_at"),
        )
        .order_by("-denied", "-total", "-last")[:30]
    )

    # Простейший список "подозрительных" — любые denied попытки
    suspicious = (
        ActivityEvent.objects.filter(entity_type="export", meta__allowed=False)
        .select_related("actor")
        .order_by("-created_at")[:200]
    )

    return render(
        request,
        "ui/settings/security.html",
        {
            "exports": exports,
            "export_stats": export_stats,
            "suspicious": suspicious,
        },
    )

# (no-op)
