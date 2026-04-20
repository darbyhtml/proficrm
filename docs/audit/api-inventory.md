# Инвентаризация API CRM ПРОФИ
_Снапшот: 2026-04-20. Wave 0.1 — полная инвентаризация REST API._

Источник: `backend/crm/urls.py` (главный URLconf) + `backend/messenger/urls.py` + DRF viewsets по app'ам.
Стек: DRF 3.16.1 + drf-spectacular 0.28.0 (установлен, но **фактически не используется** — ни одной `@extend_schema` аннотации в коде нет), SimpleJWT (`rest_framework_simplejwt`), Django 6.0.1.

---

## Сводка

| Метрика | Значение |
|---|---|
| Всего REST endpoint'ов (уникальных URL+method) | **~150** |
| Router ViewSets (DRF routered) | **13** (×2 из-за дублирования router + router_v1 для 7 из них) |
| `@api_view` функций (function-based DRF) | **14** (Widget API 11 + messenger 3) |
| `APIView` class-based (phonebridge) | **13** |
| ViewSets с `@action` (custom actions) | **18 actions** на ConversationViewSet/Push/Reporting/Macro |
| **Public endpoints** (AllowAny / widget / JWT login) | **13** (11 widget + 2 JWT token) |
| **Internal endpoints** | **~137** |
| Endpoints **без throttling** | **~130** (всё кроме 4 phonebridge scoped + 4 widget custom throttles + JWT login rate limit внутри view) |
| Endpoints **без `@extend_schema`** | **100% (ВСЕ)** — красный флаг для OpenAPI |
| Endpoints с **AllowAny** | **12** (все Widget + QrTokenExchange) |
| Duplicate endpoints (`/api/` vs `/api/v1/`) | **~50** (router зарегистрирован дважды + все phone-view дублируются) |

### Глобальные настройки DRF (`backend/crm/settings.py:465-492`)

```python
"DEFAULT_AUTHENTICATION_CLASSES": (
    "rest_framework_simplejwt.authentication.JWTAuthentication",
    "rest_framework.authentication.SessionAuthentication",
),
"DEFAULT_PERMISSION_CLASSES": ("rest_framework.permissions.IsAuthenticated",),
"DEFAULT_FILTER_BACKENDS": (DjangoFilterBackend, SearchFilter, OrderingFilter),
"EXCEPTION_HANDLER": "core.exceptions.custom_exception_handler",
"DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
"DEFAULT_THROTTLE_CLASSES": [],  # <-- ПУСТО
"DEFAULT_THROTTLE_RATES": {
    "phone_pull":        "120/min",
    "phone_heartbeat":    "30/min",
    "phone_telemetry":    "20/min",
    "mobile_app_latest":  "10/min",
},
```

**Pagination**: `DEFAULT_PAGINATION_CLASS` **не задан** и `PAGE_SIZE` тоже → DRF по умолчанию **не пагинирует**. ConversationViewSet вручную вызывает `self.paginate_queryset()`, но без глобального пагинатора это no-op.

**OpenAPI / Swagger**: `/api/schema/` и `/api/schema/swagger-ui/` открыты **только при DEBUG=True**. В проде OpenAPI недоступен.

### Authentication (глобально)

| Способ | Где применяется |
|---|---|
| **SessionAuthentication** | Вся Web UI (`ui/views.py` через `@login_required` — не DRF), DRF endpoint'ы для браузера |
| **JWTAuthentication** (SimpleJWT) | Все DRF endpoints по умолчанию + phonebridge (явно указано `authentication_classes = [JWTAuthentication]`) |
| **MagicLinkToken** | Только UI: `accounts.views.magic_link_login` → `/auth/magic/<token>/`. Не REST |
| **AllowAny** (без auth) | Widget API (`api/widget/...`) + `QrTokenExchangeView` (обмен QR → JWT) |
| **SimpleJWT blacklist** | Включён (`BLACKLIST_AFTER_ROTATION=True`). Endpoints `phone/logout/` и `phone/logout/all/` используют |

---

## 1. Public endpoints

### 1.1. Widget API — `/api/widget/*` (PUBLIC, без авторизации)

Все зарегистрированы в `backend/crm/urls.py:150-160` + один дополнительный роут в `backend/messenger/widget_api.py` (SSE). Все используют `@api_view + authentication_classes([]) + permission_classes([AllowAny])`. CORS через ручной `_add_widget_cors_headers()` (django-cors-headers не работает с public API).

#### 1.1.1. `POST /api/widget/bootstrap/` — PUBLIC

- **View:** `messenger.widget_api.widget_bootstrap` (function-based `@api_view(["POST", "OPTIONS"])`)
- **Serializer (in):** `WidgetBootstrapSerializer` (messenger/serializers.py)
- **Serializer (out):** `WidgetBootstrapResponseSerializer`
- **Permissions:** `AllowAny`
- **Authentication:** `[]` (явно отключена)
- **Throttling:** `WidgetBootstrapThrottle` (10/min per IP, 20/min per widget_token; Redis-based, не DRF-стандарт)
- **Filters:** —
- **Pagination:** —
- **OpenAPI:** нет `@extend_schema`
- **Notes:** CORS whitelist через `Inbox.allowed_domains` → `enforce_widget_origin_allowed()`. Anti-spam: math captcha по IP. Создаёт `widget_session_token` в Redis с TTL и привязкой к IP.

#### 1.1.2. `POST /api/widget/contact/` — PUBLIC

- **View:** `messenger.widget_api.widget_contact_update` (`@api_view(["POST", "OPTIONS"])`)
- **Serializer:** inline (ручная валидация)
- **Permissions:** `AllowAny`
- **Throttling:** ❌ **НЕТ** (только валидация сессии)
- **OpenAPI:** нет

#### 1.1.3. `POST /api/widget/offhours-request/` — PUBLIC

- **View:** `messenger.widget_api.widget_offhours_request` (`@api_view(["POST", "OPTIONS"])`)
- **Serializer:** ручная валидация полей
- **Permissions:** `AllowAny`
- **Throttling:** ❌ **НЕТ**
- **OpenAPI:** нет
- **Notes:** F5 2026-04-18 — заявка вне рабочих часов. Переводит Conversation → WAITING_OFFLINE.

#### 1.1.4. `POST /api/widget/send/` — PUBLIC

- **View:** `messenger.widget_api.widget_send` (`@api_view(["POST"])`)
- **Serializer (in):** `WidgetSendSerializer` (только для JSON; multipart парсится вручную)
- **Serializer (out):** `WidgetSendResponseSerializer`
- **Permissions:** `AllowAny`
- **Throttling:** `WidgetSendThrottle` (30/min per session, 60/min per IP)
- **OpenAPI:** нет
- **Notes:** captcha required, duplicate-message spam defense (cache 300s), file uploads + magic-bytes validation. Публичный CORS.

#### 1.1.5. `GET /api/widget/poll/` — PUBLIC

- **View:** `messenger.widget_api.widget_poll` (`@api_view(["GET"])`)
- **Serializer:** inline dict response
- **Permissions:** `AllowAny`
- **Throttling:** `WidgetPollThrottle` (20/min per session, min interval 2s)
- **OpenAPI:** нет

#### 1.1.6. `GET /api/widget/stream/` — PUBLIC (SSE)

