from __future__ import annotations

import logging

from django.conf import settings

from ui.views._base import (
    ActivityEvent,
    Branch,
    BranchForm,
    CompanySphere,
    CompanySphereForm,
    CompanyStatus,
    CompanyStatusForm,
    ContractType,
    ContractTypeForm,
    Count,
    HttpRequest,
    HttpResponse,
    JsonResponse,
    MagicLinkToken,
    Paginator,
    Q,
    TaskType,
    TaskTypeForm,
    User,
    UserCreateForm,
    UserEditForm,
    cache,
    datetime,
    get_object_or_404,
    log_event,
    login_required,
    messages,
    policy_required,
    redirect,
    render,
    require_admin,
    timedelta,
    timezone,
)

logger = logging.getLogger(__name__)


@login_required
@policy_required(resource_type="page", resource="ui:settings:dashboard")
def settings_dashboard(request: HttpRequest) -> HttpResponse:
    # W2.1.4.1: inline require_admin() preserved as defense-in-depth.
    # Декоратор @policy_required = primary gate (audit + policy engine).
    if not require_admin(request.user):
        messages.error(request, "Доступ запрещён.")
        return redirect("dashboard")
    from companies.models import Company

    ctx = {
        "MESSENGER_ENABLED": getattr(settings, "MESSENGER_ENABLED", False),
        "v2_count_users": User.objects.count(),
        "v2_count_branches": Branch.objects.count(),
        "v2_count_statuses": CompanyStatus.objects.count(),
        "v2_count_spheres": CompanySphere.objects.count(),
        "v2_count_task_types": TaskType.objects.count(),
        "v2_count_contract_types": ContractType.objects.count(),
        "v2_count_companies": Company.objects.count(),
    }

    return render(request, "ui/settings/dashboard_v2.html", ctx)


@login_required
@policy_required(resource_type="page", resource="ui:settings:announcements")
def settings_announcements(request: HttpRequest) -> HttpResponse:
    from django.contrib.auth import get_user_model

    from notifications.models import CrmAnnouncement, CrmAnnouncementRead

    User = get_user_model()

    # W2.1.4.1: inline require_admin() preserved as defense-in-depth.
    if not require_admin(request.user):
        messages.error(request, "Доступ запрещён.")
        return redirect("dashboard")

    if request.method == "POST":
        action = request.POST.get("action")
        if action == "create":
            title = request.POST.get("title", "").strip()
            body = request.POST.get("body", "").strip()
            ann_type = request.POST.get("announcement_type", "info")
            scheduled_at_str = request.POST.get("scheduled_at", "").strip()
            if title and body:
                import datetime

                scheduled_at = None
                if scheduled_at_str:
                    try:
                        scheduled_at = datetime.datetime.fromisoformat(scheduled_at_str)
                        if timezone.is_naive(scheduled_at):
                            scheduled_at = timezone.make_aware(scheduled_at)
                    except ValueError:
                        pass
                CrmAnnouncement.objects.create(
                    title=title,
                    body=body,
                    announcement_type=ann_type,
                    created_by=request.user,
                    scheduled_at=scheduled_at,
                )
                messages.success(request, "Объявление отправлено.")
            else:
                messages.error(request, "Заполните заголовок и текст.")
        elif action == "deactivate":
            ann_id = request.POST.get("ann_id")
            CrmAnnouncement.objects.filter(id=ann_id).update(is_active=False)
            messages.success(request, "Объявление деактивировано.")
        elif action == "activate":
            ann_id = request.POST.get("ann_id")
            CrmAnnouncement.objects.filter(id=ann_id).update(is_active=True)
            messages.success(request, "Объявление активировано.")
        return redirect("settings_announcements")

    total_users = User.objects.filter(is_active=True).count()
    announcements = (
        CrmAnnouncement.objects.select_related("created_by")
        .prefetch_related("reads")
        .order_by("-created_at")[:50]
    )
    ann_data = []
    for a in announcements:
        ann_data.append(
            {
                "obj": a,
                "read_count": a.reads.count(),
                "total_users": total_users,
            }
        )

    return render(
        request,
        "ui/settings/announcements.html",
        {
            "announcements": ann_data,
            "total_users": total_users,
            "type_choices": CrmAnnouncement.Type.choices,
        },
    )


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

    from policy.models import PolicyConfig, PolicyRule

    cfg = PolicyConfig.load()
    rules_total = PolicyRule.objects.filter(enabled=True).count()
    rules_role_total = PolicyRule.objects.filter(
        enabled=True, subject_type=PolicyRule.SubjectType.ROLE
    ).count()

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
            actions = [
                r for r in list_resources(resource_type="action") if (r.key or "").startswith("ui:")
            ]

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

            messages.success(
                request,
                f"Baseline применён: менеджеру запрещены sensitive ресурсы. Изменений: {changed}.",
            )
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
    existing = PolicyRule.objects.filter(
        enabled=True,
        subject_type=PolicyRule.SubjectType.ROLE,
        role=role,
    ).order_by("priority", "id")
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
                messages.success(
                    request, f"Готово: всё сброшено в inherit. Удалено правил: {changed}."
                )
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

                messages.success(
                    request, f"Готово: sensitive ресурсы запрещены. Изменений: {changed}."
                )
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
@policy_required(resource_type="page", resource="ui:settings:branches")
def settings_branches(request: HttpRequest) -> HttpResponse:
    # W2.1.4.1: inline require_admin() preserved as defense-in-depth.
    if not require_admin(request.user):
        messages.error(request, "Доступ запрещён.")
        return redirect("dashboard")
    branches = Branch.objects.order_by("name")
    return render(request, "ui/settings/branches.html", {"branches": branches})


