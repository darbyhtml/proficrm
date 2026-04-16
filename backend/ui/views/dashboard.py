from __future__ import annotations
from ui.views._base import (
    ActivityEvent,
    Branch,
    CallRequest,
    Company,
    CompanyDeletionRequest,
    CompanyPhone,
    Contact,
    ContactPhone,
    Decimal,
    Http404,
    HttpRequest,
    HttpResponse,
    JsonResponse,
    Paginator,
    Task,
    TaskType,
    UiUserPreference,
    User,
    _can_delete_task_ui,
    _can_edit_task_ui,
    _can_manage_task_status_ui,
    _can_view_cold_call_reports,
    _month_label,
    _qs_without_page,
    datetime,
    get_effective_user,
    get_object_or_404,
    get_users_for_lists,
    json,
    login_required,
    messages,
    policy_required,
    redirect,
    render,
    timedelta,
    timezone,
)
from django.utils.http import url_has_allowed_host_and_scheme
import logging
logger = logging.getLogger(__name__)


def _safe_redirect_url(request, url, fallback="/"):
    if url and url_has_allowed_host_and_scheme(url, allowed_hosts={request.get_host()}, require_https=request.is_secure()):
        return url
    return fallback


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

    next_url = _safe_redirect_url(request, request.POST.get("next") or request.META.get("HTTP_REFERER"))
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

    return redirect(_safe_redirect_url(request, request.META.get("HTTP_REFERER")))


def _build_dashboard_context(request: HttpRequest) -> dict:
    """Собирает весь context рабочего стола. Права уже проверены декоратором выше."""
    # Эффективный пользователь для отображения данных (режим «просмотр как»). Права не меняются.
    user: User = get_effective_user(request)
    now = timezone.now()
    # Важно: при USE_TZ=True timezone.now() в UTC. Для фильтров "сегодня/неделя" считаем границы по локальной TZ.
    local_now = timezone.localtime(now)
    today_date = timezone.localdate(now)
    today_start = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
    tomorrow_start = today_start + timedelta(days=1)
    
    # "Задачи на неделю" = ближайшие 7 дней, начиная с завтра (сегодня исключаем).
    week_range_start = today_date + timedelta(days=1)
    week_range_end = week_range_start + timedelta(days=6)
    week_start = tomorrow_start
    week_end = tomorrow_start + timedelta(days=7)
    
    contract_until_30 = today_date + timedelta(days=30)


    # ОПТИМИЗАЦИЯ: один запрос на активные задачи, затем категоризация в Python
    all_tasks = (
        Task.objects.filter(assigned_to=user)
        .exclude(status__in=[Task.Status.DONE, Task.Status.CANCELLED])
        .select_related("company", "created_by", "assigned_to", "type")
        .only(
            "id", "title", "status", "due_at", "created_at", "description", "type_id",
            "is_urgent",
            "assigned_to__id", "assigned_to__first_name", "assigned_to__last_name",
            "company__id", "company__name", "company__address", "company__work_timezone",
            "created_by__id", "created_by__first_name", "created_by__last_name",
            "type__id", "type__name", "type__color", "type__icon",
        )
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
    task_types_by_name = {tt.name: tt for tt in TaskType.objects.all()}
    
    # Добавляем права доступа к задачам для модального окна
    # Подготавливаем задачи с правами доступа и сопоставляем с TaskType
    for task_list in [tasks_new_list, tasks_today_list, overdue_list, tasks_week_list]:
        for task in task_list:
            # Если у задачи нет типа, но есть title, подбираем TaskType в памяти
            # (read-only: GET-запрос дашборда не должен писать в БД, иначе race +
            # нагрузка на каждый рендер; backfill вынесен в одноразовую миграцию).
            if not task.type and task.title and task.title in task_types_by_name:
                task_type = task_types_by_name[task.title]
                task.type = task_type  # type: ignore[assignment]
                task.type_id = task_type.id  # type: ignore[attr-defined]
            
            task.can_manage_status = _can_manage_task_status_ui(user, task)  # type: ignore[attr-defined]
            task.can_edit_task = _can_edit_task_ui(user, task)  # type: ignore[attr-defined]
            task.can_delete_task = _can_delete_task_ui(user, task)  # type: ignore[attr-defined]

    # Выполнено сегодня (отдельный запрос — активные задачи его не содержат)
    tasks_done_today = Task.objects.filter(
        assigned_to=user,
        status=Task.Status.DONE,
        updated_at__gte=today_start,
        updated_at__lt=tomorrow_start,
    ).count()

    # Компании без активных задач (для виджета «зависшие компании»)
    active_company_ids = (
        Task.objects.filter(assigned_to=user, company__isnull=False)
        .exclude(status__in=[Task.Status.DONE, Task.Status.CANCELLED])
        .values_list("company_id", flat=True)
    )
    stale_companies_qs = (
        Company.objects.filter(responsible=user)
        .exclude(id__in=active_company_ids)
        .only("id", "name")
        .order_by("name")
    )
    stale_companies_count = stale_companies_qs.count()
    stale_companies = list(stale_companies_qs[:5])

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
        "can_view_cold_call_reports": _can_view_cold_call_reports(request.user),
        # Общие количества для кнопок "Посмотреть все"
        "tasks_new_count": tasks_new_count,
        "tasks_today_count": tasks_today_count,
        "overdue_count": overdue_count,
        "tasks_week_count": tasks_week_count,
        # Диапазон дат для "Ближайшие 7 дней"
        "week_range_start": week_range_start,
        "week_range_end": week_range_end,
        # Выполнено сегодня
        "tasks_done_today": tasks_done_today,
        # Компании без активных задач
        "stale_companies": stale_companies,
        "stale_companies_count": stale_companies_count,
        "effective_user_id": user.id,
        # Запросы на удаление
        "deletion_requests": deletion_requests,
        "deletion_requests_count": deletion_requests_count,
    }

    return context


