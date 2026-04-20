from __future__ import annotations

import logging

from django.utils.http import url_has_allowed_host_and_scheme

from audit.service import log_event
from phonebridge.models import CallRequest
from ui.views._base import (
    ActivityEvent,
    Branch,
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
    login_required,
    messages,
    policy_required,
    redirect,
    render,
    timedelta,
    timezone,
)

logger = logging.getLogger(__name__)


def _safe_redirect_url(request, url, fallback="/"):
    if url and url_has_allowed_host_and_scheme(
        url, allowed_hosts={request.get_host()}, require_https=request.is_secure()
    ):
        return url
    return fallback


def _log_view_as_event(
    actor: User,
    action: str,
    *,
    target_user: User | None = None,
    role: str = "",
    branch_id: int | None = None,
    ip: str = "",
) -> None:
    """Пишет audit-событие о включении/изменении/сбросе режима «просмотр как».

    Critical для compliance: без лога невозможно ответить на вопрос
    «кто и когда смотрел данные менеджера X?» при расследовании инцидентов.
    """
    try:
        if target_user is not None:
            target_name = target_user.get_full_name() or target_user.username
            message = f"View-as включён: {actor.username} → {target_name} (id={target_user.id})"
        elif role or branch_id:
            parts = []
            if role:
                parts.append(f"role={role}")
            if branch_id:
                parts.append(f"branch_id={branch_id}")
            message = f"View-as фильтр: {actor.username} → {', '.join(parts)}"
        else:
            message = f"View-as {action}: {actor.username}"

        log_event(
            actor=actor,
            verb=ActivityEvent.Verb.UPDATE,
            entity_type="session_impersonation",
            entity_id=str(actor.id),
            message=message[:255],
            meta={
                "action": action,
                "target_user_id": getattr(target_user, "id", None),
                "target_username": getattr(target_user, "username", None),
                "role": role or None,
                "branch_id": branch_id,
                "ip": ip,
            },
        )
    except Exception:
        # Аудит не должен ронять основную функциональность,
        # но обязан оставлять след при сбое.
        logger.exception(
            "Failed to write view-as audit event for actor=%s", getattr(actor, "id", None)
        )


def _client_ip(request: HttpRequest) -> str:
    """Безопасное извлечение IP для audit-meta (без модификации логики security)."""
    try:
        from accounts.security import get_client_ip

        return get_client_ip(request) or ""
    except Exception:
        return request.META.get("REMOTE_ADDR", "") or ""


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
    view_branch_id = (
        request.POST.get("view_as_branch_id") or request.POST.get("view_branch_id") or ""
    ).strip()

    ip = _client_ip(request)

    # Приоритет: если выбран конкретный пользователь, используем его
    # и сбрасываем роль/филиал (они берутся из пользователя)
    if view_user_id:
        try:
            user_id = int(view_user_id)
            view_as_user = User.objects.filter(id=user_id, is_active=True).first()
            # L1 security: запрещаем имперсонировать суперпользователя.
            # ADMIN ≠ superuser, это важное различие для compliance.
            if view_as_user and view_as_user.is_superuser and not user.is_superuser:
                messages.error(request, "Нельзя включить просмотр как суперпользователь.")
                _log_view_as_event(user, "denied_superuser_target", target_user=view_as_user, ip=ip)
                return redirect(
                    _safe_redirect_url(
                        request, request.POST.get("next") or request.META.get("HTTP_REFERER")
                    )
                )
            if view_as_user:
                request.session["view_as_user_id"] = user_id
                # Автоматически устанавливаем роль и филиал из выбранного пользователя
                request.session["view_as_role"] = view_as_user.role
                if view_as_user.branch_id:
                    request.session["view_as_branch_id"] = view_as_user.branch_id
                else:
                    request.session.pop("view_as_branch_id", None)
                messages.success(
                    request,
                    f"Режим просмотра: от лица пользователя {view_as_user.get_full_name() or view_as_user.username}",
                )
                # AUDIT: имперсонация — обязательна для аудит-трейла
                _log_view_as_event(user, "set_user", target_user=view_as_user, ip=ip)
            else:
                request.session.pop("view_as_user_id", None)
                _log_view_as_event(user, "set_user_not_found", ip=ip)
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
        resolved_bid: int | None = None
        if view_branch_id:
            try:
                bid = int(view_branch_id)
                if Branch.objects.filter(id=bid).exists():
                    request.session["view_as_branch_id"] = bid
                    resolved_bid = bid
                else:
                    request.session.pop("view_as_branch_id", None)
            except (TypeError, ValueError):
                request.session.pop("view_as_branch_id", None)
        else:
            request.session.pop("view_as_branch_id", None)

        # AUDIT: смена role/branch фильтра тоже логируется
        if view_role or resolved_bid:
            _log_view_as_event(user, "set_filter", role=view_role, branch_id=resolved_bid, ip=ip)

    next_url = _safe_redirect_url(
        request, request.POST.get("next") or request.META.get("HTTP_REFERER")
    )
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

    had_state = bool(
        request.session.get("view_as_user_id")
        or request.session.get("view_as_role")
        or request.session.get("view_as_branch_id")
    )

    request.session.pop("view_as_user_id", None)
    request.session.pop("view_as_role", None)
    request.session.pop("view_as_branch_id", None)

    # AUDIT: сброс view-as логируем только если было что сбрасывать (снизить шум)
    if had_state:
        _log_view_as_event(user, "reset", ip=_client_ip(request))

    return redirect(_safe_redirect_url(request, request.META.get("HTTP_REFERER")))


