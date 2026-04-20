from __future__ import annotations

# phonebridge models — lazy import в функциях, где используются (company_detail, settings_integrations)
import json
import logging
import mimetypes
import os
import re
import uuid
from datetime import date as _date
from datetime import datetime, timedelta
from datetime import time as datetime_time
from decimal import Decimal
from uuid import UUID

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.core.validators import validate_email
from django.db import IntegrityError, models, transaction
from django.db.models import Avg, Count, Exists, F, IntegerField, Max, OuterRef, Prefetch, Q, Subquery
from django.db.models.functions import Coalesce
from django.http import (
    FileResponse,
    Http404,
    HttpRequest,
    HttpResponse,
    HttpResponseNotFound,
    JsonResponse,
    StreamingHttpResponse,
)
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from accounts.models import Branch, MagicLinkToken, User
from audit.models import ActivityEvent
from audit.service import log_event
from companies.decorators import require_can_view_company, require_can_view_note_company
from companies.models import (
    Company,
    CompanyDeal,
    CompanyDeletionRequest,
    CompanyEmail,
    CompanyHistoryEvent,
    CompanyNote,
    CompanyNoteAttachment,
    CompanyPhone,
    CompanySearchIndex,
    CompanySphere,
    CompanyStatus,
    Contact,
    ContactEmail,
    ContactPhone,
    ContractType,
    Region,
)
from companies.normalizers import normalize_phone as _normalize_phone_canonical
from companies.permissions import (
    can_edit_company as can_edit_company_perm,
)
from companies.permissions import (
    can_transfer_companies,
    can_transfer_company,
    get_transfer_targets,
    get_users_for_lists,
)
from companies.permissions import (
    editable_company_qs as editable_company_qs_perm,
)
from companies.policy import can_view_company as can_view_company_policy
from companies.policy import visible_companies_qs
from companies.services import resolve_target_companies
from notifications.models import Notification
from notifications.service import notify
from tasksapp.models import Task, TaskComment, TaskEvent, TaskType
from tasksapp.policy import can_manage_task_status, visible_tasks_qs

logger = logging.getLogger(__name__)

from django.core.exceptions import PermissionDenied

from accounts.permissions import get_effective_user, get_view_as_user, require_admin
from policy.decorators import policy_required
from policy.engine import decide as policy_decide
from ui.cleaners import clean_int_id
from ui.models import AmoApiConfig, UiGlobalConfig, UiUserPreference
from ui.templatetags.ui_extras import format_phone

from ..forms import (
    AmoApiConfigForm,
    AmoMigrateFilterForm,
    BranchForm,
    CompanyContractForm,
    CompanyCreateForm,
    CompanyEditForm,
    CompanyInlineEditForm,
    CompanyListColumnsForm,
    CompanyNoteForm,
    CompanyQuickEditForm,
    CompanySphereForm,
    CompanyStatusForm,
    ContactEmailFormSet,
    ContactForm,
    ContactPhoneFormSet,
    ContractTypeForm,
    ImportCompaniesForm,
    ImportTasksIcsForm,
    TaskEditForm,
    TaskForm,
    TaskTypeForm,
    UserCreateForm,
    UserEditForm,
)

# Константы для фильтров
RESPONSIBLE_FILTER_NONE = "none"  # Значение для фильтрации компаний без ответственного
STRONG_CONFIRM_THRESHOLD = 200  # Порог, после которого для bulk переноса включается усиленное подтверждение (логируется как strong_confirm_required)