- **View:** `messenger.widget_api.widget_stream` (обычный Django view, не DRF — из-за content-negotiation text/event-stream)
- **Serializer:** SSE frames
- **Permissions:** AllowAny (проверка через `widget_session_token`)
- **Throttling:** ❌ **НЕТ**
- **OpenAPI:** нет
- **Notes:** 25-секундное long-poll SSE-соединение, keep-alive, автоматический reconnect

#### 1.1.7. `POST /api/widget/typing/` — PUBLIC

- **View:** `messenger.widget_api.widget_typing`
- **Permissions:** `AllowAny`
- **Throttling:** `WidgetPollThrottle`
- **OpenAPI:** нет

#### 1.1.8. `POST /api/widget/mark_read/` — PUBLIC

- **View:** `messenger.widget_api.widget_mark_read`
- **Permissions:** `AllowAny`
- **Throttling:** `WidgetPollThrottle`
- **OpenAPI:** нет

#### 1.1.9. `POST /api/widget/rate/` — PUBLIC

- **View:** `messenger.widget_api.widget_rate`
- **Permissions:** `AllowAny`
- **Throttling:** `WidgetPollThrottle`
- **OpenAPI:** нет
- **Notes:** CSAT: score 1-5 (stars) или 0-10 (NPS), one-time per conversation

#### 1.1.10. `GET /api/widget/campaigns/` — PUBLIC

- **View:** `messenger.widget_api.widget_campaigns`
- **Permissions:** `AllowAny`
- **Throttling:** ❌ **НЕТ**
- **OpenAPI:** нет

#### 1.1.11. `GET /api/widget/attachment/<int:attachment_id>/` — PUBLIC

- **View:** `messenger.widget_api.widget_attachment_download` (returns `FileResponse`)
- **Permissions:** `AllowAny` (проверка session → conversation ownership)
- **Throttling:** ❌ **НЕТ**
- **OpenAPI:** нет

### 1.2. JWT Auth endpoints — PUBLIC (login)

#### 1.2.1. `POST /api/token/` + `POST /api/v1/token/` — PUBLIC

- **View:** `accounts.jwt_views.SecureTokenObtainPairView` (наследует `rest_framework_simplejwt.views.TokenObtainPairView`)
- **Serializer:** SimpleJWT default `TokenObtainPairSerializer`
- **Permissions:** AllowAny (дефолт SimpleJWT)
- **Throttling:** inline через `is_ip_rate_limited(ip, "jwt_login", 5/min)` + `is_user_locked_out(username)` (не DRF throttle)
- **OpenAPI:** нет
- **Notes:** Брутфорс-защита: 5 попыток/мин per IP, 15 мин lockout per username. Возвращает `{access, refresh, is_admin}`. Дублируется на `/api/v1/token/` без namespace.

#### 1.2.2. `POST /api/token/refresh/` + `POST /api/v1/token/refresh/` — PUBLIC

- **View:** `accounts.jwt_views.LoggedTokenRefreshView` (наследует `TokenRefreshView`)
- **Permissions:** AllowAny
- **Throttling:** ❌ **НЕТ**
- **OpenAPI:** нет

#### 1.2.3. `POST /api/phone/qr/exchange/` + `/api/v1/phone/qr/exchange/` — PUBLIC

- **View:** `phonebridge.api.QrTokenExchangeView`
- **Serializer:** `QrTokenExchangeSerializer` (inline: `{token}`)
- **Permissions:** `[]` (пустой список = AllowAny)
- **Throttling:** ❌ **НЕТ** (!)
- **OpenAPI:** нет
- **Notes:** Обмен одноразового QR-токена (TTL 5 мин) на `{access, refresh, username, is_admin}`. Потенциальная поверхность для угона, защита — one-time-use token.

---

## 2. Internal endpoints

Все перечисленные ниже требуют авторизации (JWT или Session), `permission_classes = [IsAuthenticated]` + (для большинства CRM-viewsets) `PolicyPermission` из ABAC-движка (`backend/policy/drf.py`).

### 2.1. Companies API (`backend/companies/api.py`)

Зарегистрирован в `crm/urls.py:86-88, 103-105` — дважды (router + router_v1).

#### 2.1.1. CompanyViewSet — `/api/companies/` и `/api/v1/companies/`

- **Methods:** GET (list/retrieve), POST (create), PUT/PATCH (update/partial_update), DELETE (destroy)
- **Serializer:** `CompanySerializer` (с custom validators: normalize_phone, normalize_inn, normalize_work_schedule)
- **Permissions:** `[IsAuthenticated, PolicyPermission]` + `policy_resource_prefix = "api:companies"`
- **Authentication:** JWT + Session (глобально)
- **Throttling:** ❌ **НЕТ**
- **Filters:** `DjangoFilterBackend` (branch, responsible, status, contract_type, is_cold_call) + `CompanySearchFilterBackend` (PostgreSQL FTS через `get_company_search_backend()`) + `OrderingFilter` (updated_at, created_at, name)
- **Pagination:** **нет** (page_size не задан)
- **OpenAPI:** нет `@extend_schema`
- **Notes:** `perform_create` реализует идемпотентность (дедупликация за 10 сек по created_by+name+inn). `perform_update` проверяет role/branch.

**URL paths (от router):**
- `GET/POST   /api/companies/`
- `GET/PUT/PATCH/DELETE /api/companies/<uuid:pk>/`
- `GET/POST   /api/v1/companies/`
- `GET/PUT/PATCH/DELETE /api/v1/companies/<uuid:pk>/`

#### 2.1.2. ContactViewSet — `/api/contacts/` и `/api/v1/contacts/`

- **Methods:** GET/POST/PUT/PATCH/DELETE
- **Serializer:** `ContactSerializer`
- **Permissions:** `[IsAuthenticated, PolicyPermission]` + `policy_resource_prefix = "api:contacts"`
- **Throttling:** ❌ **НЕТ**
- **Filters:** DjangoFilterBackend (`company`), SearchFilter (first_name, last_name, position, company__name), OrderingFilter
- **Pagination:** нет
- **OpenAPI:** нет

#### 2.1.3. CompanyNoteViewSet — `/api/company-notes/` и `/api/v1/company-notes/`

- **Methods:** GET/POST/PUT/PATCH/DELETE
- **Serializer:** `CompanyNoteSerializer` (author/created_at readonly)
- **Permissions:** `[IsAuthenticated, PolicyPermission]` + `policy_resource_prefix = "api:company_notes"`
- **Throttling:** ❌ **НЕТ**
- **Filters:** DjangoFilterBackend (`company`), OrderingFilter (created_at)
- **Pagination:** нет
- **OpenAPI:** нет

### 2.2. Tasks API (`backend/tasksapp/api.py`)

#### 2.2.1. TaskTypeViewSet — `/api/task-types/` и `/api/v1/task-types/`

- **Methods:** GET/POST/PUT/PATCH/DELETE
- **Serializer:** `TaskTypeSerializer` (id, name)
- **Permissions:** `[IsAuthenticated, PolicyPermission]` + `policy_resource_prefix = "api:task_types"`
- **Throttling:** ❌ **НЕТ**
- **Filters:** default (DjangoFilterBackend + SearchFilter + OrderingFilter из settings), `search_fields = ("name",)`
- **Pagination:** нет
- **OpenAPI:** нет

#### 2.2.2. TaskViewSet — `/api/tasks/` и `/api/v1/tasks/`