# ---------------------------------------------------------------------------
# Константы дашборда — единственный источник правды для подстраиваемых лимитов
# ---------------------------------------------------------------------------

DASHBOARD_PREVIEW_LIMIT = 3  # задач на карточку «Сегодня/Просрочено/…»
DASHBOARD_WEEK_PREVIEW_LIMIT = 3  # задач в карточке «Ближайшие 7 дней» (унифицировано с остальными)
DASHBOARD_STALE_COMPANIES_LIMIT = 10  # компаний без задач
DASHBOARD_DELETION_REQUESTS_LIMIT = 10
TASK_TYPE_CACHE_KEY = "task_types_by_name"
TASK_TYPE_CACHE_TTL = 300  # 5 минут

# Пороги часов для приветствия (локальное время пользователя)
_GREETING_MORNING_START = 5
_GREETING_DAY_START = 12
_GREETING_EVENING_START = 17
_GREETING_NIGHT_START = 23


def _dashboard_time_ranges(local_now: datetime) -> dict:
    """Вычисляет временные рамки для категоризации задач на дашборде.

    Возвращает словарь с today_start, tomorrow_start, week_range_*,
    week_start, week_end. Важно: границы считаются по локальной TZ,
    чтобы задачи с due_at = 23:59 UTC не проваливались в «завтра»
    у пользователя в Екатеринбурге (UTC+5).
    """
    today_date = local_now.date()
    today_start = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
    tomorrow_start = today_start + timedelta(days=1)
    # "На неделю" = 7 дней, начиная с завтра (сегодня исключаем).
    return {
        "today_date": today_date,
        "today_start": today_start,
        "tomorrow_start": tomorrow_start,
        "week_range_start": today_date + timedelta(days=1),
        "week_range_end": today_date + timedelta(days=7),
        "week_start": tomorrow_start,
        "week_end": tomorrow_start + timedelta(days=7),
    }


def _build_greeting(hour: int) -> str:
    """Выбирает приветствие по часу (локальное время).

    5-11: утро, 12-16: день, 17-22: вечер, остальное: ночь.
    """
    if _GREETING_MORNING_START <= hour < _GREETING_DAY_START:
        return "Доброе утро"
    if _GREETING_DAY_START <= hour < _GREETING_EVENING_START:
        return "Добрый день"
    if _GREETING_EVENING_START <= hour < _GREETING_NIGHT_START:
        return "Добрый вечер"
    return "Доброй ночи"


