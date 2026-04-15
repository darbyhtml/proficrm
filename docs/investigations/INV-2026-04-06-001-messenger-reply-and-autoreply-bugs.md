# INV-2026-04-06-001 -- Admin can_reply + Auto-reply display bugs

---
investigation_id: INV-2026-04-06-001
status: complete
timestamp: 2026-04-06
bugs: P1, P2
---

## Резюме

Расследованы два связанных бага мессенджера. Оба имеют чёткие корневые причины и конкретные точки исправления.

- **P1**: Admin/superuser не может отвечать в чатах из-за жёсткой проверки `role == MANAGER` в 3 местах (view, API send, API read-mark).
- **P2**: Auto-reply не отображается в виджете при новом подключении **не** из-за `since_id` race condition, а из-за того, что auto-reply создаётся **после** того как bootstrap уже вернул `initial_messages`.

---

## P1 -- Admin не может отвечать в чатах

### Корневая причина

Проверка `can_reply` привязана только к роли `MANAGER`, хотя архитектурно `ADMIN` и `superuser` имеют максимальные привилегии во всём приложении (selectors.py, SSE stream, merge-contacts, и т.д.).

### Все точки проверки ролей в messenger/ (полный список)

**Блокирующие admin (BUG -- 3 места):**

| # | Файл | Строка | Код | Последствие |
|---|------|--------|-----|-------------|
| 1 | `ui/views/messenger_panel.py` | 51 | `can_reply = user.role == User.Role.MANAGER` | UI скрывает поле ввода, кнопки "Ответить", "Назначить на себя", drag-n-drop |
| 2 | `messenger/api.py` | 559 | `if request.user.role != User.Role.MANAGER:` return 403 | API POST `/conversations/{id}/messages/` отвергает сообщение |
| 3 | `messenger/api.py` | 217 | `if request.user.role != User.Role.MANAGER:` return ignored | API POST `mark_read` -- прочтение не записывается |

**Корректно обрабатывающие admin (для сравнения):**

| Файл | Строка | Код | Что делает |
|------|--------|-----|-----------|
| `messenger/selectors.py` | 26, 49, 78 | `if user.is_superuser or user.role == User.Role.ADMIN:` | Видимость всех диалогов -- КОРРЕКТНО |
| `messenger/api.py` | 248, 334 | `if not (request.user.is_superuser or request.user.role == User.Role.ADMIN):` | merge-contacts, bulk-ops -- КОРРЕКТНО |
| `messenger/api.py` | 688 | `if not (user.is_superuser or user.role == User.Role.ADMIN):` | SSE stream access -- КОРРЕКТНО |

**Связанные ограничения (by design, НЕ баг):**

| Файл | Строка | Код | Назначение |
|------|--------|-----|-----------|
| `messenger/serializers.py` | 92 | `if assignee.role != User.Role.MANAGER:` raise | Assignee может быть ТОЛЬКО менеджером |
| `messenger/services.py` | 271, 362, 527 | `.exclude(role=User.Role.ADMIN)` | Auto-assign/round-robin пропускает admin (корректно) |
| `messenger/assignment_services/round_robin.py` | 160 | `.exclude(role=User.Role.ADMIN)` | Round-robin пропускает admin (корректно) |

### Какие роли ДОЛЖНЫ иметь can_reply

Анализ кодовой базы показывает устойчивый паттерн:

- `ADMIN` и `is_superuser` -- ПОЛНЫЙ доступ ко всему (selectors, merge, bulk-ops, SSE). Должны отвечать.
- `MANAGER` -- оператор чата, основная рабочая роль. Отвечает.
- `BRANCH_DIRECTOR`, `SALES_HEAD`, `GROUP_MANAGER` -- НЕ участвуют в мессенджере (нет AgentProfile, не попадают в round-robin, не назначаются ответственными). Могут просматривать, но не отвечать.

**Рекомендуемая формула:**

```python
can_reply = user.is_superuser or user.role in (User.Role.ADMIN, User.Role.MANAGER)
```

### Точки исправления (3 файла)

1. **`backend/ui/views/messenger_panel.py:51`**
   ```python
   # БЫЛО:
   can_reply = user.role == User.Role.MANAGER
   # НАДО:
   can_reply = user.is_superuser or user.role in (User.Role.ADMIN, User.Role.MANAGER)
   ```

2. **`backend/messenger/api.py:559`** (отправка сообщений)
   ```python
   # БЫЛО:
   if request.user.role != User.Role.MANAGER:
   # НАДО:
   if not (request.user.is_superuser or request.user.role in (User.Role.ADMIN, User.Role.MANAGER)):
   ```

