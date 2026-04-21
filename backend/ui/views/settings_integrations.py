from __future__ import annotations

import logging

from ui.views._base import (
    ActivityEvent,
    Avg,
    CompanyListColumnsForm,
    Count,
    HttpRequest,
    HttpResponse,
    ImportCompaniesForm,
    ImportTasksIcsForm,
    JsonResponse,
    Max,
    Paginator,
    Q,
    UiGlobalConfig,
    User,
    _month_label,
    cache,
    datetime,
    get_object_or_404,
    get_users_for_lists,
    json,
    login_required,
    messages,
    models,
    os,
    redirect,
    render,
    require_admin,
    timedelta,
    timezone,
    uuid,
)

logger = logging.getLogger(__name__)

# amoCRM integration removed 2026-04-21 (subscription expired, dead code).
# All settings_amocrm* views + amocrm.client/migrate imports deleted.
# See docs/decisions/2026-04-21-remove-amocrm.md.


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
                    messages.success(
                        request,
                        f"Импорт выполнен: добавлено {result.created_companies}, обновлено {result.updated_companies}.",
                    )
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


# amoCRM views removed 2026-04-21 (dead code — subscription expired).
# Removed: settings_amocrm, settings_amocrm_callback, settings_amocrm_disconnect,
# settings_amocrm_migrate, settings_amocrm_migrate_progress,
# settings_amocrm_contacts_dry_run, settings_amocrm_debug_contacts.
# See docs/decisions/2026-04-21-remove-amocrm.md.


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

    qs = PhoneDevice.objects.select_related("user").order_by("-last_seen_at", "-created_at")

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
        qs = qs.filter(
            models.Q(last_seen_at__lt=active_threshold) | models.Q(last_seen_at__isnull=True)
        )

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

    from django.db.models import Count, Q

    from phonebridge.models import PhoneDevice, PhoneLogBundle, PhoneTelemetry

    now = timezone.now()
    active_threshold = now - timedelta(minutes=15)
    day_ago = now - timedelta(days=1)

    # Общая статистика
    total_devices = PhoneDevice.objects.count()
    active_devices = PhoneDevice.objects.filter(last_seen_at__gte=active_threshold).count()
    stale_devices = total_devices - active_devices

    # Проблемы за сутки
    devices_with_errors = PhoneDevice.objects.filter(
        Q(last_error_code__isnull=False) & ~Q(last_error_code=""), last_seen_at__gte=day_ago
    ).count()

    # Устройства с частыми 401 (более 3 за последний час)
    hour_ago = now - timedelta(hours=1)
    devices_401_storm = PhoneDevice.objects.filter(
        last_poll_code=401, last_poll_at__gte=hour_ago
    ).count()

    # Устройства без сети долго (не видели более 2 часов)
    two_hours_ago = now - timedelta(hours=2)
    devices_no_network = PhoneDevice.objects.filter(
        Q(last_seen_at__lt=two_hours_ago) | Q(last_seen_at__isnull=True),
        last_seen_at__lt=active_threshold,
    ).count()

    # Устройства с ошибками refresh (last_error_code содержит "refresh" или "401")
    devices_refresh_fail = PhoneDevice.objects.filter(
        Q(last_error_code__icontains="refresh") | Q(last_error_code__icontains="401"),
        last_seen_at__gte=day_ago,
    ).count()

    # Последние алерты (устройства с проблемами)
    alerts = []
    problem_devices = (
        PhoneDevice.objects.filter(
            Q(last_error_code__isnull=False) & ~Q(last_error_code=""), last_seen_at__gte=day_ago
        )
        .select_related("user")
        .order_by("-last_seen_at")[:10]
    )

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

        alerts.append(
            {
                "device": device,
                "type": alert_type,
                "message": alert_message,
                "timestamp": device.last_seen_at or device.created_at,
            }
        )

    # Статистика по телеметрии за сутки
    telemetry_stats = PhoneTelemetry.objects.filter(ts__gte=day_ago).aggregate(
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

    from phonebridge.models import PhoneDevice, PhoneLogBundle, PhoneTelemetry

    device = get_object_or_404(
        PhoneDevice.objects.select_related("user"),
        pk=pk,
    )

    # Ограничиваемся последними записями, чтобы не грузить страницу
    telemetry_qs = PhoneTelemetry.objects.filter(device=device).order_by("-ts")[:200]
    logs_qs = PhoneLogBundle.objects.filter(device=device).order_by("-ts")[:100]

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
    if (
        request.user.role
        not in [User.Role.MANAGER, User.Role.SALES_HEAD, User.Role.BRANCH_DIRECTOR, User.Role.ADMIN]
        and not request.user.is_superuser
    ):
        messages.error(request, "Доступ запрещён.")
        return redirect("dashboard")

    from django.db.models import Avg, Count, Q, Sum

    from phonebridge.models import CallRequest

    now = timezone.now()
    local_now = timezone.localtime(now)

    # Период: день или месяц
    period = (request.GET.get("period") or "day").strip()
    if period not in ("day", "month"):
        period = "day"

    # Фильтры
    filter_manager_id = request.GET.get("manager", "").strip()
    filter_status = request.GET.get(
        "status", ""
    ).strip()  # connected, no_answer, busy, rejected, missed

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
    if (
        (request.user.is_superuser or request.user.role == User.Role.ADMIN)
        and session.get("view_as_enabled")
        and session.get("view_as_branch_id")
    ):
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
        managers_qs = base_qs.select_related("branch").order_by(
            "branch__name", "last_name", "first_name"
        )
    elif request.user.role in [User.Role.SALES_HEAD, User.Role.BRANCH_DIRECTOR]:
        # Руководители видят менеджеров своего филиала
        managers_qs = (
            User.objects.filter(
                is_active=True,
                branch_id=request.user.branch_id,
                role__in=[User.Role.MANAGER, User.Role.SALES_HEAD, User.Role.BRANCH_DIRECTOR],
            )
            .select_related("branch")
            .order_by("last_name", "first_name")
        )
    else:
        # Менеджер видит только себя
        managers_qs = User.objects.filter(is_active=True, id=request.user.id).select_related(
            "branch"
        )

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
        call_status__isnull=False,  # Только звонки с результатом
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
        stats = stats_by_manager.get(
            manager.id,
            {
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
            },
        )
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
    avg_duration_all = (
        total_duration_connected // total_connected
        if total_connected > 0
        else (total_duration // total_calls if total_calls > 0 else 0)
    )
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
            total_by_action_source["notification"] += stat["by_action_source"].get(
                "notification", 0
            )
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
    if (
        request.user.role
        not in [User.Role.MANAGER, User.Role.SALES_HEAD, User.Role.BRANCH_DIRECTOR, User.Role.ADMIN]
        and not request.user.is_superuser
    ):
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
    calls_qs = (
        CallRequest.objects.filter(
            user=manager,
            call_started_at__gte=start,
            call_started_at__lt=end,
            call_status__isnull=False,
        )
        .select_related("company", "contact")
        .order_by("-call_started_at")
    )

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
