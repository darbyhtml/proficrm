from __future__ import annotations

from datetime import datetime, time as datetime_time, timedelta
from uuid import UUID
from decimal import Decimal

from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.core.paginator import Paginator
from django.db.models import Exists, OuterRef, Q, F
from django.db.models import Count, Max, Prefetch, Avg
from django.db import models, transaction, IntegrityError
from django.http import HttpRequest, HttpResponse
from django.http import StreamingHttpResponse
from django.http import JsonResponse
from django.http import FileResponse, Http404, HttpResponseNotFound
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.core.exceptions import ValidationError
from django.core.validators import validate_email

from accounts.models import Branch, User, MagicLinkToken
from audit.models import ActivityEvent
from audit.service import log_event
from companies.models import (
    ContractType,
    Company,
    CompanyNote,
    CompanySphere,
    CompanyStatus,
    Region,
    Contact,
    ContactEmail,
    ContactPhone,
    CompanyDeletionRequest,
    CompanyEmail,
    CompanyPhone,
    CompanySearchIndex,
)
from companies.services import resolve_target_companies
from companies.permissions import (
    can_edit_company as can_edit_company_perm,
    editable_company_qs as editable_company_qs_perm,
    can_transfer_company,
    get_transfer_targets,
    get_users_for_lists,
    can_transfer_companies,
)
from companies.policy import can_view_company as can_view_company_policy, visible_companies_qs
from companies.decorators import require_can_view_company, require_can_view_note_company
from tasksapp.models import Task, TaskType
from tasksapp.policy import visible_tasks_qs, can_manage_task_status
from notifications.models import Notification
from notifications.service import notify
from phonebridge.models import CallRequest, PhoneDevice, MobileAppBuild, MobileAppQrToken
import json
import logging
import mimetypes
import os
import re
import uuid
from datetime import date as _date

from django.core.cache import cache

logger = logging.getLogger(__name__)

from .forms import (
    CompanyCreateForm,
    CompanyQuickEditForm,
    CompanyContractForm,
    CompanyEditForm,
    CompanyInlineEditForm,
    CompanyNoteForm,
    ContactEmailFormSet,
    ContactForm,
    ContactPhoneFormSet,
    TaskForm,
    TaskEditForm,
    BranchForm,
    CompanySphereForm,
    CompanyStatusForm,
    ContractTypeForm,
    TaskTypeForm,
    UserCreateForm,
    UserEditForm,
    ImportCompaniesForm,
    ImportTasksIcsForm,
    AmoApiConfigForm,
    AmoMigrateFilterForm,
    CompanyListColumnsForm,
)
from ui.models import UiGlobalConfig, AmoApiConfig, UiUserPreference

from amocrm.client import AmoApiError, AmoClient
from amocrm.migrate import fetch_amo_users, fetch_company_custom_fields, migrate_filtered
from crm.utils import require_admin, get_effective_user, get_view_as_user
from policy.decorators import policy_required
from policy.engine import decide as policy_decide
from django.core.exceptions import PermissionDenied
from ui.templatetags.ui_extras import format_phone
from ui.cleaners import clean_int_id

# Константы для фильтров
RESPONSIBLE_FILTER_NONE = "none"  # Значение для фильтрации компаний без ответственного
STRONG_CONFIRM_THRESHOLD = 200  # Порог, после которого для bulk переноса включается усиленное подтверждение (логируется как strong_confirm_required)


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


@login_required
def view_as_update(request: HttpRequest) -> HttpResponse:
    """
    Установить режим "просмотр как роль/филиал/пользователь" для администратора.
    При выборе конкретного пользователя применяются его реальные права.
    """
    user: User = request.user  # type: ignore[assignment]
    if not (user.is_superuser or user.role == User.Role.ADMIN):
        messages.error(request, "Доступ запрещён.")
        return redirect("dashboard")

    if request.method != "POST":
        return redirect(request.META.get("HTTP_REFERER") or "/")

    view_user_id = (request.POST.get("view_user_id") or "").strip()
    view_role = (request.POST.get("view_role") or "").strip()
    view_branch_id = (request.POST.get("view_as_branch_id") or request.POST.get("view_branch_id") or "").strip()

    # Приоритет: если выбран конкретный пользователь, используем его
    # и сбрасываем роль/филиал (они берутся из пользователя)
    if view_user_id:
        try:
            user_id = int(view_user_id)
            view_as_user = User.objects.filter(id=user_id, is_active=True).first()
            if view_as_user:
                request.session["view_as_user_id"] = user_id
                # Автоматически устанавливаем роль и филиал из выбранного пользователя
                request.session["view_as_role"] = view_as_user.role
                if view_as_user.branch_id:
                    request.session["view_as_branch_id"] = view_as_user.branch_id
                else:
                    request.session.pop("view_as_branch_id", None)
                # Сбрасываем старые настройки
                messages.success(request, f"Режим просмотра: от лица пользователя {view_as_user.get_full_name() or view_as_user.username}")
            else:
                request.session.pop("view_as_user_id", None)
        except (TypeError, ValueError):
            request.session.pop("view_as_user_id", None)
    else:
        # Если пользователь не выбран, работаем с ролью/филиалом как раньше
        request.session.pop("view_as_user_id", None)
        
        # Валидация и сохранение роли
        valid_roles = {choice[0] for choice in User.Role.choices}
        if view_role and view_role in valid_roles:
            request.session["view_as_role"] = view_role
        else:
            request.session.pop("view_as_role", None)

        # Валидация и сохранение филиала
        if view_branch_id:
            try:
                bid = int(view_branch_id)
                if Branch.objects.filter(id=bid).exists():
                    request.session["view_as_branch_id"] = bid
                else:
                    request.session.pop("view_as_branch_id", None)
            except (TypeError, ValueError):
                request.session.pop("view_as_branch_id", None)
        else:
            request.session.pop("view_as_branch_id", None)

    next_url = request.POST.get("next") or request.META.get("HTTP_REFERER") or "/"
    return redirect(next_url)


@login_required
def view_as_reset(request: HttpRequest) -> HttpResponse:
    """
    Сбросить режим "просмотр как" для администратора.
    """
    user: User = request.user  # type: ignore[assignment]
    if not (user.is_superuser or user.role == User.Role.ADMIN):
        messages.error(request, "Доступ запрещён.")
        return redirect("dashboard")

    request.session.pop("view_as_user_id", None)
    request.session.pop("view_as_role", None)
    request.session.pop("view_as_branch_id", None)

    return redirect(request.META.get("HTTP_REFERER") or "/")


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


def _normalize_phone_for_search(phone: str) -> str:
    """
    Нормализует номер телефона для поиска.
    Поддерживает различные форматы: 89123456789, +79123456789, 8(912)3456789 и т.д.
    """
    if not phone:
        return ""
    phone = str(phone).strip()
    # Убираем все нецифровые символы, кроме + в начале
    raw = phone.replace(" ", "").replace("-", "").replace("(", "").replace(")", "")
    normalized = "".join(ch for i, ch in enumerate(raw) if ch.isdigit() or (ch == "+" and i == 0))
    
    # Если номер уже в формате +7XXXXXXXXXX, оставляем как есть
    if normalized.startswith("+7") and len(normalized) == 12:
        return normalized
    else:
        # Приводим к формату +7XXXXXXXXXX для российских номеров
        digits = normalized[1:] if normalized.startswith("+") else normalized
        if digits.startswith("8") and len(digits) == 11:
            return "+7" + digits[1:]
        elif digits.startswith("7") and len(digits) == 11:
            return "+7" + digits
        elif len(digits) == 10:
            return "+7" + digits
        # Fallback для случаев, когда номер пришел без плюса, но с 11 цифрами и российским префиксом
        elif normalized and not normalized.startswith("+") and len(normalized) >= 11 and normalized[0] in ("7", "8"):
            return "+7" + normalized[1:]
        # Если не российский номер или не 10-11 цифр, оставляем как есть (с плюсом, если был)
        elif not normalized.startswith("+") and normalized:
            return "+" + normalized
    return normalized


def _normalize_for_search(text: str) -> str:
    """
    Нормализует текст для поиска: убирает тире, пробелы и другие разделители.
    Используется для поиска по названию, ИНН, адресу - чтобы находить совпадения
    даже если пользователь не помнит точное написание (например, с тире или без).
    """
    if not text:
        return ""
    # Убираем тире, дефисы, пробелы и другие разделители
    normalized = text.replace("-", "").replace("—", "").replace("–", "").replace(" ", "").replace("_", "")
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


def _cold_call_json(*, entity: str, entity_id: str, is_cold_call: bool, marked_at: datetime | None, marked_by: str, can_reset: bool, message: str) -> JsonResponse:
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

def _apply_company_filters(*, qs, params: dict, default_responsible_id: int | None = None):
    """
    Единые фильтры компаний для:
    - списка компаний
    - экспорта
    - массового переназначения (apply_mode=filtered)
    
    Если default_responsible_id указан и параметр responsible отсутствует в params,
    применяется фильтр по default_responsible_id.
    """
    # Безопасное извлечение строкового значения из параметров (может быть список)
    def _get_str_param(key: str, default: str = "") -> str:
        value = params.get(key, default)
        if isinstance(value, list):
            return (value[0] if value else default).strip()
        return (value or default).strip()
    
    q = _get_str_param("q")
    if q:
        # Нормализуем запрос для поиска (убираем тире, пробелы и т.д.)
        normalized_q = _normalize_for_search(q)
        tokens = _tokenize_search_query(q)
        
        # Базовые фильтры по полям компании (обычный поиск)
        # Для ИНН: ищем как подстроку в поле, а также по каждому отдельному ИНН из строки
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
        
        # Оптимизированный поиск с нормализацией для названия, ИНН, адреса
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

        # Токенизированный поиск: если в запросе несколько слов/токенов, ищем так, чтобы
        # ВСЕ токены встречались (в любых из основных полей компании). Это чинит кейсы вида:
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
        
        # Поиск по телефонам (с нормализацией) - оптимизировано с использованием Exists
        normalized_phone = _normalize_phone_for_search(q)
        phone_filters = Q()
        if normalized_phone and normalized_phone != q:
            # Основной телефон компании (точное совпадение быстрее)
            phone_filters = Q(phone=normalized_phone)
            # Также ищем по исходному запросу в основном телефоне
            phone_filters |= Q(phone__icontains=q)
            
            # Дополнительные телефоны компании - используем Exists вместо JOIN (быстрее)
            phone_filters |= Exists(
                CompanyPhone.objects.filter(
                    company_id=OuterRef('pk'),
                    value=normalized_phone
                )
            )
            phone_filters |= Exists(
                CompanyPhone.objects.filter(
                    company_id=OuterRef('pk'),
                    value__icontains=q
                )
            )
            
            # Телефоны контактов - используем Exists вместо JOIN (быстрее)
            phone_filters |= Exists(
                ContactPhone.objects.filter(
                    contact__company_id=OuterRef('pk'),
                    value=normalized_phone
                )
            )
            phone_filters |= Exists(
                ContactPhone.objects.filter(
                    contact__company_id=OuterRef('pk'),
                    value__icontains=q
                )
            )
        else:
            # Если нормализация не удалась, ищем как есть
            phone_filters = Q(phone__icontains=q)
            # Используем Exists для связанных таблиц
            phone_filters |= Exists(
                CompanyPhone.objects.filter(
                    company_id=OuterRef('pk'),
                    value__icontains=q
                )
            )
            phone_filters |= Exists(
                ContactPhone.objects.filter(
                    contact__company_id=OuterRef('pk'),
                    value__icontains=q
                )
            )
        
        # Поиск по email (с нормализацией) - оптимизировано с использованием Exists
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
                    contact__company_id=OuterRef('pk'),
                    value__iexact=normalized_email
                )
            )
            email_filters |= Exists(
                ContactEmail.objects.filter(
                    contact__company_id=OuterRef('pk'),
                    value__icontains=q
                )
            )
            
            # Дополнительные email компании - используем Exists
            email_filters |= Exists(
                CompanyEmail.objects.filter(
                    company_id=OuterRef('pk'),
                    value__iexact=normalized_email
                )
            )
            email_filters |= Exists(
                CompanyEmail.objects.filter(
                    company_id=OuterRef('pk'),
                    value__icontains=q
                )
            )
        else:
            email_filters = Q(email__icontains=q)
            # Используем Exists для связанных таблиц
            email_filters |= Exists(
                ContactEmail.objects.filter(
                    contact__company_id=OuterRef('pk'),
                    value__icontains=q
                )
            )
            email_filters |= Exists(
                CompanyEmail.objects.filter(
                    company_id=OuterRef('pk'),
                    value__icontains=q
                )
            )
        
        # Поиск по ФИО в контактах
        # Разбиваем запрос на слова для более гибкого поиска
        words = tokens or [w.strip().lower() for w in q.split() if w.strip()]
        fio_filters = Q()
        
        if len(words) > 1:
            # Если несколько слов, ищем контакты, где ВСЕ слова найдены (в любых полях одного контакта)
            # Используем Exists для проверки, что есть контакт компании, где все слова найдены
            # Создаем фильтр для контакта, где все слова найдены
            contact_q = Contact.objects.filter(company_id=OuterRef('pk'))
            # Для каждого слова создаем условие, что оно найдено в ФИО контакта
            # И все эти условия должны выполняться для одного контакта
            for word in words:
                contact_q = contact_q.filter(
                    Q(first_name__icontains=word)
                    | Q(last_name__icontains=word)
                )
            
            # Ищем компании, у которых есть такие контакты
            fio_filters = Exists(contact_q)
            # Также ищем по полному запросу в каждом поле (на случай, если ФИО хранится в одном поле)
            fio_filters |= Q(contacts__first_name__icontains=q)
            fio_filters |= Q(contacts__last_name__icontains=q)
        elif len(words) == 1:
            # Одно слово - используем Exists для оптимизации
            word = words[0]
            contact_q = Contact.objects.filter(
                company_id=OuterRef('pk')
            ).filter(
                Q(first_name__icontains=word) | Q(last_name__icontains=word)
            )
            fio_filters = Exists(contact_q)
        else:
            # Пустой запрос (не должно быть, но на всякий случай) - используем Exists
            contact_q = Contact.objects.filter(
                company_id=OuterRef('pk')
            ).filter(
                Q(first_name__icontains=q) | Q(last_name__icontains=q)
            )
            fio_filters = Exists(contact_q)
        
        # Объединяем все фильтры
        qs = qs.filter(
            base_filters
            | token_filters
            | phone_filters
            | email_filters
            | fio_filters
        ).distinct()

    responsible = _get_str_param("responsible")
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

    status = _get_str_param("status")
    if status:
        try:
            status_id = int(status)
            qs = qs.filter(status_id=status_id)
        except (ValueError, TypeError):
            # Некорректный ID - пропускаем фильтр
            pass

    branch = _get_str_param("branch")
    if branch:
        try:
            branch_id = int(branch)
            qs = qs.filter(branch_id=branch_id)
        except (ValueError, TypeError):
            # Некорректный ID - пропускаем фильтр
            pass

    sphere = _get_str_param("sphere")
    if sphere:
        try:
            sphere_id = int(sphere)
            qs = qs.filter(spheres__id=sphere_id)
        except (ValueError, TypeError):
            # Некорректный ID - пропускаем фильтр
            pass

    contract_type = _get_str_param("contract_type")
    if contract_type:
        try:
            contract_type_id = int(contract_type)
            qs = qs.filter(contract_type_id=contract_type_id)
        except (ValueError, TypeError):
            # Некорректный ID - пропускаем фильтр
            pass

    # Поддержка множественного выбора регионов.
    # params может быть как dict, так и QueryDict (request.GET / request.POST).
    def _get_list_param(key: str) -> list[str]:
        # QueryDict: корректно достаём все значения
        if hasattr(params, "getlist"):
            try:
                return [str(x) for x in (params.getlist(key) or [])]
            except Exception:
                return []
        # dict: значение может быть строкой или списком
        v = params.get(key, [])
        if isinstance(v, list):
            return [str(x) for x in v]
        if isinstance(v, str):
            return [v] if v else []
        return []

    region_values = _get_list_param("region")
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

    overdue = _get_str_param("overdue")
    if overdue == "1":
        qs = qs.filter(has_overdue=True)

    filter_active = any([q, responsible, status, branch, sphere, contract_type, region_ids, overdue == "1"])
    return {
        "qs": qs.distinct(),
        "q": q,
        "responsible": responsible,
        "status": status,
        "branch": branch,
        "sphere": sphere,
        "contract_type": contract_type,
        "region": region,  # Для обратной совместимости
        "selected_regions": selected_regions,  # Список выбранных регионов
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
    except Exception as e:
        from crm.request_id_middleware import get_request_id
        logger.warning(
            f"Ошибка при удалении параметра '{page_key}' из URL: {e}",
            exc_info=True,
            extra={"request_id": get_request_id()},
        )
    return params.urlencode()

@login_required
@policy_required(resource_type="page", resource="ui:dashboard")
def dashboard(request: HttpRequest) -> HttpResponse:
    """
    Dashboard (Рабочий стол) с оптимизированными запросами и кэшированием.
    Доступ проверяется по request.user (policy_required); отображаемые данные — по эффективному пользователю (режим просмотра).
    """
    from django.core.cache import cache

    # Эффективный пользователь для отображения данных (режим «просмотр как»). Права не меняются.
    user: User = get_effective_user(request)
    now = timezone.now()
    # Важно: при USE_TZ=True timezone.now() в UTC. Для фильтров "сегодня/неделя" считаем границы по локальной TZ.
    local_now = timezone.localtime(now)
    today_date = timezone.localdate(now)
    today_start = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
    tomorrow_start = today_start + timedelta(days=1)
    
    # "Задачи на неделю" в UI и тестах = ближайшие 7 дней, начиная с завтра (сегодня исключаем).
    week_monday = today_date + timedelta(days=1)
    week_sunday = week_monday + timedelta(days=6)
    week_start = tomorrow_start
    week_end = tomorrow_start + timedelta(days=7)
    
    contract_until_30 = today_date + timedelta(days=30)

    # Кэш-ключ: включает user_id и дату (инвалидируется при изменении дня)
    # ВРЕМЕННО ОТКЛЮЧАЕМ КЭШ ДЛЯ ОТЛАДКИ
    # cache_key = f"dashboard_{user.id}_{today_date.isoformat()}"
    # cached_data = cache.get(cache_key)
    # 
    # if cached_data:
    #     # Восстанавливаем datetime объекты из строк (кэш сериализует их)
    #     cached_data["now"] = now
    #     cached_data["local_now"] = local_now
    #     return render(request, "ui/dashboard.html", cached_data)

    # ОПТИМИЗАЦИЯ: один запрос на активные задачи, затем категоризация в Python
    all_tasks = (
        Task.objects.filter(assigned_to=user)
        .exclude(status__in=[Task.Status.DONE, Task.Status.CANCELLED])
        .select_related("company", "created_by", "type")
        .only(
            "id", "title", "status", "due_at", "created_at", "description", "type_id",
            "company__id", "company__name",
            "created_by__id", "created_by__first_name", "created_by__last_name",
            "type__id", "type__name", "type__color", "type__icon"
        )
        .order_by("-created_at")
    )

    # Разделяем задачи по категориям в Python
    tasks_today_list = []
    overdue_list = []
    tasks_week_list = []
    tasks_new_all = []  # NEW (для блока "Новые задачи")

    # Проходим по всем задачам и категоризируем их
    for task in all_tasks:
        # "Новые задачи" = только статус NEW
        if task.status == Task.Status.NEW:
            tasks_new_all.append(task)

        if task.due_at is None:
            continue

        task_due_local = timezone.localtime(task.due_at)

        # Просроченные = дедлайн раньше начала сегодняшнего дня (не относительно текущего времени)
        if task_due_local < today_start:
            overdue_list.append(task)
            continue

        # На сегодня (в пределах календарного дня)
        if today_start <= task_due_local < tomorrow_start:
            tasks_today_list.append(task)
            continue

        # На неделю (ближайшие 7 дней начиная с завтра)
        if week_start <= task_due_local < week_end:
            tasks_week_list.append(task)

    # Сортируем:
    # - Просроченные: по due_at (самые старые первыми)
    # - На сегодня: по due_at (раньше первыми)
    # - На неделю: по due_at (раньше первыми)
    # - Новые: по created_at (новые первыми)
    overdue_list.sort(key=lambda t: t.due_at or timezone.now())
    tasks_today_list.sort(key=lambda t: t.due_at or timezone.now())
    tasks_week_list.sort(key=lambda t: t.due_at or timezone.now())
    tasks_new_all.sort(key=lambda t: t.created_at or timezone.now(), reverse=True)
    
    # Подсчитываем общие количества
    overdue_count = len(overdue_list)
    tasks_today_count = len(tasks_today_list)
    tasks_week_count = len(tasks_week_list)
    tasks_new_count = len(tasks_new_all)
    
    # Ограничиваем до 3 для отображения на dashboard
    overdue_list = overdue_list[:3]  # 3 самых просроченных
    tasks_today_list = tasks_today_list[:3]
    tasks_week_list = tasks_week_list[:3]
    tasks_new_list = tasks_new_all[:3]

    # Договоры: для обычных - по сроку (<= 30 дней), для годовых - по сумме
    # Обычные договоры по сроку
    contracts_soon_qs = (
        Company.objects.filter(responsible=user, contract_until__isnull=False)
        .exclude(contract_type__is_annual=True)  # Исключаем годовые
        .filter(contract_until__gte=today_date, contract_until__lte=contract_until_30)
        .select_related("contract_type")
        .only("id", "name", "contract_type", "contract_until")
        .order_by("contract_until", "name")[:50]
    )
    contracts_soon = []
    for c in contracts_soon_qs:
        days_left = (c.contract_until - today_date).days if c.contract_until else None
        if days_left is not None and c.contract_type:
            # Используем настройки из ContractType
            warning_days = c.contract_type.warning_days
            danger_days = c.contract_type.danger_days
            if days_left <= danger_days:
                level = "danger"
            elif days_left <= warning_days:
                level = "warn"
            else:
                level = None  # Не показываем, если больше warning_days
        else:
            # Fallback на старую логику, если нет contract_type
            level = "danger" if (days_left is not None and days_left < 14) else "warn" if days_left is not None else None
        
        if level:  # Добавляем только если есть предупреждение
            contracts_soon.append({"company": c, "days_left": days_left, "level": level, "is_annual": False})
    
    # Годовые договоры: показываем все (с суммой и без), чтобы можно было ввести/редактировать
    annual_contracts_qs = (
        Company.objects.filter(responsible=user, contract_type__is_annual=True)
        .select_related("contract_type")
        .only("id", "name", "contract_type", "contract_amount")
        .order_by("contract_amount", "name")[:50]
    )
    for c in annual_contracts_qs:
        amount = c.contract_amount
        # Нет суммы или меньше 25 000 — красный, меньше 70 000 — оранжевый, больше — не показываем в блоке предупреждений
        if amount is None:
            level = "warn"  # напомнить указать сумму
        elif amount < 25000:
            level = "danger"
        elif amount < 70000:
            level = "warn"
        else:
            level = None
        if level:
            contracts_soon.append({"company": c, "amount": amount, "level": level, "is_annual": True})

    # Сопоставляем задачи без типа с TaskType по точному совпадению названия
    # Загружаем все TaskType для сопоставления
    from tasksapp.models import TaskType
    task_types_by_name = {tt.name: tt for tt in TaskType.objects.all()}
    
    # Добавляем права доступа к задачам для модального окна
    # Подготавливаем задачи с правами доступа и сопоставляем с TaskType
    for task_list in [tasks_new_list, tasks_today_list, overdue_list, tasks_week_list]:
        for task in task_list:
            # Если у задачи нет типа, но есть title, проверяем точное совпадение с TaskType
            if not task.type and task.title and task.title in task_types_by_name:
                task_type = task_types_by_name[task.title]
                task.type = task_type  # type: ignore[assignment]
                # Сохраняем связь в БД для будущих запросов
                task.type_id = task_type.id  # type: ignore[attr-defined]
                # Сохраняем в БД, чтобы не делать это каждый раз
                Task.objects.filter(id=task.id).update(type_id=task_type.id)
            
            task.can_manage_status = _can_manage_task_status_ui(user, task)  # type: ignore[attr-defined]
            task.can_edit_task = _can_edit_task_ui(user, task)  # type: ignore[attr-defined]
            task.can_delete_task = _can_delete_task_ui(user, task)  # type: ignore[attr-defined]

    # Запросы на удаление компаний для РОП/директора
    deletion_requests = []
    deletion_requests_count = 0
    if user.role in (User.Role.SALES_HEAD, User.Role.BRANCH_DIRECTOR) and user.branch_id:
        from companies.models import CompanyDeletionRequest
        deletion_requests_qs = (
            CompanyDeletionRequest.objects.filter(
                status=CompanyDeletionRequest.Status.PENDING,
                requested_by_branch_id=user.branch_id,
            )
            .select_related("requested_by", "company")
            .order_by("-created_at")[:10]
        )
        deletion_requests = list(deletion_requests_qs)
        deletion_requests_count = CompanyDeletionRequest.objects.filter(
            status=CompanyDeletionRequest.Status.PENDING,
            requested_by_branch_id=user.branch_id,
        ).count()

    context = {
        "now": now,
        "local_now": local_now,
        "today_start": today_start,
        "tasks_new": tasks_new_list,
        "tasks_today": tasks_today_list,
        "overdue": overdue_list,
        "tasks_week": tasks_week_list,
        "contracts_soon": contracts_soon,
        "can_view_cold_call_reports": _can_view_cold_call_reports(user),
        # Общие количества для кнопок "Посмотреть все"
        "tasks_new_count": tasks_new_count,
        "tasks_today_count": tasks_today_count,
        "overdue_count": overdue_count,
        "tasks_week_count": tasks_week_count,
        # Диапазон дат для "Задачи на неделю"
        "week_monday": week_monday,
        "week_sunday": week_sunday,
        # Запросы на удаление
        "deletion_requests": deletion_requests,
        "deletion_requests_count": deletion_requests_count,
    }

    # ВРЕМЕННО ОТКЛЮЧАЕМ КЭШ ДЛЯ ОТЛАДКИ
    # cache.set(cache_key, context, timeout=120)
    
    return render(request, "ui/dashboard.html", context)


@login_required
@policy_required(resource_type="action", resource="ui:dashboard")
def dashboard_poll(request: HttpRequest) -> JsonResponse:
    """
    AJAX polling endpoint для обновления dashboard.
    Данные фильтруются по эффективному пользователю (режим просмотра).
    """
    user: User = get_effective_user(request)
    since = request.GET.get('since')
    
    if since:
        try:
            since_dt = datetime.fromtimestamp(int(since) / 1000, tz=timezone.utc)
            # Проверяем, были ли изменения после since_dt
            has_changes = (
                Task.objects.filter(
                    assigned_to=user,
                    updated_at__gt=since_dt
                ).exists() or
                Company.objects.filter(
                    responsible=user,
                    updated_at__gt=since_dt
                ).exists()
            )
            if not has_changes:
                return JsonResponse({"updated": False})
        except (ValueError, TypeError) as e:
            from crm.request_id_middleware import get_request_id
            logger.warning(
                f"Некорректный параметр 'since' в dashboard_poll: {since}",
                exc_info=True,
                extra={"user_id": user.id, "since": since, "request_id": get_request_id()},
            )
            # Если since некорректный, возвращаем полные данные
    
    # Возвращаем обновлённые данные (используем ту же логику, что и в dashboard)
    now = timezone.now()
    local_now = timezone.localtime(now)
    today_date = timezone.localdate(now)
    today_start = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
    tomorrow_start = today_start + timedelta(days=1)
    
    # "Задачи на неделю" = ближайшие 7 дней, начиная с завтра
    week_monday = today_date + timedelta(days=1)
    week_sunday = week_monday + timedelta(days=6)
    week_start = tomorrow_start
    week_end = tomorrow_start + timedelta(days=7)
    
    contract_until_30 = today_date + timedelta(days=30)

    # Получаем все активные задачи одним запросом
    all_tasks = (
        Task.objects.filter(assigned_to=user)
        .exclude(status__in=[Task.Status.DONE, Task.Status.CANCELLED])
        .select_related("company", "created_by")
        .order_by("due_at", "-created_at")
    )

    tasks_today_list = []
    overdue_list = []
    tasks_week_list = []
    tasks_new_list = []
    tasks_new_all = []  # Все задачи для блока "Задачи" (кроме просроченных)

    # Проходим по всем задачам и категоризируем их (та же логика, что и в dashboard)
    for task in all_tasks:
        if task.status == Task.Status.NEW:
            tasks_new_all.append(task)
        if task.due_at is None:
            continue
        task_due_local = timezone.localtime(task.due_at)
        if task_due_local < today_start:
            overdue_list.append(task)
            continue
        if today_start <= task_due_local < tomorrow_start:
            tasks_today_list.append(task)
            continue
        if week_start <= task_due_local < week_end:
            tasks_week_list.append(task)

    # Сортируем:
    # - Просроченные: по дате (самые старые первыми)
    # - На сегодня: по ближайшему дедлайну
    # - На неделю: по ближайшему дедлайну
    # - Задачи: по ближайшему дедлайну (ближайшие первыми), затем по created_at
    overdue_list.sort(key=lambda t: t.due_at or timezone.now())
    tasks_today_list.sort(key=lambda t: t.due_at or timezone.now())
    tasks_week_list.sort(key=lambda t: t.due_at or timezone.now())
    tasks_new_all.sort(key=lambda t: t.created_at or timezone.now(), reverse=True)
    
    # Ограничиваем для JSON ответа
    overdue_list = overdue_list[:20]
    tasks_today_list = tasks_today_list[:20]
    tasks_week_list = tasks_week_list[:50]
    tasks_new_list = tasks_new_all[:20]

    # Договоры: для обычных - по сроку (<= 30 дней), для годовых - по сумме
    # Обычные договоры по сроку
    contracts_soon_qs = (
        Company.objects.filter(responsible=user, contract_until__isnull=False)
        .exclude(contract_type__is_annual=True)  # Исключаем годовые
        .filter(contract_until__gte=today_date, contract_until__lte=contract_until_30)
        .select_related("contract_type")
        .only("id", "name", "contract_type", "contract_until")
        .order_by("contract_until", "name")[:50]
    )
    contracts_soon = []
    for c in contracts_soon_qs:
        days_left = (c.contract_until - today_date).days if c.contract_until else None
        if days_left is not None and c.contract_type:
            # Используем настройки из ContractType
            warning_days = c.contract_type.warning_days
            danger_days = c.contract_type.danger_days
            if days_left <= danger_days:
                level = "danger"
            elif days_left <= warning_days:
                level = "warn"
            else:
                level = None  # Не показываем, если больше warning_days
        else:
            # Fallback на старую логику, если нет contract_type
            level = "danger" if (days_left is not None and days_left < 14) else "warn" if days_left is not None else None
        
        if level:  # Добавляем только если есть предупреждение
            contracts_soon.append({
                "company_id": str(c.id),
                "company_name": c.name,
                "contract_type": c.contract_type.name if c.contract_type else "",
                "is_annual": False,
                "days_left": days_left,
                "level": level,
            })
    
    # Годовые договоры: показываем все (с суммой и без)
    annual_contracts_qs = (
        Company.objects.filter(responsible=user, contract_type__is_annual=True)
        .select_related("contract_type")
        .only("id", "name", "contract_type", "contract_amount")
        .order_by("contract_amount", "name")[:50]
    )
    for c in annual_contracts_qs:
        amount = c.contract_amount
        if amount is None:
            level = "warn"
        elif amount < 25000:
            level = "danger"
        elif amount < 70000:
            level = "warn"
        else:
            level = None
        if level:
            contracts_soon.append({
                "company_id": str(c.id),
                "company_name": c.name,
                "contract_type": c.contract_type.name if c.contract_type else "",
                "is_annual": True,
                "amount": float(amount) if amount is not None else None,
                "level": level,
            })

    # Сопоставляем задачи без типа с TaskType по точному совпадению названия
    from tasksapp.models import TaskType
    task_types_by_name = {tt.name: tt for tt in TaskType.objects.all()}
    
    # Применяем сопоставление ко всем задачам и добавляем права доступа
    tasks_to_update = []
    for task_list in [tasks_new_list, tasks_today_list, overdue_list, tasks_week_list]:
        for task in task_list:
            if not task.type and task.title and task.title in task_types_by_name:
                task_type = task_types_by_name[task.title]
                task.type = task_type  # type: ignore[assignment]
                task.type_id = task_type.id  # type: ignore[attr-defined]
                tasks_to_update.append(task.id)
            
            # Добавляем права доступа к задачам (для кнопок редактирования/удаления)
            task.can_manage_status = _can_manage_task_status_ui(user, task)  # type: ignore[attr-defined]
            task.can_edit_task = _can_edit_task_ui(user, task)  # type: ignore[attr-defined]
            task.can_delete_task = _can_delete_task_ui(user, task)  # type: ignore[attr-defined]
    
    # Сохраняем в БД пакетно для оптимизации
    if tasks_to_update:
        for task_id in tasks_to_update:
            task = next((t for task_list in [tasks_new_list, tasks_today_list, overdue_list, tasks_week_list] for t in task_list if t.id == task_id), None)
            if task and task.type_id:
                Task.objects.filter(id=task_id).update(type_id=task.type_id)
    
    # Сериализуем задачи для JSON
    def serialize_task(task):
        return {
            "id": str(task.id),
            "title": task.title,
            "status": task.status,
            "due_at": task.due_at.isoformat() if task.due_at else None,
            "company_id": str(task.company.id) if task.company else None,
            "company_name": task.company.name if task.company else None,
            "created_at": task.created_at.isoformat(),
            "created_by": str(task.created_by) if task.created_by else None,
        }

    return JsonResponse({
        "updated": True,
        "timestamp": int(now.timestamp() * 1000),
        "tasks_today": [serialize_task(t) for t in tasks_today_list],
        "overdue": [serialize_task(t) for t in overdue_list],
        "tasks_week": [serialize_task(t) for t in tasks_week_list],
        "tasks_new": [serialize_task(t) for t in tasks_new_list],
        "contracts_soon": contracts_soon,
    })


@login_required
@policy_required(resource_type="action", resource="ui:dashboard")
def dashboard_sse(request: HttpRequest) -> StreamingHttpResponse:
    """
    Server-Sent Events endpoint для live updates dashboard.
    События строятся по данным эффективного пользователя (режим просмотра).
    """
    import json
    import time

    user: User = get_effective_user(request)
    
    def event_stream():
        last_check = timezone.now()
        sent_initial = False
        
        while True:
            try:
                # Проверяем изменения каждые 5 секунд
                time.sleep(5)
                
                now = timezone.now()
                
                # Проверяем, были ли изменения
                has_changes = (
                    Task.objects.filter(
                        assigned_to=user,
                        updated_at__gt=last_check
                    ).exists() or
                    Company.objects.filter(
                        responsible=user,
                        updated_at__gt=last_check
                    ).exists()
                )
                
                if has_changes or not sent_initial:
                    # Отправляем событие обновления
                    data = {
                        "type": "update",
                        "timestamp": int(now.timestamp() * 1000),
                    }
                    yield f"data: {json.dumps(data)}\n\n"
                    last_check = now
                    sent_initial = True
                else:
                    # Отправляем heartbeat
                    yield f"data: {json.dumps({'type': 'heartbeat'})}\n\n"
                    
            except GeneratorExit:
                break
            except Exception as e:
                # Отправляем ошибку и закрываем соединение
                error_data = {"type": "error", "message": str(e)}
                yield f"data: {json.dumps(error_data)}\n\n"
                break
    
    response = StreamingHttpResponse(event_stream(), content_type='text/event-stream')
    response['Cache-Control'] = 'no-cache'
    response['X-Accel-Buffering'] = 'no'  # Отключаем буферизацию в nginx
    return response


@login_required
@policy_required(resource_type="page", resource="ui:analytics")
def analytics(request: HttpRequest) -> HttpResponse:
    """
    Аналитика по звонкам/отметкам для руководителей.
    Доступ только по реальному пользователю; список и данные — по эффективному (режим просмотра).
    """
    if not (request.user.is_superuser or request.user.role in (User.Role.ADMIN, User.Role.GROUP_MANAGER, User.Role.BRANCH_DIRECTOR, User.Role.SALES_HEAD)):
        messages.error(request, "Нет доступа к аналитике.")
        return redirect("dashboard")

    user: User = get_effective_user(request)
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
    # Используем get_users_for_lists для исключения администраторов и группировки по филиалам
    if user.is_superuser or user.role in (User.Role.ADMIN, User.Role.GROUP_MANAGER):
        users_qs = get_users_for_lists(user).filter(role__in=[User.Role.MANAGER, User.Role.SALES_HEAD, User.Role.BRANCH_DIRECTOR])
    else:
        users_qs = get_users_for_lists(user).filter(branch_id=user.branch_id, role__in=[User.Role.MANAGER, User.Role.SALES_HEAD, User.Role.BRANCH_DIRECTOR])
    users_list = list(users_qs)
    user_ids = [u.id for u in users_list]

    # Звонки за период (лимит на страницу, чтобы не убить UI)
    # Для консистентности с аналитикой сотрудника считаем только клики "Позвонить с телефона" (note="UI click").
    calls_qs_base = (
        CallRequest.objects.filter(created_by_id__in=user_ids, created_at__gte=start, created_at__lt=end, note="UI click")
        .exclude(status=CallRequest.Status.CANCELLED)
        .select_related("company", "contact", "created_by")
    )

    # Полный QS для вычисления холодных звонков (без среза)
    cold_call_ids = set(
        calls_qs_base.filter(is_cold_call=True).filter(_cold_call_confirm_q()).values_list("id", flat=True)
    )

    # Ограничиваем только отображаемый список
    calls_qs = calls_qs_base.order_by("-created_at")[:5000]

    stats = {uid: {"calls_total": 0, "cold_calls": 0} for uid in user_ids}

    for call in calls_qs:
        uid = call.created_by_id
        if uid not in stats:
            continue
        stats[uid]["calls_total"] += 1
        if call.id in cold_call_ids:
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
@policy_required(resource_type="page", resource="ui:help")
def help_page(request: HttpRequest) -> HttpResponse:
    """Страница помощи - ролики, FAQ, инструкции."""
    return render(request, "ui/help.html")


@login_required
@policy_required(resource_type="page", resource="ui:preferences")
def preferences(request: HttpRequest) -> HttpResponse:
    """
    Настройки пользователя (не админские).
    Здесь собраны страницы, которые доступны всем ролям и позволяют что-то "донастроить".
    """
    return render(
        request,
        "ui/preferences.html",
        {
            "user": request.user,
        },
    )


@login_required
@policy_required(resource_type="page", resource="ui:preferences")
def preferences_ui(request: HttpRequest) -> HttpResponse:
    """
    Настройки интерфейса (персональные): масштаб шрифта и т.п.
    """
    user = request.user

    if request.method == "POST":
        scale_raw = (request.POST.get("font_scale") or "").strip().replace(",", ".")
        try:
            scale = float(scale_raw)
        except Exception:
            scale = None

        if scale is None or not (0.90 <= scale <= 1.15):
            messages.error(request, "Некорректный масштаб. Допустимо от 90% до 115%.")
            return redirect("preferences_ui")

        prefs = UiUserPreference.load_for_user(user)
        prefs.font_scale = Decimal(f"{scale:.2f}")
        prefs.save(update_fields=["font_scale", "updated_at"])
        try:
            request.session["ui_font_scale"] = float(prefs.font_scale_float())
        except Exception:
            pass
        messages.success(request, "Настройки интерфейса сохранены.")
        return redirect("preferences_ui")

    prefs = UiUserPreference.load_for_user(user)
    font_scale = prefs.font_scale_float()
    return render(
        request,
        "ui/preferences_ui.html",
        {
            "user": user,
            "ui_font_scale_value": font_scale,
        },
    )


@login_required
@policy_required(resource_type="action", resource="ui:preferences")
def preferences_company_detail_view_mode(request: HttpRequest) -> JsonResponse:
    """
    AJAX endpoint для сохранения режима просмотра карточки компании.
    POST: {"view_mode": "classic" | "modern"}
    """
    if request.method != "POST":
        return JsonResponse({"success": False, "error": "Метод не разрешен."}, status=405)

    user = request.user
    view_mode = (request.POST.get("view_mode") or "").strip().lower()

    if view_mode not in ["classic", "modern"]:
        return JsonResponse({"success": False, "error": "Некорректный режим просмотра."}, status=400)

    prefs = UiUserPreference.load_for_user(user)
    prefs.company_detail_view_mode = view_mode
    prefs.save(update_fields=["company_detail_view_mode", "updated_at"])

    # Сохраняем в session для быстрого доступа
    try:
        request.session["company_detail_view_mode"] = view_mode
    except Exception:
        pass

    return JsonResponse({"success": True, "view_mode": view_mode})


@login_required
@policy_required(resource_type="page", resource="ui:preferences")
def preferences_mail(request: HttpRequest) -> HttpResponse:
    """
    Почтовые настройки/разделы.
    """
    return render(
        request,
        "ui/preferences_mail.html",
        {
            "user": request.user,
        },
    )


@login_required
@policy_required(resource_type="page", resource="ui:analytics")
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

    # Холодные звонки (строгая логика, включая отметки на телефонах):
    # - звонок инициирован через кнопку (note="UI click")
    # - у звонка is_cold_call=True
    # - и именно этот звонок был подтверждён отметкой (FK marked_call) на компании, контакте или их телефонах
    cold_calls_qs = (
        calls_qs.filter(is_cold_call=True)
        .filter(_cold_call_confirm_q())
        .order_by("-created_at")
        .distinct()
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


def _cold_call_confirm_q() -> Q:
    """
    Условие "подтвержденный холодный звонок":
    - is_cold_call=True на CallRequest
    - и этот звонок записан как marked_call либо на компании, либо на контакте,
      либо на их телефонах (CompanyPhone/ContactPhone).
    """
    return Q(
        Q(company__primary_cold_marked_call_id=F("id"))
        | Q(contact__cold_marked_call_id=F("id"))
        | Q(company__phones__cold_marked_call_id=F("id"))
        | Q(contact__phones__cold_marked_call_id=F("id"))
    )


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
@policy_required(resource_type="page", resource="ui:analytics")
def cold_calls_report_day(request: HttpRequest) -> JsonResponse:
    user: User = request.user
    if not _can_view_cold_call_reports(user):
        return JsonResponse({"ok": False, "detail": "forbidden"}, status=403)

    # Поддерживаем выбор дня через параметр ?date=YYYY-MM-DD, по умолчанию сегодня.
    date_str = (request.GET.get("date") or "").strip()
    try:
        if date_str:
            target_date = datetime.strptime(date_str, "%Y-%m-%d").date()
        else:
            target_date = timezone.localdate(timezone.now())
    except (ValueError, TypeError):
        target_date = timezone.localdate(timezone.now())

    day_start = timezone.make_aware(datetime.combine(target_date, datetime.min.time()))
    day_end = day_start + timedelta(days=1)
    day_label = target_date.strftime("%d.%m.%Y")

    # Строгая логика холодных звонков:
    # - инициированы через кнопку "Позвонить с телефона" (note="UI click")
    # - is_cold_call=True
    # - и подтверждены отметкой (marked_call) в допустимое окно (проверяется в момент отметки)
    qs_base = (
        CallRequest.objects.filter(created_by=user, created_at__gte=day_start, created_at__lt=day_end, note="UI click")
        .exclude(status=CallRequest.Status.CANCELLED)
        .select_related("company", "contact")
    )
    qs = (
        qs_base.filter(is_cold_call=True)
        .filter(_cold_call_confirm_q())
        .order_by("created_at")
        .distinct()
    )
    
    # Дополнительные метрики для ежедневного отчета менеджеров
    # 1. Общее количество входящих звонков
    incoming_calls_count = (
        CallRequest.objects.filter(
            created_by=user,
            created_at__gte=day_start,
            created_at__lt=day_end,
            direction=CallRequest.CallDirection.INCOMING
        )
        .exclude(status=CallRequest.Status.CANCELLED)
        .count()
    )
    
    # 2. Количество новых компаний
    new_companies_count = Company.objects.filter(
        created_by=user,
        created_at__gte=day_start,
        created_at__lt=day_end
    ).count()
    
    # 3. Количество новых контактов (в компаниях, где пользователь ответственный)
    new_contacts_count = Contact.objects.filter(
        company__responsible=user,
        created_at__gte=day_start,
        created_at__lt=day_end
    ).count()
    
    items = []
    lines = [
        f"Отчёт: ежедневная статистика за {day_label}",
        "",
        "Холодные звонки:",
        f"  Всего: {qs.count()}",
        "",
        "Общая статистика:",
        f"  Общее количество звонков (входящие), шт: {incoming_calls_count}",
        f"  Количество новых компаний, шт: {new_companies_count}",
        f"  Количество новых контактов, шт: {new_contacts_count}",
        "",
        "Детализация холодных звонков:",
        ""
    ]
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

    return JsonResponse({
        "ok": True,
        "range": "day",
        "date": day_label,
        "count": len(items),
        "items": items,
        "text": "\n".join(lines),
        "stats": {
            "cold_calls": qs.count(),
            "incoming_calls": incoming_calls_count,
            "new_companies": new_companies_count,
            "new_contacts": new_contacts_count,
        },
    })


@login_required
@policy_required(resource_type="page", resource="ui:analytics")
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
            .filter(_cold_call_confirm_q())
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
    qs = (
        qs_base.filter(is_cold_call=True)
        .filter(_cold_call_confirm_q())
        .order_by("created_at")
        .distinct()
    )
    
    # Дополнительные метрики для месячного отчета менеджеров
    month_start_aware = timezone.make_aware(datetime.combine(selected, datetime.min.time()))
    month_end_aware = timezone.make_aware(datetime.combine(month_end, datetime.min.time()))
    
    # 1. Общее количество входящих звонков
    incoming_calls_count = (
        CallRequest.objects.filter(
            created_by=user,
            created_at__gte=month_start_aware,
            created_at__lt=month_end_aware,
            direction=CallRequest.CallDirection.INCOMING
        )
        .exclude(status=CallRequest.Status.CANCELLED)
        .count()
    )
    
    # 2. Количество новых компаний
    new_companies_count = Company.objects.filter(
        created_by=user,
        created_at__gte=month_start_aware,
        created_at__lt=month_end_aware
    ).count()
    
    # 3. Количество новых контактов (в компаниях, где пользователь ответственный)
    new_contacts_count = Contact.objects.filter(
        company__responsible=user,
        created_at__gte=month_start_aware,
        created_at__lt=month_end_aware
    ).count()

    items = []
    lines = [
        f"Отчёт: месячная статистика за {_month_label(selected)}",
        "",
        "Холодные звонки:",
        f"  Всего: {qs.count()}",
        "",
        "Общая статистика:",
        f"  Общее количество звонков (входящие), шт: {incoming_calls_count}",
        f"  Количество новых компаний, шт: {new_companies_count}",
        f"  Количество новых контактов, шт: {new_contacts_count}",
        "",
        "Детализация холодных звонков:",
        ""
    ]
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
            "stats": {
                "cold_calls": qs.count(),
                "incoming_calls": incoming_calls_count,
                "new_companies": new_companies_count,
                "new_contacts": new_contacts_count,
            },
        }
    )