# Explicitly list all names (including private helpers) so that
# "from ui.views._base import *" exports them into sub-modules.
__all__ = [
    # constants
    "RESPONSIBLE_FILTER_NONE",
    "STRONG_CONFIRM_THRESHOLD",
    # logging (sub-modules override this with their own logger)
    "logger",
    # private helpers
    "_dup_reasons",
    "_can_edit_company",
    "_editable_company_qs",
    "_company_branch_id",
    "_can_delete_company",
    "_notify_branch_leads",
    "_detach_client_branches",
    "_notify_head_deleted_with_branches",
    "_invalidate_company_count_cache",
    "_companies_with_overdue_flag",
    "_normalize_phone_for_search",
    "_normalize_for_search",
    "_tokenize_search_query",
    "_normalize_email_for_search",
    "_is_ajax",
    "_safe_next_v3",
    "_dt_label",
    "_cold_call_json",
    # sub-functions of _apply_company_filters
    "_cf_get_str_param",
    "_cf_get_list_param",
    "_cf_get_list_param_stripped",
    "_cf_to_int_list",
    "_filter_by_search",
    "_filter_by_selects",
    "_filter_by_tasks",
    "_filter_by_responsible",
    "_apply_company_filters",
    "_qs_without_page",
    # all imported names that sub-modules need
    "datetime",
    "datetime_time",
    "timedelta",
    "UUID",
    "Decimal",
    "login_required",
    "messages",
    "Paginator",
    "Exists",
    "OuterRef",
    "Q",
    "F",
    "Count",
    "Max",
    "Prefetch",
    "Avg",
    "models",
    "transaction",
    "IntegrityError",
    "HttpRequest",
    "HttpResponse",
    "StreamingHttpResponse",
    "JsonResponse",
    "FileResponse",
    "Http404",
    "HttpResponseNotFound",
    "get_object_or_404",
    "redirect",
    "render",
    "timezone",
    "ValidationError",
    "validate_email",
    "Branch",
    "User",
    "MagicLinkToken",
    "ActivityEvent",
    "log_event",
    "ContractType",
    "Company",
    "CompanyDeal",
    "CompanyHistoryEvent",
    "CompanyNote",
    "CompanyNoteAttachment",
    "CompanySphere",
    "CompanyStatus",
    "Region",
    "Contact",
    "ContactEmail",
    "ContactPhone",
    "CompanyDeletionRequest",
    "CompanyEmail",
    "CompanyPhone",
    "CompanySearchIndex",
    "resolve_target_companies",
    "can_edit_company_perm",
    "editable_company_qs_perm",
    "can_transfer_company",
    "get_transfer_targets",
    "get_users_for_lists",
    "can_transfer_companies",
    "can_view_company_policy",
    "visible_companies_qs",
    "require_can_view_company",
    "require_can_view_note_company",
    "Task",
    "TaskComment",
    "TaskEvent",
    "TaskType",
    "visible_tasks_qs",
    "can_manage_task_status",
    "Notification",
    "notify",
    # phonebridge models убраны из __all__ — lazy import
    "json",
    "mimetypes",
    "os",
    "re",
    "uuid",
    "_date",
    "cache",
    "UiGlobalConfig",
    "AmoApiConfig",
    "UiUserPreference",
    "require_admin",
    "get_effective_user",
    "get_view_as_user",
    "policy_required",
    "policy_decide",
    "PermissionDenied",
    "format_phone",
    "clean_int_id",
    "CompanyCreateForm",
    "CompanyQuickEditForm",
    "CompanyContractForm",
    "CompanyEditForm",
    "CompanyInlineEditForm",
    "CompanyNoteForm",
    "ContactEmailFormSet",
    "ContactForm",
    "ContactPhoneFormSet",
    "TaskForm",
    "TaskEditForm",
    "BranchForm",
    "CompanySphereForm",
    "CompanyStatusForm",
    "ContractTypeForm",
    "TaskTypeForm",
    "UserCreateForm",
    "UserEditForm",
    "ImportCompaniesForm",
    "ImportTasksIcsForm",
    "AmoApiConfigForm",
    "AmoMigrateFilterForm",
    "CompanyListColumnsForm",
    # cross-module helpers (used in multiple sub-modules)
    "_can_view_cold_call_reports",
    "_cold_call_confirm_q",
    "_month_start",
    "_add_months",
    "_month_label",
    "_can_manage_task_status_ui",
    "_can_edit_task_ui",
    "_can_delete_task_ui",
]


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
    qs = User.objects.filter(
        is_active=True,
        branch_id=branch_id,
        role__in=[User.Role.SALES_HEAD, User.Role.BRANCH_DIRECTOR],
    )
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
    children_qs = (
        Company.objects.filter(head_company_id=head_company.id)
        .select_related("responsible", "branch")
        .order_by("name")
    )
    children = list(children_qs[:200])
    if children:
        now_ts = timezone.now()
        Company.objects.filter(head_company_id=head_company.id).update(
            head_company=None, updated_at=now_ts
        )
    return children


def _notify_head_deleted_with_branches(
    *, actor: User, head_company: Company, detached: list[Company]
):
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


def _invalidate_company_count_cache():
    """
    Инвалидирует кэш общего количества компаний.
    Удаляет все ключи с префиксом 'companies_total_count_*'.
    """
    from django.core.cache import cache

    # Для Redis можно использовать delete_pattern, но для LocMemCache нужно удалять по ключам
    # Используем простой подход: удаляем ключи для всех возможных комбинаций user/view_as
    # В реальности лучше использовать Redis с delete_pattern или версионирование ключей

    # Удаляем старый глобальный ключ (для обратной совместимости)
    cache.delete("companies_total_count")

    # Если используется Redis, можно использовать delete_pattern
    # Для LocMemCache это не работает, поэтому очищаем весь кэш при массовых операциях
    # или используем версионирование ключей


