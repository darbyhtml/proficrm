# Chatwoot: Deep Architecture Analysis
# Для построения аналога в ProfiCRM (Django)

---

## 1. Общая архитектура

```
                    +-----------+
                    |  Nginx    |
                    +-----+-----+
                          |
              +-----------+-----------+
              |                       |
        +-----+-----+          +-----+-----+
        | Rails App  |          | ActionCable |
        | (Puma)     |          | (WebSocket) |
        +-----+------+          +------+-----+
              |                        |
              +--------+-------+-------+
                       |       |
                 +-----+--+ +--+-----+
                 |Postgres| | Redis  |
                 +--------+ +--+-----+
                               |
                         +-----+-----+
                         | Sidekiq   |
                         | (фоновые  |
                         |  задачи)  |
                         +-----------+
```

**Наш аналог:**
- Rails App -> Django + Gunicorn
- ActionCable -> Django Channels (или SSE с улучшениями)
- PostgreSQL -> PostgreSQL (уже есть)
- Redis -> Redis (уже есть)
- Sidekiq -> Celery (уже настроен)

---

## 2. База данных: ключевые таблицы

### 2.1 Мультитенантность

Chatwoot: каждая сущность привязана к `account_id`. Account = организация.

**У нас аналог**: `Branch` (филиал). Уже реализовано.

### 2.2 Core-таблицы

#### conversations
```
id, account_id, inbox_id, contact_id, assignee_id, team_id,
display_id (автоинкремент per account),
status: enum (open=0, resolved=1, pending=2, snoozed=3),
priority: enum (low=0, medium=1, high=2, urgent=3),
waiting_since (timestamp - когда клиент ждёт ответа),
first_reply_created_at (timestamp - метрика FRT),
snoozed_until (timestamp),
contact_last_seen_at, agent_last_seen_at, assignee_last_seen_at,
last_activity_at,
additional_attributes (JSONB), custom_attributes (JSONB),
uuid (уникальный)
```
**17 индексов** на тяжёлые паттерны запросов.

**У нас**: Conversation модель есть, ~90% полей совпадает. Нет `display_id`, `team_id`.

#### messages
```
id, account_id, conversation_id,
message_type: enum (incoming=0, outgoing=1, activity=2, template=3),
content_type: enum (text, input_select, cards, form, article, input_csat,
                    integrations, input_email, voice_call... всего 13 типов),
content (text), processed_message_content (text),
status: enum (sent=0, delivered=1, read=2, failed=3),
sender_type + sender_id (полиморфный: User, Contact, AgentBot),
content_attributes (JSONB - in_reply_to, deleted, etc),
external_source_ids (JSONB),
source_id (string - ID из внешнего канала)
```
GIN trigram индекс на `content` для полнотекстового поиска.

**У нас**: Message модель есть. Нет `content_type` enum (у нас только text). Нет `status` (sent/delivered/read/failed). Нет полнотекстового поиска.

#### contacts
```
id, account_id, name, email, phone_number,
contact_type: enum (visitor=0, lead=1, customer=2),
identifier (внешний ID), additional_attributes (JSONB),
custom_attributes (JSONB), avatar (file),
last_activity_at, blocked (boolean)
```
GIN trigram индекс на `name` для fuzzy-поиска.

**У нас**: Contact модель есть, ~80% совпадает. Нет `contact_type` (visitor/lead/customer lifecycle).

#### inboxes
```
id, account_id, name,
channel_type + channel_id (полиморфный: WebWidget, Email, Telegram...),
enable_auto_assignment, auto_assignment_config (JSONB),
greeting_enabled, greeting_message,
out_of_office_message, csat_survey_enabled,
allow_messages_after_resolved,
working_hours_enabled, timezone,
sender_name_type: enum (friendly=0, professional=1),
business_name, lock_to_single_conversation
```

**У нас**: Inbox модель есть с JSONField settings. Нужно вынести ключевые поля из JSON в отдельные колонки для индексации.

#### channel_web_widgets
```
id, account_id, inbox_id, website_url, website_token,
widget_color (#hex), welcome_title, welcome_tagline,
reply_time: enum (in_a_few_minutes=0, in_a_few_hours=1, in_a_day=2),
feature_flags (integer bitfield),
pre_chat_form_enabled, pre_chat_form_options (JSONB),
hmac_token, hmac_mandatory
```

**У нас**: Нет отдельной модели для виджет-настроек. Всё в `Inbox.settings` JSON. Нужно создать.

---

## 3. Real-time: как сообщения доставляются мгновенно