@login_required
@policy_required(resource_type="page", resource="ui:analytics")
def cold_calls_report_last_7_days(request: HttpRequest) -> JsonResponse:
    """
    Сводка по холодным звонкам за последние 7 дней (включая сегодня) для текущего пользователя:
    список дней с количеством, чтобы UI мог дать выбор даты.
    """
    user: User = request.user
    if not _can_view_cold_call_reports(user):
        return JsonResponse({"ok": False, "detail": "forbidden"}, status=403)

    today = timezone.localdate(timezone.now())
    start_date = today - timedelta(days=6)
    days = []
    total = 0
    for i in range(7):
        d = start_date + timedelta(days=i)
        day_start = timezone.make_aware(datetime.combine(d, datetime.min.time()))
        day_end = day_start + timedelta(days=1)
        qs_base = (
            CallRequest.objects.filter(created_by=user, created_at__gte=day_start, created_at__lt=day_end, note="UI click")
            .exclude(status=CallRequest.Status.CANCELLED)
        )
        cnt = (
            qs_base.filter(is_cold_call=True)
            .filter(_cold_call_confirm_q())
            .distinct()
            .count()
        )
        total += cnt
        days.append(
            {
                "date": d.strftime("%Y-%m-%d"),
                "label": d.strftime("%d.%m.%Y"),
                "count": cnt,
            }
        )

    period_label = f"{start_date.strftime('%d.%m.%Y')} — {today.strftime('%d.%m.%Y')}"
    return JsonResponse({"ok": True, "range": "last_7_days", "period": period_label, "total": total, "days": days})


@login_required
@policy_required(resource_type="page", resource="ui:companies:list")
def company_list(request: HttpRequest) -> HttpResponse:
    # Реальный пользователь для проверки прав (is_admin и т.д.)
    user: User = request.user
    # Эффективный пользователь для списков «Ответственный»/«Кому передать» в режиме просмотра
    effective_user: User = get_effective_user(request)
    now = timezone.now()
    # Просмотр компаний: всем доступна вся база (без ограничения по филиалу/scope).
    # Кэшируем общее количество компаний (TTL 10 минут)
    from django.core.cache import cache

    # Кэш-ключ должен учитывать пользователя и режим view_as (только если режим включён)
    view_as_user = get_view_as_user(request)
    effective_user_id = view_as_user.id if view_as_user else user.id
    view_as_enabled = request.session.get("view_as_enabled", False)
    view_as_role = request.session.get("view_as_role") if view_as_enabled else None
    view_as_branch_id = None
    if view_as_enabled and request.session.get("view_as_branch_id"):
        try:
            view_as_branch_id = int(request.session.get("view_as_branch_id"))
        except (TypeError, ValueError):
            view_as_branch_id = None
    
    # Создаем уникальный ключ кэша с учетом прав доступа
    cache_key_parts = ["companies_total_count", str(effective_user_id)]
    if view_as_role:
        cache_key_parts.append(f"role_{view_as_role}")
    if view_as_branch_id:
        cache_key_parts.append(f"branch_{view_as_branch_id}")
    cache_key_total = "_".join(cache_key_parts)
    
    companies_total = cache.get(cache_key_total)
    if companies_total is None:
        # Применяем те же фильтры, что могут быть в view_as
        qs = Company.objects.all()
        if view_as_branch_id:
            qs = qs.filter(branch_id=view_as_branch_id)
        companies_total = qs.order_by().count()
        # TTL 60 секунд для быстрой инвалидации при изменении прав/назначений
        cache.set(cache_key_total, companies_total, 60)
    # Оптимизация: предзагружаем только необходимые связанные объекты
    qs = (
        _companies_with_overdue_flag(now=now)
        .select_related("responsible", "branch", "status", "region")
        .prefetch_related("spheres")
    )
    # Ранее здесь были разные фильтры по умолчанию в зависимости от роли (ответственный/филиал).
    # По запросу заказчика убираем предустановленные фильтры: всем пользователям показываем полный список,
    # пока они сами явно не выберут фильтры в интерфейсе.
    #
    # Важно: QueryDict может содержать несколько значений для одного ключа (например, region=1&region=2),
    # поэтому используем .lists(), чтобы не потерять мультивыбор.
    q = (request.GET.get("q") or "").strip()
    filter_params = {k: v for k, v in request.GET.lists()}
    # Применяем ВСЕ фильтры, кроме поиска (поиск теперь через SearchService).
    filter_params_wo_search = dict(filter_params)
    filter_params_wo_search.pop("q", None)
    f = _apply_company_filters(qs=qs, params=filter_params_wo_search, default_responsible_id=None)
    qs = f["qs"]
    if q:
        from companies.search_service import get_company_search_backend
        qs = get_company_search_backend().apply(qs=qs, query=q)

    # Sorting (asc/desc) — как в задачах
    sort_raw = (request.GET.get("sort") or "").strip()
    sort = sort_raw or "updated_at"
    direction = (request.GET.get("dir") or "").strip().lower() or "desc"
    direction = "asc" if direction == "asc" else "desc"
    sort_map = {
        "updated_at": "updated_at",
        "name": "name",
        "inn": "inn",
        "status": "status__name",
        "responsible": "responsible__last_name",
        "branch": "branch__name",
        "region": "region__name",
    }
    sort_field = sort_map.get(sort, "updated_at")
    if sort == "responsible":
        order = [sort_field, "responsible__first_name", "name"]
    else:
        order = [sort_field, "name"]
    if direction == "desc":
        order = [f"-{f}" for f in order]
    # Если пользователь не выбирал сортировку и есть q — приоритет релевантности (сервис уже отсортировал).
    if not (q and not sort_raw):
        qs = qs.order_by(*order)

    companies_filtered = qs.order_by().count()
    filter_active = bool(q) or f["filter_active"]

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
    
    # Оптимизация: пакетная проверка прав на передачу вместо проверки для каждой компании
    company_ids = [c.id for c in page.object_list]
    transfer_check = can_transfer_companies(user, company_ids)
    allowed_ids_set = set(transfer_check["allowed"])
    # Добавляем флаг can_transfer для каждой компании (для UI проверки)
    for company in page.object_list:
        company.can_transfer = company.id in allowed_ids_set  # type: ignore[attr-defined]

    # match_reasons + подсветка (детерминированно, без JS-regex по innerHTML)
    if q:
        try:
            from companies.search_service import get_company_search_backend
            explain_map = get_company_search_backend().explain(companies=list(page.object_list), query=q)
            for company in page.object_list:
                ex = explain_map.get(company.id)
                if not (ex and ex.reasons):
                    single_map = get_company_search_backend().explain(companies=[company], query=q)
                    ex_one = single_map.get(company.id) if single_map else None
                    if ex_one and ex_one.reasons:
                        company.search_name_html = ex_one.name_html  # type: ignore[attr-defined]
                        company.search_inn_html = ex_one.inn_html  # type: ignore[attr-defined]
                        company.search_address_html = ex_one.address_html  # type: ignore[attr-defined]
                        company.search_reasons = ex_one.reasons  # type: ignore[attr-defined]
                        company.search_reasons_total = ex_one.reasons_total  # type: ignore[attr-defined]
                    else:
                        from companies.search_index import parse_query
                        from companies.search_service import SearchReason, highlight_html
                        pq = parse_query(q)
                        dig = pq.strong_digit_tokens + pq.weak_digit_tokens
                        company.search_name_html = highlight_html(company.name or "", text_tokens=pq.text_tokens, digit_tokens=dig)  # type: ignore[attr-defined]
                        company.search_inn_html = highlight_html(company.inn or "", text_tokens=pq.text_tokens, digit_tokens=dig)  # type: ignore[attr-defined]
                        company.search_address_html = highlight_html(company.address or "", text_tokens=pq.text_tokens, digit_tokens=dig)  # type: ignore[attr-defined]
                        reasons = [
                            SearchReason(field="company.name", label="Название", value=(company.name or ""), value_html=company.search_name_html),  # type: ignore[arg-type]
                        ]
                        company.search_reasons = tuple([r for r in reasons if r.value])  # type: ignore[attr-defined]
                        company.search_reasons_total = len(company.search_reasons)  # type: ignore[attr-defined]
                        continue
                company.search_name_html = ex.name_html  # type: ignore[attr-defined]
                company.search_inn_html = ex.inn_html  # type: ignore[attr-defined]
                company.search_address_html = ex.address_html  # type: ignore[attr-defined]
                company.search_reasons = ex.reasons  # type: ignore[attr-defined]
                company.search_reasons_total = ex.reasons_total  # type: ignore[attr-defined]
        except Exception:
            # “последняя линия обороны”: всё равно показываем причины и подсветку,
            # даже если explain-логика упала.
            from companies.search_index import parse_query
            from companies.search_service import SearchReason, highlight_html
            pq = parse_query(q)
            for company in page.object_list:
                dig = pq.strong_digit_tokens + pq.weak_digit_tokens
                company.search_name_html = highlight_html(company.name or "", text_tokens=pq.text_tokens, digit_tokens=dig)  # type: ignore[attr-defined]
                company.search_inn_html = highlight_html(company.inn or "", text_tokens=pq.text_tokens, digit_tokens=dig)  # type: ignore[attr-defined]
                company.search_address_html = highlight_html(company.address or "", text_tokens=pq.text_tokens, digit_tokens=dig)  # type: ignore[attr-defined]
                reasons = [
                    SearchReason(field="company.inn", label="ИНН", value=(company.inn or ""), value_html=company.search_inn_html),  # type: ignore[arg-type]
                    SearchReason(field="company.name", label="Название", value=(company.name or ""), value_html=company.search_name_html),  # type: ignore[arg-type]
                    SearchReason(field="company.address", label="Адрес", value=(company.address or ""), value_html=company.search_address_html),  # type: ignore[arg-type]
                ]
                company.search_reasons = tuple([r for r in reasons if r.value])  # type: ignore[attr-defined]
                company.search_reasons_total = len(company.search_reasons)  # type: ignore[attr-defined]
    
    # Формируем qs для пагинации, включая per_page если он отличается от значения по умолчанию
    # Используем filter_params вместо request.GET, чтобы включить default_branch_id для директора филиала
    from urllib.parse import urlencode
    qs_params = {}
    for key, value in filter_params.items():
        if key != "page":
            if isinstance(value, list):
                qs_params[key] = value
            else:
                qs_params[key] = [value]
    qs_no_page = urlencode(qs_params, doseq=True) if qs_params else ""
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

    is_admin = require_admin(user)

    return render(
        request,
        "ui/company_list.html",
        {
            "page": page,
            "qs": qs_no_page,
            "q": q,
            "responsible": f["responsible"],
            "status": f["status"],
            "branch": f["branch"],
            "sphere": f["sphere"],
            "contract_type": f["contract_type"],
            "region": f["region"],
            "selected_regions": f.get("selected_regions", []),
            "overdue": f["overdue"],
            "companies_total": companies_total,
            "companies_filtered": companies_filtered,
            "filter_active": filter_active,
            "sort": sort,
            "dir": direction,
            "sort_field": sort,
            "sort_dir": direction,
            "responsibles": get_users_for_lists(effective_user),
            "statuses": CompanyStatus.objects.order_by("name"),
            "spheres": CompanySphere.objects.order_by("name"),
            "branches": Branch.objects.order_by("name"),
            "regions": Region.objects.order_by("name"),
            "contract_types": ContractType.objects.order_by("order", "name"),
            "company_list_columns": columns,
            "transfer_targets": get_transfer_targets(effective_user),
            "per_page": per_page,
            "is_admin": is_admin,
            "has_companies_without_responsible": has_companies_without_responsible,
        },
    )


@login_required
@policy_required(resource_type="page", resource="ui:companies:list")
def company_list_ajax(request: HttpRequest) -> JsonResponse:
    """
    AJAX endpoint для получения списка компаний без перезагрузки страницы.
    Возвращает HTML таблицы и метаданные для виртуального скроллинга.
    """
    user: User = request.user
    now = timezone.now()
    
    # Используем ту же логику, что и в company_list
    from django.core.cache import cache
    
    # Кэш-ключ должен учитывать пользователя и режим view_as (только если режим включён)
    view_as_user = get_view_as_user(request)
    effective_user_id = view_as_user.id if view_as_user else user.id
    view_as_enabled = request.session.get("view_as_enabled", False)
    view_as_role = request.session.get("view_as_role") if view_as_enabled else None
    view_as_branch_id = None
    if view_as_enabled and request.session.get("view_as_branch_id"):
        try:
            view_as_branch_id = int(request.session.get("view_as_branch_id"))
        except (TypeError, ValueError):
            view_as_branch_id = None
    
    # Создаем уникальный ключ кэша с учетом прав доступа
    cache_key_parts = ["companies_total_count", str(effective_user_id)]
    if view_as_role:
        cache_key_parts.append(f"role_{view_as_role}")
    if view_as_branch_id:
        cache_key_parts.append(f"branch_{view_as_branch_id}")
    cache_key_total = "_".join(cache_key_parts)
    
    companies_total = cache.get(cache_key_total)
    if companies_total is None:
        # Применяем те же фильтры, что и в company_list
        qs = Company.objects.all()
        # Если есть view_as, применяем соответствующие фильтры
        if view_as_branch_id:
            qs = qs.filter(branch_id=view_as_branch_id)
        companies_total = qs.order_by().count()
        # Держим TTL консистентным с company_list (60 сек)
        cache.set(cache_key_total, companies_total, 60)
    
    # Оптимизация: предзагружаем только необходимые связанные объекты
    # Используем only() для уменьшения объема загружаемых данных
    qs = (
        _companies_with_overdue_flag(now=now)
        .select_related("responsible", "branch", "status", "region")
        .prefetch_related(
            "spheres",
            # Предзагружаем только value для телефонов и email (не все поля)
            models.Prefetch("phones", queryset=CompanyPhone.objects.only("id", "company_id", "value")),
            models.Prefetch("emails", queryset=CompanyEmail.objects.only("id", "company_id", "value")),
            models.Prefetch(
                "contacts",
                queryset=Contact.objects.only("id", "company_id", "first_name", "last_name")
                .prefetch_related(
                    models.Prefetch("phones", queryset=ContactPhone.objects.only("id", "contact_id", "value")),
                    models.Prefetch("emails", queryset=ContactEmail.objects.only("id", "contact_id", "value")),
                )
            ),
        )
    )
    
    q = (request.GET.get("q") or "").strip()
    # Важно: QueryDict может содержать несколько значений для одного ключа (например, region=1&region=2),
    # поэтому используем .lists(), чтобы не потерять мультивыбор.
    filter_params = {k: v for k, v in request.GET.lists()}
    filter_params_wo_search = dict(filter_params)
    filter_params_wo_search.pop("q", None)
    f = _apply_company_filters(qs=qs, params=filter_params_wo_search, default_responsible_id=None)
    qs = f["qs"]
    if q:
        from companies.search_service import get_company_search_backend
        qs = get_company_search_backend().apply(qs=qs, query=q)
    
    # Sorting
    sort_raw = (request.GET.get("sort") or "").strip()
    sort = sort_raw or "updated_at"
    direction = (request.GET.get("dir") or "").strip().lower() or "desc"
    direction = "asc" if direction == "asc" else "desc"
    sort_map = {
        "updated_at": "updated_at",
        "name": "name",
        "inn": "inn",
        "status": "status__name",
        "responsible": "responsible__last_name",
        "branch": "branch__name",
        "region": "region__name",
    }
    sort_field = sort_map.get(sort, "updated_at")
    if sort == "responsible":
        order = [sort_field, "responsible__first_name", "name"]
    else:
        order = [sort_field, "name"]
    if direction == "desc":
        order = [f"-{f}" for f in order]
    if not (q and not sort_raw):
        qs = qs.order_by(*order)
    
    companies_filtered = qs.order_by().count()
    
    # Пагинация
    per_page = int(request.GET.get("per_page", request.session.get("company_list_per_page", 25)))
    if per_page not in [25, 50, 100, 200]:
        per_page = 25
    
    paginator = Paginator(qs, per_page)
    page_num = int(request.GET.get("page", 1))
    page = paginator.get_page(page_num)
    
    # Проверка прав на передачу
    company_ids = [c.id for c in page.object_list]
    transfer_check = can_transfer_companies(user, company_ids)
    allowed_ids_set = set(transfer_check["allowed"])
    for company in page.object_list:
        company.can_transfer = company.id in allowed_ids_set  # type: ignore[attr-defined]
    
    # Получаем конфигурацию колонок
    ui_cfg = UiGlobalConfig.load()
    columns = ui_cfg.company_list_columns or ["name"]
    
    # match_reasons + подсветка (детерминированно)
    if q:
        try:
            from companies.search_service import get_company_search_backend
            explain_map = get_company_search_backend().explain(companies=list(page.object_list), query=q)
            for company in page.object_list:
                ex = explain_map.get(company.id)
                if ex and ex.reasons:
                    company.search_name_html = ex.name_html  # type: ignore[attr-defined]
                    company.search_inn_html = ex.inn_html  # type: ignore[attr-defined]
                    company.search_address_html = ex.address_html  # type: ignore[attr-defined]
                    company.search_reasons = ex.reasons  # type: ignore[attr-defined]
                    company.search_reasons_total = ex.reasons_total  # type: ignore[attr-defined]
                else:
                    single_map = get_company_search_backend().explain(companies=[company], query=q)
                    ex_one = single_map.get(company.id) if single_map else None
                    if ex_one and ex_one.reasons:
                        company.search_name_html = ex_one.name_html  # type: ignore[attr-defined]
                        company.search_inn_html = ex_one.inn_html  # type: ignore[attr-defined]
                        company.search_address_html = ex_one.address_html  # type: ignore[attr-defined]
                        company.search_reasons = ex_one.reasons  # type: ignore[attr-defined]
                        company.search_reasons_total = ex_one.reasons_total  # type: ignore[attr-defined]
                    else:
                        from companies.search_index import parse_query
                        from companies.search_service import SearchReason, highlight_html
                        pq = parse_query(q)
                        dig = pq.strong_digit_tokens + pq.weak_digit_tokens
                        company.search_name_html = highlight_html(company.name or "", text_tokens=pq.text_tokens, digit_tokens=dig)  # type: ignore[attr-defined]
                        company.search_inn_html = highlight_html(company.inn or "", text_tokens=pq.text_tokens, digit_tokens=dig)  # type: ignore[attr-defined]
                        company.search_address_html = highlight_html(company.address or "", text_tokens=pq.text_tokens, digit_tokens=dig)  # type: ignore[attr-defined]
                        reasons = [
                            SearchReason(field="company.name", label="Название", value=(company.name or ""), value_html=company.search_name_html),  # type: ignore[arg-type]
                        ]
                        company.search_reasons = tuple([r for r in reasons if r.value])  # type: ignore[attr-defined]
                        company.search_reasons_total = len(company.search_reasons)  # type: ignore[attr-defined]
        except Exception:
            from companies.search_index import parse_query
            from companies.search_service import SearchReason, highlight_html
            pq = parse_query(q)
            for company in page.object_list:
                dig = pq.strong_digit_tokens + pq.weak_digit_tokens
                company.search_name_html = highlight_html(company.name or "", text_tokens=pq.text_tokens, digit_tokens=dig)  # type: ignore[attr-defined]
                company.search_inn_html = highlight_html(company.inn or "", text_tokens=pq.text_tokens, digit_tokens=dig)  # type: ignore[attr-defined]
                company.search_address_html = highlight_html(company.address or "", text_tokens=pq.text_tokens, digit_tokens=dig)  # type: ignore[attr-defined]
                reasons = [
                    SearchReason(field="company.inn", label="ИНН", value=(company.inn or ""), value_html=company.search_inn_html),  # type: ignore[arg-type]
                    SearchReason(field="company.name", label="Название", value=(company.name or ""), value_html=company.search_name_html),  # type: ignore[arg-type]
                    SearchReason(field="company.address", label="Адрес", value=(company.address or ""), value_html=company.search_address_html),  # type: ignore[arg-type]
                ]
                company.search_reasons = tuple([r for r in reasons if r.value])  # type: ignore[attr-defined]
                company.search_reasons_total = len(company.search_reasons)  # type: ignore[attr-defined]
    
    # Рендерим HTML строк таблицы
    from django.template.loader import render_to_string
    rows_html = render_to_string(
        "ui/company_list_rows.html",
        {
            "companies": page.object_list,
            "company_list_columns": columns,
            "search_query": q,  # Передаем запрос для подсветки совпадений
        },
        request=request,
    )
    
    return JsonResponse({
        "html": rows_html,
        "total": companies_total,
        "filtered": companies_filtered,
        "page": page_num,
        "num_pages": paginator.num_pages,
        "has_previous": page.has_previous(),
        "has_next": page.has_next(),
        "per_page": per_page,
    })


@login_required
@policy_required(resource_type="action", resource="ui:companies:bulk_transfer")
def company_bulk_transfer_preview(request: HttpRequest) -> JsonResponse:
    """
    AJAX: превью массового переназначения компаний.
    Возвращает список компаний, которые будут изменены, без фактического изменения.
    """
    if request.method != "POST":
        return JsonResponse({"error": "Method not allowed"}, status=405)
    
    user: User = request.user
    new_resp_id = (request.POST.get("responsible_id") or "").strip()
    apply_mode = (request.POST.get("apply_mode") or "selected").strip().lower()
    
    if not new_resp_id:
        return JsonResponse({"error": "Выберите нового ответственного"}, status=400)
    
    try:
        new_resp = User.objects.get(id=new_resp_id, is_active=True)
    except User.DoesNotExist:
        return JsonResponse({"error": "Ответственный не найден"}, status=404)
    
    # Проверка прав на нового ответственного
    if new_resp.role in (User.Role.GROUP_MANAGER, User.Role.ADMIN):
        return JsonResponse({"error": "Нельзя передать компании управляющему или администратору"}, status=400)
    
    if new_resp.role not in (User.Role.MANAGER, User.Role.BRANCH_DIRECTOR, User.Role.SALES_HEAD):
        return JsonResponse({"error": "Нового ответственного можно выбрать только из: менеджер / директор филиала / РОП"}, status=400)
    
    editable_qs = _editable_company_qs(user)
    
    # Режим "по фильтру"
    if apply_mode == "filtered":
        now = timezone.now()
        qs = _companies_with_overdue_flag(now=now)
        f = _apply_company_filters(qs=qs, params=request.POST)
        qs = f["qs"]
        qs = qs.filter(id__in=editable_qs.values_list("id", flat=True)).distinct()
        cap = 5000
        ids = list(qs.values_list("id", flat=True)[:cap])
        if not ids:
            return JsonResponse({"error": "Нет компаний для переназначения (или нет прав)"}, status=400)
        if len(ids) >= cap:
            return JsonResponse({"error": f"Выбрано слишком много компаний (>{cap}). Сузьте фильтр и повторите"}, status=400)
    else:
        ids = request.POST.getlist("company_ids") or []
        ids = [i for i in ids if i]
        if not ids:
            return JsonResponse({"error": "Выберите хотя бы одну компанию"}, status=400)
        ids = list(editable_qs.filter(id__in=ids).values_list("id", flat=True))
        if not ids:
            return JsonResponse({"error": "Нет выбранных компаний, доступных для переназначения"}, status=400)
    
    # Проверка прав на передачу
    transfer_check = can_transfer_companies(user, ids)
    allowed_ids = transfer_check["allowed"]
    forbidden_list = transfer_check["forbidden"]
    
    # Проверка, что новый ответственный из того же филиала (для РОП/директора)
    if user.role in (User.Role.BRANCH_DIRECTOR, User.Role.SALES_HEAD) and user.branch_id:
        if new_resp.branch_id != user.branch_id:
            return JsonResponse({
                "error": "Новый ответственный должен быть из вашего филиала",
                "allowed_count": 0,
                "forbidden_count": len(ids),
            }, status=400)
    
    # Если есть запрещённые компании, возвращаем детали, но не блокируем полностью
    # (пользователь увидит preview с allowed/forbidden)
    
    # Используем только разрешённые компании для превью
    if not allowed_ids:
        return JsonResponse({
            "error": "Нет компаний, доступных для переназначения",
            "allowed_count": 0,
            "forbidden_count": len(ids),
            "forbidden": forbidden_list[:10],  # Первые 10 с причинами
        }, status=400)
    
    # Получаем данные компаний для превью (только разрешённые)
    companies = Company.objects.filter(id__in=allowed_ids).select_related("responsible", "branch", "status")[:100]
    
    companies_preview = []
    old_responsibles = {}
    for company in companies:
        companies_preview.append({
            "id": str(company.id),
            "name": company.name,
            "inn": company.inn or "",
            "old_responsible": str(company.responsible) if company.responsible else "—",
            "old_branch": str(company.branch) if company.branch else "—",
        })
        if company.responsible_id:
            old_resp_id = str(company.responsible_id)
            if old_resp_id not in old_responsibles:
                old_responsibles[old_resp_id] = {
                    "id": old_resp_id,
                    "name": str(company.responsible),
                    "count": 0,
                }
            old_responsibles[old_resp_id]["count"] += 1
    
    return JsonResponse({
        "total_count": len(ids),
        "preview_count": len(companies_preview),
        "new_responsible": {
            "id": str(new_resp.id),
            "name": str(new_resp),
            "role": new_resp.get_role_display(),
            "branch": str(new_resp.branch) if new_resp.branch else "—",
        },
        "old_responsibles": list(old_responsibles.values()),
        "companies": companies_preview,
        "mode": apply_mode,
    })


@login_required
@policy_required(resource_type="action", resource="ui:companies:bulk_transfer")
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
    
    # Проверка, что новый ответственный разрешён (не GROUP_MANAGER, не ADMIN)
    if new_resp.role in (User.Role.GROUP_MANAGER, User.Role.ADMIN):
        messages.error(request, "Нельзя передать компании управляющему или администратору.")
        return redirect("company_list")
    
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

    # Проверка прав на передачу каждой компании (используем новую функцию)
    transfer_check = can_transfer_companies(user, ids)
    if transfer_check["forbidden"]:
        # Есть запрещённые компании - показываем детали
        forbidden_names = [f["name"] for f in transfer_check["forbidden"][:5]]
        if len(transfer_check["forbidden"]) > 5:
            forbidden_names.append(f"... и ещё {len(transfer_check['forbidden']) - 5}")
        messages.error(
            request,
            f"Некоторые компании нельзя передать ({len(transfer_check['forbidden'])} из {len(ids)}): "
            f"{', '.join(forbidden_names)}"
        )
        return redirect("company_list")
    
    # Используем только разрешённые компании
    ids = transfer_check["allowed"]
    if not ids:
        messages.error(request, "Нет компаний, доступных для переназначения.")
        return redirect("company_list")

    now_ts = timezone.now()
    # Транзакция обеспечивается декоратором @transaction.atomic на функции
    qs_to_update = Company.objects.filter(id__in=ids).select_related("responsible", "branch", "status")
    
    # Собираем детальную информацию для аудита
    companies_data = []
    old_responsibles_data = {}
    for company in qs_to_update[:50]:  # Первые 50 для детального лога
        companies_data.append({
            "id": str(company.id),
            "name": company.name,
            "inn": company.inn or "",
        })
        if company.responsible_id:
            old_resp_id = str(company.responsible_id)
            if old_resp_id not in old_responsibles_data:
                old_responsibles_data[old_resp_id] = {
                    "id": old_resp_id,
                    "name": str(company.responsible),
                    "count": 0,
                }
            old_responsibles_data[old_resp_id]["count"] += 1
    
    # Получаем уникальные ID старых ответственных
    old_resp_ids = list(qs_to_update.values_list("responsible_id", flat=True).distinct()[:20])
    old_resp_ids = [str(rid) for rid in old_resp_ids if rid]
    
    # Собираем информацию о фильтрах (если был режим filtered)
    filters_info = {}
    if apply_mode == "filtered":
        # Получаем множественные значения region
        region_list = request.POST.getlist("region") or []
        region_str = ",".join(region_list) if region_list else ""
        filters_info = {
            "q": request.POST.get("q", ""),
            "responsible": request.POST.get("responsible", ""),
            "status": request.POST.get("status", ""),
            "branch": request.POST.get("branch", ""),
            "sphere": request.POST.get("sphere", ""),
            "contract_type": request.POST.get("contract_type", ""),
            "region": region_str,  # Сохраняем как строку для логирования
            "overdue": request.POST.get("overdue", ""),
        }
    
    updated = qs_to_update.update(responsible=new_resp, branch=new_resp.branch, updated_at=now_ts)
    _invalidate_company_count_cache()  # Инвалидируем кэш при массовом переназначении
    
    # Аудит-лог массовой передачи
    forbidden_count = len(transfer_check.get("forbidden", []))
    forbidden_list = transfer_check.get("forbidden", [])
    
    # Формируем сообщение с сводкой
    if forbidden_count > 0:
        messages.success(
            request,
            f"Переназначено компаний: {updated}. Пропущено: {forbidden_count}. Новый ответственный: {new_resp}."
        )
    else:
        messages.success(request, f"Переназначено компаний: {updated}. Новый ответственный: {new_resp}.")
    
    # Расширенное логирование для аудита
    log_event(
        actor=user,
        verb=ActivityEvent.Verb.UPDATE,
        entity_type="company_bulk_transfer",
        entity_id=str(new_resp.id),
        message=f"Массовое переназначение {updated} компаний → {new_resp}",
        meta={
            "count": updated,
            "to": {
                "id": str(new_resp.id),
                "name": str(new_resp),
                "role": new_resp.get_role_display(),
                "branch": str(new_resp.branch) if new_resp.branch else None,
            },
            "from": list(old_responsibles_data.values()),  # Детальная информация о старых ответственных
            "old_responsible_ids": old_resp_ids,
            "mode": apply_mode,
            "companies_sample": companies_data,  # Первые 50 компаний для детального лога
            "filters": filters_info if apply_mode == "filtered" else None,
            "forbidden_count": forbidden_count,
            "forbidden_sample": forbidden_list[:10] if forbidden_list else [],  # Первые 10 запрещённых с причинами
        },
    )
    if new_resp.id != user.id:
        notify(
            user=new_resp,
            kind=Notification.Kind.COMPANY,
            title="Вам передали компании",
            body=f"Количество: {updated}",
            url=f"/companies/?responsible={new_resp.id}",
        )
    
    # Инвалидируем кэш количества компаний после массового переназначения
    _invalidate_company_count_cache()
    
    return redirect("company_list")


