# Волна 4. Tasks, Notifications, Reminders

**Цель волны:** Довести систему задач, напоминаний и уведомлений до уровня, при котором менеджер не пропускает ни одной важной активности.

**Параллелизация:** средняя. Этапы 4.1–4.3 связаны, 4.4–4.5 независимы.

**Длительность:** 7–10 рабочих дней.

**Требования:** Wave 3 завершена. Audit log работает. Push notifications инфраструктура (pywebpush, firebase-admin) поднята.

---

## Этап 4.1. Задачи: календарь-вид + SLA

### Контекст
Сейчас есть Task модель с RRULE и `generate_recurring_tasks` Celery task. UI — список. Нет календарного вида, нет SLA-маркеров, нет группировки «просрочено / сегодня / завтра / этот месяц».

### Цель
Превратить задачи в ежедневный рабочий инструмент менеджера: календарь, smart-группы, SLA-индикаторы, связывание с сущностями.

### Что делать
1. **Календарный вид** (`/tasks/calendar/`):
   - Месячный / недельный / дневной.
   - FullCalendar.io (библиотека) или самопис на Tailwind grid.
   - Drag & drop для перемещения задач между днями.
   - Цвет — по TaskType, иконка — по SLA-статусу.

2. **Smart-группы** в списке:
   - «Просрочено» (overdue)
   - «Сегодня»
   - «Завтра»
   - «На этой неделе»
   - «На следующей неделе»
   - «Без срока»
   - Счётчики в sidebar.

3. **SLA** на TaskType:
   - Поле `sla_hours` (уже может быть).
   - Индикатор: зелёный / жёлтый / красный по близости к deadline.
   - Notification when SLA breach scheduled in X hours (Celery beat).

4. **Связь с сущностями**:
   - Task уже имеет FK на Company (если есть) — расширить до generic relation (Company, Deal, Contact, Conversation).
   - В карточке каждой сущности — раздел «Задачи».

5. **Повторяющиеся задачи** (RRULE):
   - Уже работает через Celery beat.
   - Улучшить UI: при создании — rrule builder («Каждый понедельник», «15-го числа каждого месяца»).
   - Библиотека: `dateutil.rrule` + кастомный JS-виджет.

6. **Templates задач**:
   - `TaskTemplate` модель: название, тип, SLA, дефолтное описание.
   - При создании сделки — auto-создание набора задач по template'у (configurable per pipeline stage).

### Инструменты
- `mcp__context7__*` — FullCalendar docs
- `mcp__playwright__*` — E2E тесты календаря

### Definition of Done
- [ ] `/tasks/calendar/` работает в 3 режимах
- [ ] Drag & drop перемещает задачу (с audit)
- [ ] Smart-группы работают с корректными счётчиками
- [ ] SLA indicators видны, Celery beat запускает SLA notifications
- [ ] RRULE builder в форме создания
- [ ] TaskTemplate: создание сделки генерирует задачи

### Артефакты
- `backend/tasks/models/template.py`
- `backend/tasks/services/task_service.py` (upgrade)
- `backend/tasks/services/sla_service.py`
- `backend/ui/views/pages/tasks/calendar.py`
- `backend/templates/pages/tasks/calendar.html`
- `backend/static/ui/tasks/calendar.js`
- `backend/static/ui/tasks/rrule-builder.js`
- `backend/celery/tasks/sla_notifications.py`
- `tests/tasks/test_calendar.py`
- `tests/tasks/test_sla.py`
- `docs/features/tasks.md`

### Валидация
```bash
pytest tests/tasks/
playwright test tests/e2e/test_tasks_calendar.py
```

### Откат
```bash
git revert
```

### Обновить в документации
- `docs/features/tasks.md`

---

## Этап 4.2. Notification Hub: централизованная отправка

### Контекст
Сейчас: in-app Notification, email, web push (pywebpush), FCM (firebase-admin — под звонки). Всё рассеяно. Каждый производитель события сам решает, куда слать.

### Цель
Единый Notification Hub: producer создаёт событие → Hub решает, куда слать (на основе user preferences, event type, role).

### Что делать
1. **Архитектура**:
   ```
   event (e.g. "task.assigned") 
     → NotificationHub.dispatch(event, payload, recipients)
     → per-recipient: apply UserNotificationPreferences
     → deliver via enabled channels (in-app, email, web_push, fcm, telegram*)
     → track delivery status
   ```