def _fetch_active_tasks(user: User):
    """Queryset активных задач пользователя с нужными select_related/.only().

    Один запрос — основа оптимизации. Категоризация (сегодня/просрочено/
    неделя/новые) выполняется в Python, а не 4 отдельными SQL.
    """
    return (
        Task.objects.filter(assigned_to=user)
        .exclude(status__in=[Task.Status.DONE, Task.Status.CANCELLED])
        .select_related("company", "created_by", "assigned_to", "type")
        .only(
            "id",
            "title",
            "status",
            "due_at",
            "created_at",
            "description",
            "type_id",
            "is_urgent",
            "assigned_to__id",
            "assigned_to__first_name",
            "assigned_to__last_name",
            "company__id",
            "company__name",
            "company__address",
            "company__work_timezone",
            "created_by__id",
            "created_by__first_name",
            "created_by__last_name",
            "type__id",
            "type__name",
            "type__color",
            "type__icon",
        )
    )


def _split_active_tasks(tasks, ranges: dict) -> dict:
    """Делит задачи по 4 бакетам: today/overdue/week/new.

    Каждый бакет возвращает полный список (без лимита) — limit
    применяется ниже, после сортировки, чтобы `*_count` были точными.
    """
    today_start = ranges["today_start"]
    tomorrow_start = ranges["tomorrow_start"]
    week_start = ranges["week_start"]
    week_end = ranges["week_end"]

    today_list: list = []
    overdue_list: list = []
    week_list: list = []
    new_list: list = []

    for task in tasks:
        if task.status == Task.Status.NEW:
            new_list.append(task)

        if task.due_at is None:
            continue

        task_due_local = timezone.localtime(task.due_at)
        if task_due_local < today_start:
            overdue_list.append(task)
        elif today_start <= task_due_local < tomorrow_start:
            today_list.append(task)
        elif week_start <= task_due_local < week_end:
            week_list.append(task)

    # Сортируем — единый sentinel вместо вызова timezone.now() на каждом compare
    _due_sentinel = timezone.now()
    overdue_list.sort(key=lambda t: t.due_at or _due_sentinel)
    today_list.sort(key=lambda t: t.due_at or _due_sentinel)
    week_list.sort(key=lambda t: t.due_at or _due_sentinel)
    _created_sentinel = timezone.now()
    new_list.sort(key=lambda t: t.created_at or _created_sentinel, reverse=True)

    return {
        "today_all": today_list,
        "overdue_all": overdue_list,
        "week_all": week_list,
        "new_all": new_list,
    }


def _get_task_types_by_name() -> dict:
    """Читает справочник TaskType (кэш в Redis на 5 мин).

    Инвалидация — в `tasksapp.signals._invalidate_task_type_widget_cache`
    (post_save/post_delete TaskType).
    """
    from django.core.cache import cache

    cached = cache.get(TASK_TYPE_CACHE_KEY)
    if cached is None:
        cached = {tt.name: tt for tt in TaskType.objects.all()}
        cache.set(TASK_TYPE_CACHE_KEY, cached, TASK_TYPE_CACHE_TTL)
    return cached


def _annotate_task_permissions(task_lists: list, task_types_by_name: dict, user: User) -> None:
    """Проставляет permission-флаги на задачах + резолвит TaskType по названию.

    Ходит по всем 4 слайсам (each [:3]) — ~12 задач, дешёвая операция.
    TaskType резолвится только в памяти: read-only запрос, никаких .save().
    """
    for task_list in task_lists:
        for task in task_list:
            # Резолв TaskType по названию (legacy — старые задачи без type_id)
            if not task.type and task.title and task.title in task_types_by_name:
                task_type = task_types_by_name[task.title]
                task.type = task_type  # type: ignore[assignment]
                task.type_id = task_type.id  # type: ignore[attr-defined]

            task.can_manage_status = _can_manage_task_status_ui(user, task)  # type: ignore[attr-defined]
            task.can_edit_task = _can_edit_task_ui(user, task)  # type: ignore[attr-defined]
            task.can_delete_task = _can_delete_task_ui(user, task)  # type: ignore[attr-defined]