@login_required
@policy_required(resource_type="action", resource="ui:companies:export")
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
                "region": ",".join((request.GET.getlist("region") or [])) if hasattr(request.GET, "getlist") else (request.GET.get("region") or "").strip(),
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
    # Экспортируем только компании, видимые пользователю (единая политика с UI и API)
    qs = qs.filter(pk__in=visible_companies_qs(user).values_list("pk", flat=True))

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
            return company.contract_type.name if company.contract_type else ""
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
@policy_required(resource_type="action", resource="ui:companies:create")
def company_create(request: HttpRequest) -> HttpResponse:
    user: User = request.user

    if request.method == "POST":
        form = CompanyCreateForm(request.POST, user=user)
        if form.is_valid():
            company: Company = form.save(commit=False)

            # Менеджер создаёт компанию только на себя; филиал подтягиваем от пользователя.
            company.created_by = user
            company.responsible = user
            company.branch = user.branch
            company.save()
            form.save_m2m()
            
            # Сохраняем дополнительные email адреса
            new_company_emails: list[tuple[int, str]] = []
            for key, value in request.POST.items():
                if key.startswith("company_emails_"):
                    raw = (value or "").strip()
                    if not raw:
                        continue
                    try:
                        index = int(key.replace("company_emails_", ""))
                    except (ValueError, TypeError):
                        continue
                    new_company_emails.append((index, raw))
            
            # Сохраняем дополнительные телефоны компании
            new_company_phones: list[tuple[int, str]] = []
            for key, value in request.POST.items():
                if key.startswith("company_phones_"):
                    raw = (value or "").strip()
                    if not raw:
                        continue
                    try:
                        index = int(key.replace("company_phones_", ""))
                    except (ValueError, TypeError):
                        continue
                    new_company_phones.append((index, raw))
            
            # Валидация телефонов: проверка на дубликаты
            from ui.forms import _normalize_phone
            all_phones = []
            if company.phone:
                normalized_main = _normalize_phone(company.phone)
                if normalized_main:
                    all_phones.append(normalized_main)
            
            for order, phone_value in new_company_phones:
                normalized = _normalize_phone(phone_value)
                if normalized:
                    all_phones.append(normalized)
            
            # Проверка на дубликаты
            if len(all_phones) != len(set(all_phones)):
                form.add_error(None, "Есть повторяющиеся телефоны (основной телефон не должен совпадать с дополнительными).")
                # Восстанавливаем введённые значения для отображения ошибки
                company_emails = []
                company_phones = []
                for key, value in request.POST.items():
                    if key.startswith("company_emails_"):
                        company_emails.append(
                            CompanyEmail(company=company, value=(value or "").strip())
                        )
                    if key.startswith("company_phones_"):
                        company_phones.append(
                            CompanyPhone(company=company, value=(value or "").strip())
                        )
                return render(
                    request,
                    "ui/company_create.html",
                    {"form": form, "company_emails": company_emails, "company_phones": company_phones},
                )
            
            # Сохраняем дополнительные email и телефоны
            for order, email_value in sorted(new_company_emails, key=lambda x: x[0]):
                CompanyEmail.objects.create(company=company, value=email_value, order=order)
            
            for order, phone_value in sorted(new_company_phones, key=lambda x: x[0]):
                # Нормализуем телефон перед сохранением; если номер не удаётся нормализовать,
                # не сохраняем его во избежание "мусорных" значений.
                normalized = _normalize_phone(phone_value)
                if normalized:
                    CompanyPhone.objects.create(company=company, value=normalized, order=order)
            
            _invalidate_company_count_cache()  # Инвалидируем кэш при создании
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
        form = CompanyCreateForm(user=user)

    return render(request, "ui/company_create.html", {"form": form, "company_emails": [], "company_phones": []})


@login_required
@policy_required(resource_type="action", resource="ui:companies:autocomplete")
def company_autocomplete(request: HttpRequest) -> JsonResponse:
    """
    AJAX: автодополнение для поиска компаний.
    Возвращает список компаний по запросу (название, ИНН, адрес, телефон, email).
    """
    q = (request.GET.get("q") or "").strip()

    # Режим получения одной компании по ID (для префилла модалок и т.п.).
    company_id_raw = (request.GET.get("id") or "").strip()
    if company_id_raw:
        try:
            company_uuid = UUID(company_id_raw)
        except Exception:
            return JsonResponse({"items": []})

        qs = (
            Company.objects.filter(id=company_uuid)
            .select_related("responsible", "branch", "status")
            .prefetch_related("phones", "emails", "contacts__phones", "contacts__emails")
        )
    else:
        if not q or len(q) < 2:
            return JsonResponse({"items": []})

    exclude_raw = (request.GET.get("exclude") or "").strip()
    exclude_id = None
    if exclude_raw:
        try:
            exclude_id = UUID(exclude_raw)
        except Exception:
            exclude_id = None

    # Подготовим нормализованные значения для последующей подсветки совпадений
    normalized_phone = _normalize_phone_for_search(q)
    normalized_email = _normalize_email_for_search(q)

    # Используем ту же логику поиска, что и в списке компаний (SearchService),
    # чтобы поведение автодополнения и таблицы было одинаковым.
    if not company_id_raw:
        from companies.search_service import get_company_search_backend
        base_qs = Company.objects.all()
        qs = get_company_search_backend().apply(qs=base_qs, query=q)
        if exclude_id:
            qs = qs.exclude(id=exclude_id)
        qs = (
            qs.select_related("responsible", "branch", "status")
            .prefetch_related("phones", "emails", "contacts__phones", "contacts__emails")
            .distinct()[:10]
        )
    
    items = []
    for c in qs:
        # Определяем, где найдено совпадение
        match_in_name = q.lower() in (c.name or "").lower()
        match_in_inn = q in (c.inn or "")
        match_in_address = q.lower() in (c.address or "").lower()
        match_in_phone = False
        match_in_email = False
        
        # Проверяем совпадение в телефонах
        matched_phone = None
        # Проверяем основной телефон
        if c.phone and (q in c.phone or (normalized_phone and normalized_phone in c.phone)):
            match_in_phone = True
            matched_phone = c.phone
        # Проверяем дополнительные телефоны
        if not matched_phone:
            for phone_obj in c.phones.all():
                if q in phone_obj.value or (normalized_phone and normalized_phone in phone_obj.value):
                    match_in_phone = True
                    matched_phone = phone_obj.value
                    break
        
        # Проверяем совпадение в email
        matched_email = None
        # Проверяем основной email
        if c.email and (q.lower() in c.email.lower() or (normalized_email and normalized_email == c.email.lower())):
            match_in_email = True
            matched_email = c.email
        # Проверяем дополнительные email
        if not matched_email:
            for email_obj in c.emails.all():
                if q.lower() in email_obj.value.lower() or (normalized_email and normalized_email == email_obj.value.lower()):
                    match_in_email = True
                    matched_email = email_obj.value
                    break
        
        # Признаки структуры организации для UI:
        # - is_branch: у компании есть головная (сама компания — филиал);
        # - has_branches: у компании есть хотя бы один филиал.
        is_branch = bool(c.head_company_id)
        has_branches = Company.objects.filter(head_company_id=c.id).exists()

        items.append({
            "id": str(c.id),
            "name": c.name,
            "inn": c.inn or "",
            "address": c.address or "",
            "status": c.status.name if c.status else "",
            "responsible": str(c.responsible) if c.responsible else "",
            "url": f"/companies/{c.id}/",
            "phone": matched_phone if match_in_phone else None,
            "email": matched_email if match_in_email else None,
            "is_branch": is_branch,
            "has_branches": has_branches,
            "match_in": {
                "name": match_in_name,
                "inn": match_in_inn,
                "address": match_in_address,
                "phone": match_in_phone,
                "email": match_in_email,
            },
            "query": q,  # Передаем запрос для подсветки
        })
    
    return JsonResponse({"items": items})


@login_required
@policy_required(resource_type="action", resource="ui:companies:duplicates")
def company_duplicates(request: HttpRequest) -> HttpResponse:
    """
    JSON: подсказки дублей при создании компании.
    Проверяем по ИНН/КПП/названию/адресу и возвращаем только то, что пользователь может видеть.
    ИНН нормализуем через normalize_inn, чтобы совпадали и "901000327", и "901 000 327".
    """
    user: User = request.user
    inn_raw = (request.GET.get("inn") or "").strip()
    from companies.normalizers import normalize_inn
    inn = normalize_inn(inn_raw) if inn_raw else ""
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
@policy_required(resource_type="page", resource="ui:companies:detail")
@require_can_view_company
def company_detail(request: HttpRequest, company_id) -> HttpResponse:
    logger = logging.getLogger(__name__)
    user: User = request.user
    # Загружаем компанию с связанными объектами, включая поля для истории холодных звонков
    company = get_object_or_404(
        Company.objects.select_related(
            "responsible",
            "branch",
            "status",
            "head_company",
            "contract_type",
            "primary_cold_marked_by",
            "primary_cold_marked_call",
        ).prefetch_related(
            "emails",
            Prefetch(
                "phones",
                queryset=CompanyPhone.objects.select_related("cold_marked_by", "cold_marked_call").order_by("order", "value")
            ),
        ),
        id=company_id,
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
    is_group_manager = user.role == User.Role.GROUP_MANAGER
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
    # Сортируем задачи: сначала просроченные (по дедлайну, старые сначала), потом по дедлайну (ближайшие сначала), потом по дате создания (новые сначала)
    # Исключаем выполненные задачи из списка "Последние задачи"
    now = timezone.now()
    local_now = timezone.localtime(now)
    # Индикатор: можно ли звонить (рабочее время компании + часовой пояс)
    worktime = {
        # Источник "можно ли звонить" теперь только "Режим работы"
        "has": bool(company.work_schedule),
        "status": None,  # "ok" | "warn_end" | "off" | "unknown"
        "label": "",
    }
    try:
        from zoneinfo import ZoneInfo
        from ui.timezone_utils import guess_ru_timezone_from_address
        from core.work_schedule_utils import get_worktime_status_from_schedule

        guessed = guess_ru_timezone_from_address(company.address or "")
        # приоритет: сохранённый вручную, затем авто по адресу
        tz_name = (((company.work_timezone or "").strip()) or guessed or "Europe/Moscow").strip()
        tz = ZoneInfo(tz_name)
        now_tz = timezone.now().astimezone(tz)

        if company.work_schedule:
            status, _mins = get_worktime_status_from_schedule(company.work_schedule, now_tz=now_tz)
            worktime["status"] = status
            if status == "ok":
                worktime["label"] = "Рабочее время"
            elif status == "warn_end":
                worktime["label"] = "Остался час"
            elif status == "off":
                worktime["label"] = "Не рабочее время"
            else:
                worktime["label"] = ""
    except Exception:
        worktime["status"] = "unknown"
        worktime["label"] = ""
    tasks = (
        Task.objects.filter(company=company)
        .exclude(status=Task.Status.DONE)  # Исключаем выполненные задачи
        .select_related("assigned_to", "type", "created_by")
        .annotate(
            is_overdue=models.Case(
                models.When(
                    models.Q(due_at__lt=now) & ~models.Q(status__in=[Task.Status.DONE, Task.Status.CANCELLED]),
                    then=models.Value(1)
                ),
                default=models.Value(0),
                output_field=models.IntegerField()
            )
        )
        .order_by("-is_overdue", "due_at", "-created_at")[:25]
    )
    for t in tasks:
        t.can_manage_status = _can_manage_task_status_ui(user, t)  # type: ignore[attr-defined]
        t.can_edit_task = _can_edit_task_ui(user, t)  # type: ignore[attr-defined]
        t.can_delete_task = _can_delete_task_ui(user, t)  # type: ignore[attr-defined]

    note_form = CompanyNoteForm()
    activity = []
    if can_view_activity:
        activity = ActivityEvent.objects.filter(company_id=company.id).select_related("actor")[:50]
    quick_form = CompanyQuickEditForm(instance=company)
    contract_form = CompanyContractForm(instance=company)

    transfer_targets = get_transfer_targets(user)

    # Подсветка договора: используем настройки из ContractType
    contract_alert = ""
    contract_days_left = None
    if company.contract_until:
        today_date = timezone.localdate(timezone.now())
        contract_days_left = (company.contract_until - today_date).days
        if contract_days_left is not None:
            if company.contract_type:
                # Используем настройки из ContractType
                warning_days = company.contract_type.warning_days
                danger_days = company.contract_type.danger_days
                if contract_days_left <= danger_days:
                    contract_alert = "danger"
                elif contract_days_left <= warning_days:
                    contract_alert = "warn"
            else:
                # Fallback на старую логику, если нет contract_type
                if contract_days_left < 14:
                    contract_alert = "danger"
                elif contract_days_left <= 30:
                    contract_alert = "warn"

    # Принудительно загружаем телефоны, чтобы убедиться, что prefetch работает
    # Это гарантирует, что телефоны будут доступны в шаблоне
    company_phones_list = list(company.phones.all())
    # Отладочная информация: логируем количество загруженных телефонов
    if company_phones_list:
        logger.info(f"Company {company.id} has {len(company_phones_list)} phones loaded")
    else:
        logger.warning(f"Company {company.id} has no phones loaded (check if phones exist in DB)")
    
    # Получаем режим просмотра карточки: из GET параметра, session или preferences (по умолчанию classic)
    detail_view_mode = request.GET.get("view", "").strip().lower()
    if detail_view_mode not in ["classic", "modern"]:
        detail_view_mode = request.session.get("company_detail_view_mode")
        if not detail_view_mode:
            prefs = UiUserPreference.load_for_user(user)
            detail_view_mode = prefs.company_detail_view_mode or "classic"
            request.session["company_detail_view_mode"] = detail_view_mode
    
    # Подготовка данных для modern layout (pinned/latest note, ближайшие задачи)
    display_note = pinned_note
    if not display_note and notes:
        display_note = notes[0]  # Первая заметка из отсортированного списка (latest)
    
    # Ближайшие задачи для modern layout (2-3 по дедлайну, исключая выполненные)
    upcoming_tasks = list(tasks[:3])  # Уже отсортированы: просроченные -> ближайшие
    
    from companies.models import ContractType
    contract_types_list = ContractType.objects.all().order_by("order", "name")
    # Сумма договора для input в модалке — всегда с точкой (для type="number")
    contract_amount_value = ""
    if getattr(company, "contract_amount", None) is not None:
        try:
            contract_amount_value = f"{float(company.contract_amount):.2f}"
        except (TypeError, ValueError):
            pass

    return render(
        request,
        "ui/company_detail.html",
        {
            "company": company,
            "contract_amount_value": contract_amount_value,
            "org_head": org_head,
            "org_branches": org_branches,
            "can_edit_company": can_edit_company,
            "contacts": contacts,
            "primary_cold_available": primary_cold_available,
            "is_admin": is_admin,
            "is_group_manager": is_group_manager,
            "notes": notes,
            "pinned_note": pinned_note,
            "note_form": note_form,
            "tasks": tasks,
            "local_now": local_now,  # Для корректного сравнения дат в шаблоне
            "worktime": worktime,
            "activity": activity,
            "can_view_activity": can_view_activity,
            "can_delete_company": can_delete_company,
            "can_request_delete": can_request_delete,
            "delete_req": delete_req,
            "quick_form": quick_form,
            "contract_form": contract_form,
            "contract_types": contract_types_list,  # Для JavaScript определения годовых договоров
            "transfer_targets": transfer_targets,
            "contract_alert": contract_alert,
            "contract_days_left": contract_days_left,
            "company_phones_list": company_phones_list,  # Явно передаем список телефонов для отладки
            "detail_view_mode": detail_view_mode,
            "display_note": display_note,  # Для modern layout: pinned или latest
            "upcoming_tasks": upcoming_tasks,  # Ближайшие 2-3 задачи для modern layout
            "statuses": CompanyStatus.objects.order_by("name"),  # Для быстрого изменения статуса в Modern
            "contacts_rest": list(contacts)[5:],  # Контакты с 6-го для кнопки «Показать всех» в Modern
        },
    )


@login_required
@policy_required(resource_type="page", resource="ui:companies:detail")
@require_can_view_company
def company_tasks_history(request: HttpRequest, company_id) -> HttpResponse:
    """
    История выполненных задач по компании (для модального окна в карточке компании).
    Показываем только задачи со статусом DONE, отсортированные от новых к старым.
    """
    user: User = request.user  # noqa: F841  # зарезервировано на будущее (фильтрация прав)
    company = get_object_or_404(Company, id=company_id)

    tasks = (
        Task.objects.filter(company=company, status=Task.Status.DONE)
        .select_related("assigned_to", "type", "created_by")
        .order_by("-created_at")[:100]
    )

    local_now = timezone.localtime(timezone.now())

    return render(
        request,
        "ui/partials/company_tasks_history.html",
        {
            "company": company,
            "tasks": tasks,
            "local_now": local_now,
        },
    )


@login_required
@policy_required(resource_type="action", resource="ui:companies:delete_request:create")
@require_can_view_company
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
    # Дополнительно создаём Notification с payload для UI
    from notifications.service import notify as notify_service
    from notifications.models import Notification
    branch_leads = User.objects.filter(
        is_active=True, branch_id=branch_id, role__in=[User.Role.SALES_HEAD, User.Role.BRANCH_DIRECTOR]
    ).exclude(id=user.id)
    for lead in branch_leads:
        notify_service(
            user=lead,
            kind=Notification.Kind.COMPANY,
            title="Запрос на удаление компании",
            body=f"{company.name}: {(note[:180] + '…') if len(note) > 180 else note or 'без комментария'}",
            url=f"/companies/{company.id}/",
            payload={
                "company_id": str(company.id),
                "request_id": req.id,
                "requested_by_id": user.id,
                "requested_by_name": f"{user.last_name} {user.first_name}".strip() or user.get_username(),
                "reason": note[:500] if note else "",
            },
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
@policy_required(resource_type="action", resource="ui:companies:delete_request:cancel")
@require_can_view_company
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
            payload={
                "company_id": str(company.id),
                "request_id": req.id,
                "decided_by_id": user.id,
                "decided_by_name": f"{user.last_name} {user.first_name}".strip() or user.get_username(),
                "decision": "cancelled",
            },
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
@policy_required(resource_type="action", resource="ui:companies:delete_request:approve")
@require_can_view_company
def company_delete_request_approve(request: HttpRequest, company_id, req_id: int) -> HttpResponse:
    if request.method != "POST":
        return redirect("company_detail", company_id=company_id)
    user: User = request.user
    company = get_object_or_404(Company.objects.select_related("responsible", "branch"), id=company_id)
    # Сохраняем ID компании отдельно — после company.delete() pk на инстансе станет None.
    company_pk = company.id
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

    try:
        with transaction.atomic():
            # На всякий случай удаляем индекс до каскада, как и при прямом удалении.
            CompanySearchIndex.objects.filter(company_id=company_pk).delete()

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
                    payload={
                        "company_id": str(company_pk),
                        "request_id": req.id,
                        "decided_by_id": user.id,
                        "decided_by_name": f"{user.last_name} {user.first_name}".strip() or user.get_username(),
                        "decision": "approved",
                    },
                )
            log_event(
                actor=user,
                verb=ActivityEvent.Verb.DELETE,
                entity_type="company",
                entity_id=str(company_pk),
                company_id=company_pk,
                message="Компания удалена (по запросу)",
                meta={
                    "request_id": req.id,
                    "detached_branches": [str(c.id) for c in detached[:50]],
                    "detached_count": len(detached),
                    "branches_notified": branches_notified,
                },
            )
            company.delete()
    except IntegrityError:
        logger.exception("Failed to delete company %s via delete request due to CompanySearchIndex integrity error", company_pk)
        messages.error(
            request,
            "Не удалось полностью удалить компанию по запросу из-за проблем с индексом поиска. "
            "Обратитесь к администратору.",
        )
        return redirect("company_detail", company_id=company_pk)

    messages.success(request, "Компания удалена.")
    return redirect("company_list")


@login_required
@policy_required(resource_type="action", resource="ui:companies:delete")
@require_can_view_company
def company_delete_direct(request: HttpRequest, company_id) -> HttpResponse:
    if request.method != "POST":
        return redirect("company_detail", company_id=company_id)
    user: User = request.user
    company = get_object_or_404(Company.objects.select_related("responsible", "branch"), id=company_id)
    # Сохраняем исходный ID компании отдельно, т.к. после company.delete() pk на инстансе станет None,
    # а ошибка IntegrityError может возникнуть уже на COMMIT.
    company_pk = company.id
    if not _can_delete_company(user, company):
        messages.error(request, "Нет прав на удаление этой компании.")
        return redirect("company_detail", company_id=company.id)

    reason = (request.POST.get("reason") or "").strip()

    # Удаление компании иногда падало с IntegrityError по CompanySearchIndex
    # (битые/рассинхронизированные данные индекса поиска).
    # Чтобы не отдавать 500 пользователю, подчистим индекс и перехватим ошибку.
    try:
        with transaction.atomic():
            # На всякий случай удаляем индекс до каскада.
            CompanySearchIndex.objects.filter(company_id=company_pk).delete()

            detached = _detach_client_branches(head_company=company)
            branches_notified = _notify_head_deleted_with_branches(
                actor=user,
                head_company=company,
                detached=detached,
            )
            log_event(
                actor=user,
                verb=ActivityEvent.Verb.DELETE,
                entity_type="company",
                entity_id=str(company_pk),
                company_id=company_pk,
                message="Компания удалена",
                meta={
                    "reason": reason[:500],
                    "detached_branches": [str(c.id) for c in detached[:50]],
                    "detached_count": len(detached),
                    "branches_notified": branches_notified,
                },
            )
            company.delete()
    except IntegrityError:
        logger.exception("Failed to delete company %s due to CompanySearchIndex integrity error", company_pk)
        messages.error(
            request,
            "Не удалось полностью удалить компанию из-за проблем с индексом поиска. "
            "Обратитесь к администратору.",
        )
        # Компания формально ещё существует (транзакция откатится), но текущий инстанс уже “битый”.
        # Ведём пользователя в список компаний, чтобы избежать NoReverseMatch и повторных сбоев.
        return redirect("company_detail", company_id=company_pk)

    messages.success(request, "Компания удалена.")
    return redirect("company_list")




@login_required
@policy_required(resource_type="action", resource="ui:companies:contract:update")
@require_can_view_company
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

    contract_type = form.cleaned_data.get("contract_type")
    if contract_type:
        if contract_type.is_annual:
            # Для годовых: очищаем дату окончания и явно берём сумму из POST (модалка рендерит свой input)
            company.contract_until = None
            raw_amount = (request.POST.get("contract_amount") or "").strip()
            if raw_amount:
                try:
                    from decimal import Decimal
                    company.contract_amount = Decimal(raw_amount.replace(",", "."))
                except (ValueError, TypeError):
                    company.contract_amount = None
            else:
                company.contract_amount = None
        else:
            # Для негодовых: очищаем сумму
            company.contract_amount = None
    
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
@require_can_view_company
def company_cold_call_toggle(request: HttpRequest, company_id) -> HttpResponse:
    """
    Отметить основной контакт компании как холодный звонок.
    Отметку можно поставить только один раз.
    """
    if request.method != "POST":
        return redirect("company_detail", company_id=company_id)

    user: User = request.user
    company = get_object_or_404(Company.objects.select_related("responsible", "branch", "primary_cold_marked_by"), id=company_id)
    if not _can_edit_company(user, company):
        if _is_ajax(request):
            return JsonResponse({"ok": False, "error": "Нет прав на изменение признака 'Холодный звонок'."}, status=403)
        messages.error(request, "Нет прав на изменение признака 'Холодный звонок'.")
        return redirect("company_detail", company_id=company.id)

    # Проверка подтверждения
    confirmed = request.POST.get("confirmed") == "1"
    if not confirmed:
        if _is_ajax(request):
            return JsonResponse({"ok": False, "error": "Требуется подтверждение действия."}, status=400)
        messages.error(request, "Требуется подтверждение действия.")
        return redirect("company_detail", company_id=company.id)

    # Проверка: уже отмечен?
    if company.primary_contact_is_cold_call:
        if _is_ajax(request):
            return _cold_call_json(
                entity="company",
                entity_id=str(company.id),
                is_cold_call=True,
                marked_at=company.primary_cold_marked_at,
                marked_by=str(company.primary_cold_marked_by or ""),
                can_reset=bool(require_admin(user)),
                message="Основной контакт уже отмечен как холодный.",
            )
        messages.info(request, "Основной контакт уже отмечен как холодный.")
        return redirect("company_detail", company_id=company.id)

    phone = (company.phone or "").strip()
    if not phone:
        if _is_ajax(request):
            return JsonResponse({"ok": False, "error": "У компании не задан основной телефон."}, status=400)
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
        if _is_ajax(request):
            return JsonResponse({"ok": False, "error": "Не найден звонок по основному номеру."}, status=400)
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

    if _is_ajax(request):
        return _cold_call_json(
            entity="company",
            entity_id=str(company.id),
            is_cold_call=True,
            marked_at=company.primary_cold_marked_at,
            marked_by=str(company.primary_cold_marked_by or ""),
            can_reset=bool(require_admin(user)),
            message="Отмечено: холодный звонок (основной контакт).",
        )

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
@policy_required(resource_type="action", resource="ui:companies:cold_call:toggle")
def contact_cold_call_toggle(request: HttpRequest, contact_id) -> HttpResponse:
    """
    Отметить контакт как холодный звонок.
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
        if _is_ajax(request):
            return JsonResponse({"ok": False, "error": "Нет прав на изменение контактов этой компании."}, status=403)
        messages.error(request, "Нет прав на изменение контактов этой компании.")
        return redirect("company_detail", company_id=company.id)

    # Проверка подтверждения
    confirmed = request.POST.get("confirmed") == "1"
    if not confirmed:
        if _is_ajax(request):
            return JsonResponse({"ok": False, "error": "Требуется подтверждение действия."}, status=400)
        messages.error(request, "Требуется подтверждение действия.")
        return redirect("company_detail", company_id=company.id)

    # Проверка: уже отмечен?
    if contact.is_cold_call:
        if _is_ajax(request):
            return _cold_call_json(
                entity="contact",
                entity_id=str(contact.id),
                is_cold_call=True,
                marked_at=contact.cold_marked_at,
                marked_by=str(contact.cold_marked_by or ""),
                can_reset=bool(require_admin(user)),
                message="Контакт уже отмечен как холодный.",
            )
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
        if _is_ajax(request):
            return JsonResponse({"ok": False, "error": "Не найден звонок по этому контакту."}, status=400)
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

    if _is_ajax(request):
        return _cold_call_json(
            entity="contact",
            entity_id=str(contact.id),
            is_cold_call=True,
            marked_at=contact.cold_marked_at,
            marked_by=str(contact.cold_marked_by or ""),
            can_reset=bool(require_admin(user)),
            message="Отмечено: холодный звонок (контакт).",
        )

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
@require_can_view_company
def company_cold_call_reset(request: HttpRequest, company_id) -> HttpResponse:
    """
    Откатить отметку холодного звонка для основного контакта компании.
    Доступно только администраторам.
    """
    if request.method != "POST":
        return redirect("company_detail", company_id=company_id)

    user: User = request.user
    if not require_admin(user):
        if _is_ajax(request):
            return JsonResponse({"ok": False, "error": "Только администратор может откатить отметку холодного звонка."}, status=403)
        messages.error(request, "Только администратор может откатить отметку холодного звонка.")
        return redirect("company_detail", company_id=company_id)

    company = get_object_or_404(Company.objects.select_related("responsible", "branch"), id=company_id)
    
    if not company.primary_contact_is_cold_call:
        if _is_ajax(request):
            return _cold_call_json(
                entity="company",
                entity_id=str(company.id),
                is_cold_call=False,
                marked_at=company.primary_cold_marked_at,
                marked_by=str(company.primary_cold_marked_by or ""),
                can_reset=True,
                message="Основной контакт не отмечен как холодный.",
            )
        messages.info(request, "Основной контакт не отмечен как холодный.")
        return redirect("company_detail", company_id=company.id)

    # Откатываем отметку (убираем признак и метаданные, чтобы не показывать бейдж)
    company.primary_contact_is_cold_call = False
    company.primary_cold_marked_at = None
    company.primary_cold_marked_by = None
    company.primary_cold_marked_call = None
    company.save(update_fields=["primary_contact_is_cold_call", "primary_cold_marked_at", "primary_cold_marked_by", "primary_cold_marked_call", "updated_at"])

    if _is_ajax(request):
        return _cold_call_json(
            entity="company",
            entity_id=str(company.id),
            is_cold_call=False,
            marked_at=company.primary_cold_marked_at,
            marked_by="",
            can_reset=True,
            message="Отметка холодного звонка отменена (основной контакт).",
        )

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
@policy_required(resource_type="action", resource="ui:companies:cold_call:reset")
def contact_cold_call_reset(request: HttpRequest, contact_id) -> HttpResponse:
    """
    Откатить отметку холодного звонка для контакта.
    Доступно только администраторам.
    """
    if request.method != "POST":
        return redirect("dashboard")

    user: User = request.user
    if not require_admin(user):
        if _is_ajax(request):
            return JsonResponse({"ok": False, "error": "Только администратор может откатить отметку холодного звонка."}, status=403)
        messages.error(request, "Только администратор может откатить отметку холодного звонка.")
        return redirect("dashboard")

    contact = get_object_or_404(Contact.objects.select_related("company"), id=contact_id)
    company = contact.company
    if not company:
        messages.error(request, "Контакт не привязан к компании.")
        return redirect("dashboard")

    if not contact.is_cold_call:
        if _is_ajax(request):
            return _cold_call_json(
                entity="contact",
                entity_id=str(contact.id),
                is_cold_call=False,
                marked_at=contact.cold_marked_at,
                marked_by=str(contact.cold_marked_by or ""),
                can_reset=True,
                message="Контакт не отмечен как холодный.",
            )
        messages.info(request, "Контакт не отмечен как холодный.")
        return redirect("company_detail", company_id=company.id)

    # Откатываем отметку
    contact.is_cold_call = False
    # Важно для отчетов/аналитики: очищаем метаданные, иначе звонок продолжит считаться "подтвержденным".
    contact.cold_marked_at = None
    contact.cold_marked_by = None
    contact.cold_marked_call = None
    contact.save(update_fields=["is_cold_call", "cold_marked_at", "cold_marked_by", "cold_marked_call"])

    if _is_ajax(request):
        return _cold_call_json(
            entity="contact",
            entity_id=str(contact.id),
            is_cold_call=False,
            marked_at=contact.cold_marked_at,
            marked_by="",
            can_reset=True,
            message="Отметка холодного звонка отменена (контакт).",
        )

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
@policy_required(resource_type="action", resource="ui:companies:cold_call:toggle")
def contact_phone_cold_call_toggle(request: HttpRequest, contact_phone_id) -> HttpResponse:
    """
    Отметить конкретный номер телефона контакта как холодный звонок.
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
        if _is_ajax(request):
            return JsonResponse({"ok": False, "error": "Ошибка: номер телефона не найден."}, status=404)
        messages.error(request, f"Ошибка: номер телефона не найден.")
        return redirect("dashboard")
    contact = contact_phone.contact
    company = contact.company if contact else None
    if not company:
        messages.error(request, "Контакт не привязан к компании.")
        return redirect("dashboard")
    if not _can_edit_company(user, company):
        if _is_ajax(request):
            return JsonResponse({"ok": False, "error": "Нет прав на изменение контактов этой компании."}, status=403)
        messages.error(request, "Нет прав на изменение контактов этой компании.")
        return redirect("company_detail", company_id=company.id)

    # Проверка подтверждения
    confirmed = request.POST.get("confirmed") == "1"
    if not confirmed:
        if _is_ajax(request):
            return JsonResponse({"ok": False, "error": "Требуется подтверждение действия."}, status=400)
        messages.error(request, "Требуется подтверждение действия.")
        return redirect("company_detail", company_id=company.id)

    # Проверка: уже отмечен?
    if contact_phone.is_cold_call:
        if _is_ajax(request):
            return _cold_call_json(
                entity="contact_phone",
                entity_id=str(contact_phone.id),
                is_cold_call=True,
                marked_at=contact_phone.cold_marked_at,
                marked_by=str(contact_phone.cold_marked_by or ""),
                can_reset=bool(require_admin(user)),
                message="Этот номер уже отмечен как холодный.",
            )
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
        if _is_ajax(request):
            return JsonResponse({"ok": False, "error": "Не найден звонок по этому номеру телефона."}, status=400)
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

    if _is_ajax(request):
        return _cold_call_json(
            entity="contact_phone",
            entity_id=str(contact_phone.id),
            is_cold_call=True,
            marked_at=contact_phone.cold_marked_at,
            marked_by=str(contact_phone.cold_marked_by or ""),
            can_reset=bool(require_admin(user)),
            message=f"Отмечено: холодный звонок (номер {contact_phone.value}).",
        )

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
@policy_required(resource_type="action", resource="ui:companies:cold_call:reset")
def contact_phone_cold_call_reset(request: HttpRequest, contact_phone_id) -> HttpResponse:
    """
    Откатить отметку холодного звонка для конкретного номера телефона контакта.
    Доступно только администраторам.
    """
    if request.method != "POST":
        return redirect("dashboard")

    user: User = request.user
    if not require_admin(user):
        if _is_ajax(request):
            return JsonResponse({"ok": False, "error": "Только администратор может откатить отметку холодного звонка."}, status=403)
        messages.error(request, "Только администратор может откатить отметку холодного звонка.")
        return redirect("dashboard")

    contact_phone = get_object_or_404(ContactPhone.objects.select_related("contact__company"), id=contact_phone_id)
    contact = contact_phone.contact
    company = contact.company if contact else None
    if not company:
        messages.error(request, "Контакт не привязан к компании.")
        return redirect("dashboard")

    if not contact_phone.is_cold_call and not contact_phone.cold_marked_at:
        if _is_ajax(request):
            return _cold_call_json(
                entity="contact_phone",
                entity_id=str(contact_phone.id),
                is_cold_call=False,
                marked_at=contact_phone.cold_marked_at,
                marked_by=str(contact_phone.cold_marked_by or ""),
                can_reset=True,
                message="Этот номер не отмечен как холодный.",
            )
        messages.info(request, "Этот номер не отмечен как холодный.")
        return redirect("company_detail", company_id=company.id)

    # Откатываем отметку (убираем признак и метаданные, чтобы не показывать бейдж)
    contact_phone.is_cold_call = False
    contact_phone.cold_marked_at = None
    contact_phone.cold_marked_by = None
    contact_phone.cold_marked_call = None
    contact_phone.save(update_fields=["is_cold_call", "cold_marked_at", "cold_marked_by", "cold_marked_call"])

    if _is_ajax(request):
        return _cold_call_json(
            entity="contact_phone",
            entity_id=str(contact_phone.id),
            is_cold_call=False,
            marked_at=contact_phone.cold_marked_at,
            marked_by="",
            can_reset=True,
            message=f"Отметка холодного звонка отменена (номер {contact_phone.value}).",
        )

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


@login_required
@policy_required(resource_type="action", resource="ui:companies:cold_call:toggle")
def company_phone_cold_call_toggle(request: HttpRequest, company_phone_id) -> HttpResponse:
    """
    Отметить конкретный дополнительный номер телефона компании как холодный звонок.
    Аналогично contact_phone_cold_call_toggle, но для CompanyPhone.
    """
    if request.method != "POST":
        return redirect("dashboard")
    user: User = request.user
    import logging
    logger = logging.getLogger(__name__)
    try:
        company_phone = get_object_or_404(CompanyPhone.objects.select_related("company", "cold_marked_by"), id=company_phone_id)
    except Exception as e:
        logger.error(f"Error finding CompanyPhone {company_phone_id}: {e}", exc_info=True)
        if _is_ajax(request):
            return JsonResponse({"ok": False, "error": "Ошибка: номер телефона не найден."}, status=404)
        messages.error(request, f"Ошибка: номер телефона не найден.")
        return redirect("dashboard")
    company = company_phone.company
    if not _can_edit_company(user, company):
        if _is_ajax(request):
            return JsonResponse({"ok": False, "error": "Нет прав на изменение данных этой компании."}, status=403)
        messages.error(request, "Нет прав на изменение данных этой компании.")
        return redirect("company_detail", company_id=company.id)

    # Проверка подтверждения
    confirmed = request.POST.get("confirmed") == "1"
    if not confirmed:
        if _is_ajax(request):
            return JsonResponse({"ok": False, "error": "Требуется подтверждение действия."}, status=400)
        messages.error(request, "Требуется подтверждение действия.")
        return redirect("company_detail", company_id=company.id)

    # Проверка: уже отмечен?
    if company_phone.is_cold_call:
        if _is_ajax(request):
            return _cold_call_json(
                entity="company_phone",
                entity_id=str(company_phone.id),
                is_cold_call=True,
                marked_at=company_phone.cold_marked_at,
                marked_by=str(company_phone.cold_marked_by or ""),
                can_reset=bool(require_admin(user)),
                message="Этот номер уже отмечен как холодный.",
            )
        messages.info(request, "Этот номер уже отмечен как холодный.")
        return redirect("company_detail", company_id=company.id)

    # Ищем последний звонок по этому номеру телефона
    now = timezone.now()
    # Нормализуем номер телефона так же, как в phone_call_create
    raw = company_phone.value.replace(" ", "").replace("-", "").replace("(", "").replace(")", "")
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
        CallRequest.objects.filter(created_by=user, company=company, phone_raw=normalized_phone)
        .order_by("-created_at")
        .first()
    )
    if not last_call:
        if _is_ajax(request):
            return JsonResponse({"ok": False, "error": "Не найден звонок по этому номеру телефона."}, status=400)
        messages.error(request, "Не найден звонок по этому номеру телефона.")
        return redirect("company_detail", company_id=company.id)

    # Отмечаем как холодный
    company_phone.is_cold_call = True
    company_phone.cold_marked_at = now
    company_phone.cold_marked_by = user
    company_phone.cold_marked_call = last_call
    company_phone.save(update_fields=["is_cold_call", "cold_marked_at", "cold_marked_by", "cold_marked_call"])

    if not last_call.is_cold_call:
        last_call.is_cold_call = True
        last_call.save(update_fields=["is_cold_call"])

    if _is_ajax(request):
        return _cold_call_json(
            entity="company_phone",
            entity_id=str(company_phone.id),
            is_cold_call=True,
            marked_at=company_phone.cold_marked_at,
            marked_by=str(company_phone.cold_marked_by or ""),
            can_reset=bool(require_admin(user)),
            message=f"Отмечено: холодный звонок (номер {company_phone.value}).",
        )

    messages.success(request, f"Отмечено: холодный звонок (номер {company_phone.value}).")
    log_event(
        actor=user,
        verb=ActivityEvent.Verb.UPDATE,
        entity_type="company_phone",
        entity_id=str(company_phone.id),
        company_id=company.id,
        message=f"Отмечено: холодный звонок (номер {company_phone.value})",
        meta={"company_phone_id": str(company_phone.id), "call_id": str(last_call.id)},
    )
    return redirect("company_detail", company_id=company.id)


