---
tags: [журнал, changelog]
---

# Changelog

> Claude Code автоматически обновляет этот файл при каждом значимом изменении.

---

## 2026-04-13 (дополнение)

### Feat: Live-chat Notifications + Escalation (Plan 3)
**Коммиты:** `a909afa..3f2355f` (9 коммитов)

Автоматическая эскалация молчаливых диалогов по порогам `waiting_minutes` + звуковые/desktop/визуальные уведомления:

**Модель и API:**
- `Conversation.resolution` (JSONField, default=dict) — outcome/comment/resolved_at из resolve modal
- `Conversation.escalation_level` (PositiveSmallIntegerField, db_index, 0..4) — идемпотентность эскалации
- `Conversation.last_escalated_at` — timestamp последней эскалации
- `PolicyConfig.livechat_escalation` (JSONField) — настройки порогов (warn/urgent/rop_alert/pool_return, дефолты 3/10/20/40 мин)
- `ConversationSerializer` расширен: `resolution` (editable, в whitelist PATCH), `escalation_level`/`last_escalated_at` (read-only)

**Celery task `escalate_waiting_conversations`:**
- Регистрация в `CELERY_BEAT_SCHEDULE` — раз в 30 секунд
- Идемпотентна через `escalation_level` (каждый уровень триггерит ровно одно событие)
- Level 1 (warn, 3 мин) — только бейдж
- Level 2 (urgent, 10 мин) — Notification для assignee
- Level 3 (rop_alert, 20 мин) — Notification всем SALES_HEAD филиала
- Level 4 (pool_return, 40 мин) — assignee=None + Notification всем онлайн-менеджерам филиала
- Обход инварианта `Conversation.save()` через `.filter(pk=...).update(...)`
- Фильтр candidates исключает отвеченные (`last_agent_msg_at >= last_customer_msg_at`) и RESOLVED

**Frontend (`operator-panel.js`):**
- Resolve modal отправляет `resolution: {outcome, comment, resolved_at}` в PATCH (Plan 2 Task 7 был заглушкой)
- `playNotificationSound()` — WebAudio beep 880→440 Гц, без бинарных файлов
- `showDesktopNotification(conv, message)` — Notification API с tag, onclick открывает диалог
- `updateTitleBadge(unread)` — формат `(N) <base>`, вызывает `window.setFaviconBadge`
- `visibilitychange` — сброс счётчика при возврате на вкладку
- `requestNotificationPermission` — запрос прав на первом клике
- `highlightConversation(convId)` — ring-2 ring-red-500 на 3 секунды
- Бейдж `waiting_minutes` в списке диалогов (yellow ≥3, orange ≥10, red+pulse ≥20)

**Новый файл `backend/messenger/static/messenger/favicon-badge.js`:**
- Canvas-рендер поверх favicon, бейдж с числом, кэш по count
- `window.setFaviconBadge(count)` — глобальный API

**Глобальный notification handler (`templates/ui/base.html`):**
- `pollOnce()` (`/notifications/poll/`) расширен: для новых нотификаций с `payload.conversation_id` вызывает `MessengerPanel.playNotificationSound()` + `highlightConversation(id)`
- Эскалационные Notification из celery-таски имеют `payload={"conversation_id": id, "level": "urgent|rop_alert|pool_return"}`

**Тесты:** 6 в `test_escalation.py` + 2 в `test_resolution_field.py` (EscalationThresholdsFromPolicy) + 2 (ResolutionApi) + 4 (ConversationEscalationFields). Регрессия messenger: 123/123. Полный прогон `messenger accounts policy notifications`: 214/214 OK.

**Миграции:** `messenger.0022_conversation_escalation_fields`, `policy.0003_policyconfig_livechat_escalation`.

---

## 2026-04-13

### Feat: Live-chat Operator UX Panel (Plan 2)
**Коммиты:** `cce8224..9dfa761` (13 коммитов) + `53e5808` (fix предсуществующего теста `accounts.tests_branch_region`)

Полная оператор-панель в стиле Chatwoot поверх существующей SSE-инфраструктуры:

**Модель и API:**
- `Message.save()` теперь обновляет `Conversation.last_customer_msg_at` / `last_agent_msg_at` (только при создании, только для OUT/IN — INTERNAL игнорируется)
- `Conversation.ui_status` (property) — слой над DB-статусом: `NEW` (без assignee) / `WAITING` (клиент ждёт ответа) / `IN_PROGRESS` / `CLOSED`
- `Conversation.waiting_minutes` (property) и `escalation_thresholds` (classmethod, читает из `PolicyConfig.livechat_escalation` если есть, иначе дефолты warn=3/urgent=10/rop_alert=20/pool_return=40)
- `CannedResponse.is_quick_button` + `sort_order` + `Meta.ordering`
- `GET /api/conversations/agents/?branch_id=X&online=1` — фильтры операторов
- `GET /api/messenger/branches/` — список активных филиалов
- `POST /api/conversations/{id}/needs-help/` — флаг "позван старший" (права: assignee/ADMIN/BRANCH_DIRECTOR/SALES_HEAD)
- `GET /api/canned-responses/?quick=1` — быстрые ответы для чипов

