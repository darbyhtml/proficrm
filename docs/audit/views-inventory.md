# Инвентаризация views CRM ПРОФИ
_Снапшот: 2026-04-20. Wave 0.1._

Полный список всех Django view-функций и классов, URL-маппинг, декораторы, модели. Строится как input для Wave 2 (Policy ENFORCE — все mutating endpoints должны иметь `@policy_required`) и Wave 9 (разбиение god-views).

---

## Сводка

| Метрика | Значение |
|---------|----------|
| Views-файлов (Python) | 25 (ui/views: 14, mailer/views: 11 подмодулей, messenger: 3, accounts: 2, phonebridge: 1, notifications: 1, crm: 1, messenger widget: 1, company_detail_v3: 1) |
| Всего view-функций (ui + mailer + notifications + accounts + messenger SSR) | ≈ 213 |
| Всего view-классов (CBV/APIView/ViewSet) | 23 |
| DRF `@action`-методов (nested endpoints) | ≈ 20 |
| HTML-views (render/redirect/TemplateResponse) | ≈ 150 |
| JSON-views (JsonResponse/DRF Response) | ≈ 75 |
| Stream/SSE views | 3 (`widget_stream`, `ConversationViewSet.stream`, `ConversationViewSet.notifications_stream`) |
| WebSocket consumers | 2 (`OperatorConsumer`, `WidgetConsumer` в `messenger/consumers.py`) |
| God-views (>200 LOC) | 20 (см. конец файла) |
| Views с `@csrf_exempt` | **1** (`unsubscribe` — оправдан: List-Unsubscribe-Post от email-клиентов) |
| Views без `@login_required` (публичные) | 12 (все widget-API, `magic_link_login`, `unsubscribe`, `robots_txt`, `security_txt`, `health_check`, `handler404`, `handler500`, `metrics_endpoint`, `sw_push_js`, JWT-views, widget_test_page) |
| **Mutating views БЕЗ `@policy_required`** | **≈ 75** (главный артефакт для Wave 2, см. в конце файла) |

### Фактическая статистика LOC по файлам views

| Файл | LOC | Views |
|------|-----|-------|
| `backend/ui/views/company_detail.py` | 2698 | 32 |
| `backend/ui/views/tasks.py` | 2215 | 13 |
| `backend/messenger/widget_api.py` | 1721 | 10 |
| `backend/ui/views/settings_core.py` | 1581 | 34 |
| `backend/ui/views/company_list.py` | 1496 | 8 |
| `backend/ui/views/settings_integrations.py` | 1495 | 15 |
| `backend/messenger/api.py` | 1349 | 11 (классы) + 3 FBV + 20 actions |
| `backend/ui/views/dashboard.py` | 1290 | 17 |
| `backend/ui/views/settings_messenger.py` | 1069 | 15 |
| `backend/phonebridge/api.py` | 1059 | 14 классов (APIView) |
| `backend/mailer/views/campaigns/list_detail.py` | 689 | 2 |
| `backend/ui/views/reports.py` | 595 | 3 |
| `backend/mailer/views/recipients.py` | 557 | 8 |
| `backend/mailer/views/settings.py` | 450 | 4 |
| `backend/ui/views/company_detail_v3.py` | 392 | 2 |
| `backend/mailer/views/sending.py` | 385 | 4 |
| `backend/ui/views/settings_mail.py` | 342 | 5 |
| `backend/accounts/views.py` | 340 | 2 (SecureLoginView + magic_link_login) |
| `backend/companies/api.py` | 305 | 3 ViewSet |
| `backend/crm/views.py` | 258 | 7 |
| `backend/tasksapp/api.py` | 255 | 2 ViewSet |
| `backend/messenger/views.py` | 177 | 2 |
| `backend/mailer/views/campaigns/files.py` | 163 | 5 |
| `backend/mailer/views/campaigns/crud.py` | 162 | 4 |
| `backend/mailer/views/polling.py` | 216 | 2 |
| `backend/accounts/jwt_views.py` | 151 | 2 |
| `backend/mailer/views/unsubscribe.py` | 148 | 4 |
| `backend/ui/views/settings_mobile_apps.py` | 145 | 3 |
| `backend/ui/views/mobile.py` | 126 | 3 |
| `backend/mailer/views/campaigns/templates_views.py` | 97 | 4 |
| `backend/notifications/views.py` | 239 | 6 |
| `backend/ui/views/messenger_panel.py` | 251 | 2 |
| `backend/ui/views/analytics_v2.py` | 55 | 1 |

---

## Легенда декораторов

- `@login_required` — стандартный Django-декоратор, требует авторизации.
- `@policy_required(resource_type=..., resource=...)` — собственный декоратор из `policy.decorators` (RBAC/ABAC-политика).
- `@require_can_view_company`, `@require_can_view_note_company` — из `companies.decorators`, проверяют доступ на уровне объекта.
- `@require_POST` / `@require_http_methods` — Django-декораторы метода.
- `@csrf_exempt` — **красный флаг**, присутствует только в одном месте.
- `@transaction.atomic` — транзакция.
- `@xframe_options_exempt` — отключение X-Frame-Options (widget_test_page).
- DRF: `@api_view`, `@authentication_classes`, `@permission_classes`, `@throttle_classes`.
- `PolicyPermission` (из `policy.drf`) — для DRF ViewSets, проверяет policy на каждое действие.
- `require_admin(user)` внутри тела — ручная проверка роли.
- `enforce(user=request.user, resource_type=..., resource=...)` — ручной вызов policy-engine внутри тела (эквивалент `@policy_required`, но не декоратор).

---

## app: crm (корневые)

### `backend/crm/views.py` (258 LOC, 7 views)

#### `handler404(request, exception)`
- **URL:** (handler)
- **Return:** HTML
- **Decorators:** —
- **LOC:** ~4
- **Complexity:** ~1
- **Notes:** обработчик 404, настройка — `handler404 = "crm.views.handler404"`.

#### `handler500(request)`
- **URL:** (handler)
- **Return:** HTML
- **Decorators:** —
- **LOC:** ~4
- **Complexity:** ~1

#### `robots_txt(request)`
- **URL:** GET `/robots.txt`
- **Return:** HTML (plain text)
- **Decorators:** — (публичный)
- **LOC:** 9
- **Complexity:** ~1

#### `security_txt(request)`
- **URL:** GET `/.well-known/security.txt`
- **Return:** HTML (plain text)
- **Decorators:** — (публичный)
- **LOC:** ~25
- **Complexity:** ~1

#### `sw_push_js(request)`
- **URL:** GET `/sw-push.js`
- **Return:** JS (HttpResponse)
- **Decorators:** — (публичный)
- **LOC:** ~18
- **Complexity:** ~1
- **Notes:** Service Worker для push-уведомлений.

#### `metrics_endpoint(request)`
- **URL:** GET `/metrics`
- **Return:** plain text (Prometheus format)
- **Decorators:** — (предположительно IP-whitelist или token-auth внутри)
- **LOC:** ~105
- **Complexity:** ~15
- **Notes:** ⚠ Метрики для Prometheus — публичный. Проверить наличие защиты (IP allowlist?).

#### `health_check(request)`
- **URL:** GET `/health/`
- **Return:** JSON (JsonResponse)
- **Decorators:** — (публичный)
- **LOC:** ~70
- **Complexity:** ~10
- **Models:** Company (проверка БД через query)
- **Notes:** health-check для K8s/monitoring.

---

## app: accounts

### `backend/accounts/views.py` (340 LOC, 2 views)

#### `SecureLoginView(auth_views.LoginView)`
- **URL:** GET/POST `/login/`
- **Return:** HTML (render)
- **Decorators:** — (публичный, CBV)
- **LOC:** ~200
- **Complexity:** ~25
- **Models:** `MagicLinkToken`, `User`, `ActivityEvent`
- **Notes:** Кастомный LoginView с защитой от брутфорса (rate limiting по IP, lockout по username). Поддерживает два типа входа: `access_key` и `password` (только для ADMIN). **Публичный — это ОК.** Обязательные фичи: `is_ip_rate_limited`, `is_user_locked_out`, логирование через `log_event`.

#### `magic_link_login(request, token)`
- **URL:** GET `/auth/magic/<str:token>/`
- **Return:** HTML (render/redirect)
- **Decorators:** `@require_http_methods(["GET"])`
- **LOC:** ~105
- **Complexity:** ~12
- **Models:** `MagicLinkToken`, `ActivityEvent`
- **Notes:** Вход по одноразовой ссылке. Публичный — это ОК.

### `backend/accounts/jwt_views.py` (151 LOC, 2 views)

#### `SecureTokenObtainPairView(TokenObtainPairView)`
- **URL:** POST `/api/token/`, POST `/api/v1/token/`
- **Return:** JSON (DRF Response)
- **Decorators:** — (публичный, CBV на базе simplejwt)
- **LOC:** 69
- **Complexity:** ~10
- **Models:** `User`, `ActivityEvent`
- **Notes:** Защита от брутфорса. Публичный — это ОК.

#### `LoggedTokenRefreshView(TokenRefreshView)`
- **URL:** POST `/api/token/refresh/`, POST `/api/v1/token/refresh/`
- **Return:** JSON
- **Decorators:** — (публичный)
- **LOC:** 52
- **Complexity:** ~8
- **Notes:** Обёртка над `TokenRefreshView` с логированием. Публичный — это ОК.

---

## app: ui (SSR + JSON-endpoints фронтенда)

### `backend/ui/views/dashboard.py` (1290 LOC, 17 views + 11 helpers)

#### `view_as_update(request)`
- **URL:** POST `/admin/view-as/`
- **Return:** HTML (redirect) или JSON
- **Decorators:** `@login_required`
- **LOC:** 82
- **Complexity:** ~15
- **Models:** `User`, `Branch`, `ActivityEvent`
- **Notes:** ⚠ **MUTATING без `@policy_required`**. Админ переключается "под роль". Внутри есть ручная проверка `user.is_admin`. Требует policy.

#### `view_as_reset(request)`
- **URL:** POST `/admin/view-as/reset/`
- **Return:** HTML (redirect)
- **Decorators:** `@login_required`
- **LOC:** 45
- **Complexity:** ~6
- **Notes:** ⚠ **MUTATING без `@policy_required`**.

#### `dashboard(request)`
- **URL:** GET `/`
- **Return:** HTML (render)
- **Decorators:** `@login_required`, `@policy_required(page, ui:dashboard)`
- **LOC:** 8 (делегирует `_build_dashboard_context`)
- **Complexity:** ~1
- **Models:** `User`, `Task`, `Company`, `Notification`, `CompanyDeletionRequest`

#### `dashboard_poll(request)`
- **URL:** GET `/api/dashboard/poll/`
- **Return:** JSON (JsonResponse)
- **Decorators:** `@login_required`, `@policy_required(action, ui:dashboard)`
- **LOC:** 63
- **Complexity:** ~10
- **Models:** `Task`, `Company`

#### `analytics(request)`
- **URL:** GET `/analytics/`
- **Return:** HTML
- **Decorators:** `@login_required`, `@policy_required(page, ui:analytics)`
- **LOC:** 129
- **Complexity:** ~20
- **Models:** `User`, `Task`, `Company`, `ActivityEvent`

#### `help_page(request)`
- **URL:** GET `/help/`
- **Return:** HTML
- **Decorators:** `@login_required`, `@policy_required(page, ui:help)`
- **LOC:** 5
- **Complexity:** ~1

#### `preferences(request)`
- **URL:** GET `/settings/`
- **Return:** HTML
- **Decorators:** `@login_required`, `@policy_required(page, ui:preferences)`
- **LOC:** 32
- **Complexity:** ~4
- **Models:** `UiUserPreference`

#### `preferences_ui(request)`
- **URL:** GET/POST `/settings/ui/`
- **Return:** HTML
- **Decorators:** `@login_required`, `@policy_required(page, ui:preferences)`
- **LOC:** 33
- **Complexity:** ~5
- **Models:** `UiUserPreference`

#### `preferences_company_detail_view_mode(request)`
- **URL:** POST `/settings/ui/company-detail-view-mode/`
- **Return:** JSON
- **Decorators:** `@login_required`, `@policy_required(action, ui:preferences)`
- **LOC:** 28
- **Complexity:** ~4
- **Models:** `UiUserPreference`

#### `preferences_v2_scale(request)`
- **URL:** POST `/settings/ui/v2-scale/`
- **Return:** JSON
- **Decorators:** `@login_required`, `@policy_required(action, ui:preferences)`
- **LOC:** 36
- **Complexity:** ~6
- **Models:** `UiUserPreference`

#### `preferences_mail(request)`
- **URL:** GET `/settings/mail/`
- **Return:** HTML
- **Decorators:** `@login_required`, `@policy_required(page, ui:preferences)`
- **LOC:** 8
- **Complexity:** ~1

#### `preferences_profile(request)`
- **URL:** GET/POST `/settings/profile/`
- **Return:** HTML
- **Decorators:** `@login_required`, `@policy_required(action, ui:preferences)`
- **LOC:** 19
- **Complexity:** ~3
- **Models:** `User`

#### `preferences_password(request)`
- **URL:** GET/POST `/settings/password/`
- **Return:** HTML
- **Decorators:** `@login_required`, `@policy_required(action, ui:preferences)`
- **LOC:** 26
- **Complexity:** ~5
- **Models:** `User`

#### `preferences_absence_create(request)`
- **URL:** POST `/settings/absence/create/`
- **Return:** HTML
- **Decorators:** `@login_required`, `@policy_required(action, ui:preferences)`
- **LOC:** 66
- **Complexity:** ~10