@login_required
@policy_required(resource_type="action", resource="ui:companies:cold_call:reset")
def company_phone_cold_call_reset(request: HttpRequest, company_phone_id) -> HttpResponse:
    """
    Откатить отметку холодного звонка для конкретного дополнительного номера телефона компании.
    Доступно только администраторам.
    """
    if request.method != "POST":
        return redirect("dashboard")

    user: User = request.user
    if not require_admin(user):
        if _is_ajax(request):
            return JsonResponse({"ok": False, "error": "Только администратор может откатить отметку холодного звонка."}, status=403)
        messages.error(request, "Только администратор может откатить отметку холодного звонка.")
        return redirect("dashboard")

    company_phone = get_object_or_404(CompanyPhone.objects.select_related("company"), id=company_phone_id)
    company = company_phone.company

    if not company_phone.is_cold_call and not company_phone.cold_marked_at:
        if _is_ajax(request):
            return _cold_call_json(
                entity="company_phone",
                entity_id=str(company_phone.id),
                is_cold_call=False,
                marked_at=company_phone.cold_marked_at,
                marked_by=str(company_phone.cold_marked_by or ""),
                can_reset=True,
                message="Этот номер не отмечен как холодный.",
            )
        messages.info(request, "Этот номер не отмечен как холодный.")
        return redirect("company_detail", company_id=company.id)

    # Откатываем отметку (убираем признак и метаданные, чтобы не показывать бейдж)
    company_phone.is_cold_call = False
    company_phone.cold_marked_at = None
    company_phone.cold_marked_by = None
    company_phone.cold_marked_call = None
    company_phone.save(update_fields=["is_cold_call", "cold_marked_at", "cold_marked_by", "cold_marked_call"])

    if _is_ajax(request):
        return _cold_call_json(
            entity="company_phone",
            entity_id=str(company_phone.id),
            is_cold_call=False,
            marked_at=company_phone.cold_marked_at,
            marked_by="",
            can_reset=True,
            message=f"Отметка холодного звонка отменена (номер {company_phone.value}).",
        )

    messages.success(request, f"Отметка холодного звонка отменена (номер {company_phone.value}).")
    log_event(
        actor=user,
        verb=ActivityEvent.Verb.UPDATE,
        entity_type="company_phone",
        entity_id=str(company_phone.id),
        company_id=company.id,
        message=f"Откат: холодный звонок (номер {company_phone.value})",
    )
    return redirect("company_detail", company_id=company.id)


@login_required
@policy_required(resource_type="action", resource="ui:companies:update")
@require_can_view_company
def company_main_phone_update(request: HttpRequest, company_id) -> HttpResponse:
    """Обновление основного телефона компании (AJAX)"""
    if request.method != "POST":
        if request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return JsonResponse({"success": False, "error": "Метод не разрешен."}, status=405)
        return redirect("company_detail", company_id=company_id)

    user: User = request.user
    company = get_object_or_404(Company.objects.select_related("responsible", "branch"), id=company_id)
    if not _can_edit_company(user, company):
        if request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return JsonResponse({"success": False, "error": "Нет прав на редактирование этой компании."}, status=403)
        messages.error(request, "Нет прав на редактирование этой компании.")
        return redirect("company_detail", company_id=company.id)

    raw = (request.POST.get("phone") or "").strip()
    from ui.forms import _normalize_phone
    normalized = _normalize_phone(raw) if raw else ""

    # Проверка дублей с доп. телефонами
    if normalized:
        exists = CompanyPhone.objects.filter(company=company, value=normalized).exists()
        if exists:
            return JsonResponse({"success": False, "error": "Такой телефон уже есть в дополнительных номерах."}, status=400)

    company.phone = normalized
    company.save(update_fields=["phone", "updated_at"])

    log_event(
        actor=user,
        verb=ActivityEvent.Verb.UPDATE,
        entity_type="company",
        entity_id=company.id,
        company_id=company.id,
        message="Инлайн: обновлен основной телефон",
    )

    try:
        from ui.templatetags.ui_extras import phone_local_info  # type: ignore
        local_info = phone_local_info(normalized)
    except Exception:
        local_info = ""

    return JsonResponse({"success": True, "phone": normalized, "display": format_phone(normalized) if normalized else "—", "local_info": local_info})


@login_required
@policy_required(resource_type="action", resource="ui:companies:update")
def company_phone_value_update(request: HttpRequest, company_phone_id) -> HttpResponse:
    """Обновление значения дополнительного телефона компании (AJAX)"""
    if request.method != "POST":
        if request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return JsonResponse({"success": False, "error": "Метод не разрешен."}, status=405)
        return redirect("dashboard")

    user: User = request.user
    company_phone = get_object_or_404(CompanyPhone.objects.select_related("company"), id=company_phone_id)
    company = company_phone.company
    if not _can_edit_company(user, company):
        if request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return JsonResponse({"success": False, "error": "Нет прав на редактирование этой компании."}, status=403)
        messages.error(request, "Нет прав на редактирование этой компании.")
        return redirect("company_detail", company_id=company.id)

    raw = (request.POST.get("phone") or "").strip()
    from ui.forms import _normalize_phone
    normalized = _normalize_phone(raw) if raw else ""
    if not normalized:
        return JsonResponse({"success": False, "error": "Телефон не может быть пустым."}, status=400)

    # Дубли: основной телефон и другие доп. телефоны
    if (company.phone or "").strip() == normalized:
        return JsonResponse({"success": False, "error": "Этот телефон уже указан как основной."}, status=400)
    if CompanyPhone.objects.filter(company=company, value=normalized).exclude(id=company_phone.id).exists():
        return JsonResponse({"success": False, "error": "Такой телефон уже есть в дополнительных номерах."}, status=400)

    company_phone.value = normalized
    company_phone.save(update_fields=["value"])

    log_event(
        actor=user,
        verb=ActivityEvent.Verb.UPDATE,
        entity_type="company_phone",
        entity_id=str(company_phone.id),
        company_id=company.id,
        message="Инлайн: обновлен дополнительный телефон",
    )

    try:
        from ui.templatetags.ui_extras import phone_local_info  # type: ignore
        local_info = phone_local_info(normalized)
    except Exception:
        local_info = ""

    return JsonResponse({"success": True, "phone": normalized, "display": format_phone(normalized), "local_info": local_info})


@login_required
@policy_required(resource_type="action", resource="ui:companies:update")
@require_can_view_company
def company_phone_create(request: HttpRequest, company_id) -> HttpResponse:
    """Создание дополнительного телефона компании (AJAX)"""
    if request.method != "POST":
        if request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return JsonResponse({"success": False, "error": "Метод не разрешен."}, status=405)
        return redirect("company_detail", company_id=company_id)

    user: User = request.user
    company = get_object_or_404(Company.objects.select_related("responsible", "branch"), id=company_id)
    if not _can_edit_company(user, company):
        return JsonResponse({"success": False, "error": "Нет прав на редактирование этой компании."}, status=403)

    raw = (request.POST.get("phone") or "").strip()
    from ui.forms import _normalize_phone
    normalized = _normalize_phone(raw) if raw else ""
    if not normalized:
        return JsonResponse({"success": False, "error": "Телефон не может быть пустым."}, status=400)

    # Дубли: основной телефон и другие доп. телефоны
    if (company.phone or "").strip() == normalized:
        return JsonResponse({"success": False, "error": "Этот телефон уже указан как основной."}, status=400)
    if CompanyPhone.objects.filter(company=company, value=normalized).exists():
        return JsonResponse({"success": False, "error": "Такой телефон уже есть в дополнительных номерах."}, status=400)

    from django.db.models import Max

    max_order = CompanyPhone.objects.filter(company=company).aggregate(m=Max("order")).get("m")
    next_order = int(max_order) + 1 if max_order is not None else 0

    company_phone = CompanyPhone.objects.create(company=company, value=normalized, order=next_order)
    log_event(
        actor=user,
        verb=ActivityEvent.Verb.CREATE,
        entity_type="company_phone",
        entity_id=str(company_phone.id),
        company_id=company.id,
        message="Инлайн: добавлен дополнительный телефон",
    )

    try:
        from ui.templatetags.ui_extras import phone_local_info  # type: ignore
        local_info = phone_local_info(normalized)
    except Exception:
        local_info = ""

    return JsonResponse({"success": True, "id": company_phone.id, "phone": normalized, "display": format_phone(normalized), "local_info": local_info})


@login_required
@policy_required(resource_type="action", resource="ui:companies:update")
@require_can_view_company
def company_main_email_update(request: HttpRequest, company_id) -> HttpResponse:
    """Обновление основного email компании (AJAX)"""
    if request.method != "POST":
        if request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return JsonResponse({"success": False, "error": "Метод не разрешен."}, status=405)
        return redirect("company_detail", company_id=company_id)

    user: User = request.user
    company = get_object_or_404(Company.objects.select_related("responsible", "branch"), id=company_id)
    if not _can_edit_company(user, company):
        if request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return JsonResponse({"success": False, "error": "Нет прав на редактирование этой компании."}, status=403)
        messages.error(request, "Нет прав на редактирование этой компании.")
        return redirect("company_detail", company_id=company.id)

    raw = (request.POST.get("email") or "").strip()
    email = raw.lower()
    if email:
        try:
            validate_email(email)
        except ValidationError:
            return JsonResponse({"success": False, "error": "Некорректный email."}, status=400)

        # Дубли с доп. email
        if CompanyEmail.objects.filter(company=company, value__iexact=email).exists():
            return JsonResponse({"success": False, "error": "Такой email уже есть в дополнительных адресах."}, status=400)

    company.email = email
    company.save(update_fields=["email", "updated_at"])

    log_event(
        actor=user,
        verb=ActivityEvent.Verb.UPDATE,
        entity_type="company",
        entity_id=company.id,
        company_id=company.id,
        message="Инлайн: обновлен основной email",
    )
    return JsonResponse({"success": True, "email": email})


@login_required
@policy_required(resource_type="action", resource="ui:companies:update")
def company_email_value_update(request: HttpRequest, company_email_id) -> HttpResponse:
    """Обновление значения дополнительного email компании (AJAX)"""
    if request.method != "POST":
        if request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return JsonResponse({"success": False, "error": "Метод не разрешен."}, status=405)
        return redirect("dashboard")

    user: User = request.user
    company_email = get_object_or_404(CompanyEmail.objects.select_related("company"), id=company_email_id)
    company = company_email.company
    if not _can_edit_company(user, company):
        if request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return JsonResponse({"success": False, "error": "Нет прав на редактирование этой компании."}, status=403)
        messages.error(request, "Нет прав на редактирование этой компании.")
        return redirect("company_detail", company_id=company.id)

    raw = (request.POST.get("email") or "").strip()
    email = raw.lower()
    if not email:
        return JsonResponse({"success": False, "error": "Email не может быть пустым."}, status=400)
    try:
        validate_email(email)
    except ValidationError:
        return JsonResponse({"success": False, "error": "Некорректный email."}, status=400)

    # Дубли: основной email и другие доп. email
    if (company.email or "").strip().lower() == email:
        return JsonResponse({"success": False, "error": "Этот email уже указан как основной."}, status=400)
    if CompanyEmail.objects.filter(company=company, value__iexact=email).exclude(id=company_email.id).exists():
        return JsonResponse({"success": False, "error": "Такой email уже есть в дополнительных адресах."}, status=400)

    company_email.value = email
    company_email.save(update_fields=["value"])

    log_event(
        actor=user,
        verb=ActivityEvent.Verb.UPDATE,
        entity_type="company_email",
        entity_id=str(company_email.id),
        company_id=company.id,
        message="Инлайн: обновлен дополнительный email",
    )

    return JsonResponse({"success": True, "email": email})


@login_required
@policy_required(resource_type="action", resource="ui:companies:update")
@require_can_view_company
def company_main_phone_comment_update(request: HttpRequest, company_id) -> HttpResponse:
    """Обновление комментария к основному телефону компании (AJAX)"""
    if request.method != "POST":
        if request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return JsonResponse({"error": "Метод не разрешен."}, status=405)
        return redirect("company_detail", company_id=company_id)
    
    user: User = request.user
    try:
        company = Company.objects.select_related("responsible", "branch").get(id=company_id)
    except Company.DoesNotExist:
        if request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return JsonResponse({"error": "Компания не найдена."}, status=404)
        raise Http404("Компания не найдена")
    
    if not _can_edit_company(user, company):
        if request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return JsonResponse({"error": "Нет прав на редактирование этой компании."}, status=403)
        messages.error(request, "Нет прав на редактирование этой компании.")
        return redirect("company_detail", company_id=company.id)
    
    comment = (request.POST.get("comment") or "").strip()[:255]
    company.phone_comment = comment
    company.save(update_fields=["phone_comment"])
    
    log_event(
        actor=user,
        verb=ActivityEvent.Verb.UPDATE,
        entity_type="company",
        entity_id=company.id,
        company_id=company.id,
        message=f"Обновлен комментарий к основному телефону: {comment[:50] if comment else '(удален)'}",
    )
    
    if request.headers.get("X-Requested-With") == "XMLHttpRequest":
        return JsonResponse({"success": True, "comment": comment})
    
    messages.success(request, "Комментарий обновлен.")
    return redirect("company_detail", company_id=company.id)


@login_required
@policy_required(resource_type="action", resource="ui:companies:update")
def company_phone_comment_update(request: HttpRequest, company_phone_id) -> HttpResponse:
    """Обновление комментария к дополнительному телефону компании (AJAX)"""
    if request.method != "POST":
        if request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return JsonResponse({"error": "Метод не разрешен."}, status=405)
        return redirect("dashboard")
    
    user: User = request.user
    try:
        company_phone = CompanyPhone.objects.select_related("company").get(id=company_phone_id)
    except CompanyPhone.DoesNotExist:
        if request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return JsonResponse({"error": "Номер телефона не найден."}, status=404)
        raise Http404("Номер телефона не найден")
    
    company = company_phone.company
    if not _can_edit_company(user, company):
        if request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return JsonResponse({"error": "Нет прав на редактирование этой компании."}, status=403)
        messages.error(request, "Нет прав на редактирование этой компании.")
        return redirect("company_detail", company_id=company.id)
    
    comment = (request.POST.get("comment") or "").strip()[:255]
    company_phone.comment = comment
    company_phone.save(update_fields=["comment"])
    
    log_event(
        actor=user,
        verb=ActivityEvent.Verb.UPDATE,
        entity_type="company_phone",
        entity_id=str(company_phone.id),
        company_id=company.id,
        message=f"Обновлен комментарий к телефону {company_phone.value}: {comment[:50] if comment else '(удален)'}",
    )
    
    if request.headers.get("X-Requested-With") == "XMLHttpRequest":
        return JsonResponse({"success": True, "comment": comment})
    
    messages.success(request, "Комментарий обновлен.")
    return redirect("company_detail", company_id=company.id)


@login_required
@policy_required(resource_type="action", resource="ui:companies:update")
def contact_phone_comment_update(request: HttpRequest, contact_phone_id) -> HttpResponse:
    """Обновление комментария к телефону контакта (AJAX)"""
    if request.method != "POST":
        if request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return JsonResponse({"error": "Метод не разрешен."}, status=405)
        return redirect("dashboard")
    
    user: User = request.user
    try:
        contact_phone = ContactPhone.objects.select_related("contact__company").get(id=contact_phone_id)
    except ContactPhone.DoesNotExist:
        if request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return JsonResponse({"error": "Номер телефона не найден."}, status=404)
        raise Http404("Номер телефона не найден")
    
    contact = contact_phone.contact
    company = contact.company if contact else None
    if not company:
        if request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return JsonResponse({"error": "Контакт не привязан к компании."}, status=400)
        messages.error(request, "Контакт не привязан к компании.")
        return redirect("dashboard")
    
    if not _can_edit_company(user, company):
        if request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return JsonResponse({"error": "Нет прав на редактирование этой компании."}, status=403)
        messages.error(request, "Нет прав на редактирование этой компании.")
        return redirect("company_detail", company_id=company.id)
    
    comment = (request.POST.get("comment") or "").strip()[:255]
    contact_phone.comment = comment
    contact_phone.save(update_fields=["comment"])
    
    log_event(
        actor=user,
        verb=ActivityEvent.Verb.UPDATE,
        entity_type="contact_phone",
        entity_id=str(contact_phone.id),
        company_id=company.id,
        message=f"Обновлен комментарий к телефону {contact_phone.value}: {comment[:50] if comment else '(удален)'}",
    )
    
    if request.headers.get("X-Requested-With") == "XMLHttpRequest":
        return JsonResponse({"success": True, "comment": comment})
    
    messages.success(request, "Комментарий обновлен.")
    return redirect("company_detail", company_id=company.id)


@login_required
@policy_required(resource_type="action", resource="ui:companies:update")
@require_can_view_company
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
@policy_required(resource_type="page", resource="ui:companies:detail")
@require_can_view_note_company
def company_note_attachment_open(request: HttpRequest, company_id, note_id: int) -> HttpResponse:
    """
    Открыть вложение заметки в новом окне (inline). Доступ: всем пользователям (как просмотр компании).
    """
    company = get_object_or_404(Company.objects.all(), id=company_id)
    note = get_object_or_404(CompanyNote.objects.select_related("company"), id=note_id, company_id=company.id)
    if not note.attachment:
        raise Http404("Файл не найден")
    ctype = (note.attachment_content_type or "").strip()
    if not ctype:
        ctype = mimetypes.guess_type(note.attachment_name or note.attachment.name)[0] or "application/octet-stream"
    try:
        return FileResponse(
            open(note.attachment.path, "rb"),
            as_attachment=False,
            filename=(note.attachment_name or "file"),
            content_type=ctype,
        )
    except FileNotFoundError:
        return HttpResponseNotFound("Файл вложения не найден.")


@login_required
@policy_required(resource_type="page", resource="ui:companies:detail")
@require_can_view_note_company
def company_note_attachment_download(request: HttpRequest, company_id, note_id: int) -> HttpResponse:
    """
    Скачать вложение заметки (attachment). Доступ: всем пользователям (как просмотр компании).
    """
    company = get_object_or_404(Company.objects.all(), id=company_id)
    note = get_object_or_404(CompanyNote.objects.select_related("company"), id=note_id, company_id=company.id)
    if not note.attachment:
        raise Http404("Файл не найден")
    ctype = (note.attachment_content_type or "").strip()
    if not ctype:
        ctype = mimetypes.guess_type(note.attachment_name or note.attachment.name)[0] or "application/octet-stream"
    try:
        return FileResponse(
            open(note.attachment.path, "rb"),
            as_attachment=True,
            filename=(note.attachment_name or "file"),
            content_type=ctype,
        )
    except FileNotFoundError:
        return HttpResponseNotFound("Файл вложения не найден.")


@login_required
@policy_required(resource_type="action", resource="ui:companies:update")
@require_can_view_company
def company_edit(request: HttpRequest, company_id) -> HttpResponse:
    user: User = request.user
    company = get_object_or_404(Company.objects.select_related("responsible", "branch", "status"), id=company_id)
    if not _can_edit_company(user, company):
        messages.error(request, "Нет прав на редактирование данных компании.")
        return redirect("company_detail", company_id=company.id)

    company_emails: list[CompanyEmail] = []
    company_phones: list[CompanyPhone] = []

    if request.method == "POST":
        form = CompanyEditForm(request.POST, instance=company, user=user)
        if form.is_valid():
            # Вспомогательные структуры: (index, value)
            new_company_emails: list[tuple[int, str]] = []
            new_company_phones: list[tuple[int, str]] = []

            # Сохраняем множественные email адреса
            for key, value in request.POST.items():
                if key.startswith("company_emails_"):
                    raw = (value or "").strip()
                    if not raw:
                        continue
                    try:
                        index = int(key.replace("company_emails_", ""))
                    except (ValueError, TypeError):
                        continue
                    new_company_emails.append((index, raw))

            # Сохраняем множественные телефоны компании
            for key, value in request.POST.items():
                if key.startswith("company_phones_"):
                    raw = (value or "").strip()
                    if not raw:
                        continue
                    try:
                        index = int(key.replace("company_phones_", ""))
                    except (ValueError, TypeError):
                        continue
                    new_company_phones.append((index, raw))

            # Валидация телефонов: проверка на дубликаты и использование в других контактах
            from ui.forms import _normalize_phone
            
            # Собираем все телефоны (основной + дополнительные)
            all_phones = []
            main_phone = (form.cleaned_data.get("phone") or "").strip()
            if main_phone:
                normalized_main = _normalize_phone(main_phone)
                if normalized_main:
                    all_phones.append(normalized_main)
            
            normalized_phones = []
            for _, phone_value in new_company_phones:
                normalized = _normalize_phone(phone_value)
                if normalized:
                    normalized_phones.append(normalized)
                    all_phones.append(normalized)
            
            # Проверка на дубликаты в самой форме (включая основной телефон)
            if len(all_phones) != len(set(all_phones)):
                form.add_error(None, "Есть повторяющиеся телефоны (основной телефон не должен совпадать с дополнительными).")
                # Восстанавливаем введённые значения для отображения ошибки
                for key, value in request.POST.items():
                    if key.startswith("company_emails_"):
                        company_emails.append(
                            CompanyEmail(company=company, value=(value or "").strip())
                        )
                    if key.startswith("company_phones_"):
                        company_phones.append(
                            CompanyPhone(company=company, value=(value or "").strip())
                        )
                return render(
                    request,
                    "ui/company_edit.html",
                    {"company": company, "form": form, "company_emails": company_emails, "company_phones": company_phones},
                )
            
            # Сохраняем форму (включая основной телефон)
            form.save()

            # Удаляем старые значения и создаем новые в упорядоченном виде
            CompanyEmail.objects.filter(company=company).delete()
            CompanyPhone.objects.filter(company=company).delete()

            for order, email_value in sorted(new_company_emails, key=lambda x: x[0]):
                CompanyEmail.objects.create(company=company, value=email_value, order=order)

            for order, phone_value in sorted(new_company_phones, key=lambda x: x[0]):
                # Нормализуем телефон перед сохранением
                normalized = _normalize_phone(phone_value)
                CompanyPhone.objects.create(company=company, value=normalized if normalized else phone_value, order=order)

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
            # При ошибках в форме восстанавливаем введённые значения из POST,
            # чтобы пользователь не потерял данные.
            for key, value in request.POST.items():
                if key.startswith("company_emails_"):
                    company_emails.append(
                        CompanyEmail(company=company, value=(value or "").strip())
                    )
                if key.startswith("company_phones_"):
                    company_phones.append(
                        CompanyPhone(company=company, value=(value or "").strip())
                    )
    else:
        form = CompanyEditForm(instance=company, user=user)
        # Загружаем существующие email и телефоны для отображения в форме
        company_emails = list(company.emails.all())
        company_phones = list(company.phones.all())

    return render(
        request,
        "ui/company_edit.html",
        {"company": company, "form": form, "company_emails": company_emails, "company_phones": company_phones},
    )


@login_required
@policy_required(resource_type="action", resource="ui:companies:transfer")
@transaction.atomic
@require_can_view_company
def company_transfer(request: HttpRequest, company_id) -> HttpResponse:
    if request.method != "POST":
        return redirect("company_detail", company_id=company_id)

    user: User = request.user
    company = get_object_or_404(Company.objects.select_related("responsible", "branch"), id=company_id)
    
    # Проверка прав на передачу (используем новую функцию)
    if not can_transfer_company(user, company):
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
    old_resp_id = company.responsible_id
    
    # При передаче обновляем филиал компании под филиал нового ответственного (может быть другой регион).
    company.responsible = new_resp
    
    # Инвалидируем кэш количества компаний после передачи
    _invalidate_company_count_cache()
    company.branch = new_resp.branch
    company.save(update_fields=["responsible", "branch", "updated_at"])
    
    # Логируем для отладки
    import logging
    logger = logging.getLogger(__name__)
    logger.info(
        f"Company transferred: company_id={company.id}, "
        f"old_responsible_id={old_resp_id}, new_responsible_id={new_resp.id}, "
        f"transferred_by_user_id={user.id}"
    )
    
    _invalidate_company_count_cache()  # Инвалидируем кэш при передаче компании

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
@policy_required(resource_type="action", resource="ui:companies:update")
@require_can_view_company
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
        _invalidate_company_count_cache()  # Инвалидируем кэш при обновлении (на случай изменения статуса/филиала)
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
@policy_required(resource_type="action", resource="ui:companies:update")
@require_can_view_company
def company_inline_update(request: HttpRequest, company_id) -> HttpResponse:
    """
    Инлайн-обновление одного поля компании (AJAX) из карточки компании.
    Вход: POST {field, value}. Выход: JSON.
    """
    if request.method != "POST":
        if request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return JsonResponse({"ok": False, "error": "Метод не разрешен."}, status=405)
        return redirect("company_detail", company_id=company_id)

    user: User = request.user
    company = get_object_or_404(
        Company.objects.select_related("responsible", "branch", "region", "contract_type"),
        id=company_id,
    )
    if not _can_edit_company(user, company):
        if request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return JsonResponse({"ok": False, "error": "Нет прав на редактирование этой компании."}, status=403)
        messages.error(request, "Нет прав на редактирование этой компании.")
        return redirect("company_detail", company_id=company.id)

    field = (request.POST.get("field") or "").strip()
    if field not in CompanyInlineEditForm.ALLOWED_FIELDS:
        return JsonResponse({"ok": False, "error": "Недопустимое поле."}, status=400)

    value = request.POST.get("value")
    data = {field: value}
    form = CompanyInlineEditForm(data=data, instance=company, field=field)
    if not form.is_valid():
        return JsonResponse({"ok": False, "errors": form.errors, "error": "Проверь значение поля."}, status=400)

    form.save()

    # Для внешних ключей и спец-полей приводим значение к строке для JSON.
    if field == "region":
        updated_value = company.region.name if company.region else ""
    elif field == "employees_count":
        v = getattr(company, field, None)
        updated_value = str(v) if v is not None else ""
    elif field == "contract_amount":
        # Для суммы форматируем как число с двумя знаками после запятой
        amount = getattr(company, field, None)
        if amount is not None:
            updated_value = f"{float(amount):.2f}"
        else:
            updated_value = ""
    else:
        updated_value = getattr(company, field, "")

    log_event(
        actor=user,
        verb=ActivityEvent.Verb.UPDATE,
        entity_type="company",
        entity_id=company.id,
        company_id=company.id,
        message=f"Инлайн-обновление поля компании: {field}",
        meta={"field": field},
    )

    # Для часового пояса отдаём доп. данные, чтобы можно было обновить UI без перезагрузки
    if field == "work_timezone":
        try:
            from zoneinfo import ZoneInfo
            from ui.timezone_utils import RUS_TZ_CHOICES, guess_ru_timezone_from_address

            guessed = guess_ru_timezone_from_address(company.address or "")
            effective_tz = (((company.work_timezone or "").strip()) or guessed or "Europe/Moscow").strip()
            label_map = {tz: lbl for tz, lbl in (RUS_TZ_CHOICES or [])}
            effective_label = label_map.get(effective_tz, effective_tz)
            now_hhmm = timezone.now().astimezone(ZoneInfo(effective_tz)).strftime("%H:%M")
        except Exception:
            effective_tz = (company.work_timezone or "").strip() or ""
            effective_label = effective_tz
            now_hhmm = ""

        return JsonResponse(
            {
                "ok": True,
                "field": field,
                # value = сохранённое значение (может быть пустым = авто)
                "value": (updated_value or ""),
                "effective_tz": effective_tz,
                "effective_label": effective_label,
                "effective_now_hhmm": now_hhmm,
            }
        )

    return JsonResponse({"ok": True, "field": field, "value": updated_value})


@login_required
@policy_required(resource_type="action", resource="ui:companies:update")
def contact_create(request: HttpRequest, company_id) -> HttpResponse:
    user: User = request.user
    company = get_object_or_404(Company.objects.select_related("responsible", "branch"), id=company_id)
    if not _can_edit_company(user, company):
        messages.error(request, "Нет прав на добавление контактов в эту компанию.")
        return redirect("company_detail", company_id=company.id)

    contact = Contact(company=company)
    # Определяем модальный режим: по заголовку AJAX или параметру modal=1
    is_modal = (
        request.headers.get("x-requested-with", "").lower() == "xmlhttprequest"
        or request.headers.get("X-Requested-With", "").lower() == "xmlhttprequest"
        or (request.GET.get("modal") == "1")
        or (request.POST.get("modal") == "1")
    )

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
            if is_modal:
                return JsonResponse({"ok": True, "redirect": f"/companies/{company.id}/"})
            return redirect("company_detail", company_id=company.id)
        if is_modal:
            from django.template.loader import render_to_string
            html = render_to_string(
                "ui/contact_form_modal.html",
                {"company": company, "form": form, "email_fs": email_fs, "phone_fs": phone_fs, "mode": "create"},
                request=request,
            )
            return JsonResponse({"ok": False, "html": html}, status=400)
    else:
        form = ContactForm(instance=contact)
        email_fs = ContactEmailFormSet(instance=contact, prefix="emails")
        phone_fs = ContactPhoneFormSet(instance=contact, prefix="phones")

    context = {
        "company": company,
        "form": form,
        "email_fs": email_fs,
        "phone_fs": phone_fs,
        "mode": "create",
        "action_url": f"/companies/{company.id}/contacts/new/",
    }
    if is_modal:
        return render(request, "ui/contact_form_modal.html", context)
    return render(request, "ui/contact_form.html", context)


@login_required
@policy_required(resource_type="action", resource="ui:companies:update")
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

    # Определяем модальный режим: по заголовку AJAX или параметру modal=1
    is_modal = (
        request.headers.get("x-requested-with", "").lower() == "xmlhttprequest"
        or request.headers.get("X-Requested-With", "").lower() == "xmlhttprequest"
        or (request.GET.get("modal") == "1")
        or (request.POST.get("modal") == "1")
    )

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
            if is_modal:
                return JsonResponse({"ok": True, "redirect": f"/companies/{company.id}/"})
            return redirect("company_detail", company_id=company.id)
        if is_modal:
            from django.template.loader import render_to_string
            html = render_to_string(
                "ui/contact_form_modal.html",
                {"company": company, "contact": contact, "form": form, "email_fs": email_fs, "phone_fs": phone_fs, "mode": "edit"},
                request=request,
            )
            return JsonResponse({"ok": False, "html": html}, status=400)
    else:
        form = ContactForm(instance=contact)
        email_fs = ContactEmailFormSet(instance=contact, prefix="emails")
        phone_fs = ContactPhoneFormSet(instance=contact, prefix="phones")

    context = {
        "company": company,
        "contact": contact,
        "form": form,
        "email_fs": email_fs,
        "phone_fs": phone_fs,
        "mode": "edit",
        "action_url": f"/contacts/{contact.id}/edit/",
    }
    if is_modal:
        return render(request, "ui/contact_form_modal.html", context)
    return render(request, "ui/contact_form.html", context)


