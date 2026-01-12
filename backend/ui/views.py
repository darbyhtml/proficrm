from __future__ import annotations

from datetime import datetime
from datetime import timedelta

from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.core.paginator import Paginator
from django.db.models import Exists, OuterRef, Q, F
from django.db.models import Count, Max, Prefetch
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
from companies.models import Company, CompanyNote, CompanySphere, CompanyStatus, Contact, ContactPhone, CompanyDeletionRequest, CompanyLeadStateRequest
from companies.permissions import can_edit_company as can_edit_company_perm, editable_company_qs as editable_company_qs_perm
from tasksapp.models import Task, TaskType
from notifications.models import Notification
from notifications.service import notify
from phonebridge.models import CallRequest, PhoneDevice
import mimetypes
import os
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
    TaskEditForm,
    BranchForm,
    CompanySphereForm,
    CompanyStatusForm,
    TaskTypeForm,
    UserCreateForm,
    UserEditForm,
    ImportCompaniesForm,
    ImportTasksIcsForm,
    AmoApiConfigForm,
    AmoMigrateFilterForm,
    CompanyListColumnsForm,
)
from ui.models import UiGlobalConfig, AmoApiConfig

from amocrm.client import AmoApiError, AmoClient
from amocrm.migrate import fetch_amo_users, fetch_company_custom_fields, migrate_filtered
from crm.utils import require_admin

# Константы для фильтров
RESPONSIBLE_FILTER_NONE = "none"  # Значение для фильтрации компаний без ответственного


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


def _can_decide_company_lead_state(user: User, company: Company) -> bool:
    """
    Решать запрос смены состояния карточки могут:
    - админ/суперпользователь/управляющий группой компаний
    - РОП/директор филиала в рамках своего филиала
    """
    if not user or not user.is_authenticated or not user.is_active:
        return False
    if user.is_superuser or user.role in (User.Role.ADMIN, User.Role.GROUP_MANAGER):
        return True
    if user.role in (User.Role.SALES_HEAD, User.Role.BRANCH_DIRECTOR) and user.branch_id:
        return bool(_company_branch_id(company) == user.branch_id)
    return False


def _can_revert_company_lead_state(user: User) -> bool:
    """
    Вернуть карточку из "тёплый контакт" обратно в "холодный контакт" может только администратор/суперпользователь.
    """
    if not user or not user.is_authenticated or not user.is_active:
        return False
    return bool(user.is_superuser or user.role == User.Role.ADMIN)


def _apply_company_become_warm(*, company: Company):
    """
    При переводе карточки в "тёплый контакт" снимаем текущие отметки "холодный" с контактов/основного номера.
    Историю НЕ удаляем: CallRequest.is_cold_call и поля *_marked_* остаются для отчётов/аудита.
    """
    now_ts = timezone.now()
    # основной контакт компании
    # ВАЖНО: сбрасываем только is_cold_call, НЕ трогаем cold_marked_* поля (они остаются для истории)
    Company.objects.filter(id=company.id).update(primary_contact_is_cold_call=False, updated_at=now_ts)
    # контакты компании
    # ВАЖНО: сбрасываем только is_cold_call, НЕ трогаем cold_marked_* поля (они остаются для истории)
    Contact.objects.filter(company_id=company.id).update(is_cold_call=False, updated_at=now_ts)


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