@login_required
@policy_required(resource_type="action", resource="ui:settings:branches:create")
def settings_branch_create(request: HttpRequest) -> HttpResponse:
    # W2.1.4.1: inline require_admin() preserved as defense-in-depth.
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
@policy_required(resource_type="action", resource="ui:settings:branches:edit")
def settings_branch_edit(request: HttpRequest, branch_id: int) -> HttpResponse:
    # W2.1.4.1: inline require_admin() preserved as defense-in-depth.
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
    return render(
        request, "ui/settings/branch_form.html", {"form": form, "mode": "edit", "branch": branch}
    )


@login_required
@policy_required(resource_type="page", resource="ui:settings:users")
def settings_users(request: HttpRequest) -> HttpResponse:
    # W2.1.4.1: inline require_admin() preserved as defense-in-depth.
    # Note: POST с toggle_view_as использует отдельную policy resource
    # `ui:settings:view_as:update` (codified W2.1.3b) — inline enforce()
    # будет добавлен отдельно в W2.1.5 (inline enforce → decorator migration).
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
        messages.success(
            request,
            f"Режим просмотра администратора {'включён' if view_as_enabled else 'выключен'}.",
        )
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
    from django.contrib.auth import SESSION_KEY
    from django.contrib.sessions.models import Session

    recently_online_threshold = timezone.now() - timedelta(minutes=15)

    # Применяем фильтр по онлайн статусу (учитывая и сессии, и last_login)
    if online_filter == "online":
        # Фильтр по онлайн: либо есть активная сессия, либо last_login за последние 15 минут
        # Получаем ID пользователей с активными сессиями (только из текущего queryset)
        user_ids_in_queryset = set(users.values_list("id", flat=True))
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
            users.filter(last_login__gte=recently_online_threshold).values_list("id", flat=True)
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
        user_ids_in_queryset = set(users.values_list("id", flat=True))
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
                users.filter(last_login__gte=recently_online_threshold).values_list("id", flat=True)
            )
            # Офлайн = все пользователи минус онлайн (сессии или недавний вход)
            offline_ids = (
                user_ids_in_queryset - online_user_ids_from_sessions - users_with_recent_login
            )
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
    user_ids_for_status = set(
        users.values_list("id", flat=True)
    )  # Выполняем queryset один раз для получения ID
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
        user.is_online = user.id in online_user_ids or (
            user.last_login and user.last_login >= recently_online_threshold
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
@policy_required(resource_type="action", resource="ui:settings:users:create")
def settings_user_create(request: HttpRequest) -> HttpResponse:
    # W2.1.4.1: inline require_admin() preserved as defense-in-depth.
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
                messages.success(
                    request, f"Пользователь {user} создан. Пароль сгенерирован автоматически."
                )
            else:
                messages.success(
                    request, f"Пользователь {user} создан. Ключ доступа сгенерирован автоматически."
                )
            return redirect("settings_user_edit", user_id=user.id)
    else:
        form = UserCreateForm()
    return render(request, "ui/settings/user_form.html", {"form": form, "mode": "create"})


@login_required
@policy_required(resource_type="action", resource="ui:settings:users:edit")
def settings_user_edit(request: HttpRequest, user_id: int) -> HttpResponse:
    # W2.1.4.1: inline require_admin() preserved as defense-in-depth.
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
                    ActivityEvent.objects.filter(actor=u, created_at__gte=token.used_at)
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
    from django.contrib.auth import SESSION_KEY
    from django.contrib.sessions.models import Session

    active_sessions = []
    for session in Session.objects.filter(expire_date__gte=timezone.now()):
        session_data = session.get_decoded()
        user_id_from_session = session_data.get(SESSION_KEY)
        if user_id_from_session and int(user_id_from_session) == u.id:
            active_sessions.append(
                {
                    "session_key": session.session_key,
                    "expire_date": session.expire_date,
                    "last_activity": session.expire_date,  # Приблизительно
                }
            )

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
@policy_required(resource_type="action", resource="ui:settings:users:magic_link:generate")
def settings_user_magic_link_generate(request: HttpRequest, user_id: int) -> HttpResponse:
    """
    Генерация одноразовой ссылки входа для пользователя (только для админа).
    URL: /settings/users/<user_id>/magic-link/generate/
    """
    # W2.1.4.1: inline require_admin() preserved as defense-in-depth.
    # Rate limit (1/10s per target user) + audit log preserved unchanged below.
    if not require_admin(request.user):
        messages.error(request, "Доступ запрещён.")
        return redirect("dashboard")

    user = get_object_or_404(User, id=user_id, is_active=True)
    admin_user: User = request.user

    # Rate limiting: не чаще 1 раза в 10 секунд на пользователя
    from accounts.security import get_client_ip, is_ip_rate_limited

    ip = get_client_ip(request)
    cache_key = f"magic_link_generate_rate:{user_id}"
    from django.core.cache import cache

    if cache.get(cache_key):
        messages.error(request, "Подождите 10 секунд перед генерацией новой ссылки.")
        return redirect("settings_user_edit", user_id=user_id)
    cache.set(cache_key, True, 10)

    # Генерируем токен
    from django.conf import settings as django_settings

    from accounts.models import MagicLinkToken

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
            extra={
                "admin_user_id": admin_user.id if admin_user else None,
                "target_user_id": user.id,
            },
        )

    # Возвращаем JSON с ссылкой (для AJAX) или редиректим с сообщением
    if request.headers.get("X-Requested-With") == "XMLHttpRequest":
        return JsonResponse(
            {
                "success": True,
                "token": plain_token,
                "link": magic_link_url,
                "expires_at": magic_link.expires_at.isoformat(),
            }
        )

    # Для обычного запроса сохраняем в сессии и редиректим
    request.session["magic_link_generated"] = {
        "token": plain_token,  # Сохраняем сам ключ для отображения
        "link": magic_link_url,
        "expires_at": magic_link.expires_at.isoformat(),
        "user_id": user_id,
    }
    messages.success(
        request, f"Ссылка входа создана для {user}. Она будет показана на странице редактирования."
    )
    return redirect("settings_user_edit", user_id=user_id)