#### `preferences_absence_delete(request, absence_id)`
- **URL:** POST `/settings/absence/<int:absence_id>/delete/`
- **Return:** HTML
- **Decorators:** `@login_required`, `@policy_required(action, ui:preferences)`
- **LOC:** 29
- **Complexity:** ~4

#### `preferences_mail_signature(request)`
- **URL:** GET/POST `/settings/mail-signature/`
- **Return:** HTML
- **Decorators:** `@login_required`, `@policy_required(action, ui:preferences)`
- **LOC:** 28
- **Complexity:** ~4

#### `preferences_avatar_upload(request)`
- **URL:** POST `/settings/avatar/upload/`
- **Return:** HTML
- **Decorators:** `@login_required`, `@policy_required(action, ui:preferences)`
- **LOC:** 58
- **Complexity:** ~8
- **Models:** `User`

#### `preferences_avatar_delete(request)`
- **URL:** POST `/settings/avatar/delete/`
- **Return:** HTML
- **Decorators:** `@login_required`, `@policy_required(action, ui:preferences)`
- **LOC:** 21
- **Complexity:** ~3

#### `preferences_table_settings(request)`
- **URL:** GET/POST `/settings/table-settings/`
- **Return:** HTML
- **Decorators:** `@login_required`, `@policy_required(page, ui:preferences)`
- **LOC:** 34
- **Complexity:** ~5

#### `analytics_user(request, user_id)`
- **URL:** GET `/analytics/users/<int:user_id>/`
- **Return:** HTML
- **Decorators:** `@login_required`, `@policy_required(page, ui:analytics)`
- **LOC:** ~145
- **Complexity:** ~20
- **Models:** `User`, `Task`, `Company`

### `backend/ui/views/reports.py` (595 LOC, 3 views + helpers)

#### `cold_calls_report_day(request)`
- **URL:** GET `/reports/cold-calls/day/`
- **Return:** HTML
- **Decorators:** `@login_required`, `@policy_required(page, ui:dashboard)`
- **LOC:** 221
- **Complexity:** ~35
- **Models:** `User`, `Company`, `Contact`, `CompanyPhone`, `ContactPhone`, `CallRequest`, `Branch`
- **Notes:** Large report. Candidate for service extraction.

#### `cold_calls_report_month(request)`
- **URL:** GET `/reports/cold-calls/month/`
- **Return:** HTML
- **Decorators:** `@login_required`, `@policy_required(page, ui:dashboard)`
- **LOC:** 246
- **Complexity:** ~35
- **Models:** `Company`, `Contact`, `CallRequest`, `Branch`
- **Notes:** Large report. Candidate for service extraction.

#### `cold_calls_report_last_7_days(request)`
- **URL:** GET `/reports/cold-calls/last-7-days/`
- **Return:** JSON
- **Decorators:** `@login_required`, `@policy_required(page, ui:dashboard)`
- **LOC:** 48
- **Complexity:** ~8
- **Models:** `CallRequest`

### `backend/ui/views/company_list.py` (1496 LOC, 8 views)

#### `company_list(request)`
- **URL:** GET `/companies/`
- **Return:** HTML
- **Decorators:** `@login_required`, `@policy_required(page, ui:companies:list)`
- **LOC:** 256
- **Complexity:** ~40
- **Models:** `Company`, `User`, `CompanyStatus`, `ContractType`, `Branch`
- **Notes:** God-view. Огромный сбор фильтров, сортировок, пагинации. Кандидат на Wave 9.

#### `company_list_ajax(request)`
- **URL:** GET `/companies/ajax/`
- **Return:** JSON
- **Decorators:** `@login_required`, `@policy_required(page, ui:companies:list)`
- **LOC:** 210
- **Complexity:** ~30
- **Models:** `Company`

#### `company_bulk_transfer_preview(request)`
- **URL:** POST `/companies/bulk-transfer/preview/`
- **Return:** JSON
- **Decorators:** `@login_required`, `@policy_required(action, ui:companies:bulk_transfer)`
- **LOC:** 126
- **Complexity:** ~18
- **Models:** `Company`, `User`

#### `company_bulk_transfer(request)`
- **URL:** POST `/companies/bulk-transfer/`
- **Return:** HTML (redirect)
- **Decorators:** `@login_required`, `@policy_required(action, ui:companies:bulk_transfer)`, `@transaction.atomic`
- **LOC:** 261
- **Complexity:** ~40
- **Models:** `Company`, `User`, `ActivityEvent`

#### `company_export(request)`
- **URL:** GET `/companies/export/`
- **Return:** HTML / file (StreamingHttpResponse / HttpResponse with CSV)
- **Decorators:** `@login_required`, `@policy_required(action, ui:companies:export)`
- **LOC:** 257
- **Complexity:** ~30
- **Models:** `Company`, `Contact`, `CompanyPhone`, `CompanyEmail`

#### `company_create(request)`
- **URL:** GET/POST `/companies/new/`
- **Return:** HTML (redirect)
- **Decorators:** `@login_required`, `@policy_required(action, ui:companies:create)`
- **LOC:** 149
- **Complexity:** ~22
- **Models:** `Company`, `Contact`, `CompanyPhone`, `CompanyEmail`, `ActivityEvent`

#### `company_autocomplete(request)`
- **URL:** GET `/companies/autocomplete/`
- **Return:** JSON
- **Decorators:** `@login_required`, `@policy_required(action, ui:companies:autocomplete)`
- **LOC:** 120
- **Complexity:** ~18
- **Models:** `Company`, `CompanySearchIndex`

#### `company_duplicates(request)`
- **URL:** GET `/companies/duplicates/`
- **Return:** HTML
- **Decorators:** `@login_required`, `@policy_required(action, ui:companies:duplicates)`
- **LOC:** 55
- **Complexity:** ~8
- **Models:** `Company`

### `backend/ui/views/company_detail.py` (2698 LOC, 32 views)

#### `company_detail(request, company_id)`
- **URL:** GET `/companies/<uuid:company_id>/`
- **Return:** HTML
- **Decorators:** `@login_required`, `@policy_required(page, ui:companies:detail)`, `@require_can_view_company`
- **LOC:** 243
- **Complexity:** ~45
- **Models:** `Company`, `Contact`, `CompanyPhone`, `CompanyEmail`, `CompanyDeal`, `CompanyNote`, `Task`, `CallRequest`, `CompanyDeletionRequest`, `ActivityEvent`, `PhoneDevice`, `User`
- **Notes:** God-view (243 LOC). Ядро карточки компании. Использует `build_company_timeline` (Phase 1 refactor уже сделан частично, но view всё равно большой).

#### `company_tasks_history(request, company_id)`
- **URL:** GET `/companies/<uuid:company_id>/tasks/history/`
- **Return:** HTML
- **Decorators:** `@login_required`, `@policy_required(page, ui:companies:detail)`, `@require_can_view_company`
- **LOC:** 29
- **Complexity:** ~4
- **Models:** `Task`

#### `company_delete_request_create(request, company_id)`
- **URL:** POST `/companies/<uuid:company_id>/delete-request/`
- **Return:** HTML (redirect)
- **Decorators:** `@login_required`, `@policy_required(action, ui:companies:delete_request:create)`, `@require_can_view_company`
- **LOC:** 64
- **Complexity:** ~10
- **Models:** `CompanyDeletionRequest`, `Notification`

#### `company_delete_request_cancel(request, company_id, req_id)`
- **URL:** POST `/companies/<uuid:company_id>/delete-request/<int:req_id>/cancel/`
- **Return:** HTML (redirect)
- **Decorators:** `@login_required`, `@policy_required(action, ui:companies:delete_request:cancel)`, `@require_can_view_company`
- **LOC:** 49
- **Complexity:** ~8

#### `company_delete_request_approve(request, company_id, req_id)`
- **URL:** POST `/companies/<uuid:company_id>/delete-request/<int:req_id>/approve/`
- **Return:** HTML (redirect)
- **Decorators:** `@login_required`, `@policy_required(action, ui:companies:delete_request:approve)`, `@require_can_view_company`
- **LOC:** 56
- **Complexity:** ~10
- **Models:** `Company`, `CompanyDeletionRequest`, `ActivityEvent`

#### `company_delete_direct(request, company_id)`
- **URL:** POST `/companies/<uuid:company_id>/delete/`
- **Return:** HTML (redirect)
- **Decorators:** `@login_required`, `@policy_required(action, ui:companies:delete)`, `@require_can_view_company`
- **LOC:** 35
- **Complexity:** ~5

#### `company_contract_update(request, company_id)`
- **URL:** POST `/companies/<uuid:company_id>/contract/update/`
- **Return:** JSON
- **Decorators:** `@login_required`, `@policy_required(action, ui:companies:contract:update)`, `@require_can_view_company`
- **LOC:** 47
- **Complexity:** ~6
- **Models:** `Company`, `ContractType`

#### `company_cold_call_toggle(request, company_id)`
- **URL:** POST `/companies/<uuid:company_id>/cold-call/toggle/`
- **Return:** JSON
- **Decorators:** `@login_required`, `@require_can_view_company`
- **LOC:** 78
- **Complexity:** ~12
- **Models:** `Company`, `CompanyPhone`
- **Notes:** ⚠ **НЕТ `@policy_required`** — только `@require_can_view_company`. Требует Wave 2.

#### `contact_cold_call_toggle(request, contact_id)`
- **URL:** POST `/contacts/<uuid:contact_id>/cold-call/toggle/`
- **Return:** JSON
- **Decorators:** `@login_required`, `@policy_required(action, ui:companies:cold_call:toggle)`
- **LOC:** 74
- **Complexity:** ~12
- **Models:** `Contact`, `ContactPhone`, `Company`

#### `company_cold_call_reset(request, company_id)`
- **URL:** POST `/companies/<uuid:company_id>/cold-call/reset/`
- **Return:** JSON
- **Decorators:** `@login_required`, `@require_can_view_company`
- **LOC:** 57
- **Complexity:** ~10
- **Notes:** ⚠ **НЕТ `@policy_required`**.

#### `contact_cold_call_reset(request, contact_id)`
- **URL:** POST `/contacts/<uuid:contact_id>/cold-call/reset/`
- **Return:** JSON
- **Decorators:** `@login_required`, `@policy_required(action, ui:companies:cold_call:reset)`
- **LOC:** 61
- **Complexity:** ~10

#### `contact_phone_cold_call_toggle(request, contact_phone_id)`
- **URL:** POST `/contact-phones/<int:contact_phone_id>/cold-call/toggle/`
- **Return:** JSON
- **Decorators:** `@login_required`, `@policy_required(action, ui:companies:cold_call:toggle)`
- **LOC:** 86
- **Complexity:** ~14
- **Models:** `ContactPhone`, `Contact`, `Company`

#### `contact_phone_cold_call_reset(request, contact_phone_id)`
- **URL:** POST `/contact-phones/<int:contact_phone_id>/cold-call/reset/`
- **Return:** JSON
- **Decorators:** `@login_required`, `@policy_required(action, ui:companies:cold_call:reset)`
- **LOC:** 64
- **Complexity:** ~10

#### `company_phone_cold_call_toggle(request, company_phone_id)`
- **URL:** POST `/company-phones/<int:company_phone_id>/cold-call/toggle/`
- **Return:** JSON
- **Decorators:** `@login_required`, `@policy_required(action, ui:companies:cold_call:toggle)`
- **LOC:** 82
- **Complexity:** ~14
- **Models:** `CompanyPhone`, `Company`

#### `company_phone_cold_call_reset(request, company_phone_id)`
- **URL:** POST `/company-phones/<int:company_phone_id>/cold-call/reset/`
- **Return:** JSON
- **Decorators:** `@login_required`, `@policy_required(action, ui:companies:cold_call:reset)`
- **LOC:** 58
- **Complexity:** ~10

#### `company_main_phone_update(request, company_id)`
- **URL:** POST `/companies/<uuid:company_id>/main-phone/update/`
- **Return:** JSON
- **Decorators:** `@login_required`, `@policy_required(action, ui:companies:update)`, `@require_can_view_company`
- **LOC:** 54
- **Complexity:** ~8

#### `company_phone_value_update(request, company_phone_id)`
- **URL:** POST `/company-phones/<int:company_phone_id>/update/`
- **Return:** JSON
- **Decorators:** `@login_required`, `@policy_required(action, ui:companies:update)`
- **LOC:** 52
- **Complexity:** ~8

#### `company_phone_delete(request, company_phone_id)`
- **URL:** POST `/company-phones/<int:company_phone_id>/delete/`
- **Return:** JSON
- **Decorators:** `@login_required`, `@policy_required(action, ui:companies:update)`
- **LOC:** 30
- **Complexity:** ~4

#### `company_phone_create(request, company_id)`
- **URL:** POST `/companies/<uuid:company_id>/phones/create/`
- **Return:** JSON
- **Decorators:** `@login_required`, `@policy_required(action, ui:companies:update)`, `@require_can_view_company`
- **LOC:** 67
- **Complexity:** ~10
- **Models:** `Company`, `CompanyPhone`
- **Services:** `validate_phone_strict`, `check_phone_duplicate`, `validate_phone_comment`

#### `company_main_email_update(request, company_id)`
- **URL:** POST `/companies/<uuid:company_id>/main-email/update/`
- **Return:** JSON
- **Decorators:** `@login_required`, `@policy_required(action, ui:companies:update)`, `@require_can_view_company`
- **LOC:** 48
- **Complexity:** ~7