def _companies_with_overdue_flag(*, now):
    """
    Базовый QS компаний с вычисляемым флагом просроченных задач `has_overdue`
    и наличием активных задач `has_any_active_task`.
    Используется в списке/экспорте/массовых операциях, чтобы фильтры работали одинаково.
    """
    overdue_tasks = (
        Task.objects.filter(company_id=OuterRef("pk"), due_at__lt=now)
        .exclude(status__in=[Task.Status.DONE, Task.Status.CANCELLED])
        .values("id")
    )
    active_tasks = (
        Task.objects.filter(company_id=OuterRef("pk"))
        .exclude(status__in=[Task.Status.DONE, Task.Status.CANCELLED])
        .values("id")
    )
    cold_contacts = Contact.objects.filter(company_id=OuterRef("pk"), is_cold_call=True).values(
        "id"
    )
    # Скалярная подзапрос-аннотация количества активных задач (не ломает JOIN'ы)
    active_tasks_count_sq = (
        Task.objects.filter(company_id=OuterRef("pk"))
        .exclude(status__in=[Task.Status.DONE, Task.Status.CANCELLED])
        .values("company_id")
        .annotate(_c=Count("id"))
        .values("_c")
    )
    return Company.objects.all().annotate(
        has_overdue=Exists(overdue_tasks),
        has_any_active_task=Exists(active_tasks),
        has_cold_call_contact=Exists(cold_contacts),
        active_tasks_count=Coalesce(
            Subquery(active_tasks_count_sq, output_field=IntegerField()),
            0,
        ),
    )


def _normalize_phone_for_search(phone: str) -> str:
    """Нормализует номер телефона для поиска через единый нормализатор."""
    return _normalize_phone_canonical(phone)


def _normalize_for_search(text: str) -> str:
    """
    Нормализует текст для поиска: убирает тире, пробелы и другие разделители.
    Используется для поиска по названию, ИНН, адресу - чтобы находить совпадения
    даже если пользователь не помнит точное написание (например, с тире или без).
    """
    if not text:
        return ""
    # Убираем тире, дефисы, пробелы и другие разделители
    normalized = (
        text.replace("-", "").replace("—", "").replace("–", "").replace(" ", "").replace("_", "")
    )
    # Приводим к нижнему регистру для регистронезависимого поиска
    return normalized.lower().strip()


_SEARCH_TOKEN_RE = re.compile(r"[0-9A-Za-zА-Яа-яЁё]+", re.UNICODE)


def _tokenize_search_query(q: str) -> list[str]:
    """
    Токенизация пользовательского поиска:
    - режем по любым разделителям/пунктуации
    - приводим к lower
    - отбрасываем слишком короткие токены (1 символ), чтобы не раздувать выдачу по "г", "и" и т.п.
    """
    if not q:
        return []
    tokens = [m.group(0).lower() for m in _SEARCH_TOKEN_RE.finditer(q)]
    out: list[str] = []
    for t in tokens:
        tt = (t or "").strip()
        if not tt:
            continue
        if len(tt) == 1 and not tt.isdigit():
            continue
        out.append(tt)
    return out


def _normalize_email_for_search(email: str) -> str:
    """
    Нормализует email для поиска: убирает пробелы, приводит к нижнему регистру.
    """
    if not email:
        return ""
    return email.strip().lower()


def _is_ajax(request: HttpRequest) -> bool:
    # Django 4+ убрал request.is_ajax(); используем заголовок как и в других AJAX endpoints проекта.
    return (request.headers.get("X-Requested-With") or "") == "XMLHttpRequest"


def _safe_next_v3(request: HttpRequest, company_id) -> str | None:
    """F4 R3: если POST/GET содержит `next`, который указывает на v3-preview
    этой же компании — вернуть его. Whitelist защита от open-redirect.

    Используется в view-хендлерах create/delete (deal/note/task/phone/email),
    чтобы после submit возвращаться туда, откуда пришёл запрос (v3/b/ vs
    классическая карточка).
    """
    nxt = (request.POST.get("next") or request.GET.get("next") or "").strip()
    if not nxt:
        return None
    prefix = f"/companies/{company_id}/v3/"
    # whitelist: только внутренние v3-URL этой компании
    if nxt.startswith(prefix) and "\n" not in nxt and "\r" not in nxt:
        return nxt
    return None


def _dt_label(dt: datetime | None) -> str:
    if not dt:
        return ""
    try:
        return timezone.localtime(dt).strftime("%d.%m.%Y %H:%M")
    except Exception:
        try:
            return dt.strftime("%d.%m.%Y %H:%M")
        except Exception:
            return ""


def _cold_call_json(
    *,
    entity: str,
    entity_id: str,
    is_cold_call: bool,
    marked_at: datetime | None,
    marked_by: str,
    can_reset: bool,
    message: str,
) -> JsonResponse:
    return JsonResponse(
        {
            "ok": True,
            "entity": entity,
            "id": entity_id,
            "is_cold_call": bool(is_cold_call),
            "has_mark": bool(marked_at),
            "marked_at": _dt_label(marked_at),
            "marked_by": marked_by or "",
            "can_reset": bool(can_reset),
            "message": message or "",
        }
    )


# ---------------------------------------------------------------------------
# Вспомогательные функции для _apply_company_filters
# ---------------------------------------------------------------------------


def _cf_get_str_param(params: dict, key: str, default: str = "") -> str:
    """Безопасное извлечение строкового значения из params (dict или QueryDict)."""
    value = params.get(key, default)
    if isinstance(value, list):
        return (value[0] if value else default).strip()
    return (value or default).strip()