- **Methods:** GET/POST/PUT/PATCH/DELETE
- **Serializer:** `TaskSerializer` (с валидацией RRULE iCalendar RFC 5545, INTERVAL 1-366, COUNT ≤1000)
- **Permissions:** `[IsAuthenticated, PolicyPermission]` + `policy_resource_prefix = "api:tasks"`
- **Throttling:** ❌ **НЕТ**
- **Filters:** DjangoFilterBackend (status, assigned_to, company, type), SearchFilter (title, description, company__name), OrderingFilter (created_at, due_at)
- **Pagination:** нет
- **OpenAPI:** нет
- **Notes:** `perform_create` дедуплицирует за 10 сек; поддерживает `apply_to_org_branches=true` для массового создания задач по всем филиалам организации.

### 2.3. Messenger API (`backend/messenger/api.py`)

Все наследуют `MessengerEnabledApiMixin` — проверка feature-флага `MESSENGER_ENABLED` в `initial()`.

#### 2.3.1. ConversationViewSet — `/api/conversations/` и `/api/v1/conversations/`

- **Methods:** GET (list/retrieve), PATCH (partial_update), DELETE (destroy — только для ADMIN/superuser)
- **Serializer:** `ConversationSerializer`, `MessageSerializer` (для action messages)
- **Permissions:** `[IsAuthenticated, PolicyPermission]` + `policy_resource_prefix = "api:messenger:conversations"`
- **Throttling:** ❌ **НЕТ** (но `read` action использует троттлинг `services.touch_assignee_last_seen`)
- **Filters:** ручные query_params: `q`, `status`, `mine`, `assignee`
- **Pagination:** вручную (`self.paginate_queryset`), но глобальный пагинатор не задан → no-op
- **OpenAPI:** нет
- **Custom @action:**
  - `POST /api/conversations/<pk>/read/` — отметить прочитанным (только assignee MANAGER/ADMIN)
  - `POST /api/conversations/merge-contacts/` — слияние контактов (только ADMIN)
  - `GET  /api/conversations/unread-count/` — badge count (Redis cache 30s)
  - `GET  /api/conversations/agents/` — список менеджеров (с filter branch_id, online)
  - `POST /api/conversations/<pk>/needs-help/` — флаг «нужна помощь»
  - `POST /api/conversations/<pk>/contacted-back/` — «Я связался» (off-hours)
  - `POST /api/conversations/bulk/` — bulk close/reopen/assign
  - `GET  /api/conversations/notifications/stream/` — SSE (account-wide, 55s)
  - `GET  /api/conversations/<pk>/messages/` — список сообщений (since/before/limit)
  - `POST /api/conversations/<pk>/messages/` — создать OUT/INTERNAL сообщение (+attachments)
  - `GET  /api/conversations/<pk>/stream/` — SSE для одного диалога (30s)
  - `GET  /api/conversations/<pk>/typing/` — статус печати
  - `POST /api/conversations/<pk>/typing/` — отметить «оператор печатает» (Redis TTL 8s)
  - `GET  /api/conversations/<pk>/context/` — агрегированные данные правой панели

#### 2.3.2. CannedResponseViewSet — `/api/canned-responses/` и `/api/v1/canned-responses/`

- **Methods:** GET/POST/PUT/PATCH/DELETE
- **Serializer:** `CannedResponseSerializer`
- **Permissions:** `[IsAuthenticated, PolicyPermission]` + `policy_resource_prefix = "api:messenger:canned-responses"`
- **Throttling:** ❌ НЕТ
- **Filters:** ручной `?quick=1` (быстрые кнопки)
- **OpenAPI:** нет

#### 2.3.3. ConversationLabelViewSet — `/api/conversation-labels/`

- **Methods:** GET/POST/PUT/PATCH/DELETE
- **Serializer:** `ConversationLabelSerializer`
- **Permissions:** `[IsAuthenticated]` — ⚠️ **БЕЗ PolicyPermission** (в отличие от других messenger-viewsets)
- **Throttling:** ❌ НЕТ
- **OpenAPI:** нет

#### 2.3.4. PushSubscriptionViewSet — `/api/push/`

- **Type:** `viewsets.ViewSet` (не ModelViewSet)
- **Permissions:** `[IsAuthenticated]` — ⚠️ **БЕЗ PolicyPermission**
- **Throttling:** ❌ НЕТ
- **OpenAPI:** нет
- **Actions:**
  - `GET  /api/push/vapid-key/` — публичный VAPID-ключ
  - `POST /api/push/subscribe/` — сохранить push-subscription
  - `POST /api/push/unsubscribe/` — деактивировать

#### 2.3.5. CampaignViewSet — `/api/campaigns/`

- **Methods:** GET/POST/PUT/PATCH/DELETE (ModelViewSet)
- **Serializer:** inline `CampaignSerializer` (inbox, title, message, url_pattern, time_on_page, status, only_during_business_hours, created_at)
- **Permissions:** `[IsAuthenticated]` — ⚠️ **БЕЗ PolicyPermission**
- **Throttling:** ❌ НЕТ
- **OpenAPI:** нет
- **Notes:** Это «проактивные кампании» для widget popup, не email campaigns.

#### 2.3.6. AutomationRuleViewSet — `/api/automation-rules/`

- **Methods:** GET/POST/PUT/PATCH/DELETE
- **Serializer:** inline `AutomationRuleSerializer`
- **Permissions:** `[IsAuthenticated]` — ⚠️ **БЕЗ PolicyPermission**
- **Throttling:** ❌ НЕТ
- **OpenAPI:** нет

#### 2.3.7. ReportingViewSet — `/api/messenger-reports/`

- **Type:** `viewsets.ViewSet`
- **Permissions:** `[IsAuthenticated]` — ⚠️ **БЕЗ PolicyPermission**
- **Throttling:** ❌ НЕТ
- **OpenAPI:** нет
- **Actions:**
  - `GET /api/messenger-reports/overview/` — метрики FRT, reply_time, CSAT, resolved/total (?days=7)

#### 2.3.8. MacroViewSet — `/api/macros/`

- **Methods:** GET/POST/PUT/PATCH/DELETE + action execute
- **Serializer:** `MacroSerializer`
- **Permissions:** `[IsAuthenticated]` — ⚠️ **БЕЗ PolicyPermission**
- **Throttling:** ❌ НЕТ
- **OpenAPI:** нет
- **Actions:**
  - `POST /api/macros/<pk>/execute/` — выполнить макрос на conversation

#### 2.3.9. Messenger function-based views (вне router)

Зарегистрированы в `backend/messenger/urls.py`:

- `GET  /api/messenger/branches/` — `branches_list_view` — `@api_view(["GET"]) + @permission_classes([IsAuthenticated])`. Throttling ❌.
- `POST /api/messenger/heartbeat/` — `heartbeat_view` — обновляет `messenger_online/messenger_last_seen`. Throttling ❌.
- `POST /api/messenger/conversations/<int:conversation_id>/transfer/` — `transfer_conversation` — передача диалога другому оператору с обязательной причиной (`TransferRequestSerializer`). Throttling ❌.

### 2.4. Phonebridge API (`backend/phonebridge/api.py`)

Все — class-based `APIView` + явно `authentication_classes = [JWTAuthentication]`. Зарегистрированы в `backend/crm/urls.py:135-148, 162-173` — **дважды**: `/api/phone/...` (legacy) и `/api/v1/phone/...` (versioned). Каждая пара — одна и та же view-class.