2. **Модели**:
   - `NotificationEvent` (тип события, шаблон).
   - `Notification` (одна запись на получателя + факт доставки).
   - `NotificationDelivery` (per-channel: channel, status, delivered_at, error).
   - `UserNotificationPreferences`:
     - По каждому `event_type` — список каналов (in-app always on, others optional).
     - DND (do not disturb): часы + дни недели.
     - Digest: вместо мгновенных — daily summary at X time.

3. **Шаблоны**:
   - Per-channel templates: email (MJML/HTML), push (title+body+deeplink), in-app (text + icon + action buttons).
   - Jinja-like interpolation из payload.

4. **Каналы** (provider abstraction):
   - `InAppProvider` — сохраняет в `Notification`, пушит через SSE/WebSocket.
   - `EmailProvider` — Celery + SMTP pool (существующий).
   - `WebPushProvider` — pywebpush.
   - `FCMProvider` — firebase-admin.
   - `TelegramProvider` — опционально (V2), но интерфейс заложен.

5. **Preferences UI** (`/profile/notifications/`):
   - Матрица «событие × канал» с чек-боксами.
   - DND настройки.
   - Digest настройки.

6. **Audit**:
   - `NotificationDelivery` — полный лог.
   - Dashboard админа: deliverability rates.

7. **Retry logic**:
   - Transient failures (SMTP timeout, FCM 5xx) — retry с exp backoff.
   - Permanent failures (invalid token) — mark as failed + уведомление админа после X.

### Инструменты
- `mcp__context7__*`

### Definition of Done
- [ ] NotificationHub работает, события диспатчатся через него
- [ ] 4 канала реализованы: in-app, email, web_push, fcm
- [ ] UserNotificationPreferences + DND + Digest — UI и backend
- [ ] Все существующие уведомления мигрированы на Hub
- [ ] Retry logic работает
- [ ] Admin dashboard: deliverability
- [ ] Тесты: 20+ сценариев

### Артефакты
- `backend/notifications/hub.py`
- `backend/notifications/models.py`
- `backend/notifications/providers/*.py`
- `backend/notifications/templates/*.py` (template registry)
- `backend/notifications/services/preferences.py`
- `backend/ui/views/pages/profile/notifications.py`
- `backend/templates/profile/notifications/*.html`
- `backend/ui/views/pages/admin/notification_dashboard.py`
- `tests/notifications/`
- `docs/features/notifications.md`
- `docs/runbooks/notification-troubleshooting.md`

### Валидация
```bash
pytest tests/notifications/
playwright test tests/e2e/test_notification_preferences.py
```

### Откат
```bash
git revert
```

### Обновить в документации
- `docs/features/notifications.md`
- `docs/decisions.md`: ADR-017

---

## Этап 4.3. Напоминания по задачам и упоминаниям

### Контекст
Менеджерам нужны reminder'ы: «задача через 1 час», «задача просрочена», «меня упомянули в комментарии». Сейчас не все срабатывают.

### Цель
Полный набор напоминаний с кастомизацией.

### Что делать
1. **Типы reminder'ов**:
   - Task due soon (customizable: 15m / 1h / 1d before).
   - Task overdue (immediate + repeat every 4h).
   - Deal stage SLA breach soon.
   - Unread conversation > 30min.
   - Mention in comment (@username syntax в notes, task descriptions).
   - Campaign scheduled to send in 1 hour (для author).

2. **Celery beat jobs**:
   - `check_upcoming_task_reminders` — каждые 5 минут.
   - `check_overdue_tasks` — каждые 30 минут.
   - `check_sla_breaches` — каждые 15 минут.
   - `check_unread_conversations` — каждые 10 минут.

