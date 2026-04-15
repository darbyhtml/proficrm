from __future__ import annotations
from ui.views._base import (
    ActivityEvent,
    Company,
    CompanyNote,
    F,
    Http404,
    HttpRequest,
    HttpResponse,
    JsonResponse,
    Notification,
    Paginator,
    PermissionDenied,
    Q,
    STRONG_CONFIRM_THRESHOLD,
    Task,
    TaskEditForm,
    TaskEvent,
    TaskForm,
    TaskType,
    UUID,
    User,
    ValidationError,
    _can_delete_task_ui,
    _can_edit_company,
    _can_edit_task_ui,
    _can_manage_task_status_ui,
    _editable_company_qs,
    clean_int_id,
    datetime,
    datetime_time,
    get_effective_user,
    get_object_or_404,
    get_transfer_targets,
    get_users_for_lists,
    log_event,
    login_required,
    messages,
    notify,
    policy_decide,
    policy_required,
    re,
    redirect,
    render,
    require_admin,
    resolve_target_companies,
    timedelta,
    timezone,
    transaction,
    visible_tasks_qs,
)
import logging
logger = logging.getLogger(__name__)

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
        # Без выбранного статуса: галочка "Выполн." — только выполненные; иначе скрываем выполненные.
        if show_done == "1":
            qs = qs.filter(status=Task.Status.DONE)
        else:
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
    # Если включена галочка "Мои", игнорируем assigned_to_param (приоритет у фильтра "мои")
    assigned_to_param = (request.GET.get("assigned_to") or "").strip()
    if assigned_to_param and mine != "1":
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
    elif mine == "1":
        # Если включена галочка "Мои", игнорируем assigned_to_param
        assigned_to_param = ""

    overdue = (request.GET.get("overdue") or "").strip()
    if overdue == "1":
        qs = qs.filter(due_at__lt=now)
        if show_done != "1":
            qs = qs.exclude(status__in=[Task.Status.DONE, Task.Status.CANCELLED])

    today = (request.GET.get("today") or "").strip()
    if today == "1":
        # При show_done=1 фильтруем по дате выполнения, иначе по дедлайну
        today_field = "completed_at" if show_done == "1" else "due_at"
        qs = qs.filter(**{f"{today_field}__gte": today_start, f"{today_field}__lt": tomorrow_start})
        if show_done != "1":
            qs = qs.exclude(status__in=[Task.Status.DONE, Task.Status.CANCELLED])
        # Для выполненных по "Сегодня": показываем только задачи с заполненной датой завершения
        if show_done == "1":
            qs = qs.filter(completed_at__isnull=False)

    # Фильтр по датам (date_from и date_to).
    # При show_done=1 (только выполненные) период — по дате выполнения (completed_at).
    # Иначе период — по дедлайну (due_at).
    date_from = (request.GET.get("date_from") or "").strip()
    date_to = (request.GET.get("date_to") or "").strip()
    date_field = "completed_at" if show_done == "1" else "due_at"
    if date_from:
        try:
            date_from_dt = datetime.strptime(date_from, "%Y-%m-%d")
            date_from_start = timezone.make_aware(date_from_dt.replace(hour=0, minute=0, second=0, microsecond=0))
            qs = qs.filter(**{f"{date_field}__gte": date_from_start})
        except (ValueError, TypeError):
            pass
    if date_to:
        try:
            date_to_dt = datetime.strptime(date_to, "%Y-%m-%d")
            date_to_end = timezone.make_aware(date_to_dt.replace(hour=23, minute=59, second=59, microsecond=999999))
            qs = qs.filter(**{f"{date_field}__lte": date_to_end})
        except (ValueError, TypeError):
            pass
    # Для выполненных по периоду: показываем только задачи с заполненной датой завершения
    if show_done == "1" and (date_from or date_to):
        qs = qs.filter(completed_at__isnull=False)

    # Текстовый поиск по названию/описанию задачи
    search_q = (request.GET.get("q") or "").strip()
    if search_q:
        qs = qs.filter(
            Q(title__icontains=search_q) | Q(description__icontains=search_q)
        )

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

    # Пагинация с выбором per_page — сохраняется в UiUserPreference.tasks_per_page
    from ui.models import UiUserPreference
    _ui_prefs = UiUserPreference.load_for_user(user)
    per_page = int(_ui_prefs.tasks_per_page or 25)
    per_page_param = request.GET.get("per_page", "").strip()
    if per_page_param:
        try:
            _pp = int(per_page_param)
            if _pp in (25, 50, 100, 200) and _pp != per_page:
                _ui_prefs.tasks_per_page = _pp
                _ui_prefs.save(update_fields=["tasks_per_page", "updated_at"])
                per_page = _pp
        except (ValueError, TypeError):
            pass

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
                    view_task.can_manage_status = _can_manage_task_status_ui(user, view_task)  # type: ignore[attr-defined]
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
    # (read-only: GET не пишет в БД — backfill вынесен в data-миграцию).
    for task in page.object_list:
        if not task.type and task.title and task.title in task_types_by_name:
            task_type = task_types_by_name[task.title]
            task.type = task_type  # type: ignore[assignment]
            task.type_id = task_type.id  # type: ignore[attr-defined]
    
    # Сохраняем сортировку в cookie, если она была изменена через GET параметры
    _template_name = "ui/task_list_v2.html" if getattr(request, "_preview_v2", False) else "ui/task_list.html"
    response = render(
        request,
        _template_name,
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
            "search_q": search_q,
        },
    )
    
    # Устанавливаем cookie для сохранения сортировки (срок действия 1 год)
    if sort_field:
        cookie_value = f"{sort_field}:{sort_dir}"
        response.set_cookie("task_list_sort", cookie_value, max_age=31536000)  # 1 год
    
    return response