def _cf_get_list_param(params: dict, key: str) -> list[str]:
    """
    Извлечение списка значений из params.
    Поддерживает QueryDict (getlist) и обычный dict (строка или список).
    Без strip — для совместимости с регионами, где strip делается отдельно.
    """
    if hasattr(params, "getlist"):
        try:
            return [str(x) for x in (params.getlist(key) or [])]
        except Exception:
            return []
    v = params.get(key, [])
    if isinstance(v, list):
        return [str(x) for x in v]
    if isinstance(v, str):
        return [v] if v else []
    return []


def _cf_get_list_param_stripped(params: dict, key: str) -> list[str]:
    """
    Извлечение списка значений из params со strip каждого элемента.
    Используется для status/branch/sphere/responsible.
    """
    if hasattr(params, "getlist"):
        try:
            return [str(x).strip() for x in (params.getlist(key) or []) if str(x).strip()]
        except Exception:
            return []
    v = params.get(key, [])
    if isinstance(v, list):
        return [str(x).strip() for x in v if str(x).strip()]
    if isinstance(v, str):
        return [v.strip()] if v.strip() else []
    return []


def _cf_to_int_list(vals: list[str]) -> list[int]:
    """Конвертация списка строк в список целых чисел (некорректные значения пропускаются)."""
    out: list[int] = []
    for v in vals:
        try:
            out.append(int(v))
        except (ValueError, TypeError):
            pass
    return out