#### `company_email_value_update(request, company_email_id)`
- **URL:** POST `/company-emails/<int:company_email_id>/update/`
- **Return:** JSON
- **Decorators:** `@login_required`, `@policy_required(action, ui:companies:update)`
- **LOC:** 46
- **Complexity:** ~7

#### `company_main_phone_comment_update(request, company_id)`
- **URL:** POST `/companies/<uuid:company_id>/main-phone/comment/`
- **Return:** JSON
- **Decorators:** `@login_required`, `@policy_required(action, ui:companies:update)`, `@require_can_view_company`
- **LOC:** 44
- **Complexity:** ~6

#### `company_phone_comment_update(request, company_phone_id)`
- **URL:** POST `/company-phones/<int:company_phone_id>/comment/`
- **Return:** JSON
- **Decorators:** `@login_required`, `@policy_required(action, ui:companies:update)`
- **LOC:** 44
- **Complexity:** ~6

#### `contact_phone_comment_update(request, contact_phone_id)`
- **URL:** POST `/contact-phones/<int:contact_phone_id>/comment/`
- **Return:** JSON
- **Decorators:** `@login_required`, `@policy_required(action, ui:companies:update)`
- **LOC:** 51
- **Complexity:** ~7

#### `company_note_pin_toggle(request, company_id, note_id)`
- **URL:** POST `/companies/<uuid:company_id>/notes/<int:note_id>/pin/`
- **Return:** JSON
- **Decorators:** `@login_required`, `@policy_required(action, ui:companies:update)`, `@require_can_view_company`
- **LOC:** 50
- **Complexity:** ~7
- **Models:** `CompanyNote`

#### `company_note_attachment_open(request, company_id, note_id)`
- **URL:** GET `/companies/<uuid:company_id>/notes/<int:note_id>/file/open/`
- **Return:** FileResponse
- **Decorators:** `@login_required`, `@policy_required(page, ui:companies:detail)`, `@require_can_view_note_company`
- **LOC:** 25
- **Complexity:** ~4
- **Models:** `CompanyNote`, `CompanyNoteAttachment`

#### `company_note_attachment_by_id_open(request, company_id, note_id, attachment_id)`
- **URL:** GET `/companies/<uuid:company_id>/notes/<int:note_id>/attachments/<int:attachment_id>/open/`
- **Return:** FileResponse
- **Decorators:** `@login_required`, `@policy_required(page, ui:companies:detail)`, `@require_can_view_note_company`
- **LOC:** 22
- **Complexity:** ~3

#### `company_note_attachment_by_id_download(request, company_id, note_id, attachment_id)`
- **URL:** GET `/companies/<uuid:company_id>/notes/<int:note_id>/attachments/<int:attachment_id>/download/`
- **Return:** FileResponse
- **Decorators:** `@login_required`, `@policy_required(page, ui:companies:detail)`, `@require_can_view_note_company`
- **LOC:** 22
- **Complexity:** ~3

#### `company_note_attachment_download(request, company_id, note_id)`
- **URL:** GET `/companies/<uuid:company_id>/notes/<int:note_id>/file/download/`
- **Return:** FileResponse
- **Decorators:** `@login_required`, `@policy_required(page, ui:companies:detail)`, `@require_can_view_note_company`
- **LOC:** 25
- **Complexity:** ~4

#### `company_edit(request, company_id)`
- **URL:** GET/POST `/companies/<uuid:company_id>/edit/`
- **Return:** HTML (redirect)
- **Decorators:** `@login_required`, `@policy_required(action, ui:companies:update)`, `@require_can_view_company`
- **LOC:** 137
- **Complexity:** ~20
- **Models:** `Company`, `Contact`, `CompanyPhone`, `CompanyEmail`, `ContactPhone`, `ContactEmailFormSet`, `ActivityEvent`

#### `company_transfer(request, company_id)`
- **URL:** POST `/companies/<uuid:company_id>/transfer/`
- **Return:** HTML (redirect)
- **Decorators:** `@login_required`, `@policy_required(action, ui:companies:transfer)`, `@transaction.atomic`, `@require_can_view_company`
- **LOC:** 33
- **Complexity:** ~5
- **Models:** `Company`, `User`, `ActivityEvent`

#### `company_update(request, company_id)`
- **URL:** POST `/companies/<uuid:company_id>/update/`
- **Return:** HTML (redirect)
- **Decorators:** `@login_required`, `@policy_required(action, ui:companies:update)`, `@require_can_view_company`
- **LOC:** 31
- **Complexity:** ~5

#### `company_inline_update(request, company_id)`
- **URL:** POST `/companies/<uuid:company_id>/inline/`
- **Return:** JSON
- **Decorators:** `@login_required`, `@policy_required(action, ui:companies:update)`, `@require_can_view_company`
- **LOC:** 93
- **Complexity:** ~15

#### `contact_create(request, company_id)`
- **URL:** POST `/companies/<uuid:company_id>/contacts/new/`
- **Return:** HTML (redirect)
- **Decorators:** `@login_required`, `@policy_required(action, ui:companies:update)`
- **LOC:** 66
- **Complexity:** ~10
- **Models:** `Contact`, `ContactPhone`

#### `contact_edit(request, contact_id)`
- **URL:** GET/POST `/contacts/<uuid:contact_id>/edit/`
- **Return:** HTML (redirect)
- **Decorators:** `@login_required`, `@policy_required(action, ui:companies:update)`
- **LOC:** 68
- **Complexity:** ~10

#### `contact_delete(request, contact_id)`
- **URL:** POST `/contacts/<uuid:contact_id>/delete/`
- **Return:** HTML (redirect)
- **Decorators:** `@login_required`, `@policy_required(action, ui:companies:update)`
- **LOC:** 36
- **Complexity:** ~5

#### `company_note_add(request, company_id)`
- **URL:** POST `/companies/<uuid:company_id>/notes/add/`
- **Return:** HTML (redirect)
- **Decorators:** `@login_required`, `@policy_required(action, ui:companies:update)`, `@require_can_view_company`
- **LOC:** 37
- **Complexity:** ~6
- **Models:** `CompanyNote`, `CompanyNoteAttachment`

#### `company_note_edit(request, company_id, note_id)`
- **URL:** GET/POST `/companies/<uuid:company_id>/notes/<int:note_id>/edit/`
- **Return:** HTML (redirect)
- **Decorators:** `@login_required`, `@policy_required(action, ui:companies:update)`, `@require_can_view_company`
- **LOC:** 123
- **Complexity:** ~18

#### `company_note_delete(request, company_id, note_id)`
- **URL:** POST `/companies/<uuid:company_id>/notes/<int:note_id>/delete/`
- **Return:** HTML (redirect)
- **Decorators:** `@login_required`, `@policy_required(action, ui:companies:update)`, `@require_can_view_company`
- **LOC:** 49
- **Complexity:** ~7

#### `company_deal_add(request, company_id)`
- **URL:** POST `/companies/<uuid:company_id>/deals/add/`
- **Return:** HTML (redirect)
- **Decorators:** `@login_required`, `@policy_required(action, ui:companies:update)`, `@require_can_view_company`
- **LOC:** 56
- **Complexity:** ~8
- **Models:** `CompanyDeal`

#### `company_deal_delete(request, company_id, deal_id)`
- **URL:** POST `/companies/<uuid:company_id>/deals/<int:deal_id>/delete/`
- **Return:** HTML (redirect)
- **Decorators:** `@login_required`, `@policy_required(action, ui:companies:update)`, `@require_can_view_company`
- **LOC:** 29
- **Complexity:** ~4

#### `phone_call_create(request)`
- **URL:** POST `/phone/call/`
- **Return:** JSON
- **Decorators:** `@login_required`, `@policy_required(action, ui:companies:update)`
- **LOC:** 121
- **Complexity:** ~20
- **Models:** `Company`, `Contact`, `CompanyPhone`, `ContactPhone`, `CallRequest`, `PhoneDevice`

#### `company_timeline_items(request, company_id)`
- **URL:** GET `/companies/<uuid:company_id>/timeline/items/`
- **Return:** HTML/JSON
- **Decorators:** `@login_required`, `@require_can_view_company`
- **LOC:** 38
- **Complexity:** ~5
- **Notes:** ⚠ GET endpoint без `@policy_required` (но имеет object-level check).

### `backend/ui/views/company_detail_v3.py` (392 LOC, 2 views)

#### `company_detail_v3_preview(request, company_id, variant)`
- **URL:** GET `/companies/<uuid:company_id>/v3/<str:variant>/`
- **Return:** HTML
- **Decorators:** `@login_required`, `@policy_required(page, ui:companies:detail)`, `@require_can_view_company`
- **LOC:** 233
- **Complexity:** ~40
- **Models:** `Company`, `Contact`, `CompanyPhone`, `CompanyEmail`, `CompanyDeal`, `CompanyNote`, `Task`, `Branch`, `User`
- **Notes:** Preview 3 вариантов редизайна карточки (F4 R3). Большая подготовка контекста — нужно вынести в сервис.

#### `contact_quick_create(request, company_id)`
- **URL:** POST `/companies/<uuid:company_id>/contacts/quick-create/`
- **Return:** JSON
- **Decorators:** `@login_required`, `@require_POST`, `@policy_required(action, ui:companies:update)`, `@require_can_view_company`
- **LOC:** 115
- **Complexity:** ~18
- **Models:** `Contact`, `ContactPhone`

### `backend/ui/views/tasks.py` (2215 LOC, 13 views + helpers)

#### `task_list(request)`
- **URL:** GET `/tasks/`
- **Return:** HTML
- **Decorators:** `@login_required`, `@policy_required(page, ui:tasks:list)`
- **LOC:** 356
- **Complexity:** ~55
- **Models:** `Task`, `TaskType`, `User`, `Company`, `Branch`
- **Notes:** God-view (356 LOC). Огромные фильтры/сортировки. Кандидат на Wave 9.

#### `task_create(request)`
- **URL:** GET/POST `/tasks/new/`
- **Return:** HTML (redirect)
- **Decorators:** `@login_required`, `@policy_required(action, ui:tasks:create)`
- **LOC:** 355
- **Complexity:** ~55
- **Models:** `Task`, `TaskType`, `Company`, `User`, `Branch`
- **Notes:** God-view (355 LOC). RRULE повторения, групповая постановка.

#### `task_delete(request, task_id)`
- **URL:** POST `/tasks/<uuid:task_id>/delete/`
- **Return:** HTML (redirect)
- **Decorators:** `@login_required`, `@policy_required(action, ui:tasks:delete)`
- **LOC:** 46
- **Complexity:** ~8

#### `task_bulk_reassign(request)`
- **URL:** POST `/tasks/bulk-reassign/`
- **Return:** HTML (redirect)
- **Decorators:** `@login_required`, `@policy_required(action, ui:tasks:bulk_reassign)`
- **LOC:** 154
- **Complexity:** ~24

#### `task_bulk_reschedule(request)`
- **URL:** POST `/tasks/bulk-reschedule/`
- **Return:** HTML (redirect)
- **Decorators:** `@login_required`, `@policy_required(action, ui:tasks:bulk_reschedule)`
- **LOC:** 208
- **Complexity:** ~35

#### `task_bulk_reschedule_preview(request)`
- **URL:** POST `/tasks/bulk-reschedule/preview/`
- **Return:** JSON
- **Decorators:** `@login_required`, `@policy_required(action, ui:tasks:bulk_reschedule)`
- **LOC:** 109
- **Complexity:** ~18

#### `task_bulk_reschedule_undo(request, event_id)`
- **URL:** POST `/tasks/bulk-reschedule/undo/<uuid:event_id>/`
- **Return:** HTML (redirect)
- **Decorators:** `@login_required`
- **LOC:** 73
- **Complexity:** ~12
- **Notes:** ⚠ **НЕТ `@policy_required`** — только `@login_required`. Требует Wave 2.

#### `task_set_status(request, task_id)`
- **URL:** POST `/tasks/<uuid:task_id>/status/`
- **Return:** HTML (redirect)
- **Decorators:** `@login_required`, `@policy_required(action, ui:tasks:status)`
- **LOC:** 66
- **Complexity:** ~10

#### `task_add_comment(request, task_id)`
- **URL:** POST `/tasks/<uuid:task_id>/comment/`
- **Return:** HTML (redirect)
- **Decorators:** `@login_required`
- **LOC:** 36
- **Complexity:** ~5
- **Notes:** ⚠ **НЕТ `@policy_required`** — mutating без policy.

#### `task_view(request, task_id)`
- **URL:** GET `/tasks/<uuid:task_id>/`
- **Return:** HTML
- **Decorators:** `@login_required`, `@policy_required(page, ui:tasks:detail)`
- **LOC:** 116
- **Complexity:** ~18

#### `task_edit(request, task_id)`
- **URL:** GET/POST `/tasks/<uuid:task_id>/edit/`
- **Return:** HTML (redirect)
- **Decorators:** `@login_required`, `@policy_required(action, ui:tasks:update)`
- **LOC:** 97
- **Complexity:** ~15

#### `task_create_v2_partial(request)`
- **URL:** GET/POST `/tasks/v2/new/partial/`
- **Return:** HTML (partial)
- **Decorators:** `@login_required`, `@policy_required(action, ui:tasks:create)`
- **LOC:** 80
- **Complexity:** ~12

#### `task_view_v2_partial(request, task_id)`
- **URL:** GET `/tasks/v2/<uuid:task_id>/partial/`
- **Return:** HTML (partial)
- **Decorators:** `@login_required`
- **LOC:** 28
- **Complexity:** ~4
- **Notes:** GET-only, read-only. Нет `@policy_required` — допустимо.