@login_required
@policy_required(resource_type="page", resource="ui:tasks:list")
def task_list_v2_preview(request: HttpRequest) -> HttpResponse:
    """Preview редизайна списка задач (Notion-стиль). Только ADMIN."""
    if not (request.user.is_superuser or getattr(request.user, "role", None) == User.Role.ADMIN):
        from django.core.exceptions import PermissionDenied as _PD
        raise _PD("Preview доступен только администраторам")
    request._preview_v2 = True  # type: ignore[attr-defined]
    return task_list(request)


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

                    # Защита от случайного дублирования задач при повторной отправке формы:
                    # если за последние несколько секунд уже есть такая же задача по этой компании,
                    # просто пропускаем создание.
                    recent_cutoff = timezone.now() - timedelta(seconds=10)
                    has_duplicate = Task.objects.filter(
                        created_by=user,
                        assigned_to=task.assigned_to,
                        company=c,
                        type=task.type,
                        title=task.title,
                        due_at=task.due_at,
                        recurrence_rrule=task.recurrence_rrule or "",
                        created_at__gte=recent_cutoff,
                    ).exists()
                    if has_duplicate:
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

            # Защита от случайного дублирования задач при повторной отправке формы:
            # если за последние несколько секунд уже есть такая же задача, не создаём новую.
            recent_cutoff = timezone.now() - timedelta(seconds=10)
            duplicate_qs = Task.objects.filter(
                created_by=user,
                company_id=task.company_id,
                type_id=task.type_id,
                assigned_to_id=task.assigned_to_id,
                title=task.title,
                due_at=task.due_at,
                recurrence_rrule=task.recurrence_rrule or "",
                created_at__gte=recent_cutoff,
            ).order_by("-created_at")
            existing_task = duplicate_qs.first()
            if existing_task:
                if is_ajax:
                    return JsonResponse({
                        "ok": True,
                        "task_id": str(existing_task.id),
                        "message": "Похожая задача уже была создана недавно.",
                        "duplicate": True,
                    })
                messages.info(request, "Похожая задача уже была создана недавно.")
                return redirect("task_list")

            task.save()
            form.save_m2m()
            # История создания
            TaskEvent.objects.create(
                task=task,
                actor=user,
                kind=TaskEvent.Kind.CREATED,
                new_value=task.title,
            )
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
            # PII-safe: логируем только список полей с ошибками, без содержимого POST и form.errors (могут содержать имена/email).
            logger.warning(
                "Task form validation failed: fields_with_errors=%s, assigned_to_id=%r",
                list(form.errors.keys()),
                assigned_to_id,
            )
            
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

        from tasksapp.services import TaskService
        try:
            result = TaskService.delete_task(task=task, user=user, save_to_notes=save_to_notes)
        except Exception as e:
            if request.headers.get("X-Requested-With") == "XMLHttpRequest":
                return JsonResponse({"error": str(e)}, status=500)
            messages.error(request, str(e))
            return redirect(request.META.get("HTTP_REFERER") or "task_list")

        title = result["title"]
        if result["note_created"]:
            messages.success(request, f"Задача «{title}» удалена. Заметка создана.")
        else:
            messages.success(request, f"Задача «{title}» удалена.")

        if request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return JsonResponse({
                "success": True,
                "note_created": result["note_created"],
                "message": f"Задача «{title}» удалена." + (" Заметка создана." if result["note_created"] else ""),
            })

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
        # Без выбранного статуса: show_done=1 — только выполненные; иначе скрываем выполненные.
        if show_done == "1":
            qs = qs.filter(status=Task.Status.DONE)
            filters_summary.append("Только выполненные задачи")
            has_restricting_filters = True
        else:
            qs = qs.exclude(status=Task.Status.DONE)
            filters_summary.append("Без выполненных задач")

    # Флаг "Мои" (mine)
    mine = (data.get("mine") or "").strip()
    # В bulk-операциях интерпретируем mine строго как "assigned_to = текущий пользователь",
    # чтобы не расширять выборку за счёт сложной роль-логики из task_list.
    if mine == "1":
        qs = qs.filter(assigned_to=user)
        filters_summary.append("Только мои задачи")
        has_restricting_filters = True

    # Фильтр по конкретному исполнителю (игнорируется, если включена галочка "Мои")
    assigned_to_param = (data.get("assigned_to") or "").strip()
    if assigned_to_param and mine != "1":
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

    # Просрочено / сегодня (при show_done=1 не исключаем DONE, чтобы видеть выполненные по периоду)
    overdue = (data.get("overdue") or "").strip()
    if overdue == "1":
        qs = qs.filter(due_at__lt=now)
        if show_done != "1":
            qs = qs.exclude(status__in=[Task.Status.DONE, Task.Status.CANCELLED])
        filters_summary.append("Только просроченные")
        has_restricting_filters = True

    today = (data.get("today") or "").strip()
    if today == "1":
        # При show_done=1 фильтруем по дате выполнения, иначе по дедлайну
        today_field = "completed_at" if show_done == "1" else "due_at"
        qs = qs.filter(**{f"{today_field}__gte": today_start, f"{today_field}__lt": tomorrow_start})
        if show_done != "1":
            qs = qs.exclude(status__in=[Task.Status.DONE, Task.Status.CANCELLED])
        # Для выполненных по "Сегодня": показываем только задачи с заполненной датой завершения
        if show_done == "1":
            qs = qs.filter(completed_at__isnull=False)
        filters_summary.append("Только на сегодня")
        has_restricting_filters = True

    # Период: при show_done=1 — по дате выполнения (completed_at), иначе по дедлайну (due_at)
    date_from = (data.get("date_from") or "").strip()
    date_to = (data.get("date_to") or "").strip()
    date_field = "completed_at" if show_done == "1" else "due_at"

    period_start_label: str | None = None
    period_end_label: str | None = None

    if date_from:
        try:
            date_from_dt = datetime.strptime(date_from, "%Y-%m-%d")
            date_from_start = timezone.make_aware(
                date_from_dt.replace(hour=0, minute=0, second=0, microsecond=0)
            )
            qs = qs.filter(**{f"{date_field}__gte": date_from_start})
            period_start_label = date_from_dt.strftime("%d.%m.%Y")
        except (ValueError, TypeError):
            pass

    if date_to:
        try:
            date_to_dt = datetime.strptime(date_to, "%Y-%m-%d")
            date_to_end = timezone.make_aware(
                date_to_dt.replace(hour=23, minute=59, second=59, microsecond=999999)
            )
            qs = qs.filter(**{f"{date_field}__lte": date_to_end})
            period_end_label = date_to_dt.strftime("%d.%m.%Y")
        except (ValueError, TypeError):
            pass

    if show_done == "1" and (date_from or date_to):
        qs = qs.filter(completed_at__isnull=False)

    period_name = "выполнения" if show_done == "1" else "дедлайна"
    if period_start_label or period_end_label:
        has_restricting_filters = True
        if period_start_label and period_end_label:
            filters_summary.append(
                f"Период {period_name}: {period_start_label} – {period_end_label}"
            )
        elif period_start_label:
            filters_summary.append(f"Дата {period_name} не раньше: {period_start_label}")
        elif period_end_label:
            filters_summary.append(f"Дата {period_name} не позже: {period_end_label}")

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

    old_status = task.status
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

    save_to_notes = request.POST.get("save_to_notes") == "1" if new_status == Task.Status.DONE else False

    from tasksapp.services import TaskService
    try:
        result = TaskService.set_status(
            task=task, user=user, new_status=new_status, save_to_notes=save_to_notes
        )
    except Exception as e:
        if request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return JsonResponse({"error": str(e)}, status=500)
        messages.error(request, str(e))
        return redirect(request.META.get("HTTP_REFERER") or "/tasks/")

    if not result["note_created"]:
        messages.success(request, "Статус обновлён.")
    else:
        messages.success(request, "Задача выполнена. Заметка создана.")

    if request.headers.get("X-Requested-With") == "XMLHttpRequest":
        return JsonResponse({
            "success": True,
            "note_created": result["note_created"],
            "message": "Задача выполнена." + (" Заметка создана." if result["note_created"] else ""),
            "redirect": request.META.get("HTTP_REFERER") or "/tasks/",
        })

    return redirect(request.META.get("HTTP_REFERER") or "/tasks/")