@login_required
@policy_required(resource_type="page", resource="ui:dashboard")
def dashboard(request: HttpRequest) -> HttpResponse:
    """Рабочий стол (Notion-стиль v2)."""
    context = _build_dashboard_context(request)
    return render(request, "ui/dashboard_v2.html", context)


@login_required
@policy_required(resource_type="action", resource="ui:dashboard")
def dashboard_poll(request: HttpRequest) -> JsonResponse:
    """
    Лёгкий AJAX polling: возвращает только {updated: true/false}.
    Клиент делает location.reload() при updated=true.
    """
    user: User = get_effective_user(request)
    since = request.GET.get('since')

    if not since:
        return JsonResponse({"updated": True, "timestamp": int(timezone.now().timestamp() * 1000)})

    try:
        since_dt = datetime.fromtimestamp(int(since) / 1000, tz=timezone.UTC)
    except (ValueError, TypeError):
        from crm.request_id_middleware import get_request_id
        logger.warning(
            f"Некорректный параметр 'since' в dashboard_poll: {since}",
            extra={"user_id": user.id, "since": since, "request_id": get_request_id()},
        )
        return JsonResponse({"updated": True, "timestamp": int(timezone.now().timestamp() * 1000)})

    has_changes = (
        Task.objects.filter(assigned_to=user, updated_at__gt=since_dt).exists()
        or Company.objects.filter(responsible=user, updated_at__gt=since_dt).exists()
    )
    now = timezone.now()
    return JsonResponse({"updated": has_changes, "timestamp": int(now.timestamp() * 1000)})



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
    # Учитываем все звонки с is_cold_call=True (включая ручные отметки)
    cold_call_ids = set(
        calls_qs_base.filter(is_cold_call=True).values_list("id", flat=True)
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
    
    # Добавляем ручные отметки в статистику холодных звонков
    # Ручные отметки на компаниях
    manual_companies = Company.objects.filter(
        responsible_id__in=user_ids,
        primary_cold_marked_at__gte=start,
        primary_cold_marked_at__lt=end
    ).values_list("responsible_id", flat=True)
    for uid in manual_companies:
        if uid in stats:
            stats[uid]["cold_calls"] += 1
    
    # Ручные отметки на контактах
    manual_contacts = Contact.objects.filter(
        company__responsible_id__in=user_ids,
        cold_marked_at__gte=start,
        cold_marked_at__lt=end
    ).values_list("company__responsible_id", flat=True)
    for uid in manual_contacts:
        if uid and uid in stats:
            stats[uid]["cold_calls"] += 1
    
    # Ручные отметки на телефонах компаний
    manual_company_phones = CompanyPhone.objects.filter(
        company__responsible_id__in=user_ids,
        cold_marked_at__gte=start,
        cold_marked_at__lt=end
    ).values_list("company__responsible_id", flat=True)
    for uid in manual_company_phones:
        if uid and uid in stats:
            stats[uid]["cold_calls"] += 1
    
    # Ручные отметки на телефонах контактов
    manual_contact_phones = ContactPhone.objects.filter(
        contact__company__responsible_id__in=user_ids,
        cold_marked_at__gte=start,
        cold_marked_at__lt=end
    ).values_list("contact__company__responsible_id", flat=True)
    for uid in manual_contact_phones:
        if uid and uid in stats:
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
    Единая страница настроек пользователя.
    Включает: профиль, безопасность, интерфейс, почта.
    """
    user = request.user
    prefs = UiUserPreference.load_for_user(user)
    return render(
        request,
        "ui/preferences.html",
        {
            "user": user,
            "ui_font_scale_value": prefs.font_scale_float(),
            "company_detail_view_mode": prefs.company_detail_view_mode,
            "tasks_per_page": prefs.tasks_per_page,
            "default_task_tab": prefs.default_task_tab,
        },
    )


@login_required
@policy_required(resource_type="page", resource="ui:preferences")
def preferences_ui(request: HttpRequest) -> HttpResponse:
    """
    Настройки интерфейса: масштаб шрифта.
    GET → редирект на единую страницу настроек.
    POST → сохраняет масштаб, редиректит обратно.
    """
    user = request.user

    if request.method == "POST":
        scale_raw = (request.POST.get("font_scale") or "").strip().replace(",", ".")
        try:
            scale = float(scale_raw)
        except Exception:
            scale = None

        if scale is None or not (0.85 <= scale <= 1.30):
            messages.error(request, "Некорректный масштаб. Допустимо от 85% до 130%.")
            return redirect("/settings/#interface")

        prefs = UiUserPreference.load_for_user(user)
        prefs.font_scale = Decimal(f"{scale:.3f}")
        prefs.save(update_fields=["font_scale", "updated_at"])
        try:
            request.session["ui_font_scale"] = float(prefs.font_scale_float())
        except Exception:
            pass
        messages.success(request, "Настройки интерфейса сохранены.")
        return redirect("/settings/#interface")

    return redirect("/settings/#interface")


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
@policy_required(resource_type="action", resource="ui:preferences")
def preferences_v2_scale(request: HttpRequest) -> JsonResponse:
    """
    AJAX endpoint для сохранения масштаба v2-интерфейса.
    POST: {"scale": "0.875" | "1.000" | "1.125" | "1.250"}
    Применяется через CSS zoom на .v2 обёртке.
    """
    if request.method != "POST":
        return JsonResponse({"success": False, "error": "Метод не разрешен."}, status=405)

    raw = (request.POST.get("scale") or "").strip().replace(",", ".")
    try:
        scale = float(raw)
    except Exception:
        return JsonResponse({"success": False, "error": "Некорректное значение."}, status=400)

    # Разрешаем только 4 пресета, чтобы не плодить промежуточных значений.
    allowed = {0.875: "0.875", 1.000: "1.000", 1.125: "1.125", 1.250: "1.250"}
    key = next((k for k in allowed if abs(k - scale) < 0.001), None)
    if key is None:
        return JsonResponse(
            {"success": False, "error": "Допустимо только 0.875 / 1.000 / 1.125 / 1.250."},
            status=400,
        )

    prefs = UiUserPreference.load_for_user(request.user)
    prefs.font_scale = Decimal(allowed[key])
    prefs.save(update_fields=["font_scale", "updated_at"])
    try:
        request.session["ui_font_scale"] = prefs.font_scale_float()
    except Exception:
        pass

    return JsonResponse({"success": True, "scale": allowed[key]})


@login_required
@policy_required(resource_type="page", resource="ui:preferences")
def preferences_mail(request: HttpRequest) -> HttpResponse:
    """
    Почтовые настройки — редирект на единую страницу настроек.
    """
    return redirect("/settings/#mail")


@login_required
@policy_required(resource_type="page", resource="ui:preferences")
def preferences_profile(request: HttpRequest) -> HttpResponse:
    """
    AJAX/POST: сохранение профиля пользователя (имя, фамилия).
    """
    if request.method != "POST":
        return redirect("preferences")

    user = request.user
    first_name = (request.POST.get("first_name") or "").strip()[:30]
    last_name = (request.POST.get("last_name") or "").strip()[:150]

    user.first_name = first_name
    user.last_name = last_name
    user.save(update_fields=["first_name", "last_name"])
    messages.success(request, "Профиль обновлён.")
    return redirect("/settings/#profile")


@login_required
@policy_required(resource_type="page", resource="ui:preferences")
def preferences_password(request: HttpRequest) -> HttpResponse:
    """
    POST: смена пароля через Django PasswordChangeForm.
    """
    from django.contrib.auth import update_session_auth_hash
    from django.contrib.auth.forms import PasswordChangeForm

    if request.method != "POST":
        return redirect("preferences")

    form = PasswordChangeForm(request.user, request.POST)
    if form.is_valid():
        user = form.save()
        update_session_auth_hash(request, user)
        messages.success(request, "Пароль успешно изменён.")
        return redirect("/settings/#security")
    else:
        for field_errors in form.errors.values():
            for err in field_errors:
                messages.error(request, err)
        return redirect("/settings/#security")


@login_required
@policy_required(resource_type="page", resource="ui:preferences")
def preferences_mail_signature(request: HttpRequest) -> HttpResponse:
    """
    POST: сохранение HTML-подписи в письмах.
    """
    if request.method != "POST":
        return redirect("preferences")

    user = request.user
    signature_html = request.POST.get("email_signature_html", "").strip()
    if len(signature_html) > 10_000:
        messages.error(request, "Подпись слишком длинная (максимум 10 000 символов).")
        return redirect("/settings/#mail")

    user.email_signature_html = signature_html
    user.save(update_fields=["email_signature_html"])
    messages.success(request, "Подпись сохранена.")
    return redirect("/settings/#mail")


@login_required
@policy_required(resource_type="page", resource="ui:preferences")
def preferences_avatar_upload(request: HttpRequest) -> HttpResponse:
    """
    POST: загрузка фото профиля. Ресайзит до 300×300 через Pillow, сохраняет как JPEG.
    """
    if request.method != "POST":
        return redirect("preferences")

    uploaded = request.FILES.get("avatar")
    if not uploaded:
        messages.error(request, "Файл не выбран.")
        return redirect("/settings/#profile")

    if uploaded.size > 5 * 1024 * 1024:
        messages.error(request, "Файл слишком большой (максимум 5 МБ).")
        return redirect("/settings/#profile")

    try:
        from PIL import Image
        import io
        from django.core.files.base import ContentFile

        img = Image.open(uploaded)
        img.verify()  # Проверяем, что файл — реальное изображение

        # Повторно открываем после verify (он закрывает поток)
        uploaded.seek(0)
        img = Image.open(uploaded)

        # Конвертируем в RGB (убираем alpha-канал, если RGBA/PA)
        if img.mode not in ("RGB", "L"):
            img = img.convert("RGB")

        # Ресайз с сохранением пропорций, вписываем в квадрат 300×300
        img.thumbnail((300, 300), Image.LANCZOS)

        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=88, optimize=True)
        buf.seek(0)

        user = request.user
        filename = f"user_{user.pk}.jpg"

        # Удаляем старый файл, если он есть
        if user.avatar:
            try:
                user.avatar.delete(save=False)
            except Exception:
                pass

        user.avatar.save(filename, ContentFile(buf.read()), save=True)
        messages.success(request, "Фото профиля обновлено.")

    except Exception:
        messages.error(request, "Не удалось обработать изображение. Убедитесь, что файл — это JPEG, PNG или WEBP.")

    return redirect("/settings/#profile")


@login_required
@policy_required(resource_type="page", resource="ui:preferences")
def preferences_avatar_delete(request: HttpRequest) -> HttpResponse:
    """
    POST: удаление фото профиля.
    """
    if request.method != "POST":
        return redirect("preferences")

    user = request.user
    if user.avatar:
        try:
            user.avatar.delete(save=False)
        except Exception:
            pass
        user.avatar = None
        user.save(update_fields=["avatar"])
        messages.success(request, "Фото профиля удалено.")

    return redirect("/settings/#profile")


@login_required
@policy_required(resource_type="page", resource="ui:preferences")
def preferences_table_settings(request: HttpRequest) -> HttpResponse:
    """
    POST: сохранение настроек таблиц (строк на странице, вкладка задач).
    """
    if request.method != "POST":
        return redirect("preferences")

    user = request.user
    prefs = UiUserPreference.load_for_user(user)

    per_page_raw = request.POST.get("tasks_per_page", "")
    try:
        per_page = int(per_page_raw)
        if per_page not in (10, 25, 50, 100):
            raise ValueError
    except (ValueError, TypeError):
        per_page = 25

    default_tab = (request.POST.get("default_task_tab") or "all").strip()
    if default_tab not in ("all", "mine", "overdue", "today"):
        default_tab = "all"

    prefs.tasks_per_page = per_page
    prefs.default_task_tab = default_tab
    prefs.save(update_fields=["tasks_per_page", "default_task_tab", "updated_at"])

    # Обновляем сессию, чтобы task_list подхватил без перезагрузки
    request.session["task_list_per_page"] = per_page

    messages.success(request, "Настройки таблиц сохранены.")
    return redirect("/settings/#interface")


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

    # Холодные звонки: все звонки с is_cold_call=True (включая ручные отметки)
    # - звонок инициирован через кнопку (note="UI click")
    # - у звонка is_cold_call=True
    cold_calls_qs = (
        calls_qs.filter(is_cold_call=True)
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