def _get_stale_companies(
    user: User, limit: int = DASHBOARD_STALE_COMPANIES_LIMIT
) -> tuple[list, int]:
    """Компании без активных задач (ответственность user).

    Оптимизация: fetch [:limit+1] + len вместо отдельного COUNT.
    Точное число до `limit`, иначе «больше limit» (показываем как limit+).
    """
    active_company_ids = (
        Task.objects.filter(assigned_to=user, company__isnull=False)
        .exclude(status__in=[Task.Status.DONE, Task.Status.CANCELLED])
        .values_list("company_id", flat=True)
    )
    qs = (
        Company.objects.filter(responsible=user)
        .exclude(id__in=active_company_ids)
        .only("id", "name")
        .order_by("name")
    )
    fetched = list(qs[: limit + 1])
    return fetched[:limit], len(fetched)


def _get_deletion_requests(
    user: User, limit: int = DASHBOARD_DELETION_REQUESTS_LIMIT
) -> tuple[list, int]:
    """Запросы на удаление компаний — видны только РОП/директору своего филиала."""
    if user.role not in (User.Role.SALES_HEAD, User.Role.BRANCH_DIRECTOR) or not user.branch_id:
        return [], 0
    qs = (
        CompanyDeletionRequest.objects.filter(
            status=CompanyDeletionRequest.Status.PENDING,
            requested_by_branch_id=user.branch_id,
        )
        .select_related("requested_by", "company")
        .order_by("-created_at")[: limit + 1]
    )
    fetched = list(qs)
    return fetched[:limit], len(fetched)


def _count_tasks_done_today(user: User, ranges: dict) -> int:
    """Число задач, выполненных пользователем сегодня.

    Используется `updated_at` (не completed_at) — это известное ограничение:
    если DONE-задача была отредактирована, счётчик увеличится. Для отображения
    на дашборде это приемлемо.
    """
    return Task.objects.filter(
        assigned_to=user,
        status=Task.Status.DONE,
        updated_at__gte=ranges["today_start"],
        updated_at__lt=ranges["tomorrow_start"],
    ).count()