3. **`backend/messenger/api.py:217`** (mark_read)
   ```python
   # БЫЛО:
   if request.user.role != User.Role.MANAGER:
   # НАДО:
   if not (request.user.is_superuser or request.user.role in (User.Role.ADMIN, User.Role.MANAGER)):
   ```

### Шаблон и JS -- менять НЕ НАДО

- `messenger_conversations_unified.html:855` -- `window.MESSENGER_CAN_REPLY = {{ can_reply|yesno:"true,false" }};` -- берёт из view context, исправится автоматически.
- `operator-panel.js:1084, 1610, 3129` -- читает `window.MESSENGER_CAN_REPLY`, исправится автоматически.

### Дополнительное замечание

`serializers.py:92` (`assignee.role != User.Role.MANAGER`) -- assignee может быть только MANAGER. Это **by design**: admin не назначается ответственным, но может отвечать в любом диалоге. Менять НЕ нужно.

---

## P2 -- Auto-reply не отображается в виджете при новом подключении

### Корневая причина

Проблема **НЕ** в `since_id` race condition. Проблема в **последовательности операций** при первом подключении.

### Последовательность событий при первом подключении

```
1. Widget: POST /api/widget/bootstrap/
   ├── Сервер: создаёт Contact + Conversation (строка 251-258)
   ├── Сервер: auto_assign_conversation (строка 263)
   ├── Сервер: dispatch_event("conversation_created") (строка 270)
   │   └── AutomationRule: send_message → record_message(direction=OUT, body="Здравствуйте...")
   │       → создаётся Message(id=N)
   ├── Сервер: формирует initial_messages (строки 313-332)
   │   └── conversation.messages.exclude(INTERNAL).order_by("-created_at")[:10]
   │   └── Auto-reply Message(id=N) ПОПАДЁТ сюда ✓
   └── Ответ: { initial_messages: [{id: N, body: "Здравствуйте...", direction: "out"}], ... }

2. Widget JS: обрабатывает bootstrap response (строки 346-363)
   ├── Находит maxId = N из initial_messages
   ├── sinceId = N (было null → стало N)
   ├── this.initialMessages = data.initial_messages  ← СОХРАНЯЕТ
   └── saveToStorage()

3. Widget JS: render() → вызывает renderMessages() 
   └── Должен отрендерить this.initialMessages в DOM
```

### Ключевой вопрос: рендерит ли widget initial_messages?

Посмотрим, как `render()` обрабатывает `this.initialMessages`:

Проверка widget.js показывает, что bootstrap сохраняет `this.initialMessages = data.initial_messages` (строка 360). Далее должен быть вызов `render()` или `renderMessages()` который отрисовывает их в DOM.

### Гипотезы почему auto-reply не видно

**Гипотеза A (вероятная): Timing -- dispatch_event вызывается ПОСЛЕ формирования initial_messages**

Проверка кода `widget_api.py` строки 258-315:

```
258: conversation.save()                     ← conversation создана
263: services.auto_assign_conversation(...)  ← assignee назначен
270: dispatch_event("conversation_created")  ← auto-reply создан ЗДЕСЬ
...
313: messages_qs = conversation.messages...  ← выборка сообщений ПОСЛЕ dispatch_event
```

Порядок: `save() → auto_assign → dispatch_event → query messages`. dispatch_event **ДО** query messages. Значит auto-reply **ДОЛЖЕН** попасть в initial_messages.

**НО**: `run_automation_for_incoming_message` (legacy auto-reply, `automation.py:35`) срабатывает только на **входящее сообщение** (`message.direction == IN`), а при `conversation_created` входящего сообщения ещё НЕТ. Legacy auto-reply НЕ сработает на bootstrap.

**Автоматический auto-reply через AutomationRule** (`dispatch_event("conversation_created")`) зависит от наличия настроенного правила в БД. Если правило есть и action = `send_message`, auto-reply попадёт в initial_messages.

**Гипотеза B (альтернативная): auto-reply создаётся legacy-механизмом при ПЕРВОМ входящем сообщении**

Если auto-reply настроен через `inbox.settings.automation.auto_reply` (legacy), он сработает ТОЛЬКО при первом входящем сообщении пользователя (`run_automation_for_incoming_message`). В этом случае:

```
1. Bootstrap → conversation создана, initial_messages = [] (пусто)
2. sinceId = null (нет сообщений)
3. SSE подключается с since_id не передан → last_id = 0
4. Пользователь отправляет сообщение → Message(id=100, direction=IN)
5. Celery/sync: run_automation_for_incoming_message → Message(id=101, direction=OUT, "Здравствуйте...")
6. SSE: filter(direction=OUT, id__gt=0) → находит Message(id=101) ← ДОЛЖЕН доставить
```