def _filter_by_search(qs, q: str):
    """
    Фильтрация компаний по текстовому запросу.
    Включает: базовый icontains, нормализацию (тире/пробелы), токенизацию,
    поиск по телефонам (с нормализацией), поиск по email, поиск по ФИО контактов.
    """
    normalized_q = _normalize_for_search(q)
    tokens = _tokenize_search_query(q)

    # --- Базовые фильтры по полям компании ---
    # Для ИНН: ищем как подстроку, а также по каждому отдельному ИНН из строки
    inn_filters = Q(inn__icontains=q)
    # Если запрос похож на ИНН (только цифры, 8–12 символов), ищем по каждому ИНН отдельно
    if q.isdigit() and 8 <= len(q) <= 12:
        from companies.inn_utils import parse_inns

        # Парсим все ИНН из запроса (на случай, если введено несколько)
        query_inns = parse_inns(q)
        if query_inns:
            # Ищем компании, у которых любой из ИНН в поле совпадает с запросом
            for query_inn in query_inns:
                inn_filters |= Q(inn__icontains=query_inn)

    base_filters = (
        Q(name__icontains=q)
        | inn_filters
        | Q(kpp__icontains=q)
        | Q(legal_name__icontains=q)
        | Q(address__icontains=q)
        | Q(phone__icontains=q)
        | Q(email__icontains=q)
        | Q(contact_name__icontains=q)
        | Q(contact_position__icontains=q)
        | Q(branch__name__icontains=q)
    )

    # --- Поиск по нормализованному запросу (тире/пробелы) ---
    # Используем простой icontains вместо regex для производительности
    # Ограничиваем количество вариантов для ускорения запроса
    if normalized_q and len(normalized_q) >= 2:  # Минимум 2 символа для нормализованного поиска
        # Для ИНН: ищем как подстроку, а также по каждому отдельному ИНН
        normalized_inn_filters = Q(inn__icontains=normalized_q)
        if normalized_q.isdigit() and 8 <= len(normalized_q) <= 12:
            from companies.inn_utils import parse_inns

            query_inns = parse_inns(normalized_q)
            if query_inns:
                for query_inn in query_inns:
                    normalized_inn_filters |= Q(inn__icontains=query_inn)

        # Основной поиск по нормализованному запросу (самый быстрый)
        normalized_simple_filters = (
            Q(name__icontains=normalized_q)
            | Q(legal_name__icontains=normalized_q)
            | normalized_inn_filters
            | Q(address__icontains=normalized_q)
        )

        # Добавляем только самые важные варианты (не все, чтобы не замедлять)
        # Если запрос содержит тире, пробуем без него и наоборот
        if "-" in q:
            variant_no_dash = q.replace("-", " ")
            if variant_no_dash != q:
                normalized_simple_filters |= (
                    Q(name__icontains=variant_no_dash)
                    | Q(legal_name__icontains=variant_no_dash)
                    | Q(address__icontains=variant_no_dash)
                )
        elif " " in q:
            variant_with_dash = q.replace(" ", "-")
            if variant_with_dash != q:
                normalized_simple_filters |= (
                    Q(name__icontains=variant_with_dash)
                    | Q(legal_name__icontains=variant_with_dash)
                    | Q(address__icontains=variant_with_dash)
                )

        base_filters |= normalized_simple_filters

    # --- Токенизированный поиск ---
    # Если в запросе несколько слов/токенов, ищем так, чтобы ВСЕ токены встречались
    # (в любых из основных полей компании). Это чинит кейсы вида:
    # "пат таштагол" vs "ПАТ', г.Таштагол" (пунктуация/точки/запятые).
    token_filters = Q()
    if len(tokens) >= 2:
        token_filters = Q()
        for tok in tokens:
            # Для ИНН: ищем как подстроку, а также по каждому отдельному ИНН
            tok_inn_filters = Q(inn__icontains=tok)
            if tok.isdigit() and 8 <= len(tok) <= 12:
                from companies.inn_utils import parse_inns

                query_inns = parse_inns(tok)
                if query_inns:
                    for query_inn in query_inns:
                        tok_inn_filters |= Q(inn__icontains=query_inn)

            per_tok = (
                Q(name__icontains=tok)
                | Q(legal_name__icontains=tok)
                | tok_inn_filters
                | Q(kpp__icontains=tok)
                | Q(address__icontains=tok)
                | Q(phone__icontains=tok)
                | Q(email__icontains=tok)
                | Q(contact_name__icontains=tok)
                | Q(contact_position__icontains=tok)
                | Q(branch__name__icontains=tok)
            )
            token_filters &= per_tok

    # --- Поиск по телефонам (с нормализацией) ---
    # Оптимизировано с использованием Exists вместо JOIN
    normalized_phone = _normalize_phone_for_search(q)
    phone_filters = Q()
    if normalized_phone and normalized_phone != q:
        # Основной телефон компании (точное совпадение быстрее)
        phone_filters = Q(phone=normalized_phone)
        # Также ищем по исходному запросу в основном телефоне
        phone_filters |= Q(phone__icontains=q)

        # Дополнительные телефоны компании - используем Exists вместо JOIN (быстрее)
        phone_filters |= Exists(
            CompanyPhone.objects.filter(company_id=OuterRef("pk"), value=normalized_phone)
        )
        phone_filters |= Exists(
            CompanyPhone.objects.filter(company_id=OuterRef("pk"), value__icontains=q)
        )

        # Телефоны контактов - используем Exists вместо JOIN (быстрее)
        phone_filters |= Exists(
            ContactPhone.objects.filter(contact__company_id=OuterRef("pk"), value=normalized_phone)
        )
        phone_filters |= Exists(
            ContactPhone.objects.filter(contact__company_id=OuterRef("pk"), value__icontains=q)
        )
    else:
        # Если нормализация не удалась, ищем как есть
        phone_filters = Q(phone__icontains=q)
        # Используем Exists для связанных таблиц
        phone_filters |= Exists(
            CompanyPhone.objects.filter(company_id=OuterRef("pk"), value__icontains=q)
        )
        phone_filters |= Exists(
            ContactPhone.objects.filter(contact__company_id=OuterRef("pk"), value__icontains=q)
        )

    # --- Поиск по email (с нормализацией) ---
    # Оптимизировано с использованием Exists вместо JOIN
    normalized_email = _normalize_email_for_search(q)
    email_filters = Q()
    if normalized_email:
        # Основной email компании (точное совпадение быстрее)
        email_filters = Q(email__iexact=normalized_email)
        # Также ищем по частичному совпадению в основном email
        email_filters |= Q(email__icontains=q)

        # Email контактов - используем Exists вместо JOIN (быстрее)
        email_filters |= Exists(
            ContactEmail.objects.filter(
                contact__company_id=OuterRef("pk"), value__iexact=normalized_email
            )
        )
        email_filters |= Exists(
            ContactEmail.objects.filter(contact__company_id=OuterRef("pk"), value__icontains=q)
        )

        # Дополнительные email компании - используем Exists
        email_filters |= Exists(
            CompanyEmail.objects.filter(company_id=OuterRef("pk"), value__iexact=normalized_email)
        )
        email_filters |= Exists(
            CompanyEmail.objects.filter(company_id=OuterRef("pk"), value__icontains=q)
        )
    else:
        email_filters = Q(email__icontains=q)
        # Используем Exists для связанных таблиц
        email_filters |= Exists(
            ContactEmail.objects.filter(contact__company_id=OuterRef("pk"), value__icontains=q)
        )
        email_filters |= Exists(
            CompanyEmail.objects.filter(company_id=OuterRef("pk"), value__icontains=q)
        )

    # --- Поиск по ФИО в контактах ---
    # Разбиваем запрос на слова для более гибкого поиска
    words = tokens or [w.strip().lower() for w in q.split() if w.strip()]
    fio_filters = Q()

    if len(words) > 1:
        # Если несколько слов, ищем контакты, где ВСЕ слова найдены (в любых полях одного контакта)
        # Используем Exists для проверки, что есть контакт компании, где все слова найдены
        contact_q = Contact.objects.filter(company_id=OuterRef("pk"))
        # Для каждого слова создаём условие, что оно найдено в ФИО контакта
        # И все эти условия должны выполняться для одного контакта
        for word in words:
            contact_q = contact_q.filter(
                Q(first_name__icontains=word) | Q(last_name__icontains=word)
            )

        # Ищем компании, у которых есть такие контакты
        fio_filters = Exists(contact_q)
        # Также ищем по полному запросу в каждом поле (на случай, если ФИО хранится в одном поле)
        fio_filters |= Q(contacts__first_name__icontains=q)
        fio_filters |= Q(contacts__last_name__icontains=q)
    elif len(words) == 1:
        # Одно слово - используем Exists для оптимизации
        word = words[0]
        contact_q = Contact.objects.filter(company_id=OuterRef("pk")).filter(
            Q(first_name__icontains=word) | Q(last_name__icontains=word)
        )
        fio_filters = Exists(contact_q)
    else:
        # Пустой запрос (не должно быть, но на всякий случай) - используем Exists
        contact_q = Contact.objects.filter(company_id=OuterRef("pk")).filter(
            Q(first_name__icontains=q) | Q(last_name__icontains=q)
        )
        fio_filters = Exists(contact_q)

    # Объединяем все фильтры
    return qs.filter(
        base_filters | token_filters | phone_filters | email_filters | fio_filters
    ).distinct()


