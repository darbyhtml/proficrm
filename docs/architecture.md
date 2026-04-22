# Архитектура CRM ПРОФИ

## Стек технологий

| Слой | Технология | Версия |
|------|-----------|--------|
| Язык | Python | 3.13 |
| Web-фреймворк | Django | 6.0.1 |
| REST API | Django REST Framework | 3.16.1 |
| БД | PostgreSQL | 16 |
| Кэш/брокер | Redis | 7 |
| Фоновые задачи | Celery | 5.4.0 |
| WSGI | Gunicorn | 23.0.0 (gthread) |
| ASGI/WebSocket | Daphne | 4.1.2 |
| WebSocket | Django Channels | 4.2.0 |
| CSS | Tailwind CSS | 3.4.17 |
| XSS-защита | DOMPurify | 3.3.3 |
| Шифрование | cryptography (Fernet) | 46.0.3 |
| HTML санитизация | nh3 | 0.3.3 |
| Контейнеры | Docker Compose | — |
| Reverse proxy | Nginx | alpine |

## Структура проекта

```
CRM/
├── CLAUDE.md                  — главный файл для Claude Code
├── Dockerfile.staging         — образ: python:3.13-slim + deps
├── docker-compose.staging.yml — 7 сервисов staging
├── docker-compose.prod.yml    — продакшен
├── docker-compose.yml         — разработка
├── docker-compose.test.yml    — тесты
├── package.json               — Tailwind + DOMPurify
├── tailwind.config.js         — brand colors, template paths
│
├── backend/
│   ├── crm/                   — ЯДРО DJANGO (минимум: settings, urls, wsgi/asgi, celery)
│   │   ├── settings.py        — конфигурация (750+ LOC)
│   │   ├── urls.py            — все URL-маршруты (184 LOC)
│   │   ├── wsgi.py / asgi.py  — точки входа
│   │   ├── middleware.py       — SecurityHeaders, ErrorLogging
│   │   └── celery.py          — конфиг Celery
│   │
│   ├── core/                  — ИНФРАСТРУКТУРА (общие утилиты)
│   │   ├── crypto.py          — Fernet encrypt/decrypt (MultiFernet, ротация ключей)
│   │   ├── timezone_utils.py  — RUS_TZ_CHOICES, guess_ru_timezone_from_address
│   │   ├── request_id.py      — X-Request-ID middleware + logging filter
│   │   ├── json_formatter.py  — JSON log formatter
│   │   ├── exceptions.py      — DRF custom_exception_handler
│   │   ├── test_runner.py     — SQLiteCompatibleTestRunner
│   │   └── work_schedule_utils.py — рабочие дни/часы
│   │
│   ├── accounts/              — ПОЛЬЗОВАТЕЛИ (2 модели)
│   │   ├── models.py          — User (5 ролей, data scope), Branch
│   │   ├── permissions.py     — require_admin, get_view_as_user, get_effective_user
│   │   ├── middleware.py      — RateLimitMiddleware (DDoS защита)
│   │   └── views.py           — SecureLoginView, MagicLinkLogin
│   │
│   ├── companies/             — ЯДРО CRM (16 моделей)
│   │   ├── models.py          — Company, Contact, Deal, Note, SearchIndex
│   │   ├── api.py             — CompanyViewSet, ContactViewSet (305 LOC)
│   │   ├── search.py          — PostgreSQL FTS + pg_trgm
│   │   ├── services.py        — бизнес-логика
│   │   ├── selectors.py       — запросы с учётом data scope
│   │   └── tasks.py           — reindex (daily crontab)
│   │
│   ├── messenger/             — LIVE-CHAT (16 моделей)
│   │   ├── models.py          — Conversation, Message, Inbox, Channel...
│   │   ├── api.py             — ConversationViewSet + SSE стримы (1033 LOC)
│   │   ├── widget_api.py      — публичный Widget API (bootstrap, send, stream)
│   │   ├── services.py        — create_message, assign, resolve
│   │   ├── selectors.py       — visible_conversations_qs (по scope)
│   │   ├── consumers.py       — Django Channels WebSocket consumers
│   │   ├── ws_notify.py       — push через channel layer
│   │   ├── typing.py          — typing-индикаторы через Redis
│   │   ├── tasks.py           — escalation, auto-resolve, email notify
│   │   └── static/messenger/  — widget.js, widget.css, operator-panel.js
│   │
│   ├── mailer/                — EMAIL-РАССЫЛКИ (11 моделей)
│   │   ├── models.py          — Campaign, MailAccount, GlobalMailAccount, Queue
│   │   ├── smtp_sender.py     — SMTP отправка, build_message
│   │   ├── crypto.py          — shim → core/crypto.py
│   │   └── tasks.py           — send_pending, sync_quota, reconcile
│   │
│   ├── tasksapp/              — ЗАДАЧИ (4 модели)
│   │   ├── models.py          — Task (UUID, RRULE), TaskType, TaskComment
│   │   ├── api.py             — TaskViewSet
│   │   └── tasks.py           — generate_recurring_tasks
│   │
│   ├── ui/                    — ФРОНТЕНД VIEWS (~13K LOC)
│   │   ├── views.py           — все страницы (dashboard, companies, tasks...)
│   │   └── models.py          — UiGlobalConfig, AmoApiConfig, UiUserPreference
│   │
│   ├── notifications/         — УВЕДОМЛЕНИЯ (4 модели)
│   ├── phonebridge/           — ТЕЛЕФОНИЯ (6 моделей)
│   ├── audit/                 — АУДИТ (2 модели)
│   ├── policy/                — ПОЛИТИКИ (2 модели)
│   ├── amocrm/                — ИНТЕГРАЦИЯ AMOCRM
│   ├── templates/             — 89 HTML шаблонов
│   └── requirements.txt       — Python зависимости
│
├── frontend/src/main.css      — Tailwind source
├── nginx/                     — конфиги nginx (staging, prod, snippets)
├── docker/entrypoint.sh       — migrate, collectstatic, gosu
├── scripts/                   — deploy, backup, health, тесты
└── docs/                      — документация
```

