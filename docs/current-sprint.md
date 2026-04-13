# Текущий спринт

## Текущая задача

Live-chat UX Completion — реализация по спецификации `docs/superpowers/specs/2026-04-13-livechat-ux-completion-design.md`.

**Статус:** Plan 1, Plan 2, Plan 3 завершены 2026-04-13. Следующее — Plan 4 (Right panel: Client Context).

## Сделано в этом спринте

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

1. **Plan 4: Right panel — Client Context** — карточка клиента (компания, регион, deal'ы), история диалогов, заметки.
2. **Полировка Task 6/7** (nice-to-have, не блокеры): secondary стиль кнопки "Переоткрыть"; подтверждение при Вернуть в очередь; focus trap в модалках.

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