#### `task_edit_v2_partial(request, task_id)`
- **URL:** GET/POST `/tasks/v2/<uuid:task_id>/edit/partial/`
- **Return:** HTML (partial)
- **Decorators:** `@login_required`, `@policy_required(action, ui:tasks:update)`
- **LOC:** 55
- **Complexity:** ~8

### `backend/ui/views/settings_core.py` (1581 LOC, 34 views)

**Общая особенность:** ВСЕ 34 views декорированы только `@login_required`. Проверка администраторских прав — через `require_admin(user)` внутри тела функции. **Нет `@policy_required`**. Это системный gap для Wave 2.

#### `settings_dashboard(request)`
- **URL:** GET `/admin/`
- **Return:** HTML
- **Decorators:** `@login_required`
- **LOC:** 20
- **Complexity:** ~3
- **Notes:** ⚠ Админка без `@policy_required`.

#### `settings_announcements(request)`
- **URL:** GET/POST `/admin/announcements/`
- **Return:** HTML
- **Decorators:** `@login_required`
- **LOC:** 64
- **Complexity:** ~10
- **Models:** `CrmAnnouncement`
- **Notes:** ⚠ MUTATING без policy.

#### `settings_access(request)`
- **URL:** GET `/admin/access/`
- **Return:** HTML
- **Decorators:** `@login_required`
- **LOC:** 196
- **Complexity:** ~30
- **Models:** `PolicyRule`, `PolicyConfig`, `User`

#### `settings_access_role(request, role)`
- **URL:** GET/POST `/admin/access/roles/<str:role>/`
- **Return:** HTML
- **Decorators:** `@login_required`
- **LOC:** 188
- **Complexity:** ~30
- **Models:** `PolicyRule`
- **Notes:** ⚠ MUTATING (изменение политик) без `@policy_required`.

#### `settings_branches(request)`
- **URL:** GET `/admin/branches/`
- **Return:** HTML
- **Decorators:** `@login_required`
- **LOC:** 9
- **Complexity:** ~1

#### `settings_branch_create(request)`
- **URL:** GET/POST `/admin/branches/new/`
- **Return:** HTML
- **Decorators:** `@login_required`
- **LOC:** 16
- **Complexity:** ~2
- **Models:** `Branch`
- **Notes:** ⚠ MUTATING без policy.

#### `settings_branch_edit(request, branch_id)`
- **URL:** GET/POST `/admin/branches/<int:branch_id>/edit/`
- **Return:** HTML
- **Decorators:** `@login_required`
- **LOC:** 17
- **Complexity:** ~2
- **Notes:** ⚠ MUTATING без policy.

#### `settings_users(request)`
- **URL:** GET `/admin/users/`
- **Return:** HTML
- **Decorators:** `@login_required`
- **LOC:** 233
- **Complexity:** ~40
- **Models:** `User`, `Branch`

#### `settings_user_create(request)`
- **URL:** GET/POST `/admin/users/new/`
- **Return:** HTML
- **Decorators:** `@login_required`
- **LOC:** 35
- **Complexity:** ~6
- **Notes:** ⚠ MUTATING (создание юзеров) без policy.

#### `settings_user_edit(request, user_id)`
- **URL:** GET/POST `/admin/users/<int:user_id>/edit/`
- **Return:** HTML
- **Decorators:** `@login_required`
- **LOC:** 97
- **Complexity:** ~15
- **Notes:** ⚠ MUTATING без policy.

#### `settings_user_magic_link_generate(request, user_id)`
- **URL:** POST `/admin/users/<int:user_id>/magic-link/generate/`
- **Return:** HTML (redirect)
- **Decorators:** `@login_required`
- **LOC:** 80
- **Complexity:** ~12
- **Models:** `MagicLinkToken`, `ActivityEvent`
- **Notes:** ⚠ **КРИТИЧНО:** генерация magic-link без `@policy_required`. Security-risk.

#### `settings_user_logout(request, user_id)`
- **URL:** POST `/admin/users/<int:user_id>/logout/`
- **Return:** HTML (redirect)
- **Decorators:** `@login_required`
- **LOC:** 49
- **Complexity:** ~8
- **Notes:** ⚠ MUTATING без policy.

#### `settings_user_form_ajax(request, user_id)`
- **URL:** GET `/admin/users/<int:user_id>/form/`
- **Return:** JSON
- **Decorators:** `@login_required`
- **LOC:** 40
- **Complexity:** ~6

#### `settings_user_update_ajax(request, user_id)`
- **URL:** POST `/admin/users/<int:user_id>/update/`
- **Return:** JSON
- **Decorators:** `@login_required`
- **LOC:** 49
- **Complexity:** ~8
- **Notes:** ⚠ MUTATING без policy.

#### `settings_user_delete(request, user_id)`
- **URL:** POST `/admin/users/<int:user_id>/delete/`
- **Return:** JSON
- **Decorators:** `@login_required`
- **LOC:** 44
- **Complexity:** ~7
- **Notes:** ⚠ MUTATING (удаление юзера) без policy.

#### `settings_dicts(request)`
- **URL:** GET `/admin/dicts/`
- **Return:** HTML
- **Decorators:** `@login_required`
- **LOC:** 17
- **Complexity:** ~2

#### `settings_company_status_create(request)`
- **URL:** GET/POST `/admin/dicts/company-status/new/`
- **Return:** HTML
- **Decorators:** `@login_required`
- **LOC:** 16
- **Complexity:** ~2
- **Notes:** ⚠ MUTATING без policy.

#### `settings_company_sphere_create(request)`
- **URL:** GET/POST `/admin/dicts/company-sphere/new/`
- **Decorators:** `@login_required`
- **LOC:** 16 / ⚠ MUTATING без policy.

#### `settings_contract_type_create(request)`
- **URL:** GET/POST `/admin/dicts/contract-type/new/`
- **Decorators:** `@login_required`
- **LOC:** 16 / ⚠ MUTATING без policy.

#### `settings_task_type_create(request)`
- **URL:** GET/POST `/admin/dicts/task-type/new/`
- **Decorators:** `@login_required`
- **LOC:** 19 / ⚠ MUTATING без policy.

#### `settings_company_status_edit(request, status_id)`
- **URL:** GET/POST `/admin/dicts/company-status/<int:status_id>/edit/`
- **Decorators:** `@login_required`
- **LOC:** 19 / ⚠ MUTATING без policy.

#### `settings_company_status_delete(request, status_id)`
- **URL:** POST `/admin/dicts/company-status/<int:status_id>/delete/`
- **Decorators:** `@login_required`
- **LOC:** 15 / ⚠ MUTATING без policy.

#### `settings_company_sphere_edit(request, sphere_id)`
- **Decorators:** `@login_required` / ⚠ MUTATING без policy / LOC: 19

#### `settings_company_sphere_delete(request, sphere_id)`
- **Decorators:** `@login_required` / ⚠ MUTATING без policy / LOC: 38

#### `settings_contract_type_edit(request, contract_type_id)`
- **Decorators:** `@login_required` / ⚠ MUTATING без policy / LOC: 19

#### `settings_contract_type_delete(request, contract_type_id)`
- **Decorators:** `@login_required` / ⚠ MUTATING без policy / LOC: 15

#### `settings_task_type_edit(request, task_type_id)`
- **Decorators:** `@login_required` / ⚠ MUTATING без policy / LOC: 22

#### `settings_task_type_delete(request, task_type_id)`
- **Decorators:** `@login_required` / ⚠ MUTATING без policy / LOC: 18

#### `settings_activity(request)`
- **URL:** GET `/admin/activity/`
- **Decorators:** `@login_required`
- **LOC:** 18
- **Models:** `ActivityEvent`

#### `settings_error_log(request)`
- **URL:** GET `/admin/error-log/`
- **Decorators:** `@login_required`
- **LOC:** 66
- **Models:** `ErrorLog`

#### `settings_error_log_resolve(request, error_id)`
- **URL:** POST `/admin/error-log/<uuid:error_id>/resolve/`
- **Decorators:** `@login_required` / ⚠ MUTATING без policy / LOC: 23

#### `settings_error_log_unresolve(request, error_id)`
- **URL:** POST `/admin/error-log/<uuid:error_id>/unresolve/`
- **Decorators:** `@login_required` / ⚠ MUTATING без policy / LOC: 19

#### `settings_error_log_details(request, error_id)`
- **URL:** GET `/admin/error-log/<uuid:error_id>/details/`
- **Return:** JSON
- **Decorators:** `@login_required`
- **LOC:** 27

### `backend/ui/views/settings_integrations.py` (1495 LOC, 15 views)

**Общая особенность:** ВСЕ 15 views декорированы только `@login_required`. Ни одна не имеет `@policy_required`.

#### `settings_import(request)`
- **URL:** GET/POST `/admin/import/`
- **Decorators:** `@login_required`
- **LOC:** 54 / ⚠ MUTATING без policy (импорт компаний).

#### `settings_import_tasks(request)`
- **URL:** GET/POST `/admin/import/tasks/`
- **Decorators:** `@login_required`
- **LOC:** 60 / ⚠ MUTATING без policy (импорт задач из ICS).

#### `settings_amocrm(request)`
- **URL:** GET/POST `/admin/amocrm/`
- **Decorators:** `@login_required`
- **LOC:** 67 / ⚠ MUTATING без policy (управление AmoCRM интеграцией).

#### `settings_amocrm_callback(request)`
- **URL:** GET `/admin/amocrm/callback/`
- **Decorators:** `@login_required`
- **LOC:** 81 / ⚠ OAuth callback без policy, но критично — Amo может прислать code; обычный admin check.

#### `settings_amocrm_disconnect(request)`
- **URL:** POST `/admin/amocrm/disconnect/`
- **Decorators:** `@login_required`
- **LOC:** 16 / ⚠ MUTATING без policy.

#### `settings_amocrm_migrate(request)`
- **URL:** POST `/admin/amocrm/migrate/`
- **Decorators:** `@login_required`
- **LOC:** 288 / ⚠ **МАССОВАЯ МИГРАЦИЯ** без policy. God-view.

#### `settings_amocrm_migrate_progress(request)`
- **URL:** GET `/admin/amocrm/migrate/progress/`
- **Decorators:** `@login_required`
- **LOC:** 37 / read-only.

#### `settings_amocrm_contacts_dry_run(request)`
- **URL:** POST `/admin/amocrm/contacts-dry-run/`
- **Decorators:** `@login_required`
- **LOC:** 73 / GET-only (dry-run).

#### `settings_amocrm_debug_contacts(request)`
- **URL:** GET `/admin/amocrm/debug-contacts/`
- **Decorators:** `@login_required`
- **LOC:** 119 / debug.

#### `settings_company_columns(request)`
- **URL:** GET/POST `/admin/company-columns/`
- **Decorators:** `@login_required`
- **LOC:** 20 / ⚠ MUTATING без policy.

#### `settings_security(request)`
- **URL:** GET/POST `/admin/security/`
- **Decorators:** `@login_required`
- **LOC:** 43 / ⚠ MUTATING (security-settings) без policy.

#### `settings_mobile_devices(request)`
- **URL:** GET `/admin/mobile/devices/`
- **Decorators:** `@login_required`
- **LOC:** 58
- **Models:** `PhoneDevice`

#### `settings_mobile_overview(request)`
- **URL:** GET `/admin/mobile/overview/`
- **Decorators:** `@login_required`
- **LOC:** 105
- **Models:** `PhoneDevice`, `CallRequest`, `PhoneTelemetry`

#### `settings_mobile_device_detail(request, pk)`
- **URL:** GET `/admin/mobile/devices/<uuid:pk>/`
- **Decorators:** `@login_required`
- **LOC:** 39

#### `settings_calls_stats(request)`
- **URL:** GET `/admin/calls/stats/`
- **Decorators:** `@login_required`
- **LOC:** 276 / god-view, статистика звонков.
- **Models:** `CallRequest`, `User`

#### `settings_calls_manager_detail(request, user_id)`
- **URL:** GET `/admin/calls/stats/<int:user_id>/`
- **Decorators:** `@login_required`
- **LOC:** 95

### `backend/ui/views/settings_messenger.py` (1069 LOC, 15 views)

**Общая особенность:** ВСЕ 15 views только `@login_required`. Ни одна не имеет `@policy_required`.

#### `settings_messenger_overview(request)`
- **URL:** GET `/admin/messenger/`
- **Decorators:** `@login_required` / LOC: 63 / read-only.

#### `settings_messenger_source_choose(request)`
- **URL:** GET `/admin/messenger/sources/choose/`
- **Decorators:** `@login_required` / LOC: 16 / read-only.

#### `settings_messenger_inbox_edit(request, inbox_id=None)`
- **URL:** GET/POST `/admin/messenger/inboxes/new/`, `/admin/messenger/inboxes/<int:inbox_id>/`
- **Decorators:** `@login_required` / LOC: 434 / ⚠ MUTATING без policy, god-view.
- **Models:** `Inbox`

#### `settings_messenger_inbox_ready(request, inbox_id)`
- **URL:** POST `/admin/messenger/inboxes/<int:inbox_id>/ready/`
- **Decorators:** `@login_required` / LOC: 23 / ⚠ MUTATING без policy.

#### `settings_messenger_health(request)`
- **URL:** GET `/admin/messenger/health/`
- **Decorators:** `@login_required` / LOC: 55 / read-only.

#### `settings_messenger_analytics(request)`
- **URL:** GET `/admin/messenger/analytics/`
- **Decorators:** `@login_required` / LOC: 143 / read-only.