## Схема БД (66 моделей)

### accounts (2)
- **User** — username, email, role (`manager`|`branch_director`|`sales_head`|`group_manager`|`admin`), data_scope (`SELF`|`BRANCH`|`GLOBAL`), branch FK
- **Branch** — name, адрес, телефон

### companies (16)
- **Company** — name, inn, manager FK(User), branch FK, status FK, sphere FK, region FK, contract dates, custom fields, parent FK(self)
- **Contact** — name, position, company FK
- **CompanyDeal** — company FK, amount, stage, probability
- **CompanyNote** / **CompanyNoteAttachment** — текст + файлы
- **CompanyEmail** / **CompanyPhone** — множественные контакты
- **ContactEmail** / **ContactPhone** — контакты физ.лиц
- **CompanyStatus** / **CompanySphere** / **Region** / **ContractType** — справочники
- **CompanyDeletionRequest** — soft-delete workflow
- **CompanySearchIndex** — tsvector + similarity
- **CompanyHistoryEvent** — полный аудит изменений

### messenger (16)
- **Inbox** — widget_token, branch FK, allowed_domains, settings JSON
- **Channel** — name, type (website, telegram, whatsapp, vk, email)
- **Contact** — name, email, phone, external_id (отдельная от companies.Contact)
- **Conversation** — inbox FK, contact FK, assignee FK(User), status (new→assigned→waiting→resolved→closed), labels
- **Message** — conversation FK, direction (IN/OUT), body, sender_user/sender_contact, attachments
- **MessageAttachment** — file, content_type, size
- **ContactInbox** — contact↔inbox, last_seen
- **RoutingRule** — inbox FK, conditions JSON (GeoIP, device)
- **CannedResponse** — title, body, branch FK
- **ConversationLabel** — name, color
- **AgentProfile** — user FK, availability
- **PushSubscription** — endpoint, keys
- **Campaign** — title, message, schedule, inbox FK
- **AutomationRule** — trigger, actions JSON
- **ReportingEvent** — conversation FK, event_type, timestamp
- **Macro** — name, actions JSON

### mailer (11)
- **MailAccount** — user FK, smtp_host/port, password Fernet, from_email, reply_to, rate limits
- **GlobalMailAccount** — singleton, smtp.bz, api_key Fernet
- **Campaign** — name, subject, body, status (draft→ready→sending→sent), send_at
- **CampaignRecipient** — campaign FK, email, status (pending→sent→failed→unsubscribed)
- **CampaignQueue** — campaign FK, priority, status, deferred_until
- **SendLog** — message_id, recipient, timestamp (idempotency)
- **Unsubscribe** / **UnsubscribeToken** — отписки
- **EmailCooldown** — cooldown после отписки
- **SmtpBzQuota** — синхронизация квот

### tasksapp (4)
- **Task** — UUID PK, title, assignee FK(User), status, due_date, recurrence_rrule
- **TaskType** — name, icon, color
- **TaskComment** — task FK, author FK, text
- **TaskEvent** — task FK, event_type, old/new values

### notifications (4), phonebridge (6), audit (2), policy (2), ui (3)
(см. docs/wiki/ для деталей)

## Паттерны

### API: двойная маршрутизация
```python
# /api/ — canonical
router.register("conversations", ConversationViewSet)
# /api/v1/ — versioned alias (basename: v1-conversation)
v1_router.register("conversations", ConversationViewSet, basename="v1-conversation")
```

### Widget API: отдельный слой
Widget API не использует DRF ViewSets. Это обычные Django views с ручной аутентификацией по `widget_token` + `widget_session_token`. CORS обрабатывается `_add_widget_cors_headers()`, не `django-cors-headers`.

### SSE (Server-Sent Events)
Три SSE-стрима для real-time:
- Widget stream (25с) — новые OUT-сообщения оператора
- Per-conversation stream (30с) — все сообщения + typing + status
- Notifications stream (55с) — все входящие по видимым диалогам