def _build_dashboard_context(request: HttpRequest) -> dict:
    """Собирает весь context рабочего стола. Права уже проверены декоратором выше.

    Чистая функция-оркестратор: все тяжёлые детали вынесены в
    `_fetch_active_tasks`, `_split_active_tasks`, `_get_stale_companies`
    и т.д. — так проще тестировать и переиспользовать в API/mobile.
    """
    from companies.services import get_dashboard_contracts

    # Эффективный пользователь для отображения данных (режим «просмотр как»).
    # Права не меняются — их проверяет @policy_required выше.
    user: User = get_effective_user(request)
    now = timezone.now()
    local_now = timezone.localtime(now)
    ranges = _dashboard_time_ranges(local_now)

    # 1. Активные задачи: 1 SQL + Python-категоризация
    active_tasks = _fetch_active_tasks(user)
    buckets = _split_active_tasks(active_tasks, ranges)

    # 2. Счётчики до обрезки
    overdue_count = len(buckets["overdue_all"])
    tasks_today_count = len(buckets["today_all"])
    tasks_week_count = len(buckets["week_all"])
    tasks_new_count = len(buckets["new_all"])

    # 3. Слайсы для отображения
    overdue_list = buckets["overdue_all"][:DASHBOARD_PREVIEW_LIMIT]
    tasks_today_list = buckets["today_all"][:DASHBOARD_PREVIEW_LIMIT]
    tasks_week_list = buckets["week_all"][:DASHBOARD_WEEK_PREVIEW_LIMIT]
    tasks_new_list = buckets["new_all"][:DASHBOARD_PREVIEW_LIMIT]

    # 4. Permissions для модалок + TaskType legacy-резолв
    task_types_by_name = _get_task_types_by_name()
    _annotate_task_permissions(
        [tasks_new_list, tasks_today_list, overdue_list, tasks_week_list],
        task_types_by_name,
        user,
    )

    # 5. Остальные блоки дашборда
    contracts_soon = get_dashboard_contracts(user, today=ranges["today_date"])
    tasks_done_today = _count_tasks_done_today(user, ranges)
    stale_companies, stale_companies_count = _get_stale_companies(user)
    deletion_requests, deletion_requests_count = _get_deletion_requests(user)

    # 6. Приветствие
    greeting = _build_greeting(local_now.hour)

    today_start = ranges["today_start"]
    week_range_start = ranges["week_range_start"]
    week_range_end = ranges["week_range_end"]

    context = {
        "now": now,
        "local_now": local_now,
        "greeting": greeting,
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

    Оптимизации:
    - ETag/304 Not Modified: если изменений нет с прошлого запроса,
      возвращаем пустой 304 ответ (браузер не тратит трафик на JSON).
    - 400 на битый `since` вместо `updated=true`: избегаем
      бесконечного reload-цикла, клиент сбрасывает значение.
    - `since` ограничен 7 днями назад — защита от full-scan по
      устаревшему таймстампу (DoS vector через `since=0`).
    """
    from datetime import timedelta

    user: User = get_effective_user(request)
    since = request.GET.get("since")

    if not since:
        return JsonResponse({"updated": True, "timestamp": int(timezone.now().timestamp() * 1000)})

    try:
        since_dt = datetime.fromtimestamp(int(since) / 1000, tz=timezone.UTC)
    except (ValueError, TypeError):
        # Битый `since` → 400. Клиент сбросит свой lastPollTs.
        from core.request_id import get_request_id

        logger.warning(
            f"Некорректный параметр 'since' в dashboard_poll: {since}",
            extra={"user_id": user.id, "since": since, "request_id": get_request_id()},
        )
        return JsonResponse(
            {"error": "invalid_since", "detail": "since must be milliseconds timestamp"},
            status=400,
        )

    # DoS-защита: if since слишком старый, сдвигаем на 7 дней назад.
    # Старше этого дашборд всё равно не показывает, и full-scan не нужен.
    now = timezone.now()
    min_since = now - timedelta(days=7)
    if since_dt < min_since:
        since_dt = min_since

    has_changes = (
        Task.objects.filter(assigned_to=user, updated_at__gt=since_dt).exists()
        or Company.objects.filter(responsible=user, updated_at__gt=since_dt).exists()
    )

    # ETag/304: если нет изменений — возвращаем 304 с пустым body.
    # Клиент получит 304, не будет парсить JSON, сэкономит network + CPU.
    etag = f'"{int(since_dt.timestamp() * 1000)}-{int(has_changes)}"'
    if_none_match = request.META.get("HTTP_IF_NONE_MATCH", "")
    if not has_changes and if_none_match == etag:
        from django.http import HttpResponseNotModified

        response = HttpResponseNotModified()
        response["ETag"] = etag
        return response

    response = JsonResponse({"updated": has_changes, "timestamp": int(now.timestamp() * 1000)})
    response["ETag"] = etag
    response["Cache-Control"] = "private, no-cache"
    return response


@login_required
@policy_required(resource_type="page", resource="ui:analytics")
def analytics(request: HttpRequest) -> HttpResponse:
    """
    Аналитика по звонкам/отметкам для руководителей.
    Доступ только по реальному пользователю; список и данные — по эффективному (режим просмотра).
    """
    if not (
        request.user.is_superuser
        or request.user.role
        in (
            User.Role.ADMIN,
            User.Role.GROUP_MANAGER,
            User.Role.BRANCH_DIRECTOR,
            User.Role.SALES_HEAD,
        )
    ):
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
        users_qs = get_users_for_lists(user).filter(
            role__in=[User.Role.MANAGER, User.Role.SALES_HEAD, User.Role.BRANCH_DIRECTOR]
        )
    else:
        users_qs = get_users_for_lists(user).filter(
            branch_id=user.branch_id,
            role__in=[User.Role.MANAGER, User.Role.SALES_HEAD, User.Role.BRANCH_DIRECTOR],
        )
    users_list = list(users_qs)
    user_ids = [u.id for u in users_list]

    # Звонки за период (лимит на страницу, чтобы не убить UI)
    # Для консистентности с аналитикой сотрудника считаем только клики "Позвонить с телефона" (note="UI click").
    calls_qs_base = (
        CallRequest.objects.filter(
            created_by_id__in=user_ids, created_at__gte=start, created_at__lt=end, note="UI click"
        )
        .exclude(status=CallRequest.Status.CANCELLED)
        .select_related("company", "contact", "created_by")
    )

    # Полный QS для вычисления холодных звонков (без среза)
    # Учитываем все звонки с is_cold_call=True (включая ручные отметки)
    cold_call_ids = set(calls_qs_base.filter(is_cold_call=True).values_list("id", flat=True))

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
        primary_cold_marked_at__lt=end,
    ).values_list("responsible_id", flat=True)
    for uid in manual_companies:
        if uid in stats:
            stats[uid]["cold_calls"] += 1

    # Ручные отметки на контактах
    manual_contacts = Contact.objects.filter(
        company__responsible_id__in=user_ids, cold_marked_at__gte=start, cold_marked_at__lt=end
    ).values_list("company__responsible_id", flat=True)
    for uid in manual_contacts:
        if uid and uid in stats:
            stats[uid]["cold_calls"] += 1

    # Ручные отметки на телефонах компаний
    manual_company_phones = CompanyPhone.objects.filter(
        company__responsible_id__in=user_ids, cold_marked_at__gte=start, cold_marked_at__lt=end
    ).values_list("company__responsible_id", flat=True)
    for uid in manual_company_phones:
        if uid and uid in stats:
            stats[uid]["cold_calls"] += 1

    # Ручные отметки на телефонах контактов
    manual_contact_phones = ContactPhone.objects.filter(
        contact__company__responsible_id__in=user_ids,
        cold_marked_at__gte=start,
        cold_marked_at__lt=end,
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
    Включает: профиль, безопасность, интерфейс, почта, отсутствие.
    """
    from accounts.models import UserAbsence

    user = request.user
    prefs = UiUserPreference.load_for_user(user)
    today = timezone.localdate()
    # Сортируем: текущие/будущие сверху (по start_date), прошлые внизу (старые последние)
    absences = list(UserAbsence.objects.filter(user=user).order_by("-end_date")[:20])
    return render(
        request,
        "ui/preferences.html",
        {
            "user": user,
            "ui_font_scale_value": prefs.font_scale_float(),
            "company_detail_view_mode": prefs.company_detail_view_mode,
            "tasks_per_page": prefs.tasks_per_page,
            "default_task_tab": prefs.default_task_tab,
            "absences": absences,
            "absence_today": today,
            "absence_type_choices": UserAbsence.Type.choices,
            "is_currently_absent": user.is_currently_absent(today),
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
        return JsonResponse(
            {"success": False, "error": "Некорректный режим просмотра."}, status=400
        )

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
@policy_required(resource_type="action", resource="ui:preferences")
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
@policy_required(resource_type="action", resource="ui:preferences")
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
        # Вкладка «Безопасность» создана в F8 quick-win 2026-04-18 (preferences.html).
        return redirect("/settings/#security")
    else:
        for field_errors in form.errors.values():
            for err in field_errors:
                messages.error(request, err)
        return redirect("/settings/#security")


@login_required
@policy_required(resource_type="action", resource="ui:preferences")
def preferences_absence_create(request: HttpRequest) -> HttpResponse:
    """F5: пользователь сам отмечает своё отсутствие (отпуск/больничный/отгул).

    POST /settings/absence/create/
    Поля: type, start_date (YYYY-MM-DD), end_date (YYYY-MM-DD), note

    Валидации:
    - end_date >= start_date (также на уровне CheckConstraint БД)
    - start_date не в прошлом более чем на 7 дней (защита от мусора)
    - type в списке choices
    """
    from datetime import date
    from datetime import timedelta as _td

    from accounts.models import UserAbsence

    if request.method != "POST":
        return redirect("/settings/#absence")

    user = request.user
    type_value = (request.POST.get("type") or "").strip()
    start_str = (request.POST.get("start_date") or "").strip()
    end_str = (request.POST.get("end_date") or "").strip()
    note = (request.POST.get("note") or "").strip()[:255]

    valid_types = {c[0] for c in UserAbsence.Type.choices}
    if type_value not in valid_types:
        messages.error(request, "Неверный тип отсутствия.")
        return redirect("/settings/#absence")

    try:
        start_date = date.fromisoformat(start_str)
        end_date = date.fromisoformat(end_str)
    except ValueError:
        messages.error(request, "Даты должны быть в формате ГГГГ-ММ-ДД.")
        return redirect("/settings/#absence")

    if end_date < start_date:
        messages.error(request, "Дата окончания раньше даты начала.")
        return redirect("/settings/#absence")

    today = timezone.localdate()
    if start_date < today - _td(days=7):
        messages.error(
            request,
            "Дата начала слишком давняя (больше 7 дней назад). "
            "Свяжитесь с администратором для ретро-записи.",
        )
        return redirect("/settings/#absence")

    UserAbsence.objects.create(
        user=user,
        start_date=start_date,
        end_date=end_date,
        type=type_value,
        note=note,
        created_by=user,
    )
    messages.success(
        request,
        f"Отсутствие сохранено: {start_date:%d.%m.%Y} — {end_date:%d.%m.%Y}. "
        "Новые диалоги в чате не будут назначаться на вас на это время.",
    )
    return redirect("/settings/#absence")


@login_required
@policy_required(resource_type="action", resource="ui:preferences")
def preferences_absence_delete(request: HttpRequest, absence_id: int) -> HttpResponse:
    """F5: удаление собственной записи UserAbsence (или администратор может удалить чужую).

    POST /settings/absence/<id>/delete/
    """
    from accounts.models import UserAbsence

    if request.method != "POST":
        return redirect("/settings/#absence")

    user = request.user
    try:
        absence = UserAbsence.objects.get(id=absence_id)
    except UserAbsence.DoesNotExist:
        messages.error(request, "Запись не найдена.")
        return redirect("/settings/#absence")

    is_owner = absence.user_id == user.id
    is_admin = bool(user.is_superuser or user.role == User.Role.ADMIN)
    if not (is_owner or is_admin):
        messages.error(request, "Нет прав на удаление этой записи.")
        return redirect("/settings/#absence")

    absence.delete()
    messages.success(request, "Запись об отсутствии удалена.")
    return redirect("/settings/#absence")


@login_required
@policy_required(resource_type="action", resource="ui:preferences")
def preferences_mail_signature(request: HttpRequest) -> HttpResponse:
    """
    POST: сохранение HTML-подписи в письмах.
    """
    if request.method != "POST":
        return redirect("preferences")

    user = request.user
    raw_html = request.POST.get("email_signature_html", "").strip()
    if len(raw_html) > 10_000:
        messages.error(request, "Подпись слишком длинная (максимум 10 000 символов).")
        return redirect("/settings/#mail")

    # SECURITY FIX (2026-04-20, audit P0): раньше HTML сохранялся как есть.
    # Это позволяло stored XSS: пользователь вводит <script>...</script> в подписи,
    # админ открывает preview campaign с этой подписью — JS исполняется
    # в srcdoc iframe (same-origin с CRM). Chain: любой user → session-takeover admin.
    # sanitize_email_html удаляет <script>, event handlers, javascript: URLs.
    from mailer.utils import sanitize_email_html

    signature_html = sanitize_email_html(raw_html)

    user.email_signature_html = signature_html
    user.save(update_fields=["email_signature_html"])
    messages.success(request, "Подпись сохранена.")
    return redirect("/settings/#mail")


@login_required
@policy_required(resource_type="action", resource="ui:preferences")
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
        import io

        from django.core.files.base import ContentFile
        from PIL import Image

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
        messages.error(
            request,
            "Не удалось обработать изображение. Убедитесь, что файл — это JPEG, PNG или WEBP.",
        )

    return redirect("/settings/#profile")


@login_required
@policy_required(resource_type="action", resource="ui:preferences")
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
    if not (
        viewer.is_superuser
        or viewer.role
        in (
            User.Role.ADMIN,
            User.Role.GROUP_MANAGER,
            User.Role.BRANCH_DIRECTOR,
            User.Role.SALES_HEAD,
        )
    ):
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
        CallRequest.objects.filter(
            created_by=target, created_at__gte=start, created_at__lt=end, note="UI click"
        )
        .exclude(status=CallRequest.Status.CANCELLED)
        .select_related("company", "contact")
        .order_by("-created_at")
    )

    # Холодные звонки: все звонки с is_cold_call=True (включая ручные отметки)
    # - звонок инициирован через кнопку (note="UI click")
    # - у звонка is_cold_call=True
    cold_calls_qs = calls_qs.filter(is_cold_call=True).order_by("-created_at").distinct()

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
    events_qs = ActivityEvent.objects.filter(
        actor=target, created_at__gte=start, created_at__lt=end
    ).order_by("-created_at")
    events_p = Paginator(events_qs, per_page)

    def _safe_int(v: str, default: int = 1) -> int:
        try:
            return max(int(v), 1)
        except Exception:
            return default

    calls_page_num = _safe_int(request.GET.get("calls_page") or "1")
    cold_page_num = _safe_int(request.GET.get("cold_page") or "1")
    events_page_num = _safe_int(request.GET.get("events_page") or "1")

    calls_page = calls_p.get_page(calls_page_num)
    cold_page = cold_p.get_page(cold_page_num)
    events_page = events_p.get_page(events_page_num)

    # Добавляем форматированную длительность для каждого звонка
    for call in calls_page:
        if call.call_duration_seconds:
            minutes = call.call_duration_seconds // 60
            seconds = call.call_duration_seconds % 60
            call.duration_formatted = (
                f"{minutes} мин. {seconds} сек." if minutes > 0 else f"{seconds} сек."
            )
        else:
            call.duration_formatted = None

    # Также для холодных звонков
    for call in cold_page:
        if call.call_duration_seconds:
            minutes = call.call_duration_seconds // 60
            seconds = call.call_duration_seconds % 60
            call.duration_formatted = (
                f"{minutes} мин. {seconds} сек." if minutes > 0 else f"{seconds} сек."
            )
        else:
            call.duration_formatted = None

    # Формируем qs для пагинации, включая per_page если он отличается от значения по умолчанию
    calls_qs_str = _qs_without_page(request, page_key="calls_page")
    cold_qs = _qs_without_page(request, page_key="cold_page")
    events_qs_str = _qs_without_page(request, page_key="events_page")

    if per_page != 25:
        from urllib.parse import parse_qs, urlencode

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