#### `settings_messenger_routing_list(request)`
- **URL:** GET `/admin/messenger/routing/`
- **Decorators:** `@login_required` / LOC: 22

#### `settings_messenger_routing_edit(request, rule_id=None)`
- **URL:** GET/POST `/admin/messenger/routing/new/`, `/admin/messenger/routing/<int:rule_id>/`
- **Decorators:** `@login_required` / LOC: 91 / ⚠ MUTATING без policy.
- **Models:** `RoutingRule`

#### `settings_messenger_routing_delete(request, rule_id)`
- **URL:** POST `/admin/messenger/routing/<int:rule_id>/delete/`
- **Decorators:** `@login_required` / LOC: 21 / ⚠ MUTATING без policy.

#### `settings_messenger_canned_list(request)`
- **URL:** GET `/admin/messenger/canned-responses/`
- **Decorators:** `@login_required` / LOC: 26

#### `settings_messenger_canned_edit(request, response_id=None)`
- **URL:** GET/POST `/admin/messenger/canned-responses/new/`, `/admin/messenger/canned-responses/<int:response_id>/`
- **Decorators:** `@login_required` / LOC: 62 / ⚠ MUTATING без policy.
- **Models:** `CannedResponse`

#### `settings_messenger_canned_delete(request, response_id)`
- **URL:** POST `/admin/messenger/canned-responses/<int:response_id>/delete/`
- **Decorators:** `@login_required` / LOC: 23 / ⚠ MUTATING без policy.

#### `settings_messenger_campaigns(request)`
- **URL:** GET `/admin/messenger/campaigns/`
- **Decorators:** `@login_required` / LOC: 41 / read-only.
- **Models:** `MessengerCampaign`

#### `settings_messenger_automation(request)`
- **URL:** GET `/admin/messenger/automation/`
- **Decorators:** `@login_required` / LOC: 30 / read-only.
- **Models:** `AutomationRule`

### `backend/ui/views/settings_mail.py` (342 LOC, 5 views)

**Общая особенность:** все 5 views только `@login_required` + `@require_POST`. Ни одна не имеет `@policy_required`. Проверка admin — через `require_admin` ручная.

#### `settings_mail_setup(request)`
- **URL:** GET `/admin/mail/setup/`
- **Decorators:** `@login_required` / LOC: 17 / read-only.

#### `settings_mail_save_password(request)`
- **URL:** POST `/admin/mail/setup/save-password/`
- **Decorators:** `@login_required`, `@require_POST` / LOC: 62 / ⚠ MUTATING без policy.
- **Models:** `GlobalMailAccount`
- **Notes:** **Критично:** сохранение SMTP-пароля (Fernet) — без policy.

#### `settings_mail_test_send(request)`
- **URL:** POST `/admin/mail/setup/test-send/`
- **Decorators:** `@login_required`, `@require_POST` / LOC: 95 / ⚠ MUTATING без policy.

#### `settings_mail_save_config(request)`
- **URL:** POST `/admin/mail/setup/save-config/`
- **Decorators:** `@login_required`, `@require_POST` / LOC: 69 / ⚠ MUTATING без policy.

#### `settings_mail_toggle_enabled(request)`
- **URL:** POST `/admin/mail/setup/toggle-enabled/`
- **Decorators:** `@login_required`, `@require_POST` / LOC: 38 / ⚠ MUTATING без policy.

### `backend/ui/views/settings_mobile_apps.py` (145 LOC, 3 views)

#### `settings_mobile_apps(request)`
- **URL:** GET `/admin/mobile-apps/`
- **Decorators:** `@login_required` / LOC: 19 / read-only.
- **Models:** `MobileAppBuild`

#### `settings_mobile_apps_upload(request)`
- **URL:** POST `/admin/mobile-apps/upload/`
- **Decorators:** `@login_required`, `@require_POST` / LOC: 70 / ⚠ **APK upload без policy.**

#### `settings_mobile_apps_toggle(request, build_id)`
- **URL:** POST `/admin/mobile-apps/<uuid:build_id>/toggle/`
- **Decorators:** `@login_required`, `@require_POST` / LOC: 29 / ⚠ MUTATING без policy.

### `backend/ui/views/messenger_panel.py` (251 LOC, 2 views)

#### `messenger_conversations_unified(request)`
- **URL:** GET `/messenger/`
- **Return:** HTML
- **Decorators:** `@login_required`, `@policy_required(page, ui:messenger:conversations:list)`
- **LOC:** 183
- **Complexity:** ~30
- **Models:** `Conversation`, `Branch`, `User`

#### `messenger_agent_status(request)`
- **URL:** GET/POST `/messenger/me/status/`
- **Return:** HTML (redirect)
- **Decorators:** `@login_required`
- **LOC:** 32
- **Complexity:** ~5
- **Notes:** ⚠ MUTATING без `@policy_required`.

### `backend/ui/views/mobile.py` (126 LOC, 3 views)

#### `mobile_app_page(request)`
- **URL:** GET `/mobile-app/`
- **Decorators:** `@login_required`, `@policy_required(page, ui:mobile_app)`
- **LOC:** 24

#### `mobile_app_download(request, build_id)`
- **URL:** GET `/mobile-app/download/<uuid:build_id>/`
- **Decorators:** `@login_required`, `@policy_required(action, ui:mobile_app:download)`
- **LOC:** 40
- **Return:** FileResponse

#### `mobile_app_qr_image(request)`
- **URL:** GET `/mobile-app/qr.png`
- **Decorators:** `@login_required`, `@policy_required(action, ui:mobile_app:qr)`
- **LOC:** 40
- **Return:** HttpResponse (image)

### `backend/ui/views/analytics_v2.py` (55 LOC, 1 view)

#### `analytics_v2_home(request)`
- **URL:** GET `/analytics/v2/`
- **Decorators:** `@login_required`
- **LOC:** 31
- **Notes:** F7 R1 MVP. ⚠ Нет `@policy_required` (аналитика может утекать KPI).

---

## app: messenger

### `backend/messenger/views.py` (177 LOC, 2 views — SSR)

#### `widget_demo(request)`
- **URL:** GET `/widget-demo/`
- **Decorators:** `@login_required`
- **LOC:** 32
- **Return:** HTML
- **Notes:** Демо widget для тестов.

#### `widget_test_page(request)`
- **URL:** GET `/widget-test/`
- **Decorators:** `@xframe_options_exempt`
- **LOC:** 124
- **Return:** HTML
- **Notes:** ⚠ **БЕЗ `@login_required`** — публичная testing-страница для виджета. Оправданно, но проверить что в prod не доступна.

### `backend/messenger/api.py` (1349 LOC, 11 классов + 3 FBV + ≈20 actions)

#### `ConversationViewSet(viewsets.ModelViewSet)`
- **URL:** `/api/conversations/` + nested (`/api/v1/conversations/`)
- **Permissions:** `IsAuthenticated`, `PolicyPermission` + `policy_resource_prefix = "api:messenger:conversations"`
- **LOC:** ~980
- **Models:** `Conversation`, `Message`, `Contact`, `Branch`, `Inbox`, `Label`
- **Actions (nested endpoints):**
  - `partial_update()` — PATCH, LOC ~10
  - `list()` — GET, LOC ~18
  - `destroy()` — DELETE, LOC ~30
  - `read` — POST `/conversations/{pk}/read/`, LOC ~33
  - `merge_contacts` — POST `/conversations/merge-contacts/`, LOC ~48
  - `unread_count` — GET `/conversations/unread-count/`, LOC ~12
  - `agents` — GET `/conversations/agents/`, LOC ~38
  - `needs_help` — POST `/conversations/{pk}/needs-help/`, LOC ~35
  - `contacted_back` — POST `/conversations/{pk}/contacted-back/`, LOC ~73
  - `bulk` — POST `/conversations/bulk/`, LOC ~30
  - `notifications_stream` — GET `/conversations/notifications/stream/` (SSE), LOC ~107
  - `messages` — GET/POST `/conversations/{pk}/messages/`, LOC ~145
  - `stream` — GET `/conversations/{pk}/stream/` (SSE), LOC ~114
  - `typing` — GET/POST `/conversations/{pk}/typing/`, LOC ~27
  - `context` — GET `/conversations/{pk}/context/`, LOC ~100

#### `CannedResponseViewSet(viewsets.ModelViewSet)`
- **URL:** `/api/canned-responses/`, `/api/v1/canned-responses/`
- **LOC:** 35
- **Models:** `CannedResponse`

#### `ConversationLabelViewSet(viewsets.ModelViewSet)`
- **URL:** `/api/conversation-labels/`
- **LOC:** 10

#### `PushSubscriptionViewSet(viewsets.ViewSet)`
- **URL:** `/api/push/`
- **LOC:** 50
- **Actions:**
  - `vapid_key` — GET `/push/vapid-key/`
  - `subscribe` — POST `/push/subscribe/`
  - `unsubscribe` — POST `/push/unsubscribe/`
- **Models:** `PushSubscription`

#### `CampaignViewSet(viewsets.ModelViewSet)`
- **URL:** `/api/campaigns/`
- **LOC:** 20
- **Models:** `MessengerCampaign`

#### `AutomationRuleViewSet(viewsets.ModelViewSet)`
- **URL:** `/api/automation-rules/`
- **LOC:** 19
- **Models:** `AutomationRule`

#### `ReportingViewSet(viewsets.ViewSet)`
- **URL:** `/api/messenger-reports/`
- **LOC:** 56
- **Actions:** `overview` — GET
- **Notes:** read-only аналитика.

#### `MacroViewSet(viewsets.ModelViewSet)`
- **URL:** `/api/macros/`
- **LOC:** 41
- **Models:** `Macro`
- **Actions:** `execute` — POST `/macros/{pk}/execute/`

#### `branches_list_view(request)` (FBV)
- **URL:** GET `/api/messenger/branches/`
- **Decorators:** `@api_view(["GET"])`, `@permission_classes([IsAuthenticated])`
- **LOC:** 14
- **Return:** JSON

#### `heartbeat_view(request)` (FBV)
- **URL:** POST `/api/messenger/heartbeat/`
- **Decorators:** `@api_view(["POST"])`, `@permission_classes([IsAuthenticated])`
- **LOC:** 9
- **Return:** JSON
- **Notes:** ⚠ MUTATING (ставит online_status) без `@policy_required` (через PolicyPermission).

#### `transfer_conversation(request, conversation_id)` (FBV)
- **URL:** POST `/api/messenger/conversations/<int:conversation_id>/transfer/`
- **Decorators:** `@api_view(["POST"])`, `@permission_classes([IsAuthenticated])`
- **LOC:** ~45
- **Return:** JSON
- **Notes:** ⚠ MUTATING (передача диалога) без `@policy_required`. Требует Wave 2.

### `backend/messenger/widget_api.py` (1721 LOC, 10 публичных views)

**Общая особенность:** ВСЕ views публичные (`@permission_classes([AllowAny])`, `@authentication_classes([])`). Это ОЖИДАЕМО — Widget API для внешних сайтов. Защита — через widget_token, throttles, rate limiting, CORS allowed_domains.

#### `widget_bootstrap(request)`
- **URL:** POST/OPTIONS `/api/widget/bootstrap/`
- **Decorators:** `@api_view(["POST", "OPTIONS"])`, `@authentication_classes([])`, `@permission_classes([AllowAny])`, `@throttle_classes([WidgetBootstrapThrottle])`
- **LOC:** 321
- **Complexity:** ~50
- **Return:** JSON
- **Models:** `Inbox`, `Contact`, `Conversation`, `Message`, `WidgetSession`
- **Notes:** God-view (321 LOC). Публичный, защищён throttle + widget_token + CORS.

#### `widget_contact_update(request)`
- **URL:** POST/OPTIONS `/api/widget/contact/`
- **Decorators:** `@api_view(["POST", "OPTIONS"])`, `@authentication_classes([])`, `@permission_classes([AllowAny])`
- **LOC:** 80
- **Return:** JSON

#### `widget_offhours_request(request)`
- **URL:** POST/OPTIONS `/api/widget/offhours-request/`
- **Decorators:** (как выше)
- **LOC:** 176
- **Return:** JSON
- **Notes:** Запрос на обратный звонок в нерабочие часы.

#### `widget_send(request)`
- **URL:** POST `/api/widget/send/`
- **Decorators:** `@api_view(["POST"])`, `@permission_classes([AllowAny])`, `@throttle_classes([WidgetSendThrottle])`
- **LOC:** 328
- **Return:** JSON
- **Models:** `Message`, `Conversation`, `WidgetAttachment`

#### `widget_poll(request)`
- **URL:** GET `/api/widget/poll/`
- **Decorators:** (AllowAny, WidgetPollThrottle)
- **LOC:** 190
- **Return:** JSON
- **Notes:** Polling обновлений. Поддерживает since_id.

#### `widget_stream(request)`
- **URL:** GET/OPTIONS `/api/widget/stream/`
- **Decorators:** — (обычный Django view для SSE)
- **LOC:** 198
- **Return:** **SSE (StreamingHttpResponse)**
- **Notes:** SSE endpoint, короткие соединения (~25s).

#### `widget_attachment_download(request, attachment_id)`
- **URL:** GET `/api/widget/attachment/<int:attachment_id>/`
- **Decorators:** (AllowAny)
- **LOC:** 56
- **Return:** FileResponse
- **Models:** `Message`, `WidgetAttachment`

#### `widget_typing(request)`
- **URL:** POST `/api/widget/typing/`
- **Decorators:** (AllowAny, WidgetPollThrottle)
- **LOC:** 36
- **Return:** JSON

