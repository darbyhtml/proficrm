"""Company list filter helpers (большой _apply_company_filters chain).

Extracted из backend/ui/views/_base.py в W1.1 refactor.
Zero behavior change.

Provides:
- Parameter extractors ``_cf_*``.
- Filter sub-functions ``_filter_by_search/selects/tasks/responsible``.
- Orchestrator ``_apply_company_filters``.
- Pagination helper ``_qs_without_page``.
"""

from __future__ import annotations

import logging
from datetime import timedelta

from django.db.models import Exists, OuterRef, Q
from django.http import HttpRequest
from django.utils import timezone

from companies.models import (
    CompanyEmail,
    CompanyPhone,
    Contact,
    ContactEmail,
    ContactPhone,
)
from tasksapp.models import Task
from ui.views.helpers.search import (
    _normalize_email_for_search,
    _normalize_for_search,
    _normalize_phone_for_search,
    _tokenize_search_query,
)

logger = logging.getLogger(__name__)

# Константа filter — совместима с `_base.py` re-export.
RESPONSIBLE_FILTER_NONE = "none"


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
    if normalized_q and len(normalized_q) >= 2:
        normalized_inn_filters = Q(inn__icontains=normalized_q)
        if normalized_q.isdigit() and 8 <= len(normalized_q) <= 12:
            from companies.inn_utils import parse_inns

            query_inns = parse_inns(normalized_q)
            if query_inns:
                for query_inn in query_inns:
                    normalized_inn_filters |= Q(inn__icontains=query_inn)

        normalized_simple_filters = (
            Q(name__icontains=normalized_q)
            | Q(legal_name__icontains=normalized_q)
            | normalized_inn_filters
            | Q(address__icontains=normalized_q)
        )

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
    token_filters = Q()
    if len(tokens) >= 2:
        token_filters = Q()
        for tok in tokens:
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
    normalized_phone = _normalize_phone_for_search(q)
    phone_filters = Q()
    if normalized_phone and normalized_phone != q:
        phone_filters = Q(phone=normalized_phone)
        phone_filters |= Q(phone__icontains=q)

        phone_filters |= Exists(
            CompanyPhone.objects.filter(company_id=OuterRef("pk"), value=normalized_phone)
        )
        phone_filters |= Exists(
            CompanyPhone.objects.filter(company_id=OuterRef("pk"), value__icontains=q)
        )

        phone_filters |= Exists(
            ContactPhone.objects.filter(contact__company_id=OuterRef("pk"), value=normalized_phone)
        )
        phone_filters |= Exists(
            ContactPhone.objects.filter(contact__company_id=OuterRef("pk"), value__icontains=q)
        )
    else:
        phone_filters = Q(phone__icontains=q)
        phone_filters |= Exists(
            CompanyPhone.objects.filter(company_id=OuterRef("pk"), value__icontains=q)
        )
        phone_filters |= Exists(
            ContactPhone.objects.filter(contact__company_id=OuterRef("pk"), value__icontains=q)
        )

    # --- Поиск по email (с нормализацией) ---
    normalized_email = _normalize_email_for_search(q)
    email_filters = Q()
    if normalized_email:
        email_filters = Q(email__iexact=normalized_email)
        email_filters |= Q(email__icontains=q)

        email_filters |= Exists(
            ContactEmail.objects.filter(
                contact__company_id=OuterRef("pk"), value__iexact=normalized_email
            )
        )
        email_filters |= Exists(
            ContactEmail.objects.filter(contact__company_id=OuterRef("pk"), value__icontains=q)
        )

        email_filters |= Exists(
            CompanyEmail.objects.filter(company_id=OuterRef("pk"), value__iexact=normalized_email)
        )
        email_filters |= Exists(
            CompanyEmail.objects.filter(company_id=OuterRef("pk"), value__icontains=q)
        )
    else:
        email_filters = Q(email__icontains=q)
        email_filters |= Exists(
            ContactEmail.objects.filter(contact__company_id=OuterRef("pk"), value__icontains=q)
        )
        email_filters |= Exists(
            CompanyEmail.objects.filter(company_id=OuterRef("pk"), value__icontains=q)
        )

    # --- Поиск по ФИО в контактах ---
    words = tokens or [w.strip().lower() for w in q.split() if w.strip()]
    fio_filters = Q()

    if len(words) > 1:
        contact_q = Contact.objects.filter(company_id=OuterRef("pk"))
        for word in words:
            contact_q = contact_q.filter(
                Q(first_name__icontains=word) | Q(last_name__icontains=word)
            )

        fio_filters = Exists(contact_q)
        fio_filters |= Q(contacts__first_name__icontains=q)
        fio_filters |= Q(contacts__last_name__icontains=q)
    elif len(words) == 1:
        word = words[0]
        contact_q = Contact.objects.filter(company_id=OuterRef("pk")).filter(
            Q(first_name__icontains=word) | Q(last_name__icontains=word)
        )
        fio_filters = Exists(contact_q)
    else:
        contact_q = Contact.objects.filter(company_id=OuterRef("pk")).filter(
            Q(first_name__icontains=q) | Q(last_name__icontains=q)
        )
        fio_filters = Exists(contact_q)

    return qs.filter(
        base_filters | token_filters | phone_filters | email_filters | fio_filters
    ).distinct()


def _filter_by_selects(qs, params: dict):
    """
    Фильтрация по select-полям: статус, подразделение, сфера, тип договора, регион.
    Возвращает кортеж (qs, context_dict) с отфильтрованным QS и данными для шаблона.
    """
    status_ids = _cf_to_int_list(_cf_get_list_param_stripped(params, "status"))
    if status_ids:
        qs = qs.filter(status_id__in=status_ids)
    selected_statuses = [str(i) for i in status_ids]
    status = selected_statuses[0] if selected_statuses else ""

    branch_ids = _cf_to_int_list(_cf_get_list_param_stripped(params, "branch"))
    if branch_ids:
        qs = qs.filter(branch_id__in=branch_ids)
    selected_branches = [str(i) for i in branch_ids]
    branch = selected_branches[0] if selected_branches else ""

    sphere_ids = _cf_to_int_list(_cf_get_list_param_stripped(params, "sphere"))
    if sphere_ids:
        qs = qs.filter(spheres__id__in=sphere_ids)
    selected_spheres = [str(i) for i in sphere_ids]
    sphere = selected_spheres[0] if selected_spheres else ""

    contract_type = _cf_get_str_param(params, "contract_type")
    if contract_type:
        try:
            contract_type_id = int(contract_type)
            qs = qs.filter(contract_type_id=contract_type_id)
        except (ValueError, TypeError):
            pass

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

    region = str(region_ids[0]) if region_ids else ""
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


def _apply_company_filters(*, qs, params: dict, default_responsible_id: int | None = None):
    """
    Единые фильтры компаний для:
    - списка компаний
    - экспорта
    - массового переназначения (apply_mode=filtered)
    """
    q = _cf_get_str_param(params, "q")
    if q:
        qs = _filter_by_search(qs, q)

    qs, selects_ctx = _filter_by_selects(qs, params)

    qs, overdue, task_filter = _filter_by_tasks(qs, params)

    qs, responsible, selected_responsibles, responsible_ids, has_none = _filter_by_responsible(
        qs, params, default_responsible_id
    )

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

    # PERF: .distinct() применяем ТОЛЬКО когда реально нужен (M2M / JOIN-фильтры).
    needs_distinct = bool(
        selects_ctx["sphere_ids"]
        or overdue == "1"
        or bool(task_filter)
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