def _filter_by_selects(qs, params: dict):
    """
    Фильтрация по select-полям: статус, подразделение, сфера, тип договора, регион.
    Возвращает кортеж (qs, context_dict) с отфильтрованным QS и данными для шаблона.
    """
    # --- Status (multi) ---
    status_ids = _cf_to_int_list(_cf_get_list_param_stripped(params, "status"))
    if status_ids:
        qs = qs.filter(status_id__in=status_ids)
    selected_statuses = [str(i) for i in status_ids]
    status = selected_statuses[0] if selected_statuses else ""

    # --- Branch (multi) ---
    branch_ids = _cf_to_int_list(_cf_get_list_param_stripped(params, "branch"))
    if branch_ids:
        qs = qs.filter(branch_id__in=branch_ids)
    selected_branches = [str(i) for i in branch_ids]
    branch = selected_branches[0] if selected_branches else ""

    # --- Sphere (multi) ---
    sphere_ids = _cf_to_int_list(_cf_get_list_param_stripped(params, "sphere"))
    if sphere_ids:
        qs = qs.filter(spheres__id__in=sphere_ids)
    selected_spheres = [str(i) for i in sphere_ids]
    sphere = selected_spheres[0] if selected_spheres else ""

    # --- Contract type ---
    contract_type = _cf_get_str_param(params, "contract_type")
    if contract_type:
        try:
            contract_type_id = int(contract_type)
            qs = qs.filter(contract_type_id=contract_type_id)
        except (ValueError, TypeError):
            # Некорректный ID - пропускаем фильтр
            pass

    # --- Region (multi) ---
    # Поддержка множественного выбора регионов.
    # params может быть как dict, так и QueryDict (request.GET / request.POST).
    region_values = _cf_get_list_param(params, "region")
    region_ids: list[int] = []
    for r in region_values:
        r_str = (r or "").strip()
        if not r_str:
            continue
        try:
            region_ids.append(int(r_str))
        except (ValueError, TypeError):
            pass

    if region_ids:
        qs = qs.filter(region_id__in=region_ids)

    # Для обратной совместимости сохраняем строковое представление (первое значение или пустая строка)
    region = str(region_ids[0]) if region_ids else ""
    # Список выбранных регионов для шаблона
    selected_regions = [str(rid) for rid in region_ids]

    return qs, {
        "status": status,
        "selected_statuses": selected_statuses,
        "status_ids": status_ids,
        "branch": branch,
        "selected_branches": selected_branches,
        "branch_ids": branch_ids,
        "sphere": sphere,
        "selected_spheres": selected_spheres,
        "sphere_ids": sphere_ids,
        "contract_type": contract_type,
        "region": region,
        "selected_regions": selected_regions,
        "region_ids": region_ids,
    }


def _filter_by_tasks(qs, params: dict):
    """
    Фильтрация по задачам: overdue (просроченные) и task_filter (нет задач / диапазон дат).
    Возвращает кортеж (qs, overdue, task_filter).
    """
    overdue = _cf_get_str_param(params, "overdue")
    task_filter = _cf_get_str_param(params, "task_filter")

    # task_filter=no_tasks перекрывает флаг overdue
    if task_filter == "no_tasks":
        overdue = ""

    if overdue == "1":
        qs = qs.filter(has_overdue=True)

    _VALID_TASK_FILTERS = ("no_tasks", "today", "tomorrow", "week", "month", "quarter")
    if task_filter and task_filter not in _VALID_TASK_FILTERS:
        task_filter = ""

    if task_filter:
        local_now = timezone.localtime(timezone.now())
        today = local_now.date()
        active_task_status_exclude = [Task.Status.DONE, Task.Status.CANCELLED]
        if task_filter == "no_tasks":
            qs = qs.filter(has_any_active_task=False)
        elif task_filter in ("today", "tomorrow", "week", "month", "quarter"):
            if task_filter == "today":
                task_due_q = Q(due_at__date=today)
            elif task_filter == "tomorrow":
                task_due_q = Q(due_at__date=today + timedelta(days=1))
            elif task_filter == "week":
                start_week = today - timedelta(days=today.weekday())
                end_week = start_week + timedelta(days=7)
                task_due_q = Q(due_at__date__gte=start_week, due_at__date__lt=end_week)
            elif task_filter == "month":
                task_due_q = Q(due_at__year=local_now.year, due_at__month=local_now.month)
            else:
                month = local_now.month
                q_start = (month - 1) // 3 * 3 + 1
                task_due_q = Q(
                    due_at__year=local_now.year,
                    due_at__month__in=[q_start, q_start + 1, q_start + 2],
                )
            tasks_in_range = (
                Task.objects.filter(company_id=OuterRef("pk"))
                .exclude(status__in=active_task_status_exclude)
                .filter(due_at__isnull=False)
                .filter(task_due_q)
                .values("id")
            )
            qs = qs.filter(Exists(tasks_in_range))

    return qs, overdue, task_filter