#### `widget_mark_read(request)`
- **URL:** POST `/api/widget/mark_read/`
- **Decorators:** (AllowAny, WidgetPollThrottle)
- **LOC:** 66
- **Return:** JSON

#### `widget_rate(request)`
- **URL:** POST `/api/widget/rate/`
- **Decorators:** (AllowAny, WidgetPollThrottle)
- **LOC:** 68
- **Return:** JSON
- **Notes:** Оценка диалога виджетом.

#### `widget_campaigns(request)`
- **URL:** GET `/api/widget/campaigns/`
- **Decorators:** (AllowAny)
- **LOC:** ~25
- **Return:** JSON

### `backend/messenger/consumers.py` (WebSocket)

#### `OperatorConsumer(AsyncWebsocketConsumer)`
- **URL:** WebSocket `/ws/operator/` (из routing)
- **LOC:** ~214
- **Notes:** WebSocket для операторов. Аутентификация — через session middleware.

#### `WidgetConsumer(AsyncWebsocketConsumer)`
- **URL:** WebSocket `/ws/widget/`
- **LOC:** ~220
- **Notes:** WebSocket для виджета. Публичный, аутентификация через widget_token.

---

## app: companies

### `backend/companies/api.py` (305 LOC, 3 ViewSet)

#### `CompanyViewSet(viewsets.ModelViewSet)`
- **URL:** `/api/companies/`, `/api/v1/companies/`
- **Permissions:** `IsAuthenticated`, `PolicyPermission` (prefix `api:companies`)
- **LOC:** 100
- **Models:** `Company`, `User`, `Branch`, `Contact`
- **Methods:** GET/POST/PUT/PATCH/DELETE
- **Notes:** Full CRUD + защита `perform_create`/`perform_update` через `can_edit_company`. **Есть PolicyPermission** — хорошо.

#### `ContactViewSet(viewsets.ModelViewSet)`
- **URL:** `/api/contacts/`, `/api/v1/contacts/`
- **Permissions:** `IsAuthenticated`, `PolicyPermission` (prefix `api:contacts`)
- **LOC:** 35
- **Models:** `Contact`, `Company`

#### `CompanyNoteViewSet(viewsets.ModelViewSet)`
- **URL:** `/api/company-notes/`, `/api/v1/company-notes/`
- **Permissions:** `IsAuthenticated`, `PolicyPermission` (prefix `api:company_notes`)
- **LOC:** 55
- **Models:** `CompanyNote`, `Company`

### `backend/companies/views.py` (4 LOC)

Пустой (только `from .api import *` — фактически нет views).

---

## app: tasksapp

### `backend/tasksapp/api.py` (255 LOC, 2 ViewSet)

#### `TaskTypeViewSet(viewsets.ModelViewSet)`
- **URL:** `/api/task-types/`, `/api/v1/task-types/`
- **Permissions:** `IsAuthenticated`, `PolicyPermission` (prefix `api:task_types`)
- **LOC:** 7
- **Models:** `TaskType`

#### `TaskViewSet(viewsets.ModelViewSet)`
- **URL:** `/api/tasks/`, `/api/v1/tasks/`
- **Permissions:** `IsAuthenticated`, `PolicyPermission` (prefix `api:tasks`)
- **LOC:** 140
- **Models:** `Task`, `Company`, `User`, `TaskType`
- **Notes:** Сложный `perform_create` с apply_to_org_branches (групповая постановка), дедупликация по idempotency-window (10s).

### `backend/tasksapp/views.py` (3 LOC) — пустой.

---

## app: phonebridge

### `backend/phonebridge/api.py` (1059 LOC, 14 классов APIView)

**Общая особенность:** все views используют `enforce(...)` напрямую внутри тела вместо `@policy_required`. Это эквивалентно, но не декоратор. Для учёта Wave 2: все имеют политику, OK.

#### `RegisterDeviceView(APIView)`
- **URL:** POST `/api/phone/devices/register/`, `/api/v1/phone/devices/register/`
- **Permissions:** `IsAuthenticated`, JWT
- **LOC:** 26
- **Policy:** `enforce(phone:devices:register)`
- **Models:** `PhoneDevice`

#### `DeviceHeartbeatView(APIView)`
- **URL:** POST `/api/phone/devices/heartbeat/`
- **LOC:** 119
- **Policy:** `enforce(phone:devices:heartbeat)`
- **Throttle:** `phone_heartbeat`
- **Models:** `PhoneDevice`

#### `PullCallView(APIView)`
- **URL:** GET `/api/phone/calls/pull/`
- **LOC:** 67
- **Policy:** `enforce(phone:calls:pull)`
- **Throttle:** `phone_pull`
- **Models:** `PhoneDevice`, `CallRequest`

#### `UpdateCallInfoView(APIView)`
- **URL:** POST `/api/phone/calls/update/`
- **LOC:** 98
- **Policy:** `enforce(phone:calls:update)`
- **Models:** `CallRequest`

#### `PhoneTelemetryView(APIView)`
- **URL:** POST `/api/phone/telemetry/`
- **LOC:** 60
- **Policy:** `enforce(phone:telemetry)`
- **Throttle:** `phone_telemetry`
- **Models:** `PhoneTelemetry`, `PhoneDevice`

#### `PhoneLogUploadView(APIView)`
- **URL:** POST `/api/phone/logs/`
- **LOC:** 52
- **Policy:** `enforce(phone:logs:upload)`
- **Models:** `PhoneLogBundle`, `PhoneDevice`

#### `QrTokenCreateView(APIView)`
- **URL:** POST `/api/phone/qr/create/`
- **LOC:** 54
- **Policy:** `enforce(phone:qr:create)`
- **Models:** `MobileAppQrToken`
- **Notes:** Rate limiting через IP (1/10s).

#### `QrTokenExchangeView(APIView)`
- **URL:** POST `/api/phone/qr/exchange/`
- **LOC:** 68
- **Permission:** `[]` (публичный!)
- **Policy:** `enforce(phone:qr:exchange)` после валидации токена.
- **Notes:** Публичный endpoint, но защищён через QR-токен с TTL 5 минут.

#### `LogoutView(APIView)`
- **URL:** POST `/api/phone/logout/`
- **LOC:** 51
- **Policy:** `enforce(phone:logout)`

#### `LogoutAllView(APIView)`
- **URL:** POST `/api/phone/logout/all/`
- **LOC:** 57
- **Policy:** `enforce(phone:logout_all)`

#### `UserInfoView(APIView)`
- **URL:** GET `/api/phone/user/info/`
- **LOC:** 14
- **Policy:** `enforce(phone:user:info)`

#### `QrTokenStatusView(APIView)`
- **URL:** GET `/api/phone/qr/status/`
- **LOC:** 48
- **Policy:** `enforce(phone:qr:status)`

#### `MobileAppLatestView(APIView)`
- **URL:** GET `/api/phone/app/latest/`
- **LOC:** 57
- **Throttle:** `mobile_app_latest`
- **Models:** `MobileAppBuild`
- **Notes:** ⚠ Нет `enforce(...)` — только JWT. Возможно, OK для публичного APK info, но стоит проверить.

---

## app: notifications

### `backend/notifications/views.py` (239 LOC, 6 views)

#### `mark_all_read(request)`
- **URL:** POST `/notifications/mark-all-read/`
- **Decorators:** `@login_required`
- **LOC:** 9
- **Policy:** `enforce(ui:notifications:mark_all_read)` в теле
- **Models:** `Notification`

#### `mark_read(request, notification_id)`
- **URL:** POST `/notifications/<int:notification_id>/read/`
- **Decorators:** `@login_required`
- **LOC:** 10
- **Policy:** `enforce(ui:notifications:mark_read)`
- **Models:** `Notification`

#### `poll(request)`
- **URL:** GET `/notifications/poll/`
- **Decorators:** `@login_required`
- **LOC:** 67
- **Return:** JSON
- **Policy:** `enforce(ui:notifications:poll)`
- **Models:** `Notification`, `CrmAnnouncement`, `CrmAnnouncementRead`

#### `all_notifications(request)`
- **URL:** GET `/notifications/all/`
- **Decorators:** `@login_required`
- **LOC:** 23
- **Return:** HTML
- **Policy:** `enforce(ui:notifications:all)`

#### `all_reminders(request)`
- **URL:** GET `/notifications/reminders/all/`
- **Decorators:** `@login_required`
- **LOC:** 82
- **Return:** HTML
- **Policy:** `enforce(ui:notifications:reminders)`
- **Models:** `Task`, `Company`, `ContractType`

#### `mark_announcement_read(request, announcement_id)`
- **URL:** POST `/notifications/announcements/<int:announcement_id>/read/`
- **Decorators:** `@login_required`
- **LOC:** 10
- **Return:** JSON
- **Models:** `CrmAnnouncement`, `CrmAnnouncementRead`
- **Notes:** ⚠ **Нет `@policy_required` и нет `enforce()` в теле.** Mutating без защиты policy.

---

## app: mailer

### `backend/mailer/views/settings.py` (450 LOC, 4 views)

#### `mail_signature(request)`
- **URL:** GET/POST `/mail/signature/`
- **Decorators:** `@login_required`
- **LOC:** 25
- **Policy:** через `enforce(ui:mail:signature)` в теле
- **Models:** `User`

#### `mail_settings(request)`
- **URL:** GET/POST `/mail/settings/`
- **Decorators:** `@login_required`
- **LOC:** 65
- **Policy:** `enforce(ui:mail:settings)`
- **Models:** `MailAccount`

#### `mail_admin(request)`
- **URL:** GET `/mail/admin/`
- **Decorators:** `@login_required`
- **LOC:** 303
- **Policy:** `enforce(ui:mail:admin)`
- **Models:** `GlobalMailAccount`, `SmtpBzQuota`, `SendLog`
- **Notes:** God-view (303 LOC). Табы, админка SMTP.

#### `mail_quota_poll(request)`
- **URL:** GET `/mail/quota/poll/`
- **Decorators:** `@login_required`
- **LOC:** 25
- **Return:** JSON
- **Policy:** `enforce(ui:mail:quota_poll)`

### `backend/mailer/views/campaigns/list_detail.py` (689 LOC, 2 views)

#### `campaigns(request)`
- **URL:** GET `/mail/campaigns/`
- **Decorators:** `@login_required`
- **LOC:** 362
- **Policy:** `enforce(ui:mail:campaigns)`
- **Models:** `Campaign`, `MailAccount`
- **Notes:** God-view (362 LOC). Фильтры, пагинация, сортировки.

#### `campaign_detail(request, campaign_id)`
- **URL:** GET `/mail/campaigns/<uuid:campaign_id>/`
- **Decorators:** `@login_required`
- **LOC:** 294
- **Policy:** `enforce(ui:mail:campaign_detail)`
- **Notes:** God-view (294 LOC).

### `backend/mailer/views/campaigns/crud.py` (162 LOC, 4 views)

#### `campaign_create(request)`
- **URL:** GET/POST `/mail/campaigns/new/`
- **Decorators:** `@login_required`
- **LOC:** 34
- **Policy:** `enforce(ui:mail:campaign_create)`
- **Models:** `Campaign`

#### `campaign_edit(request, campaign_id)`
- **URL:** GET/POST `/mail/campaigns/<uuid:campaign_id>/edit/`
- **Decorators:** `@login_required`
- **LOC:** 57
- **Policy:** `enforce(ui:mail:campaign_edit)`

#### `campaign_delete(request, campaign_id)`
- **URL:** POST `/mail/campaigns/<uuid:campaign_id>/delete/`
- **Decorators:** `@login_required`
- **LOC:** 23
- **Policy:** `enforce(ui:mail:campaign_delete)`

#### `campaign_clone(request, campaign_id)`
- **URL:** POST `/mail/campaigns/<uuid:campaign_id>/clone/`
- **Decorators:** `@login_required`
- **LOC:** 19
- **Policy:** `enforce(ui:mail:campaign_clone)`

### `backend/mailer/views/campaigns/files.py` (163 LOC, 5 views)

#### `campaign_html_preview(request, campaign_id)`
- **URL:** GET `/mail/campaigns/<uuid:campaign_id>/preview/`
- **Decorators:** `@login_required`
- **LOC:** 32
- **Policy:** `enforce(ui:mail:campaign_html_preview)`

#### `campaign_attachment_download(request, campaign_id)`
- **URL:** GET `/mail/campaigns/<uuid:campaign_id>/attachment/download/`
- **Decorators:** `@login_required`
- **LOC:** 18
- **Return:** FileResponse

#### `campaign_attachment_delete(request, campaign_id)`
- **URL:** POST `/mail/campaigns/<uuid:campaign_id>/attachment/delete/`
- **Decorators:** `@login_required`
- **LOC:** 31
- **Policy:** `enforce(ui:mail:campaign_attachment_delete)`

#### `campaign_export_failed(request, campaign_id)`
- **URL:** GET `/mail/campaigns/<uuid:campaign_id>/export-failed/`
- **Decorators:** `@login_required`
- **LOC:** 30
- **Return:** StreamingHttpResponse CSV
- **Policy:** `enforce(ui:mail:campaign_export_failed)`

#### `campaign_retry_failed(request, campaign_id)`
- **URL:** POST `/mail/campaigns/<uuid:campaign_id>/retry-failed/`
- **Decorators:** `@login_required`
- **LOC:** 29
- **Policy:** `enforce(ui:mail:campaign_retry_failed)`

### `backend/mailer/views/campaigns/templates_views.py` (97 LOC, 4 views)