Все используют `enforce(user, resource_type="action", resource="phone:...")` — ABAC policy engine (не DRF permission).

#### 2.4.1. `POST /api/phone/devices/register/` + `/api/v1/.../`

- **View:** `RegisterDeviceView`
- **Serializer:** `RegisterDeviceSerializer` (device_id, device_name, fcm_token)
- **Permissions:** `[IsAuthenticated]`
- **Auth:** `[JWTAuthentication]` (явно)
- **Throttling:** ❌ **НЕТ**
- **OpenAPI:** нет

#### 2.4.2. `POST /api/phone/devices/heartbeat/` + `/api/v1/.../`

- **View:** `DeviceHeartbeatView`
- **Serializer:** `DeviceHeartbeatSerializer`
- **Permissions:** `[IsAuthenticated]`
- **Throttling:** `ScopedRateThrottle` scope=`phone_heartbeat` → **30/min**
- **OpenAPI:** нет

#### 2.4.3. `GET /api/phone/calls/pull/` + `/api/v1/.../`

- **View:** `PullCallView`
- **Permissions:** `[IsAuthenticated]`
- **Throttling:** `ScopedRateThrottle` scope=`phone_pull` → **120/min**
- **OpenAPI:** нет
- **Notes:** Использует `select_for_update(skip_locked=True)` для атомарной выдачи CallRequest.

#### 2.4.4. `POST /api/phone/calls/update/` + `/api/v1/.../`

- **View:** `UpdateCallInfoView`
- **Serializer:** `UpdateCallInfoSerializer` (graceful handling неизвестных choices)
- **Permissions:** `[IsAuthenticated]`
- **Throttling:** ❌ **НЕТ**
- **OpenAPI:** нет

#### 2.4.5. `POST /api/phone/telemetry/` + `/api/v1/.../`

- **View:** `PhoneTelemetryView`
- **Serializer:** `TelemetryBatchSerializer` (max 100 items/batch)
- **Permissions:** `[IsAuthenticated]`
- **Throttling:** `ScopedRateThrottle` scope=`phone_telemetry` → **20/min**
- **OpenAPI:** нет

#### 2.4.6. `POST /api/phone/logs/` + `/api/v1/.../`

- **View:** `PhoneLogUploadView`
- **Serializer:** `PhoneLogBundleSerializer` (max payload 50KB)
- **Permissions:** `[IsAuthenticated]`
- **Throttling:** ❌ **НЕТ**
- **OpenAPI:** нет

#### 2.4.7. `POST /api/phone/qr/create/` + `/api/v1/.../`

- **View:** `QrTokenCreateView`
- **Permissions:** `[IsAuthenticated]`
- **Throttling:** inline `is_ip_rate_limited(ip, "qr_token_create", 1, 10)` (1/10sec per IP; не DRF throttle)
- **OpenAPI:** нет
- **Notes:** Создаёт одноразовый token, TTL 5 мин, возвращает `{token, expires_at}`.

#### 2.4.8. `POST /api/phone/qr/exchange/` + `/api/v1/.../` — **PUBLIC** (см. §1.2.3)

#### 2.4.9. `GET /api/phone/qr/status/` + `/api/v1/.../`

- **View:** `QrTokenStatusView`
- **Permissions:** `[IsAuthenticated]`
- **Auth:** default (SessionAuthentication для web или JWT)
- **Throttling:** ❌ **НЕТ**
- **OpenAPI:** нет

#### 2.4.10. `GET /api/phone/user/info/` + `/api/v1/.../`

- **View:** `UserInfoView`
- **Permissions:** `[IsAuthenticated]`
- **Auth:** `[JWTAuthentication]`
- **Throttling:** ❌ **НЕТ**
- **OpenAPI:** нет

#### 2.4.11. `POST /api/phone/logout/` + `/api/v1/.../`

- **View:** `LogoutView`
- **Serializer:** `LogoutSerializer`
- **Permissions:** `[IsAuthenticated]`
- **Auth:** `[JWTAuthentication]`
- **Throttling:** ❌ **НЕТ**
- **OpenAPI:** нет
- **Notes:** Blacklist refresh token через `token.blacklist()`, audit log.

#### 2.4.12. `POST /api/phone/logout/all/` + `/api/v1/.../`

- **View:** `LogoutAllView`
- **Permissions:** `[IsAuthenticated]`
- **Auth:** `[JWTAuthentication]`
- **Throttling:** ❌ **НЕТ**
- **OpenAPI:** нет
- **Notes:** Blacklist всех OutstandingToken пользователя.

#### 2.4.13. `GET /api/phone/app/latest/` (только legacy путь, **НЕТ `/api/v1/`** дублирования)

- **View:** `MobileAppLatestView`
- **Permissions:** `[IsAuthenticated]`
- **Auth:** `[JWTAuthentication]`
- **Throttling:** `ScopedRateThrottle` scope=`mobile_app_latest` → **10/min**
- **OpenAPI:** нет
- **Notes:** F9 2026-04-18. Возвращает метаданные последнего активного APK `MobileAppBuild` (env=production).

### 2.5. Dashboard / UI endpoints (не DRF, но JSON-based)

`backend/ui/views.py` — это **не DRF**, а обычные Django views с `@login_required`. В задаче «инвентаризация REST API» эти endpoints **не учитываются** как REST (нет DRF infrastructure, нет сериализаторов), но они возвращают JSON и AJAX-HTML partial. Для полноты — краткий список:

- `GET /api/dashboard/poll/` — long-poll dashboard updates (UI)
- `GET /notifications/poll/` — long-poll уведомлений
- `POST /notifications/mark-all-read/`, `POST /notifications/<id>/read/`, `GET /notifications/all/`, `POST /notifications/announcements/<id>/read/`
- ~90 UI-endpoints для админки (компании, задачи, настройки) — все используют Django `Client`-like session auth

Эти endpoints **не являются частью REST API surface** и не подлежат сплиту public/internal в Wave 11.

### 2.6. Mailer endpoints (`backend/mailer/urls.py`)

Также **не DRF** — это обычные Django views для UI рассылок. Исключение: `POST mail/campaigns/<uuid>/start/` и родственные — это actions, не REST API. В инвентаризации REST API не учитываются.

### 2.7. Admin / UI-static endpoints

- `GET /robots.txt` (crm/views.robots_txt) — public, no auth
- `GET /.well-known/security.txt` — public
- `GET /health/` — public health check
- `GET /metrics` — Prometheus metrics (Django view)
- `GET /sw-push.js` — Service Worker для push (public static)
- Django-admin: `/django-admin/` — session auth + custom `_admin_has_permission()` (только ADMIN role)

Эти эндпоинты **не являются REST API**, но технически доступны. Для полноты OpenAPI их тоже нет смысла включать.

---

## 3. Красные флаги

### 3.1. OpenAPI / drf-spectacular — **0 покрытия**

**`drf-spectacular` подключён в settings** (INSTALLED_APPS + DEFAULT_SCHEMA_CLASS + SPECTACULAR_SETTINGS), но **НИ ОДИН endpoint не имеет `@extend_schema` аннотаций**. Проверка: `grep -r extend_schema backend/` → `No matches found`.