@login_required
@policy_required(resource_type="action", resource="ui:companies:update")
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
@policy_required(resource_type="action", resource="ui:companies:update")
@require_can_view_company
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
            except Exception as e:
                logger.warning(
                    f"Ошибка при извлечении метаданных вложения заметки: {e}",
                    exc_info=True,
                    extra={"company_id": str(company.id), "note_id": note.id if hasattr(note, "id") else None},
                )
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
@policy_required(resource_type="action", resource="ui:companies:update")
@require_can_view_company
def company_note_edit(request: HttpRequest, company_id, note_id: int) -> HttpResponse:
    if request.method != "POST":
        return redirect("company_detail", company_id=company_id)

    user: User = request.user
    company = get_object_or_404(Company.objects.all(), id=company_id)

    # Редактировать заметки:
    # - админ/суперпользователь/управляющий: любые
    # - остальные: только свои ИЛИ заметки без автора (author=None), если пользователь - ответственный за компанию
    if user.is_superuser or user.role in (User.Role.ADMIN, User.Role.GROUP_MANAGER):
        note = get_object_or_404(CompanyNote.objects.select_related("author"), id=note_id, company_id=company.id)
    else:
        # Обычные пользователи могут редактировать свои заметки или заметки без автора, если они ответственные за компанию
        note_qs = CompanyNote.objects.select_related("author").filter(id=note_id, company_id=company.id)
        if company.responsible_id == user.id:
            # Ответственный может редактировать свои заметки и заметки без автора
            note = get_object_or_404(note_qs.filter(Q(author_id=user.id) | Q(author__isnull=True)))
        else:
            # Остальные могут редактировать только свои заметки
            note = get_object_or_404(note_qs, author_id=user.id)

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
        except Exception as e:
            logger.warning(
                f"Ошибка при извлечении метаданных нового вложения заметки: {e}",
                exc_info=True,
                extra={"company_id": str(company.id), "note_id": note.id if hasattr(note, "id") else None},
            )

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
    except Exception as e:
        logger.warning(
            f"Ошибка при удалении старого файла вложения заметки: {e}",
            exc_info=True,
            extra={"company_id": str(company.id), "note_id": note.id if hasattr(note, "id") else None},
        )

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
@policy_required(resource_type="action", resource="ui:companies:update")
@require_can_view_company
def company_note_delete(request: HttpRequest, company_id, note_id: int) -> HttpResponse:
    if request.method != "POST":
        return redirect("company_detail", company_id=company_id)

    user: User = request.user
    company = get_object_or_404(Company.objects.all(), id=company_id)

    # Удалять заметки:
    # - админ/суперпользователь/управляющий: любые
    # - остальные: только свои ИЛИ заметки без автора (author=None), если пользователь - ответственный за компанию
    if user.is_superuser or user.role in (User.Role.ADMIN, User.Role.GROUP_MANAGER):
        note = get_object_or_404(CompanyNote.objects.select_related("author"), id=note_id, company_id=company.id)
    else:
        # Обычные пользователи могут удалять свои заметки или заметки без автора, если они ответственные за компанию
        note_qs = CompanyNote.objects.select_related("author").filter(id=note_id, company_id=company.id)
        if company.responsible_id == user.id:
            # Ответственный может удалять свои заметки и заметки без автора
            note = get_object_or_404(note_qs.filter(Q(author_id=user.id) | Q(author__isnull=True)))
        else:
            # Остальные могут удалять только свои заметки
            note = get_object_or_404(note_qs, author_id=user.id)
    # Удаляем вложенный файл из storage, затем запись
    try:
        if note.attachment:
            note.attachment.delete(save=False)
    except Exception as e:
        logger.warning(
            f"Ошибка при удалении файла вложения заметки: {e}",
            exc_info=True,
            extra={"company_id": str(company.id), "note_id": note_id},
        )
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
@policy_required(resource_type="action", resource="ui:companies:update")
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
    # Маскируем номер телефона для логов (защита от утечки персональных данных)
    from phonebridge.api import mask_phone, send_fcm_call_command_notification
    masked_phone = mask_phone(normalized) if normalized else "N/A"
    logger.info(
        "phone_call_create: created CallRequest %s for user %s, phone %s, device check: %s",
        call.id,
        user.id,
        masked_phone,
        PhoneDevice.objects.filter(user=user).exists(),
    )

    # FCM-ускоритель: отправляем data-push на все устройства пользователя с fcm_token.
    # Push только пробуждает pullCall на клиенте, сама команда всё равно будет доставлена через /calls/pull/.
    try:
        devices_with_fcm = PhoneDevice.objects.filter(user=user).exclude(fcm_token="")
        for device in devices_with_fcm:
            send_fcm_call_command_notification(device, reason="new_call")
    except Exception as e:
        logger.warning("phone_call_create: failed to send FCM notifications for CallRequest %s: %s", call.id, e)
    
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
@policy_required(resource_type="page", resource="ui:tasks:list")
def task_list(request: HttpRequest) -> HttpResponse:
    # Эффективный пользователь для отображения списка (режим «просмотр как»). Права проверяются по request.user выше.
    user: User = get_effective_user(request)
    now = timezone.now()
    local_now = timezone.localtime(now)
    today_start = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
    tomorrow_start = today_start + timedelta(days=1)

    # Базовую видимость задач берём из domain policy слоя (tasksapp.policy),
    # чтобы UI и API использовали одно и то же правило.
    qs = visible_tasks_qs(user)

    # Справочник "Кому поставлена задача" (assigned_to) для фильтра:
    # - админ / управляющий: все сотрудники
    # - остальные: только сотрудники своего филиала (если филиал задан)
    # Используем get_users_for_lists для исключения администраторов и группировки по филиалам
    if user.role == User.Role.MANAGER:
        assignees = [user]
    elif user.role in (User.Role.ADMIN, User.Role.GROUP_MANAGER):
        assignees_qs = get_users_for_lists(user)
        assignees = list(assignees_qs)
    elif user.branch_id:
        assignees_qs = get_users_for_lists(user).filter(branch_id=user.branch_id)
        assignees = list(assignees_qs)
    else:
        assignees_qs = get_users_for_lists(user)
        assignees = list(assignees_qs)

    status = (request.GET.get("status") or "").strip()
    show_done = (request.GET.get("show_done") or "").strip()
    if status:
        # Поддерживаем множественные статусы через запятую (например, "new,in_progress")
        if ',' in status:
            statuses = [s.strip() for s in status.split(',')]
            qs = qs.filter(status__in=statuses)
        else:
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
    if user.role == User.Role.MANAGER:
        # Для менеджера mine/0 не должен расширять видимость.
        mine = "1"
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

    # Фильтр: кому поставлена задача (assigned_to)
    assigned_to_param = (request.GET.get("assigned_to") or "").strip()
    if assigned_to_param:
        try:
            assigned_to_id = int(assigned_to_param)
            # Менеджер не должен смотреть задачи других сотрудников
            if user.role == User.Role.MANAGER and assigned_to_id != user.id:
                assigned_to_param = str(user.id)
                qs = qs.filter(assigned_to=user)
            else:
                qs = qs.filter(assigned_to_id=assigned_to_id)
        except (ValueError, TypeError):
            assigned_to_param = ""

    overdue = (request.GET.get("overdue") or "").strip()
    if overdue == "1":
        qs = qs.filter(due_at__lt=now).exclude(status__in=[Task.Status.DONE, Task.Status.CANCELLED])

    today = (request.GET.get("today") or "").strip()
    if today == "1":
        qs = qs.filter(due_at__gte=today_start, due_at__lt=tomorrow_start).exclude(status__in=[Task.Status.DONE, Task.Status.CANCELLED])

    # Фильтр по датам (date_from и date_to)
    date_from = (request.GET.get("date_from") or "").strip()
    date_to = (request.GET.get("date_to") or "").strip()
    if date_from:
        try:
            date_from_dt = datetime.strptime(date_from, "%Y-%m-%d")
            date_from_start = timezone.make_aware(date_from_dt.replace(hour=0, minute=0, second=0, microsecond=0))
            qs = qs.filter(due_at__gte=date_from_start)
        except (ValueError, TypeError):
            pass
    if date_to:
        try:
            date_to_dt = datetime.strptime(date_to, "%Y-%m-%d")
            date_to_end = timezone.make_aware(date_to_dt.replace(hour=23, minute=59, second=59, microsecond=999999))
            qs = qs.filter(due_at__lte=date_to_end)
        except (ValueError, TypeError):
            pass

    # Сортировка: читаем из GET или из cookies
    sort_field = (request.GET.get("sort") or "").strip()
    sort_dir = (request.GET.get("dir") or "").strip().lower()
    
    # Если параметры не указаны, читаем из cookies
    if not sort_field:
        cookie_sort = request.COOKIES.get("task_list_sort", "")
        if cookie_sort:
            try:
                # Формат в cookies: "field:direction" (например, "due_at:asc")
                parts = cookie_sort.split(":")
                if len(parts) == 2:
                    sort_field, sort_dir = parts[0], parts[1]
            except Exception:
                pass
    
    # Валидация направления сортировки
    if sort_dir not in ("asc", "desc"):
        sort_dir = "desc"  # По умолчанию desc
    
    # Применяем сортировку
    if sort_field == "due_at":
        if sort_dir == "asc":
            qs = qs.order_by(F("due_at").asc(nulls_last=True), "-created_at")
        else:
            qs = qs.order_by(F("due_at").desc(nulls_last=True), "-created_at")
    elif sort_field == "status":
        if sort_dir == "asc":
            qs = qs.order_by("status", "-created_at")
        else:
            qs = qs.order_by("-status", "-created_at")
    elif sort_field == "company":
        if sort_dir == "asc":
            qs = qs.order_by("company__name", "-created_at")
        else:
            qs = qs.order_by("-company__name", "-created_at")
    elif sort_field == "assignee":
        if sort_dir == "asc":
            qs = qs.order_by("assigned_to__last_name", "assigned_to__first_name", "-created_at")
        else:
            qs = qs.order_by("-assigned_to__last_name", "-assigned_to__first_name", "-created_at")
    elif sort_field == "created_by":
        if sort_dir == "asc":
            qs = qs.order_by("created_by__last_name", "created_by__first_name", "-created_at")
        else:
            qs = qs.order_by("-created_by__last_name", "-created_by__first_name", "-created_at")
    elif sort_field == "created_at":
        if sort_dir == "asc":
            qs = qs.order_by("created_at")
        else:
            qs = qs.order_by("-created_at")
    elif sort_field == "title":
        if sort_dir == "asc":
            qs = qs.order_by("title", "-created_at")
        else:
            qs = qs.order_by("-title", "-created_at")
    else:
        # По умолчанию: сортировка по дате создания (новые сверху)
        sort_field = "created_at"
        sort_dir = "desc"
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
    # Формируем query string без параметров page, sort, dir (sort и dir добавляются в ссылках заголовков)
    from urllib.parse import urlencode, parse_qs
    params = dict(request.GET)
    params.pop("page", None)
    params.pop("sort", None)
    params.pop("dir", None)
    # Преобразуем в список значений для urlencode
    qs_params = {}
    for key, value in params.items():
        if isinstance(value, list):
            qs_params[key] = value
        else:
            qs_params[key] = [value]
    qs_no_page = urlencode(qs_params, doseq=True) if qs_params else ""
    if per_page != 25:
        if qs_no_page:
            qs_params["per_page"] = [str(per_page)]
        else:
            qs_params = {"per_page": [str(per_page)]}
        qs_no_page = urlencode(qs_params, doseq=True)

    # Для шаблона: не делаем сложные выражения в {% if %}, чтобы не ловить TemplateSyntaxError.
    # Проставим флаг прямо в объекты текущей страницы.
    for t in page.object_list:
        t.can_manage_status = _can_manage_task_status_ui(user, t)  # type: ignore[attr-defined]
        t.can_edit_task = _can_edit_task_ui(user, t)  # type: ignore[attr-defined]
        t.can_delete_task = _can_delete_task_ui(user, t)  # type: ignore[attr-defined]

    is_admin = require_admin(user)
    transfer_targets = get_transfer_targets(user) if is_admin else []

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
                # Проверяем права на просмотр через тот же visible_tasks_qs,
                # чтобы UI и API были консистентны.
                if not visible_tasks_qs(user).filter(id=view_task.id).exists():
                    view_task = None
                else:
                    # Вычисляем просрочку в днях (только если известны дедлайн и время завершения)
                    if view_task.due_at and view_task.completed_at and view_task.completed_at > view_task.due_at:
                        delta = view_task.completed_at - view_task.due_at
                        view_task_overdue_days = delta.days
                    # Добавляем флаги прав
                    view_task.can_edit_task = _can_edit_task_ui(user, view_task)  # type: ignore[attr-defined]
        except (ValueError, TypeError):
            pass

    # Подсчитываем общее количество задач после всех фильтров (до пагинации)
    tasks_count = qs.count()

    # Bulk-действия в задачах должны жить на одном флаге,
    # чтобы не было ситуации "панель есть — чекбоксов нет" и наоборот.
    can_bulk_reschedule = policy_decide(
        user=user, resource_type="action", resource="ui:tasks:bulk_reschedule"
    ).allowed
    can_bulk_actions = bool(can_bulk_reschedule or is_admin)
    show_task_checkboxes = can_bulk_actions

    # Сопоставляем задачи без типа с TaskType по точному совпадению названия
    # Загружаем все TaskType для сопоставления
    from tasksapp.models import TaskType
    task_types_by_name = {tt.name: tt for tt in TaskType.objects.all()}
    
    # Применяем сопоставление к задачам на текущей странице
    tasks_to_update = []
    for task in page.object_list:
        if not task.type and task.title and task.title in task_types_by_name:
            task_type = task_types_by_name[task.title]
            task.type = task_type  # type: ignore[assignment]
            task.type_id = task_type.id  # type: ignore[attr-defined]
            tasks_to_update.append(task.id)
    
    # Сохраняем в БД пакетно для оптимизации
    if tasks_to_update:
        for task_id in tasks_to_update:
            task = next((t for t in page.object_list if t.id == task_id), None)
            if task and task.type_id:
                Task.objects.filter(id=task_id).update(type_id=task.type_id)
    
    # Сохраняем сортировку в cookie, если она была изменена через GET параметры
    response = render(
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
            "date_from": date_from,
            "date_to": date_to,
            "assigned_to": assigned_to_param,
            "filter_assignees": assignees,
            "sort_field": sort_field,
            "sort_dir": sort_dir,
            "per_page": per_page,
            "is_admin": is_admin,
            "can_bulk_reschedule": can_bulk_reschedule,
            "can_bulk_actions": can_bulk_actions,
            "show_task_checkboxes": show_task_checkboxes,
            "transfer_targets": transfer_targets,
            "view_task": view_task,
            "view_task_overdue_days": view_task_overdue_days,
            "tasks_count": tasks_count,
        },
    )
    
    # Устанавливаем cookie для сохранения сортировки (срок действия 1 год)
    if sort_field:
        cookie_value = f"{sort_field}:{sort_dir}"
        response.set_cookie("task_list_sort", cookie_value, max_age=31536000)  # 1 год
    
    return response


@login_required
@policy_required(resource_type="action", resource="ui:tasks:create")
def task_create(request: HttpRequest) -> HttpResponse:
    user: User = request.user
    # Получаем company_id из GET параметров (доступно и для GET, и для POST)
    company_id = (request.GET.get("company") or "").strip()

    if request.method == "POST":
        is_ajax = request.headers.get("X-Requested-With") == "XMLHttpRequest"
        
        # Получаем выбранного пользователя из POST данных ДО создания формы
        # assigned_to может прийти в различных форматах, используем функцию очистки
        assigned_to_raw = request.POST.get("assigned_to", "")
        assigned_to_id = clean_int_id(assigned_to_raw)
        
        # Логирование для отладки
        import logging
        logger = logging.getLogger(__name__)
        logger.info(f"Task creation POST: assigned_to_raw={assigned_to_raw!r}, assigned_to_id={assigned_to_id!r}, user={user.id}, role={user.role}")
        
        # Создаем форму
        form = TaskForm(request.POST)
        
        # ВАЖНО: Устанавливаем queryset ДО валидации формы
        # Сначала устанавливаем queryset на всех активных пользователей, чтобы валидация прошла
        # Потом ограничим его для отображения
        # ВАЖНО: Устанавливаем queryset на всех пользователей перед валидацией,
        # чтобы Django мог принять любое значение из POST, даже если оно не в queryset
        form.fields["assigned_to"].queryset = User.objects.filter(is_active=True).select_related("branch")
        
        # Если есть выбранное значение, убеждаемся, что оно в queryset
        if assigned_to_id is not None:
            # Добавляем выбранного пользователя в queryset, если его там нет
            if not form.fields["assigned_to"].queryset.filter(id=assigned_to_id).exists():
                logger.warning(f"Selected user {assigned_to_id} not in queryset, adding it")
                form.fields["assigned_to"].queryset = User.objects.filter(
                    Q(is_active=True) | Q(id=assigned_to_id)
                ).select_related("branch")
        
        # Теперь валидируем форму
        
        if form.is_valid():
            # После валидации устанавливаем правильный queryset для отображения (если форма будет перерендерена)
            # Но сначала сохраняем выбранное значение
            selected_assigned_to_id: int | None = None
            if form.cleaned_data.get("assigned_to"):
                selected_assigned_to_id = int(form.cleaned_data["assigned_to"].id)
            _set_assigned_to_queryset(form, user, assigned_to_id=selected_assigned_to_id)
            task: Task = form.save(commit=False)
            task.created_by = user
            apply_to_org = bool(form.cleaned_data.get("apply_to_org_branches"))
            comp: Company | None = None
            if task.company_id:
                comp = Company.objects.select_related("responsible", "branch", "head_company").filter(id=task.company_id).first()
                if comp and not _can_edit_company(user, comp):
                    if is_ajax:
                        return JsonResponse({"ok": False, "error": "Нет прав на постановку задач по этой компании."}, status=403)
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
                        if is_ajax:
                            return JsonResponse({"ok": False, "error": "Можно назначать задачи только сотрудникам своего филиала."}, status=400)
                        messages.error(request, "Можно назначать задачи только сотрудникам своего филиала.")
                        return redirect("task_create")

            # Доп. ограничение: в списках исполнителей не должно быть ADMIN/GROUP_MANAGER,
            # и назначать на них тоже нельзя.
            if task.assigned_to and task.assigned_to.role in (User.Role.ADMIN, User.Role.GROUP_MANAGER):
                if is_ajax:
                    return JsonResponse({"ok": False, "error": "Нельзя назначать задачи администратору или управляющему компанией."}, status=400)
                messages.error(request, "Нельзя назначать задачи администратору или управляющему компанией.")
                return redirect("task_create")

            # Если включено "на все филиалы организации" — единый путь создания по целевому списку компаний
            if apply_to_org and comp:
                target_companies = resolve_target_companies(selected_company=comp, apply_to_org_branches=True)

                # Доп. защита от дублей: seen_ids на уровне цикла
                seen_ids: set = set()
                created = 0
                skipped = 0

                for c in target_companies:
                    if not c or c.id in seen_ids:
                        continue
                    seen_ids.add(c.id)

                    if not _can_edit_company(user, c):
                        skipped += 1
                        continue

                    # Определяем статус: если создатель назначает задачу себе, то "В работе", иначе "Новая"
                    initial_status = Task.Status.IN_PROGRESS if task.assigned_to_id == user.id else Task.Status.NEW

                    t = Task(
                        created_by=user,
                        assigned_to=task.assigned_to,
                        company=c,
                        type=task.type,
                        title=task.title,
                        description=task.description,
                        due_at=task.due_at,
                        recurrence_rrule=task.recurrence_rrule,
                        status=initial_status,
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
                    messages.success(
                        request,
                        f"Задача создана по организации: {created} карточек. Пропущено (нет прав): {skipped}.",
                    )
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
            # Заголовок задачи берём из выбранного типа/статуса
            if task.type:
                task.title = task.type.name
            
            # Определяем статус задачи:
            # - Если создатель назначает задачу себе, то статус "В работе" (IN_PROGRESS)
            # - Если РОП/директор/управляющий/админ создаёт задачу кому-то другому, то статус "Новая" (NEW)
            if task.assigned_to_id == user.id:
                # Создатель назначает задачу себе - автоматически "В работе"
                task.status = Task.Status.IN_PROGRESS
            else:
                # Создатель назначает задачу кому-то другому - статус "Новая"
                task.status = Task.Status.NEW
            
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
            # Если AJAX запрос - возвращаем JSON
            if is_ajax:
                return JsonResponse({
                    "ok": True,
                    "task_id": str(task.id),
                    "message": "Задача создана успешно.",
                })
            return redirect("task_list")
        else:
            # Форма не валидна
            # Логирование для отладки
            import logging
            logger = logging.getLogger(__name__)
            logger.warning(f"Form validation failed: {form.errors}, assigned_to_raw={request.POST.get('assigned_to', '')!r}, assigned_to_id={assigned_to_id!r}")
            
            # Устанавливаем queryset для assigned_to с учетом выбранного значения
            # Это нужно для того, чтобы форма могла быть отрендерена с ошибками
            _set_assigned_to_queryset(form, user, assigned_to_id=assigned_to_id)
            
            if is_ajax:
                # Собираем ошибки валидации (в JSON отдаём ЧИСТЫЕ строки без "['...']").
                # Django ValidationError при str() даёт "['msg']", поэтому разворачиваем messages.
                errors: dict[str, list[str]] = {}
                for field, field_errors in form.errors.items():
                    msgs: list[str] = []
                    data = getattr(field_errors, "data", None)
                    if data is not None:
                        for e in data:
                            e_msgs = getattr(e, "messages", None)
                            if e_msgs:
                                msgs.extend([str(m) for m in e_msgs])
                            else:
                                msg = getattr(e, "message", None)
                                if msg:
                                    msgs.append(str(msg))
                                else:
                                    msgs.append(str(e))
                    elif isinstance(field_errors, (list, tuple)):
                        msgs.extend([str(e) for e in field_errors])
                    else:
                        msgs.append(str(field_errors))

                    # Финальная зачистка от случайного "['...']"
                    cleaned: list[str] = []
                    for m in msgs:
                        s = str(m).strip()
                        if s.startswith("['") and s.endswith("']") and len(s) >= 4:
                            s = s[2:-2]
                        cleaned.append(s)
                    errors[field] = cleaned
                return JsonResponse({
                    "ok": False,
                    "error": "Ошибки валидации формы.",
                    "errors": errors
                }, status=400)
            # Для не-AJAX запросов продолжаем рендеринг формы с ошибками
    else:
        initial = {"assigned_to": user}
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
    # Для модального окна ограничиваем количество (для быстрой загрузки)
    # Полный список можно будет искать через автокомплит (TODO)
    is_modal = request.headers.get("X-Requested-With") == "XMLHttpRequest" or request.GET.get("modal") == "1"
    company_qs = _editable_company_qs(user).order_by("name")
    if is_modal:
        # В модальном окне показываем только первые 200 компаний для быстрой загрузки
        # НО: если передана конкретная компания через GET, убедимся, что она в списке
        if company_id:
            # Получаем компанию отдельно
            selected_company = Company.objects.filter(id=company_id).first()
            if selected_company and _can_edit_company(user, selected_company):
                # Берем первые 199 компаний, чтобы добавить выбранную компанию
                limited_qs = company_qs.exclude(id=company_id)[:199]
                # Объединяем с выбранной компанией и сортируем
                from django.db.models import Case, When, IntegerField
                company_qs = (
                    Company.objects.filter(
                        Q(id__in=[c.id for c in limited_qs]) | Q(id=company_id)
                    )
                    .annotate(
                        custom_order=Case(
                            When(id=company_id, then=0),
                            default=1,
                            output_field=IntegerField(),
                        )
                    )
                    .order_by("custom_order", "name")
                )
            else:
                company_qs = company_qs[:200]
        else:
            company_qs = company_qs[:200]
    form.fields["company"].queryset = company_qs

    # Ограничить назначаемых с группировкой по городам филиалов (как при передаче компании)
    # ВАЖНО: вызываем ПОСЛЕ создания формы, чтобы переустановить queryset
    _set_assigned_to_queryset(form, user)

    # Оптимизация queryset для типа задачи (используем only() для загрузки только необходимых полей)
    form.fields["type"].queryset = TaskType.objects.only("id", "name").order_by("name")

    # Если запрос на модалку (через AJAX или параметр modal=1)
    if request.headers.get("X-Requested-With") == "XMLHttpRequest" or request.GET.get("modal") == "1":
        return render(request, "ui/task_create_modal.html", {"form": form})
    
    return render(request, "ui/task_create.html", {"form": form})


def _set_assigned_to_queryset(form: "TaskForm", user: User, assigned_to_id: int | None = None) -> None:
    """
    Устанавливает queryset для поля assigned_to в зависимости от роли пользователя.
    Также убеждается, что выбранное значение (если есть) включено в queryset.
    
    Args:
        form: Форма TaskForm
        user: Текущий пользователь
        assigned_to_id: ID выбранного пользователя (опционально, для POST запросов)
    """
    # Получаем текущее выбранное значение (если есть)
    current_user_id: int | None = None
    
    # Если передан assigned_to_id (для POST запросов), используем его
    if assigned_to_id is not None:
        current_user_id = assigned_to_id
    
    # Если не передан, проверяем initial (для GET запросов)
    if not current_user_id and hasattr(form, 'initial') and 'assigned_to' in form.initial:
        assigned_to_value = form.initial['assigned_to']
        if isinstance(assigned_to_value, User):
            current_user_id = str(assigned_to_value.id)
        elif assigned_to_value:
            current_user_id = clean_int_id(assigned_to_value)
    
    # Если все еще нет, проверяем data (для POST запросов как fallback)
    if not current_user_id and hasattr(form, 'data') and form.data:
        assigned_to_value = form.data.get('assigned_to', '')
        if assigned_to_value:
            current_user_id = clean_int_id(assigned_to_value)
    
    # Общие исключения для списков исполнителей:
    # - не показываем "Без филиала" (branch is null)
    # - не показываем пользователей с ролью ADMIN и GROUP_MANAGER
    exclude_roles = (User.Role.ADMIN, User.Role.GROUP_MANAGER)

    # Устанавливаем queryset в зависимости от роли
    if user.role == User.Role.MANAGER:
        # Менеджер может назначать задачи только себе
        base_queryset = User.objects.filter(id=user.id, is_active=True).select_related("branch")
    elif user.role in (User.Role.BRANCH_DIRECTOR, User.Role.SALES_HEAD) and user.branch_id:
        # РОП и Директор филиала могут назначать задачи сотрудникам своего филиала
        base_queryset = (
            User.objects.filter(is_active=True, branch_id=user.branch_id)
            .exclude(role__in=exclude_roles)
            .select_related("branch")
            .order_by("branch__name", "last_name", "first_name")
        )
    elif user.role in (User.Role.GROUP_MANAGER, User.Role.ADMIN) or user.is_superuser:
        # Управляющий и Администратор могут назначать задачи всем сотрудникам (кроме ADMIN/GROUP_MANAGER)
        base_queryset = (
            User.objects.filter(is_active=True)
            .exclude(role__in=exclude_roles)
            .exclude(branch__isnull=True)
            .select_related("branch")
            .order_by("branch__name", "last_name", "first_name")
        )
    else:
        # Для остальных ролей используем get_transfer_targets (только менеджеры, директора, РОП)
        from companies.permissions import get_transfer_targets
        base_queryset = (
            get_transfer_targets(user)
            .exclude(role__in=exclude_roles)
            .exclude(branch__isnull=True)
        )
    
    # Если есть выбранное значение и его нет в queryset, добавляем его
    if current_user_id is not None and not base_queryset.filter(id=current_user_id).exists():
        # Добавляем выбранного пользователя в queryset
        from django.db.models import Case, When, IntegerField
        queryset = (
            User.objects.filter(
                Q(id__in=base_queryset.values_list('id', flat=True)) | Q(id=current_user_id)
            )
            .annotate(
                custom_order=Case(
                    When(id=current_user_id, then=0),
                    default=1,
                    output_field=IntegerField(),
                )
            )
            .order_by("custom_order", "last_name", "first_name")
            .select_related("branch")
        )
    elif current_user_id:
        queryset = base_queryset
    else:
        queryset = base_queryset
    
    # Устанавливаем queryset
    form.fields["assigned_to"].queryset = queryset


def _can_manage_task_status_ui(user: User, task: Task) -> bool:
    if not user or not user.is_authenticated or not user.is_active:
        return False
    if user.is_superuser or user.role in (User.Role.ADMIN, User.Role.GROUP_MANAGER):
        return True
    # Создатель всегда может менять статус своей задачи (проверяем ПЕРВЫМ)
    if task.created_by_id and task.created_by_id == user.id:
        return True
    # Исполнитель может менять статус назначенной ему задачи
    if task.assigned_to_id and task.assigned_to_id == user.id:
        return True
    # Ответственный за компанию может менять статус задач по своей компании
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


def _can_edit_task_ui(user: User, task: Task) -> bool:
    """
    Право на редактирование задачи:
    - Создатель всегда может редактировать свою задачу
    - Исполнитель (assigned_to) может редактировать назначенную ему задачу
    - Администратор / управляющий — любые задачи
    - Ответственный за карточку компании (company.responsible)
    - Директор филиала / РОП — задачи своего филиала
    """
    # Создатель всегда может редактировать свою задачу (проверяем ПЕРВЫМ)
    if task.created_by_id and task.created_by_id == user.id:
        return True
    # Исполнитель может редактировать назначенную ему задачу
    if task.assigned_to_id and task.assigned_to_id == user.id:
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
    - Создатель может удалять свои задачи;
    - Исполнитель может удалять назначенные ему задачи;
    - Ответственный за карточку компании (company.responsible);
    - Директор филиала / РОП — задачи своего филиала.
    """
    if not user or not user.is_authenticated or not user.is_active:
        return False
    if user.is_superuser or user.role in (User.Role.ADMIN, User.Role.GROUP_MANAGER):
        return True
    # Создатель всегда может удалять свою задачу (проверяем ПЕРВЫМ)
    if task.created_by_id and task.created_by_id == user.id:
        return True
    # Исполнитель может удалять назначенную ему задачу
    if task.assigned_to_id and task.assigned_to_id == user.id:
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


def _create_note_from_task(task: Task, user: User) -> CompanyNote:
    """Создает заметку из задачи с информацией о статусе и описании."""
    from companies.models import CompanyNote
    
    # Формируем текст заметки
    note_parts = []
    
    # Задача (тип задачи)
    if task.type:
        status_text = f"Задача: {task.type.name}"
    elif task.title:
        status_text = f"Задача: {task.title}"
    else:
        status_text = "Задача: Без типа"
    note_parts.append(status_text)
    
    # Описание задачи
    if task.description:
        note_parts.append(f"\n{task.description}")
    
    # Дедлайн, если был
    if task.due_at:
        note_parts.append(f"\nДедлайн: {task.due_at.strftime('%d.%m.%Y %H:%M')}")
    
    note_text = "\n".join(note_parts)
    
    # Создаем заметку
    note = CompanyNote.objects.create(
        company=task.company,
        author=user,
        text=note_text,
    )
    
    return note


@login_required
@policy_required(resource_type="action", resource="ui:tasks:delete")
def task_delete(request: HttpRequest, task_id) -> HttpResponse:
    user: User = request.user
    try:
        task = Task.objects.select_related("company", "assigned_to", "created_by", "type").get(id=task_id)
    except Task.DoesNotExist:
        if request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return JsonResponse({"error": "Задача не найдена."}, status=404)
        raise Http404("Задача не найдена")
    
    if not _can_delete_task_ui(user, task):
        if request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return JsonResponse({"error": "Нет прав на удаление этой задачи."}, status=403)
        messages.error(request, "Нет прав на удаление этой задачи.")
        return redirect("task_list")

    if request.method == "POST":
        save_to_notes = request.POST.get("save_to_notes") == "1"
        title = task.title
        company_id = task.company_id
        
        # Если нужно сохранить в заметки
        if save_to_notes and task.company_id:
            try:
                note = _create_note_from_task(task, user)
                log_event(
                    actor=user,
                    verb=ActivityEvent.Verb.COMMENT,
                    entity_type="note",
                    entity_id=note.id,
                    company_id=company_id,
                    message="Добавлена заметка из задачи",
                )
                if request.headers.get("X-Requested-With") == "XMLHttpRequest":
                    # Не удаляем задачу здесь, удалим ниже
                    pass
                else:
                    messages.success(request, f"Задача «{title}» удалена. Заметка создана.")
            except Exception as e:
                if request.headers.get("X-Requested-With") == "XMLHttpRequest":
                    return JsonResponse({"error": f"Ошибка при создании заметки: {str(e)}"}, status=500)
                messages.error(request, f"Ошибка при создании заметки: {str(e)}")
        
        task.delete()
        
        if not save_to_notes:
            if request.headers.get("X-Requested-With") == "XMLHttpRequest":
                pass  # Вернем JSON ниже
            else:
                messages.success(request, f"Задача «{title}» удалена.")
        
        log_event(
            actor=user,
            verb=ActivityEvent.Verb.DELETE,
            entity_type="task",
            entity_id=str(task_id),
            company_id=company_id,
            message=f"Удалена задача: {title}",
        )
        
        if request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return JsonResponse({"success": True, "note_created": save_to_notes, "message": f"Задача «{title}» удалена." + (" Заметка создана." if save_to_notes else "")})
        
        return redirect(request.META.get("HTTP_REFERER") or "task_list")

    return redirect("task_list")


@login_required
@policy_required(resource_type="action", resource="ui:tasks:bulk_reassign")
def task_bulk_reassign(request: HttpRequest) -> HttpResponse:
    """
    Массовое переназначение задач:
    - либо по выбранным task_ids[]
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

    # apply_mode=filtered поддерживается на уровне backend (служебно),
    # но в UI используется только режим "selected" (выбранные чекбоксами).
    if apply_mode == "filtered":
        # ВАЖНО: даже для админа bulk должен работать только по "видимым" задачам,
        # иначе можно затронуть задачи, которые не должны быть в текущем контексте видимости.
        qs = visible_tasks_qs(user).select_related("type").order_by("-created_at").distinct()
        qs, filters_summary = _apply_task_filters_for_bulk_ui(qs, user, request.POST)

        # safety cap
        cap = 5000
        ids = list(qs.values_list("id", flat=True)[:cap])
        if not ids:
            try:
                logger.warning(
                    "tasks.bulk_reassign.apply.reject",
                    extra={
                        "user_id": user.id,
                        "apply_mode": apply_mode,
                        "reason": "no_tasks_for_filters",
                        "cap": cap,
                        "path": request.path,
                        "method": request.method,
                    },
                )
            except Exception:
                pass
            messages.error(request, "Нет задач для переназначения.")
            return redirect("task_list")
        if len(ids) >= cap:
            try:
                logger.warning(
                    "tasks.bulk_reassign.apply.reject",
                    extra={
                        "user_id": user.id,
                        "apply_mode": apply_mode,
                        "reason": "cap_reached",
                        "count": len(ids),
                        "cap": cap,
                        "path": request.path,
                        "method": request.method,
                    },
                )
            except Exception:
                pass
            messages.warning(
                request, f"Выбрано слишком много задач (>{cap}). Сузьте фильтр и повторите."
            )
            return redirect("task_list")
        requested_count = None
    else:
        raw_ids = request.POST.getlist("task_ids") or []
        raw_ids = [i for i in raw_ids if i]
        if not raw_ids:
            try:
                logger.warning(
                    "tasks.bulk_reassign.apply.reject",
                    extra={
                        "user_id": user.id,
                        "apply_mode": apply_mode,
                        "reason": "no_task_ids_selected",
                        "requested_count": 0,
                        "path": request.path,
                        "method": request.method,
                    },
                )
            except Exception:
                pass
            messages.error(request, "Выберите хотя бы одну задачу (чекбоксы слева).")
            return redirect("task_list")
        # Только задачи, видимые пользователю (на случай подмены task_ids в POST).
        ids = list(
            visible_tasks_qs(user).filter(id__in=raw_ids).values_list("id", flat=True)
        )
        if not ids:
            messages.error(request, "Нет доступа к выбранным задачам.")
            return redirect("task_list")
        requested_count = len(raw_ids)
        filters_summary = []
        cap = None

    target_count = len(ids)
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
    try:
        logger.info(
            "tasks.bulk_reassign.apply",
            extra={
                "user_id": user.id,
                "apply_mode": apply_mode,
                "count": updated,
                "target_count": target_count,
                "requested_count": requested_count,
                "cap": cap,
                "filters_summary": filters_summary,
                "strong_confirm_required": bool(
                    apply_mode == "filtered"
                    and target_count is not None
                    and target_count > STRONG_CONFIRM_THRESHOLD
                ),
                "cap_reached": bool(cap is not None and target_count >= cap),
                "path": request.path,
                "method": request.method,
            },
        )
    except Exception:
        # Логирование не должно ломать основной флоу.
        pass
    if new_assigned.id != user.id:
        notify(
            user=new_assigned,
            kind=Notification.Kind.TASK,
            title="Вам назначены задачи",
            body=f"Количество: {updated}",
            url="/tasks/?mine=1",
        )
    return redirect("task_list")


def _apply_task_filters_for_bulk_ui(qs, user: User, data):
    """
    Общая утилита для применения UI‑фильтров задач в bulk‑операциях (перенос дедлайна, переназначение).
    Принимает тот же набор параметров, что и task_list (status, mine, assigned_to, overdue, today,
    date_from, date_to, show_done) и возвращает:
      - отфильтрованный queryset
      - человекочитаемый summary списка фильтров.
    """
    # Разрешённые ключи фильтров, приходящие из UI (QueryDict/POST/GET).
    ALLOWED_FILTER_KEYS = {
        "status",
        "mine",
        "assigned_to",
        "overdue",
        "today",
        "date_from",
        "date_to",
        "show_done",
    }
    # Собираем исходные ключи (включая потенциальный мусор), чтобы при необходимости залогировать их.
    try:
        incoming_keys = set(data.keys())
    except Exception:
        incoming_keys = set()
    unknown_keys = sorted(incoming_keys - ALLOWED_FILTER_KEYS)

    # Строго фильтруем входящие параметры, чтобы не тащить случайный мусор
    # (лишние ключи игнорируем, логику фильтрации не меняем).
    data = {key: data.get(key) for key in ALLOWED_FILTER_KEYS if key in data}

    if unknown_keys:
        try:
            logger.debug(
                "tasks.bulk_filters.unknown_keys",
                extra={
                    "user_id": getattr(user, "id", None),
                    "unknown_keys": unknown_keys,
                    "source": "_apply_task_filters_for_bulk_ui",
                },
            )
        except Exception:
            pass

    now = timezone.now()
    local_now = timezone.localtime(now)
    today_start = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
    tomorrow_start = today_start + timedelta(days=1)

    filters_summary: list[str] = []
    has_restricting_filters = False

    # Статус + show_done (как в task_list: по умолчанию скрываем DONE, если статус не выбран и show_done != 1)
    status_param = (data.get("status") or "").strip()
    show_done = (data.get("show_done") or "").strip()

    if status_param:
        # Ожидаем status как строку (в т.ч. через запятую), а не как getlist.
        if "," in status_param:
            statuses = [s.strip() for s in status_param.split(",") if s.strip()]
            if statuses:
                qs = qs.filter(status__in=statuses)
        else:
            qs = qs.filter(status=status_param)

        status_labels_map = {value: label for value, label in Task.Status.choices}
        raw_statuses = (
            [s.strip() for s in status_param.split(",") if s.strip()]
            if "," in status_param
            else [status_param]
        )
        status_labels = [status_labels_map.get(s, s) for s in raw_statuses if s]
        if status_labels:
            filters_summary.append("Статус: " + ", ".join(status_labels))
        has_restricting_filters = True
    else:
        if show_done != "1":
            qs = qs.exclude(status=Task.Status.DONE)
            # Скрытие выполненных задач — это дефолтное поведение списка,
            # считаем его не «сужающим фильтром» для предупреждения про широкую выборку.
            filters_summary.append("Без выполненных задач")
        else:
            filters_summary.append("Включая выполненные задачи")

    # Флаг "Мои" (mine)
    mine = (data.get("mine") or "").strip()
    # В bulk-операциях интерпретируем mine строго как "assigned_to = текущий пользователь",
    # чтобы не расширять выборку за счёт сложной роль-логики из task_list.
    if mine == "1":
        qs = qs.filter(assigned_to=user)
        filters_summary.append("Только мои задачи")
        has_restricting_filters = True

    # Фильтр по конкретному исполнителю
    assigned_to_param = (data.get("assigned_to") or "").strip()
    if assigned_to_param:
        try:
            assigned_to_id = int(assigned_to_param)
        except (ValueError, TypeError):
            assigned_to_id = None
        if assigned_to_id:
            qs = qs.filter(assigned_to_id=assigned_to_id)
            assignee = (
                User.objects.filter(id=assigned_to_id)
                .only("first_name", "last_name", "email")
                .first()
            )
            if assignee:
                filters_summary.append(f"Исполнитель: {assignee}")
            has_restricting_filters = True

    # Просрочено / сегодня
    overdue = (data.get("overdue") or "").strip()
    if overdue == "1":
        qs = qs.filter(due_at__lt=now).exclude(
            status__in=[Task.Status.DONE, Task.Status.CANCELLED]
        )
        filters_summary.append("Только просроченные")
        has_restricting_filters = True

    today = (data.get("today") or "").strip()
    if today == "1":
        qs = qs.filter(
            due_at__gte=today_start,
            due_at__lt=tomorrow_start,
        ).exclude(status__in=[Task.Status.DONE, Task.Status.CANCELLED])
        filters_summary.append("Только на сегодня")
        has_restricting_filters = True

    # Период по due_at (date_from / date_to)
    date_from = (data.get("date_from") or "").strip()
    date_to = (data.get("date_to") or "").strip()

    period_start_label: str | None = None
    period_end_label: str | None = None

    if date_from:
        try:
            date_from_dt = datetime.strptime(date_from, "%Y-%m-%d")
            date_from_start = timezone.make_aware(
                date_from_dt.replace(hour=0, minute=0, second=0, microsecond=0)
            )
            qs = qs.filter(due_at__gte=date_from_start)
            period_start_label = date_from_dt.strftime("%d.%m.%Y")
        except (ValueError, TypeError):
            pass

    if date_to:
        try:
            date_to_dt = datetime.strptime(date_to, "%Y-%m-%d")
            date_to_end = timezone.make_aware(
                date_to_dt.replace(hour=23, minute=59, second=59, microsecond=999999)
            )
            qs = qs.filter(due_at__lte=date_to_end)
            period_end_label = date_to_dt.strftime("%d.%m.%Y")
        except (ValueError, TypeError):
            pass

    if period_start_label or period_end_label:
        has_restricting_filters = True
        if period_start_label and period_end_label:
            filters_summary.append(
                f"Период дедлайна: {period_start_label} – {period_end_label}"
            )
        elif period_start_label:
            filters_summary.append(f"Дедлайн не раньше: {period_start_label}")
        elif period_end_label:
            filters_summary.append(f"Дедлайн не позже: {period_end_label}")

    # Если не было ни одного «явного» фильтра (кроме базового поведения show_done),
    # подсвечиваем, что выборка по сути не ограничена.
    if not has_restricting_filters:
        filters_summary.append("Фильтры не ограничивают выборку (все доступные задачи).")

    return qs, filters_summary


@login_required
@policy_required(resource_type="action", resource="ui:tasks:bulk_reschedule")
def task_bulk_reschedule(request: HttpRequest) -> HttpResponse:
    """
    Массовый перенос даты (дедлайна) задач:
    - либо по выбранным task_ids[]
    Доступно всем ролям с доступом к списку задач. Обновляются только задачи, видимые пользователю (visible_tasks_qs).
    """
    if request.method != "POST":
        return redirect("task_list")

    user: User = request.user

    # Принимаем due_at (datetime-local) или due_date + due_time для обратной совместимости
    due_at_str = (request.POST.get("due_at") or "").strip()
    if due_at_str:
        try:
            # Формат YYYY-MM-DDTHH:MM или YYYY-MM-DDTHH:MM:SS
            if "T" in due_at_str:
                parsed = datetime.strptime(due_at_str[:16], "%Y-%m-%dT%H:%M")
            else:
                parsed = datetime.strptime(due_at_str[:10], "%Y-%m-%d")
                parsed = parsed.replace(hour=17, minute=0, second=0, microsecond=0)
            new_due_at = timezone.make_aware(parsed)
        except ValueError:
            messages.error(request, "Неверный формат даты и времени.")
            return redirect("task_list")
    else:
        due_date_str = (request.POST.get("due_date") or "").strip()
        if not due_date_str:
            messages.error(request, "Укажите дату для переноса.")
            return redirect("task_list")
        try:
            parsed_date = datetime.strptime(due_date_str, "%Y-%m-%d").date()
        except ValueError:
            messages.error(request, "Неверный формат даты. Используйте ГГГГ-ММ-ДД.")
            return redirect("task_list")
        due_time_str = (request.POST.get("due_time") or "17:00").strip()
        try:
            if len(due_time_str) >= 5 and ":" in due_time_str:
                new_time = datetime.strptime(due_time_str[:5], "%H:%M").time()
            else:
                new_time = datetime.strptime("17:00", "%H:%M").time()
        except ValueError:
            new_time = datetime.strptime("17:00", "%H:%M").time()
        new_due_at = timezone.make_aware(datetime.combine(parsed_date, new_time))

    # Ограничение: задачи ставятся с 8:00 до 17:50 (как в формах создания/редактирования задач)
    local_dt = timezone.localtime(new_due_at)
    h, m = local_dt.hour, local_dt.minute
    if h < 8 or (h == 17 and m > 50) or h > 17:
        if h < 8:
            h, m = 8, 0
        else:
            h, m = 17, 50
        new_due_at = timezone.make_aware(datetime.combine(local_dt.date(), datetime_time(h, m, 0)))
        messages.info(request, f"Время скорректировано в рабочий интервал 8:00–17:50. Применено: {new_due_at.strftime('%d.%m.%Y %H:%M')}.")

    apply_mode = (request.POST.get("apply_mode") or "selected").strip().lower()

    if apply_mode == "filtered":
        qs = visible_tasks_qs(user).order_by("-created_at").distinct()
        qs, filters_summary = _apply_task_filters_for_bulk_ui(qs, user, request.POST)

        cap = 5000
        ids = list(qs.values_list("id", flat=True)[:cap])
        if not ids:
            try:
                logger.warning(
                    "tasks.bulk_reschedule.apply.reject",
                    extra={
                        "user_id": user.id,
                        "apply_mode": apply_mode,
                        "reason": "no_tasks_for_filters",
                        "cap": cap,
                        "path": request.path,
                        "method": request.method,
                    },
                )
            except Exception:
                pass
            messages.error(request, "Нет задач для переноса даты.")
            return redirect("task_list")
        if len(ids) >= cap:
            try:
                logger.warning(
                    "tasks.bulk_reschedule.apply.reject",
                    extra={
                        "user_id": user.id,
                        "apply_mode": apply_mode,
                        "reason": "cap_reached",
                        "count": len(ids),
                        "cap": cap,
                        "path": request.path,
                        "method": request.method,
                    },
                )
            except Exception:
                pass
            messages.warning(
                request, f"Выбрано слишком много задач (>{cap}). Сузьте фильтр и повторите."
            )
            return redirect("task_list")
        requested_count = None
    else:
        raw_ids = request.POST.getlist("task_ids") or []
        raw_ids = [i for i in raw_ids if i]
        if not raw_ids:
            try:
                logger.warning(
                    "tasks.bulk_reschedule.apply.reject",
                    extra={
                        "user_id": user.id,
                        "apply_mode": apply_mode,
                        "reason": "no_task_ids_selected",
                        "requested_count": 0,
                        "path": request.path,
                        "method": request.method,
                    },
                )
            except Exception:
                pass
            messages.error(
                request,
                "Выберите хотя бы одну задачу (чекбоксы слева).",
            )
            return redirect("task_list")
        requested_count = len(raw_ids)
        # Только задачи, видимые пользователю
        ids = list(
            visible_tasks_qs(user).filter(id__in=raw_ids).values_list("id", flat=True)
        )
        if not ids:
            try:
                logger.warning(
                    "tasks.bulk_reschedule.apply.reject",
                    extra={
                        "user_id": user.id,
                        "apply_mode": apply_mode,
                        "reason": "no_visible_tasks",
                        "requested_count": requested_count,
                        "path": request.path,
                        "method": request.method,
                    },
                )
            except Exception:
                pass
            messages.error(request, "Нет доступа к выбранным задачам.")
            return redirect("task_list")
        filters_summary = []
        cap = None

    # Сохраняем старые due_at для возможности отмены переноса из журнала действий
    task_due_before = list(
        Task.objects.filter(id__in=ids).values_list("id", "due_at")
    )
    task_due_before = [
        {"id": str(t[0]), "due_at": t[1].isoformat() if t[1] else None}
        for t in task_due_before
    ][:5000]  # ограничение размера meta

    target_count = len(ids)
    now_ts = timezone.now()
    with transaction.atomic():
        qs_to_update = Task.objects.filter(id__in=ids)
        updated = qs_to_update.update(due_at=new_due_at, updated_at=now_ts)

    messages.success(
        request,
        f"Перенесено на дату задач: {updated}. Новая дата: {new_due_at.strftime('%d.%m.%Y %H:%M')}.",
    )
    log_event(
        actor=user,
        verb=ActivityEvent.Verb.UPDATE,
        entity_type="task_bulk_reschedule",
        entity_id="",
        message="Массовый перенос даты задач",
        meta={
            "count": updated,
            "due_at": new_due_at.isoformat(),
            "mode": apply_mode,
            "task_due_before": task_due_before,
        },
    )
    try:
        logger.info(
            "tasks.bulk_reschedule.apply",
            extra={
                "user_id": user.id,
                "apply_mode": apply_mode,
                "count": updated,
                "target_count": target_count,
                "requested_count": requested_count,
                "cap": cap,
                "filters_summary": filters_summary,
                "strong_confirm_required": bool(
                    apply_mode == "filtered"
                    and target_count is not None
                    and target_count > STRONG_CONFIRM_THRESHOLD
                ),
                "cap_reached": bool(cap is not None and target_count >= cap),
                "path": request.path,
                "method": request.method,
            },
        )
    except Exception:
        pass
    return redirect("task_list")


@login_required
@policy_required(resource_type="action", resource="ui:tasks:bulk_reschedule")
def task_bulk_reschedule_preview(request: HttpRequest) -> JsonResponse:
    """
    Preview для модалки подтверждения массового переноса дедлайна.
    Возвращает количество задач и небольшой сэмпл (несколько задач), чтобы пользователь понимал, что изменится.
    """
    if request.method != "POST":
        return JsonResponse({"ok": False, "error": "Method not allowed"}, status=405)

    user: User = request.user
    apply_mode = (request.POST.get("apply_mode") or "").strip().lower()
    if apply_mode not in ("selected", "filtered"):
        return JsonResponse({"ok": False, "error": "Некорректный режим."}, status=400)

    requested_count = None
    qs = visible_tasks_qs(user).select_related("company").distinct()

    if apply_mode == "filtered":
        qs, filters_summary = _apply_task_filters_for_bulk_ui(qs, user, request.POST)
    else:
        raw_ids = request.POST.getlist("task_ids") or []
        raw_ids = [i for i in raw_ids if i]
        requested_count = len(raw_ids)
        if not raw_ids:
            try:
                logger.warning(
                    "tasks.bulk_reschedule.preview.reject",
                    extra={
                        "user_id": user.id,
                        "apply_mode": apply_mode,
                        "reason": "no_task_ids_selected",
                        "requested_count": 0,
                        "path": request.path,
                        "method": request.method,
                    },
                )
            except Exception:
                pass
            return JsonResponse({"ok": False, "error": "Не выбраны задачи."}, status=400)
        qs = qs.filter(id__in=raw_ids)
        filters_summary: list[str] = []

    # cap как в основном хендлере (для filtered)
    cap = 5000
    ids = list(qs.values_list("id", flat=True)[:cap])
    count = len(ids)

    if count == 0:
        try:
            logger.warning(
                "tasks.bulk_reschedule.preview.reject",
                extra={
                    "user_id": user.id,
                    "apply_mode": apply_mode,
                    "reason": "zero_count",
                    "cap": cap,
                    "path": request.path,
                    "method": request.method,
                },
            )
        except Exception:
            pass

    sample_qs = Task.objects.filter(id__in=ids).select_related("company").order_by(
        "due_at", "-created_at"
    )
    sample = []
    for t in sample_qs[:6]:
        sample.append(
            {
                "id": str(t.id),
                "title": t.title,
                "company": (t.company.name if t.company else None),
                "due_at": t.due_at.isoformat() if t.due_at else None,
            }
        )

    payload = {
        "ok": True,
        "mode": apply_mode,
        "count": count,
        "requested_count": requested_count,
        "cap": cap,
        "sample": sample,
        "filters_summary": filters_summary,
    }
    try:
        logger.info(
            "tasks.bulk_reschedule.preview",
            extra={
                "user_id": user.id,
                "apply_mode": apply_mode,
                "count": count,
                "requested_count": requested_count,
                "cap": cap,
                "filters_summary": filters_summary,
                "strong_confirm_required": bool(
                    apply_mode == "filtered"
                    and count is not None
                    and count > STRONG_CONFIRM_THRESHOLD
                ),
                "cap_reached": bool(cap is not None and count >= cap),
                "path": request.path,
                "method": request.method,
            },
        )
    except Exception:
        pass
    return JsonResponse(payload)


@login_required
def task_bulk_reschedule_undo(request: HttpRequest, event_id: UUID) -> HttpResponse:
    """
    Отмена массового переноса даты задач по записи из журнала действий.
    Доступно только администратору. В meta события должен быть task_due_before.
    """
    user: User = request.user
    if request.method != "POST":
        return redirect("settings_activity")

    if not require_admin(user):
        messages.error(request, "Отмена переноса доступна только администратору.")
        return redirect("settings_activity")

    event = get_object_or_404(ActivityEvent, pk=event_id)
    if event.entity_type != "task_bulk_reschedule":
        messages.error(request, "Это действие нельзя отменить.")
        return redirect("settings_activity")
    meta = event.meta or {}
    task_due_before = meta.get("task_due_before")
    if not task_due_before or meta.get("undone_at"):
        messages.error(
            request,
            "Отмена недоступна: нет сохранённых прежних дат или перенос уже отменён.",
        )
        return redirect("settings_activity")
    if not isinstance(task_due_before, list):
        messages.error(request, "Некорректные данные события.")
        return redirect("settings_activity")

    now_ts = timezone.now()
    restored = 0
    with transaction.atomic():
        for item in task_due_before:
            task_id = item.get("id")
            due_at_str = item.get("due_at")
            if not task_id:
                continue
            try:
                UUID(str(task_id))  # валидный UUID, иначе не дергать БД
            except (ValueError, TypeError):
                continue
            try:
                due_at = None
                if due_at_str:
                    due_at = datetime.fromisoformat(due_at_str.replace("Z", "+00:00"))
                    if due_at.tzinfo is None:
                        due_at = timezone.make_aware(due_at)
                Task.objects.filter(pk=task_id).update(due_at=due_at, updated_at=now_ts)
                restored += 1
            except (ValueError, TypeError, ValidationError):
                continue

        meta["undone_at"] = now_ts.isoformat()
        meta["undone_by_id"] = str(user.id)
        event.meta = meta
        event.save(update_fields=["meta"])

    log_event(
        actor=user,
        verb=ActivityEvent.Verb.UPDATE,
        entity_type="task_bulk_reschedule_undo",
        entity_id=str(event_id),
        message="Отмена массового переноса даты задач",
        meta={"event_id": str(event_id), "restored": restored},
    )
    if restored:
        messages.success(request, f"Перенос отменён: восстановлены даты у {restored} задач.")
    else:
        messages.warning(request, "Перенос отменён, но ни у одной задачи дата не изменилась (возможно, задачи удалены).")
    return redirect("settings_activity")


@login_required
@policy_required(resource_type="action", resource="ui:tasks:status")
def task_set_status(request: HttpRequest, task_id) -> HttpResponse:
    if request.method != "POST":
        return redirect("task_list")

    user: User = request.user
    try:
        task = Task.objects.select_related("company", "company__responsible", "company__branch", "assigned_to", "type").get(id=task_id)
    except Task.DoesNotExist:
        if request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return JsonResponse({"error": "Задача не найдена."}, status=404)
        raise Http404("Задача не найдена")

    if not _can_manage_task_status_ui(user, task):
        if request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return JsonResponse({"error": "Нет прав на изменение статуса этой задачи."}, status=403)
        messages.error(request, "Нет прав на изменение статуса этой задачи.")
        return redirect("task_list")

    new_status = (request.POST.get("status") or "").strip()
    if new_status not in {s for s, _ in Task.Status.choices}:
        if request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return JsonResponse({"error": "Некорректный статус."}, status=400)
        messages.error(request, "Некорректный статус.")
        return redirect("task_list")

    # Дополнительная проверка для менеджера: может менять статус только если он создатель или исполнитель
    # (основная проверка уже выполнена в _can_manage_task_status_ui выше)
    if user.role == User.Role.MANAGER:
        if task.created_by_id != user.id and task.assigned_to_id != user.id:
            if request.headers.get("X-Requested-With") == "XMLHttpRequest":
                return JsonResponse({"error": "Менеджер может менять статус только своих задач (созданных им или назначенных ему)."}, status=403)
            messages.error(request, "Менеджер может менять статус только своих задач (созданных им или назначенных ему).")
            return redirect("task_list")

    # Если статус меняется на "Выполнено", проверяем, нужно ли перенести в заметки
    save_to_notes = False
    if new_status == Task.Status.DONE:
        save_to_notes = request.POST.get("save_to_notes") == "1"
        if save_to_notes and task.company_id:
            try:
                note = _create_note_from_task(task, user)
                log_event(
                    actor=user,
                    verb=ActivityEvent.Verb.COMMENT,
                    entity_type="note",
                    entity_id=note.id,
                    company_id=task.company_id,
                    message="Добавлена заметка из выполненной задачи",
                )
            except Exception as e:
                if request.headers.get("X-Requested-With") == "XMLHttpRequest":
                    return JsonResponse({"error": f"Ошибка при создании заметки: {str(e)}"}, status=500)
                messages.error(request, f"Ошибка при создании заметки: {str(e)}")

    task.status = new_status
    if new_status == Task.Status.DONE:
        task.completed_at = timezone.now()
    task.save(update_fields=["status", "completed_at", "updated_at"])

    if not save_to_notes:
        messages.success(request, "Статус обновлён.")
    else:
        messages.success(request, "Задача выполнена. Заметка создана.")

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
                title="Статус изменён",
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
            message=f"Статус: {task.get_status_display()}",
            meta={"status": new_status},
        )
    
    if request.headers.get("X-Requested-With") == "XMLHttpRequest":
        return JsonResponse({
            "success": True,
            "note_created": save_to_notes,
            "message": "Задача выполнена." + (" Заметка создана." if save_to_notes else ""),
            "redirect": request.META.get("HTTP_REFERER") or "/tasks/"
        })
    
    return redirect(request.META.get("HTTP_REFERER") or "/tasks/")