#### `campaign_save_as_template(request, campaign_id)`
- **URL:** POST `/mail/campaigns/<uuid:campaign_id>/save-as-template/`
- **Decorators:** `@login_required`
- **LOC:** 27
- **Policy:** `enforce(ui:mail:campaign_save_as_template)`

#### `campaign_create_from_template(request, template_id)`
- **URL:** POST `/mail/templates/<uuid:template_id>/use/`
- **Decorators:** `@login_required`
- **LOC:** 21
- **Policy:** `enforce(ui:mail:campaign_create_from_template)`

#### `campaign_template_delete(request, template_id)`
- **URL:** POST `/mail/templates/<uuid:template_id>/delete/`
- **Decorators:** `@login_required`
- **LOC:** 17
- **Policy:** `enforce(ui:mail:campaign_template_delete)`

#### `campaign_templates(request)`
- **URL:** GET `/mail/templates/`
- **Decorators:** `@login_required`
- **LOC:** 11
- **Policy:** `enforce(ui:mail:campaign_templates)`

### `backend/mailer/views/sending.py` (385 LOC, 4 views)

#### `campaign_start(request, campaign_id)`
- **URL:** POST `/mail/campaigns/<uuid:campaign_id>/start/`
- **Decorators:** `@login_required`
- **LOC:** 115
- **Policy:** `enforce(ui:mail:campaign_start)`
- **Models:** `Campaign`, `CampaignRecipient`

#### `campaign_pause(request, campaign_id)`
- **URL:** POST `/mail/campaigns/<uuid:campaign_id>/pause/`
- **Decorators:** `@login_required`
- **LOC:** 30
- **Policy:** `enforce(ui:mail:campaign_pause)`

#### `campaign_resume(request, campaign_id)`
- **URL:** POST `/mail/campaigns/<uuid:campaign_id>/resume/`
- **Decorators:** `@login_required`
- **LOC:** 114
- **Policy:** `enforce(ui:mail:campaign_resume)`

#### `campaign_test_send(request, campaign_id)`
- **URL:** POST `/mail/campaigns/<uuid:campaign_id>/test-send/`
- **Decorators:** `@login_required`
- **LOC:** 95
- **Policy:** `enforce(ui:mail:campaign_test_send)`

### `backend/mailer/views/recipients.py` (557 LOC, 8 views)

#### `campaign_pick(request)`
- **URL:** GET `/mail/campaigns/pick/`
- **Decorators:** `@login_required`
- **LOC:** 45
- **Return:** JSON
- **Policy:** `enforce(ui:mail:campaign_pick)`

#### `campaign_add_email(request)`
- **URL:** POST `/mail/campaigns/add-email/`
- **Decorators:** `@login_required`
- **LOC:** 58
- **Return:** JSON
- **Policy:** `enforce(ui:mail:campaign_add_email)`

#### `campaign_recipient_add(request, campaign_id)`
- **URL:** POST `/mail/campaigns/<uuid:campaign_id>/recipients/add/`
- **Decorators:** `@login_required`
- **LOC:** 56
- **Policy:** `enforce(ui:mail:campaign_recipient_add)`

#### `campaign_recipient_delete(request, campaign_id, recipient_id)`
- **URL:** POST `/mail/campaigns/<uuid:campaign_id>/recipients/<uuid:recipient_id>/delete/`
- **Decorators:** `@login_required`
- **LOC:** 18
- **Policy:** `enforce(ui:mail:campaign_recipient_delete)`

#### `campaign_recipients_bulk_delete(request, campaign_id)`
- **URL:** POST `/mail/campaigns/<uuid:campaign_id>/recipients/bulk-delete/`
- **Decorators:** `@login_required`
- **LOC:** 40
- **Policy:** `enforce(ui:mail:campaign_recipients_bulk_delete)`

#### `campaign_generate_recipients(request, campaign_id)`
- **URL:** POST `/mail/campaigns/<uuid:campaign_id>/generate/`
- **Decorators:** `@login_required`
- **LOC:** 222
- **Policy:** `enforce(ui:mail:campaign_generate_recipients)`
- **Models:** `Campaign`, `CampaignRecipient`, `Company`, `Contact`
- **Notes:** God-view (222 LOC). Генерация получателей по фильтрам компаний.

#### `campaign_recipients_reset(request, campaign_id)`
- **URL:** POST `/mail/campaigns/<uuid:campaign_id>/recipients/reset/`
- **Decorators:** `@login_required`
- **LOC:** 50
- **Policy:** `enforce(ui:mail:campaign_recipients_reset)`

#### `campaign_clear(request, campaign_id)`
- **URL:** POST `/mail/campaigns/<uuid:campaign_id>/clear/`
- **Decorators:** `@login_required`
- **LOC:** 40
- **Policy:** `enforce(ui:mail:campaign_clear)`

### `backend/mailer/views/polling.py` (216 LOC, 2 views)

#### `mail_progress_poll(request)`
- **URL:** GET `/mail/progress/poll/`
- **Decorators:** `@login_required`
- **LOC:** 139
- **Return:** JSON
- **Policy:** `enforce(ui:mail:progress_poll)`

#### `campaign_progress_poll(request, campaign_id)`
- **URL:** GET `/mail/campaigns/<uuid:campaign_id>/progress/poll/`
- **Decorators:** `@login_required`
- **LOC:** 53
- **Return:** JSON

### `backend/mailer/views/unsubscribe.py` (148 LOC, 4 views)

#### `unsubscribe(request, token)`
- **URL:** GET/POST `/unsubscribe/<str:token>/`
- **Decorators:** **`@csrf_exempt`** (публичный)
- **LOC:** 33
- **Return:** HTML
- **Models:** `Unsubscribe`, `CampaignRecipient`
- **Notes:** **Единственный `@csrf_exempt`** в проекте. Оправдан: List-Unsubscribe-Post от email-клиентов не передаёт CSRF-токен. Защита через token и IP rate limit.

#### `mail_unsubscribes_list(request)`
- **URL:** GET `/mail/unsubscribes/list/`
- **Decorators:** `@login_required`
- **LOC:** 50
- **Return:** JSON
- **Policy:** `enforce(ui:mail:unsubscribes_list)`

#### `mail_unsubscribes_delete(request)`
- **URL:** POST `/mail/unsubscribes/delete/`
- **Decorators:** `@login_required`
- **LOC:** 30
- **Return:** JSON
- **Policy:** `enforce(ui:mail:unsubscribes_delete)`

#### `mail_unsubscribes_clear(request)`
- **URL:** POST `/mail/unsubscribes/clear/`
- **Decorators:** `@login_required`
- **LOC:** 10
- **Return:** JSON
- **Policy:** `enforce(ui:mail:unsubscribes_clear)`

---

## app: audit

### `backend/audit/views.py` (4 LOC) — пустой.

---

# === ГЛАВНЫЕ АРТЕФАКТЫ ===

## 1. КРИТИЧНО: Mutating endpoints БЕЗ `@policy_required` (input для Wave 2)

Это полный список view-функций, которые изменяют состояние (POST/PUT/PATCH/DELETE), но НЕ имеют декоратора `@policy_required` и НЕ используют `enforce(...)` внутри тела. Всего ≈ 75 штук.

### 🔴 КРИТИЧНО (security-sensitive)

| View | URL | Файл:Line | Модели | Почему критично |
|------|-----|-----------|--------|------------------|
| `settings_user_magic_link_generate` | POST `/admin/users/<id>/magic-link/generate/` | settings_core.py:915 | MagicLinkToken | **Генерация magic-link от имени юзера** |
| `settings_user_delete` | POST `/admin/users/<id>/delete/` | settings_core.py:1133 | User | **Удаление пользователя** |
| `settings_user_create` | POST `/admin/users/new/` | settings_core.py:783 | User | **Создание пользователя** |
| `settings_user_update_ajax` | POST `/admin/users/<id>/update/` | settings_core.py:1084 | User | **Редактирование пользователя** |
| `settings_user_edit` | POST `/admin/users/<id>/edit/` | settings_core.py:818 | User | Редактирование пользователя |
| `settings_user_logout` | POST `/admin/users/<id>/logout/` | settings_core.py:995 | User | Принудительный logout |
| `settings_access_role` | POST `/admin/access/roles/<role>/` | settings_core.py:320 | PolicyRule | **Редактирование политик** |
| `settings_mail_save_password` | POST `/admin/mail/setup/save-password/` | settings_mail.py:79 | GlobalMailAccount | **SMTP пароль (Fernet)** |
| `settings_mail_save_config` | POST `/admin/mail/setup/save-config/` | settings_mail.py:236 | GlobalMailAccount | SMTP конфиг |
| `settings_mail_toggle_enabled` | POST `/admin/mail/setup/toggle-enabled/` | settings_mail.py:305 | GlobalMailAccount | Вкл/выкл SMTP |
| `settings_mail_test_send` | POST `/admin/mail/setup/test-send/` | settings_mail.py:141 | — | **Отправка тестового письма** |
| `settings_security` | POST `/admin/security/` | settings_integrations.py:879 | — | **Security settings** |
| `settings_amocrm_migrate` | POST `/admin/amocrm/migrate/` | settings_integrations.py:342 | Company, Contact | **Массовая миграция AmoCRM** |
| `settings_amocrm_disconnect` | POST `/admin/amocrm/disconnect/` | settings_integrations.py:326 | — | Disconnect AmoCRM |
| `settings_mobile_apps_upload` | POST `/admin/mobile-apps/upload/` | settings_mobile_apps.py:45 | MobileAppBuild | **APK upload** |
| `settings_mobile_apps_toggle` | POST `/admin/mobile-apps/<id>/toggle/` | settings_mobile_apps.py:115 | MobileAppBuild | Toggle APK active |
| `view_as_update` | POST `/admin/view-as/` | dashboard.py:102 | — | **Режим "просмотр как" (админ)** |
| `view_as_reset` | POST `/admin/view-as/reset/` | dashboard.py:184 | — | Сброс view-as |

### 🟡 ВАЖНО (справочники, настройки)

| View | URL | Файл:Line | Модели |
|------|-----|-----------|--------|
| `settings_announcements` | POST `/admin/announcements/` | settings_core.py:61 | CrmAnnouncement |
| `settings_branch_create` | POST `/admin/branches/new/` | settings_core.py:518 | Branch |
| `settings_branch_edit` | POST `/admin/branches/<id>/edit/` | settings_core.py:534 | Branch |
| `settings_company_status_create` | POST `/admin/dicts/company-status/new/` | settings_core.py:1194 | CompanyStatus |
| `settings_company_status_edit` | POST `/admin/dicts/company-status/<id>/edit/` | settings_core.py:1262 | CompanyStatus |
| `settings_company_status_delete` | POST `/admin/dicts/company-status/<id>/delete/` | settings_core.py:1281 | CompanyStatus |
| `settings_company_sphere_create` | POST `/admin/dicts/company-sphere/new/` | settings_core.py:1210 | CompanySphere |
| `settings_company_sphere_edit` | POST `/admin/dicts/company-sphere/<id>/edit/` | settings_core.py:1295 | CompanySphere |
| `settings_company_sphere_delete` | POST `/admin/dicts/company-sphere/<id>/delete/` | settings_core.py:1314 | CompanySphere |
| `settings_contract_type_create` | POST `/admin/dicts/contract-type/new/` | settings_core.py:1226 | ContractType |
| `settings_contract_type_edit` | POST `/admin/dicts/contract-type/<id>/edit/` | settings_core.py:1353 | ContractType |
| `settings_contract_type_delete` | POST `/admin/dicts/contract-type/<id>/delete/` | settings_core.py:1372 | ContractType |
| `settings_task_type_create` | POST `/admin/dicts/task-type/new/` | settings_core.py:1242 | TaskType |
| `settings_task_type_edit` | POST `/admin/dicts/task-type/<id>/edit/` | settings_core.py:1387 | TaskType |
| `settings_task_type_delete` | POST `/admin/dicts/task-type/<id>/delete/` | settings_core.py:1409 | TaskType |
| `settings_error_log_resolve` | POST `/admin/error-log/<id>/resolve/` | settings_core.py:1511 | ErrorLog |
| `settings_error_log_unresolve` | POST `/admin/error-log/<id>/unresolve/` | settings_core.py:1534 | ErrorLog |
| `settings_import` | POST `/admin/import/` | settings_integrations.py:65 | Company |
| `settings_import_tasks` | POST `/admin/import/tasks/` | settings_integrations.py:119 | Task |
| `settings_amocrm` | POST `/admin/amocrm/` | settings_integrations.py:179 | — |
| `settings_amocrm_callback` | GET `/admin/amocrm/callback/` | settings_integrations.py:246 | — |
| `settings_amocrm_contacts_dry_run` | POST `/admin/amocrm/contacts-dry-run/` | settings_integrations.py:668 | — |
| `settings_company_columns` | POST `/admin/company-columns/` | settings_integrations.py:860 | — |
| `settings_messenger_inbox_edit` | POST `/admin/messenger/inboxes/*` | settings_messenger.py:319 | Inbox |
| `settings_messenger_inbox_ready` | POST `/admin/messenger/inboxes/<id>/ready/` | settings_messenger.py:99 | Inbox |
| `settings_messenger_routing_edit` | POST `/admin/messenger/routing/*` | settings_messenger.py:776 | RoutingRule |
| `settings_messenger_routing_delete` | POST `/admin/messenger/routing/<id>/delete/` | settings_messenger.py:866 | RoutingRule |
| `settings_messenger_canned_edit` | POST `/admin/messenger/canned-responses/*` | settings_messenger.py:913 | CannedResponse |
| `settings_messenger_canned_delete` | POST `/admin/messenger/canned-responses/<id>/delete/` | settings_messenger.py:975 | CannedResponse |