### 3.1 Chatwoot: ActionCable + Redis Pub/Sub

```
Посетитель отправляет сообщение:

1. POST /api/v1/widget/messages
2. Message.save! (PostgreSQL)
3. after_create_commit:
   |
   +-- SyncDispatcher (в том же потоке, мгновенно):
   |   |
   |   +-- ActionCableListener.message_created
   |       |
   |       +-- Собирает tokens:
   |       |   - agents: inbox.members.pluck(:pubsub_token) + admins
   |       |   - contact: contact_inbox.pubsub_token
   |       |
   |       +-- ActionCableBroadcastJob (Sidekiq, queue: critical)
   |           |
   |           +-- Redis.publish(token, {event: "message.created", data: ...})
   |               |
   |               +-- ActionCable сервер подхватывает из Redis
   |                   |
   |                   +-- WebSocket push --> Браузер агента
   |                   +-- WebSocket push --> Виджет посетителя (echo)
   |
   +-- AsyncDispatcher (Sidekiq job, фоново):
       |
       +-- NotificationListener -> Notification.create -> WebSocket push
       +-- AutomationRuleListener -> проверка правил
       +-- WebhookListener -> HTTP POST на внешний URL
       +-- ReportingEventListener -> метрики
       +-- CsatSurveyListener -> опрос удовлетворённости
```

**Ключевой паттерн**: Dual Dispatcher
- **Sync** = ActionCable broadcast (критично для latency, <100ms)
- **Async** = всё остальное через Sidekiq (notifications, webhooks, automation)

### 3.2 Наш аналог на Django

```
Посетитель отправляет сообщение:

1. POST /api/widget/send/
2. Message.save() (PostgreSQL)
3. post_save signal:
   |
   +-- SyncDispatcher (в том же потоке):
   |   |
   |   +-- Django Channels: channel_layer.group_send(
   |   |     f"conversation_{conv_id}",
   |   |     {"type": "message.created", "data": serialized_message}
   |   |   )
   |   |   --> WebSocket push агенту и виджету
   |   |
   |   +-- ИЛИ (без Channels): Redis PUBLISH
   |       --> SSE endpoint подхватывает
   |
   +-- AsyncDispatcher (Celery task):
       |
       +-- send_notification.delay(message_id)
       +-- check_automation_rules.delay(message_id)
       +-- send_webhooks.delay(message_id)
       +-- record_reporting_event.delay(conversation_id)
```

**Что нужно сделать:**
- Вариант A: Django Channels (WebSocket) - полный аналог ActionCable
- Вариант B: Улучшенный SSE + Redis PUBLISH - проще, но ~1сек задержка

---

## 4. Аутентификация WebSocket / real-time

### Chatwoot

Каждый User и ContactInbox имеет уникальный `pubsub_token` (32 байта, URL-safe).

```
Агент подключается:
  WebSocket /cable
  -> RoomChannel.subscribed(pubsub_token: "abc123")
  -> Сервер: User.find_by(pubsub_token: "abc123")
  -> stream_from "abc123"  (персональный канал)

Виджет подключается:
  WebSocket /cable
  -> RoomChannel.subscribed(pubsub_token: "xyz789")
  -> Сервер: ContactInbox.find_by(pubsub_token: "xyz789")
  -> stream_from "xyz789"

Broadcast:
  ActionCable.server.broadcast("abc123", {event: "message.created", ...})
  --> Только этот конкретный пользователь получает
```

**У нас**: `pubsub_token` уже есть в `ContactInbox`. Нужно добавить в `User`/`AgentProfile`.

---

## 5. Presence (онлайн-статус)

### Chatwoot: Redis Sorted Sets

```python
# Структура в Redis:
ONLINE_PRESENCE_USERS:{account_id}   = SortedSet { user_id: timestamp }
ONLINE_PRESENCE_CONTACTS:{account_id} = SortedSet { contact_id: timestamp }
ONLINE_STATUS:{account_id}            = Hash { user_id: "online"|"busy"|"offline" }

# Агент подключился:
ZADD ONLINE_PRESENCE_USERS:1 1712345678 42    # user 42, timestamp
HSET ONLINE_STATUS:1 42 "online"

# Проверка "кто онлайн" (каждые 20сек для агентов):
ZRANGEBYSCORE ONLINE_PRESENCE_USERS:1 (now-20) +inf  # все с timestamp < 20сек назад = offline
ZREMRANGEBYSCORE ONLINE_PRESENCE_USERS:1 -inf (now-20) # удалить протухших

# Контакты - 90сек TTL (виджет пингует раз в 60сек)
```