def _filter_by_responsible(qs, params: dict, default_responsible_id: int | None):
    """
    Фильтрация по ответственному менеджеру.
    Поддерживает multi-select и специальное значение "none" (без ответственного).
    Если responsible не передан и задан default_responsible_id — применяет его.
    Возвращает кортеж (qs, responsible, selected_responsibles, responsible_ids, has_none).
    """
    responsible_raw = _cf_get_list_param_stripped(params, "responsible")
    selected_responsibles: list[str] = []
    has_none = RESPONSIBLE_FILTER_NONE in responsible_raw
    responsible_ids = _cf_to_int_list([v for v in responsible_raw if v != RESPONSIBLE_FILTER_NONE])

    if not responsible_raw and default_responsible_id is not None:
        qs = qs.filter(responsible_id=default_responsible_id)
        responsible = str(default_responsible_id)
        selected_responsibles = [responsible]
    else:
        if responsible_ids and has_none:
            qs = qs.filter(Q(responsible_id__in=responsible_ids) | Q(responsible__isnull=True))
        elif responsible_ids:
            qs = qs.filter(responsible_id__in=responsible_ids)
        elif has_none:
            qs = qs.filter(responsible__isnull=True)
        selected_responsibles = [str(i) for i in responsible_ids] + (
            [RESPONSIBLE_FILTER_NONE] if has_none else []
        )
        responsible = selected_responsibles[0] if selected_responsibles else ""

    return qs, responsible, selected_responsibles, responsible_ids, has_none


# ---------------------------------------------------------------------------
# Главная функция фильтрации — оркестратор
# ---------------------------------------------------------------------------


