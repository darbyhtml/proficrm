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
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from accounts.models import Branch, User
from accounts.scope import apply_company_scope
from audit.models import ActivityEvent
from audit.service import log_event
from companies.models import Company, CompanyNote, CompanySphere, CompanyStatus, Contact
from companies.permissions import can_edit_company as can_edit_company_perm, editable_company_qs as editable_company_qs_perm
from tasksapp.models import Task, TaskType
from notifications.models import Notification
from notifications.service import notify

from .forms import (
    CompanyCreateForm,
    CompanyQuickEditForm,
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


@login_required
def dashboard(request: HttpRequest) -> HttpResponse:
    user: User = request.user
    now = timezone.now()
    # Важно: при USE_TZ=True timezone.now() в UTC. Для фильтров "сегодня/неделя" считаем границы по локальной TZ.
    local_now = timezone.localtime(now)
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

    return render(
        request,
        "ui/dashboard.html",
        {
            "now": now,
            "tasks_new": tasks_new,
            "tasks_today": tasks_today,
            "overdue": overdue,
            "tasks_week": tasks_week,
        },
    )


@login_required
def company_list(request: HttpRequest) -> HttpResponse:
    user: User = request.user
    now = timezone.now()
    # Просмотр компаний: всем доступна вся база (без ограничения по филиалу/scope).
    base_qs = Company.objects.all()
    companies_total = base_qs.order_by().count()
    overdue_tasks = (
        Task.objects.filter(company_id=OuterRef("pk"), due_at__lt=now)
        .exclude(status__in=[Task.Status.DONE, Task.Status.CANCELLED])
        .values("id")
    )

    qs = (
        base_qs
        .select_related("responsible", "branch", "status")
        .prefetch_related("spheres")
        .annotate(has_overdue=Exists(overdue_tasks))
    )

    q = (request.GET.get("q") or "").strip()
    if q:
        qs = qs.filter(Q(name__icontains=q) | Q(inn__icontains=q) | Q(legal_name__icontains=q) | Q(address__icontains=q))

    responsible = (request.GET.get("responsible") or "").strip()
    if responsible:
        qs = qs.filter(responsible_id=responsible)

    status = (request.GET.get("status") or "").strip()
    if status:
        qs = qs.filter(status_id=status)

    branch = (request.GET.get("branch") or "").strip()
    if branch:
        qs = qs.filter(branch_id=branch)

    sphere = (request.GET.get("sphere") or "").strip()
    if sphere:
        qs = qs.filter(spheres__id=sphere)

    only_overdue = (request.GET.get("overdue") or "").strip()
    if only_overdue == "1":
        qs = qs.filter(has_overdue=True)

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

    filter_active = any([q, responsible, status, branch, sphere, only_overdue == "1"])
    companies_filtered = qs.order_by().count()

    paginator = Paginator(qs, 25)
    page = paginator.get_page(request.GET.get("page"))
    ui_cfg = UiGlobalConfig.load()
    columns = ui_cfg.company_list_columns or ["name"]

    return render(
        request,
        "ui/company_list.html",
        {
            "page": page,
            "q": q,
            "responsible": responsible,
            "status": status,
            "branch": branch,
            "sphere": sphere,
            "overdue": only_overdue,
            "companies_total": companies_total,
            "companies_filtered": companies_filtered,
            "filter_active": filter_active,
            "sort": sort,
            "dir": direction,
            "responsibles": User.objects.order_by("last_name", "first_name"),
            "statuses": CompanyStatus.objects.order_by("name"),
            "spheres": CompanySphere.objects.order_by("name"),
            "branches": Branch.objects.order_by("name"),
            "company_list_columns": columns,
        },
    )


@login_required
def company_export(request: HttpRequest) -> HttpResponse:
    """
    Экспорт компаний (по текущим фильтрам) в CSV, с учётом выбранных колонок.
    """
    import csv
    from django.utils.text import slugify

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
    overdue_tasks = (
        Task.objects.filter(company_id=OuterRef("pk"), due_at__lt=now)
        .exclude(status__in=[Task.Status.DONE, Task.Status.CANCELLED])
        .values("id")
    )

    qs = (
        Company.objects.all()
        .select_related("responsible", "branch", "status")
        .prefetch_related("spheres")
        .annotate(has_overdue=Exists(overdue_tasks))
        .order_by("-updated_at")
    )

    q = (request.GET.get("q") or "").strip()
    if q:
        qs = qs.filter(Q(name__icontains=q) | Q(inn__icontains=q) | Q(legal_name__icontains=q) | Q(address__icontains=q))

    responsible = (request.GET.get("responsible") or "").strip()
    if responsible:
        qs = qs.filter(responsible_id=responsible)

    status = (request.GET.get("status") or "").strip()
    if status:
        qs = qs.filter(status_id=status)

    branch = (request.GET.get("branch") or "").strip()
    if branch:
        qs = qs.filter(branch_id=branch)

    sphere = (request.GET.get("sphere") or "").strip()
    if sphere:
        qs = qs.filter(spheres__id=sphere)

    only_overdue = (request.GET.get("overdue") or "").strip()
    if only_overdue == "1":
        qs = qs.filter(has_overdue=True)

    cfg = UiGlobalConfig.load()
    cols = cfg.company_list_columns or ["name"]
    if "name" not in cols:
        cols = ["name"] + cols

    header_map = dict(UiGlobalConfig.COMPANY_LIST_COLUMNS)
    headers = [header_map.get(c, c) for c in cols if c != "address"]  # address идёт внутри "Компания"

    def row_for(company: Company):
        row = []
        for c in cols:
            if c == "name":
                row.append(company.name)
            elif c == "address":
                continue
            elif c == "overdue":
                row.append("Да" if getattr(company, "has_overdue", False) else "Нет")
            elif c == "inn":
                row.append(company.inn or "")
            elif c == "status":
                row.append(company.status.name if company.status else "")
            elif c == "spheres":
                row.append(", ".join([s.name for s in company.spheres.all()]))
            elif c == "responsible":
                row.append(str(company.responsible) if company.responsible else "")
            elif c == "branch":
                row.append(str(company.branch) if company.branch else "")
            elif c == "updated_at":
                row.append(company.updated_at.isoformat(sep=" ", timespec="seconds") if company.updated_at else "")
            else:
                row.append("")
        return row

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
            "filters": {"q": q, "responsible": responsible, "status": status, "branch": branch, "sphere": sphere, "overdue": only_overdue},
            "columns": cols,
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
    company = get_object_or_404(Company.objects.select_related("responsible", "branch", "status"), id=company_id)
    can_edit_company = _can_edit_company(user, company)

    contacts = Contact.objects.filter(company=company).prefetch_related("emails", "phones").order_by("last_name", "first_name")[:200]
    notes = CompanyNote.objects.filter(company=company).select_related("author").order_by("-created_at")[:50]
    tasks = (
        Task.objects.filter(company=company)
        .select_related("assigned_to", "type")
        .order_by("-created_at")[:25]
    )

    note_form = CompanyNoteForm()
    activity = ActivityEvent.objects.filter(company_id=company.id).select_related("actor")[:50]
    quick_form = CompanyQuickEditForm(instance=company)

    transfer_targets = User.objects.filter(is_active=True, role=User.Role.MANAGER).order_by("last_name", "first_name")

    return render(
        request,
        "ui/company_detail.html",
        {
            "company": company,
            "can_edit_company": can_edit_company,
            "contacts": contacts,
            "notes": notes,
            "note_form": note_form,
            "tasks": tasks,
            "activity": activity,
            "quick_form": quick_form,
            "transfer_targets": transfer_targets,
        },
    )


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
        messages.error(request, "Выберите менеджера.")
        return redirect("company_detail", company_id=company.id)

    new_resp = get_object_or_404(User, id=new_resp_id, is_active=True)
    if new_resp.role != User.Role.MANAGER:
        messages.error(request, "Передавать можно только менеджеру.")
        return redirect("company_detail", company_id=company.id)

    old_resp = company.responsible
    company.responsible = new_resp
    # При передаче обновляем филиал компании под филиал нового ответственного (может быть другой регион).
    company.branch = new_resp.branch
    company.save()

    messages.success(request, f"Компания передана: {new_resp}.")
    log_event(
        actor=user,
        verb=ActivityEvent.Verb.UPDATE,
        entity_type="company",
        entity_id=company.id,
        company_id=company.id,
        message="Передана компания другому менеджеру",
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
    if not _can_edit_company(user, company):
        messages.error(request, "Нет прав на добавление заметок по этой компании.")
        return redirect("company_detail", company_id=company.id)

    form = CompanyNoteForm(request.POST)
    if form.is_valid():
        note: CompanyNote = form.save(commit=False)
        note.company = company
        note.author = user
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
                body=f"{company.name}: {note.text[:180]}",
                url=f"/companies/{company.id}/",
            )

    return redirect("company_detail", company_id=company_id)


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

    return render(request, "ui/task_list.html", {"now": now, "page": page, "status": status, "mine": mine, "overdue": overdue, "today": today})


@login_required
def task_create(request: HttpRequest) -> HttpResponse:
    user: User = request.user

    if request.method == "POST":
        form = TaskForm(request.POST)
        if form.is_valid():
            task: Task = form.save(commit=False)
            task.created_by = user
            if task.company_id:
                comp = Company.objects.select_related("responsible", "branch").filter(id=task.company_id).first()
                if comp and not _can_edit_company(user, comp):
                    messages.error(request, "Нет прав на постановку задач по этой компании.")
                    return redirect("company_detail", company_id=comp.id)

            # RBAC как в API:
            if user.role == User.Role.MANAGER:
                task.assigned_to = user
            else:
                if not task.assigned_to:
                    task.assigned_to = user
                if user.role == User.Role.BRANCH_DIRECTOR and user.branch_id and task.assigned_to.branch_id != user.branch_id:
                    task.assigned_to = user

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
            comp = Company.objects.select_related("responsible", "branch").filter(id=company_id).first()
            if comp and _can_edit_company(user, comp):
                initial["company"] = company_id
            else:
                messages.warning(request, "Нет прав на постановку задач по этой компании.")
        form = TaskForm(initial=initial)

    # Выбор компании: только те, которые пользователь может редактировать
    form.fields["company"].queryset = _editable_company_qs(user).order_by("name")

    # Ограничить назначаемых
    if user.role == User.Role.MANAGER:
        form.fields["assigned_to"].queryset = User.objects.filter(id=user.id)
    elif user.role == User.Role.BRANCH_DIRECTOR and user.branch_id:
        form.fields["assigned_to"].queryset = User.objects.filter(branch_id=user.branch_id).order_by("last_name", "first_name")
    else:
        form.fields["assigned_to"].queryset = User.objects.order_by("last_name", "first_name")

    return render(request, "ui/task_create.html", {"form": form})


@login_required
def task_set_status(request: HttpRequest, task_id) -> HttpResponse:
    if request.method != "POST":
        return redirect("task_list")

    user: User = request.user
    task = get_object_or_404(Task.objects.select_related("company", "company__responsible", "company__branch", "assigned_to"), id=task_id)

    # Доступ: менять статус может только исполнитель задачи (assigned_to).
    # Исключение: админ/суперпользователь.
    if not (task.assigned_to_id == user.id or user.is_superuser or user.role == User.Role.ADMIN):
        messages.error(request, "Менять статус может только исполнитель задачи.")
        return redirect("task_list")

    new_status = (request.POST.get("status") or "").strip()
    if new_status not in {s for s, _ in Task.Status.choices}:
        messages.error(request, "Некорректный статус.")
        return redirect("task_list")

    # Правило: менеджер может менять статус только своих задач
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