**UI (operator-panel.js, ~600 новых строк):**
- **Контекстная primary CTA** в шапке диалога: меняется по `ui_status` — «Взять в работу» / «Ответить» / «Завершить» / «Переоткрыть»
- **Меню ⋯** с пунктами «Передать оператору», «Позвать старшего», «Вернуть в очередь» (dropdown с закрытием по клику вне / Escape)
- **Resolve modal** (`#resolveDialogModal`): select исхода (success/no_response/spam/duplicate/other, required) + textarea комментария + 5-секундный **undo-тост** с прогресс-баром перед фактическим PATCH
- **Transfer modal** (`#transferDialogModal`): филиал, оператор (загрузка через агенты API с фильтром `online`), причина (minlength=5), предупреждение cross-branch. Использует существующий `POST /transfer/` endpoint с серверным аудитом `ConversationTransfer`
- **Draft autosave** в localStorage (300ms debounce, TTL 7 дней, лимит 50 черновиков, ключ `messenger:draft:v1:<id>:<mode>`, отдельные черновики для OUT и INTERNAL режимов)
- **Визуальный режим внутренней заметки:** жёлтый фон поля ввода + плашка «Внутренняя заметка — клиент её не увидит» + жёлтая кнопка отправки при `composeMode=INTERNAL`
- **Быстрые ответы:** ряд чипов над полем ввода из `CannedResponse.is_quick_button=True` (первые 8 по `sort_order`); скрывается в INTERNAL режиме
- **SOS бейдж «Позван старший»:** красный пульсирующий в списке диалогов и статичный в шапке

**Тесты:** 11 новых в `test_operator_actions_api.py` + 3 в `test_ui_status.py` (MessageTimestampsTests) + 5 (UiStatusPropertyTests) + 4 (WaitingMinutesTests + EscalationThresholdsTests) + 2 (CannedResponseFieldsTests) + 2 (QuickButtonFilterTests). Регрессия messenger: 109/109, accounts: 4/4 после фикса `tym`-кода.

**Архитектурные решения:**
- Текст и комментарий резолва/передачи — через существующие endpoints (`PATCH status` + `/transfer/` для аудита); новых таблиц для резолюции не создавали (задача Plan 3)
- `Message.is_private` из Plan 1 оставлен deprecated в пользу уже существующего `Direction.INTERNAL` + `composeMode=internal` на фронте — избежали дублирования
- Cross-branch PATCH через ConversationSerializer не разрешён — найден существующий `POST /transfer/` с `filter(pk=...).update(branch=...)`

---

### Feat: Live-chat Backend Foundation (Plan 1)
**Коммиты:** `5f461e7..3a62b66`

Фундамент для автоматической маршрутизации диалогов по филиалам:
- **Региональная автомаршрутизация:** `Conversation.client_region` + `MultiBranchRouter` (точное совпадение / общий пул round-robin / fallback ЕКБ) + `BranchLoadBalancer` (наименее загруженный онлайн-менеджер) + `auto_assign_conversation` через post_save сигнал
- **Справочник регионов:** модель `BranchRegion` + fixture из Положения 2025-2026 (95 записей, 4 филиала + общий пул Мск/СПб/Нвг/Пск)
- **Ролевая видимость:** `get_visible_conversations(user)` — MANAGER видит свои + пул филиала, РОП/BRANCH_DIRECTOR — весь филиал, ADMIN — всё
- **Передача диалога:** модель `ConversationTransfer` + `POST /api/messenger/conversations/{id}/transfer/` с обязательной причиной и детекцией cross-branch
- **Приватные заметки:** `Message.is_private` (фильтруется из widget SSE/poll/bootstrap в 5 местах)
- **Heartbeat:** `POST /api/messenger/heartbeat/` обновляет `User.messenger_online`/`messenger_last_seen`; celery-beat task `check_offline_operators` раз в минуту переводит операторов в offline после 90 с без heartbeat
- **Флаг эскалации:** `Conversation.needs_help` + `needs_help_at` (заготовка для Plan 3)