@login_required
@policy_required(resource_type="action", resource="ui:settings:users:force_logout")
def settings_user_logout(request: HttpRequest, user_id: int) -> HttpResponse:
    """
    Принудительное разлогинивание пользователя (завершение всех его сессий).
    URL: /settings/users/<user_id>/logout/
    """
    # W2.1.4.1: inline require_admin() preserved as defense-in-depth.
    if not require_admin(request.user):
        messages.error(request, "Доступ запрещён.")
        return redirect("dashboard")

    target_user = get_object_or_404(User, id=user_id)
    admin_user: User = request.user

    if request.method == "POST":
        # Удаляем все сессии пользователя
        from django.contrib.auth import SESSION_KEY
        from django.contrib.sessions.models import Session

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
                extra={
                    "admin_user_id": admin_user.id if admin_user else None,
                    "target_user_id": target_user.id,
                },
            )

        messages.success(
            request, f"Пользователь {target_user} разлогинен. Завершено сессий: {sessions_deleted}."
        )
        return redirect("settings_users")

    return redirect("settings_users")


@login_required
@policy_required(resource_type="action", resource="ui:settings:users:form")
def settings_user_form_ajax(request: HttpRequest, user_id: int) -> JsonResponse:
    """
    AJAX endpoint для получения формы редактирования пользователя (для модалки).
    """
    # W2.1.4.1: inline require_admin() preserved as defense-in-depth.
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

    return JsonResponse(
        {
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
        }
    )