Работают на `StreamingHttpResponse` + `time.sleep()` внутри генератора. Gunicorn gthread (4w×8t=32 потока) не блокируется.

### Data scope
`selectors.py` в каждом приложении фильтрует queryset по роли:
- GLOBAL → все данные
- BRANCH → только свой филиал
- SELF → только свои записи

### Middleware pipeline
```
CORS → RequestID → Security → WhiteNoise → RateLimit → SecurityHeaders →
Session → Locale → Common → CSRF → Auth → Messages → XFrame → ErrorLogging
```

## Celery: 13 периодических задач

| Задача | Интервал | Модуль |
|--------|---------|--------|
| send-pending-emails | 60с | mailer |
| sync-smtp-bz-quota | 300с | mailer |
| sync-smtp-bz-unsubscribes | 600с | mailer |
| sync-smtp-bz-delivery-events | 600с | mailer |
| reconcile-mail-campaign-queue | 300с | mailer |
| messenger-escalate-stalled | 120с | messenger |
| messenger-auto-resolve | 900с | messenger |
| clean-old-call-requests | 3600с | phonebridge |
| reindex-companies-daily | 00:00 MSK | companies |
| generate-recurring-tasks | 06:00 MSK | tasksapp |
| purge-old-activity-events | Вс 03:00 | audit |
| purge-old-error-logs | Вс 03:15 | audit |
| purge-old-notifications | Вс 03:30 | notifications |

## Docker-сервисы (staging)

| Сервис | Образ | Порт | Worker |
|--------|-------|------|--------|
| web | python:3.13-slim | 8000 | Gunicorn gthread 4w×8t |
| nginx | nginx:alpine | 127.0.0.1:8080→80 | — |
| db | postgres:16 | 5432 | — |
| redis | redis:7-alpine | 6379 | — |
| celery | python:3.13-slim | — | 2 concurrency |
| celery-beat | python:3.13-slim | — | scheduler |
| websocket | python:3.13-slim | 8000 | Daphne |

## W1 Refactor Results (2026-04-21 → 2026-04-22)

**Volna W1 закрыта** в 4 mini-sessions. Zero behavior change, staging-only per Path E.

### god-files eliminated

| File | Before | After | Delta |
|------|--------|-------|-------|
| `backend/ui/views/_base.py` | 1 251 LOC | 371 LOC (shim) | −70% |
| `backend/ui/views/company_detail.py` | 3 022 LOC | 0 (deleted) | −100% |
| `backend/ui/views/pages/company/cold_call.py` | 691 LOC (W1.2 extract) | 608 LOC (W1.4 dedup) | −12% |

### New structure

```
backend/ui/views/
├── _base.py             # 371 LOC (W1.1: shim с re-exports для backward compat)
├── helpers/             # W1.1 — 6 shared helper modules
│   ├── search.py        (65)    normalizers (phone/email/text)
│   ├── tasks.py         (87)    permissions UI
│   ├── http.py          (72)    request helpers
│   ├── cold_call.py     (74)    date + permission utilities
│   ├── companies.py     (178)   access/edit/notifications/cache
│   └── company_filters.py (512) list filters + FTS
└── pages/company/       # W1.2 — 10 thematic views
    ├── detail.py        (393)   main card + timeline + tasks_history
    ├── edit.py          (420)   edit/update/inline/transfer/contract
    ├── deletion.py      (280)   4-stage delete workflow
    ├── contacts.py      (228)   contact CRUD
    ├── notes.py         (474)   notes + attachments + pin
    ├── deals.py         (128)   deal CRUD
    ├── cold_call.py     (608)   8 endpoints через _CCConfig + generic impl (W1.4 dedup)
    ├── phones.py        (436)   phone CRUD + comments
    ├── emails.py        (136)   email updates
    └── calls.py         (150)   PhoneBridge call request

backend/static/ui/      # W1.3 — extracted inline assets
├── css/pages/
│   ├── base_global.css        (864)   from ui/base.html
│   ├── company_detail_v3_b.css(571)   from v3/b.html
│   ├── messenger_conversations.css (560)
│   ├── _v2.css                (382)
│   └── _v3.css                (308)
└── js/pages/
    └── company_detail_handlers.js (53)  10 handlers delegation
```

### Security foundations (prep для W2 CSP strict)

- 9 bare `<script>` blocks получили `{{ csp_nonce }}` → 0 bare scripts.
- 10 inline event handlers в `company_detail.html` → `addEventListener` в external JS.
- CSP nonce генерация через `SecurityHeadersMiddleware` — готова к enforce switch.
- Deferred в W2: 66 handlers + 27 styles + full nonce script extract.

### Metrics

- **Coverage**: 51% (W0) → 52% (W1 start) → **53%** (W1 end). `fail_under` bumped 50 → 53.
- **Tests**: 1 140 → **1 164** (+24 cold_call URL tests).
- **Templates**: 109 scanned, 53 с inline content. Top 5 styles extracted (65% CSS LOC).
- **Commits**: ~40 atomic across 4 mini-sessions.

Full rollup: `docs/release/w1-wave-closure.md`.