Последствия:
- OpenAPI schema, сгенерированная автоматически, будет **бедной**: только из типов сериализаторов и query_params. Для function-based `@api_view` endpoints (все Widget + messenger 3 шт. + JWT) — практически пустая.
- **Невозможно сгенерировать клиентский SDK** (TypeScript/Python) с осмысленными типами request/response.
- **Нет tags** → Swagger UI покажет всё в одной группе.
- **Нет description** → документация не генерируется.

**Полный список endpoints без `@extend_schema`:** **100% (всё).**

### 3.2. Throttling — почти полное отсутствие

С throttling (4 DRF ScopedRateThrottle scope'а):
- `phone_heartbeat` (30/min), `phone_pull` (120/min), `phone_telemetry` (20/min), `mobile_app_latest` (10/min)

С custom Redis-based throttles (Widget API):
- `WidgetBootstrapThrottle`, `WidgetSendThrottle`, `WidgetPollThrottle`

С inline rate limit (не DRF):
- `SecureTokenObtainPairView` → `is_ip_rate_limited("jwt_login", 5/min)`
- `QrTokenCreateView` → `is_ip_rate_limited("qr_token_create", 1/10sec)`
- Ряд middleware `RateLimitMiddleware` (`accounts/security.py`) для `/login/`, `/api/token/*`, `/api/phone/*`

**БЕЗ throttling вообще:**
- `CompanyViewSet`, `ContactViewSet`, `CompanyNoteViewSet` (все CRUD companies/contacts)
- `TaskTypeViewSet`, `TaskViewSet` (все CRUD tasks)
- `ConversationViewSet` (включая 14 custom actions + SSE-стримы!)
- `CannedResponseViewSet`, `ConversationLabelViewSet`, `PushSubscriptionViewSet`, `CampaignViewSet`, `AutomationRuleViewSet`, `ReportingViewSet`, `MacroViewSet`
- `transfer_conversation`, `heartbeat_view`, `branches_list_view`
- `widget_contact_update`, `widget_offhours_request`, `widget_stream` (SSE!), `widget_campaigns`, `widget_attachment_download`
- `TokenRefreshView` (`/api/token/refresh/`)
- `QrTokenExchangeView` — **public + no throttling** (!)
- `RegisterDeviceView`, `UpdateCallInfoView`, `PhoneLogUploadView`, `QrTokenStatusView`, `UserInfoView`, `LogoutView`, `LogoutAllView` — все phonebridge кроме 4 scoped

**Итого без throttling: ~130 endpoints.** Для DDoS защиты — серьёзный пробел, особенно SSE-стримы и `TokenRefreshView`.

### 3.3. Endpoints с `AllowAny` — проверить, что только public

| Endpoint | View | Public? |
|---|---|---|
| `POST /api/widget/bootstrap/` | widget_bootstrap | ✅ public (widget) |
| `POST /api/widget/contact/` | widget_contact_update | ✅ public (widget) |
| `POST /api/widget/offhours-request/` | widget_offhours_request | ✅ public (widget) |
| `POST /api/widget/send/` | widget_send | ✅ public (widget) |
| `GET  /api/widget/poll/` | widget_poll | ✅ public (widget) |
| `GET  /api/widget/stream/` | widget_stream | ✅ public (widget, Django view) |
| `POST /api/widget/typing/` | widget_typing | ✅ public (widget) |
| `POST /api/widget/mark_read/` | widget_mark_read | ✅ public (widget) |
| `POST /api/widget/rate/` | widget_rate | ✅ public (widget) |
| `GET  /api/widget/campaigns/` | widget_campaigns | ✅ public (widget) |
| `GET  /api/widget/attachment/<id>/` | widget_attachment_download | ✅ public (widget) |
| `POST /api/phone/qr/exchange/` (+ v1) | QrTokenExchangeView | ✅ public (mobile QR login) |

Все `AllowAny` обоснованы.

### 3.4. Duplicate endpoints (`/api/` ↔ `/api/v1/`)

**Каждый** DRF endpoint и каждая phonebridge view имеют **две точки входа**: legacy `/api/...` и versioned `/api/v1/...`. Оба роутятся на **один и тот же viewset** (для router) или **один и тот же APIView** (для phonebridge). Это означает:

- Ровно **дважды увеличивается OpenAPI surface** (когда `@extend_schema` появится).
- Нет разницы в auth, throttling, versioning policy.
- Router version (`router` vs `router_v1`) различается только `basename="company"` vs `basename="v1-company"` — это чисто служебная развязка имён URL.

**Duplicate router paths (7 viewsets × 5 методов × 2 роута = 70 дубликатов):**
- companies, contacts, company-notes, task-types, tasks, conversations, canned-responses

**Единственные эндпоинты БЕЗ дубликата `/api/v1/`:**
- В `router` (не в `router_v1`): `push`, `campaigns`, `automation-rules`, `messenger-reports`, `macros`, `conversation-labels`
- Phonebridge: `/api/phone/app/latest/` (только legacy)
- Widget API: только `/api/widget/...` (нет `/api/v1/widget/`)
- Messenger function-based: `/api/messenger/...` (нет `/api/v1/messenger/`)

**Кейс для Wave 11:** дубликаты `/api/` и `/api/v1/` не дают версионирования — обе версии эволюционируют синхронно. Это **ложное версионирование**. Нужно либо прибить legacy `/api/...` (breaking), либо реально разделить viewsets.

### 3.5. Несогласованность `PolicyPermission`

**С `PolicyPermission` (ABAC через `policy.engine.enforce`):**
- CompanyViewSet, ContactViewSet, CompanyNoteViewSet
- TaskTypeViewSet, TaskViewSet
- ConversationViewSet, CannedResponseViewSet

**БЕЗ `PolicyPermission` (только `IsAuthenticated`):**
- ConversationLabelViewSet
- PushSubscriptionViewSet
- CampaignViewSet (messenger campaigns)
- AutomationRuleViewSet
- ReportingViewSet
- MacroViewSet
- Все messenger function-based views
- Все phonebridge views (но у них свой `enforce()` вызов внутри метода — альтернатива через ABAC-engine напрямую)

⚠️ Риск: **любой авторизованный пользователь** (включая MANAGER) может читать/менять через API все `Campaign`, `AutomationRule`, `Macro`, `ConversationLabel`. Проверка ролей отсутствует.

### 3.6. Pagination

**Нет глобальной пагинации.** `CompanyViewSet.get_queryset()` возвращает полный queryset `visible_companies_qs`, и DRF отдаёт **все** записи в JSON без пагинации. При большом каталоге (десятки тысяч компаний) — серьёзный performance / memory риск.

`ConversationViewSet.list()` вручную вызывает `self.paginate_queryset(base_qs)`, но т.к. `pagination_class` не задан и `DEFAULT_PAGINATION_CLASS` в settings отсутствует → `paginate_queryset` возвращает `None` → код идёт в полный `Response(serializer.data)` без пагинации.

---

## 4. Разделение для Wave 11 (API split public/internal)

Цель Wave 11 — аккуратная миграция на `/api/v1/public/...` и `/api/v1/internal/...`.

### 4.1. На `/api/v1/public/...` (с retention CORS + AllowAny + throttling)

Эти 13 endpoints — чёткие кандидаты на public:

| Текущий URL | Новый URL (предложение) |
|---|---|
| `POST /api/widget/bootstrap/` | `POST /api/v1/public/widget/bootstrap/` |
| `POST /api/widget/contact/` | `POST /api/v1/public/widget/contact/` |
| `POST /api/widget/offhours-request/` | `POST /api/v1/public/widget/offhours-request/` |
| `POST /api/widget/send/` | `POST /api/v1/public/widget/send/` |
| `GET  /api/widget/poll/` | `GET  /api/v1/public/widget/poll/` |
| `GET  /api/widget/stream/` | `GET  /api/v1/public/widget/stream/` |
| `POST /api/widget/typing/` | `POST /api/v1/public/widget/typing/` |
| `POST /api/widget/mark_read/` | `POST /api/v1/public/widget/mark_read/` |
| `POST /api/widget/rate/` | `POST /api/v1/public/widget/rate/` |
| `GET  /api/widget/campaigns/` | `GET  /api/v1/public/widget/campaigns/` |
| `GET  /api/widget/attachment/<id>/` | `GET  /api/v1/public/widget/attachment/<id>/` |
| `POST /api/token/` | `POST /api/v1/public/auth/token/` |
| `POST /api/token/refresh/` | `POST /api/v1/public/auth/token/refresh/` |
| `POST /api/phone/qr/exchange/` | `POST /api/v1/public/mobile/qr/exchange/` |

### 4.2. На `/api/v1/internal/...` (JWT/Session обязательны, ABAC, throttling)

Все остальные endpoints:

**Companies & Tasks** (CRUD + policy):
- `/api/v1/internal/companies/...` (CompanyViewSet)
- `/api/v1/internal/contacts/...` (ContactViewSet)
- `/api/v1/internal/company-notes/...` (CompanyNoteViewSet)
- `/api/v1/internal/tasks/...` (TaskViewSet)
- `/api/v1/internal/task-types/...` (TaskTypeViewSet)

**Messenger (operator panel):**
- `/api/v1/internal/messenger/conversations/...` (+ 14 custom actions)
- `/api/v1/internal/messenger/canned-responses/...`
- `/api/v1/internal/messenger/labels/...`
- `/api/v1/internal/messenger/push/...`
- `/api/v1/internal/messenger/campaigns/...` (popup campaigns)
- `/api/v1/internal/messenger/automation-rules/...`
- `/api/v1/internal/messenger/reports/...`
- `/api/v1/internal/messenger/macros/...`
- `/api/v1/internal/messenger/branches/`
- `/api/v1/internal/messenger/heartbeat/`
- `/api/v1/internal/messenger/conversations/<id>/transfer/`

**Mobile app (CRMProfiDialer):**
- `/api/v1/internal/mobile/devices/register/`
- `/api/v1/internal/mobile/devices/heartbeat/`
- `/api/v1/internal/mobile/calls/pull/`
- `/api/v1/internal/mobile/calls/update/`
- `/api/v1/internal/mobile/telemetry/`
- `/api/v1/internal/mobile/logs/`
- `/api/v1/internal/mobile/qr/create/`
- `/api/v1/internal/mobile/qr/status/`
- `/api/v1/internal/mobile/user/info/`
- `/api/v1/internal/mobile/logout/`
- `/api/v1/internal/mobile/logout/all/`
- `/api/v1/internal/mobile/app/latest/`

### 4.3. План миграции (для Wave 11)

1. **Фаза 1**: Добавить новые URL `/api/v1/public/...` и `/api/v1/internal/...` параллельно существующим. Старые legacy `/api/...` и `/api/v1/...` (без `public/internal`) оставить с deprecation-warning header.
2. **Фаза 2**: Добавить `@extend_schema(tags=['public'])` / `@extend_schema(tags=['internal'])` на каждый endpoint. Генерировать два OpenAPI документа.
3. **Фаза 3**: Глобальные defaults — для `public/` автоматически `DEFAULT_THROTTLE_CLASSES=[AnonRateThrottle]` + CORS. Для `internal/` — `UserRateThrottle` + строгий CORS (только WebApp).
4. **Фаза 4**: В клиентах (Android, Web) заменить URL. Deprecated endpoints снести через 60 дней.
5. **Фаза 5**: Прибить fallback `/api/...` (legacy), оставить только `/api/v1/public/` и `/api/v1/internal/`.

---

## 5. Приложение: полный список endpoint'ов в одной таблице

| # | URL | Method | View / ViewSet | Auth | Permissions | Throttle | OpenAPI | Категория |
|---|-----|--------|----------------|------|-------------|----------|---------|-----------|
| 1 | `/api/widget/bootstrap/` | POST,OPTIONS | widget_bootstrap | — (AllowAny) | AllowAny | WidgetBootstrapThrottle | ❌ | PUBLIC |
| 2 | `/api/widget/contact/` | POST,OPTIONS | widget_contact_update | — | AllowAny | ❌ | ❌ | PUBLIC |
| 3 | `/api/widget/offhours-request/` | POST,OPTIONS | widget_offhours_request | — | AllowAny | ❌ | ❌ | PUBLIC |
| 4 | `/api/widget/send/` | POST | widget_send | — | AllowAny | WidgetSendThrottle | ❌ | PUBLIC |
| 5 | `/api/widget/poll/` | GET | widget_poll | — | AllowAny | WidgetPollThrottle | ❌ | PUBLIC |
| 6 | `/api/widget/stream/` | GET | widget_stream (Django view) | — | AllowAny | ❌ | ❌ | PUBLIC, SSE |
| 7 | `/api/widget/typing/` | POST | widget_typing | — | AllowAny | WidgetPollThrottle | ❌ | PUBLIC |
| 8 | `/api/widget/mark_read/` | POST | widget_mark_read | — | AllowAny | WidgetPollThrottle | ❌ | PUBLIC |
| 9 | `/api/widget/rate/` | POST | widget_rate | — | AllowAny | WidgetPollThrottle | ❌ | PUBLIC |
| 10 | `/api/widget/campaigns/` | GET | widget_campaigns | — | AllowAny | ❌ | ❌ | PUBLIC |
| 11 | `/api/widget/attachment/<id>/` | GET | widget_attachment_download | — | AllowAny | ❌ | ❌ | PUBLIC, file |
| 12 | `/api/token/` | POST | SecureTokenObtainPairView | — | AllowAny | inline 5/min | ❌ | PUBLIC, JWT |
| 13 | `/api/v1/token/` | POST | SecureTokenObtainPairView | — | AllowAny | inline | ❌ | PUBLIC, dup |
| 14 | `/api/token/refresh/` | POST | LoggedTokenRefreshView | — | AllowAny | ❌ | ❌ | PUBLIC, JWT |
| 15 | `/api/v1/token/refresh/` | POST | LoggedTokenRefreshView | — | AllowAny | ❌ | ❌ | PUBLIC, dup |
| 16 | `/api/phone/qr/exchange/` | POST | QrTokenExchangeView | — | [] (AllowAny) | ❌ | ❌ | PUBLIC |
| 17 | `/api/v1/phone/qr/exchange/` | POST | QrTokenExchangeView | — | [] | ❌ | ❌ | PUBLIC, dup |
| 18 | `/api/companies/` | GET,POST | CompanyViewSet | JWT+Session | IsAuth+Policy | ❌ | ❌ | INTERNAL |
| 19 | `/api/companies/<id>/` | GET,PUT,PATCH,DELETE | CompanyViewSet | JWT+Session | IsAuth+Policy | ❌ | ❌ | INTERNAL |
| 20 | `/api/v1/companies/` | GET,POST | CompanyViewSet | JWT+Session | IsAuth+Policy | ❌ | ❌ | INTERNAL, dup |
| 21 | `/api/v1/companies/<id>/` | GET,PUT,PATCH,DELETE | CompanyViewSet | JWT+Session | IsAuth+Policy | ❌ | ❌ | INTERNAL, dup |
| 22 | `/api/contacts/` | GET,POST | ContactViewSet | JWT+Session | IsAuth+Policy | ❌ | ❌ | INTERNAL |
| 23 | `/api/contacts/<id>/` | GET,PUT,PATCH,DELETE | ContactViewSet | JWT+Session | IsAuth+Policy | ❌ | ❌ | INTERNAL |
| 24 | `/api/v1/contacts/` | GET,POST | ContactViewSet | JWT+Session | IsAuth+Policy | ❌ | ❌ | INTERNAL, dup |
| 25 | `/api/v1/contacts/<id>/` | GET,PUT,PATCH,DELETE | ContactViewSet | JWT+Session | IsAuth+Policy | ❌ | ❌ | INTERNAL, dup |
| 26 | `/api/company-notes/` | GET,POST | CompanyNoteViewSet | JWT+Session | IsAuth+Policy | ❌ | ❌ | INTERNAL |
| 27 | `/api/company-notes/<id>/` | GET,PUT,PATCH,DELETE | CompanyNoteViewSet | JWT+Session | IsAuth+Policy | ❌ | ❌ | INTERNAL |
| 28 | `/api/v1/company-notes/` | GET,POST | CompanyNoteViewSet | JWT+Session | IsAuth+Policy | ❌ | ❌ | INTERNAL, dup |
| 29 | `/api/v1/company-notes/<id>/` | GET,PUT,PATCH,DELETE | CompanyNoteViewSet | JWT+Session | IsAuth+Policy | ❌ | ❌ | INTERNAL, dup |
| 30 | `/api/task-types/` | GET,POST | TaskTypeViewSet | JWT+Session | IsAuth+Policy | ❌ | ❌ | INTERNAL |
| 31 | `/api/task-types/<id>/` | GET,PUT,PATCH,DELETE | TaskTypeViewSet | JWT+Session | IsAuth+Policy | ❌ | ❌ | INTERNAL |
| 32 | `/api/v1/task-types/` | GET,POST | TaskTypeViewSet | JWT+Session | IsAuth+Policy | ❌ | ❌ | INTERNAL, dup |
| 33 | `/api/v1/task-types/<id>/` | GET,PUT,PATCH,DELETE | TaskTypeViewSet | JWT+Session | IsAuth+Policy | ❌ | ❌ | INTERNAL, dup |
| 34 | `/api/tasks/` | GET,POST | TaskViewSet | JWT+Session | IsAuth+Policy | ❌ | ❌ | INTERNAL |
| 35 | `/api/tasks/<id>/` | GET,PUT,PATCH,DELETE | TaskViewSet | JWT+Session | IsAuth+Policy | ❌ | ❌ | INTERNAL |
| 36 | `/api/v1/tasks/` | GET,POST | TaskViewSet | JWT+Session | IsAuth+Policy | ❌ | ❌ | INTERNAL, dup |
| 37 | `/api/v1/tasks/<id>/` | GET,PUT,PATCH,DELETE | TaskViewSet | JWT+Session | IsAuth+Policy | ❌ | ❌ | INTERNAL, dup |
| 38 | `/api/conversations/` | GET | ConversationViewSet | JWT+Session | IsAuth+Policy | ❌ | ❌ | INTERNAL |
| 39 | `/api/conversations/<id>/` | GET,PATCH,DELETE | ConversationViewSet | JWT+Session | IsAuth+Policy | ❌ | ❌ | INTERNAL |
| 40 | `/api/conversations/<id>/read/` | POST | ConversationVS.read | JWT+Session | IsAuth+Policy | ❌ | ❌ | INTERNAL, action |
| 41 | `/api/conversations/merge-contacts/` | POST | ConversationVS.merge_contacts | JWT+Session | IsAuth+Policy | ❌ | ❌ | INTERNAL, admin-only |
| 42 | `/api/conversations/unread-count/` | GET | ConversationVS.unread_count | JWT+Session | IsAuth+Policy | ❌ | ❌ | INTERNAL, cached 30s |
| 43 | `/api/conversations/agents/` | GET | ConversationVS.agents | JWT+Session | IsAuth+Policy | ❌ | ❌ | INTERNAL |
| 44 | `/api/conversations/<id>/needs-help/` | POST | ConversationVS.needs_help | JWT+Session | IsAuth+Policy | ❌ | ❌ | INTERNAL |
| 45 | `/api/conversations/<id>/contacted-back/` | POST | ConversationVS.contacted_back | JWT+Session | IsAuth+Policy | ❌ | ❌ | INTERNAL |
| 46 | `/api/conversations/bulk/` | POST | ConversationVS.bulk | JWT+Session | IsAuth+Policy | ❌ | ❌ | INTERNAL |
| 47 | `/api/conversations/notifications/stream/` | GET | ConversationVS.notifications_stream | JWT+Session | IsAuth+Policy | ❌ | ❌ | INTERNAL, SSE 55s |
| 48 | `/api/conversations/<id>/messages/` | GET,POST | ConversationVS.messages | JWT+Session | IsAuth+Policy | ❌ | ❌ | INTERNAL |
| 49 | `/api/conversations/<id>/stream/` | GET | ConversationVS.stream | JWT+Session | IsAuth+Policy | ❌ | ❌ | INTERNAL, SSE 30s |
| 50 | `/api/conversations/<id>/typing/` | GET,POST | ConversationVS.typing | JWT+Session | IsAuth+Policy | ❌ | ❌ | INTERNAL |
| 51 | `/api/conversations/<id>/context/` | GET | ConversationVS.context | JWT+Session | IsAuth+Policy | ❌ | ❌ | INTERNAL |
| 52 | `/api/v1/conversations/<...>/...` | × | ConversationViewSet (все actions) | JWT+Session | IsAuth+Policy | ❌ | ❌ | INTERNAL, dup |
| 53 | `/api/canned-responses/` | GET,POST | CannedResponseViewSet | JWT+Session | IsAuth+Policy | ❌ | ❌ | INTERNAL |
| 54 | `/api/canned-responses/<id>/` | GET,PUT,PATCH,DELETE | CannedResponseViewSet | JWT+Session | IsAuth+Policy | ❌ | ❌ | INTERNAL |
| 55 | `/api/v1/canned-responses/...` | × | CannedResponseViewSet | JWT+Session | IsAuth+Policy | ❌ | ❌ | INTERNAL, dup |
| 56 | `/api/conversation-labels/` | GET,POST | ConversationLabelViewSet | JWT+Session | IsAuth (⚠️ no Policy) | ❌ | ❌ | INTERNAL |
| 57 | `/api/conversation-labels/<id>/` | GET,PUT,PATCH,DELETE | ConversationLabelViewSet | JWT+Session | IsAuth (⚠️) | ❌ | ❌ | INTERNAL |
| 58 | `/api/push/vapid-key/` | GET | PushSubscriptionVS.vapid_key | JWT+Session | IsAuth (⚠️) | ❌ | ❌ | INTERNAL |
| 59 | `/api/push/subscribe/` | POST | PushSubscriptionVS.subscribe | JWT+Session | IsAuth (⚠️) | ❌ | ❌ | INTERNAL |
| 60 | `/api/push/unsubscribe/` | POST | PushSubscriptionVS.unsubscribe | JWT+Session | IsAuth (⚠️) | ❌ | ❌ | INTERNAL |
| 61 | `/api/campaigns/` | GET,POST | CampaignViewSet | JWT+Session | IsAuth (⚠️) | ❌ | ❌ | INTERNAL |
| 62 | `/api/campaigns/<id>/` | GET,PUT,PATCH,DELETE | CampaignViewSet | JWT+Session | IsAuth (⚠️) | ❌ | ❌ | INTERNAL |
| 63 | `/api/automation-rules/` | GET,POST | AutomationRuleViewSet | JWT+Session | IsAuth (⚠️) | ❌ | ❌ | INTERNAL |
| 64 | `/api/automation-rules/<id>/` | GET,PUT,PATCH,DELETE | AutomationRuleViewSet | JWT+Session | IsAuth (⚠️) | ❌ | ❌ | INTERNAL |
| 65 | `/api/messenger-reports/overview/` | GET | ReportingVS.overview | JWT+Session | IsAuth (⚠️) | ❌ | ❌ | INTERNAL |
| 66 | `/api/macros/` | GET,POST | MacroViewSet | JWT+Session | IsAuth (⚠️) | ❌ | ❌ | INTERNAL |
| 67 | `/api/macros/<id>/` | GET,PUT,PATCH,DELETE | MacroViewSet | JWT+Session | IsAuth (⚠️) | ❌ | ❌ | INTERNAL |
| 68 | `/api/macros/<id>/execute/` | POST | MacroVS.execute | JWT+Session | IsAuth (⚠️) | ❌ | ❌ | INTERNAL, action |
| 69 | `/api/messenger/heartbeat/` | POST | heartbeat_view | JWT+Session | IsAuth | ❌ | ❌ | INTERNAL |
| 70 | `/api/messenger/branches/` | GET | branches_list_view | JWT+Session | IsAuth | ❌ | ❌ | INTERNAL |
| 71 | `/api/messenger/conversations/<id>/transfer/` | POST | transfer_conversation | JWT+Session | IsAuth | ❌ | ❌ | INTERNAL |
| 72 | `/api/phone/devices/register/` | POST | RegisterDeviceView | JWT | IsAuth | ❌ | ❌ | INTERNAL, mobile |
| 73 | `/api/v1/phone/devices/register/` | POST | RegisterDeviceView | JWT | IsAuth | ❌ | ❌ | INTERNAL, dup |
| 74 | `/api/phone/devices/heartbeat/` | POST | DeviceHeartbeatView | JWT | IsAuth | phone_heartbeat 30/min | ❌ | INTERNAL |
| 75 | `/api/v1/phone/devices/heartbeat/` | POST | DeviceHeartbeatView | JWT | IsAuth | phone_heartbeat | ❌ | INTERNAL, dup |
| 76 | `/api/phone/calls/pull/` | GET | PullCallView | JWT | IsAuth | phone_pull 120/min | ❌ | INTERNAL |
| 77 | `/api/v1/phone/calls/pull/` | GET | PullCallView | JWT | IsAuth | phone_pull | ❌ | INTERNAL, dup |
| 78 | `/api/phone/calls/update/` | POST | UpdateCallInfoView | JWT | IsAuth | ❌ | ❌ | INTERNAL |
| 79 | `/api/v1/phone/calls/update/` | POST | UpdateCallInfoView | JWT | IsAuth | ❌ | ❌ | INTERNAL, dup |
| 80 | `/api/phone/telemetry/` | POST | PhoneTelemetryView | JWT | IsAuth | phone_telemetry 20/min | ❌ | INTERNAL |
| 81 | `/api/v1/phone/telemetry/` | POST | PhoneTelemetryView | JWT | IsAuth | phone_telemetry | ❌ | INTERNAL, dup |
| 82 | `/api/phone/logs/` | POST | PhoneLogUploadView | JWT | IsAuth | ❌ | ❌ | INTERNAL |
| 83 | `/api/v1/phone/logs/` | POST | PhoneLogUploadView | JWT | IsAuth | ❌ | ❌ | INTERNAL, dup |
| 84 | `/api/phone/qr/create/` | POST | QrTokenCreateView | JWT+Session | IsAuth | inline 1/10sec | ❌ | INTERNAL |
| 85 | `/api/v1/phone/qr/create/` | POST | QrTokenCreateView | JWT+Session | IsAuth | inline | ❌ | INTERNAL, dup |
| 86 | `/api/phone/qr/status/` | GET | QrTokenStatusView | JWT+Session | IsAuth | ❌ | ❌ | INTERNAL |
| 87 | `/api/v1/phone/qr/status/` | GET | QrTokenStatusView | JWT+Session | IsAuth | ❌ | ❌ | INTERNAL, dup |
| 88 | `/api/phone/user/info/` | GET | UserInfoView | JWT | IsAuth | ❌ | ❌ | INTERNAL |
| 89 | `/api/v1/phone/user/info/` | GET | UserInfoView | JWT | IsAuth | ❌ | ❌ | INTERNAL, dup |
| 90 | `/api/phone/logout/` | POST | LogoutView | JWT | IsAuth | ❌ | ❌ | INTERNAL |
| 91 | `/api/v1/phone/logout/` | POST | LogoutView | JWT | IsAuth | ❌ | ❌ | INTERNAL, dup |
| 92 | `/api/phone/logout/all/` | POST | LogoutAllView | JWT | IsAuth | ❌ | ❌ | INTERNAL |
| 93 | `/api/v1/phone/logout/all/` | POST | LogoutAllView | JWT | IsAuth | ❌ | ❌ | INTERNAL, dup |
| 94 | `/api/phone/app/latest/` | GET | MobileAppLatestView | JWT | IsAuth | mobile_app_latest 10/min | ❌ | INTERNAL |

(Dev-only: `/api/schema/` и `/api/schema/swagger-ui/` — доступны только при DEBUG=True, не учтены.)

---

## 6. Выводы

1. **Система хорошо структурирована** на уровне политик (ABAC через `PolicyPermission` + `policy.engine.enforce`) для CompanyViewSet / TaskViewSet / ConversationViewSet. Для остального мессенджера (Campaign, Macro, AutomationRule) политики отсутствуют — это пробел.
2. **OpenAPI / drf-spectacular подключён, но не используется** (0 `@extend_schema`). Документация API сейчас не генерируется.
3. **Throttling слабое** — 130 из ~150 endpoints без защиты. Особенно критично для SSE-стримов и `TokenRefreshView`.
4. **Pagination не работает** — глобальный пагинатор не задан. `CompanyViewSet.list()` может вернуть тысячи записей разом.
5. **Дублирование `/api/...` ↔ `/api/v1/...`** даёт ложное ощущение версионирования; оба роута идут на одни и те же viewsets.
6. **Widget API хорошо изолирован** (`AllowAny` + session-token validation + CORS whitelist через Inbox.allowed_domains + custom throttles + captcha).
7. **Готовая основа для Wave 11 (API split)** — 13 public endpoints чёткие, остальные явно internal. Можно начинать с вынесения на `/api/v1/public/widget/...` и `/api/v1/public/auth/...`.