3. **@mentions**:
   - Parser в notes/tasks: `@john.doe` → linkify + trigger notification.
   - UI: autocomplete при наборе `@` (выпадающий список юзеров видимых scope'у).

4. **Snooze**:
   - Reminder в notification можно «отложить»: 10m / 1h / tomorrow.
   - Сохраняется в `NotificationSnooze`.

5. **Quiet hours** (часть UserNotificationPreferences, Wave 4.2):
   - Reminders накапливаются в DND-часы, отправляются одним digest-ом в начало рабочего дня.

### Инструменты
- `mcp__context7__*` — Django signals

### Definition of Done
- [ ] 6 типов reminder'ов работают
- [ ] @mentions parser + autocomplete
- [ ] Snooze работает, state восстанавливается
- [ ] Quiet hours соблюдаются
- [ ] Тесты: 15+ сценариев

### Артефакты
- `backend/tasks/reminders.py`
- `backend/core/mentions/parser.py`
- `backend/core/mentions/autocomplete.py`
- `backend/notifications/snooze.py`
- `backend/static/ui/mentions-autocomplete.js`
- `backend/celery/tasks/reminders.py`
- `tests/reminders/`
- `docs/features/reminders.md`

### Валидация
```bash
pytest tests/reminders/
```

### Откат
```bash
git revert
```

### Обновить в документации
- `docs/features/reminders.md`

---

## Этап 4.4. Встроенный календарь-агрегат

### Контекст
У менеджера: задачи, запланированные звонки (Wave 7), запланированные рассылки, встречи (если есть). Сейчас — в разных местах.

### Цель
Единая страница `/calendar/` с агрегатом всех activities менеджера на календаре.

### Что делать
1. Расширить календарный view (Wave 4.1) до агрегатного:
   - Задачи (tasks)
   - Запланированные звонки (scheduled calls — из phonebridge)
   - Запланированные рассылки (scheduled campaigns — из mailer)
   - Встречи (Meeting model — новая, опциональная)

2. **Meeting модель**:
   - Связь с Company/Contact/Deal.
   - Location (physical address или URL для online).
   - Participants (M2M User).
   - Reminders.
   - Integration с Google/Yandex Calendar — V2 (заложить iCal export для начала).

3. **iCal export**:
   - `/calendar/ical/<user_token>.ics` — подписной календарь.
   - Юзер добавляет в Google / Yandex Calendar / Apple Calendar — auto-sync.

4. **View переключатели**:
   - Day / Week / Month / Agenda.
   - Фильтры: типы activities, кастомизация.

### Definition of Done
- [ ] Агрегатный календарь показывает 4 типа activities
- [ ] Meeting модель работает
- [ ] iCal подписка работает (протестирована в Google Calendar)
- [ ] Фильтры работают

### Артефакты
- `backend/calendar_app/` (новый app)
- `backend/calendar_app/models.py` (Meeting)
- `backend/calendar_app/services/aggregator.py`
- `backend/calendar_app/services/ical.py`
- `backend/ui/views/pages/calendar.py`
- `backend/templates/pages/calendar/*.html`
- `backend/static/ui/calendar/*.js`
- `tests/calendar/`
- `docs/features/calendar.md`

### Валидация
```bash
pytest tests/calendar/
# Manual: добавить meeting, увидеть в календаре, подписать на iCal feed в Google
```

### Откат
```bash
git revert
```

### Обновить в документации
- `docs/features/calendar.md`

---

## Этап 4.5. Activity feed («последние действия»)

### Контекст
У менеджера часто вопрос «что произошло за последний час/день по моим клиентам?». Нужна лента.

### Цель
Activity feed на главной странице с фильтрами.

### Что делать
1. **Model**: уже есть `ActivityEvent` (retention 180d). Расширить при необходимости.

2. **UI** на `/` (dashboard):
   - Виджет «Последние события» — top 20 событий по scope юзера.
   - Группировка по сущности (клик → переход к карточке).
   - Фильтры: тип события, дата, филиал (если SALES_HEAD+).

3. **Типы событий**:
   - Новый клиент создан (широкий scope)
   - Клиент передан
   - Сделка создана / изменила стадию
   - Задача создана / выполнена
   - Сообщение в чате
   - Письмо отправлено
   - Звонок сделан
   - Note добавлена

4. **Realtime update**:
   - SSE: подписка на event feed по user_id.
   - При новом событии — обновление без reload.

### Инструменты
- Already have SSE/channels

### Definition of Done
- [ ] Activity feed виджет на главной
- [ ] 8+ типов событий
- [ ] Фильтры
- [ ] Realtime update (SSE)
- [ ] Performance: < 200ms для last 20 events

### Артефакты
- `backend/ui/views/partials/activity_feed.py`
- `backend/templates/partials/activity_feed.html`
- `backend/static/ui/activity-feed.js`
- `backend/api/v1/views/activity.py`
- `tests/activity/test_feed.py`

### Валидация
```bash
pytest tests/activity/
```

### Откат
```bash
git revert
```

### Обновить в документации
- `docs/features/activity-feed.md`

---

## Checklist завершения волны 4

- [ ] Task calendar view работает
- [ ] Notification Hub централизовал всю отправку
- [ ] 4 канала уведомлений работают
- [ ] Preferences с DND и digest
- [ ] 6 типов reminders
- [ ] @mentions работают
- [ ] Агрегатный календарь с iCal export
- [ ] Activity feed на главной

**Только после этого** — переход к Wave 5 (Live-chat) или параллельно W5/W6/W7.
