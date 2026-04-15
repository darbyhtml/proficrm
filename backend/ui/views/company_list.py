from __future__ import annotations
from ui.views._base import (
    ActivityEvent,
    Branch,
    Company,
    CompanyCreateForm,
    CompanyEmail,
    CompanyHistoryEvent,
    CompanyPhone,
    CompanySphere,
    CompanyStatus,
    Contact,
    ContactEmail,
    ContactPhone,
    ContractType,
    HttpRequest,
    HttpResponse,
    JsonResponse,
    Notification,
    Paginator,
    Q,
    Region,
    StreamingHttpResponse,
    UUID,
    UiGlobalConfig,
    User,
    _apply_company_filters,
    _companies_with_overdue_flag,
    _dup_reasons,
    _editable_company_qs,
    _invalidate_company_count_cache,
    _normalize_email_for_search,
    _normalize_phone_for_search,
    cache,
    can_transfer_companies,
    get_effective_user,
    get_transfer_targets,
    get_users_for_lists,
    get_view_as_user,
    log_event,
    login_required,
    messages,
    models,
    notify,
    policy_required,
    redirect,
    render,
    require_admin,
    timedelta,
    timezone,
    transaction,
    uuid,
    visible_companies_qs,
)
import logging
logger = logging.getLogger(__name__)

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
            "task_filter": f.get("task_filter", ""),
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
        if not new_resp.branch_id:
            return JsonResponse({
                "error": f"У сотрудника «{new_resp}» не указан филиал. Обратитесь к администратору для настройки профиля.",
                "allowed_count": 0,
                "forbidden_count": len(ids),
            }, status=400)
        if new_resp.branch_id != user.branch_id:
            return JsonResponse({
                "error": f"Сотрудник «{new_resp}» из другого филиала. Можно передавать только внутри своего филиала.",
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
        "allowed_count": len(allowed_ids),
        "forbidden_count": len(forbidden_list),
        "forbidden": forbidden_list[:10],
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

    При AJAX (X-Requested-With: XMLHttpRequest) возвращает JSON вместо redirect.
    """
    is_ajax = request.headers.get("X-Requested-With") == "XMLHttpRequest"

    if request.method != "POST":
        if is_ajax:
            return JsonResponse({"success": False, "error": "Method not allowed"}, status=405)
        return redirect("company_list")

    user: User = request.user
    new_resp_id = (request.POST.get("responsible_id") or "").strip()
    apply_mode = (request.POST.get("apply_mode") or "selected").strip().lower()
    if not new_resp_id:
        msg = "Выберите нового ответственного."
        if is_ajax:
            return JsonResponse({"success": False, "error": msg}, status=400)
        messages.error(request, msg)
        return redirect("company_list")

    try:
        new_resp = User.objects.get(id=new_resp_id, is_active=True)
    except User.DoesNotExist:
        if is_ajax:
            return JsonResponse({"success": False, "error": "Ответственный не найден"}, status=404)
        return redirect("company_list")

    # Проверка, что новый ответственный разрешён (не GROUP_MANAGER, не ADMIN)
    if new_resp.role in (User.Role.GROUP_MANAGER, User.Role.ADMIN):
        msg = "Нельзя передать компании управляющему или администратору."
        if is_ajax:
            return JsonResponse({"success": False, "error": msg}, status=400)
        messages.error(request, msg)
        return redirect("company_list")

    if new_resp.role not in (User.Role.MANAGER, User.Role.BRANCH_DIRECTOR, User.Role.SALES_HEAD):
        msg = "Нового ответственного можно выбрать только из: менеджер / директор филиала / РОП."
        if is_ajax:
            return JsonResponse({"success": False, "error": msg}, status=400)
        messages.error(request, msg)
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
            msg = "Нет компаний для переназначения (или нет прав)."
            if is_ajax:
                return JsonResponse({"success": False, "error": msg}, status=400)
            messages.error(request, msg)
            return redirect("company_list")
        if len(ids) >= cap:
            msg = f"Выбрано слишком много компаний (>{cap}). Сузьте фильтр и повторите."
            if is_ajax:
                return JsonResponse({"success": False, "error": msg}, status=400)
            messages.warning(request, msg)
            return redirect("company_list")
    else:
        ids = request.POST.getlist("company_ids") or []
        ids = [i for i in ids if i]
        if not ids:
            msg = "Выберите хотя бы одну компанию (чекбоксы слева)."
            if is_ajax:
                return JsonResponse({"success": False, "error": msg}, status=400)
            messages.error(request, msg)
            return redirect("company_list")

        # ограничиваем до редактируемых
        ids = list(editable_qs.filter(id__in=ids).values_list("id", flat=True))
        if not ids:
            msg = "Нет выбранных компаний, доступных для переназначения."
            if is_ajax:
                return JsonResponse({"success": False, "error": msg}, status=400)
            messages.error(request, msg)
            return redirect("company_list")

    # Проверка прав на передачу каждой компании
    transfer_check = can_transfer_companies(user, ids)

    # Используем только разрешённые компании (запрещённые пропускаем, не блокируем)
    ids = transfer_check["allowed"]
    if not ids:
        forbidden_names = [f["name"] for f in transfer_check["forbidden"][:5]]
        if len(transfer_check["forbidden"]) > 5:
            forbidden_names.append(f"... и ещё {len(transfer_check['forbidden']) - 5}")
        msg = f"Нет компаний, доступных для переназначения: {', '.join(forbidden_names)}"
        if is_ajax:
            return JsonResponse({"success": False, "error": msg}, status=400)
        messages.error(request, msg)
        return redirect("company_list")

    now_ts = timezone.now()
    # Транзакция обеспечивается декоратором @transaction.atomic на функции
    qs_to_update = Company.objects.select_for_update().filter(id__in=ids).select_related("responsible", "branch", "status")
    
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
            "task_filter": request.POST.get("task_filter", ""),
        }
    
    # Собираем данные ДО обновления (нужен старый ответственный для истории)
    _hist_items = list(qs_to_update.values_list("id", "responsible_id"))
    _old_resp_ids = list({rid for _, rid in _hist_items if rid})
    _old_resp_map = {
        str(u.id): u
        for u in User.objects.filter(id__in=_old_resp_ids)
    } if _old_resp_ids else {}

    updated = qs_to_update.update(responsible=new_resp, branch=new_resp.branch, updated_at=now_ts)
    _invalidate_company_count_cache()  # Инвалидируем кэш при массовом переназначении

    # FTS reindex: .update() обходит save()-сигналы, поэтому CompanySearchIndex
    # остаётся рассинхронизированным. Переиндексируем изменённые компании
    # пост-коммитом, чтобы не тормозить основную транзакцию.
    try:
        from django.db import transaction as _tx
        from companies.search_index import rebuild_company_search_index as _reindex
        _ids_snapshot = list(ids)

        def _post_commit_reindex():
            for _cid in _ids_snapshot:
                try:
                    _reindex(_cid)
                except Exception:
                    logger.exception("bulk_transfer: reindex failed for %s", _cid)

        _tx.on_commit(_post_commit_reindex)
    except Exception:
        logger.exception("bulk_transfer: reindex scheduling failed")

    # Создаём события истории для каждой перенесённой компании
    _hist_now = now_ts
    CompanyHistoryEvent.objects.bulk_create([
        CompanyHistoryEvent(
            company_id=comp_id,
            event_type=CompanyHistoryEvent.EventType.ASSIGNED,
            source=CompanyHistoryEvent.Source.LOCAL,
            actor=user,
            actor_name=str(user),
            from_user_id=old_resp_id,
            from_user_name=str(_old_resp_map[str(old_resp_id)]) if old_resp_id and str(old_resp_id) in _old_resp_map else "",
            to_user=new_resp,
            to_user_name=str(new_resp),
            occurred_at=_hist_now,
        )
        for comp_id, old_resp_id in _hist_items
    ], ignore_conflicts=True)
    
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

    if is_ajax:
        return JsonResponse({
            "success": True,
            "updated": updated,
            "new_responsible": str(new_resp),
            "skipped": forbidden_count,
        })
    return redirect(f"/companies/?responsible={new_resp.id}")


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
                "task_filter": (request.GET.get("task_filter") or "").strip(),
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
                "q": f.get("q", ""),
                "responsible": f.get("responsible", ""),
                "status": f.get("status", ""),
                "branch": f.get("branch", ""),
                "sphere": f.get("sphere", ""),
                "contract_type": f.get("contract_type", ""),
                "cold_call": (request.GET.get("cold_call") or "").strip(),
                "overdue": f.get("overdue", ""),
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
            # Защита от случайного дублирования компаний при повторной отправке формы:
            # если за последние несколько секунд уже есть очень похожая компания, не создаём новую.
            recent_cutoff = timezone.now() - timedelta(seconds=10)
            duplicate_qs = Company.objects.filter(
                created_by=user,
                name=company.name,
                inn=company.inn,
                branch=company.branch,
                created_at__gte=recent_cutoff,
            ).order_by("-created_at")
            existing_company = duplicate_qs.first()
            if existing_company:
                messages.info(request, "Похожая компания уже была создана недавно.")
                return redirect("company_detail", company_id=existing_company.id)

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
            CompanyHistoryEvent.objects.create(
                company=company,
                event_type=CompanyHistoryEvent.EventType.CREATED,
                source=CompanyHistoryEvent.Source.LOCAL,
                actor=user,
                actor_name=str(user),
                occurred_at=company.created_at,
            )
            return redirect("company_detail", company_id=company.id)
    else:
        form = CompanyCreateForm(user=user)

    # При POST с ошибкой — восстанавливаем доп. поля из POST, чтобы не терять данные
    if request.method == "POST":
        _restore_emails = [
            CompanyEmail(value=(v or "").strip())
            for k, v in request.POST.items()
            if k.startswith("company_emails_") and (v or "").strip()
        ]
        _restore_phones = [
            CompanyPhone(value=(v or "").strip())
            for k, v in request.POST.items()
            if k.startswith("company_phones_") and (v or "").strip()
        ]
    else:
        _restore_emails = []
        _restore_phones = []

    return render(request, "ui/company_create.html", {
        "form": form,
        "company_emails": _restore_emails,
        "company_phones": _restore_phones,
        "contract_types": ContractType.objects.order_by("order", "name"),
    })


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