**У нас**: Online status уже есть в `AgentProfile` + Redis кэш. Нужно перейти на Sorted Set для точности.

---

## 6. Уведомления: 3-канальная доставка

### 6.1 In-app (WebSocket)

```
Notification.create!(user: agent, type: :assigned_conversation_new_message, ...)
  --> after_create_commit:
      ActionCable.broadcast(agent.pubsub_token, {event: "notification.created", ...})
  --> Браузер: обновить счётчик колокольчика, показать toast
```

### 6.2 Звук (browser-side)

```javascript
// При получении message.created через WebSocket:
onMessageCreated(data) {
  // Проверки: не моё сообщение, не текущий открытый диалог
  if (data.sender_id !== currentUser.id && data.conversation_id !== activeConversation) {
    new Audio('/assets/ting.mp3').play();
  }
}
```

**У нас**: НЕТ. Нужно добавить: звуковой файл + JS логику воспроизведения.

### 6.3 Browser Push (Web Push API)

```
Регистрация:
1. Агент включает push в настройках профиля
2. Browser: navigator.serviceWorker.register('/sw.js')
3. Browser: registration.pushManager.subscribe({
     userVisibleOnly: true,
     applicationServerKey: VAPID_PUBLIC_KEY
   })
4. Browser -> POST /notification_subscriptions {subscription_json}
5. Сервер сохраняет endpoint + keys в notification_subscriptions таблицу

Отправка:
1. Notification.create! -> after_create_commit
2. PushNotificationService.new(notification).perform
3. Для каждой подписки пользователя:
   WebPush.payload_send(
     endpoint: subscription.endpoint,
     p256dh: subscription.p256dh,
     auth: subscription.auth,
     vapid: {public_key: ..., private_key: ...},
     message: JSON({title: "Новое сообщение", body: "...", url: "/conversations/123"})
   )
4. Service Worker получает push -> показывает нативное уведомление ОС
```

**notification_subscriptions таблица:**
```
id, user_id, subscription_type (enum: browser_push=0, fcm=1),
subscription_attributes (JSONB: {endpoint, p256dh, auth, push_token}),
identifier (unique string)
```

**У нас**: НЕТ. Нужно: модель NotificationSubscription, VAPID ключи, Service Worker, Web Push библиотека.

### 6.4 Email-уведомления

```
Notification.create! -> Celery/Sidekiq:
  if user.notification_settings.email_assigned_conversation_new_message?
    if !notification.read_at  # ещё не прочитано
      AgentNotificationsMailer.conversation_notification(notification).deliver_later
```

**У нас**: НЕТ для мессенджера (есть для CRM задач). Нужно добавить.

---

## 7. Pre-chat форма

### Chatwoot

```
Конфиг хранится в channel_web_widgets.pre_chat_form_options:
{
  "pre_chat_message": "Расскажите о себе",
  "pre_chat_fields": [
    {
      "name": "emailAddress",
      "type": "email", 
      "label": "Email",
      "required": true,
      "enabled": true,
      "field_type": "standard"    // standard | custom
    },
    {
      "name": "fullName",
      "type": "text",
      "label": "Имя",
      "required": false,
      "enabled": true,
      "field_type": "standard"
    },
    {
      "name": "company_size",
      "type": "list",
      "label": "Размер компании",
      "values": ["1-10", "11-50", "51-200", "200+"],
      "required": false,
      "enabled": true,
      "field_type": "custom"       // -> contact.custom_attributes
    }
  ]
}

Поток:
1. Виджет открывается -> если pre_chat_form_enabled -> показать форму
2. Посетитель заполняет -> POST /widget/contact (name, email, phone, custom_attributes)
3. Сервер: Contact.update!(name:, email:, phone:, custom_attributes: {company_size: "11-50"})
4. Только после этого -> создаётся Conversation, открывается чат
```

**У нас**: НЕТ. Нужно: поле `pre_chat_form_options` в Inbox, компонент формы в widget.js, эндпоинт обновления контакта.

---

## 8. Automation Rules (правила автоматизации)

### Chatwoot