Обход инварианта `Conversation.save()` (запрет смены branch) — через `Conversation.objects.filter(pk=...).update(...)`.

**Тесты:** 20+ unit/integration (`messenger.tests.test_auto_assign`, `test_heartbeat`, `test_visibility`, `test_transfer`, `test_private_messages`, `accounts.tests_branch_region`) — все зелёные (120/120 в прогоне `messenger accounts`).

**Staging:** миграции `accounts.0010-0012` + `messenger.0016-0019` применены, фикстура `branch_regions_2025_2026.json` загружена (95 регионов), BranchRegion.objects.count()=95.

---

## 2026-04-07

### Fix: Массовое переназначение компаний
**Проблема:** Директор филиала не мог массово переназначить базу уволенных сотрудников. Если хоть одна из выбранных компаний не проходила проверку прав, вся операция блокировалась с ошибкой 400.

**Решение:** Разрешённые компании переназначаются, запрещённые пропускаются с информированием. Toast показывает «Переназначено N, пропущено M».

### Harden: Security review — .gitignore hardening
**Проблема:** `.playwright-mcp/` содержала staging widget token и session token в логах Playwright Browser MCP. `test-screenshots/` и PNG в корне содержали скриншоты staging UI с PII (имена, email). Ни одна из этих директорий не была в `.gitignore` — при `git add .` всё попало бы в репозиторий.

**Исправления:**
- `.gitignore`: добавлены `.playwright-mcp/`, `test-screenshots/`, PNG-скриншоты
- Рекомендована ротация staging widget token (Inbox #8)

---

## 2026-04-06

### Fix: SSE real-time доставка — тройная дедупликация
**Коммиты:** `b26fadb`, `6c3ba20`

**Проблема:** Сообщения оператора не появлялись в виджете через SSE. При перезагрузке страницы сохранённые сообщения тоже не рендерились.

**Корневая причина:** Одна и та же ошибка в 3 местах `widget.js` — `receivedMessageIds.add(msg.id)` вызывался ДО `addMessageToUI()`, которая проверяла тот же Set и возвращала `return` (сообщение не рендерилось).

**Ложный след:** gthread буферизация — curl внутри Docker доказал что gthread стримит SSE инкрементально.

**Исправления:**
- widget.js: удалён `receivedMessageIds.add()` из SSE handler, render() savedMessages, render() initialMessages
- Host nginx (`/etc/nginx/sites-available/crm-staging`): добавлены location-блоки с `proxy_buffering off` для SSE
- Роль admin: `role == MANAGER` → `is_superuser or role in (MANAGER, ADMIN)` в `messenger_panel.py`, `api.py` (3 места)

**Подтверждение:** Playwright Browser MCP — SSE real-time доставка оператор → виджет работает.

---

### Fix: SSE real-time и производительность мессенджера
**Коммиты:** `b9e3f8b`, `18deaa7`

**Проблема:** Сообщения приходили с задержкой, real-time не работал — требовалось обновление страницы.

**Корневая причина:** Gunicorn (2 sync workers) полностью блокировался SSE-стримами. Каждый SSE-стрим (widget 25с + operator per-conv 30с + notifications 55с) занимал воркер, оставляя 0 воркеров для API-запросов.

**Исправления:**
- Gunicorn: переход на `gthread` (4 workers × 8 threads = 32 соединения)
- Widget stream: `changed = False` сбрасывал флаг `read_up_to`
- Operator stream: typing инвертирован (`is False` → `is True`)
- Operator per-conversation: дублировал все сообщения при каждом reconnect
- Offline email: `GlobalMailAccount.reply_to` AttributeError
- gevent → gthread (несовместимость с psycopg3)

---

## 2026-04-05

### Fix: Round 4 production hardening мессенджера
**Коммиты:** `eeb51ac`, `27131ce`, `34c19cb`

**Исправления:**
- operator-panel.js: утечка event listeners в label popup
- markConversationRead: обёрнуто в try-catch
- Date separator: innerHTML → createElement (XSS-защита)
- merge-contacts: авторизация (admin only) + UUID validation
- Serializers: `__all__` → explicit fields (белый список)
- Widget: destroy() для SPA + CSS autoload для внешних сайтов
- Status filter: validation против Conversation.Status.choices
- CORS: разделение nginx preflight + Django response
- WidgetSession: добавлены поля bound_ip, created_at
- Widget campaigns: добавлен CORS handler

---

## 2026-04-02

### Feature: Мессенджер влит в main
- Feature-ветка удалена, одна ветка `main`
- `MESSENGER_ENABLED=1` в .env
- Полная система live-chat (Chatwoot-style)

---

*Claude Code обновляет этот файл автоматически.*