@login_required
@policy_required(resource_type="page", resource="ui:tasks:detail")
def task_view(request: HttpRequest, task_id) -> HttpResponse:
    """
    Просмотр задачи (оптимизированный endpoint для модальных окон).
    Возвращает только HTML модального окна без всей страницы task_list.
    """
    user: User = request.user
    # Логируем начало функции для диагностики
    logger = logging.getLogger(__name__)
    logger.info(f"Task view called: user_id={user.id}, role={user.role}, task_id={task_id}")
    # Сначала загружаем задачу, чтобы проверить права на конкретную задачу
    task = get_object_or_404(
        Task.objects.select_related("company", "assigned_to", "created_by", "type").only(
            "id", "title", "description", "status", "due_at", "created_at", "completed_at",
            "company_id", "assigned_to_id", "created_by_id", "type_id",
            "company__id", "company__name", "company__responsible_id",
            "assigned_to__id", "assigned_to__first_name", "assigned_to__last_name",
            "created_by__id", "created_by__first_name", "created_by__last_name",
            "type__id", "type__name", "type__color", "type__icon"
        ),
        id=task_id
    )
    
    # Загружаем responsible_id компании напрямую, если company не загружен
    company_responsible_id = None
    if task.company_id:
        try:
            company = getattr(task, "company", None)
            if company:
                company_responsible_id = getattr(company, "responsible_id", None)
            # Если company не загружен через select_related, загружаем напрямую
            if company_responsible_id is None:
                company_responsible_id = Company.objects.filter(id=task.company_id).values_list("responsible_id", flat=True).first()
        except Exception:
            pass
    
    # Проверяем права на просмотр конкретной задачи ПЕРЕД проверкой policy
    # Это позволяет пользователям видеть задачи, к которым у них есть доступ по бизнес-логике
    can_view = False
    if user.role in (User.Role.ADMIN, User.Role.GROUP_MANAGER):
        can_view = True
    elif user.role == User.Role.MANAGER:
        # Менеджер может просматривать задачи, которые он создал или которые назначены ему
        can_view = bool(
            (task.assigned_to_id and task.assigned_to_id == user.id) or
            (task.created_by_id and task.created_by_id == user.id)
        )
        # Также менеджер может просматривать задачи по компаниям, за которые он ответственный
        if not can_view and company_responsible_id == user.id:
            can_view = True
    elif user.role in (User.Role.BRANCH_DIRECTOR, User.Role.SALES_HEAD) and user.branch_id:
        # Директор/РОП может просматривать задачи, которые он создал
        if task.created_by_id == user.id:
            can_view = True
        elif task.assigned_to_id == user.id:
            can_view = True
        elif task.company_id and getattr(task.company, "branch_id", None) == user.branch_id:
            can_view = True
        elif task.assigned_to_id and getattr(task.assigned_to, "branch_id", None) == user.branch_id:
            can_view = True
    
    # Ответственный за компанию может просматривать задачи по своей компании (для всех ролей)
    if not can_view and company_responsible_id == user.id:
        can_view = True

    # Диагностика: что именно посчитали (оставим как info, чтобы быстро понять причину 403)
    logger = logging.getLogger(__name__)
    logger.info(
        "Task view access check: user_id=%s role=%s task_id=%s created_by_id=%s assigned_to_id=%s company_id=%s company_responsible_id=%s can_view=%s",
        user.id,
        user.role,
        task.id,
        task.created_by_id,
        task.assigned_to_id,
        task.company_id,
        company_responsible_id,
        can_view,
    )
    
    # Вычисляем просрочку в днях (только если известны дедлайн и время завершения)
    view_task_overdue_days = None
    if task.due_at and task.completed_at and task.completed_at > task.due_at:
        delta = task.completed_at - task.due_at
        view_task_overdue_days = delta.days
    
    # Добавляем флаг для прав на редактирование
    task.can_edit_task = _can_edit_task_ui(user, task)  # type: ignore[attr-defined]
    
    # Сопоставляем задачу с TaskType если нужно
    if not task.type and task.title:
        from tasksapp.models import TaskType
        task_type = TaskType.objects.filter(name=task.title).first()
        if task_type:
            task.type = task_type  # type: ignore[assignment]
            task.type_id = task_type.id  # type: ignore[attr-defined]
    
    now = timezone.now()
    local_now = timezone.localtime(now)
    
    return render(request, "ui/task_view_modal.html", {
        "view_task": task,
        "view_task_overdue_days": view_task_overdue_days,
        "local_now": local_now,
    })


@login_required
@policy_required(resource_type="action", resource="ui:tasks:update")
def task_edit(request: HttpRequest, task_id) -> HttpResponse:
    """Редактирование задачи (поддержка AJAX для модалок)"""
    user: User = request.user
    task = get_object_or_404(
        Task.objects.select_related("company", "assigned_to", "created_by", "type").only(
            "id", "title", "description", "status", "due_at", "created_at", "completed_at", "recurrence_rrule",
            "company_id", "assigned_to_id", "created_by_id", "type_id",
            "company__id", "company__name",
            "assigned_to__id", "assigned_to__first_name", "assigned_to__last_name",
            "created_by__id", "created_by__first_name", "created_by__last_name",
            "type__id", "type__name", "type__color", "type__icon"
        ),
        id=task_id
    )

    if not _can_edit_task_ui(user, task):
        if request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return JsonResponse({"ok": False, "error": "Нет прав на редактирование этой задачи."}, status=403)
        messages.error(request, "Нет прав на редактирование этой задачи.")
        return redirect("task_list")
    can_delete_task = _can_delete_task_ui(user, task)

    if request.method == "POST":
        form = TaskEditForm(request.POST, instance=task)
        if form.is_valid():
            updated_task: Task = form.save(commit=False)
            # Заголовок всегда синхронизируем с выбранным типом/статусом
            if updated_task.type:
                updated_task.title = updated_task.type.name
            updated_task.save()
            log_event(
                actor=user,
                verb=ActivityEvent.Verb.UPDATE,
                entity_type="task",
                entity_id=updated_task.id,
                company_id=updated_task.company_id,
                message=f"Обновлена задача: {updated_task.title}",
            )
            # Если AJAX запрос - возвращаем JSON
            if request.headers.get("X-Requested-With") == "XMLHttpRequest":
                return JsonResponse({
                    "ok": True,
                    "task_id": str(updated_task.id),
                    "title": updated_task.title or (updated_task.type.name if updated_task.type else ""),
                    "description": updated_task.description or "",
                    "type_id": updated_task.type_id,
                    "type_name": updated_task.type.name if updated_task.type else "",
                    "type_icon": updated_task.type.icon if updated_task.type else "",
                    "type_color": updated_task.type.color if updated_task.type else "",
                    "due_at": updated_task.due_at.isoformat() if updated_task.due_at else None,
                })
            messages.success(request, "Задача обновлена.")
            # Редирект на предыдущую страницу или список задач
            referer = request.META.get("HTTP_REFERER", "/tasks/")
            if "/companies/" in referer:
                # Если редактировали из карточки компании, возвращаемся туда
                import re
                match = re.search(r"/companies/([a-f0-9-]+)/", referer)
                if match:
                    return redirect("company_detail", company_id=match.group(1))
            return redirect("task_list")
    else:
        form = TaskEditForm(instance=task)
    
    # Оптимизация queryset для типа задачи (используем only() для загрузки только необходимых полей)
    form.fields["type"].queryset = TaskType.objects.only("id", "name").order_by("name")
    
    # Если запрос на модалку (через AJAX или параметр modal=1)
    if request.headers.get("X-Requested-With") == "XMLHttpRequest" or request.GET.get("modal") == "1":
        return render(request, "ui/task_edit_modal.html", {"form": form, "task": task, "can_delete_task": can_delete_task})

    return render(request, "ui/task_edit.html", {"form": form, "task": task, "can_delete_task": can_delete_task})


# _require_admin moved to crm.utils.require_admin


@login_required
def settings_dashboard(request: HttpRequest) -> HttpResponse:
    if not require_admin(request.user):
        messages.error(request, "Доступ запрещён.")
        return redirect("dashboard")
    return render(request, "ui/settings/dashboard.html", {})


@login_required
def settings_access(request: HttpRequest) -> HttpResponse:
    """
    UI-админка: управление policy (режим observe/enforce + переход к правкам по ролям).
    """
    # Критично: управлять политиками может только реальный админ,
    # иначе любая ошибка в policy может дать эскалацию прав.
    user: User = request.user
    if not (user.is_superuser or user.role == User.Role.ADMIN):
        messages.error(request, "Доступ запрещён.")
        return redirect("dashboard")

    from policy.models import PolicyConfig
    from policy.models import PolicyRule

    cfg = PolicyConfig.load()
    rules_total = PolicyRule.objects.filter(enabled=True).count()
    rules_role_total = PolicyRule.objects.filter(enabled=True, subject_type=PolicyRule.SubjectType.ROLE).count()

    if request.method == "POST":
        # Безопасный baseline: запретить sensitive ресурсы менеджеру.
        # Это "минимально опасный" набор (уменьшает риск утечек/деструктивных операций).
        action = (request.POST.get("action") or "").strip()
        if action == "restore_default_page_rules":
            from policy.engine import baseline_allowed_for_role
            from policy.resources import list_resources

            # "Восстановление" = сделать дефолтные права видимыми в UI:
            # генерим явные allow/deny правила для СТРАНИЦ по всем ролям.
            # Важно: не перетираем уже существующие правила — только добавляем недостающие.
            changed = 0
            created = 0
            roles = [v for v, _ in User.Role.choices]
            pages = [r for r in list_resources(resource_type="page")]

            for role_value in roles:
                for res in pages:
                    exists = PolicyRule.objects.filter(
                        enabled=True,
                        subject_type=PolicyRule.SubjectType.ROLE,
                        role=role_value,
                        resource_type=res.resource_type,
                        resource=res.key,
                    ).exists()
                    if exists:
                        continue

                    allowed = baseline_allowed_for_role(
                        role=role_value,
                        resource_type=res.resource_type,
                        resource_key=res.key,
                        is_superuser=False,
                    )
                    PolicyRule.objects.create(
                        enabled=True,
                        priority=200,
                        subject_type=PolicyRule.SubjectType.ROLE,
                        role=role_value,
                        resource_type=res.resource_type,
                        resource=res.key,
                        effect=(PolicyRule.Effect.ALLOW if allowed else PolicyRule.Effect.DENY),
                        conditions={},
                    )
                    created += 1
                    changed += 1

            messages.success(
                request,
                "Дефолтные правила для страниц восстановлены (созданы недостающие записи). "
                f"Добавлено: {created}.",
            )
            return redirect("settings_access")

        if action == "restore_default_action_rules":
            from policy.engine import baseline_allowed_for_role
            from policy.resources import list_resources

            # "Восстановление" для действий: создаём недостающие role-rules для UI actions.
            # Важно: не перетираем существующие правила — только добавляем.
            created = 0
            roles = [v for v, _ in User.Role.choices]
            actions = [r for r in list_resources(resource_type="action") if (r.key or "").startswith("ui:")]

            for role_value in roles:
                for res in actions:
                    exists = PolicyRule.objects.filter(
                        enabled=True,
                        subject_type=PolicyRule.SubjectType.ROLE,
                        role=role_value,
                        resource_type=res.resource_type,
                        resource=res.key,
                    ).exists()
                    if exists:
                        continue

                    allowed = baseline_allowed_for_role(
                        role=role_value,
                        resource_type=res.resource_type,
                        resource_key=res.key,
                        is_superuser=False,
                    )
                    PolicyRule.objects.create(
                        enabled=True,
                        priority=210,
                        subject_type=PolicyRule.SubjectType.ROLE,
                        role=role_value,
                        resource_type=res.resource_type,
                        resource=res.key,
                        effect=(PolicyRule.Effect.ALLOW if allowed else PolicyRule.Effect.DENY),
                        conditions={},
                    )
                    created += 1

            messages.success(
                request,
                "Дефолтные правила для действий восстановлены (созданы недостающие записи). "
                f"Добавлено: {created}.",
            )
            return redirect("settings_access")

        if action == "baseline_manager_deny_sensitive":
            from policy.resources import list_resources

            changed = 0
            for res in list_resources():
                if not getattr(res, "sensitive", False):
                    continue
                qs = PolicyRule.objects.filter(
                    subject_type=PolicyRule.SubjectType.ROLE,
                    role=User.Role.MANAGER,
                    resource_type=res.resource_type,
                    resource=res.key,
                )
                obj = qs.order_by("id").first()
                if obj is None:
                    PolicyRule.objects.create(
                        enabled=True,
                        priority=100,
                        subject_type=PolicyRule.SubjectType.ROLE,
                        role=User.Role.MANAGER,
                        resource_type=res.resource_type,
                        resource=res.key,
                        effect=PolicyRule.Effect.DENY,
                        conditions={},
                    )
                    changed += 1
                else:
                    if obj.effect != PolicyRule.Effect.DENY or not obj.enabled:
                        obj.effect = PolicyRule.Effect.DENY
                        obj.enabled = True
                        obj.save(update_fields=["effect", "enabled", "updated_at"])
                        changed += 1

            messages.success(request, f"Baseline применён: менеджеру запрещены sensitive ресурсы. Изменений: {changed}.")
            return redirect("settings_access")

        mode = (request.POST.get("mode") or "").strip()
        if mode in (PolicyConfig.Mode.OBSERVE_ONLY, PolicyConfig.Mode.ENFORCE):
            if cfg.mode != mode:
                # Предупреждение: enforce без правил обычно означает "всё по дефолту",
                # что админ может не ожидать.
                if mode == PolicyConfig.Mode.ENFORCE:
                    confirmed = (request.POST.get("confirm_enforce") or "").strip()
                    if confirmed != "on":
                        messages.error(
                            request,
                            "Для включения enforce нужно подтверждение (галочка). "
                            "Это защита от случайного включения.",
                        )
                        return redirect("settings_access")
                if mode == PolicyConfig.Mode.ENFORCE and rules_total == 0:
                    messages.warning(
                        request,
                        "Включён режим enforce, но активных правил пока нет. "
                        "Проверьте доступы по ролям и критичные эндпоинты.",
                    )
                cfg.mode = mode
                cfg.save(update_fields=["mode", "updated_at"])
                messages.success(request, f"Режим policy обновлён: {cfg.mode}.")
        else:
            messages.error(request, "Некорректный режим policy.")
        return redirect("settings_access")

    roles = list(User.Role.choices)
    return render(
        request,
        "ui/settings/access_dashboard.html",
        {
            "cfg": cfg,
            "roles": roles,
            "rules_total": rules_total,
            "rules_role_total": rules_role_total,
        },
    )