@login_required
@policy_required(resource_type="action", resource="ui:settings:users:update")
def settings_user_update_ajax(request: HttpRequest, user_id: int) -> JsonResponse:
    """
    AJAX endpoint для сохранения изменений пользователя (для модалки).
    """
    # W2.1.4.1: inline require_admin() preserved as defense-in-depth.
    if not require_admin(request.user):
        return JsonResponse({"ok": False, "error": "Доступ запрещён."}, status=403)

    if request.method != "POST":
        return JsonResponse({"ok": False, "error": "Метод не поддерживается."}, status=405)

    user = get_object_or_404(User, id=user_id)
    from ui.forms import UserEditForm

    form = UserEditForm(request.POST, instance=user)

    if form.is_valid():
        form.save()
        return JsonResponse(
            {
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
            }
        )
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
        return JsonResponse(
            {
                "ok": False,
                "errors": form.errors,
                "html": form_html,
            },
            status=400,
        )


@login_required
@policy_required(resource_type="action", resource="ui:settings:users:delete")
def settings_user_delete(request: HttpRequest, user_id: int) -> JsonResponse:
    """AJAX: полное удаление пользователя. Компании остаются (responsible → NULL)."""
    # W2.1.4.1: inline require_admin() preserved as defense-in-depth.
    if not require_admin(request.user):
        return JsonResponse({"ok": False, "error": "Доступ запрещён."}, status=403)

    if request.method != "POST":
        return JsonResponse({"ok": False, "error": "Метод не поддерживается."}, status=405)

    user = get_object_or_404(User, id=user_id)

    if user.id == request.user.id:
        return JsonResponse({"ok": False, "error": "Нельзя удалить самого себя."}, status=400)

    if user.role == User.Role.ADMIN or user.is_superuser:
        remaining_admins = (
            User.objects.filter(
                is_active=True,
                role=User.Role.ADMIN,
            )
            .exclude(id=user.id)
            .count()
        )
        if remaining_admins == 0:
            return JsonResponse(
                {"ok": False, "error": "Нельзя удалить последнего администратора системы."},
                status=400,
            )

    username = user.username
    full_name = user.get_full_name() or username

    log_event(
        actor=request.user,
        verb=ActivityEvent.Verb.DELETE,
        entity_type="user",
        entity_id=str(user.id),
        message=f"Удалён пользователь: {full_name} ({username})",
    )

    user.delete()

    return JsonResponse(
        {
            "ok": True,
            "message": f"Пользователь «{full_name}» удалён.",
        }
    )


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
            "company_spheres": CompanySphere.objects.annotate(
                company_count=Count("companies")
            ).order_by("name"),
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
    return render(
        request, "ui/settings/dict_form.html", {"form": form, "title": "Новый статус компании"}
    )


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
    return render(
        request, "ui/settings/dict_form.html", {"form": form, "title": "Новая сфера компании"}
    )


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
    return render(
        request, "ui/settings/dict_form.html", {"form": form, "title": "Новый вид договора"}
    )


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

            cache.delete("task_types_all_dict")
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
    return render(
        request,
        "ui/settings/dict_form_modal.html",
        {
            "form": form,
            "title": "Редактировать статус компании",
            "dict_type": "company-status",
            "dict_id": status.id,
        },
    )


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
    return render(
        request,
        "ui/settings/dict_form_modal.html",
        {
            "form": form,
            "title": "Редактировать сферу компании",
            "dict_type": "company-sphere",
            "dict_id": sphere.id,
        },
    )