```
Модель:
  automation_rules:
    event_name: "conversation_created" | "message_created" | "conversation_updated"
    conditions: JSONB [
      {
        "attribute_key": "inbox_id",
        "filter_operator": "equal_to",
        "values": [5],
        "query_operator": "AND"
      },
      {
        "attribute_key": "browser_language",
        "filter_operator": "contains",
        "values": ["ru"],
        "query_operator": null
      }
    ]
    actions: JSONB [
      {"action_name": "assign_team", "action_params": [3]},
      {"action_name": "add_label", "action_params": ["russian"]},
      {"action_name": "send_message", "action_params": ["Здравствуйте!"]}
    ]

Обработка (event-driven через Listener):
1. Событие conversation_created
2. AutomationRuleListener: загрузить все active rules с event_name="conversation_created"
3. Для каждого rule: ConditionsFilterService проверяет conditions (строит SQL WHERE)
4. Если условия совпали: ActionService выполняет actions по очереди
5. Защита от цикла: если действие вызвано автоматизацией -> не триггерить автоматизации снова
```

**Поддерживаемые действия:**
- assign_agent, assign_team
- add_label, remove_label
- send_message, add_private_note
- change_status, change_priority
- mute_conversation, snooze_conversation
- send_email_transcript
- send_webhook_event
- send_attachment

**У нас**: Только auto_reply. Нужно: модель AutomationRule, ConditionsFilter, ActionService, интеграция с EventDispatcher.

---

## 9. Кампании (проактивные сообщения)

### Chatwoot

```
Модель campaigns:
  campaign_type: ongoing (виджет) | one_off (рассылка)
  trigger_rules: {"url": "https://site.com/pricing*", "time_on_page": 15}
  message: "Нужна помощь с выбором тарифа?"

Логика (КЛИЕНТСКАЯ для ongoing):
1. Виджет загружается -> GET /campaigns -> получает список активных кампаний
2. JS проверяет: текущий URL совпадает с trigger_rules.url? (wildcard matching)
3. Если да -> setTimeout(showCampaign, time_on_page * 1000)
4. Через 15 секунд -> показать bubble "Нужна помощь?" (только если виджет закрыт)
5. Посетитель кликает -> открывается чат с этим сообщением как первым
```

**У нас**: НЕТ. Нужно: модель Campaign, API endpoint, JS-логика в widget.js.

---

## 10. Macros (быстрые действия оператора)

### Chatwoot

```
Модель macros:
  name: "Эскалация в техподдержку"
  visibility: personal | global
  actions: [
    {"action_name": "assign_team", "action_params": [3]},
    {"action_name": "add_label", "action_params": ["escalated"]},
    {"action_name": "change_priority", "action_params": ["urgent"]},
    {"action_name": "send_message", "action_params": ["Передано в техподдержку."]}
  ]

Использование:
  Оператор нажимает кнопку "Макросы" -> выбирает -> POST /macros/:id/execute
  -> MacroExecutionService итерирует actions, вызывает каждый
```

Макросы используют тот же `ActionService` что и Automation Rules.

**У нас**: НЕТ. Но если реализуем ActionService для автоматизаций — макросы получатся почти бесплатно.

---

## 11. CSAT (опрос удовлетворённости)

### Chatwoot

```
Триггер:
1. Диалог переходит в status=resolved
2. CsatSurveyListener проверяет:
   - csat_survey_enabled? на inbox
   - Не отправляли уже CSAT в этот диалог
   - Правила (labels include/exclude)
3. Создаёт Message с content_type=input_csat
4. Виджет показывает emoji-рейтинг (1-5) + текстовое поле

Хранение:
  csat_survey_responses:
    conversation_id, contact_id, message_id (unique),
    assigned_agent_id, rating (1-5), feedback_message
```

**У нас**: Модель есть (rating_score, rating_comment на Conversation). Нет отдельной таблицы. Нет автоматического триггера при resolve. Нет UI в виджете.

---

## 12. Reporting (аналитика)

### Chatwoot

```
reporting_events:
  name: "first_response" | "reply_time" | "conversation_resolved" | "conversation_opened"
  value: 45.2 (секунды)
  value_in_business_hours: 30.1 (секунды, исключая нерабочее время)
  conversation_id, inbox_id, user_id (агент)
  event_start_time, event_end_time

Агрегация:
  reporting_events_rollups (предагрегированные суточные данные)

Дашборд:
  7 типов отчётов: по диалогам, агентам, командам, инбоксам, меткам, обзор, CSAT
  Фильтры: дата, агент, команда, инбокс, канал
  Метрики: кол-во диалогов, FRT, среднее время ответа, время решения, CSAT
```

**У нас**: Модели для метрик есть (first_reply_created_at, waiting_since). Нет reporting_events, нет API отчётов, нет UI дашборда.

---

## 13. Contact Merge (объединение дубликатов)

### Chatwoot