@login_required
def task_add_comment(request: HttpRequest, task_id) -> HttpResponse:
    """Добавление комментария к задаче (только POST/AJAX)."""
    if request.method != "POST":
        return JsonResponse({"error": "Метод не поддерживается."}, status=405)

    user: User = request.user
    task = get_object_or_404(Task, id=task_id)

    # Проверяем, что пользователь имеет доступ к задаче
    if not (_can_manage_task_status_ui(user, task) or _can_edit_task_ui(user, task)):
        return JsonResponse({"error": "Нет доступа к этой задаче."}, status=403)

    text = request.POST.get("text") or ""
    from tasksapp.services import TaskService
    try:
        comment = TaskService.add_comment(task=task, user=user, text=text)
    except ValueError as e:
        return JsonResponse({"error": str(e)}, status=400)

    return JsonResponse({
        "ok": True,
        "comment": {
            "id": comment.id,
            "author": str(user),
            "text": comment.text,
            "created_at": comment.created_at.strftime("%d.%m.%Y %H:%M"),
        },
    })


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
            "is_urgent", "recurrence_rrule",
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

    if not can_view:
        from django.core.exceptions import PermissionDenied
        raise PermissionDenied()

    # Вычисляем просрочку в днях (только если известны дедлайн и время завершения)
    view_task_overdue_days = None
    if task.due_at and task.completed_at and task.completed_at > task.due_at:
        delta = task.completed_at - task.due_at
        view_task_overdue_days = delta.days
    
    # Добавляем флаги прав
    task.can_edit_task = _can_edit_task_ui(user, task)  # type: ignore[attr-defined]
    task.can_manage_status = _can_manage_task_status_ui(user, task)  # type: ignore[attr-defined]
    
    # Сопоставляем задачу с TaskType если нужно
    if not task.type and task.title:
        from tasksapp.models import TaskType
        task_type = TaskType.objects.filter(name=task.title).first()
        if task_type:
            task.type = task_type  # type: ignore[assignment]
            task.type_id = task_type.id  # type: ignore[attr-defined]
    
    now = timezone.now()
    local_now = timezone.localtime(now)
    
    comments = list(task.comments.select_related("author").all())
    events = list(task.events.select_related("actor").all())

    return render(request, "ui/task_view_modal.html", {
        "view_task": task,
        "view_task_overdue_days": view_task_overdue_days,
        "local_now": local_now,
        "task_comments": comments,
        "task_events": events,
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
            old_due_at = task.due_at
            updated_task: Task = form.save(commit=False)
            # Заголовок всегда синхронизируем с выбранным типом/статусом
            if updated_task.type:
                updated_task.title = updated_task.type.name
            updated_task.save()
            # История: дедлайн изменился?
            if old_due_at != updated_task.due_at:
                old_str = old_due_at.strftime("%d.%m.%Y %H:%M") if old_due_at else "—"
                new_str = updated_task.due_at.strftime("%d.%m.%Y %H:%M") if updated_task.due_at else "—"
                TaskEvent.objects.create(
                    task=updated_task,
                    actor=user,
                    kind=TaskEvent.Kind.DEADLINE_CHANGED,
                    old_value=old_str,
                    new_value=new_str,
                )
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


# ============================================================================
#  v2 partial views: задачи в модалке (gibrid mode).
#  Возвращают либо чистый HTML-фрагмент (GET ?partial=1), либо JSON на POST.
#  Отделены от v1 endpoints, чтобы не ломать текущие /tasks/create/ и /tasks/<id>/view/.
# ============================================================================

def _v2_task_create_get(request: HttpRequest) -> HttpResponse:
    """GET v2 partial: отдаёт форму создания задачи без заголовка/RRULE."""
    user: User = request.user
    form = TaskForm()
    _set_assigned_to_queryset(form, user)
    # type_choices — для кастомного рендера плашек (вместо <select>)
    task_types = list(TaskType.objects.only("id", "name", "icon", "color").all())
    # Preselect company из ?company=<id>
    preselect_company_id = (request.GET.get("company") or "").strip()
    companies_qs = _editable_company_qs(user).only("id", "name").order_by("name")[:500]
    return render(request, "ui/_v2/task_create_partial.html", {
        "form": form,
        "task_types": task_types,
        "companies_for_picker": companies_qs,
        "preselect_company_id": preselect_company_id,
    })


@login_required
@policy_required(resource_type="action", resource="ui:tasks:create")
def task_create_v2_partial(request: HttpRequest) -> HttpResponse:
    """
    v2 partial endpoint для создания задачи внутри модалки.
    GET  → HTML-фрагмент формы.
    POST → JSON {ok:true, toast, id} или 422 c HTML-фрагментом (валидационные ошибки).
    """
    user: User = request.user

    if request.method == "GET":
        return _v2_task_create_get(request)

    # POST
    form = TaskForm(request.POST)
    form.fields["assigned_to"].queryset = User.objects.filter(is_active=True).select_related("branch")

    assigned_to_id = clean_int_id(request.POST.get("assigned_to", ""))
    if assigned_to_id is not None:
        if not form.fields["assigned_to"].queryset.filter(id=assigned_to_id).exists():
            form.fields["assigned_to"].queryset = User.objects.filter(
                Q(is_active=True) | Q(id=assigned_to_id)
            ).select_related("branch")

    if not form.is_valid():
        # Возвращаем HTML-фрагмент формы с ошибками (status 422) — v2-modal.js перерисует тело
        task_types = list(TaskType.objects.only("id", "name", "icon", "color").all())
        html = render(request, "ui/_v2/task_create_partial.html", {
            "form": form,
            "task_types": task_types,
            "companies_for_picker": _editable_company_qs(user).only("id", "name").order_by("name")[:500],
            "preselect_company_id": (request.POST.get("company") or "").strip(),
        }).content.decode("utf-8")
        return HttpResponse(html, status=422)

    task: Task = form.save(commit=False)
    task.created_by = user

    # Заголовок берём из типа задачи (как в v1)
    if task.type and not task.title:
        task.title = task.type.name

    comp = None
    if task.company_id:
        comp = Company.objects.select_related("responsible", "branch").filter(id=task.company_id).first()
        if comp and not _can_edit_company(user, comp):
            return JsonResponse({"ok": False, "error": "Нет прав на постановку задач по этой компании."}, status=403)

    # RBAC
    if user.role == User.Role.MANAGER:
        task.assigned_to = user
    else:
        if not task.assigned_to:
            task.assigned_to = user
        if user.role in (User.Role.BRANCH_DIRECTOR, User.Role.SALES_HEAD) and user.branch_id:
            if task.assigned_to and task.assigned_to.branch_id and task.assigned_to.branch_id != user.branch_id:
                return JsonResponse({"ok": False, "error": "Можно назначать задачи только сотрудникам своего подразделения."}, status=400)

    if task.assigned_to and task.assigned_to.role in (User.Role.ADMIN, User.Role.GROUP_MANAGER):
        return JsonResponse({"ok": False, "error": "Нельзя назначать задачи администратору или управляющему."}, status=400)

    # Статус: сам себе → «В работе», иначе «Новая»
    task.status = Task.Status.IN_PROGRESS if task.assigned_to_id == user.id else Task.Status.NEW
    task.save()

    try:
        log_event(
            actor=user, verb="task.create", resource_type="task", resource_id=str(task.id),
            target_user=task.assigned_to, company=task.company, payload={"title": task.title},
        )
    except Exception:
        logger.exception("task_create_v2_partial: log_event failed")

    return JsonResponse({
        "ok": True,
        "toast": "Задача создана",
        "id": str(task.id),
        "close": True,
    })