@login_required
def settings_company_sphere_delete(request: HttpRequest, sphere_id: int) -> HttpResponse:
    """Удаление сферы компании (с возможностью слияния)"""
    if not require_admin(request.user):
        return JsonResponse({"ok": False, "error": "Доступ запрещён."}, status=403)

    sphere = get_object_or_404(CompanySphere, id=sphere_id)

    # GET — вернуть модалку с выбором действия
    if request.method == "GET" and request.headers.get("X-Requested-With") == "XMLHttpRequest":
        other_spheres = CompanySphere.objects.exclude(id=sphere_id).order_by("name")
        company_count = sphere.companies.count()
        return render(
            request,
            "ui/settings/sphere_delete_modal.html",
            {"sphere": sphere, "company_count": company_count, "other_spheres": other_spheres},
        )

    if request.method != "POST":
        return JsonResponse({"ok": False, "error": "Method not allowed."}, status=405)

    action = request.POST.get("action", "delete")

    if action == "merge":
        target_id = request.POST.get("target_id")
        if not target_id:
            return JsonResponse({"ok": False, "error": "Целевая сфера не выбрана."}, status=400)
        target_sphere = get_object_or_404(CompanySphere, id=target_id)
        # Переносим все компании на целевую сферу
        for company in sphere.companies.all():
            company.spheres.add(target_sphere)

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
                return JsonResponse(
                    {"ok": True, "id": contract_type.id, "name": contract_type.name}
                )
            messages.success(request, "Вид договора обновлён.")
            return redirect("settings_dicts")
    else:
        form = ContractTypeForm(instance=contract_type)
    return render(
        request,
        "ui/settings/dict_form_modal.html",
        {
            "form": form,
            "title": "Редактировать вид договора",
            "dict_type": "contract-type",
            "dict_id": contract_type.id,
        },
    )


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

            cache.delete("task_types_all_dict")
            if request.headers.get("X-Requested-With") == "XMLHttpRequest":
                return JsonResponse(
                    {
                        "ok": True,
                        "id": task_type.id,
                        "name": task_type.name,
                        "icon": task_type.icon or "",
                        "color": task_type.color or "",
                    }
                )
            messages.success(request, "Задача обновлена.")
            return redirect("settings_dicts")
    else:
        form = TaskTypeForm(instance=task_type)
    return render(
        request,
        "ui/settings/dict_form_modal.html",
        {
            "form": form,
            "title": "Редактировать задачу",
            "dict_type": "task-type",
            "dict_id": task_type.id,
        },
    )


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

    cache.delete("task_types_all_dict")
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
    return render(
        request,
        "ui/settings/activity.html",
        {
            "events": events,
            "can_undo_bulk_reschedule": require_admin(request.user),
        },
    )


@login_required
def settings_error_log(request: HttpRequest) -> HttpResponse:
    """Страница лога ошибок (аналогично error_log в MODX CMS)."""
    if not require_admin(request.user):
        messages.error(request, "Доступ запрещён.")
        return redirect("dashboard")

    from django.db.models import Q

    from audit.models import ErrorLog

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
            Q(message__icontains=search_query)
            | Q(exception_type__icontains=search_query)
            | Q(path__icontains=search_query)
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

    from django.utils import timezone

    from audit.models import ErrorLog

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