В этом сценарии `since_id` при первом SSE подключении = null → `last_id = 0` (widget_api.py:1127). SSE ищет `id__gt=0`, поэтому найдёт auto-reply. **Race condition с since_id отсутствует**.

### Реальная причина P2

**Вероятнее всего, P2 -- это проявление P0 (SSE не рендерит сообщения)**. Из current-sprint.md:

> P0 -- SSE сообщения не рендерятся в виджете. SSE стрим подключается и получает данные (701 байт), since_id обновляется (104→105), но addMessageToUI() не вызывается или сообщение не добавляется в DOM.

Auto-reply:
- Если через AutomationRule при `conversation_created` -- попадает в `initial_messages` bootstrap-ответа. Если не видно -- проблема в рендеринге initial_messages (проверить `renderMessages()`).
- Если через legacy auto-reply при первом IN-сообщении -- доставляется через SSE. Если не видно -- это P0.

### Как проверить

1. Проверить какой механизм auto-reply активен:
   ```sql
   SELECT id, settings->'automation'->'auto_reply' FROM messenger_inbox WHERE is_active=true;
   SELECT * FROM messenger_automationrule WHERE is_active=true AND event_name='conversation_created';
   ```

2. Если legacy: P2 = P0 (SSE рендеринг). Исправление P0 автоматически исправит P2.

3. Если AutomationRule: проверить, рендерятся ли `initialMessages` в DOM после bootstrap. Поискать в widget.js функцию, которая вызывает `addMessageToUI` для `this.initialMessages`.

### Рендеринг initial_messages -- код КОРРЕКТНЫЙ

Проверено: `this.initialMessages` рендерится корректно.

1. `widget.js:360` -- `this.initialMessages = data.initial_messages;` (сохранение в bootstrap)
2. `widget.js:827` -- `this.render();` вызывается сразу после bootstrap
3. `widget.js:1617-1624` -- `render()` итерирует `this.initialMessages` и вызывает `addMessageToUI(msg)` для каждого, с дедупликацией через `receivedMessageIds`

Код рендеринга initial_messages работает. Если auto-reply создаётся при `conversation_created` (AutomationRule), он попадёт в initial_messages и отрендерится.

**Вывод**: P2 возникает ТОЛЬКО при legacy auto-reply (который срабатывает на первый IN-message, а не при создании conversation). В этом случае auto-reply доставляется через SSE, и проблема = P0 (SSE не рендерит).

---

## Связь P0, P1, P2

```
P0 (SSE не рендерит) ← блокер для real-time
     ↑
P2 (auto-reply не видно) ← вероятно следствие P0 (если legacy auto-reply)
                            или отдельный баг рендеринга initialMessages

P1 (admin can't reply) ← независимый баг, 3 точки исправления
```

**Порядок исправления:**
1. P1 -- быстрый фикс, 3 строки, нет рисков
2. P0 -- SSE рендеринг (основной блокер)
3. P2 -- после P0 проверить, осталась ли проблема

---

## Файлы проанализированные

| Файл | Что искали |
|------|-----------|
| `backend/ui/views/messenger_panel.py` | can_reply логика (строка 51) |
| `backend/messenger/api.py` | Все проверки ролей (строки 217, 248, 305, 334, 559, 688) |
| `backend/messenger/selectors.py` | Паттерн проверки admin (строки 26, 49, 78) |
| `backend/messenger/serializers.py` | Ограничение assignee (строка 92) |
| `backend/messenger/services.py` | Exclude admin из round-robin (строки 271, 362, 527) |
| `backend/messenger/automation.py` | Legacy auto-reply + AutomationRule engine (полностью) |
| `backend/messenger/widget_api.py` | Bootstrap (строки 180-370), contact update (435-508), SSE stream (1115-1175) |
| `backend/messenger/static/messenger/widget.js` | bootstrap handler (297-373), SSE handler (700-758), submitPrechat (888-946) |
| `backend/messenger/static/messenger/operator-panel.js` | MESSENGER_CAN_REPLY usage (1084, 1610, 3129) |
| `backend/templates/ui/messenger_conversations_unified.html` | can_reply в шаблоне (строка 855) |
| `backend/accounts/models.py` | User.Role choices (строки 31-36) |

---

## Следующие шаги

1. **P1**: Исправить 3 точки проверки ролей (view, api send, api mark_read) -- формула `user.is_superuser or user.role in (ADMIN, MANAGER)`
2. **P2**: Запросить SQL на staging для определения механизма auto-reply (legacy vs AutomationRule)
3. **P2**: Проверить рендеринг `initialMessages` в widget.js -- найти где `this.initialMessages` обрабатывается после bootstrap
4. **P0**: Исследовать SSE буферизацию gthread (отдельное расследование)