def _apply_company_filters(*, qs, params: dict, default_responsible_id: int | None = None):
    """
    Единые фильтры компаний для:
    - списка компаний
    - экспорта
    - массового переназначения (apply_mode=filtered)

    Если default_responsible_id указан и параметр responsible отсутствует в params,
    применяется фильтр по default_responsible_id.
    """
    # --- Текстовый поиск ---
    q = _cf_get_str_param(params, "q")
    if q:
        qs = _filter_by_search(qs, q)

    # --- Select-фильтры (статус, подразделение, сфера, тип договора, регион) ---
    qs, selects_ctx = _filter_by_selects(qs, params)

    # --- Фильтры по задачам (overdue, task_filter) ---
    qs, overdue, task_filter = _filter_by_tasks(qs, params)

    # --- Фильтр по ответственному ---
    qs, responsible, selected_responsibles, responsible_ids, has_none = _filter_by_responsible(
        qs, params, default_responsible_id
    )

    # --- Флаг активности хотя бы одного фильтра ---
    filter_active = any(
        [
            q,
            responsible_ids,
            has_none,
            selects_ctx["status_ids"],
            selects_ctx["branch_ids"],
            selects_ctx["sphere_ids"],
            selects_ctx["contract_type"],
            selects_ctx["region_ids"],
            overdue == "1",
            bool(task_filter),
        ]
    )

    # PERF (2026-04-20 audit): .distinct() применяем ТОЛЬКО когда реально нужен —
    # при M2M-фильтре по spheres или JOIN-фильтрах (overdue/task_filter через Task subquery).
    # Без этого COUNT(*) над DISTINCT по всем 45K строкам × 40+ полей = 1+ секунда впустую.
    # Остальные фильтры (FK по Company) не дают дублей → distinct лишний.
    needs_distinct = bool(
        selects_ctx["sphere_ids"]  # M2M: spheres
        or overdue == "1"  # JOIN c tasksapp_task
        or bool(task_filter)  # JOIN c tasksapp_task
    )
    result_qs = qs.distinct() if needs_distinct else qs
    return {
        "qs": result_qs,
        "q": q,
        "responsible": responsible,
        "selected_responsibles": selected_responsibles,
        "status": selects_ctx["status"],
        "selected_statuses": selects_ctx["selected_statuses"],
        "branch": selects_ctx["branch"],
        "selected_branches": selects_ctx["selected_branches"],
        "sphere": selects_ctx["sphere"],
        "selected_spheres": selects_ctx["selected_spheres"],
        "contract_type": selects_ctx["contract_type"],
        "region": selects_ctx["region"],
        "selected_regions": selects_ctx["selected_regions"],
        "overdue": overdue,
        "task_filter": task_filter,
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
    except Exception as e:
        from core.request_id import get_request_id

        logger.warning(
            f"Ошибка при удалении параметра '{page_key}' из URL: {e}",
            exc_info=True,
            extra={"request_id": get_request_id()},
        )
    return params.urlencode()


# ---------------------------------------------------------------------------
# Cross-module helpers: defined here so all sub-modules can access them via
# "from ui.views._base import *"
# ---------------------------------------------------------------------------


def _can_view_cold_call_reports(user):
    if not user or not user.is_authenticated or not user.is_active:
        return False
    return bool(
        user.is_superuser
        or user.role
        in (
            User.Role.ADMIN,
            User.Role.GROUP_MANAGER,
            User.Role.BRANCH_DIRECTOR,
            User.Role.SALES_HEAD,
            User.Role.MANAGER,
        )
    )


def _cold_call_confirm_q():
    return Q(
        Q(company__primary_cold_marked_call_id=F("id"))
        | Q(contact__cold_marked_call_id=F("id"))
        | Q(company__phones__cold_marked_call_id=F("id"))
        | Q(contact__phones__cold_marked_call_id=F("id"))
    )


def _month_start(d):
    return d.replace(day=1)


def _add_months(d, delta_months):
    import calendar

    y = d.year
    m = d.month + int(delta_months)
    while m <= 0:
        y -= 1
        m += 12
    while m > 12:
        y += 1
        m -= 12
    return _date(y, m, 1)


def _month_label(d):
    months = {
        1: "Январь",
        2: "Февраль",
        3: "Март",
        4: "Апрель",
        5: "Май",
        6: "Июнь",
        7: "Июль",
        8: "Август",
        9: "Сентябрь",
        10: "Октябрь",
        11: "Ноябрь",
        12: "Декабрь",
    }
    return f"{months.get(d.month, str(d.month))} {d.year}"


def _can_manage_task_status_ui(user, task):
    if not user or not user.is_authenticated or not user.is_active:
        return False
    if user.is_superuser or user.role in (User.Role.ADMIN, User.Role.GROUP_MANAGER):
        return True
    if task.created_by_id and task.created_by_id == user.id:
        return True
    if task.assigned_to_id and task.assigned_to_id == user.id:
        return True
    if task.company_id:
        try:
            company = getattr(task, "company", None)
            if company and company.responsible_id == user.id:
                return True
        except Exception:
            pass
    if user.role in (User.Role.BRANCH_DIRECTOR, User.Role.SALES_HEAD) and user.branch_id:
        branch_id = None
        if task.company_id and getattr(task, "company", None):
            branch_id = getattr(task.company, "branch_id", None)
        if not branch_id and getattr(task, "assigned_to", None):
            branch_id = getattr(task.assigned_to, "branch_id", None)
        return bool(branch_id and branch_id == user.branch_id)
    return False


def _can_edit_task_ui(user, task):
    if task.created_by_id and task.created_by_id == user.id:
        return True
    if task.assigned_to_id and task.assigned_to_id == user.id:
        return True
    if user.role in (User.Role.ADMIN, User.Role.GROUP_MANAGER):
        return True
    if task.company_id:
        try:
            company = getattr(task, "company", None)
            if company and company.responsible_id == user.id:
                return True
        except Exception:
            pass
    if (
        user.role in (User.Role.SALES_HEAD, User.Role.BRANCH_DIRECTOR)
        and user.branch_id
        and task.company_id
    ):
        try:
            if getattr(task.company, "branch_id", None) == user.branch_id:
                return True
        except Exception:
            pass
    return False


def _can_delete_task_ui(user, task):
    if not user or not user.is_authenticated or not user.is_active:
        return False
    if user.is_superuser or user.role in (User.Role.ADMIN, User.Role.GROUP_MANAGER):
        return True
    if task.created_by_id and task.created_by_id == user.id:
        return True
    if task.assigned_to_id and task.assigned_to_id == user.id:
        return True
    if task.company_id and getattr(task, "company", None):
        try:
            if getattr(task.company, "responsible_id", None) == user.id:
                return True
        except Exception:
            pass
    if user.role in (User.Role.BRANCH_DIRECTOR, User.Role.SALES_HEAD) and user.branch_id:
        branch_id = None
        if task.company_id and getattr(task, "company", None):
            branch_id = getattr(task.company, "branch_id", None)
        if not branch_id and getattr(task, "assigned_to", None):
            branch_id = getattr(task.assigned_to, "branch_id", None)
        return bool(branch_id and branch_id == user.branch_id)
    return False