def _apply_company_filters(*, qs, params: dict, default_responsible_id: int | None = None):
    """
    Единые фильтры компаний для:
    - списка компаний
    - экспорта
    - массового переназначения (apply_mode=filtered)
    
    Если default_responsible_id указан и параметр responsible отсутствует в params,
    применяется фильтр по default_responsible_id.
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
    # Если параметр responsible не указан и есть default_responsible_id, применяем фильтр по умолчанию
    if not responsible and default_responsible_id is not None:
        qs = qs.filter(responsible_id=default_responsible_id)
        responsible = str(default_responsible_id)  # Для возврата в результатах
    elif responsible:
        if responsible == RESPONSIBLE_FILTER_NONE:
            qs = qs.filter(responsible__isnull=True)
        else:
            try:
                responsible_id = int(responsible)
                qs = qs.filter(responsible_id=responsible_id)
            except (ValueError, TypeError):
                # Некорректный ID - пропускаем фильтр
                pass

    status = (params.get("status") or "").strip()
    if status:
        try:
            status_id = int(status)
            qs = qs.filter(status_id=status_id)
        except (ValueError, TypeError):
            # Некорректный ID - пропускаем фильтр
            pass

    branch = (params.get("branch") or "").strip()
    if branch:
        try:
            branch_id = int(branch)
            qs = qs.filter(branch_id=branch_id)
        except (ValueError, TypeError):
            # Некорректный ID - пропускаем фильтр
            pass

    sphere = (params.get("sphere") or "").strip()
    if sphere:
        try:
            sphere_id = int(sphere)
            qs = qs.filter(spheres__id=sphere_id)
        except (ValueError, TypeError):
            # Некорректный ID - пропускаем фильтр
            pass

    contract_type = (params.get("contract_type") or "").strip()
    if contract_type:
        qs = qs.filter(contract_type=contract_type)

    # Состояние карточки: холодная/тёплая
    cold_call = (params.get("cold_call") or "").strip()
    if cold_call == "1":
        qs = qs.filter(lead_state=Company.LeadState.COLD)
    elif cold_call == "0":
        qs = qs.filter(lead_state=Company.LeadState.WARM)

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


def _qs_without_page(request: HttpRequest, *, page_key: str = "page") -> str:
    """
    Для пагинации: сохранить все текущие GET-параметры, кроме номера страницы.
    Возвращает строку формата "a=1&b=2" (без ведущего "?").
    """
    params = request.GET.copy()
    try:
        params.pop(page_key, None)
    except Exception:
        pass
    return params.urlencode()

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
    # Примечание: фильтр по status=NEW уже исключает DONE и CANCELLED, поэтому дополнительный exclude не нужен
    tasks_new = (
        Task.objects.filter(assigned_to=user, status=Task.Status.NEW)
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
            "local_now": local_now,  # Для корректного сравнения дат в шаблоне
            "tasks_new": tasks_new,
            "tasks_today": tasks_today,
            "overdue": overdue,
            "tasks_week": tasks_week,
            "contracts_soon": contracts_soon,
            "can_view_cold_call_reports": _can_view_cold_call_reports(user),
        },
    )


@login_required
def analytics(request: HttpRequest) -> HttpResponse:
    """
    Аналитика по звонкам/отметкам для руководителей:
    - РОП/директор: по своему филиалу
    - управляющий/админ: по всем филиалам (с группировкой)
    """
    user: User = request.user
    if not (user.is_superuser or user.role in (User.Role.ADMIN, User.Role.GROUP_MANAGER, User.Role.BRANCH_DIRECTOR, User.Role.SALES_HEAD)):
        messages.error(request, "Нет доступа к аналитике.")
        return redirect("dashboard")

    now = timezone.now()
    local_now = timezone.localtime(now)
    period = (request.GET.get("period") or "day").strip()  # day|month
    if period not in ("day", "month"):
        period = "day"

    if period == "month":
        start = local_now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        end = (start + timedelta(days=32)).replace(day=1)
        period_label = _month_label(timezone.localdate(now))
    else:
        start = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
        end = start + timedelta(days=1)
        period_label = timezone.localdate(now).strftime("%d.%m.%Y")

    # Кого показываем (админ НЕ отображается как субъект аналитики)
    if user.is_superuser or user.role in (User.Role.ADMIN, User.Role.GROUP_MANAGER):
        users_qs = User.objects.filter(is_active=True, role__in=[User.Role.MANAGER, User.Role.SALES_HEAD, User.Role.BRANCH_DIRECTOR]).select_related("branch")
    else:
        users_qs = User.objects.filter(is_active=True, branch_id=user.branch_id, role__in=[User.Role.MANAGER, User.Role.SALES_HEAD, User.Role.BRANCH_DIRECTOR]).select_related("branch")
    users_list = list(users_qs.order_by("branch__name", "last_name", "first_name"))
    user_ids = [u.id for u in users_list]

    # Звонки за период (лимит на страницу, чтобы не убить UI)
    # Для консистентности с аналитикой сотрудника считаем только клики "Позвонить с телефона" (note="UI click").
    calls_qs = (
        CallRequest.objects.filter(created_by_id__in=user_ids, created_at__gte=start, created_at__lt=end, note="UI click")
        .exclude(status=CallRequest.Status.CANCELLED)
        .select_related("company", "contact", "created_by")
        .order_by("-created_at")[:5000]
    )
    stats = {uid: {"calls_total": 0, "cold_calls": 0} for uid in user_ids}
    for call in calls_qs:
        uid = call.created_by_id
        if uid not in stats:
            continue
        stats[uid]["calls_total"] += 1
        # "Холодный" считаем строго: is_cold_call=True + подтверждение marked_call
        is_strict_cold = bool(
            getattr(call, "is_cold_call", False)
            and (
                (getattr(call, "company", None) and getattr(call.company, "primary_cold_marked_call_id", None) == call.id)
                or (getattr(call, "contact", None) and getattr(call.contact, "cold_marked_call_id", None) == call.id)
            )
        )
        if is_strict_cold:
            stats[uid]["cold_calls"] += 1

    # Группировка по филиалу (для управляющего) + карточки для шаблона
    groups_map = {}
    for u in users_list:
        bid = getattr(u, "branch_id", None)
        groups_map.setdefault(bid, {"branch": getattr(u, "branch", None), "rows": []})
        s = stats.get(u.id, {})
        groups_map[bid]["rows"].append(
            {
                "user": u,
                "calls_total": int(s.get("calls_total", 0) or 0),
                "cold_calls": int(s.get("cold_calls", 0) or 0),
                "url": f"/analytics/users/{u.id}/?period={period}",
            }
        )

    return render(
        request,
        "ui/analytics.html",
        {
            "period": period,
            "period_label": period_label,
            "groups": list(groups_map.values()),
        },
    )


@login_required
@login_required
def help_page(request: HttpRequest) -> HttpResponse:
    """Страница помощи - ролики, FAQ, инструкции."""
    return render(request, "ui/help.html")


def analytics_user(request: HttpRequest, user_id: int) -> HttpResponse:
    """
    Страница конкретного сотрудника (менеджера/РОП/директора).
    Страница не хранится в БД: существует пока существует пользователь.
    """
    viewer: User = request.user
    if not (viewer.is_superuser or viewer.role in (User.Role.ADMIN, User.Role.GROUP_MANAGER, User.Role.BRANCH_DIRECTOR, User.Role.SALES_HEAD)):
        messages.error(request, "Нет доступа к аналитике.")
        return redirect("dashboard")

    target = get_object_or_404(User.objects.select_related("branch"), id=user_id, is_active=True)
    # Админа не показываем как субъект
    if target.role == User.Role.ADMIN:
        raise Http404()

    if viewer.is_superuser or viewer.role in (User.Role.ADMIN, User.Role.GROUP_MANAGER):
        pass
    else:
        if not viewer.branch_id or viewer.branch_id != target.branch_id:
            messages.error(request, "Нет доступа к аналитике сотрудника из другого филиала.")
            return redirect("analytics")

    period = (request.GET.get("period") or "day").strip()  # day|month
    if period not in ("day", "month"):
        period = "day"
    now = timezone.now()
    local_now = timezone.localtime(now)
    if period == "month":
        start = local_now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        end = (start + timedelta(days=32)).replace(day=1)
        period_label = _month_label(timezone.localdate(now))
    else:
        start = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
        end = start + timedelta(days=1)
        period_label = timezone.localdate(now).strftime("%d.%m.%Y")

    # Все звонки — только те, что инициированы через кнопку "Позвонить с телефона" (note="UI click")
    calls_qs = (
        CallRequest.objects.filter(created_by=target, created_at__gte=start, created_at__lt=end, note="UI click")
        .exclude(status=CallRequest.Status.CANCELLED)
        .select_related("company", "contact")
        .order_by("-created_at")
    )

    # Холодные звонки (строгая логика):
    # - звонок инициирован через кнопку (note="UI click")
    # - у звонка is_cold_call=True
    # - и именно этот звонок был подтверждён отметкой (FK marked_call) в пределах 8 часов (это проверяется в момент отметки)
    cold_calls_qs = (
        calls_qs.filter(is_cold_call=True)
        .filter(Q(company__primary_cold_marked_call_id=F("id")) | Q(contact__cold_marked_call_id=F("id")))
        .order_by("-created_at")
    )

    # Пагинация с выбором per_page (как в company_list)
    per_page_param = request.GET.get("per_page", "").strip()
    if per_page_param:
        try:
            per_page = int(per_page_param)
            if per_page in [25, 50, 100, 200]:
                request.session["analytics_user_per_page"] = per_page
            else:
                per_page = request.session.get("analytics_user_per_page", 25)
        except (ValueError, TypeError):
            per_page = request.session.get("analytics_user_per_page", 25)
    else:
        per_page = request.session.get("analytics_user_per_page", 25)

    calls_p = Paginator(calls_qs, per_page)
    cold_p = Paginator(cold_calls_qs, per_page)
    events_qs = ActivityEvent.objects.filter(actor=target, created_at__gte=start, created_at__lt=end).order_by("-created_at")
    events_p = Paginator(events_qs, per_page)

    def _safe_int(v: str, default: int = 1) -> int:
        try:
            return max(int(v), 1)
        except Exception:
            return default

    calls_page_num = _safe_int((request.GET.get("calls_page") or "1"))
    cold_page_num = _safe_int((request.GET.get("cold_page") or "1"))
    events_page_num = _safe_int((request.GET.get("events_page") or "1"))

    calls_page = calls_p.get_page(calls_page_num)
    cold_page = cold_p.get_page(cold_page_num)
    events_page = events_p.get_page(events_page_num)

    # Добавляем форматированную длительность для каждого звонка
    for call in calls_page:
        if call.call_duration_seconds:
            minutes = call.call_duration_seconds // 60
            seconds = call.call_duration_seconds % 60
            call.duration_formatted = f"{minutes} мин. {seconds} сек." if minutes > 0 else f"{seconds} сек."
        else:
            call.duration_formatted = None
    
    # Также для холодных звонков
    for call in cold_page:
        if call.call_duration_seconds:
            minutes = call.call_duration_seconds // 60
            seconds = call.call_duration_seconds % 60
            call.duration_formatted = f"{minutes} мин. {seconds} сек." if minutes > 0 else f"{seconds} сек."
        else:
            call.duration_formatted = None

    # Формируем qs для пагинации, включая per_page если он отличается от значения по умолчанию
    calls_qs_str = _qs_without_page(request, page_key="calls_page")
    cold_qs = _qs_without_page(request, page_key="cold_page")
    events_qs_str = _qs_without_page(request, page_key="events_page")
    
    if per_page != 25:
        from urllib.parse import urlencode, parse_qs
        if calls_qs_str:
            params = parse_qs(calls_qs_str)
            params["per_page"] = [str(per_page)]
            calls_qs_str = urlencode(params, doseq=True)
        if cold_qs:
            params = parse_qs(cold_qs)
            params["per_page"] = [str(per_page)]
            cold_qs = urlencode(params, doseq=True)
        if events_qs_str:
            params = parse_qs(events_qs_str)
            params["per_page"] = [str(per_page)]
            events_qs_str = urlencode(params, doseq=True)

    return render(
        request,
        "ui/analytics_user.html",
        {
            "period": period,
            "period_label": period_label,
            "target": target,
            "calls_page": calls_page,
            "cold_page": cold_page,
            "events_page": events_page,
            "cold_qs": cold_qs,
            "calls_qs": calls_qs_str,
            "events_qs": events_qs_str,
            "cold_calls_count": cold_p.count,
            "calls_count": calls_p.count,
            "events_count": events_p.count,
            "per_page": per_page,
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

    # Строгая логика холодных звонков:
    # - инициированы через кнопку "Позвонить с телефона" (note="UI click")
    # - is_cold_call=True
    # - и подтверждены отметкой (marked_call) в допустимое окно (проверяется в момент отметки)
    qs_base = (
        CallRequest.objects.filter(created_by=user, created_at__gte=day_start, created_at__lt=day_end, note="UI click")
        .exclude(status=CallRequest.Status.CANCELLED)
        .select_related("company", "contact")
    )
    qs = qs_base.filter(is_cold_call=True).filter(
        Q(company__primary_cold_marked_call_id=F("id")) | Q(contact__cold_marked_call_id=F("id"))
    ).order_by("created_at")
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
            CallRequest.objects.filter(created_by=user, created_at__date__gte=ms, created_at__date__lt=me, note="UI click")
            .exclude(status=CallRequest.Status.CANCELLED)
            .filter(is_cold_call=True)
            .filter(Q(company__primary_cold_marked_call_id=F("id")) | Q(contact__cold_marked_call_id=F("id")))
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
    qs_base = (
        CallRequest.objects.filter(created_by=user, created_at__date__gte=selected, created_at__date__lt=month_end, note="UI click")
        .exclude(status=CallRequest.Status.CANCELLED)
        .select_related("company", "contact")
    )
    qs = qs_base.filter(is_cold_call=True).filter(
        Q(company__primary_cold_marked_call_id=F("id")) | Q(contact__cold_marked_call_id=F("id"))
    ).order_by("created_at")

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
    # По умолчанию фильтруем по текущему пользователю, если параметр responsible не указан
    default_responsible_id = None
    if "responsible" not in request.GET:
        default_responsible_id = user.id
    f = _apply_company_filters(qs=qs, params=request.GET, default_responsible_id=default_responsible_id)
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

    # Количество элементов на странице: из GET или из сессии (по умолчанию 25)
    per_page_param = request.GET.get("per_page", "").strip()
    if per_page_param:
        try:
            per_page = int(per_page_param)
            # Разрешенные значения: 25, 50, 100, 200
            if per_page in [25, 50, 100, 200]:
                request.session["company_list_per_page"] = per_page
            else:
                per_page = request.session.get("company_list_per_page", 25)
        except (ValueError, TypeError):
            per_page = request.session.get("company_list_per_page", 25)
    else:
        per_page = request.session.get("company_list_per_page", 25)

    paginator = Paginator(qs, per_page)
    page = paginator.get_page(request.GET.get("page"))
    # Формируем qs для пагинации, включая per_page если он отличается от значения по умолчанию
    qs_no_page = _qs_without_page(request)
    if per_page != 25:
        # Добавляем per_page в параметры, если он отличается от значения по умолчанию
        from urllib.parse import urlencode, parse_qs
        params = parse_qs(qs_no_page) if qs_no_page else {}
        params["per_page"] = [str(per_page)]
        qs_no_page = urlencode(params, doseq=True)
    ui_cfg = UiGlobalConfig.load()
    columns = ui_cfg.company_list_columns or ["name"]

    # Проверяем наличие компаний без ответственного
    has_companies_without_responsible = Company.objects.filter(responsible__isnull=True).exists()

    return render(
        request,
        "ui/company_list.html",
        {
            "page": page,
            "qs": qs_no_page,
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
            "per_page": per_page,
            "is_admin": require_admin(user),
            "has_companies_without_responsible": has_companies_without_responsible,
        },
    )


@login_required
@transaction.atomic
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
    # Транзакция обеспечивается декоратором @transaction.atomic на функции
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
    if not require_admin(user):
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
                "contract_type": (request.GET.get("contract_type") or "").strip(),
                "cold_call": (request.GET.get("cold_call") or "").strip(),
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
    # Загружаем компанию с связанными объектами, включая поля для истории холодных звонков
    company = get_object_or_404(
        Company.objects.select_related("responsible", "branch", "status", "head_company", "primary_cold_marked_by", "primary_cold_marked_call"),
        id=company_id
    )
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

    lead_state_req = (
        CompanyLeadStateRequest.objects.filter(company=company, status=CompanyLeadStateRequest.Status.PENDING)
        .select_related("requested_by", "decided_by")
        .order_by("-created_at")
        .first()
    )
    # Менеджер может самостоятельно переводить cold -> warm (для своих компаний)
    # Руководители/админы могут переводить cold -> warm
    can_set_warm = bool(
        (user.role == User.Role.MANAGER and company.responsible_id == user.id and company.lead_state == Company.LeadState.COLD) or
        (_can_decide_company_lead_state(user, company) and company.lead_state == Company.LeadState.COLD)
    )
    # Старая логика запросов больше не используется для менеджеров (оставляем для совместимости)
    can_request_lead_state = False
    # Право руководителя/админа менять состояние
    can_decide_lead_state = bool(_can_decide_company_lead_state(user, company))
    # Только администратор может вернуть warm -> cold
    can_revert_lead_state = bool(company.lead_state == Company.LeadState.WARM and not lead_state_req and _can_revert_company_lead_state(user))

    # "Организация" (головная карточка) и "филиалы" (дочерние карточки клиента)
    head = company.head_company or company
    org_head = Company.objects.select_related("responsible", "branch").filter(id=head.id).first()
    org_branches = (
        Company.objects.select_related("responsible", "branch")
        .filter(head_company_id=head.id)
        .order_by("name")[:200]
    )

    # Загружаем контакты с связанными объектами для истории холодных звонков
    contacts = (
        Contact.objects.filter(company=company)
        .select_related("cold_marked_by", "cold_marked_call")
        .prefetch_related(
            "emails",
            Prefetch(
                "phones",
                queryset=ContactPhone.objects.select_related("cold_marked_by", "cold_marked_call")
            )
        )
        .order_by("last_name", "first_name")[:200]
    )
    is_cold_company = bool(company.lead_state == Company.LeadState.COLD)

    # Новая логика: любой звонок может быть холодным, без ограничений по времени
    # Кнопка доступна только если контакт еще не отмечен как холодный
    for c in contacts:
        # Кнопка доступна только если контакт еще не отмечен
        c.cold_mark_available = not c.is_cold_call  # type: ignore[attr-defined]

    # Основной контакт (company.phone)
    # Кнопка доступна только если основной контакт еще не отмечен
    primary_cold_available = not company.primary_contact_is_cold_call
    
    # Проверка прав администратора для отката
    is_admin = require_admin(user)
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
    for t in tasks:
        t.can_edit_task = _can_edit_task_ui(user, t)  # type: ignore[attr-defined]
        t.can_delete_task = _can_delete_task_ui(user, t)  # type: ignore[attr-defined]

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
            "is_cold_company": is_cold_company,
            "primary_cold_available": primary_cold_available,
            "is_admin": is_admin,
            "notes": notes,
            "pinned_note": pinned_note,
            "note_form": note_form,
            "tasks": tasks,
            "activity": activity,
            "can_view_activity": can_view_activity,
            "can_delete_company": can_delete_company,
            "can_request_delete": can_request_delete,
            "delete_req": delete_req,
            "lead_state_req": lead_state_req,
            "can_request_lead_state": can_request_lead_state,
            "can_set_warm": can_set_warm,
            "can_decide_lead_state": can_decide_lead_state,
            "can_revert_lead_state": can_revert_lead_state,
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


def _dismiss_lead_state_req_notifications(*, req: CompanyLeadStateRequest, company: Company):
    """
    После решения запроса (approve/cancel) скрываем уведомление у всех руководителей,
    чтобы у второго оно не висело.
    """
    key = str(req.id)
    url = f"/companies/{company.id}/"
    try:
        Notification.objects.filter(kind=Notification.Kind.COMPANY, url=url, title__icontains=key, is_read=False).update(is_read=True)
    except Exception:
        pass


@login_required
def company_lead_state_request_create(request: HttpRequest, company_id) -> HttpResponse:
    if request.method != "POST":
        return redirect("company_detail", company_id=company_id)
    user: User = request.user
    company = get_object_or_404(Company.objects.select_related("responsible", "branch"), id=company_id)
    if not (user.role == User.Role.MANAGER and company.responsible_id == user.id):
        messages.error(request, "Запросить смену состояния может только ответственный менеджер.")
        return redirect("company_detail", company_id=company.id)

    requested_state = (request.POST.get("requested_state") or "").strip()
    # Разрешаем только одно направление: cold -> warm
    if company.lead_state != Company.LeadState.COLD:
        messages.error(request, "Запрос на смену состояния доступен только для «Холодный контакт» → «Теплый контакт».")
        return redirect("company_detail", company_id=company.id)
    if requested_state != Company.LeadState.WARM:
        messages.error(request, "Можно запросить только перевод в «Теплый контакт».")
        return redirect("company_detail", company_id=company.id)
    if requested_state not in (Company.LeadState.WARM,):
        messages.error(request, "Некорректное состояние.")
        return redirect("company_detail", company_id=company.id)

    existing = CompanyLeadStateRequest.objects.filter(company=company, status=CompanyLeadStateRequest.Status.PENDING).first()
    if existing:
        messages.info(request, "Запрос уже отправлен и ожидает решения.")
        return redirect("company_detail", company_id=company.id)

    note = (request.POST.get("note") or "").strip()
    req = CompanyLeadStateRequest.objects.create(
        company=company,
        requested_by=user,
        requested_state=requested_state,
        note=note,
        status=CompanyLeadStateRequest.Status.PENDING,
    )
    branch_id = _company_branch_id(company)
    title = f"Запрос смены состояния ({req.id})"
    state_label = "Теплый контакт"
    body = f"{company.name}: запрос на смену состояния → {state_label}. {(note[:160] + '…') if len(note) > 160 else note}"
    sent = _notify_branch_leads(branch_id=branch_id, title=title, body=body, url=f"/companies/{company.id}/", exclude_user_id=user.id)
    messages.success(request, f"Запрос отправлен. Уведомлено руководителей: {sent}.")
    log_event(
        actor=user,
        verb=ActivityEvent.Verb.CREATE,
        entity_type="company_lead_state_request",
        entity_id=str(req.id),
        company_id=company.id,
        message="Запрос смены состояния карточки",
        meta={"requested_state": requested_state, "note": note[:500], "notified": sent},
    )
    return redirect("company_detail", company_id=company.id)


@login_required
def company_lead_state_request_cancel(request: HttpRequest, company_id, req_id) -> HttpResponse:
    if request.method != "POST":
        return redirect("company_detail", company_id=company_id)
    user: User = request.user
    company = get_object_or_404(Company.objects.select_related("responsible", "branch"), id=company_id)
    if not _can_decide_company_lead_state(user, company):
        messages.error(request, "Нет прав на обработку запроса смены состояния по этой компании.")
        return redirect("company_detail", company_id=company.id)
    req = get_object_or_404(CompanyLeadStateRequest.objects.select_related("requested_by"), id=req_id, company=company)
    if req.status != CompanyLeadStateRequest.Status.PENDING:
        messages.info(request, "Запрос уже обработан.")
        return redirect("company_detail", company_id=company.id)
    decision_note = (request.POST.get("decision_note") or "").strip()
    if not decision_note:
        messages.error(request, "Укажите причину отклонения.")
        return redirect("company_detail", company_id=company.id)
    req.status = CompanyLeadStateRequest.Status.CANCELLED
    req.decided_by = user
    req.decision_note = decision_note
    req.decided_at = timezone.now()
    req.save(update_fields=["status", "decided_by", "decision_note", "decided_at"])
    _dismiss_lead_state_req_notifications(req=req, company=company)

    if req.requested_by_id:
        notify(
            user=req.requested_by,
            kind=Notification.Kind.COMPANY,
            title="Смена состояния: отклонено",
            body=f"{company.name}: {decision_note}",
            url=f"/companies/{company.id}/",
        )
    messages.success(request, "Запрос отклонён. Менеджер уведомлён.")
    log_event(
        actor=user,
        verb=ActivityEvent.Verb.UPDATE,
        entity_type="company_lead_state_request",
        entity_id=str(req.id),
        company_id=company.id,
        message="Отклонён запрос смены состояния карточки",
        meta={"decision_note": decision_note[:500]},
    )
    return redirect("company_detail", company_id=company.id)


@login_required
def company_lead_state_request_approve(request: HttpRequest, company_id, req_id) -> HttpResponse:
    if request.method != "POST":
        return redirect("company_detail", company_id=company_id)
    user: User = request.user
    company = get_object_or_404(Company.objects.select_related("responsible", "branch"), id=company_id)
    if not _can_decide_company_lead_state(user, company):
        messages.error(request, "Нет прав на смену состояния по этой компании.")
        return redirect("company_detail", company_id=company.id)
    req = get_object_or_404(CompanyLeadStateRequest.objects.select_related("requested_by"), id=req_id, company=company)
    if req.status != CompanyLeadStateRequest.Status.PENDING:
        messages.info(request, "Запрос уже обработан.")
        return redirect("company_detail", company_id=company.id)
    if req.requested_state != Company.LeadState.WARM:
        messages.error(request, "Этот запрос нельзя подтвердить: разрешён только перевод в «Теплый контакт».")
        return redirect("company_detail", company_id=company.id)
    req.status = CompanyLeadStateRequest.Status.APPROVED
    req.decided_by = user
    req.decided_at = timezone.now()
    req.save(update_fields=["status", "decided_by", "decided_at"])

    # Применяем состояние к компании
    company.lead_state = Company.LeadState.WARM
    company.save(update_fields=["lead_state", "updated_at"])
    _apply_company_become_warm(company=company)
    # Обновляем объект в памяти, чтобы поля cold_marked_* остались доступными
    company.refresh_from_db()
    _dismiss_lead_state_req_notifications(req=req, company=company)

    if req.requested_by_id:
        state_label = "Теплый контакт" if req.requested_state == Company.LeadState.WARM else "Холодный контакт"
        notify(
            user=req.requested_by,
            kind=Notification.Kind.COMPANY,
            title="Смена состояния: подтверждено",
            body=f"{company.name}: состояние изменено → {state_label}",
            url=f"/companies/{company.id}/",
        )
    messages.success(request, "Состояние карточки изменено.")
    log_event(
        actor=user,
        verb=ActivityEvent.Verb.UPDATE,
        entity_type="company",
        entity_id=str(company.id),
        company_id=company.id,
        message="Изменено состояние карточки",
        meta={"requested_state": req.requested_state, "request_id": str(req.id)},
    )
    return redirect("company_detail", company_id=company.id)


@login_required
def company_lead_state_set(request: HttpRequest, company_id) -> HttpResponse:
    """
    Прямое изменение состояния карточки.
    - Менеджеры могут переводить cold -> warm (для своих компаний)
    - Руководители/админы могут переводить cold -> warm
    - Только администратор может переводить warm -> cold
    """
    if request.method != "POST":
        return redirect("company_detail", company_id=company_id)
    user: User = request.user
    company = get_object_or_404(Company.objects.select_related("responsible", "branch"), id=company_id)

    # Если есть активный запрос — сначала его обработайте, чтобы не было рассинхрона.
    pending = CompanyLeadStateRequest.objects.filter(company=company, status=CompanyLeadStateRequest.Status.PENDING).first()
    if pending:
        messages.info(request, "По компании уже есть запрос смены состояния — сначала обработайте его.")
        return redirect("company_detail", company_id=company.id)

    requested_state = (request.POST.get("lead_state") or "").strip()
    if requested_state not in (Company.LeadState.COLD, Company.LeadState.WARM):
        messages.error(request, "Некорректное состояние.")
        return redirect("company_detail", company_id=company.id)

    if requested_state == Company.LeadState.WARM:
        # Перевод cold -> warm: разрешен менеджерам (для своих компаний) и руководителям/админам
        if company.lead_state != Company.LeadState.COLD:
            messages.info(request, "Перевести в «Теплый контакт» можно только из «Холодный контакт».")
            return redirect("company_detail", company_id=company.id)
        
        # Проверяем права: менеджер может только для своих компаний, руководители/админы - для любых
        can_change = False
        if user.role == User.Role.MANAGER and company.responsible_id == user.id:
            can_change = True
        elif _can_decide_company_lead_state(user, company):
            can_change = True
        
        if not can_change:
            messages.error(request, "Нет прав на изменение состояния по этой компании.")
            return redirect("company_detail", company_id=company.id)
        
        company.lead_state = Company.LeadState.WARM
        company.save(update_fields=["lead_state", "updated_at"])
        _apply_company_become_warm(company=company)
        # Обновляем объект в памяти, чтобы поля cold_marked_* остались доступными
        company.refresh_from_db()
        messages.success(request, "Состояние изменено: «Теплый контакт».")
    else:
        # Перевод warm -> cold: только администратор
        if not _can_revert_company_lead_state(user):
            messages.error(request, "Вернуть в «Холодный контакт» может только администратор.")
            return redirect("company_detail", company_id=company.id)
        if company.lead_state != Company.LeadState.WARM:
            messages.info(request, "Состояние уже «Холодный контакт».")
            return redirect("company_detail", company_id=company.id)
        company.lead_state = Company.LeadState.COLD
        company.save(update_fields=["lead_state", "updated_at"])
        messages.success(request, "Состояние изменено: «Холодный контакт».")

    log_event(
        actor=user,
        verb=ActivityEvent.Verb.UPDATE,
        entity_type="company",
        entity_id=str(company.id),
        company_id=company.id,
        message="Изменено состояние карточки (напрямую)",
        meta={"lead_state": requested_state},
    )
    return redirect("company_detail", company_id=company.id)


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
    """
    Отметить основной контакт компании как холодный звонок.
    Любой звонок может быть холодным (независимо от lead_state компании).
    Отметку можно поставить только один раз.
    """
    if request.method != "POST":
        return redirect("company_detail", company_id=company_id)

    user: User = request.user
    company = get_object_or_404(Company.objects.select_related("responsible", "branch", "primary_cold_marked_by"), id=company_id)
    if not _can_edit_company(user, company):
        messages.error(request, "Нет прав на изменение признака 'Холодный звонок'.")
        return redirect("company_detail", company_id=company.id)

    # Проверка подтверждения
    confirmed = request.POST.get("confirmed") == "1"
    if not confirmed:
        messages.error(request, "Требуется подтверждение действия.")
        return redirect("company_detail", company_id=company.id)

    # Проверка: уже отмечен?
    if company.primary_contact_is_cold_call:
        messages.info(request, "Основной контакт уже отмечен как холодный.")
        return redirect("company_detail", company_id=company.id)

    phone = (company.phone or "").strip()
    if not phone:
        messages.error(request, "У компании не задан основной телефон.")
        return redirect("company_detail", company_id=company.id)

    # Ищем последний звонок по основному номеру (без ограничения по времени)
    normalized = phone.replace(" ", "").replace("-", "").replace("(", "").replace(")", "")
    now = timezone.now()
    last_call = (
        CallRequest.objects.filter(created_by=user, company=company, contact__isnull=True, phone_raw=normalized)
        .order_by("-created_at")
        .first()
    )
    if not last_call:
        messages.error(request, "Не найден звонок по основному номеру.")
        return redirect("company_detail", company_id=company.id)

    # Отмечаем как холодный
    company.primary_contact_is_cold_call = True
    company.primary_cold_marked_at = now
    company.primary_cold_marked_by = user
    company.primary_cold_marked_call = last_call
    company.save(update_fields=["primary_contact_is_cold_call", "primary_cold_marked_at", "primary_cold_marked_by", "primary_cold_marked_call", "updated_at"])

    if not last_call.is_cold_call:
        last_call.is_cold_call = True
        last_call.save(update_fields=["is_cold_call"])

    messages.success(request, "Отмечено: холодный звонок (основной контакт).")
    log_event(
        actor=user,
        verb=ActivityEvent.Verb.UPDATE,
        entity_type="company",
        entity_id=company.id,
        company_id=company.id,
        message="Отмечено: холодный звонок (осн. контакт)",
        meta={"call_id": str(last_call.id)},
    )
    return redirect("company_detail", company_id=company.id)


@login_required
def contact_cold_call_toggle(request: HttpRequest, contact_id) -> HttpResponse:
    """
    Отметить контакт как холодный звонок.
    Любой звонок может быть холодным (независимо от lead_state компании).
    Отметку можно поставить только один раз.
    """
    if request.method != "POST":
        return redirect("dashboard")
    user: User = request.user
    contact = get_object_or_404(Contact.objects.select_related("company", "cold_marked_by"), id=contact_id)
    company = contact.company
    if not company:
        messages.error(request, "Контакт не привязан к компании.")
        return redirect("dashboard")
    if not _can_edit_company(user, company):
        messages.error(request, "Нет прав на изменение контактов этой компании.")
        return redirect("company_detail", company_id=company.id)

    # Проверка подтверждения
    confirmed = request.POST.get("confirmed") == "1"
    if not confirmed:
        messages.error(request, "Требуется подтверждение действия.")
        return redirect("company_detail", company_id=company.id)

    # Проверка: уже отмечен?
    if contact.is_cold_call:
        messages.info(request, "Контакт уже отмечен как холодный.")
        return redirect("company_detail", company_id=company.id)

    # Ищем последний звонок по этому контакту (без ограничения по времени)
    now = timezone.now()
    last_call = (
        CallRequest.objects.filter(created_by=user, contact=contact)
        .order_by("-created_at")
        .first()
    )
    if not last_call:
        messages.error(request, "Не найден звонок по этому контакту.")
        return redirect("company_detail", company_id=company.id)

    # Отмечаем как холодный
    contact.is_cold_call = True
    contact.cold_marked_at = now
    contact.cold_marked_by = user
    contact.cold_marked_call = last_call
    contact.save(update_fields=["is_cold_call", "cold_marked_at", "cold_marked_by", "cold_marked_call", "updated_at"])

    if not last_call.is_cold_call:
        last_call.is_cold_call = True
        last_call.save(update_fields=["is_cold_call"])

    messages.success(request, "Отмечено: холодный звонок (контакт).")
    log_event(
        actor=user,
        verb=ActivityEvent.Verb.UPDATE,
        entity_type="contact",
        entity_id=str(contact.id),
        company_id=company.id,
        message="Отмечено: холодный звонок (контакт)",
        meta={"contact_id": str(contact.id), "call_id": str(last_call.id)},
    )
    return redirect("company_detail", company_id=company.id)


@login_required
def company_cold_call_reset(request: HttpRequest, company_id) -> HttpResponse:
    """
    Откатить отметку холодного звонка для основного контакта компании.
    Доступно только администраторам.
    """
    if request.method != "POST":
        return redirect("company_detail", company_id=company_id)

    user: User = request.user
    if not require_admin(user):
        messages.error(request, "Только администратор может откатить отметку холодного звонка.")
        return redirect("company_detail", company_id=company_id)

    company = get_object_or_404(Company.objects.select_related("responsible", "branch"), id=company_id)
    
    if not company.primary_contact_is_cold_call:
        messages.info(request, "Основной контакт не отмечен как холодный.")
        return redirect("company_detail", company_id=company.id)

    # Откатываем отметку
    company.primary_contact_is_cold_call = False
    company.save(update_fields=["primary_contact_is_cold_call", "updated_at"])
    # НЕ удаляем поля primary_cold_marked_at, primary_cold_marked_by, primary_cold_marked_call для истории

    messages.success(request, "Отметка холодного звонка отменена (основной контакт).")
    log_event(
        actor=user,
        verb=ActivityEvent.Verb.UPDATE,
        entity_type="company",
        entity_id=company.id,
        company_id=company.id,
        message="Откат: холодный звонок (осн. контакт)",
    )
    return redirect("company_detail", company_id=company.id)


@login_required
def contact_cold_call_reset(request: HttpRequest, contact_id) -> HttpResponse:
    """
    Откатить отметку холодного звонка для контакта.
    Доступно только администраторам.
    """
    if request.method != "POST":
        return redirect("dashboard")

    user: User = request.user
    if not require_admin(user):
        messages.error(request, "Только администратор может откатить отметку холодного звонка.")
        return redirect("dashboard")

    contact = get_object_or_404(Contact.objects.select_related("company"), id=contact_id)
    company = contact.company
    if not company:
        messages.error(request, "Контакт не привязан к компании.")
        return redirect("dashboard")

    if not contact.is_cold_call:
        messages.info(request, "Контакт не отмечен как холодный.")
        return redirect("company_detail", company_id=company.id)

    # Откатываем отметку
    contact.is_cold_call = False
    contact.save(update_fields=["is_cold_call"])
    # НЕ удаляем поля cold_marked_at, cold_marked_by, cold_marked_call для истории

    messages.success(request, "Отметка холодного звонка отменена (контакт).")
    log_event(
        actor=user,
        verb=ActivityEvent.Verb.UPDATE,
        entity_type="contact",
        entity_id=str(contact.id),
        company_id=company.id,
        message="Откат: холодный звонок (контакт)",
    )
    return redirect("company_detail", company_id=company.id)


@login_required
def contact_phone_cold_call_toggle(request: HttpRequest, contact_phone_id) -> HttpResponse:
    """
    Отметить конкретный номер телефона контакта как холодный звонок.
    Любой звонок может быть холодным (независимо от lead_state компании).
    Отметку можно поставить только один раз.
    """
    if request.method != "POST":
        return redirect("dashboard")
    user: User = request.user
    import logging
    logger = logging.getLogger(__name__)
    try:
        contact_phone = get_object_or_404(ContactPhone.objects.select_related("contact__company", "cold_marked_by"), id=contact_phone_id)
    except Exception as e:
        logger.error(f"Error finding ContactPhone {contact_phone_id}: {e}", exc_info=True)
        messages.error(request, f"Ошибка: номер телефона не найден.")
        return redirect("dashboard")
    contact = contact_phone.contact
    company = contact.company if contact else None
    if not company:
        messages.error(request, "Контакт не привязан к компании.")
        return redirect("dashboard")
    if not _can_edit_company(user, company):
        messages.error(request, "Нет прав на изменение контактов этой компании.")
        return redirect("company_detail", company_id=company.id)

    # Проверка подтверждения
    confirmed = request.POST.get("confirmed") == "1"
    if not confirmed:
        messages.error(request, "Требуется подтверждение действия.")
        return redirect("company_detail", company_id=company.id)

    # Проверка: уже отмечен?
    if contact_phone.is_cold_call:
        messages.info(request, "Этот номер уже отмечен как холодный.")
        return redirect("company_detail", company_id=company.id)

    # Ищем последний звонок по этому номеру телефона
    now = timezone.now()
    # Нормализуем номер телефона так же, как в phone_call_create
    raw = contact_phone.value.replace(" ", "").replace("-", "").replace("(", "").replace(")", "")
    normalized_phone = "".join(ch for i, ch in enumerate(raw) if ch.isdigit() or (ch == "+" and i == 0))
    digits = normalized_phone[1:] if normalized_phone.startswith("+") else normalized_phone
    if digits.startswith("8") and len(digits) == 11:
        normalized_phone = "+7" + digits[1:]
    elif digits.startswith("7") and len(digits) == 11:
        normalized_phone = "+7" + digits[1:]
    elif len(digits) == 10:
        normalized_phone = "+7" + digits
    elif normalized_phone.startswith("8") and len(normalized_phone) == 11:
        normalized_phone = "+7" + normalized_phone[1:]
    elif normalized_phone.startswith("7") and len(normalized_phone) == 11:
        normalized_phone = "+7" + normalized_phone[1:]
    elif normalized_phone and not normalized_phone.startswith("+") and len(normalized_phone) >= 11 and normalized_phone[0] in ("7", "8"):
        normalized_phone = "+7" + normalized_phone[1:]
    
    last_call = (
        CallRequest.objects.filter(created_by=user, contact=contact, phone_raw=normalized_phone)
        .order_by("-created_at")
        .first()
    )
    if not last_call:
        messages.error(request, "Не найден звонок по этому номеру телефона.")
        return redirect("company_detail", company_id=company.id)

    # Отмечаем как холодный
    contact_phone.is_cold_call = True
    contact_phone.cold_marked_at = now
    contact_phone.cold_marked_by = user
    contact_phone.cold_marked_call = last_call
    contact_phone.save(update_fields=["is_cold_call", "cold_marked_at", "cold_marked_by", "cold_marked_call"])

    if not last_call.is_cold_call:
        last_call.is_cold_call = True
        last_call.save(update_fields=["is_cold_call"])

    messages.success(request, f"Отмечено: холодный звонок (номер {contact_phone.value}).")
    log_event(
        actor=user,
        verb=ActivityEvent.Verb.UPDATE,
        entity_type="contact_phone",
        entity_id=str(contact_phone.id),
        company_id=company.id,
        message=f"Отмечено: холодный звонок (номер {contact_phone.value})",
        meta={"contact_phone_id": str(contact_phone.id), "call_id": str(last_call.id)},
    )
    return redirect("company_detail", company_id=company.id)


@login_required
def contact_phone_cold_call_reset(request: HttpRequest, contact_phone_id) -> HttpResponse:
    """
    Откатить отметку холодного звонка для конкретного номера телефона контакта.
    Доступно только администраторам.
    """
    if request.method != "POST":
        return redirect("dashboard")

    user: User = request.user
    if not require_admin(user):
        messages.error(request, "Только администратор может откатить отметку холодного звонка.")
        return redirect("dashboard")

    contact_phone = get_object_or_404(ContactPhone.objects.select_related("contact__company"), id=contact_phone_id)
    contact = contact_phone.contact
    company = contact.company if contact else None
    if not company:
        messages.error(request, "Контакт не привязан к компании.")
        return redirect("dashboard")

    if not contact_phone.is_cold_call and not contact_phone.cold_marked_at:
        messages.info(request, "Этот номер не отмечен как холодный.")
        return redirect("company_detail", company_id=company.id)

    # Откатываем отметку (но сохраняем историю)
    contact_phone.is_cold_call = False
    contact_phone.save(update_fields=["is_cold_call"])
    # НЕ удаляем поля cold_marked_at, cold_marked_by, cold_marked_call для истории

    messages.success(request, f"Отметка холодного звонка отменена (номер {contact_phone.value}).")
    log_event(
        actor=user,
        verb=ActivityEvent.Verb.UPDATE,
        entity_type="contact_phone",
        entity_id=str(contact_phone.id),
        company_id=company.id,
        message=f"Откат: холодный звонок (номер {contact_phone.value})",
    )
    return redirect("company_detail", company_id=company.id)
    contact.is_cold_call = False
    contact.save(update_fields=["is_cold_call", "updated_at"])
    # НЕ удаляем поля cold_marked_at, cold_marked_by, cold_marked_call для истории

    messages.success(request, "Отметка холодного звонка отменена (контакт).")
    log_event(
        actor=user,
        verb=ActivityEvent.Verb.UPDATE,
        entity_type="contact",
        entity_id=str(contact.id),
        company_id=company.id,
        message="Откат: холодный звонок (контакт)",
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
def contact_delete(request: HttpRequest, contact_id) -> HttpResponse:
    """
    Удалить контакт компании.
    Доступно только ответственному за карточку.
    """
    if request.method != "POST":
        return redirect("dashboard")
    
    user: User = request.user
    contact = get_object_or_404(Contact.objects.select_related("company", "company__responsible"), id=contact_id)
    company = contact.company
    if not company:
        messages.error(request, "Контакт не привязан к компании.")
        return redirect("company_list")
    
    if not _can_edit_company(user, company):
        messages.error(request, "Нет прав на удаление контактов этой компании.")
        return redirect("company_detail", company_id=company.id)
    
    contact_name = str(contact)
    contact.delete()
    
    messages.success(request, f"Контакт '{contact_name}' удалён.")
    log_event(
        actor=user,
        verb=ActivityEvent.Verb.DELETE,
        entity_type="contact",
        entity_id=str(contact_id),
        company_id=company.id,
        message=f"Удалён контакт: {contact_name}",
    )
    return redirect("company_detail", company_id=company.id)


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

    # Нормализация номера телефона к формату +7XXXXXXXXXX
    # Убираем все пробелы, дефисы, скобки
    raw = phone.replace(" ", "").replace("-", "").replace("(", "").replace(")", "")
    
    # Если номер уже в правильном формате +7XXXXXXXXXX (12 символов), оставляем как есть
    if raw.startswith("+7") and len(raw) == 12 and raw[2:].isdigit():
        normalized = raw
    else:
        # Извлекаем только цифры
        digits = "".join(ch for ch in raw if ch.isdigit())
        
        # Приводим к формату +7XXXXXXXXXX для российских номеров
        if digits.startswith("8") and len(digits) == 11:
            # 8XXXXXXXXXX => +7XXXXXXXXXX
            normalized = "+7" + digits[1:]
        elif digits.startswith("7") and len(digits) == 11:
            # 7XXXXXXXXXX => +7XXXXXXXXXX
            normalized = "+7" + digits[1:]
        elif len(digits) == 10:
            # XXXXXXXXXX => +7XXXXXXXXXX
            normalized = "+7" + digits
        elif digits.startswith("8") and len(digits) > 11:
            # 8XXXXXXXXXX... => +7XXXXXXXXXX (берем первые 11 цифр)
            normalized = "+7" + digits[1:11]
        elif digits.startswith("7") and len(digits) > 11:
            # 7XXXXXXXXXX... => +7XXXXXXXXXX (берем первые 11 цифр)
            normalized = "+7" + digits[1:11]
        elif len(digits) >= 10:
            # Берем последние 10 цифр
            normalized = "+7" + digits[-10:]
        else:
            # Если ничего не подошло, возвращаем как есть (но это ошибка)
            normalized = raw

    # Дедупликация на сервере: если пользователь несколько раз подряд нажимает "позвонить" на тот же номер/контакт,
    # не создаём новые записи (иначе отчёты раздуваются).
    # НО: если предыдущий запрос уже был получен телефоном (CONSUMED), создаём новый, чтобы можно было позвонить повторно.
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
    # Если есть предыдущий запрос И он еще не был получен телефоном (PENDING) - возвращаем его
    # Если он уже CONSUMED - создаём новый, чтобы можно было позвонить повторно
    if prev_call and prev_call.status == CallRequest.Status.PENDING:
        return JsonResponse({"ok": True, "id": str(prev_call.id), "phone": normalized, "dedup": True})

    call = CallRequest.objects.create(
        user=user,
        created_by=user,
        company_id=company_id or None,
        contact_id=contact_id or None,
        phone_raw=normalized,
        note="UI click",
    )
    
    # Логируем для отладки
    import logging
    logger = logging.getLogger(__name__)
    logger.info(f"phone_call_create: created CallRequest {call.id} for user {user.id}, phone {normalized}, device check: {PhoneDevice.objects.filter(user=user).exists()}")
    
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

    # Базовая видимость задач в зависимости от роли:
    # - Админ / управляющий: все задачи
    # - Директор филиала / РОП: задачи своего филиала
    # - Остальные: фильтрация дальше по assigned_to (см. mine)
    if user.role in (User.Role.BRANCH_DIRECTOR, User.Role.SALES_HEAD) and user.branch_id:
        qs = qs.filter(
            Q(company__branch_id=user.branch_id)
            | Q(assigned_to__branch_id=user.branch_id)
        )

    qs = qs.distinct()

    status = (request.GET.get("status") or "").strip()
    show_done = (request.GET.get("show_done") or "").strip()
    if status:
        qs = qs.filter(status=status)
    else:
        # По умолчанию не показываем выполненные задачи, чтобы они не мешали в списке.
        if show_done != "1":
            qs = qs.exclude(status=Task.Status.DONE)

    mine = (request.GET.get("mine") or "").strip()
    # Логика mine:
    # - Если mine=1: только мои
    # - Если mine=0: не фильтруем по ответственному
    # - Если параметра нет:
    #     * админ/управляющий: показываем все (без mine)
    #     * директор/РОП: показываем все своего филиала (фильтр филиала уже выше)
    #     * остальные: по умолчанию только свои
    if mine == "1":
        qs = qs.filter(assigned_to=user)
    elif mine == "0":
        pass
    else:
        if user.role in (User.Role.ADMIN, User.Role.GROUP_MANAGER):
            pass  # без фильтра
        elif user.role in (User.Role.BRANCH_DIRECTOR, User.Role.SALES_HEAD):
            pass  # без фильтра, но уже ограничено филиалом выше
        else:
            qs = qs.filter(assigned_to=user)

    overdue = (request.GET.get("overdue") or "").strip()
    if overdue == "1":
        qs = qs.filter(due_at__lt=now).exclude(status__in=[Task.Status.DONE, Task.Status.CANCELLED])

    today = (request.GET.get("today") or "").strip()
    if today == "1":
        qs = qs.filter(due_at__gte=today_start, due_at__lt=tomorrow_start).exclude(status__in=[Task.Status.DONE, Task.Status.CANCELLED])

    # Сортировка
    sort = (request.GET.get("sort") or "").strip()
    if sort == "due_asc":
        qs = qs.order_by(F("due_at").asc(nulls_last=True), "-created_at")
    elif sort == "due_desc":
        qs = qs.order_by(F("due_at").desc(nulls_last=True), "-created_at")
    elif sort == "status":
        qs = qs.order_by("status", "-created_at")
    elif sort == "company":
        qs = qs.order_by("company__name", "-created_at")
    elif sort == "assignee":
        qs = qs.order_by("assigned_to__last_name", "assigned_to__first_name", "-created_at")
    else:
        qs = qs.order_by("-created_at")

    # Пагинация с выбором per_page (как в company_list)
    per_page_param = request.GET.get("per_page", "").strip()
    if per_page_param:
        try:
            per_page = int(per_page_param)
            if per_page in [25, 50, 100, 200]:
                request.session["task_list_per_page"] = per_page
            else:
                per_page = request.session.get("task_list_per_page", 25)
        except (ValueError, TypeError):
            per_page = request.session.get("task_list_per_page", 25)
    else:
        per_page = request.session.get("task_list_per_page", 25)

    paginator = Paginator(qs, per_page)
    page = paginator.get_page(request.GET.get("page"))
    qs_no_page = _qs_without_page(request)
    if per_page != 25:
        from urllib.parse import urlencode, parse_qs
        params = parse_qs(qs_no_page) if qs_no_page else {}
        params["per_page"] = [str(per_page)]
        qs_no_page = urlencode(params, doseq=True)

    # Для шаблона: не делаем сложные выражения в {% if %}, чтобы не ловить TemplateSyntaxError.
    # Проставим флаг прямо в объекты текущей страницы.
    for t in page.object_list:
        t.can_manage_status = _can_manage_task_status_ui(user, t)  # type: ignore[attr-defined]
        t.can_edit_task = _can_edit_task_ui(user, t)  # type: ignore[attr-defined]
        t.can_delete_task = _can_delete_task_ui(user, t)  # type: ignore[attr-defined]

    is_admin = require_admin(user)
    transfer_targets = []
    if is_admin:
        transfer_targets = User.objects.filter(
            is_active=True, role__in=[User.Role.MANAGER, User.Role.SALES_HEAD, User.Role.BRANCH_DIRECTOR]
        ).order_by("last_name", "first_name")

    # Обработка параметра view_task для модального окна просмотра выполненной задачи
    view_task_id = (request.GET.get("view_task") or "").strip()
    view_task = None
    view_task_overdue_days = None
    if view_task_id:
        try:
            # Показываем модалку для задачи в любом статусе.
            view_task = Task.objects.select_related("company", "assigned_to", "created_by", "type").filter(
                id=view_task_id
            ).first()
            if view_task:
                # Проверяем права на просмотр
                can_view = False
                if user.role in (User.Role.ADMIN, User.Role.GROUP_MANAGER):
                    can_view = True
                elif user.role in (User.Role.BRANCH_DIRECTOR, User.Role.SALES_HEAD) and user.branch_id:
                    if view_task.company_id and getattr(view_task.company, "branch_id", None) == user.branch_id:
                        can_view = True
                    elif view_task.assigned_to_id and getattr(view_task.assigned_to, "branch_id", None) == user.branch_id:
                        can_view = True
                elif view_task.assigned_to_id == user.id or view_task.created_by_id == user.id:
                    can_view = True
                if not can_view:
                    view_task = None
                else:
                    # Вычисляем просрочку в днях (только если известны дедлайн и время завершения)
                    if view_task.due_at and view_task.completed_at and view_task.completed_at > view_task.due_at:
                        delta = view_task.completed_at - view_task.due_at
                        view_task_overdue_days = delta.days
                    # Добавляем флаг для прав на редактирование
                    view_task.can_edit_task = _can_edit_task_ui(user, view_task)  # type: ignore[attr-defined]
        except (ValueError, TypeError):
            pass

    return render(
        request,
        "ui/task_list.html",
        {
            "now": now,
            "local_now": local_now,
            "page": page,
            "qs": qs_no_page,
            "status": status,
            "show_done": show_done,
            "mine": mine,
            "overdue": overdue,
            "today": today,
            "sort": sort,
            "per_page": per_page,
            "is_admin": is_admin,
            "transfer_targets": transfer_targets,
            "view_task": view_task,
            "view_task_overdue_days": view_task_overdue_days,
        },
    )


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
                    if comp:
                        return redirect("company_detail", company_id=comp.id)
                    return redirect("task_create")

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
                    # Ведём сразу на список задач с модальным окном конкретной задачи.
                    url=f"/tasks/?view_task={task.id}",
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


def _can_edit_task_ui(user: User, task: Task) -> bool:
    """
    Право на редактирование задачи:
    - Создатель всегда может редактировать свою задачу
    - Администратор / управляющий — любые задачи
    - Ответственный за карточку компании (company.responsible)
    - Директор филиала / РОП — задачи своего филиала
    """
    # Создатель всегда может редактировать свою задачу
    if task.created_by_id and task.created_by_id == user.id:
        return True
    # Админ/управляющий — любые задачи
    if user.role in (User.Role.ADMIN, User.Role.GROUP_MANAGER):
        return True
    # Ответственный за компанию
    if task.company_id:
        try:
            company = getattr(task, "company", None)
            if company and company.responsible_id == user.id:
                return True
        except Exception:
            pass
    # РОП/директор — задачи своего филиала
    if user.role in (User.Role.SALES_HEAD, User.Role.BRANCH_DIRECTOR) and user.branch_id and task.company_id:
        try:
            if getattr(task.company, "branch_id", None) == user.branch_id:
                return True
        except Exception:
            pass
    return False


def _can_delete_task_ui(user: User, task: Task) -> bool:
    """
    Право на удаление задачи:
    - Администратор / управляющий — любые задачи;
    - Ответственный за карточку компании (company.responsible);
    - Директор филиала / РОП — задачи своего филиала.
    """
    if not user or not user.is_authenticated or not user.is_active:
        return False
    if user.is_superuser or user.role in (User.Role.ADMIN, User.Role.GROUP_MANAGER):
        return True
    # Ответственный за компанию
    if task.company_id and getattr(task, "company", None):
        try:
            if getattr(task.company, "responsible_id", None) == user.id:
                return True
        except Exception:
            pass
    # Директор филиала / РОП — внутри своего филиала
    if user.role in (User.Role.BRANCH_DIRECTOR, User.Role.SALES_HEAD) and user.branch_id:
        branch_id = None
        if task.company_id and getattr(task, "company", None):
            branch_id = getattr(task.company, "branch_id", None)
        if not branch_id and getattr(task, "assigned_to", None):
            branch_id = getattr(task.assigned_to, "branch_id", None)
        if branch_id and branch_id == user.branch_id:
            return True
    return False


@login_required
def task_delete(request: HttpRequest, task_id) -> HttpResponse:
    user: User = request.user
    task = get_object_or_404(Task.objects.select_related("company", "assigned_to", "created_by", "type"), id=task_id)
    if not _can_delete_task_ui(user, task):
        messages.error(request, "Нет прав на удаление этой задачи.")
        return redirect("task_list")

    if request.method == "POST":
        title = task.title
        company_id = task.company_id
        task.delete()
        messages.success(request, f"Задача «{title}» удалена.")
        log_event(
            actor=user,
            verb=ActivityEvent.Verb.DELETE,
            entity_type="task",
            entity_id=str(task_id),
            company_id=company_id,
            message=f"Удалена задача: {title}",
        )
        return redirect("task_list")

    return redirect("task_list")


@login_required
def task_bulk_reassign(request: HttpRequest) -> HttpResponse:
    """
    Массовое переназначение задач:
    - либо по выбранным task_ids[]
    - либо по текущему фильтру (apply_mode=filtered)
    Доступно только администраторам.
    """
    if request.method != "POST":
        return redirect("task_list")

    user: User = request.user
    if not require_admin(user):
        messages.error(request, "Нет прав на массовое переназначение задач.")
        return redirect("task_list")

    new_assigned_id = (request.POST.get("assigned_to_id") or "").strip()
    apply_mode = (request.POST.get("apply_mode") or "selected").strip().lower()
    if not new_assigned_id:
        messages.error(request, "Выберите нового ответственного.")
        return redirect("task_list")

    new_assigned = get_object_or_404(User, id=new_assigned_id, is_active=True)
    if new_assigned.role not in (User.Role.MANAGER, User.Role.SALES_HEAD, User.Role.BRANCH_DIRECTOR):
        messages.error(request, "Нового ответственного можно выбрать только из: менеджер / РОП / директор филиала.")
        return redirect("task_list")

    # Режим "по фильтру" — применяем фильтры из POST
    if apply_mode == "filtered":
        now = timezone.now()
        local_now = timezone.localtime(now)
        today_start = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
        tomorrow_start = today_start + timedelta(days=1)

        qs = Task.objects.select_related("company", "assigned_to", "created_by", "type").order_by("-created_at").distinct()

        status = (request.POST.get("status") or "").strip()
        if status:
            qs = qs.filter(status=status)

        mine = (request.POST.get("mine") or "").strip()
        if mine == "1":
            qs = qs.filter(assigned_to=user)

        overdue = (request.POST.get("overdue") or "").strip()
        if overdue == "1":
            qs = qs.filter(due_at__lt=now).exclude(status__in=[Task.Status.DONE, Task.Status.CANCELLED])

        today = (request.POST.get("today") or "").strip()
        if today == "1":
            qs = qs.filter(due_at__gte=today_start, due_at__lt=tomorrow_start).exclude(status__in=[Task.Status.DONE, Task.Status.CANCELLED])

        # safety cap
        cap = 5000
        ids = list(qs.values_list("id", flat=True)[:cap])
        if not ids:
            messages.error(request, "Нет задач для переназначения.")
            return redirect("task_list")
        if len(ids) >= cap:
            messages.warning(request, f"Выбрано слишком много задач (>{cap}). Сузьте фильтр и повторите.")
            return redirect("task_list")
    else:
        ids = request.POST.getlist("task_ids") or []
        ids = [i for i in ids if i]
        if not ids:
            messages.error(request, "Выберите хотя бы одну задачу (чекбоксы слева).")
            return redirect("task_list")

    now_ts = timezone.now()
    with transaction.atomic():
        qs_to_update = Task.objects.filter(id__in=ids)
        updated = qs_to_update.update(assigned_to=new_assigned, updated_at=now_ts)

    messages.success(request, f"Переназначено задач: {updated}. Новый ответственный: {new_assigned}.")
    log_event(
        actor=user,
        verb=ActivityEvent.Verb.UPDATE,
        entity_type="task_bulk_reassign",
        entity_id=str(new_assigned.id),
        message="Массовое переназначение задач",
        meta={"count": updated, "to": str(new_assigned), "mode": apply_mode},
    )
    if new_assigned.id != user.id:
        notify(
            user=new_assigned,
            kind=Notification.Kind.TASK,
            title="Вам назначены задачи",
            body=f"Количество: {updated}",
            url="/tasks/?mine=1",
        )
    return redirect("task_list")


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

    # Для всех статусов ссылка ведёт на список задач с модальным окном просмотра конкретной задачи.
    task_url = f"/tasks/?view_task={task.id}"

    if new_status == Task.Status.DONE:
        # Уведомления о выполненной задаче:
        # 1) Исполнитель (кто поменял статус)
        # 2) Ответственный за задачу (assigned_to)
        # 3) Создатель задачи
        # 4) Директор филиала / РОП по филиалу компании/ответственного
        # 5) Управляющие группой компаний
        recipient_ids: set[int] = set()
        recipient_ids.add(user.id)
        if task.assigned_to_id:
            recipient_ids.add(task.assigned_to_id)
        if task.created_by_id:
            recipient_ids.add(task.created_by_id)

        branch_id = None
        if task.company_id and getattr(task, "company", None):
            branch_id = getattr(task.company, "branch_id", None)
        if not branch_id and getattr(task, "assigned_to", None):
            branch_id = getattr(task.assigned_to, "branch_id", None)

        if branch_id:
            for uid in User.objects.filter(
                is_active=True,
                role__in=[User.Role.BRANCH_DIRECTOR, User.Role.SALES_HEAD],
                branch_id=branch_id,
            ).values_list("id", flat=True):
                recipient_ids.add(int(uid))

        for uid in User.objects.filter(is_active=True, role=User.Role.GROUP_MANAGER).values_list("id", flat=True):
            recipient_ids.add(int(uid))

        for uid in recipient_ids:
            try:
                u = User.objects.get(id=uid, is_active=True)
            except User.DoesNotExist:
                continue
            notify(
                user=u,
                kind=Notification.Kind.TASK,
                title="Задача выполнена",
                body=f"{task.title}",
                url=task_url,
            )
    else:
        # Для остальных статусов сохраняем старую логику: уведомляем создателя (если это не он меняет)
        if task.created_by_id and task.created_by_id != user.id:
            notify(
                user=task.created_by,
                kind=Notification.Kind.TASK,
                title="Статус задачи изменён",
                body=f"{task.title}: {task.get_status_display()}",
                url=task_url,
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


@login_required
def task_edit(request: HttpRequest, task_id) -> HttpResponse:
    user: User = request.user
    task = get_object_or_404(Task.objects.select_related("company", "assigned_to", "created_by", "type"), id=task_id)

    if not _can_edit_task_ui(user, task):
        messages.error(request, "Нет прав на редактирование этой задачи.")
        return redirect("task_list")
    can_delete_task = _can_delete_task_ui(user, task)

    if request.method == "POST":
        form = TaskEditForm(request.POST, instance=task)
        if form.is_valid():
            form.save()
            messages.success(request, "Задача обновлена.")
            log_event(
                actor=user,
                verb=ActivityEvent.Verb.UPDATE,
                entity_type="task",
                entity_id=task.id,
                company_id=task.company_id,
                message=f"Обновлена задача: {task.title}",
            )
            return redirect("task_list")
    else:
        form = TaskEditForm(instance=task)

    return render(request, "ui/task_edit.html", {"form": form, "task": task, "can_delete_task": can_delete_task})


# _require_admin moved to crm.utils.require_admin


@login_required
def settings_dashboard(request: HttpRequest) -> HttpResponse:
    if not require_admin(request.user):
        messages.error(request, "Доступ запрещён.")
        return redirect("dashboard")
    return render(request, "ui/settings/dashboard.html", {})


@login_required
def settings_branches(request: HttpRequest) -> HttpResponse:
    if not require_admin(request.user):
        messages.error(request, "Доступ запрещён.")
        return redirect("dashboard")
    branches = Branch.objects.order_by("name")
    return render(request, "ui/settings/branches.html", {"branches": branches})


@login_required
def settings_branch_create(request: HttpRequest) -> HttpResponse:
    if not require_admin(request.user):
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
    if not require_admin(request.user):
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
    if not require_admin(request.user):
        messages.error(request, "Доступ запрещён.")
        return redirect("dashboard")
    users = User.objects.select_related("branch").order_by("username")
    return render(request, "ui/settings/users.html", {"users": users})


@login_required
def settings_user_create(request: HttpRequest) -> HttpResponse:
    if not require_admin(request.user):
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
    if not require_admin(request.user):
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
    if not require_admin(request.user):
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
    if not require_admin(request.user):
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
    if not require_admin(request.user):
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
    if not require_admin(request.user):
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
    if not require_admin(request.user):
        messages.error(request, "Доступ запрещён.")
        return redirect("dashboard")
    events = ActivityEvent.objects.select_related("actor").order_by("-created_at")[:500]
    return render(request, "ui/settings/activity.html", {"events": events})


@login_required
def settings_import(request: HttpRequest) -> HttpResponse:
    if not require_admin(request.user):
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
            try:
                Path(tmp_path).write_bytes(f.read())
                os.close(fd)  # Закрываем файловый дескриптор после записи
                fd = None
            except Exception:
                if fd:
                    os.close(fd)
                raise

            try:
                from companies.importer import import_amo_csv

                result = import_amo_csv(
                    csv_path=tmp_path,
                    encoding="utf-8-sig",
                    dry_run=dry_run,
                    companies_only=True,
                    limit_companies=limit_companies,
                    actor=request.user,
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


@login_required
def settings_import_tasks(request: HttpRequest) -> HttpResponse:
    if not require_admin(request.user):
        messages.error(request, "Доступ запрещён.")
        return redirect("dashboard")

    result = None
    if request.method == "POST":
        form = ImportTasksIcsForm(request.POST, request.FILES)
        if form.is_valid():
            import tempfile
            from pathlib import Path

            f = form.cleaned_data["ics_file"]
            limit_events = int(form.cleaned_data["limit_events"])
            dry_run = bool(form.cleaned_data.get("dry_run"))
            only_linked = bool(form.cleaned_data.get("only_linked"))
            unmatched_mode = (form.cleaned_data.get("unmatched_mode") or "keep").strip().lower()
            if only_linked:
                unmatched_mode = "skip"

            fd, tmp_path = tempfile.mkstemp(suffix=".ics")
            try:
                Path(tmp_path).write_bytes(f.read())
                os.close(fd)  # Закрываем файловый дескриптор после записи
                fd = None
            except Exception:
                if fd:
                    os.close(fd)
                raise

            try:
                from tasksapp.importer_ics import import_amocrm_ics

                result = import_amocrm_ics(
                    ics_path=tmp_path,
                    encoding="utf-8",
                    dry_run=dry_run,
                    limit_events=limit_events,
                    actor=request.user,
                    unmatched_mode=unmatched_mode,
                )
                if dry_run:
                    messages.success(request, "Проверка (dry-run) выполнена.")
                else:
                    messages.success(
                        request,
                        f"Импорт выполнен: добавлено задач {result.created_tasks}, пропущено (уже было) {result.skipped_existing}, пропущено (нет компании) {getattr(result,'skipped_unmatched',0)}.",
                    )
            finally:
                try:
                    Path(tmp_path).unlink(missing_ok=True)
                except Exception:
                    pass
    else:
        form = ImportTasksIcsForm()

    return render(request, "ui/settings/import_tasks.html", {"form": form, "result": result})


@login_required
def settings_amocrm(request: HttpRequest) -> HttpResponse:
    if not require_admin(request.user):
        messages.error(request, "Доступ запрещён.")
        return redirect("dashboard")

    cfg = AmoApiConfig.load()
    if request.method == "POST":
        form = AmoApiConfigForm(request.POST)
        if form.is_valid():
            cfg.domain = (form.cleaned_data.get("domain") or "").strip().replace("https://", "").replace("http://", "").strip("/")
            cfg.client_id = (form.cleaned_data.get("client_id") or "").strip()
            secret = (form.cleaned_data.get("client_secret") or "").strip()
            if secret:
                cfg.client_secret = secret
            token = (form.cleaned_data.get("long_lived_token") or "").strip()
            if token:
                cfg.long_lived_token = token
            # redirect uri: если пусто — построим из request
            ru = (form.cleaned_data.get("redirect_uri") or "").strip()
            if not ru:
                ru = request.build_absolute_uri("/settings/amocrm/callback/")
            cfg.redirect_uri = ru
            cfg.save(update_fields=["domain", "client_id", "client_secret", "redirect_uri", "long_lived_token", "updated_at"])
            messages.success(request, "Настройки amoCRM сохранены.")
            return redirect("settings_amocrm")
    else:
        form = AmoApiConfigForm(
            initial={
                "domain": cfg.domain or "kmrprofi.amocrm.ru",
                "client_id": cfg.client_id,
                "client_secret": cfg.client_secret,
                "redirect_uri": cfg.redirect_uri or request.build_absolute_uri("/settings/amocrm/callback/"),
                "long_lived_token": cfg.long_lived_token,
            }
        )

    auth_url = ""
    if cfg.domain and cfg.client_id and cfg.redirect_uri:
        try:
            auth_url = AmoClient(cfg).authorize_url()
        except Exception:
            auth_url = ""

    return render(
        request,
        "ui/settings/amocrm.html",
        {"form": form, "cfg": cfg, "auth_url": auth_url},
    )


@login_required
def settings_amocrm_callback(request: HttpRequest) -> HttpResponse:
    if not require_admin(request.user):
        messages.error(request, "Доступ запрещён.")
        return redirect("dashboard")

    code = (request.GET.get("code") or "").strip()
    if not code:
        messages.error(request, "amoCRM не вернул code (или доступ не разрешён).")
        return redirect("settings_amocrm")

    cfg = AmoApiConfig.load()
    try:
        AmoClient(cfg).exchange_code(code)
        messages.success(request, "amoCRM подключен. Токены сохранены.")
    except AmoApiError as e:
        cfg.last_error = str(e)
        cfg.save(update_fields=["last_error", "updated_at"])
        messages.error(request, f"Ошибка подключения amoCRM: {e}")
    return redirect("settings_amocrm")


@login_required
def settings_amocrm_disconnect(request: HttpRequest) -> HttpResponse:
    if not require_admin(request.user):
        messages.error(request, "Доступ запрещён.")
        return redirect("dashboard")
    cfg = AmoApiConfig.load()
    cfg.access_token = ""
    cfg.refresh_token = ""
    cfg.long_lived_token = ""
    cfg.expires_at = None
    cfg.last_error = ""
    cfg.save(update_fields=["access_token", "refresh_token", "long_lived_token", "expires_at", "last_error", "updated_at"])
    messages.success(request, "amoCRM отключен (токены удалены).")
    return redirect("settings_amocrm")


@login_required
def settings_amocrm_migrate(request: HttpRequest) -> HttpResponse:
    if not require_admin(request.user):
        messages.error(request, "Доступ запрещён.")
        return redirect("dashboard")

    cfg = None
    try:
        cfg = AmoApiConfig.load()
    except Exception as e:
        import traceback
        error_details = traceback.format_exc()
        print(f"AMOCRM_MIGRATE_ERROR: Failed to load AmoApiConfig: {error_details}")
        messages.error(request, f"Ошибка загрузки настроек amoCRM: {str(e)}. Проверьте логи сервера.")
        # Создаём пустой объект для рендера
        cfg = AmoApiConfig(domain="kmrprofi.amocrm.ru")
    
    if not cfg.is_connected():
        messages.error(request, "Сначала подключите amoCRM (OAuth).")
        return redirect("settings_amocrm")

    client = None
    users = []
    fields = []
    try:
        client = AmoClient(cfg)
        users = fetch_amo_users(client)
        fields = fetch_company_custom_fields(client)
        cfg.last_error = ""
        cfg.save(update_fields=["last_error", "updated_at"])
    except AmoApiError as e:
        cfg.last_error = str(e)
        cfg.save(update_fields=["last_error", "updated_at"])
        messages.error(request, f"Ошибка API amoCRM: {e}")
    except Exception as e:
        import traceback
        error_details = traceback.format_exc()
        print(f"AMOCRM_MIGRATE_INIT_ERROR: {error_details}")
        messages.error(request, f"Ошибка инициализации: {str(e)}. Проверьте логи сервера.")
        if not client:
            # Возвращаем страницу с пустыми данными, но с формой
            form = AmoMigrateFilterForm(initial={"dry_run": True, "limit_companies": 10, "offset": 0})
            return render(
                request,
                "ui/settings/amocrm_migrate.html",
                {"cfg": cfg, "form": form, "users": [], "fields": [], "result": None},
            )

    # default guesses
    def _find_field_id(names: list[str]) -> int | None:
        for f in fields:
            nm = str(f.get("name") or "")
            if any(n.lower() in nm.lower() for n in names):
                try:
                    return int(f.get("id") or 0) or None
                except Exception:
                    pass
        return None

    guessed_field_id = _find_field_id(["Сферы деятельности", "Статусы", "Сферы"]) or 0

    result = None
    if request.method == "POST":
        form = AmoMigrateFilterForm(request.POST)
        if form.is_valid():
            if not client:
                messages.error(request, "Ошибка: клиент amoCRM не инициализирован. Проверьте настройки подключения.")
            else:
                try:
                    # Защита от nginx 504: уменьшаем batch_size в зависимости от того, что импортируем
                    batch_size = int(form.cleaned_data.get("limit_companies") or 0)
                    if batch_size <= 0:
                        batch_size = 10  # дефолт уменьшен с 50 до 10
                    import_notes = bool(form.cleaned_data.get("import_notes"))
                    import_contacts = bool(form.cleaned_data.get("import_contacts"))
                    if import_notes and import_contacts:
                        batch_size = min(batch_size, 3)  # если оба включены - очень маленькая пачка
                    elif import_notes:
                        batch_size = min(batch_size, 5)  # только заметки
                    elif import_contacts:
                        batch_size = min(batch_size, 5)  # только контакты
                    else:
                        batch_size = min(batch_size, 10)  # только компании/задачи
                    migrate_all = bool(form.cleaned_data.get("migrate_all_companies", False))
                    custom_field_id = form.cleaned_data.get("custom_field_id") or 0
                    
                    result = migrate_filtered(
                        client=client,
                        actor=request.user,
                        responsible_user_id=int(form.cleaned_data["responsible_user_id"]),
                        sphere_field_id=int(custom_field_id),
                        sphere_option_id=form.cleaned_data.get("custom_value_enum_id") or None,
                        sphere_label=form.cleaned_data.get("custom_value_label") or None,
                        limit_companies=batch_size,
                        offset=int(form.cleaned_data.get("offset") or 0),
                        dry_run=bool(form.cleaned_data.get("dry_run")),
                        import_tasks=bool(form.cleaned_data.get("import_tasks")),
                        import_notes=bool(form.cleaned_data.get("import_notes")),
                        import_contacts=bool(form.cleaned_data.get("import_contacts")),
                        company_fields_meta=fields,
                        skip_field_filter=migrate_all,
                    )
                    if form.cleaned_data.get("dry_run"):
                        messages.success(request, "Проверка (dry-run) выполнена.")
                    else:
                        messages.success(request, "Импорт выполнен.")
                except AmoApiError as e:
                    messages.error(request, f"Ошибка миграции: {e}")
                except Exception as e:
                    # Логируем полную ошибку для отладки
                    import traceback
                    error_details = traceback.format_exc()
                    messages.error(request, f"Ошибка миграции: {str(e)}. Проверьте логи сервера для деталей.")
                    # В продакшене можно логировать в файл или sentry
                    print(f"AMOCRM_MIGRATE_ERROR: {error_details}")
    else:
        # попытка найти ответственную по имени "Иванова Юлия"
        default_resp = None
        try:
            for u in users:
                nm = str(u.get("name") or "")
                if "иванова" in nm.lower() and "юлия" in nm.lower():
                    default_resp = int(u.get("id") or 0)
                    break
        except Exception as e:
            print(f"AMOCRM_MIGRATE_ERROR: Failed to find default responsible: {e}")

        try:
            form = AmoMigrateFilterForm(
                initial={
                    "dry_run": True,
                    "limit_companies": 10,  # уменьшено с 50 до 10
                    "offset": 0,
                    "responsible_user_id": default_resp or "",
                    "custom_field_id": guessed_field_id or "",
                    "custom_value_label": "Новая CRM",
                    "import_tasks": True,
                    "import_notes": True,
                    "import_contacts": False,  # по умолчанию выключено
                }
            )
        except Exception as e:
            import traceback
            error_details = traceback.format_exc()
            print(f"AMOCRM_MIGRATE_ERROR: Failed to create form: {error_details}")
            # Создаём минимальную форму
            form = AmoMigrateFilterForm(initial={"dry_run": True, "limit_companies": 10, "offset": 0})

    try:
        return render(
            request,
            "ui/settings/amocrm_migrate.html",
            {"cfg": cfg, "form": form, "users": users, "fields": fields, "result": result},
        )
    except Exception as e:
        import traceback
        error_details = traceback.format_exc()
        print(f"AMOCRM_MIGRATE_ERROR: Failed to render template: {error_details}")
        # Возвращаем простую страницу с ошибкой
        from django.http import HttpResponse
        return HttpResponse(f"Ошибка рендеринга страницы миграции: {str(e)}. Проверьте логи сервера для деталей.", status=500)

# UI settings (admin only)
@login_required
def settings_company_columns(request: HttpRequest) -> HttpResponse:
    if not require_admin(request.user):
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
    if not require_admin(request.user):
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