@login_required
def settings_access_role(request: HttpRequest, role: str) -> HttpResponse:
    """
    UI-админка: правка allow/deny по ресурсам для конкретной роли.
    """
    user: User = request.user
    if not (user.is_superuser or user.role == User.Role.ADMIN):
        messages.error(request, "Доступ запрещён.")
        return redirect("dashboard")

    from policy.models import PolicyRule
    from policy.resources import list_resources

    role = (role or "").strip()
    valid_roles = {v for v, _ in User.Role.choices}
    if role not in valid_roles:
        messages.error(request, "Неизвестная роль.")
        return redirect("settings_access")

    # Текущие правила по этой роли
    existing = (
        PolicyRule.objects.filter(
            enabled=True,
            subject_type=PolicyRule.SubjectType.ROLE,
            role=role,
        )
        .order_by("priority", "id")
    )
    existing_map = {r.resource: r for r in existing}

    def _fname(key: str) -> str:
        # HTML field name (без двоеточий)
        return "perm__" + key.replace(":", "__")

    q = (request.GET.get("q") or "").strip().lower()
    group = (request.GET.get("group") or "").strip().lower()  # ui|api|phone|all
    if group not in ("", "all", "ui", "api", "phone"):
        group = ""

    resources = list_resources()
    items = []
    for res in resources:
        if group in ("ui", "api", "phone"):
            if not res.key.startswith(group + ":"):
                continue
        if q:
            hay = f"{res.key} {res.title}".lower()
            if q not in hay:
                continue
        rule = existing_map.get(res.key)
        current = "inherit"
        if rule is not None:
            current = rule.effect  # allow|deny
        items.append(
            {
                "key": res.key,
                "title": res.title,
                "resource_type": res.resource_type,
                "sensitive": bool(getattr(res, "sensitive", False)),
                "field_name": _fname(res.key),
                "value": current,
            }
        )

    if request.method == "POST":
        preset = (request.POST.get("preset") or "").strip()
        if preset:
            changed = 0
            # Пресеты применяем к ПОЛНОМУ списку ресурсов (без фильтров),
            # чтобы админ случайно не "сохранил" только часть.
            full_items = []
            for res in list_resources():
                full_items.append(
                    {
                        "key": res.key,
                        "resource_type": res.resource_type,
                        "sensitive": bool(getattr(res, "sensitive", False)),
                    }
                )

            if preset == "inherit_all":
                deleted, _ = PolicyRule.objects.filter(
                    subject_type=PolicyRule.SubjectType.ROLE,
                    role=role,
                ).delete()
                changed = deleted
                messages.success(request, f"Готово: всё сброшено в inherit. Удалено правил: {changed}.")
                return redirect("settings_access_role", role=role)

            if preset == "deny_sensitive":
                for it2 in full_items:
                    if not it2["sensitive"]:
                        continue
                    qs2 = PolicyRule.objects.filter(
                        subject_type=PolicyRule.SubjectType.ROLE,
                        role=role,
                        resource_type=it2["resource_type"],
                        resource=it2["key"],
                    )
                    obj2 = qs2.order_by("id").first()
                    if obj2 is None:
                        PolicyRule.objects.create(
                            enabled=True,
                            priority=100,
                            subject_type=PolicyRule.SubjectType.ROLE,
                            role=role,
                            resource_type=it2["resource_type"],
                            resource=it2["key"],
                            effect=PolicyRule.Effect.DENY,
                            conditions={},
                        )
                        changed += 1
                    else:
                        if obj2.effect != PolicyRule.Effect.DENY or not obj2.enabled:
                            obj2.effect = PolicyRule.Effect.DENY
                            obj2.enabled = True
                            obj2.save(update_fields=["effect", "enabled", "updated_at"])
                            changed += 1

                messages.success(request, f"Готово: sensitive ресурсы запрещены. Изменений: {changed}.")
                return redirect("settings_access_role", role=role)

        # Сохраняем по всем ресурсам
        changed = 0
        for it in items:
            key = it["key"]
            resource_type = it["resource_type"]
            v = (request.POST.get(it["field_name"]) or "").strip()
            if v not in ("inherit", PolicyRule.Effect.ALLOW, PolicyRule.Effect.DENY):
                continue

            qs = PolicyRule.objects.filter(
                subject_type=PolicyRule.SubjectType.ROLE,
                role=role,
                resource_type=resource_type,
                resource=key,
            )

            if v == "inherit":
                # Возвращаем к дефолту: удаляем правила (и disabled тоже — чтобы не копить мусор)
                deleted, _ = qs.delete()
                if deleted:
                    changed += 1
                continue

            obj = qs.order_by("id").first()
            if obj is None:
                PolicyRule.objects.create(
                    enabled=True,
                    priority=100,
                    subject_type=PolicyRule.SubjectType.ROLE,
                    role=role,
                    resource_type=resource_type,
                    resource=key,
                    effect=v,
                    conditions={},
                )
                changed += 1
            else:
                update_fields = []
                if not obj.enabled:
                    obj.enabled = True
                    update_fields.append("enabled")
                if obj.effect != v:
                    obj.effect = v
                    update_fields.append("effect")
                if obj.resource_type != resource_type:
                    obj.resource_type = resource_type
                    update_fields.append("resource_type")
                if update_fields:
                    obj.save(update_fields=update_fields + ["updated_at"])
                    changed += 1

        messages.success(request, f"Сохранено. Изменений: {changed}.")
        return redirect("settings_access_role", role=role)

    role_map = {v: label for v, label in User.Role.choices}
    return render(
        request,
        "ui/settings/access_role.html",
        {
            "role": role,
            "role_label": role_map.get(role, role),
            "items": items,
        },
    )


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
    
    # Проверяем, включён ли режим просмотра администратора
    view_as_enabled = request.session.get("view_as_enabled", False)
    
    # Обработка переключения режима просмотра
    if request.method == "POST" and "toggle_view_as" in request.POST:
        view_as_enabled = request.POST.get("view_as_enabled") == "on"
        request.session["view_as_enabled"] = view_as_enabled
        if not view_as_enabled:
            # Если режим отключён, сбрасываем все настройки просмотра
            request.session.pop("view_as_user_id", None)
            request.session.pop("view_as_role", None)
            request.session.pop("view_as_branch_id", None)
        messages.success(request, f"Режим просмотра администратора {'включён' if view_as_enabled else 'выключен'}.")
        return redirect("settings_users")
    
    # Получаем queryset пользователей
    users = User.objects.select_related("branch")

    # Фильтры
    status = (request.GET.get("status") or "").strip()
    role_filter = (request.GET.get("role") or "").strip()
    branch_filter = (request.GET.get("branch") or "").strip()
    online_filter = (request.GET.get("online") or "").strip()
    query = (request.GET.get("q") or "").strip()

    if status == "active":
        users = users.filter(is_active=True)
    elif status == "inactive":
        users = users.filter(is_active=False)

    if role_filter and role_filter in [r[0] for r in User.Role.choices]:
        users = users.filter(role=role_filter)

    if branch_filter:
        try:
            branch_id = int(branch_filter)
            users = users.filter(branch_id=branch_id)
        except (ValueError, TypeError):
            pass

    if query:
        users = users.filter(
            Q(username__icontains=query)
            | Q(first_name__icontains=query)
            | Q(last_name__icontains=query)
            | Q(email__icontains=query)
        )
    
    # Получаем информацию об активных сессиях для определения онлайн статуса
    from django.contrib.sessions.models import Session
    from django.contrib.auth import SESSION_KEY
    
    recently_online_threshold = timezone.now() - timedelta(minutes=15)
    
    # Применяем фильтр по онлайн статусу (учитывая и сессии, и last_login)
    if online_filter == "online":
        # Фильтр по онлайн: либо есть активная сессия, либо last_login за последние 15 минут
        # Получаем ID пользователей с активными сессиями (только из текущего queryset)
        user_ids_in_queryset = set(users.values_list('id', flat=True))
        online_user_ids_from_sessions = set()
        if user_ids_in_queryset:
            active_sessions = Session.objects.filter(expire_date__gte=timezone.now())
            for session in active_sessions:
                try:
                    session_data = session.get_decoded()
                    user_id_from_session = session_data.get(SESSION_KEY)
                    if user_id_from_session and int(user_id_from_session) in user_ids_in_queryset:
                        online_user_ids_from_sessions.add(int(user_id_from_session))
                except Exception:
                    pass
        
        # Получаем ID пользователей с недавним last_login из текущего queryset
        users_with_recent_login = set(
            users.filter(last_login__gte=recently_online_threshold).values_list('id', flat=True)
        )
        # Объединяем с пользователями, у которых есть активная сессия
        online_ids = online_user_ids_from_sessions | users_with_recent_login
        if online_ids:
            users = users.filter(id__in=online_ids)
        else:
            users = users.none()  # Нет онлайн пользователей
    elif online_filter == "offline":
        # Фильтр по офлайн: нет активной сессии И нет недавнего last_login
        # Получаем ID пользователей с активными сессиями (только из текущего queryset)
        user_ids_in_queryset = set(users.values_list('id', flat=True))
        if not user_ids_in_queryset:
            users = users.none()  # Нет пользователей для фильтрации
        else:
            online_user_ids_from_sessions = set()
            active_sessions = Session.objects.filter(expire_date__gte=timezone.now())
            for session in active_sessions:
                try:
                    session_data = session.get_decoded()
                    user_id_from_session = session_data.get(SESSION_KEY)
                    if user_id_from_session and int(user_id_from_session) in user_ids_in_queryset:
                        online_user_ids_from_sessions.add(int(user_id_from_session))
                except Exception:
                    pass
            
            # Получаем ID пользователей с недавним last_login из текущего queryset
            users_with_recent_login = set(
                users.filter(last_login__gte=recently_online_threshold).values_list('id', flat=True)
            )
            # Офлайн = все пользователи минус онлайн (сессии или недавний вход)
            offline_ids = user_ids_in_queryset - online_user_ids_from_sessions - users_with_recent_login
            if offline_ids:
                users = users.filter(id__in=offline_ids)
            else:
                users = users.none()  # Нет офлайн пользователей
    
    # Сортировка: читаем из GET или из cookies (ПЕРЕД выполнением queryset!)
    sort_field = (request.GET.get("sort") or "").strip()
    sort_dir = (request.GET.get("dir") or "").strip().lower()
    
    # Если параметры не указаны, читаем из cookies
    if not sort_field:
        cookie_sort = request.COOKIES.get("settings_users_sort", "")
        if cookie_sort:
            try:
                # Формат в cookies: "field:direction" (например, "username:asc")
                parts = cookie_sort.split(":")
                if len(parts) == 2:
                    sort_field, sort_dir = parts[0], parts[1]
            except Exception:
                pass
    
    # Валидация направления сортировки
    if sort_dir not in ("asc", "desc"):
        sort_dir = "asc"  # По умолчанию asc
    
    # Применяем сортировку ПЕРЕД выполнением queryset
    if sort_field == "username":
        if sort_dir == "asc":
            users = users.order_by("username")
        else:
            users = users.order_by("-username")
    elif sort_field == "full_name":
        if sort_dir == "asc":
            users = users.order_by("last_name", "first_name", "username")
        else:
            users = users.order_by("-last_name", "-first_name", "username")
    elif sort_field == "role":
        if sort_dir == "asc":
            users = users.order_by("role", "username")
        else:
            users = users.order_by("-role", "username")
    elif sort_field == "branch":
        if sort_dir == "asc":
            users = users.order_by("branch__name", "username")
        else:
            users = users.order_by("-branch__name", "username")
    elif sort_field == "is_active":
        if sort_dir == "asc":
            users = users.order_by("is_active", "username")
        else:
            users = users.order_by("-is_active", "username")
    elif sort_field == "last_login":
        if sort_dir == "asc":
            users = users.order_by("last_login", "username")
        else:
            users = users.order_by("-last_login", "username")
    else:
        # По умолчанию: сортировка по логину
        sort_field = "username"
        sort_dir = "asc"
        users = users.order_by("username")
    
    # Получаем ID пользователей с активными сессиями для отображения статуса (ПОСЛЕ сортировки, но ДО выполнения queryset)
    online_user_ids = set()
    user_ids_for_status = set(users.values_list('id', flat=True))  # Выполняем queryset один раз для получения ID
    if user_ids_for_status:
        active_sessions = Session.objects.filter(expire_date__gte=timezone.now())
        for session in active_sessions:
            try:
                session_data = session.get_decoded()
                user_id_from_session = session_data.get(SESSION_KEY)
                if user_id_from_session and int(user_id_from_session) in user_ids_for_status:
                    online_user_ids.add(int(user_id_from_session))
            except Exception:
                pass
    
    # Теперь выполняем queryset и помечаем пользователей как онлайн
    users_list = list(users)  # Выполняем queryset один раз (уже отсортированный)
    for user in users_list:
        user.is_online = (
            user.id in online_user_ids
            or (user.last_login and user.last_login >= recently_online_threshold)
        )
    
    # Формируем строку параметров для сохранения в URL (без sort и dir)
    qs_params = request.GET.copy()
    if "sort" in qs_params:
        del qs_params["sort"]
    if "dir" in qs_params:
        del qs_params["dir"]
    qs = qs_params.urlencode()
    
    # Получаем список филиалов для фильтра
    branches = Branch.objects.order_by("name")
    
    # Сохраняем сортировку в cookie, если она была изменена через GET параметры
    response = render(
        request,
        "ui/settings/users.html",
        {
            "users": users_list,  # Используем уже выполненный список
            "view_as_enabled": view_as_enabled,
            "sort_field": sort_field,
            "sort_dir": sort_dir,
            "qs": qs,
            "status_filter": status,
            "role_filter": role_filter,
            "branch_filter": branch_filter,
            "online_filter": online_filter,
            "q": query,
            "branches": branches,
        },
    )
    
    # Устанавливаем cookie для сохранения сортировки (срок действия 1 год)
    if sort_field:
        cookie_value = f"{sort_field}:{sort_dir}"
        response.set_cookie("settings_users_sort", cookie_value, max_age=31536000)  # 1 год
    
    return response


@login_required
def settings_user_create(request: HttpRequest) -> HttpResponse:
    if not require_admin(request.user):
        messages.error(request, "Доступ запрещён.")
        return redirect("dashboard")
    if request.method == "POST":
        form = UserCreateForm(request.POST)
        if form.is_valid():
            user = form.save(commit=True, created_by=request.user, request=request)
            # Сохраняем информацию о созданном пользователе в сессии для отображения
            request.session["user_created"] = {"user_id": user.id}
            
            # Если для не-администратора был сгенерирован ключ, но ссылка не была сформирована, формируем её здесь
            if user.role != User.Role.ADMIN and "magic_link_generated" in request.session:
                session_data = request.session["magic_link_generated"]
                if not session_data.get("link"):
                    from django.conf import settings as django_settings
                    public_base_url = getattr(django_settings, "PUBLIC_BASE_URL", None)
                    if public_base_url:
                        base_url = public_base_url.rstrip("/")
                    else:
                        base_url = request.build_absolute_uri("/")[:-1]
                    session_data["link"] = f"{base_url}/auth/magic/{session_data['token']}/"
                    request.session["magic_link_generated"] = session_data
            
            if user.role == User.Role.ADMIN:
                messages.success(request, f"Пользователь {user} создан. Пароль сгенерирован автоматически.")
            else:
                messages.success(request, f"Пользователь {user} создан. Ключ доступа сгенерирован автоматически.")
            return redirect("settings_user_edit", user_id=user.id)
    else:
        form = UserCreateForm()
    return render(request, "ui/settings/user_form.html", {"form": form, "mode": "create"})


@login_required
def settings_user_edit(request: HttpRequest, user_id: int) -> HttpResponse:
    if not require_admin(request.user):
        messages.error(request, "Доступ запрещён.")
        return redirect("dashboard")
    u = get_object_or_404(User, id=user_id)
    
    # Получаем все токены доступа для отображения истории
    all_tokens = []
    if u.is_active:
        all_tokens = (
            MagicLinkToken.objects.filter(user=u)
            .order_by("-created_at")
            .select_related("created_by")
        )
        
        # Получаем дату последней активности для каждого токена (если токен использован)
        from audit.models import ActivityEvent
        for token in all_tokens:
            if token.used_at:
                # Ищем последнюю активность пользователя после использования токена
                last_activity = (
                    ActivityEvent.objects.filter(
                        actor=u,
                        created_at__gte=token.used_at
                    )
                    .order_by("-created_at")
                    .first()
                )
                if last_activity:
                    token.last_activity = last_activity.created_at
                else:
                    token.last_activity = token.used_at
            else:
                token.last_activity = None
    
    # Проверяем, была ли только что сгенерирована ссылка (из сессии)
    magic_link_generated = None
    if "magic_link_generated" in request.session:
        session_data = request.session.pop("magic_link_generated")
        if session_data.get("user_id") == user_id:
            magic_link_generated = session_data
    
    # Проверяем, был ли только что создан пользователь
    user_created = None
    if "user_created" in request.session:
        session_data = request.session.pop("user_created")
        if session_data.get("user_id") == user_id:
            user_created = True
    
    # Проверяем, был ли сгенерирован пароль для администратора
    admin_password_generated = None
    if "admin_password_generated" in request.session:
        session_data = request.session.pop("admin_password_generated")
        if session_data.get("user_id") == user_id:
            admin_password_generated = session_data
    
    if request.method == "POST":
        form = UserEditForm(request.POST, instance=u)
        if form.is_valid():
            form.save()
            messages.success(request, "Пользователь обновлён.")
            return redirect("settings_users")
    else:
        form = UserEditForm(instance=u)
    
    # Получаем информацию о сессиях пользователя
    from django.contrib.sessions.models import Session
    from django.contrib.auth import SESSION_KEY
    active_sessions = []
    for session in Session.objects.filter(expire_date__gte=timezone.now()):
        session_data = session.get_decoded()
        user_id_from_session = session_data.get(SESSION_KEY)
        if user_id_from_session and int(user_id_from_session) == u.id:
            active_sessions.append({
                "session_key": session.session_key,
                "expire_date": session.expire_date,
                "last_activity": session.expire_date,  # Приблизительно
            })
    
    return render(
        request,
        "ui/settings/user_form.html",
        {
            "form": form,
            "mode": "edit",
            "u": u,
            "all_magic_link_tokens": all_tokens,
            "magic_link_generated": magic_link_generated,
            "user_created": user_created,
            "admin_password_generated": admin_password_generated,
            "active_sessions": active_sessions,
            "now": timezone.now(),
        },
    )


@login_required
def settings_user_magic_link_generate(request: HttpRequest, user_id: int) -> HttpResponse:
    """
    Генерация одноразовой ссылки входа для пользователя (только для админа).
    URL: /settings/users/<user_id>/magic-link/generate/
    """
    if not require_admin(request.user):
        messages.error(request, "Доступ запрещён.")
        return redirect("dashboard")
    
    user = get_object_or_404(User, id=user_id, is_active=True)
    admin_user: User = request.user
    
    # Rate limiting: не чаще 1 раза в 10 секунд на пользователя
    from accounts.security import is_ip_rate_limited, get_client_ip
    ip = get_client_ip(request)
    cache_key = f"magic_link_generate_rate:{user_id}"
    from django.core.cache import cache
    if cache.get(cache_key):
        messages.error(request, "Подождите 10 секунд перед генерацией новой ссылки.")
        return redirect("settings_user_edit", user_id=user_id)
    cache.set(cache_key, True, 10)
    
    # Генерируем токен
    from accounts.models import MagicLinkToken
    from django.conf import settings as django_settings
    
    magic_link, plain_token = MagicLinkToken.create_for_user(
        user=user,
        created_by=admin_user,
        # TTL по умолчанию 24 часа (1440 минут)
    )
    
    # Формируем полную ссылку
    # Используем PUBLIC_BASE_URL если есть, иначе используем request
    public_base_url = getattr(django_settings, "PUBLIC_BASE_URL", None)
    if public_base_url:
        base_url = public_base_url.rstrip("/")
    else:
        base_url = request.build_absolute_uri("/")[:-1]
    
    magic_link_url = f"{base_url}/auth/magic/{plain_token}/"
    
    # Логируем генерацию
    try:
        log_event(
            actor=admin_user,
            verb=ActivityEvent.Verb.CREATE,
            entity_type="magic_link",
            entity_id=str(magic_link.id),
            message=f"Создана ссылка входа для {user}",
            meta={"user_id": user.id, "expires_at": str(magic_link.expires_at)},
        )
    except Exception as e:
        logger.warning(
            f"Ошибка при логировании генерации magic link: {e}",
            exc_info=True,
            extra={"admin_user_id": admin_user.id if admin_user else None, "target_user_id": user.id},
        )
    
    # Возвращаем JSON с ссылкой (для AJAX) или редиректим с сообщением
    if request.headers.get("X-Requested-With") == "XMLHttpRequest":
        return JsonResponse({
            "success": True,
            "token": plain_token,
            "link": magic_link_url,
            "expires_at": magic_link.expires_at.isoformat(),
        })
    
    # Для обычного запроса сохраняем в сессии и редиректим
    request.session["magic_link_generated"] = {
        "token": plain_token,  # Сохраняем сам ключ для отображения
        "link": magic_link_url,
        "expires_at": magic_link.expires_at.isoformat(),
        "user_id": user_id,
    }
    messages.success(request, f"Ссылка входа создана для {user}. Она будет показана на странице редактирования.")
    return redirect("settings_user_edit", user_id=user_id)


@login_required
def settings_user_logout(request: HttpRequest, user_id: int) -> HttpResponse:
    """
    Принудительное разлогинивание пользователя (завершение всех его сессий).
    URL: /settings/users/<user_id>/logout/
    """
    if not require_admin(request.user):
        messages.error(request, "Доступ запрещён.")
        return redirect("dashboard")
    
    target_user = get_object_or_404(User, id=user_id)
    admin_user: User = request.user
    
    if request.method == "POST":
        # Удаляем все сессии пользователя
        from django.contrib.sessions.models import Session
        from django.contrib.auth import SESSION_KEY
        
        sessions_deleted = 0
        for session in Session.objects.filter(expire_date__gte=timezone.now()):
            session_data = session.get_decoded()
            user_id_from_session = session_data.get(SESSION_KEY)
            if user_id_from_session and int(user_id_from_session) == target_user.id:
                session.delete()
                sessions_deleted += 1
        
        # Логируем действие
        try:
            log_event(
                actor=admin_user,
                verb=ActivityEvent.Verb.UPDATE,
                entity_type="security",
                entity_id=f"user_logout:{target_user.id}",
                message=f"Администратор {admin_user} разлогинил пользователя {target_user}",
                meta={"target_user_id": target_user.id, "sessions_deleted": sessions_deleted},
            )
        except Exception as e:
            logger.warning(
                f"Ошибка при логировании разлогинивания пользователя: {e}",
                exc_info=True,
                extra={"admin_user_id": admin_user.id if admin_user else None, "target_user_id": target_user.id},
            )
        
        messages.success(request, f"Пользователь {target_user} разлогинен. Завершено сессий: {sessions_deleted}.")
        return redirect("settings_users")
    
    return redirect("settings_users")


@login_required
def settings_user_form_ajax(request: HttpRequest, user_id: int) -> JsonResponse:
    """
    AJAX endpoint для получения формы редактирования пользователя (для модалки).
    """
    if not require_admin(request.user):
        return JsonResponse({"ok": False, "error": "Доступ запрещён."}, status=403)
    
    user = get_object_or_404(User, id=user_id)
    from ui.forms import UserEditForm
    
    form = UserEditForm(instance=user)
    
    # Рендерим форму в HTML
    from django.template.loader import render_to_string
    form_html = render_to_string(
        "ui/settings/user_form_inline.html",
        {
            "form": form,
            "u": user,
        },
        request=request,
    )
    
    return JsonResponse({
        "ok": True,
        "html": form_html,
        "user": {
            "id": user.id,
            "username": user.username,
            "full_name": str(user),
            "email": user.email,
            "role": user.role,
            "role_display": user.get_role_display(),
            "branch": str(user.branch) if user.branch else None,
            "is_active": user.is_active,
        },
    })


@login_required
def settings_user_update_ajax(request: HttpRequest, user_id: int) -> JsonResponse:
    """
    AJAX endpoint для сохранения изменений пользователя (для модалки).
    """
    if not require_admin(request.user):
        return JsonResponse({"ok": False, "error": "Доступ запрещён."}, status=403)
    
    if request.method != "POST":
        return JsonResponse({"ok": False, "error": "Метод не поддерживается."}, status=405)
    
    user = get_object_or_404(User, id=user_id)
    from ui.forms import UserEditForm
    
    form = UserEditForm(request.POST, instance=user)
    
    if form.is_valid():
        form.save()
        return JsonResponse({
            "ok": True,
            "message": "Пользователь обновлён.",
            "user": {
                "id": user.id,
                "username": user.username,
                "full_name": str(user),
                "email": user.email,
                "role": user.role,
                "role_display": user.get_role_display(),
                "branch": str(user.branch) if user.branch else None,
                "is_active": user.is_active,
            },
        })
    else:
        from django.template.loader import render_to_string
        form_html = render_to_string(
            "ui/settings/user_form_inline.html",
            {
                "form": form,
                "u": user,
            },
            request=request,
        )
        return JsonResponse({
            "ok": False,
            "errors": form.errors,
            "html": form_html,
        }, status=400)


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
            "contract_types": ContractType.objects.order_by("order", "name"),
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
def settings_contract_type_create(request: HttpRequest) -> HttpResponse:
    if not require_admin(request.user):
        messages.error(request, "Доступ запрещён.")
        return redirect("dashboard")
    if request.method == "POST":
        form = ContractTypeForm(request.POST)
        if form.is_valid():
            form.save()
            messages.success(request, "Вид договора добавлен.")
            return redirect("settings_dicts")
    else:
        form = ContractTypeForm()
    return render(request, "ui/settings/dict_form.html", {"form": form, "title": "Новый вид договора"})


@login_required
def settings_task_type_create(request: HttpRequest) -> HttpResponse:
    if not require_admin(request.user):
        messages.error(request, "Доступ запрещён.")
        return redirect("dashboard")
    if request.method == "POST":
        form = TaskTypeForm(request.POST)
        if form.is_valid():
            form.save()
            # Инвалидируем кэш типов задач
            from django.core.cache import cache
            cache.delete('task_types_all_dict')
            messages.success(request, "Задача добавлена.")
            return redirect("settings_dicts")
    else:
        form = TaskTypeForm()
    return render(request, "ui/settings/dict_form.html", {"form": form, "title": "Новая задача"})


@login_required
def settings_company_status_edit(request: HttpRequest, status_id: int) -> HttpResponse:
    """Редактирование статуса компании через модалку (AJAX)"""
    if not require_admin(request.user):
        return JsonResponse({"ok": False, "error": "Доступ запрещён."}, status=403)
    status = get_object_or_404(CompanyStatus, id=status_id)
    if request.method == "POST":
        form = CompanyStatusForm(request.POST, instance=status)
        if form.is_valid():
            form.save()
            if request.headers.get("X-Requested-With") == "XMLHttpRequest":
                return JsonResponse({"ok": True, "id": status.id, "name": status.name})
            messages.success(request, "Статус обновлён.")
            return redirect("settings_dicts")
    else:
        form = CompanyStatusForm(instance=status)
    return render(request, "ui/settings/dict_form_modal.html", {"form": form, "title": "Редактировать статус компании", "dict_type": "company-status", "dict_id": status.id})


@login_required
def settings_company_status_delete(request: HttpRequest, status_id: int) -> HttpResponse:
    """Удаление статуса компании"""
    if not require_admin(request.user):
        return JsonResponse({"ok": False, "error": "Доступ запрещён."}, status=403)
    if request.method != "POST":
        return JsonResponse({"ok": False, "error": "Method not allowed."}, status=405)
    status = get_object_or_404(CompanyStatus, id=status_id)
    status.delete()
    if request.headers.get("X-Requested-With") == "XMLHttpRequest":
        return JsonResponse({"ok": True})
    messages.success(request, "Статус удалён.")
    return redirect("settings_dicts")


@login_required
def settings_company_sphere_edit(request: HttpRequest, sphere_id: int) -> HttpResponse:
    """Редактирование сферы компании через модалку (AJAX)"""
    if not require_admin(request.user):
        return JsonResponse({"ok": False, "error": "Доступ запрещён."}, status=403)
    sphere = get_object_or_404(CompanySphere, id=sphere_id)
    if request.method == "POST":
        form = CompanySphereForm(request.POST, instance=sphere)
        if form.is_valid():
            form.save()
            if request.headers.get("X-Requested-With") == "XMLHttpRequest":
                return JsonResponse({"ok": True, "id": sphere.id, "name": sphere.name})
            messages.success(request, "Сфера обновлена.")
            return redirect("settings_dicts")
    else:
        form = CompanySphereForm(instance=sphere)
    return render(request, "ui/settings/dict_form_modal.html", {"form": form, "title": "Редактировать сферу компании", "dict_type": "company-sphere", "dict_id": sphere.id})


@login_required
def settings_company_sphere_delete(request: HttpRequest, sphere_id: int) -> HttpResponse:
    """Удаление сферы компании"""
    if not require_admin(request.user):
        return JsonResponse({"ok": False, "error": "Доступ запрещён."}, status=403)
    if request.method != "POST":
        return JsonResponse({"ok": False, "error": "Method not allowed."}, status=405)
    sphere = get_object_or_404(CompanySphere, id=sphere_id)
    sphere.delete()
    if request.headers.get("X-Requested-With") == "XMLHttpRequest":
        return JsonResponse({"ok": True})
    messages.success(request, "Сфера удалена.")
    return redirect("settings_dicts")


@login_required
def settings_contract_type_edit(request: HttpRequest, contract_type_id: int) -> HttpResponse:
    """Редактирование вида договора через модалку (AJAX)"""
    if not require_admin(request.user):
        return JsonResponse({"ok": False, "error": "Доступ запрещён."}, status=403)
    contract_type = get_object_or_404(ContractType, id=contract_type_id)
    if request.method == "POST":
        form = ContractTypeForm(request.POST, instance=contract_type)
        if form.is_valid():
            form.save()
            if request.headers.get("X-Requested-With") == "XMLHttpRequest":
                return JsonResponse({"ok": True, "id": contract_type.id, "name": contract_type.name})
            messages.success(request, "Вид договора обновлён.")
            return redirect("settings_dicts")
    else:
        form = ContractTypeForm(instance=contract_type)
    return render(request, "ui/settings/dict_form_modal.html", {"form": form, "title": "Редактировать вид договора", "dict_type": "contract-type", "dict_id": contract_type.id})


@login_required
def settings_contract_type_delete(request: HttpRequest, contract_type_id: int) -> HttpResponse:
    """Удаление вида договора"""
    if not require_admin(request.user):
        return JsonResponse({"ok": False, "error": "Доступ запрещён."}, status=403)
    if request.method != "POST":
        return JsonResponse({"ok": False, "error": "Method not allowed."}, status=405)
    contract_type = get_object_or_404(ContractType, id=contract_type_id)
    contract_type.delete()
    if request.headers.get("X-Requested-With") == "XMLHttpRequest":
        return JsonResponse({"ok": True})
    messages.success(request, "Вид договора удалён.")
    return redirect("settings_dicts")


@login_required
def settings_task_type_edit(request: HttpRequest, task_type_id: int) -> HttpResponse:
    """Редактирование типа задачи через модалку (AJAX)"""
    if not require_admin(request.user):
        return JsonResponse({"ok": False, "error": "Доступ запрещён."}, status=403)
    task_type = get_object_or_404(TaskType, id=task_type_id)
    if request.method == "POST":
        form = TaskTypeForm(request.POST, instance=task_type)
        if form.is_valid():
            form.save()
            # Инвалидируем кэш типов задач
            from django.core.cache import cache
            cache.delete('task_types_all_dict')
            if request.headers.get("X-Requested-With") == "XMLHttpRequest":
                return JsonResponse({"ok": True, "id": task_type.id, "name": task_type.name, "icon": task_type.icon or "", "color": task_type.color or ""})
            messages.success(request, "Задача обновлена.")
            return redirect("settings_dicts")
    else:
        form = TaskTypeForm(instance=task_type)
    return render(request, "ui/settings/dict_form_modal.html", {"form": form, "title": "Редактировать задачу", "dict_type": "task-type", "dict_id": task_type.id})


@login_required
def settings_task_type_delete(request: HttpRequest, task_type_id: int) -> HttpResponse:
    """Удаление типа задачи"""
    if not require_admin(request.user):
        return JsonResponse({"ok": False, "error": "Доступ запрещён."}, status=403)
    if request.method != "POST":
        return JsonResponse({"ok": False, "error": "Method not allowed."}, status=405)
    task_type = get_object_or_404(TaskType, id=task_type_id)
    task_type.delete()
    # Инвалидируем кэш типов задач
    from django.core.cache import cache
    cache.delete('task_types_all_dict')
    if request.headers.get("X-Requested-With") == "XMLHttpRequest":
        return JsonResponse({"ok": True})
    messages.success(request, "Задача удалена.")
    return redirect("settings_dicts")


@login_required
def settings_activity(request: HttpRequest) -> HttpResponse:
    if not require_admin(request.user):
        messages.error(request, "Доступ запрещён.")
        return redirect("dashboard")
    # Не показываем в журнале события типа "policy" (решения политики доступа: poll, page и т.д.),
    # чтобы видны были только реальные действия сотрудников. В БД и логах на сервере они остаются.
    events = (
        ActivityEvent.objects.select_related("actor")
        .exclude(entity_type="policy")
        .order_by("-created_at")[:500]
    )
    return render(request, "ui/settings/activity.html", {
        "events": events,
        "can_undo_bulk_reschedule": require_admin(request.user),
    })


@login_required
def settings_error_log(request: HttpRequest) -> HttpResponse:
    """Страница лога ошибок (аналогично error_log в MODX CMS)."""
    if not require_admin(request.user):
        messages.error(request, "Доступ запрещён.")
        return redirect("dashboard")
    
    from audit.models import ErrorLog
    from django.db.models import Q
    
    # Фильтры
    level_filter = request.GET.get("level", "")
    resolved_filter = request.GET.get("resolved", "")
    path_filter = request.GET.get("path", "")
    search_query = request.GET.get("q", "")
    
    # Базовый queryset
    errors = ErrorLog.objects.select_related("user", "resolved_by").order_by("-created_at")
    
    # Применяем фильтры
    if level_filter:
        errors = errors.filter(level=level_filter)
    
    if resolved_filter == "1":
        errors = errors.filter(resolved=True)
    elif resolved_filter == "0":
        errors = errors.filter(resolved=False)
    
    if path_filter:
        errors = errors.filter(path__icontains=path_filter)
    
    if search_query:
        errors = errors.filter(
            Q(message__icontains=search_query) |
            Q(exception_type__icontains=search_query) |
            Q(path__icontains=search_query)
        )
    
    # Пагинация
    from django.core.paginator import Paginator
    paginator = Paginator(errors, 50)  # 50 ошибок на страницу
    page_number = request.GET.get("page", 1)
    page_obj = paginator.get_page(page_number)
    
    # Статистика
    total_count = ErrorLog.objects.count()
    unresolved_count = ErrorLog.objects.filter(resolved=False).count()
    error_count = ErrorLog.objects.filter(level=ErrorLog.Level.ERROR).count()
    critical_count = ErrorLog.objects.filter(level=ErrorLog.Level.CRITICAL).count()
    
    context = {
        "errors": page_obj,
        "total_count": total_count,
        "unresolved_count": unresolved_count,
        "error_count": error_count,
        "critical_count": critical_count,
        "level_filter": level_filter,
        "resolved_filter": resolved_filter,
        "path_filter": path_filter,
        "search_query": search_query,
        "levels": ErrorLog.Level.choices,
    }
    
    return render(request, "ui/settings/error_log.html", context)


@login_required
def settings_error_log_resolve(request: HttpRequest, error_id) -> HttpResponse:
    """Отметить ошибку как исправленную."""
    if not require_admin(request.user):
        messages.error(request, "Доступ запрещён.")
        return redirect("dashboard")
    
    from audit.models import ErrorLog
    from django.utils import timezone
    
    error = get_object_or_404(ErrorLog, id=error_id)
    
    if request.method == "POST":
        error.resolved = True
        error.resolved_at = timezone.now()
        error.resolved_by = request.user
        error.notes = request.POST.get("notes", "")[:1000]
        error.save()
        messages.success(request, "Ошибка отмечена как исправленная.")
    
    return redirect("settings_error_log")


@login_required
def settings_error_log_unresolve(request: HttpRequest, error_id) -> HttpResponse:
    """Снять отметку об исправлении ошибки."""
    if not require_admin(request.user):
        messages.error(request, "Доступ запрещён.")
        return redirect("dashboard")
    
    from audit.models import ErrorLog
    
    error = get_object_or_404(ErrorLog, id=error_id)
    error.resolved = False
    error.resolved_at = None
    error.resolved_by = None
    error.save()
    messages.success(request, "Отметка об исправлении снята.")
    
    return redirect("settings_error_log")


@login_required
def settings_error_log_details(request: HttpRequest, error_id) -> JsonResponse:
    """AJAX endpoint для получения деталей ошибки."""
    if not require_admin(request.user):
        return JsonResponse({"error": "Доступ запрещён."}, status=403)
    
    from audit.models import ErrorLog
    
    error = get_object_or_404(ErrorLog, id=error_id)
    
    data = {
        "created_at": error.created_at.strftime("%d.%m.%Y %H:%M:%S"),
        "level": error.level,
        "level_display": error.get_level_display(),
        "exception_type": error.exception_type,
        "message": error.message,
        "traceback": error.traceback,
        "method": error.method,
        "path": error.path,
        "user": error.user.get_full_name() if error.user else None,
        "ip_address": str(error.ip_address) if error.ip_address else None,
        "user_agent": error.user_agent,
        "request_data": error.request_data,
        "notes": error.notes,
    }
    
    return JsonResponse(data)


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
            cfg.region_custom_field_id = form.cleaned_data.get("region_custom_field_id") or None
            cfg.save(
                update_fields=[
                    "domain",
                    "client_id",
                    "client_secret",
                    "redirect_uri",
                    "long_lived_token",
                    "region_custom_field_id",
                    "updated_at",
                ]
            )
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
                "region_custom_field_id": getattr(cfg, "region_custom_field_id", None),
            }
        )

    auth_url = ""
    if cfg.domain and cfg.client_id and cfg.redirect_uri:
        try:
            auth_url = AmoClient(cfg).authorize_url()
        except Exception:
            auth_url = ""

    # Вычисляем redirect_uri для отображения в шаблоне
    redirect_uri_display = cfg.redirect_uri or request.build_absolute_uri("/settings/amocrm/callback/")
    
    return render(
        request,
        "ui/settings/amocrm.html",
        {"form": form, "cfg": cfg, "auth_url": auth_url, "redirect_uri_display": redirect_uri_display},
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

    # AmoCRM возвращает referer с поддоменом пользователя
    # Нужно использовать этот поддомен для обмена кода на токен
    referer = (request.GET.get("referer") or "").strip()
    state = (request.GET.get("state") or "").strip()
    
    cfg = AmoApiConfig.load()
    
    # Логируем все параметры для отладки
    import logging
    logger = logging.getLogger(__name__)
    logger.info(f"AmoCRM OAuth callback: code={'present' if code else 'missing'}, referer={referer}, state={state}, current_domain={cfg.domain}")
    
    # Если получен referer, обновляем domain (но сохраняем старый, если referer пустой)
    if referer:
        # referer может быть в формате "subdomain.amocrm.ru" или полный URL
        referer_domain = referer.replace("https://", "").replace("http://", "").strip("/")
        if referer_domain and referer_domain != cfg.domain:
            logger.info(f"Updating domain from {cfg.domain} to {referer_domain} based on referer")
            cfg.domain = referer_domain
            cfg.save(update_fields=["domain", "updated_at"])
    else:
        logger.warning(f"No referer parameter received. Using existing domain: {cfg.domain}")
    
    # Проверяем наличие обязательных параметров
    if not cfg.client_id:
        messages.error(request, "Client ID не настроен. Проверьте настройки AmoCRM.")
        return redirect("settings_amocrm")
    
    if not cfg.client_secret:
        messages.error(request, "Client Secret не настроен. Проверьте настройки AmoCRM.")
        return redirect("settings_amocrm")
    
    if not cfg.redirect_uri:
        messages.error(request, "Redirect URI не настроен. Проверьте настройки AmoCRM.")
        return redirect("settings_amocrm")
    
    try:
        logger.info(f"Exchanging code for token. Domain: {cfg.domain}, Client ID: {cfg.client_id[:10]}..., Redirect URI: {cfg.redirect_uri}")
        AmoClient(cfg).exchange_code(code)
        messages.success(request, "amoCRM подключен. Токены сохранены.")
    except AmoApiError as e:
        error_msg = str(e)
        logger.error(f"AmoCRM token exchange failed: {error_msg}")
        cfg.last_error = error_msg
        cfg.save(update_fields=["last_error", "updated_at"])
        
        # Более детальное сообщение об ошибке
        if "403" in error_msg:
            messages.error(
                request,
                f"Ошибка 403 Forbidden при обмене токена. Возможные причины:\n"
                f"- IP адрес заблокирован AmoCRM\n"
                f"- Неправильный Client Secret\n"
                f"- Неправильный Redirect URI (должен точно совпадать с настройками интеграции)\n"
                f"- Domain: {cfg.domain}\n"
                f"Ошибка: {error_msg}"
            )
        else:
            messages.error(request, f"Ошибка подключения amoCRM: {error_msg}")
    except Exception as e:
        import traceback
        error_details = traceback.format_exc()
        logger.error(f"Unexpected error during AmoCRM token exchange: {error_details}")
        cfg.last_error = str(e)
        cfg.save(update_fields=["last_error", "updated_at"])
        messages.error(request, f"Неожиданная ошибка: {str(e)}. Проверьте логи.")
    
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
    users = []  # По умолчанию пустой список
    try:
        client = AmoClient(cfg)
        amo_users_raw = fetch_amo_users(client)
        # Если список пользователей пуст (например, из-за 403), показываем предупреждение
        if not amo_users_raw:
            messages.warning(
                request,
                "Не удалось получить список пользователей AmoCRM. "
                "Long-lived token может не иметь прав на доступ к /api/v4/users. "
                "Для полного доступа используйте OAuth токен (переавторизуйтесь). "
                "Миграция будет работать, но выбор ответственного пользователя может быть ограничен."
            )
        else:
            # Список пользователей amoCRM для выбора ответственного (без филиалов).
            # fetch_amo_users(client) не принимает branch — всегда полный список; фильтрации по филиалу нет.
            users = [{"id": u.get("id"), "name": u.get("name")} for u in (amo_users_raw or [])]
        fields = fetch_company_custom_fields(client)
        cfg.last_error = ""
        cfg.save(update_fields=["last_error", "updated_at"])
    except AmoApiError as e:
        cfg.last_error = str(e)
        cfg.save(update_fields=["last_error", "updated_at"])
        # Если это не 403 для users, показываем ошибку
        if "403" not in str(e) or "/api/v4/users" not in str(e):
            messages.error(request, f"Ошибка API amoCRM: {e}")
        else:
            messages.warning(
                request,
                f"Не удалось получить список пользователей: {e}. "
                "Миграция будет работать, но выбор ответственного пользователя может быть ограничен."
            )
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
    run_id = None
    migrate_responsible_user_id = None
    if request.method == "POST":
        form = AmoMigrateFilterForm(request.POST)
        # Если offset = 0, это новый импорт - очищаем результаты предыдущего импорта
        offset = int(request.POST.get("offset") or 0)
        if offset == 0:
            result = None  # Явно очищаем результаты для нового импорта
        if form.is_valid():
            if not client:
                messages.error(request, "Ошибка: клиент amoCRM не инициализирован. Проверьте настройки подключения.")
            else:
                try:
                    # Проверяем, была ли нажата кнопка "Dry-run"
                    action = request.POST.get("action", "")
                    is_dry_run_button = action == "dry_run"
                    
                    # Используем размер пачки, указанный пользователем
                    # Если нажата кнопка "Dry-run", принудительно устанавливаем 10 компаний
                    if is_dry_run_button:
                        batch_size = 10
                        dry_run = True
                    else:
                        batch_size = int(form.cleaned_data.get("limit_companies") or 0)
                        if batch_size <= 0:
                            batch_size = 10  # дефолт, если не указано
                        dry_run = bool(form.cleaned_data.get("dry_run"))
                    
                    migrate_all = bool(form.cleaned_data.get("migrate_all_companies", False))
                    custom_field_id = form.cleaned_data.get("custom_field_id") or 0
                    
                    # Ответственный — только один (single select); при массиве — 400
                    raw_ids = request.POST.getlist("responsible_user_id")
                    if len(raw_ids) > 1:
                        messages.error(request, "Выберите только одного менеджера. Передан массив.")
                    else:
                        val = request.POST.get("responsible_user_id") or (form.cleaned_data.get("responsible_user_id") if form.cleaned_data else None)
                        if not val:
                            messages.error(request, "Выберите ответственного пользователя.")
                        else:
                            try:
                                responsible_user_id = int(val) if isinstance(val, (int, str)) else int(str(val).strip().split(",")[0])
                            except (ValueError, TypeError):
                                responsible_user_id = None
                            if not responsible_user_id:
                                messages.error(request, "Некорректный идентификатор ответственного.")
                            else:
                                # Запрет параллельного импорта: блокировка per-user (два админа не мешали друг другу).
                                # migrate_filtered синхронный, не пишет промежуточный прогресс; общее состояние — ключ amocrm_import_run.
                                # Внимание: request держится всё время импорта — проверьте nginx/gunicorn timeouts. Для долгих
                                # импортов предпочтительнее background job (Celery), иначе обрывы и «упал до finally».
                                lock_key = f"amocrm_import_run:{request.user.id}"
                                run_id = str(uuid.uuid4())
                                lock_payload = json.dumps({
                                    "run_id": run_id,
                                    "status": "running",
                                    "started_at": datetime.now().isoformat(),
                                })
                                lock_acquired = cache.add(lock_key, lock_payload, timeout=3600)
                                if not lock_acquired:
                                    messages.error(request, "Импорт уже выполняется. Дождитесь завершения.")
                                    result = None
                                    run_id = None
                                else:
                                    try:
                                        target_responsible = form.cleaned_data.get("target_responsible")
                                        result = migrate_filtered(
                                            client=client,
                                            actor=request.user,
                                            responsible_user_id=responsible_user_id,
                                            sphere_field_id=int(custom_field_id),
                                            sphere_option_id=form.cleaned_data.get("custom_value_enum_id") or None,
                                            sphere_label=form.cleaned_data.get("custom_value_label") or None,
                                            limit_companies=batch_size,
                                            offset=int(form.cleaned_data.get("offset") or 0),
                                            dry_run=dry_run,
                                            import_tasks=bool(form.cleaned_data.get("import_tasks")),
                                            import_notes=bool(form.cleaned_data.get("import_notes")),
                                            import_contacts=bool(form.cleaned_data.get("import_contacts")),
                                            company_fields_meta=fields,
                                            skip_field_filter=migrate_all,
                                            region_field_id=getattr(cfg, "region_custom_field_id", None) or None,
                                            target_responsible=target_responsible,
                                        )
                                        migrate_responsible_user_id = responsible_user_id
                                        if dry_run:
                                            messages.success(request, "Проверка (dry-run) выполнена.")
                                        else:
                                            messages.success(request, "Импорт выполнен.")
                                    finally:
                                        # Удаляем ключ только если lock реально взяли (мы в ветке else при lock_acquired=True).
                                        cache.delete(lock_key)
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
            {
                "cfg": cfg,
                "form": form,
                "users": users,
                "fields": fields,
                "result": result,
                "run_id": run_id,
                "migrate_responsible_user_id": migrate_responsible_user_id,
            },
        )
    except Exception as e:
        import traceback
        error_details = traceback.format_exc()
        print(f"AMOCRM_MIGRATE_ERROR: Failed to render template: {error_details}")
        # Возвращаем простую страницу с ошибкой
        from django.http import HttpResponse
        return HttpResponse(f"Ошибка рендеринга страницы миграции: {str(e)}. Проверьте логи сервера для деталей.", status=500)


