# Текущий спринт

## Текущая задача

Live-chat UX Completion — реализация по спецификации `docs/superpowers/specs/2026-04-13-livechat-ux-completion-design.md`.

**Статус:** Plan 1, Plan 2, Plan 3, Plan 4 завершены 2026-04-13. Live-chat UX Completion — все 4 плана закрыты.

## Сделано в этом спринте

**[2026-04-16]** — Аудит и рефакторинг дашборда ✅

- `c27f3fd` Комплексный аудит dashboard: performance, UX, accessibility (32 находки → 18 правок).
- **Performance (P0):** select_related + .only() для assigned_to, company__address, is_urgent — устранено до 48 N+1 запросов. Удалён мёртвый SSE endpoint (блокировал gunicorn workers). dashboard_poll упрощён до `{updated: true/false}` — удалено 170 строк дублированной логики сериализации.
- **UX (P2):** русское склонение даты (`ru_date` фильтр — «среда, 16 апреля 2026»). Hero-статистики стали кликабельными ссылками. Кнопка «+ Задача» в hero. «ХЗ: день/месяц» → «Звонки: день/месяц». Кнопка «показать все» для договоров. confirm() заменён на двойной клик с подсветкой (2.5с timeout).
- **Accessibility (P1):** heading hierarchy (h1+h2), aria-label на hero-секции, touch target 36px для чекбокса.
- **Code quality:** переименованы week_monday/week_sunday → week_range_start/week_range_end. Удалены неиспользуемые импорты (cache, дубль TaskType, StreamingHttpResponse). Все ссылки «Посмотреть все» получили фильтр mine=1.
- Контраст даты и подзаголовка в hero улучшен (#E6F4F3 вместо #B3DEDC).
- Staging задеплоен, Playwright-тест OK.

**[2026-04-16]** — v2 → основной интерфейс, удалены v1 шаблоны ✅

- `2ccc112` Dashboard/Tasks/Companies/Settings всегда рендерят v2 шаблоны.
  Удалены v1 шаблоны: `dashboard.html` (1764 строки), `task_list.html` (2134),
  `company_list.html` (1813), `settings/dashboard.html` (619). Итого −6770 строк.
- Удалены 4 preview view-функции и `/_preview/*` URL-маршруты.
  Удалён `v2_toggle.html` переключатель и его CSS из `v2_styles.html`.
- Удалены 4 тестовых файла preview, обновлены 7 dashboard-тестов под v2 разметку.
- Побочный баг-фикс: template paths `ui/admin/*` → `ui/settings/*` (ошибка
  из URL-рефактора, ломала amocrm_migrate и calls_stats).
- 177 ui тестов OK. Staging задеплоен, все 6 страниц 200.

**[2026-04-15]** — Редизайн Фаза 2 — v2-модалка, SPA-задачи, круглый чекбокс ✅

- `6616287` v2-modal/v2-toast компонент (`templates/ui/_v2/v2_modal.html`):
  backdrop, Esc, click-outside, confirm-on-dirty, auto-wire форм через
  fetch POST. JSON-контракт `{ok:true, toast, close}` или HTML-фрагмент
  с ошибками (422). Toast-стек внизу справа с auto-dismiss 3 сек.
  Глобальные API `window.V2Modal.open/openHtml/close` и `V2Toast.show`.
  Подключён к dashboard_v2, company_list_v2, task_list_v2.
- `6616287` dashboard_v2: убраны hover-кнопки «В работу»/«Выполнено».
  Вместо них круглый чекбокс слева от задачи с подтверждением и
  плавным fade-out перед reload. «Компании без активных задач»
  перенесены выше «На неделе». `seed_demo_data` форсит
  `responsible=user` на contract target компаниях — иначе блок
  «Договоры» оставался пустым у sdm.
- `73572aa` task_create_v2_partial — новый thin view + partial-шаблон.
  GET → HTML формы, POST → JSON / 422. TaskType рендерится плашками
  (цвет + иконка из справочника), без title и RRULE, чекбокс «⚡ Срочно».
  Кнопка «Новая задача» получает `data-v2-modal-open`. Страницы
  подписаны на `v2-modal:saved` → reload.
- `82a33d5` task_view_v2_partial + task_edit_v2_partial — просмотр и
  редактирование задачи в модалке. View-карточка с бейджами,
  секциями полей, кнопками «Редактировать» и «✓ Выполнить». Edit-форма
  с плашками и «Срочно». Клики по строкам задач на дашборде и в
  /tasks/ открывают модалку вместо `/tasks/<id>/`.
- `c20d9a6` /tasks/: цветной dot в строке задачи стал кликабельным
  чекбоксом «выполнить» (hover ring + scale, confirm, POST done,
  reload). Квадратный bulk-чекбокс слева остался для массовых действий.
- dashboard_v2: компактная шапка (padding 16/20, title 18px, stats
  20px value, 10px label). Баннер «Preview редизайна» закрывается
  крестиком, состояние в localStorage.
- URL-рефактор: `/preferences/*` → `/settings/*` (личные настройки),
  старые `/settings/*` админские → `/admin/*`, Django admin
  `/admin/` → `/django-admin/`. Имена `name=` в `path()` сохранены,
  поэтому все `{% url %}` автоматически рендерят новые пути. Правки:
  45 файлов (`backend/ui/urls.py` 79 строк, `backend/crm/urls.py`,
  38 шаблонов, 5 .py с хардкод-путями). Мотив: личные настройки и
  админка в разных URL-пространствах — понятнее пользователю, и
  `/settings/` зарезервирован за тем, что пользователь ожидает там
  увидеть (личные параметры, а не админ-панель приложения).

**[2026-04-15]** — Редизайн Фаза 2 — иконки, масштабирование UI, компактные фильтры ✅

- `f76b139` settings/dashboard_v2: заменены иконки для разделов
  Журнал действий, Импорт, Колонки, Статистика звонков, Кампании,
  Автоматизация, Журнал ошибок — Heroicons solid, ближе к смыслу.
- `75ce571` UiUserPreference.font_scale: диапазон расширен
  0.85–1.30, миграция `0011_uiuserpreference_font_scale_widen`.
  В `.v2` добавлен `zoom: var(--ui-font-scale, 1)` — пропорциональное
  масштабирование всего v2-интерфейса (вариант Б). v2 использует только
  px → с rem-хаком v1 не конфликтует.
- `cb772ac` settings/dashboard_v2: секция «Интерфейс» — 4 пресета
  масштаба (87.5% / 100% / 112.5% / 125%) с live-apply через CSS var
  и AJAX POST на `/preferences/ui/v2-scale/` (новый view
  `preferences_v2_scale`).
- `ff2382f` task_list_v2: компактный фильтр-бар — поле поиска + кнопка
  «Фильтр» с бейджем количества активных + «Сброс». Чипсы активных
  фильтров (статус/исполнитель/период/флаги) со × . Popover со всеми
  полями (select'ы + чекбоксы + «Применить/Отмена»). Закрытие по
  клику вне/Escape. Убран `onchange=submit` — применение только
  по кнопке.
- `0649286` company_list_v2: аналогичный компактный фильтр-бар с
  чипсами и popover — 8 select'ов (статус/сфера/тип договора/регион/
  подразделение/ответственный/task_filter/per_page) + overdue флаг.

**[2026-04-15]** — Редизайн Фаза 2 — подсветка поиска в v2 списке ✅

- `45e32d8` company_list_v2: при активном `?q=...` рендерим
  `c.search_name_html` / `search_inn_html` / `search_address_html`
  (с тегами `<mark>`) и блок «Найдено:» с `search_reasons` — как в v1.
  Закрыт последний визуальный gap поиска между v1 и v2.

**[2026-04-15]** — Редизайн Фаза 2 — настраиваемые колонки + фильтр-чипы ✅

- `20d15c2` company_list_v2: уважаем `ui_cfg.company_list_columns` —
  заголовки/ячейки responsible/branch/region/status/updated_at + inline
  бейджи inn/overdue/spheres показываются только если выбраны в
  `/settings/company-columns/`; grid-template-columns строится динамически.
  Добавлена колонка «Регион».
- `20d15c2` task_list_v2: активные фильтр-чипы над формой (Мои/Сегодня/
  Просрочено/Выполненные/Статус/Исполнитель/поиск/Период) с кликабельным
  × — удаляют ключ из URL и localStorage, редиректят. Визуальная
  синхронизация «Мои» ↔ Исполнитель (disable + opacity) до сабмита.
- 190 ui тестов OK. Staging задеплоен.

**[2026-04-15]** — Редизайн Фаза 2 — important tier (v2 обогащение) ✅

- `c473869` company_list_v2: в ячейке «Название» добавлены ИНН, overdue-бейдж,
  сферы-пилюли с ★ для `is_important`, work_timezone badge (`guess_ru_tz`
  fallback → `tz_now_hhmm` / `tz_label`) — полный паритет с v1 rows.
- `c473869` dashboard_v2: inline-редактирование суммы годового договора
  в карточке «Договоры» — `<input data-inline-input>` + `✓` кнопка,
  POST на `/companies/<id>/inline/` (field=contract_amount),
  визуальная обратная связь ✓/✗.

**[2026-04-15]** — Редизайн Фаза 2 — перенос недостающего функционала (v2 паритет) ✅

После замечания пользователя «Не весь функционал ты перенес, проверяй и анализируй!» — провёл аудит v1 vs v2 (4 parallel Explore-агента), выявил ~40 gaps, закрыл критичные на трёх страницах:

- `1d84432` company_list_v2: экспорт CSV (admin), опция «— Без ответственного —», task_filter (no_tasks/today/tomorrow/…/quarter), per_page 25/50/100/200, сортировка по updated_at (новая колонка «Обновлено»), can_transfer гард на чекбоксах (disabled при отсутствии прав), поменял несуществующее `c.main_phone` на `c.address` (truncatechars:60), bulk preview modal с fetch POST `/companies/bulk-transfer/preview/` — показ allowed/forbidden/companies/old_responsibles, apply_mode=selected|filtered с hidden inputs фильтров.
- `7252b92` task_list_v2: per_page, сортировки по status/created_at/created_by, колонки «Постановщик» + «Создана», task_type_badge + ⚡ в заголовке, inline actions (Редактировать ссылка / В работу form POST / Выполнено form POST с confirm / Удалить form POST с confirm), bulk reschedule — отдельная форма с datetime-local и кнопкой «Перенести» (при `can_bulk_reschedule`), переработка инжекции фильтров + task_ids для обеих bulk-форм.
- `bf94d48` dashboard_v2: бейдж живого времени (work_timezone badge) + описания задач во всех 4 секциях (Новые/Просрочено/Сегодня/Неделя) через `guess_ru_tz` + `tz_now_hhmm` + `tz_label`, AJAX polling `/api/dashboard/poll/` 30с с паузой при скрытой вкладке, индикатор «Обновление…», кнопка «Обновить» в hero, ссылки «ХЗ: день» / «ХЗ: месяц» (при `can_view_cold_call_reports`), inline quick actions (hover-reveal «В работу» / «Выполнено» на карточках задач, AJAX POST на `/tasks/<id>/status/`).

Тесты: `ui.test_company_list_v2_preview` (3), `ui.test_task_list_v2_preview` (3), `ui.test_tasks_views` (26), `ui.test_dashboard_v2_preview` + `ui.test_dashboard` (38) — всё OK. Staging деплой после каждого коммита.

**[2026-04-15]** — Редизайн Фаза 2 Tasks (функциональный паритет с v1) ✅

- `c7723cc` dashboard v2: блок «Запросы на удаление» (РОП/директор),
  индикатор `⚡` is_urgent, футер stale_companies.
- Добавлен templatetag `accounts.templatetags.accounts_extras.full_name`
  («Фамилия Имя» → fallback first/last/username) + 5 unit-тестов.
  Применён в v2 шаблонах там, где выводится ответственный/исполнитель —
  чтобы не путать тёзок в команде.
- `9fec3ad` task_list_v2: реальные фильтры — status select, assignee
  select с `{% regroup %}` по branch, чекбокс-чипы mine/today/overdue/
  show_done (auto-submit), кнопка «Сброс».
- `dad33c3` task_list_v2: sort (сортируемые заголовки title/company/
  due_at/assignee со стрелками ▲▼), date range (date_from/date_to
  auto-submit), bulk reassign panel (sticky sticky, чекбоксы строк,
  групповой select по branch, счётчик выбранных, инжекция фильтров
  в POST), localStorage remember filters (`v2_task_filters_v1`).
- Все 190 ui + 269 ui+accounts тестов OK. Staging задеплоен.

**Следующее:** Фаза 2 Companies (filters sphere/contract/region/branch,
sort headers, bulk transfer), затем Фаза 2 Settings.

**[2026-04-15]** — Редизайн Фаза 2 Companies + Settings ✅

- `a3aac5d` company_list_v2: полный набор фильтров (status/sphere/
  contract_type/region/branch/responsible + overdue chip + Сброс),
  сортируемые заголовки name/responsible/status, bulk transfer
  panel (sticky, чекбоксы строк, select по branch), localStorage
  `v2_company_filters_v1`. Все имена через `|full_name`.
- `e0a8584` settings_v2: счётчики пользователей/подразделений,
  расширенная сводка справочников, security hint «Fernet + rate
  limiting», AmoCRM hint. В views/settings_core.py добавлены
  v2_count_* в контекст только для _preview_v2.
- Все тесты зелёные.

**Фаза 2 завершена для Dashboard/Tasks/Companies/Settings.**

**[2026-04-15]** — Редизайн Фаза 3 (финал) ✅

- `9a19bda` base.html: scoped CSS-блок полирует существующий
  <header> под Notion-стиль (#fff вместо backdrop-blur, бордер
  #E5E7EB, мягкие кнопки r10, градиент лого/аватар #01948E→#0EA5A0,
  бейдж колокольчика с белой обводкой, logout hover → красный).
  Никаких правок DOM/JS — только селекторы по классам/атрибутам.
  Применяется к v1 и v2 одновременно. 190 ui OK.

**Редизайн полностью завершён.**

**[2026-04-15]** — Редизайн Фаза 1 (визуальная полировка v2) ✅

- `2a57b5a` Фаза 1A/1B: фундамент v2 — `templates/ui/_v2/v2_styles.html`
  (дизайн-токены как CSS-переменные, классы v2-card/grid/table/chip/btn/
  banner/hero/toggle/anim), `v2_toggle.html` (плавающий ADMIN-only
  переключатель). Dashboard v2 перерисован как эталон: Heroicons Solid с
  `fill-rule:evenodd`, hero + 4 stat, 12-кол grid на всю ширину `main`,
  fade-анимации. Toggle «к новой версии» добавлен на v1-dashboard.
- `46e1a0c` Фаза 1C/1D/1E: tasks/companies/settings v2 переведены на
  общие стили. Везде Heroicons Solid, grid на всю ширину 1536px, убран
  внутренний `max-width`, staggered fade-анимации. Toggle «к новой
  версии» добавлен на все v1-страницы (task_list, company_list,
  settings/dashboard).
- Инфра-нюанс: staging деплоится через
  `docker compose -f docker-compose.staging.yml up -d --build web`
  (базовый `docker-compose.yml` конфликтует с прод-контейнерами по порту
  8001 на том же VPS).
- Тесты: 190 ui OK на обоих коммитах.

**[2026-04-15]** — Редизайн K1..K6 подготовка ✅

Серия подготовительных коммитов перед редизайном 4 страниц
(Рабочий стол / Задачи / Компании / Админка UI) в Notion-стиле.

- `284366d` K1 `accounts.signals.sync_is_staff_with_role` (post_save):
  автоматическая синхронизация `is_staff` с ролью. 9 тестов.
- `45572f9` K2 templatetag `has_role` / `role_label` в
  `accounts/templatetags/accounts_extras.py` — единая точка проверки
  ролей в шаблонах. 11 тестов. Шаблоны перенесены с прямых сравнений
  `user.role == "..."` на `|has_role:"..."`.
- `e7e09bf` K3 роль TENDERIST (Тендерист): read-only для всего
  кроме задач и уведомлений. Дедицированный baseline в
  `policy/engine.py`, блокировка в `companies/permissions.py`,
  `messenger/selectors.py`, исключение из round-robin. Миграция
  `accounts.0013_add_tenderist_role`. 15 тестов. Переименованы
  подписи ролей: «Директор филиала» → «Директор подразделения»,
  «Руководитель отдела продаж» → «РОП».
- `9c60d1b` K4 Филиал → Подразделение (UI-only): 37 файлов,
  только verbose_name / labels / тексты в шаблонах. Python-идентификаторы
  (Branch/branch/BRANCH_DIRECTOR), миграции, тесты, API-error-messages
  не трогались.
- `8b5aee4` K5 Tailwind токены: `brand.primary` (50..900),
  `brand.accent` (50..900), `crm-neutral` (0..900), семантические
  success/warning/danger/info, `shadow-crm-*`. Старые алиасы
  `brand.teal/orange/dark/soft` оставлены для обратной совместимости.
  fontSize/radius/boxShadow по умолчанию НЕ переопределены —
  чтобы не сдвинуть существующий UI.
- `51b7ca7` K6 dead-code cleanup: 7 неиспользуемых импортов в
  `ui/views/{dashboard,tasks,company_list,company_detail,settings_core,settings_integrations,settings_messenger}.py`.
  Тесты ui: 177 ok.

**Шаги 1..4 редизайна — все 4 preview-страницы готовы:**

- `24ea4be` Шаг 1 Рабочий стол → `/_preview/dashboard-v2/`. Hero с
  4 метриками, карточки (Новые / Просрочено / На сегодня / Неделя /
  Договоры / Компании без задач). Извлечена `_build_dashboard_context`
  для переиспользования. 4 новых теста.
- `5b16171` Шаг 2 Задачи → `/_preview/tasks-v2/`. Тулбар с поиском,
  chip-фильтры, grid-таблица задач. Переключение через `request._preview_v2`
  без дублирования логики фильтров/пагинации. 3 новых теста.
- `b4a5612` Шаг 3 Компании → `/_preview/companies-v2/`. Хедер со
  счётчиками, тулбар фильтров, grid-таблица. 3 новых теста.
- `ddaefe8` Шаг 4 Админка → `/_preview/settings-v2/`. 13 CRM-тайлов +
  3 Live-chat (если MESSENGER_ENABLED). Иконки Heroicons Solid. 3 теста.

**Итого:** 6 подготовительных коммитов (K1..K6) + 4 шага preview v2.
Ни одна существующая страница не изменена. Полный прогон ui: 190 тестов
(было 177 до K-серии → +13 новых). Все preview-страницы доступны только
ADMIN, ручная итерация визуала не мешает основному UI.

**Дальше:** итерация внутри preview-шаблонов по замечаниям пользователя,
затем промо v2 → основные URL (дропнуть v1-шаблоны одним коммитом).

**[2026-04-15]** — Phase 2 hotfixes: P1/P2 из bug-hunt.md ✅

Вторая волна исправлений по `knowledge-base/research/bug-hunt.md`
после первой hardening-серии. Все коммиты деплоены на staging
(`crm-staging.groupprofi.ru`), web healthy, migration 0013 применена.

- `ecefbe0` Observability: пять `except Exception: pass` в
  `companies/signals.py` (P2-7) и один в `audit/service.py:log_event`
  (P2-8) заменены на `logger.exception(...)`; `/notifications/poll/`
  кэшируется per-user на 3с через Redis — схлопывает burst-polling
  от нескольких вкладок (P1-6); в `ui/views/tasks.py` form.errors
  больше не пишется в лог (PII-утечка, P2-12).
- `e118a36` Messenger routing: `send_outbound_webhook` и
  `send_push_notification` — новые Celery-таски с
  `autoretry_for=(Exception,)`, `retry_backoff`, `max_retries=5/3`,
  `acks_late=True`. `messenger/integrations.py` и `messenger/push.py`
  заменили `threading.Thread(daemon=True)` на `.delay()` — payload
  больше не теряется при рестарте gunicorn (P1-7, P1-8).
  `messenger.Contact.clean()` валидирует email (lowercase) и телефон
  (E.164-ish, 7-15 цифр), Widget API нормализует вход через
  `_normalize_contact_email/_phone` — невалидные значения отбрасываются
  в лог, не пишутся в БД (P1-11).
- `880d445` Recurring tasks race (P1-2):
  `UniqueConstraint(parent_recurring_task, due_at)` с условием
  `parent_recurring_task IS NOT NULL` — partial unique index,
  не мешает ручному созданию задач; миграция
  `tasksapp.0013_task_uniq_recurrence_occurrence`. `_process_template`
  оборачивает `Task.objects.create` в savepoint (`transaction.atomic`)
  и ловит `IntegrityError` — второй воркер, если обойдёт redis-lock
  и `select_for_update`, получит конфликт БД и тихо пропустит.
- `0c30357` UI perf:
  `TaskTypeSelectWidget` — вернули `cache.set(..., 300)` (5 мин),
  инвалидация в `post_save`/`post_delete` на `TaskType` (P2-6);
  `templates/ui/base.html` — campaign poll 4s → 15s,
  `pollOnce`/`poll` ставятся на паузу на `visibilitychange`,
  `pollDashboard` аналогично (P2-2, P2-3); `console.log` в
  `base.html` и `company_detail.html` обёрнут в `if (window.DEBUG)` (P2-11).
- `c1febf1` Reports perf (P2-9): `qs.count()` кэшируется в
  переменную, сам проход — через `.iterator(chunk_size=500)` —
  Django стримит CallRequest порциями, не грузит весь queryset в RAM.

**Итого из bug-hunt.md за сессию:** P1-1, P1-2, P1-3, P1-4, P1-5,
P1-6, P1-7, P1-8, P1-9, P1-10, P1-11, P2-1, P2-6, P2-7, P2-8,
P2-9, P2-11, P2-12. Из P1 осталось — ничего (все actionable закрыты).
Из P2: P2-10 (Session scan в settings_core) — не блокирующее.

**[2026-04-15]** — Phase 0/1 hotfixes после аудита 2026-04-14 ✅

Серия hardening-коммитов по результатам полного аудита
(`knowledge-base/synthesis/state-of-project.md`, 203 находки).

- `d48f741` Phase 0 P0: дубль `SecureLoginView.post` удалён;
  widget Origin hijack + fail-closed allowlist +
  `MESSENGER_WIDGET_STRICT_ORIGIN`; `get_client_ip` делегирует в secure
  версию с PROXY_IPS; WS consumers — убраны несуществующие поля
  (`AgentProfile.last_seen_at`, `Contact.session_token`), виджет-сессии
  идут через Redis-кеш; notifications DB-writes в GET-поллинге вынесены
  в celery-beat `generate_contract_reminders` (ежедневно 06:30 MSK);
  удалён `backend/mailer/tasks.py` (721 строка shadowed пакетом);
  Android TokenManager — plaintext JWT fallback убран, fallback-режим
  хранит токены только в памяти.
- `4378f3e` Phase 1 P1: RRULE DoS — `MAX_OCCURRENCES=1000`,
  `MAX_ITERATIONS=100_000` + строгая валидация (`COUNT≤1000`,
  `INTERVAL 1..366`); `MultiFernet` с ротацией через
  `MAILER_FERNET_KEYS_OLD`; prod Gunicorn → gthread 4×8.
- `72a58bc` P0 cleanup: удалён `ui/views_LEGACY_DEAD_CODE.py`
  (12571 строка), `html_to_text` regex исправлен (был сломан `\\`-экранами);
  удалён дубль `MAILER_MAX_CAMPAIGN_RECIPIENTS`; убрано дублирование
  poll `/notifications/poll/` (было 15с+60с, стало одно 30с);
  `LogoutAllView` реально блеклистит все outstanding refresh-токены
  через simplejwt.
- `5874749` Phonebridge rate-limit: DRF ScopedRateThrottle на
  `pull` (120/min), `heartbeat` (30/min), `telemetry` (20/min).
- `e5784ff` Race-protection `generate_recurring_tasks`: redis-lock
  (TTL 15 мин) + `SELECT FOR UPDATE` на каждый шаблон в atomic.

**[2026-04-15]** — Staging hardening (TLS/cookies/policy) ✅
- PolicyConfig staging: `observe_only → enforce` через
  `manage.py set_policy_mode --mode enforce`, login=200, health=200
- Host nginx (`/etc/nginx/sites-enabled/crm-staging`):
  добавлен `Strict-Transport-Security: max-age=31536000; includeSubDomains`
- `/opt/proficrm-staging/.env.staging`:
  `DJANGO_SECURE_SSL_REDIRECT=1`, `SESSION_COOKIE_SECURE=1`,
  `CSRF_COOKIE_SECURE=1`, `SECURE_HSTS_SECONDS=31536000`;
  web recreated, Set-Cookie с флагом `Secure` подтверждён

Осталось из P0 (требует ручного включения / риск для прод):
- P0-22 daphne service в prod docker-compose (WebSocket работает
  только на staging)
- P0-23 Android compileSdk=34 → 35 (Google Play требование с 08.2025)

**[2026-04-13]** — Live-chat Client Context Panel (Plan 4) ✅
- 5 задач выполнено, коммиты `3696406..00fc2a6` (+ docs commit)
- Модель: `Conversation.company` FK (nullable, on_delete=SET_NULL, db_index) → миграция `messenger.0023_conversation_company`
- Автосвязь диалога с компанией по email/phone клиента (нормализация, поиск в `Company/Contact/CompanyPhone/ContactPhone/CompanyEmail/ContactEmail`), срабатывает при создании conversation и при первом заполнении контактов; не перезаписывает уже проставленную вручную связь
- API: `GET /api/messenger/conversations/{id}/context/` — отдаёт блоки `company` (название, responsible, branch, deal'ы, next contract alert), `conversations_history` (последние 10 диалогов клиента), `audit` (transfers + escalations)
- Фронтенд оператора: правая панель с тремя collapsible-блоками «Компания / История диалогов / Аудит», ссылки в карточку компании, ленивая загрузка при выборе диалога
- Тесты: 134/134 messenger + общий прогон `messenger accounts policy notifications companies` = 354/354 OK
- Миграция: `messenger.0023_conversation_company`

**[2026-04-13]** — Live-chat Notifications + Escalation (Plan 3) ✅
- 9 задач выполнено (коммиты `a909afa..3f2355f`)
- Backend: `Conversation.resolution/escalation_level/last_escalated_at` + миграция `0022`; `PolicyConfig.livechat_escalation` JSONField + миграция `policy.0003`; Celery task `escalate_waiting_conversations` (warn/urgent/rop_alert/pool_return, идемпотентна, 30с); расширен `ConversationSerializer` (`resolution` editable, `escalation_level`/`last_escalated_at` read-only) + whitelist в update
- Frontend: resolve modal сохраняет `resolution` (outcome+comment+resolved_at) в PATCH; звук WebAudio beep на новое сообщение; Desktop Notification API; title badge `(N)`; favicon-badge canvas; бейдж `waiting_minutes` в списке диалогов (yellow/orange/red+pulse); `highlightConversation` при эскалационной нотификации; интеграция в `/notifications/poll/` handler
- Тесты: 123/123 messenger зелёные, 8 новых (resolution_field + escalation task); общий прогон `messenger accounts policy notifications` — 214/214 OK
- Миграции: `messenger.0022_conversation_escalation_fields`, `policy.0003_policyconfig_livechat_escalation`

**[2026-04-13]** — Live-chat Operator UX Panel (Plan 2) ✅
- 13 задач выполнено (включая полировку и фикс предсуществующих тестов)
- Коммиты: `cce8224` (last_*_msg_at) → `5c81536` (ui_status) → `ac93be1` (waiting_minutes + escalation_thresholds) → `40ebff0` (CannedResponse.is_quick_button + sort_order) → `2a6df8b`/`3c57dae` (needs-help API + agents filters + branches + code review fixes) → `0ae5ae4` (контекстная CTA + меню ⋯ в шапке) → `4551b0c`/`5bdef2c` (resolve modal + 5s undo toast) → `f6cbf47` (transfer modal с обязательной причиной и cross-branch warning) → `ae48596` (draft autosave в localStorage) → `75abc68` (внутренние заметки — визуальный аффорданс) → `b7c0104` (quick-reply кнопки) → `9dfa761` (needs_help бейдж SOS) → `53e5808` (fix accounts.tests_branch_region tym)
- Модель: `last_customer_msg_at`, `last_agent_msg_at`, `ui_status` property (NEW/WAITING/IN_PROGRESS/CLOSED), `waiting_minutes`, `escalation_thresholds`, `CannedResponse.is_quick_button/sort_order`
- API: `GET /api/conversations/agents/?branch_id=&online=1`, `GET /api/messenger/branches/`, `POST /api/conversations/{id}/needs-help/`, `?quick=1` для canned-responses
- UI: контекстная primary CTA (Взять / Ответить / Завершить / Переоткрыть) + меню ⋯ (Передать / Позвать старшего / Вернуть в очередь); resolve modal с 5s undo; transfer modal с обязательной причиной (через существующий `/transfer/` endpoint); draft autosave 300ms debounce + TTL 7д + лимит 50; визуальный режим внутренней заметки (жёлтая плашка); быстрые ответы (чипы над полем ввода); SOS бейдж "Позван старший" в списке и шапке
- Миграции: `messenger.0020_conversation_msg_timestamps`, `messenger.0021_cannedresponse_quick_button`
- Тесты: все новые Task-тесты зелёные, регрессия messenger 109/109 + accounts 4/4 (fix tym)

**[2026-04-13]** — Live-chat Backend Foundation (Plan 1) ✅
- 12 задач выполнено, коммиты `5f461e7..3a62b66` (12 коммитов)
- Региональная автомаршрутизация: `Conversation.client_region` + `MultiBranchRouter` + `BranchLoadBalancer` + `auto_assign_conversation` post_save сигнал
- Справочник `BranchRegion` (95 записей) + fixture из Положения 2025-2026 + management-команда `load_branch_regions`
- Ролевая видимость `get_visible_conversations(user)` (MANAGER/РОП/BRANCH_DIRECTOR/ADMIN)
- Модель `ConversationTransfer` + endpoint `POST /api/messenger/conversations/{id}/transfer/` с cross-branch аудитом
- Приватные заметки `Message.is_private` (фильтрация в widget SSE/poll/bootstrap, 5 мест)
- Heartbeat endpoint `POST /api/messenger/heartbeat/` + celery-beat `check_offline_operators` (TTL 90 c)
- Флаг эскалации `Conversation.needs_help` / `needs_help_at` (задел для Plan 3)
- Тесты: 120/120 зелёных (`messenger accounts`)
- Staging: миграции `accounts.0010-0012` + `messenger.0016-0019` применены; BranchRegion=95, health=200
- Pre-existing issue в логах celery: Fernet InvalidToken на SMTP (MAILER_FERNET_KEY из Round 2 P0 backlog, не связан с Plan 1)

## Следующее

1. **Полировка Task 6/7 из Plan 2** (nice-to-have, не блокеры): secondary стиль кнопки "Переоткрыть"; подтверждение при Вернуть в очередь; focus trap в модалках.
2. **Round 2 P0 backlog:** test.sh harden, MAILER_FERNET_KEY ротация, RRULE, Policy.

---

## Архив

**[2026-04-06]** — SSE real-time fix + gthread
- Диагностика: 2 sync workers блокировались 3 SSE стримами → 0 воркеров для API
- Переход на gthread (4w×8t=32 потока)
- Исправлено 5 багов: typing инвертирован, stream дублировал сообщения, changed flag, read_up_to, email notify
- Коммиты: `b9e3f8b`, `18deaa7`
- Задеплоено на staging, проверено curl'ом (3 параллельных SSE + health = всё OK)

**[2026-04-06]** — Obsidian wiki + система документации
- Создана структура `docs/wiki/` (21 файл, 5 разделов)
- Создана система `CLAUDE.md` + `docs/architecture.md` + `docs/decisions.md` + `docs/problems-solved.md`
- Claude Code memory обновлена

**[2026-04-05]** — Round 4 production hardening
- operator-panel.js: утечка listeners, XSS в date separator
- merge-contacts: авторизация + UUID validation
- Serializers: `__all__` → explicit fields
- Widget: destroy(), CSS autoload, CORS split
- Коммиты: `eeb51ac`, `27131ce`, `34c19cb`, `50f1efe`, `5a88c6e`, `c024e71` и др.

**[2026-04-04-05]** — Widget на внешнем сайте
- Тестирование на vm-f841f9cb.na4u.ru/chat-test.html
- Решены CORS, CSS autoload, WidgetSession, Inbox branch проблемы
- Inbox #8 создан и работает

**[2026-04-06]** — Комплексное тестирование live-chat (Browser MCP)

Проведено сквозное тестирование с Playwright Browser MCP на staging.

**Результаты по компонентам:**

| Компонент | Статус | Детали |
|-----------|--------|--------|
| Staging health | OK | Все 7 контейнеров UP, celery unhealthy (но работает) |
| Widget загрузка | OK | Виджет загружается на `vm-f841f9cb.na4u.ru/chat-test.html`, CSS autoload работает |
| Prechat-форма | OK | Имя, Email, Телефон, согласие. Кнопка disabled до чекбокса |
| Отправка из виджета | OK | Сообщение доставлено, ✓ отображается, время корректное |
| Оператор-панель | OK | Сообщение видно, диалог в списке, контакт/детали отображаются |
| Auto-reply | OK | "Здравствуйте! Менеджер скоро подключится." — приходит |
| Ответ оператора | OK | Отправляется из панели, msg сохраняется в БД |
| CORS preflight | OK | OPTIONS → 204, nginx обрабатывает корректно |
| Campaigns API | OK | 200, пустой массив (нет активных кампаний) |
| SSE подключение | OK | Widget подключается к `/api/widget/stream/`, reconnect ~25с |
| **SSE доставка** | **OK** | РЕШЕНО: тройная дедупликация + host nginx buffering. Real-time доставка подтверждена |
| JS API | OK | `window.ProfiMessenger` доступен (open/close/toggle/destroy/isOpen) |

**Найденные и исправленные баги:**

1. **P0 — SSE real-time доставка — РЕШЕНО**
   - Корневая причина: тройная дедупликация в `widget.js` — `receivedMessageIds.add()` вызывался ДО `addMessageToUI()`, которая проверяла тот же Set
   - Три места: SSE handler, render() savedMessages, render() initialMessages
   - Дополнительно: host nginx без `proxy_buffering off` для SSE
   - Ложный след: gthread буферизация (curl доказал что стрим инкрементальный)
   - **Коммиты**: `b26fadb`, `6c3ba20`

2. **P1 — Роль admin не может отвечать — РЕШЕНО**
   - Замена `role == MANAGER` на `is_superuser or role in (MANAGER, ADMIN)` в 3 местах
   - **Файлы**: `messenger_panel.py:51`, `api.py:217`, `api.py:559`

3. **P2 — Auto-reply не отображается в виджете при первом подключении**
   - Причина: `since_id` из localStorage уже больше id auto-reply

## Следующий шаг

1. **Typing-индикаторы** — протестировать (SSE работает)
2. **Нагрузочное тестирование** — несколько одновременных виджетов
3. **P2 auto-reply** — пересмотреть since_id при первом подключении
4. **Деплой на прод** — после полного QA

## Стоп-точка

Сессия: SSE P0 баг полностью решён и подтверждён тестами через Playwright Browser MCP. Real-time доставка работает. P1 admin-reply тоже исправлен. HEAD: `6c3ba20`.