### 🟢 UI/Tasks (есть ручные проверки)

| View | URL | Файл:Line | Notes |
|------|-----|-----------|-------|
| `company_cold_call_toggle` | POST `/companies/<id>/cold-call/toggle/` | company_detail.py:620 | Есть `@require_can_view_company` |
| `company_cold_call_reset` | POST `/companies/<id>/cold-call/reset/` | company_detail.py:776 | Есть `@require_can_view_company` |
| `task_bulk_reschedule_undo` | POST `/tasks/bulk-reschedule/undo/<event_id>/` | tasks.py:1617 | Только `@login_required` |
| `task_add_comment` | POST `/tasks/<id>/comment/` | tasks.py:1756 | Только `@login_required` |
| `messenger_agent_status` | POST `/messenger/me/status/` | messenger_panel.py:220 | Только `@login_required` |
| `mark_announcement_read` | POST `/notifications/announcements/<id>/read/` | notifications/views.py:231 | Только `@login_required`, **нет даже `enforce(...)`** |
| `heartbeat_view` | POST `/api/messenger/heartbeat/` | messenger/api.py:1286 | DRF `api_view`, только IsAuthenticated |
| `transfer_conversation` | POST `/api/messenger/conversations/<id>/transfer/` | messenger/api.py:1304 | DRF `api_view`, только IsAuthenticated, **но mutating (передача диалога)** |

### Рекомендация Wave 2

1. **Для `settings_*.py`** (core/integrations/messenger/mail/mobile_apps) — нужен массовый рефакторинг:
   - Добавить `@policy_required(resource_type="action", resource="ui:admin:*")` к каждому mutating view.
   - ИЛИ перенести проверку `require_admin()` в декоратор + policy.
2. **Для `dashboard.view_as_*`** — добавить `@policy_required(action, ui:admin:view_as)`.
3. **Для UI-миксовых** (`cold_call_toggle/reset` в `company_detail`) — применить унифицированный policy-prefix `ui:companies:cold_call:*`.
4. **Для `notifications.mark_announcement_read`** — добавить `enforce(...)`.
5. **Для messenger FBV** (`heartbeat_view`, `transfer_conversation`) — добавить `@permission_classes([IsAuthenticated, PolicyPermission])` с policy_resource_prefix.

---

## 2. God-views (>200 LOC), отсортированные по LOC

| LOC | View | Файл:Line | Сложность |
|-----|------|-----------|-----------|
| 434 | `settings_messenger_inbox_edit` | ui/views/settings_messenger.py:319 | ~60 |
| 362 | `campaigns` | mailer/views/campaigns/list_detail.py:32 | ~55 |
| 356 | `task_list` | ui/views/tasks.py:55 | ~55 |
| 355 | `task_create` | ui/views/tasks.py:412 | ~55 |
| 328 | `widget_send` | messenger/widget_api.py:744 | ~50 |
| 321 | `widget_bootstrap` | messenger/widget_api.py:129 | ~50 |
| 303 | `mail_admin` | mailer/views/settings.py:122 | ~45 |
| 294 | `campaign_detail` | mailer/views/campaigns/list_detail.py:395 | ~45 |
| 288 | `settings_amocrm_migrate` | ui/views/settings_integrations.py:342 | ~45 |
| 276 | `settings_calls_stats` | ui/views/settings_integrations.py:1124 | ~40 |
| 261 | `company_bulk_transfer` | ui/views/company_list.py:652 | ~40 |
| 257 | `company_export` | ui/views/company_list.py:915 | ~30 |
| 256 | `company_list` | ui/views/company_list.py:60 | ~40 |
| 246 | `cold_calls_report_month` | ui/views/reports.py:302 | ~35 |
| 243 | `company_detail` | ui/views/company_detail.py:80 | ~45 |
| 233 | `company_detail_v3_preview` | ui/views/company_detail_v3.py:44 | ~40 |
| 233 | `settings_users` | ui/views/settings_core.py:551 | ~40 |
| 222 | `campaign_generate_recipients` | mailer/views/recipients.py:246 | ~35 |
| 221 | `cold_calls_report_day` | ui/views/reports.py:81 | ~35 |
| 214 | `OperatorConsumer` (WS) | messenger/consumers.py:25 | ~30 |
| 210 | `company_list_ajax` | ui/views/company_list.py:316 | ~30 |
| 208 | `task_bulk_reschedule` | ui/views/tasks.py:1296 | ~35 |
| 200 | `SecureLoginView` | accounts/views.py:36 | ~25 |
| 198 | `widget_stream` | messenger/widget_api.py:1262 | ~25 |
| 196 | `settings_access` | ui/views/settings_core.py:125 | ~30 |
| 190 | `widget_poll` | messenger/widget_api.py:1072 | ~25 |
| 188 | `settings_access_role` | ui/views/settings_core.py:321 | ~30 |
| 183 | `messenger_conversations_unified` | ui/views/messenger_panel.py:38 | ~30 |
| 176 | `widget_offhours_request` | messenger/widget_api.py:533 | ~30 |

**Кандидаты на Wave 9 (разбиение):** топ-10 требуют вынесения в service-layer. Особенно:
- `settings_messenger_inbox_edit` (434 LOC) — единый view для create+edit, нужно разделить + вынести сервис.
- `task_create` / `task_list` — два god-view в одном файле.
- `widget_send` / `widget_bootstrap` — публичные API, нужна декомпозиция на sub-functions.

---

## 3. Views с `@csrf_exempt`

**Всего: 1 view.**

| View | URL | Файл:Line | Обоснование |
|------|-----|-----------|-------------|
| `unsubscribe` | GET/POST `/unsubscribe/<token>/` | mailer/views/unsubscribe.py:24 | List-Unsubscribe-Post от email-клиентов не несёт CSRF-токен. Защита — rate limit по IP + одноразовый token. **OK.** |

---

## 4. Views БЕЗ `@login_required` (публичные)

| View | URL | Файл:Line | Обоснование |
|------|-----|-----------|-------------|
| `handler404` | (handler) | crm/views.py:9 | OK (error page) |
| `handler500` | (handler) | crm/views.py:14 | OK (error page) |
| `robots_txt` | GET `/robots.txt` | crm/views.py:20 | OK (public) |
| `security_txt` | GET `/.well-known/security.txt` | crm/views.py:31 | OK (public) |
| `sw_push_js` | GET `/sw-push.js` | crm/views.py:59 | OK (Service Worker) |
| `metrics_endpoint` | GET `/metrics` | crm/views.py:79 | **⚠ Проверить:** Prometheus metrics — должен быть IP-whitelist! |
| `health_check` | GET `/health/` | crm/views.py:186 | OK (health check) |
| `SecureLoginView` | GET/POST `/login/` | accounts/views.py:36 | OK (login) |
| `magic_link_login` | GET `/auth/magic/<token>/` | accounts/views.py:236 | OK (auth) |
| `SecureTokenObtainPairView` | POST `/api/token/` | accounts/jwt_views.py:29 | OK (JWT login) |
| `LoggedTokenRefreshView` | POST `/api/token/refresh/` | accounts/jwt_views.py:99 | OK (JWT refresh) |
| `widget_test_page` | GET `/widget-test/` | messenger/views.py:48 | **⚠ Проверить:** testing-страница, в prod не должна быть доступна |
| `widget_bootstrap` | POST/OPTIONS `/api/widget/bootstrap/` | widget_api.py:129 | OK (widget public API + token) |
| `widget_contact_update` | POST `/api/widget/contact/` | widget_api.py:453 | OK |
| `widget_offhours_request` | POST `/api/widget/offhours-request/` | widget_api.py:533 | OK |
| `widget_send` | POST `/api/widget/send/` | widget_api.py:744 | OK |
| `widget_poll` | GET `/api/widget/poll/` | widget_api.py:1072 | OK |
| `widget_stream` | GET `/api/widget/stream/` | widget_api.py:1262 | OK (SSE) |
| `widget_typing` | POST `/api/widget/typing/` | widget_api.py:1520 | OK |
| `widget_mark_read` | POST `/api/widget/mark_read/` | widget_api.py:1560 | OK |
| `widget_rate` | POST `/api/widget/rate/` | widget_api.py:1630 | OK |
| `widget_campaigns` | GET `/api/widget/campaigns/` | widget_api.py:1701 | OK |
| `widget_attachment_download` | GET `/api/widget/attachment/<id>/` | widget_api.py:1463 | OK |
| `unsubscribe` | GET/POST `/unsubscribe/<token>/` | mailer/views/unsubscribe.py:24 | OK (email unsubscribe) |
| `QrTokenExchangeView` | POST `/api/phone/qr/exchange/` | phonebridge/api.py:727 | OK (QR auth exchange) |

**Красный флаг:** `metrics_endpoint` и `widget_test_page` — проверить, что они защищены на уровне nginx / не доступны в prod.

---

## 5. Views по типам возврата

### HTML (render/redirect/TemplateResponse) — ≈ 150
- Все SSR-страницы в `ui/views/*.py`
- `mailer/views/campaigns/*`
- `accounts/views.py::SecureLoginView, magic_link_login`
- `notifications/views.py::all_notifications, all_reminders`
- `crm/views.py::handler404, handler500, robots_txt, security_txt, sw_push_js`

### JSON (JsonResponse / DRF Response) — ≈ 75
- Все DRF ViewSets: `CompanyViewSet`, `ContactViewSet`, `CompanyNoteViewSet`, `TaskTypeViewSet`, `TaskViewSet`, `ConversationViewSet`, `CannedResponseViewSet`, `ConversationLabelViewSet`, `PushSubscriptionViewSet`, `CampaignViewSet`, `AutomationRuleViewSet`, `ReportingViewSet`, `MacroViewSet`
- Все DRF `@api_view`: `branches_list_view`, `heartbeat_view`, `transfer_conversation`, все `widget_*` (кроме stream/attachment)
- AJAX JSON views: `company_list_ajax`, `company_autocomplete`, `company_phone_*`, `notifications/poll`, `dashboard_poll`, `mail_progress_poll`, `campaign_progress_poll`, `mail_unsubscribes_*`, `settings_user_form_ajax`, `settings_user_update_ajax`, `settings_user_delete`, `settings_error_log_details`, `mark_announcement_read`, `preferences_*`
- Phonebridge: все `*View(APIView)`

### Stream / SSE — 3
- `widget_stream` (messenger/widget_api.py:1262) — SSE для виджета
- `ConversationViewSet.stream` (messenger/api.py:788) — SSE для оператора
- `ConversationViewSet.notifications_stream` (messenger/api.py:506) — SSE notifications

### WebSocket — 2 (не обычные views)
- `OperatorConsumer` (messenger/consumers.py:25)
- `WidgetConsumer` (messenger/consumers.py:239)

### File / FileResponse — ≈ 8
- `company_note_attachment_open`, `company_note_attachment_by_id_open`, `company_note_attachment_download`, `company_note_attachment_by_id_download`
- `mobile_app_download`, `mobile_app_qr_image`
- `campaign_attachment_download`, `campaign_export_failed` (StreamingHttpResponse CSV)
- `widget_attachment_download`

---

## Сводная статистика по декораторам

| Декоратор | Количество использований |
|-----------|--------------------------|
| `@login_required` | ≈ 175 |
| `@policy_required` | 90 |
| `@require_can_view_company` / `@require_can_view_note_company` | ≈ 20 |
| `@require_POST` | 7 |
| `@require_http_methods` | 1 |
| `@transaction.atomic` | 2 |
| `@csrf_exempt` | 1 |
| `@xframe_options_exempt` | 1 |
| `@api_view` + `@permission_classes` | ≈ 15 (widget_api + messenger/api FBV) |
| `enforce(...)` внутри тела | 56 (mailer, notifications, phonebridge) |

---

## Итоговый путь к policy-coverage 100%

Если считать, что view защищён policy если ИЛИ есть `@policy_required` ИЛИ есть `enforce(...)` в теле ИЛИ есть `PolicyPermission` в DRF:

- **Защищены:** ≈ 150 views (ui/views/{dashboard, reports, company_list, company_detail, company_detail_v3, tasks, messenger_panel, mobile}, mailer/views/*, notifications/views.py (5 из 6), phonebridge/api.py, messenger/api.py DRF viewsets, companies/api.py, tasksapp/api.py, widget_api.py через throttle + object-level)
- **НЕ защищены (Wave 2 target):**
  - `ui/views/settings_core.py` — 34/34 без policy
  - `ui/views/settings_integrations.py` — 15/15 без policy
  - `ui/views/settings_messenger.py` — 15/15 без policy
  - `ui/views/settings_mail.py` — 5/5 без policy
  - `ui/views/settings_mobile_apps.py` — 3/3 без policy
  - `ui/views/dashboard.py::view_as_*` — 2 views
  - `ui/views/company_detail.py::company_cold_call_toggle/reset` — 2 views
  - `ui/views/tasks.py::task_bulk_reschedule_undo, task_add_comment` — 2 views
  - `ui/views/messenger_panel.py::messenger_agent_status` — 1 view
  - `ui/views/analytics_v2.py::analytics_v2_home` — 1 view
  - `notifications/views.py::mark_announcement_read` — 1 view
  - `messenger/api.py::heartbeat_view, transfer_conversation` — 2 views (FBV без PolicyPermission)

**Итого ≈ 83 mutating/sensitive endpoints требуют policy в Wave 2.**