```
POST /contacts/:base_id/merge { mergee_id: 456 }

В одной транзакции:
1. UPDATE conversations SET contact_id = base WHERE contact_id = mergee
2. UPDATE messages SET sender_id = base WHERE sender_type = 'Contact' AND sender_id = mergee
3. Переносит contact_inboxes, notes
4. Мержит атрибуты: mergee.merge(base) — base приоритетнее
5. Удаляет mergee
```

**У нас**: НЕТ. Нужно: endpoint + service.

---

## 14. Кастомизация виджета

### Chatwoot (channel_web_widgets)

```
widget_color: "#1F93FF"          // любой hex-цвет
welcome_title: "Привет!"
welcome_tagline: "Мы обычно отвечаем за несколько минут"
reply_time: in_a_few_minutes | in_a_few_hours | in_a_day
feature_flags: bitfield (
  attachments, emoji_picker, end_conversation,
  use_inbox_avatar_for_bot, typing_indicator
)
```

Виджет получает эти настройки при bootstrap и применяет CSS custom properties:
```css
:root { --widget-color: #1F93FF; }
```

**У нас**: Захардкожено. Нужно: модель WidgetConfig (или поля в Inbox), передача при bootstrap, CSS variables в widget.js.

---

## 15. Сравнительная карта: Chatwoot vs Наш мессенджер

### Уже реализовано (совпадает с Chatwoot):
- [x] Conversations CRUD + status lifecycle
- [x] Messages (incoming/outgoing/internal)
- [x] Contacts + ContactInbox
- [x] Inboxes с настройками
- [x] Round-robin assignment + rate limiter
- [x] Escalation (reassignment по таймауту)
- [x] GeoIP routing (ЛУЧШЕ чем Chatwoot)
- [x] Labels / теги
- [x] Canned responses
- [x] Working hours + offline mode
- [x] Typing indicators (Redis TTL)
- [x] SSE streaming (widget + operator)
- [x] Webhooks (HMAC-signed)
- [x] Event dispatcher
- [x] Anti-spam (throttle + CAPTCHA)
- [x] Widget origin validation
- [x] File attachments
- [x] CSAT (базовый, на модели)
- [x] Snooze
- [x] Priority
- [x] Bulk actions
- [x] Management commands (close old, escalate, GDPR)

### Нужно реализовать:

#### P0 - Критично (без этого нельзя работать):
- [ ] Звуковые уведомления (ting.mp3 при новом сообщении)
- [ ] Browser Push Notifications (VAPID + Service Worker)
- [ ] Уменьшить latency real-time (WebSocket или быстрый SSE reconnect)

#### P1 - Важно (для нормальной работы):
- [ ] Pre-chat форма (настраиваемые поля перед чатом)
- [ ] Auto-resolve (Celery beat, закрывать неактивные)
- [ ] Кастомизация виджета (цвет, текст, позиция)
- [ ] Message status tracking (sent -> delivered -> read)
- [ ] Email notification агенту (при offline)

#### P2 - Качество (паритет с Chatwoot):
- [ ] Shortcode `/` для canned responses в поле ввода
- [ ] Проактивные кампании (trigger по URL + время на странице)
- [ ] Automation Rules (conditions -> actions, event-driven)
- [ ] Macros (быстрые multi-action кнопки)
- [ ] Базовая аналитика (FRT, resolution time, CSAT dashboard)
- [ ] @mentions в private notes
- [ ] Contact merge
- [ ] Delete/copy message actions
- [ ] Contact lifecycle (visitor -> lead -> customer)

#### P3 - Расширения (конкурентное преимущество):
- [ ] Telegram/WhatsApp каналы
- [ ] AI-бот (auto-reply с LLM)
- [ ] Email как канал
- [ ] Мобильное приложение / PWA

---

## 16. Рекомендуемый порядок реализации

### Фаза 1: "Можно работать" (1-2 дня)
1. Звук при новом сообщении
2. Browser Push Notifications (VAPID)
3. Service Worker для push
4. Уменьшить polling interval SSE (5сек вместо 25-30)

### Фаза 2: "Удобно работать" (2-3 дня)
5. Pre-chat форма в виджете
6. Кастомизация виджета (цвет, текст)
7. Auto-resolve через Celery beat
8. Email уведомление агенту
9. Message delivery status (sent/delivered/read)

### Фаза 3: "Как Chatwoot" (3-5 дней)
10. Shortcode `/` для canned responses
11. Кампании (проактивные сообщения)
12. Базовая аналитика (reporting_events + dashboard)
13. Contact merge
14. @mentions

### Фаза 4: "Лучше Chatwoot" (5-7 дней)
15. Automation Rules engine
16. Macros
17. Telegram канал
18. AI-бот интеграция