@login_required
def settings_amocrm_migrate_progress(request: HttpRequest) -> HttpResponse:
    """
    GET: прогресс импорта amoCRM по текущему пользователю.
    active_run только при status=running; done/failed/canceled → active_run: null.
    При не-running — self-clean. Парсинг: dict как есть; str/bytes → json.loads;
    delete только если str/bytes не парсится (чтобы не стереть валидный ключ из-за типа).
    """
    if not require_admin(request.user):
        return JsonResponse({"error": "Forbidden", "active_run": None}, status=403)
    lock_key = f"amocrm_import_run:{request.user.id}"
    raw = cache.get(lock_key)
    if raw is None:
        return JsonResponse({"active_run": None})

    if isinstance(raw, dict):
        data = raw
    elif isinstance(raw, (str, bytes)):
        s = raw.decode("utf-8") if isinstance(raw, bytes) else raw
        try:
            data = json.loads(s)
        except (TypeError, ValueError, json.JSONDecodeError):
            cache.delete(lock_key)  # не парсится — мусор, не валидный run
            return JsonResponse({"active_run": None})
    else:
        # Неожиданный тип (артефакт бэкенда) — не удаляем, чтобы не стереть валидный ключ
        return JsonResponse({"active_run": None})

    status = (data.get("status") or "").lower()
    if status not in ("running",):
        cache.delete(lock_key)
        return JsonResponse({"active_run": None})
    return JsonResponse({
        "active_run": {"run_id": data.get("run_id"), "status": data.get("status", "running")},
    })


@login_required
def settings_amocrm_contacts_dry_run(request: HttpRequest) -> HttpResponse:
    """
    Отдельный dry-run для контактов компаний.
    Показывает все контакты, найденные у компаний из текущей пачки.
    """
    if not require_admin(request.user):
        messages.error(request, "Доступ запрещён.")
        return redirect("dashboard")

    from amocrm.client import AmoClient, AmoApiError

    cfg = AmoApiConfig.load()
    if not cfg.domain:
        messages.error(request, "AmoCRM domain не настроен.")
        return redirect("settings_amocrm_migrate")

    try:
        client = AmoClient(cfg)
    except Exception as e:
        messages.error(request, f"Ошибка создания клиента AmoCRM: {e}")
        return redirect("settings_amocrm_migrate")

    # Получаем параметры из GET или POST
    responsible_user_id = request.GET.get("responsible_user_id") or request.POST.get("responsible_user_id")
    limit_companies = int(request.GET.get("limit_companies", 250) or request.POST.get("limit_companies", 250))
    offset = int(request.GET.get("offset", 0) or request.POST.get("offset", 0))
    
    if not responsible_user_id:
        messages.error(request, "Не указан ответственный пользователь.")
        return redirect("settings_amocrm_migrate")

    try:
        # Запускаем dry-run только для контактов
        # КРИТИЧЕСКИ: не запрашиваем задачи и заметки - это слишком тяжело
        result = migrate_filtered(
            client=client,
            actor=request.user,
            responsible_user_id=int(responsible_user_id),
            sphere_field_id=0,
            sphere_option_id=None,
            sphere_label=None,
            limit_companies=limit_companies,
            offset=offset,
            dry_run=True,  # Всегда dry-run
            import_tasks=False,  # НЕ запрашиваем задачи (слишком тяжело)
            import_notes=False,  # НЕ запрашиваем заметки (слишком тяжело)
            import_contacts=True,  # Включаем импорт контактов для получения данных
            company_fields_meta=None,
            skip_field_filter=True,  # Берем все компании ответственного
        )

        return render(
            request,
            "ui/settings/amocrm_contacts_dry_run.html",
            {
                "result": result,
                "responsible_user_id": responsible_user_id,
                "limit_companies": limit_companies,
                "offset": offset,
            },
        )

    except AmoApiError as e:
        messages.error(request, f"Ошибка AmoCRM API: {e}")
        return redirect("settings_amocrm_migrate")
    except Exception as e:
        import traceback
        messages.error(request, f"Ошибка: {str(e)}")
        print(f"AMOCRM_CONTACTS_DRY_RUN_ERROR: {traceback.format_exc()}")
        return redirect("settings_amocrm_migrate")


@login_required
def settings_amocrm_debug_contacts(request: HttpRequest) -> HttpResponse:
    """
    View для отладки структуры контактов из AmoCRM API.
    Показывает все поля, которые приходят из API.
    """
    if not require_admin(request.user):
        messages.error(request, "Доступ запрещён.")
        return redirect("dashboard")

    import json
    from amocrm.client import AmoClient, AmoApiError

    cfg = AmoApiConfig.load()
    if not cfg.domain:
        messages.error(request, "AmoCRM domain не настроен.")
        return redirect("settings_amocrm_migrate")

    try:
        client = AmoClient(cfg)
    except Exception as e:
        messages.error(request, f"Ошибка создания клиента AmoCRM: {e}")
        return redirect("settings_amocrm_migrate")

    limit = int(request.GET.get("limit", 250))
    responsible_user_id = request.GET.get("responsible_user_id")
    
    # Параметры запроса (максимум 250 - лимит AmoCRM API)
    limit = min(limit, 250)
    params = {
        "with": "custom_fields,notes,leads,customers,catalog_elements",
        "limit": limit,
    }
    
    if responsible_user_id:
        params["filter[responsible_user_id]"] = int(responsible_user_id)

    try:
        # Получаем контакты
        contacts = client.get_all_pages(
            "/api/v4/contacts",
            params=params,
            embedded_key="contacts",
            limit=250,
            max_pages=1,
        )

        if not contacts:
            messages.warning(request, "Контакты не найдены!")
            return redirect("settings_amocrm_migrate")

        # Формируем данные для отображения
        contacts_data = []
        for contact in contacts[:limit]:
            contact_info = {
                "id": contact.get("id"),
                "name": contact.get("name"),
                "first_name": contact.get("first_name"),
                "last_name": contact.get("last_name"),
                "standard_fields": {},
                "custom_fields": contact.get("custom_fields_values") or [],
                "embedded": contact.get("_embedded") or {},
                "all_keys": list(contact.keys()),
                "full_json": json.dumps(contact, ensure_ascii=False, indent=2),
            }
            
            # Стандартные поля
            standard_fields = [
                "id", "name", "first_name", "last_name",
                "responsible_user_id", "group_id", "created_by", "updated_by",
                "created_at", "updated_at", "is_deleted",
                "phone", "email", "company_id", "closest_task_at", "account_id",
            ]
            for field in standard_fields:
                value = contact.get(field)
                if value is not None:
                    contact_info["standard_fields"][field] = value
            
            contacts_data.append(contact_info)

        # Статистика
        stats = {
            "total_contacts": len(contacts_data),
            "field_types": {},
            "field_codes": {},
            "field_names": {},
        }
        
        for contact in contacts_data:
            for cf in contact["custom_fields"]:
                field_type = cf.get("field_type", "unknown")
                field_code = cf.get("field_code", "no_code")
                field_name = cf.get("field_name", "no_name")
                stats["field_types"][field_type] = stats["field_types"].get(field_type, 0) + 1
                stats["field_codes"][field_code] = stats["field_codes"].get(field_code, 0) + 1
                stats["field_names"][field_name] = stats["field_names"].get(field_name, 0) + 1

        return render(
            request,
            "ui/settings/amocrm_debug_contacts.html",
            {
                "contacts": contacts_data,
                "stats": stats,
                "limit": limit,
                "responsible_user_id": responsible_user_id,
            },
        )

    except AmoApiError as e:
        messages.error(request, f"Ошибка AmoCRM API: {e}")
        return redirect("settings_amocrm_migrate")
    except Exception as e:
        import traceback
        messages.error(request, f"Ошибка: {str(e)}")
        print(f"AMOCRM_DEBUG_CONTACTS_ERROR: {traceback.format_exc()}")
        return redirect("settings_amocrm_migrate")


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


@login_required
def settings_mobile_devices(request: HttpRequest) -> HttpResponse:
    """
    Админский список устройств мобильного приложения.
    Только чтение, без действий. Используется для раздела
    «Настройки → Мобильное приложение».
    """
    if not require_admin(request.user):
        messages.error(request, "Доступ запрещён.")
        return redirect("dashboard")

    from phonebridge.models import PhoneDevice

    now = timezone.now()
    active_threshold = now - timedelta(minutes=15)

    qs = (
        PhoneDevice.objects.select_related("user")
        .order_by("-last_seen_at", "-created_at")
    )

    # Фильтры по пользователю и статусу (живое/неживое)
    user_id = (request.GET.get("user") or "").strip()
    status = (request.GET.get("status") or "").strip()  # active|stale|all
    if user_id:
        try:
            qs = qs.filter(user_id=int(user_id))
        except (ValueError, TypeError):
            user_id = ""
    if status == "active":
        qs = qs.filter(last_seen_at__gte=active_threshold)
    elif status == "stale":
        qs = qs.filter(models.Q(last_seen_at__lt=active_threshold) | models.Q(last_seen_at__isnull=True))

    total = qs.count()
    active_count = qs.filter(last_seen_at__gte=active_threshold).count()

    per_page = 50
    paginator = Paginator(qs, per_page)
    page = paginator.get_page(request.GET.get("page"))

    users = get_users_for_lists(request.user)

    return render(
        request,
        "ui/settings/mobile_devices.html",
        {
            "page": page,
            "total": total,
            "active_count": active_count,
            "active_threshold": active_threshold,
            "users": users,
            "filter_user": user_id,
            "filter_status": status or "all",
        },
    )


@login_required
def settings_mobile_overview(request: HttpRequest) -> HttpResponse:
    """
    Overview dashboard для мобильных устройств: карточки с метриками,
    проблемы за сутки, алерты.
    """
    if not require_admin(request.user):
        messages.error(request, "Доступ запрещён.")
        return redirect("dashboard")

    from phonebridge.models import PhoneDevice, PhoneTelemetry, PhoneLogBundle
    from django.db.models import Count, Q

    now = timezone.now()
    active_threshold = now - timedelta(minutes=15)
    day_ago = now - timedelta(days=1)

    # Общая статистика
    total_devices = PhoneDevice.objects.count()
    active_devices = PhoneDevice.objects.filter(last_seen_at__gte=active_threshold).count()
    stale_devices = total_devices - active_devices

    # Проблемы за сутки
    devices_with_errors = PhoneDevice.objects.filter(
        Q(last_error_code__isnull=False) & ~Q(last_error_code=""),
        last_seen_at__gte=day_ago
    ).count()

    # Устройства с частыми 401 (более 3 за последний час)
    hour_ago = now - timedelta(hours=1)
    devices_401_storm = PhoneDevice.objects.filter(
        last_poll_code=401,
        last_poll_at__gte=hour_ago
    ).count()

    # Устройства без сети долго (не видели более 2 часов)
    two_hours_ago = now - timedelta(hours=2)
    devices_no_network = PhoneDevice.objects.filter(
        Q(last_seen_at__lt=two_hours_ago) | Q(last_seen_at__isnull=True),
        last_seen_at__lt=active_threshold
    ).count()

    # Устройства с ошибками refresh (last_error_code содержит "refresh" или "401")
    devices_refresh_fail = PhoneDevice.objects.filter(
        Q(last_error_code__icontains="refresh") | Q(last_error_code__icontains="401"),
        last_seen_at__gte=day_ago
    ).count()

    # Последние алерты (устройства с проблемами)
    alerts = []
    problem_devices = PhoneDevice.objects.filter(
        Q(last_error_code__isnull=False) & ~Q(last_error_code=""),
        last_seen_at__gte=day_ago
    ).select_related("user").order_by("-last_seen_at")[:10]

    for device in problem_devices:
        alert_type = "unknown"
        alert_message = device.last_error_message or device.last_error_code or "Ошибка"
        
        if "401" in (device.last_error_code or ""):
            alert_type = "auth"
            alert_message = "Проблемы с авторизацией (401)"
        elif "refresh" in (device.last_error_code or "").lower():
            alert_type = "refresh"
            alert_message = "Ошибка обновления токена"
        elif device.last_seen_at and device.last_seen_at < two_hours_ago:
            alert_type = "network"
            alert_message = "Нет подключения более 2 часов"
        elif device.last_poll_code == 401:
            alert_type = "auth"
            alert_message = "Требуется повторный вход"
        
        alerts.append({
            "device": device,
            "type": alert_type,
            "message": alert_message,
            "timestamp": device.last_seen_at or device.created_at,
        })

    # Статистика по телеметрии за сутки
    telemetry_stats = PhoneTelemetry.objects.filter(
        ts__gte=day_ago
    ).aggregate(
        total=Count("id"),
        errors=Count("id", filter=Q(http_code__gte=400)),
        avg_latency=Avg("value_ms", filter=Q(type="latency")),
    )

    return render(
        request,
        "ui/settings/mobile_overview.html",
        {
            "total_devices": total_devices,
            "active_devices": active_devices,
            "stale_devices": stale_devices,
            "devices_with_errors": devices_with_errors,
            "devices_401_storm": devices_401_storm,
            "devices_no_network": devices_no_network,
            "devices_refresh_fail": devices_refresh_fail,
            "alerts": alerts,
            "telemetry_stats": telemetry_stats,
        },
    )


@login_required
def settings_mobile_device_detail(request: HttpRequest, pk) -> HttpResponse:
    """
    Детали конкретного устройства мобильного приложения:
    последние heartbeat/telemetry и бандлы логов.
    Только для админов.
    """
    if not require_admin(request.user):
        messages.error(request, "Доступ запрещён.")
        return redirect("dashboard")

    from phonebridge.models import PhoneDevice, PhoneTelemetry, PhoneLogBundle

    device = get_object_or_404(
        PhoneDevice.objects.select_related("user"),
        pk=pk,
    )

    # Ограничиваемся последними записями, чтобы не грузить страницу
    telemetry_qs = (
        PhoneTelemetry.objects.filter(device=device)
        .order_by("-ts")[:200]
    )
    logs_qs = (
        PhoneLogBundle.objects.filter(device=device)
        .order_by("-ts")[:100]
    )

    return render(
        request,
        "ui/settings/mobile_device_detail.html",
        {
            "device": device,
            "telemetry": telemetry_qs,
            "logs": logs_qs,
        },
    )


@login_required
def settings_calls_stats(request: HttpRequest) -> HttpResponse:
    """
    Статистика звонков по менеджерам за день/месяц.
    Показывает количество звонков, статусы (connected, no_answer и т.д.), длительность.
    Доступ:
    - Админ/суперпользователь: видит всех менеджеров
    - Руководитель отдела (SALES_HEAD): видит менеджеров своего филиала
    - Директор филиала (BRANCH_DIRECTOR): видит менеджеров своего филиала
    - Менеджер (MANAGER): видит только свои звонки
    """
    # Разрешаем доступ менеджерам, руководителям и админам
    if request.user.role not in [User.Role.MANAGER, User.Role.SALES_HEAD, User.Role.BRANCH_DIRECTOR, User.Role.ADMIN] and not request.user.is_superuser:
        messages.error(request, "Доступ запрещён.")
        return redirect("dashboard")

    from phonebridge.models import CallRequest
    from django.db.models import Count, Avg, Sum, Q

    now = timezone.now()
    local_now = timezone.localtime(now)
    
    # Период: день или месяц
    period = (request.GET.get("period") or "day").strip()
    if period not in ("day", "month"):
        period = "day"
    
    # Фильтры
    filter_manager_id = request.GET.get("manager", "").strip()
    filter_status = request.GET.get("status", "").strip()  # connected, no_answer, busy, rejected, missed
    
    if period == "month":
        start = local_now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        end = (start + timedelta(days=32)).replace(day=1)
        period_label = _month_label(timezone.localdate(now))
    else:
        start = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
        end = start + timedelta(days=1)
        period_label = timezone.localdate(now).strftime("%d.%m.%Y")
    
    # Определяем, каких менеджеров показывать
    session = getattr(request, "session", {})
    view_as_branch_id = None
    if (request.user.is_superuser or request.user.role == User.Role.ADMIN) and session.get("view_as_enabled") and session.get("view_as_branch_id"):
        try:
            view_as_branch_id = int(session.get("view_as_branch_id"))
        except (TypeError, ValueError):
            view_as_branch_id = None

    if request.user.is_superuser or request.user.role == User.Role.ADMIN:
        base_qs = User.objects.filter(
            is_active=True,
            role__in=[User.Role.MANAGER, User.Role.SALES_HEAD, User.Role.BRANCH_DIRECTOR],
        )
        # Если админ выбрал конкретный филиал в режиме "просмотр как" – ограничиваем менеджеров этим филиалом.
        if view_as_branch_id:
            base_qs = base_qs.filter(branch_id=view_as_branch_id)
        managers_qs = base_qs.select_related("branch").order_by("branch__name", "last_name", "first_name")
    elif request.user.role in [User.Role.SALES_HEAD, User.Role.BRANCH_DIRECTOR]:
        # Руководители видят менеджеров своего филиала
        managers_qs = User.objects.filter(
            is_active=True,
            branch_id=request.user.branch_id,
            role__in=[User.Role.MANAGER, User.Role.SALES_HEAD, User.Role.BRANCH_DIRECTOR]
        ).select_related("branch").order_by("last_name", "first_name")
    else:
        # Менеджер видит только себя
        managers_qs = User.objects.filter(
            is_active=True,
            id=request.user.id
        ).select_related("branch")
    
    # Фильтр по менеджеру
    if filter_manager_id:
        try:
            filter_manager_id_int = int(filter_manager_id)
            managers_qs = managers_qs.filter(id=filter_manager_id_int)
        except (ValueError, TypeError):
            filter_manager_id = ""
    
    manager_ids = list(managers_qs.values_list("id", flat=True))
    
    # Собираем статистику по звонкам
    calls_qs = CallRequest.objects.filter(
        user_id__in=manager_ids,
        call_started_at__gte=start,
        call_started_at__lt=end,
        call_status__isnull=False  # Только звонки с результатом
    ).select_related("user", "user__branch", "company", "contact")
    
    # Фильтр по исходу звонка
    if filter_status:
        status_map = {
            "connected": CallRequest.CallStatus.CONNECTED,
            "no_answer": CallRequest.CallStatus.NO_ANSWER,
            "busy": CallRequest.CallStatus.BUSY,
            "rejected": CallRequest.CallStatus.REJECTED,
            "missed": CallRequest.CallStatus.MISSED,
        }
        if filter_status in status_map:
            calls_qs = calls_qs.filter(call_status=status_map[filter_status])
    
    # Группируем по менеджеру и статусу
    stats_by_manager = {}
    for call in calls_qs:
        manager_id = call.user_id
        if manager_id not in stats_by_manager:
            stats_by_manager[manager_id] = {
                "user": call.user,
                "total": 0,
                "connected": 0,
                "no_answer": 0,
                "busy": 0,
                "rejected": 0,
                "missed": 0,
                "unknown": 0,
                "total_duration": 0,  # Старая логика для обратной совместимости
                "total_duration_connected": 0,  # Новая логика: только для CONNECTED
                "avg_duration": 0,
                "connect_rate_percent": 0.0,
                # Группировки по новым полям (ЭТАП 3: считаем, UI добавим в ЭТАП 4)
                "by_direction": {"outgoing": 0, "incoming": 0, "missed": 0, "unknown": 0},
                "by_resolve_method": {"observer": 0, "retry": 0, "unknown": 0},
                "by_action_source": {"crm_ui": 0, "notification": 0, "history": 0, "unknown": 0},
            }
        
        stats = stats_by_manager[manager_id]
        stats["total"] += 1
        
        if call.call_status == CallRequest.CallStatus.CONNECTED:
            stats["connected"] += 1
            # Длительность считаем только для CONNECTED (для правильного avg_duration)
            if call.call_duration_seconds:
                stats["total_duration_connected"] += call.call_duration_seconds
        elif call.call_status == CallRequest.CallStatus.NO_ANSWER:
            stats["no_answer"] += 1
        elif call.call_status == CallRequest.CallStatus.BUSY:
            stats["busy"] += 1
        elif call.call_status == CallRequest.CallStatus.REJECTED:
            stats["rejected"] += 1
        elif call.call_status == CallRequest.CallStatus.MISSED:
            stats["missed"] += 1
        elif call.call_status == CallRequest.CallStatus.UNKNOWN:
            stats["unknown"] += 1
        
        # Старая логика для обратной совместимости (если UI ожидает total_duration)
        if call.call_duration_seconds:
            stats["total_duration"] += call.call_duration_seconds
        
        # Группировки по новым полям (ЭТАП 3)
        if call.direction:
            direction_key = call.direction
            if direction_key in stats["by_direction"]:
                stats["by_direction"][direction_key] += 1
            else:
                stats["by_direction"]["unknown"] += 1
        
        if call.resolve_method:
            resolve_key = call.resolve_method
            if resolve_key in stats["by_resolve_method"]:
                stats["by_resolve_method"][resolve_key] += 1
            else:
                stats["by_resolve_method"]["unknown"] += 1
        
        if call.action_source:
            action_key = call.action_source
            if action_key in stats["by_action_source"]:
                stats["by_action_source"][action_key] += 1
            else:
                stats["by_action_source"]["unknown"] += 1
    
    # Вычисляем среднюю длительность и дозвоняемость
    for stats in stats_by_manager.values():
        # Новая логика: avg_duration только по CONNECTED
        if stats["connected"] > 0 and stats.get("total_duration_connected", 0) > 0:
            stats["avg_duration"] = stats["total_duration_connected"] // stats["connected"]
        # Старая логика для обратной совместимости
        elif stats["total"] > 0:
            stats["avg_duration"] = stats["total_duration"] // stats["total"]
        else:
            stats["avg_duration"] = 0
        
        # Дозвоняемость % = connected / total (где total = все с call_status != null)
        if stats["total"] > 0:
            connect_rate = (stats["connected"] / stats["total"]) * 100
            stats["connect_rate_percent"] = round(connect_rate, 1)
        else:
            stats["connect_rate_percent"] = 0.0
    
    # Формируем список для шаблона
    stats_list = []
    for manager in managers_qs:
        stats = stats_by_manager.get(manager.id, {
            "user": manager,
            "total": 0,
            "connected": 0,
            "no_answer": 0,
            "busy": 0,
            "rejected": 0,
            "missed": 0,
            "unknown": 0,
            "total_duration": 0,
            "total_duration_connected": 0,
            "avg_duration": 0,
            "connect_rate_percent": 0.0,
            "by_direction": {"outgoing": 0, "incoming": 0, "missed": 0, "unknown": 0},
            "by_resolve_method": {"observer": 0, "retry": 0, "unknown": 0},
            "by_action_source": {"crm_ui": 0, "notification": 0, "history": 0, "unknown": 0},
        })
        stats_list.append(stats)
    
    # Общая статистика
    total_calls = sum(s["total"] for s in stats_list)
    total_connected = sum(s["connected"] for s in stats_list)
    total_no_answer = sum(s["no_answer"] for s in stats_list)
    total_busy = sum(s["busy"] for s in stats_list)
    total_rejected = sum(s["rejected"] for s in stats_list)
    total_missed = sum(s["missed"] for s in stats_list)
    total_unknown = sum(s.get("unknown", 0) for s in stats_list)
    total_duration = sum(s["total_duration"] for s in stats_list)
    total_duration_connected = sum(s.get("total_duration_connected", 0) for s in stats_list)
    # Новая логика: avg_duration только по CONNECTED
    avg_duration_all = total_duration_connected // total_connected if total_connected > 0 else (total_duration // total_calls if total_calls > 0 else 0)
    # Дозвоняемость %
    connect_rate_all = round((total_connected / total_calls * 100), 1) if total_calls > 0 else 0.0
    
    # ЭТАП 4: Вычисляем общие суммы для распределений (для шаблона)
    total_by_direction = {"outgoing": 0, "incoming": 0, "missed": 0, "unknown": 0}
    total_by_action_source = {"crm_ui": 0, "notification": 0, "history": 0, "unknown": 0}
    total_by_resolve_method = {"observer": 0, "retry": 0, "unknown": 0}
    
    for stat in stats_list:
        if "by_direction" in stat:
            total_by_direction["outgoing"] += stat["by_direction"].get("outgoing", 0)
            total_by_direction["incoming"] += stat["by_direction"].get("incoming", 0)
            total_by_direction["missed"] += stat["by_direction"].get("missed", 0)
            total_by_direction["unknown"] += stat["by_direction"].get("unknown", 0)
        if "by_action_source" in stat:
            total_by_action_source["crm_ui"] += stat["by_action_source"].get("crm_ui", 0)
            total_by_action_source["notification"] += stat["by_action_source"].get("notification", 0)
            total_by_action_source["history"] += stat["by_action_source"].get("history", 0)
            total_by_action_source["unknown"] += stat["by_action_source"].get("unknown", 0)
        if "by_resolve_method" in stat:
            total_by_resolve_method["observer"] += stat["by_resolve_method"].get("observer", 0)
            total_by_resolve_method["retry"] += stat["by_resolve_method"].get("retry", 0)
            total_by_resolve_method["unknown"] += stat["by_resolve_method"].get("unknown", 0)
    
    return render(
        request,
        "ui/settings/calls_stats.html",
        {
            "period": period,
            "period_label": period_label,
            "start": start,
            "end": end,
            "stats_list": stats_list,
            "total_calls": total_calls,
            "total_connected": total_connected,
            "total_no_answer": total_no_answer,
            "total_busy": total_busy,
            "total_rejected": total_rejected,
            "total_missed": total_missed,
            "total_unknown": total_unknown,
            "total_duration": total_duration,
            "connect_rate_all": connect_rate_all,
            "avg_duration_all": avg_duration_all,
            "total_by_direction": total_by_direction,
            "total_by_action_source": total_by_action_source,
            "total_by_resolve_method": total_by_resolve_method,
            "managers": managers_qs,
            "filter_manager": filter_manager_id,
            "filter_status": filter_status,
        },
    )


@login_required
def settings_calls_manager_detail(request: HttpRequest, user_id: int) -> HttpResponse:
    """
    Детальный список звонков конкретного менеджера за период (drill-down из статистики).
    """
    # Разрешаем доступ менеджерам, руководителям и админам
    if request.user.role not in [User.Role.MANAGER, User.Role.SALES_HEAD, User.Role.BRANCH_DIRECTOR, User.Role.ADMIN] and not request.user.is_superuser:
        messages.error(request, "Доступ запрещён.")
        return redirect("dashboard")

    from phonebridge.models import CallRequest

    manager = get_object_or_404(User.objects.select_related("branch"), id=user_id, is_active=True)
    
    # Проверка доступа:
    # - Админ/суперпользователь: видит всех
    # - Руководители: видят менеджеров своего филиала
    # - Менеджер: видит только свои звонки
    if request.user.is_superuser or request.user.role == User.Role.ADMIN:
        pass  # Админ видит всех
    elif request.user.role == User.Role.MANAGER:
        if request.user.id != manager.id:
            messages.error(request, "Вы можете просматривать только свои звонки.")
            return redirect("settings_calls_stats")
    else:  # SALES_HEAD или BRANCH_DIRECTOR
        if not request.user.branch_id or request.user.branch_id != manager.branch_id:
            messages.error(request, "Нет доступа к звонкам менеджера из другого филиала.")
            return redirect("settings_calls_stats")
    
    now = timezone.now()
    local_now = timezone.localtime(now)
    
    # Период: день или месяц
    period = (request.GET.get("period") or "day").strip()
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
    
    # Получаем звонки менеджера
    calls_qs = CallRequest.objects.filter(
        user=manager,
        call_started_at__gte=start,
        call_started_at__lt=end,
        call_status__isnull=False
    ).select_related("company", "contact").order_by("-call_started_at")
    
    # Фильтр по исходу звонка
    filter_status = request.GET.get("status", "").strip()
    if filter_status:
        status_map = {
            "connected": CallRequest.CallStatus.CONNECTED,
            "no_answer": CallRequest.CallStatus.NO_ANSWER,
            "busy": CallRequest.CallStatus.BUSY,
            "rejected": CallRequest.CallStatus.REJECTED,
            "missed": CallRequest.CallStatus.MISSED,
            "unknown": CallRequest.CallStatus.UNKNOWN,
        }
        if filter_status in status_map:
            calls_qs = calls_qs.filter(call_status=status_map[filter_status])
    
    per_page = 50
    paginator = Paginator(calls_qs, per_page)
    page = paginator.get_page(request.GET.get("page"))
    
    # Статистика для этого менеджера
    stats = {
        "total": calls_qs.count(),
        "connected": calls_qs.filter(call_status=CallRequest.CallStatus.CONNECTED).count(),
        "no_answer": calls_qs.filter(call_status=CallRequest.CallStatus.NO_ANSWER).count(),
        "busy": calls_qs.filter(call_status=CallRequest.CallStatus.BUSY).count(),
        "rejected": calls_qs.filter(call_status=CallRequest.CallStatus.REJECTED).count(),
        "missed": calls_qs.filter(call_status=CallRequest.CallStatus.MISSED).count(),
    }
    
    return render(
        request,
        "ui/settings/calls_manager_detail.html",
        {
            "manager": manager,
            "period": period,
            "period_label": period_label,
            "page": page,
            "stats": stats,
            "filter_status": filter_status,
        },
    )


@login_required
@policy_required(resource_type="page", resource="ui:mobile_app")
def mobile_app_page(request: HttpRequest) -> HttpResponse:
    """
    Страница мобильного приложения: скачивание APK и QR-вход.
    Доступна всем авторизованным пользователям.
    """
    from accounts.security import get_client_ip
    
    # Получаем последнюю production версию
    latest_build = MobileAppBuild.objects.filter(env="production", is_active=True).order_by("-uploaded_at").first()
    
    # Получаем список всех версий (последние 10)
    builds = MobileAppBuild.objects.filter(env="production", is_active=True).order_by("-uploaded_at")[:10]
    
    return render(
        request,
        "ui/mobile_app.html",
        {
            "latest_build": latest_build,
            "builds": builds,
        },
    )


@login_required
@policy_required(resource_type="action", resource="ui:mobile_app:download")
def mobile_app_download(request: HttpRequest, build_id) -> HttpResponse:
    """
    Скачивание APK файла. Только для авторизованных пользователей.
    """
    from accounts.security import get_client_ip
    
    build = get_object_or_404(MobileAppBuild, id=build_id, env="production", is_active=True)
    
    if not build.file:
        raise Http404("Файл не найден")
    
    # Логируем скачивание
    try:
        log_event(
            actor=request.user,
            verb=ActivityEvent.Verb.VIEW,
            entity_type="mobile_app",
            entity_id=str(build.id),
            message=f"Скачана версия {build.version_name} ({build.version_code})",
            meta={
                "version_name": build.version_name,
                "version_code": build.version_code,
                "ip": get_client_ip(request),
            },
        )
    except Exception as e:
        logger.warning(
            f"Ошибка при логировании скачивания мобильного приложения: {e}",
            exc_info=True,
            extra={"user_id": request.user.id if request.user.is_authenticated else None},
        )
        # Не критично, если логирование не удалось, но фиксируем для отладки
    
    # Отдаем файл с правильным Content-Disposition
    response = FileResponse(build.file.open("rb"), content_type="application/vnd.android.package-archive")
    response["Content-Disposition"] = f'attachment; filename="crmprofi-{build.version_name}-{build.version_code}.apk"'
    return response


@login_required
@policy_required(resource_type="action", resource="ui:mobile_app:qr")
def mobile_app_qr_image(request: HttpRequest) -> HttpResponse:
    """
    Генерация QR-кода для входа в мобильное приложение.
    Токен передается через query параметр ?token=...
    Android приложение сканирует просто токен (строку), а не URL.
    """
    import qrcode
    import io
    
    token = request.GET.get("token", "").strip()
    if not token:
        raise Http404("Токен не указан")
    
    # Проверяем, что токен существует и принадлежит текущему пользователю
    try:
        qr_token = MobileAppQrToken.objects.get(user=request.user, token=token)
    except MobileAppQrToken.DoesNotExist:
        raise Http404("Токен не найден")
    
    # Android приложение ожидает просто токен (строку), а не URL
    # QR-код содержит только токен, который приложение отправит на /api/phone/qr/exchange/
    qr_data = token
    
    # Генерируем QR-код
    qr = qrcode.QRCode(version=1, box_size=10, border=4)
    qr.add_data(qr_data)
    qr.make(fit=True)
    
    img = qr.make_image(fill_color="black", back_color="white")
    
    # Сохраняем в BytesIO
    buffer = io.BytesIO()
    img.save(buffer, format="PNG")
    buffer.seek(0)
    
    response = HttpResponse(buffer.read(), content_type="image/png")
    response["Cache-Control"] = "no-cache, no-store, must-revalidate"
    return response
