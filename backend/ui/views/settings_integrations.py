from __future__ import annotations

import logging

from ui.views._base import (
    ActivityEvent,
    AmoApiConfig,
    AmoApiConfigForm,
    AmoMigrateFilterForm,
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

# amocrm is imported here (and only here) so that other view modules don't
# trigger its import at startup. Keep these lazy if you want zero-cost startup.
from amocrm.client import AmoApiError, AmoClient
from amocrm.migrate import (
    fetch_amo_users,
    fetch_company_custom_fields,
    fetch_matched_amo_company_ids,
    import_company_histories,
    migrate_filtered,
)


def _amo_fetch_users(client):
    return fetch_amo_users(client)


def _amo_fetch_custom_fields(client):
    return fetch_company_custom_fields(client)


def _amo_migrate_filtered(*args, **kwargs):
    return migrate_filtered(*args, **kwargs)


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


@login_required
def settings_amocrm(request: HttpRequest) -> HttpResponse:
    if not require_admin(request.user):
        messages.error(request, "Доступ запрещён.")
        return redirect("dashboard")

    cfg = AmoApiConfig.load()
    if request.method == "POST":
        form = AmoApiConfigForm(request.POST)
        if form.is_valid():
            cfg.domain = (
                (form.cleaned_data.get("domain") or "")
                .strip()
                .replace("https://", "")
                .replace("http://", "")
                .strip("/")
            )
            cfg.client_id = (form.cleaned_data.get("client_id") or "").strip()
            secret = (form.cleaned_data.get("client_secret") or "").strip()
            if secret:
                cfg.set_client_secret(secret)
            token = (form.cleaned_data.get("long_lived_token") or "").strip()
            if token:
                cfg.long_lived_token = token
            # redirect uri: если пусто — построим из request
            ru = (form.cleaned_data.get("redirect_uri") or "").strip()
            if not ru:
                ru = request.build_absolute_uri("/admin/amocrm/callback/")
            cfg.redirect_uri = ru
            cfg.region_custom_field_id = form.cleaned_data.get("region_custom_field_id") or None
            cfg.save(
                update_fields=[
                    "domain",
                    "client_id",
                    "client_secret",  # очищается set_client_secret()
                    "client_secret_enc",
                    "redirect_uri",
                    "long_lived_token_enc",
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
                "client_secret": cfg.get_client_secret(),
                "redirect_uri": cfg.redirect_uri
                or request.build_absolute_uri("/admin/amocrm/callback/"),
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
    redirect_uri_display = cfg.redirect_uri or request.build_absolute_uri("/admin/amocrm/callback/")

    return render(
        request,
        "ui/settings/amocrm.html",
        {
            "form": form,
            "cfg": cfg,
            "auth_url": auth_url,
            "redirect_uri_display": redirect_uri_display,
        },
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
    logger.info(
        f"AmoCRM OAuth callback: code={'present' if code else 'missing'}, referer={referer}, state={state}, current_domain={cfg.domain}"
    )

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

    if not cfg.get_client_secret():
        messages.error(request, "Client Secret не настроен. Проверьте настройки AmoCRM.")
        return redirect("settings_amocrm")

    if not cfg.redirect_uri:
        messages.error(request, "Redirect URI не настроен. Проверьте настройки AmoCRM.")
        return redirect("settings_amocrm")

    try:
        logger.info(
            f"Exchanging code for token. Domain: {cfg.domain}, Client ID: {cfg.client_id[:10]}..., Redirect URI: {cfg.redirect_uri}"
        )
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
                f"Ошибка: {error_msg}",
            )
        else:
            messages.error(request, f"Ошибка подключения amoCRM: {error_msg}")
    except Exception as e:
        import traceback

        error_details = traceback.format_exc()
        logger.error(f"Unexpected error during AmoCRM token exchange: {error_details}")
        cfg.last_error = str(e)
        cfg.save(update_fields=["last_error", "updated_at"])
        messages.error(request, f"Неожиданная ошибка: {e!s}. Проверьте логи.")

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
    cfg.save(
        update_fields=[
            "access_token_enc",
            "refresh_token_enc",
            "long_lived_token_enc",
            "expires_at",
            "last_error",
            "updated_at",
        ]
    )
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
        logger.error("AMOCRM_MIGRATE_ERROR: Failed to load AmoApiConfig: %s", error_details)
        messages.error(
            request, f"Ошибка загрузки настроек amoCRM: {e!s}. Проверьте логи сервера."
        )
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
        amo_users_raw = _amo_fetch_users(client)
        # Если список пользователей пуст (например, из-за 403), показываем предупреждение
        if not amo_users_raw:
            messages.warning(
                request,
                "Не удалось получить список пользователей AmoCRM. "
                "Long-lived token может не иметь прав на доступ к /api/v4/users. "
                "Для полного доступа используйте OAuth токен (переавторизуйтесь). "
                "Миграция будет работать, но выбор ответственного пользователя может быть ограничен.",
            )
        else:
            # Список пользователей amoCRM для выбора ответственного (без филиалов).
            # fetch_amo_users(client) не принимает branch — всегда полный список; фильтрации по филиалу нет.
            users = [{"id": u.get("id"), "name": u.get("name")} for u in (amo_users_raw or [])]
        fields = _amo_fetch_custom_fields(client)
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
                "Миграция будет работать, но выбор ответственного пользователя может быть ограничен.",
            )
    except Exception as e:
        import traceback

        error_details = traceback.format_exc()
        logger.error("AMOCRM_MIGRATE_INIT_ERROR: %s", error_details)
        messages.error(request, f"Ошибка инициализации: {e!s}. Проверьте логи сервера.")
        if not client:
            # Возвращаем страницу с пустыми данными, но с формой
            form = AmoMigrateFilterForm(
                initial={"dry_run": True, "limit_companies": 10, "offset": 0}
            )
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
                messages.error(
                    request,
                    "Ошибка: клиент amoCRM не инициализирован. Проверьте настройки подключения.",
                )
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
                        val = request.POST.get("responsible_user_id") or (
                            form.cleaned_data.get("responsible_user_id")
                            if form.cleaned_data
                            else None
                        )
                        if not val:
                            messages.error(request, "Выберите ответственного пользователя.")
                        else:
                            try:
                                responsible_user_id = (
                                    int(val)
                                    if isinstance(val, (int, str))
                                    else int(str(val).strip().split(",")[0])
                                )
                            except (ValueError, TypeError):
                                responsible_user_id = None
                            if not responsible_user_id:
                                messages.error(
                                    request, "Некорректный идентификатор ответственного."
                                )
                            else:
                                # Запрет параллельного импорта: блокировка per-user (два админа не мешали друг другу).
                                # migrate_filtered синхронный, не пишет промежуточный прогресс; общее состояние — ключ amocrm_import_run.
                                # Внимание: request держится всё время импорта — проверьте nginx/gunicorn timeouts. Для долгих
                                # импортов предпочтительнее background job (Celery), иначе обрывы и «упал до finally».
                                lock_key = f"amocrm_import_run:{request.user.id}"
                                run_id = str(uuid.uuid4())
                                lock_payload = json.dumps(
                                    {
                                        "run_id": run_id,
                                        "status": "running",
                                        "started_at": datetime.now().isoformat(),
                                    }
                                )
                                lock_acquired = cache.add(lock_key, lock_payload, timeout=3600)
                                if not lock_acquired:
                                    messages.error(
                                        request, "Импорт уже выполняется. Дождитесь завершения."
                                    )
                                    result = None
                                    run_id = None
                                else:
                                    try:
                                        import_history_only = bool(
                                            form.cleaned_data.get("import_history")
                                        )
                                        target_responsible = form.cleaned_data.get(
                                            "target_responsible"
                                        )

                                        if import_history_only:
                                            # Режим «только история» — не меняем данные карточек
                                            sphere_field_id_hist = (
                                                int(custom_field_id) if custom_field_id else 0
                                            )
                                            company_amo_ids, amo_created_by = (
                                                fetch_matched_amo_company_ids(
                                                    client,
                                                    responsible_user_id=responsible_user_id,
                                                    sphere_field_id=sphere_field_id_hist,
                                                    sphere_option_id=form.cleaned_data.get(
                                                        "custom_value_enum_id"
                                                    )
                                                    or None,
                                                    sphere_label=form.cleaned_data.get(
                                                        "custom_value_label"
                                                    )
                                                    or None,
                                                    skip_field_filter=migrate_all,
                                                )
                                            )
                                            history_offset = int(request.POST.get("offset") or 0)
                                            history_limit = int(
                                                form.cleaned_data.get("limit_companies") or 0
                                            )
                                            prev_created = int(
                                                request.POST.get("prev_events_created") or 0
                                            )
                                            prev_skipped = int(
                                                request.POST.get("prev_events_skipped") or 0
                                            )
                                            result = import_company_histories(
                                                client,
                                                actor=request.user,
                                                dry_run=dry_run,
                                                company_amo_ids=company_amo_ids,
                                                amo_created_by=amo_created_by,
                                                offset=history_offset,
                                                limit_companies=history_limit,
                                            )
                                            # Накапливаем счётчики между пачками
                                            result.events_created += prev_created
                                            result.events_skipped += prev_skipped
                                            # companies_processed = сколько обработано всего (offset + текущая пачка)
                                            result.companies_processed = (
                                                result.companies_next_offset
                                            )
                                            migrate_responsible_user_id = responsible_user_id
                                            if dry_run:
                                                messages.success(
                                                    request,
                                                    f"Dry-run: компаний в пачке {result.companies_processed} "
                                                    f"(из {result.companies_total}), "
                                                    f"событий для создания: {result.events_created}.",
                                                )
                                            else:
                                                messages.success(
                                                    request,
                                                    f"История импортирована: создано {result.events_created} событий, "
                                                    f"пропущено {result.events_skipped} (дубликаты). "
                                                    f"Обработано {result.companies_processed} из {result.companies_total} компаний.",
                                                )
                                        else:
                                            result = _amo_migrate_filtered(
                                                client=client,
                                                actor=request.user,
                                                responsible_user_id=responsible_user_id,
                                                sphere_field_id=int(custom_field_id),
                                                sphere_option_id=form.cleaned_data.get(
                                                    "custom_value_enum_id"
                                                )
                                                or None,
                                                sphere_label=form.cleaned_data.get(
                                                    "custom_value_label"
                                                )
                                                or None,
                                                limit_companies=batch_size,
                                                offset=int(form.cleaned_data.get("offset") or 0),
                                                dry_run=dry_run,
                                                import_tasks=bool(
                                                    form.cleaned_data.get("import_tasks")
                                                ),
                                                import_notes=bool(
                                                    form.cleaned_data.get("import_notes")
                                                ),
                                                import_contacts=bool(
                                                    form.cleaned_data.get("import_contacts")
                                                ),
                                                company_fields_meta=fields,
                                                skip_field_filter=migrate_all,
                                                region_field_id=getattr(
                                                    cfg, "region_custom_field_id", None
                                                )
                                                or None,
                                                target_responsible=target_responsible,
                                            )
                                            migrate_responsible_user_id = responsible_user_id
                                            if dry_run:
                                                messages.success(
                                                    request, "Проверка (dry-run) выполнена."
                                                )
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
                    messages.error(
                        request, f"Ошибка миграции: {e!s}. Проверьте логи сервера для деталей."
                    )
                    # В продакшене можно логировать в файл или sentry
                    logger.error("AMOCRM_MIGRATE_ERROR: %s", error_details)
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
            logger.error("AMOCRM_MIGRATE_ERROR: Failed to find default responsible: %s", e)

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
            logger.error("AMOCRM_MIGRATE_ERROR: Failed to create form: %s", error_details)
            # Создаём минимальную форму
            form = AmoMigrateFilterForm(
                initial={"dry_run": True, "limit_companies": 10, "offset": 0}
            )

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
        logger.error("AMOCRM_MIGRATE_ERROR: Failed to render template: %s", error_details)
        # Возвращаем простую страницу с ошибкой
        from django.http import HttpResponse

        return HttpResponse(
            f"Ошибка рендеринга страницы миграции: {e!s}. Проверьте логи сервера для деталей.",
            status=500,
        )


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
    return JsonResponse(
        {
            "active_run": {"run_id": data.get("run_id"), "status": data.get("status", "running")},
        }
    )


@login_required
def settings_amocrm_contacts_dry_run(request: HttpRequest) -> HttpResponse:
    """
    Отдельный dry-run для контактов компаний.
    Показывает все контакты, найденные у компаний из текущей пачки.
    """
    if not require_admin(request.user):
        messages.error(request, "Доступ запрещён.")
        return redirect("dashboard")

    from amocrm.client import AmoApiError, AmoClient

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
    responsible_user_id = request.GET.get("responsible_user_id") or request.POST.get(
        "responsible_user_id"
    )
    limit_companies = int(
        request.GET.get("limit_companies", 250) or request.POST.get("limit_companies", 250)
    )
    offset = int(request.GET.get("offset", 0) or request.POST.get("offset", 0))

    if not responsible_user_id:
        messages.error(request, "Не указан ответственный пользователь.")
        return redirect("settings_amocrm_migrate")

    try:
        # Запускаем dry-run только для контактов
        # КРИТИЧЕСКИ: не запрашиваем задачи и заметки - это слишком тяжело
        result = _amo_migrate_filtered(
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

        messages.error(request, f"Ошибка: {e!s}")
        logger.error("AMOCRM_CONTACTS_DRY_RUN_ERROR: %s", traceback.format_exc())
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

    from amocrm.client import AmoApiError, AmoClient

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
                "id",
                "name",
                "first_name",
                "last_name",
                "responsible_user_id",
                "group_id",
                "created_by",
                "updated_by",
                "created_at",
                "updated_at",
                "is_deleted",
                "phone",
                "email",
                "company_id",
                "closest_task_at",
                "account_id",
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

        messages.error(request, f"Ошибка: {e!s}")
        logger.error("AMOCRM_DEBUG_CONTACTS_ERROR: %s", traceback.format_exc())
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
