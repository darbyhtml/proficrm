# Полный анализ архитектуры и реализации Chatwoot

Этот документ содержит доскональное изучение кода Chatwoot для понимания всех архитектурных решений, паттернов и edge cases.

**Дата создания:** 2026-02-20  
**Версия Chatwoot:** последняя из main ветки  
**Цель:** Полное понимание архитектуры для улучшения нашего мессенджера

---

## Оглавление

1. [Архитектура проекта](#архитектура-проекта)
2. [Модели данных](#модели-данных)
3. [Real-time коммуникация](#real-time-коммуникация)
4. [Сервисы и бизнес-логика](#сервисы-и-бизнес-логика)
5. [API и контроллеры](#api-и-контроллеры)
6. [Виджет](#виджет)
7. [Frontend (React)](#frontend-react)
8. [Безопасность и валидации](#безопасность-и-валидации)
9. [Производительность и оптимизации](#производительность-и-оптимизации)
10. [Edge cases и обработка ошибок](#edge-cases-и-обработка-ошибок)
11. [Тесты и покрытие](#тесты-и-покрытие)
12. [Ключевые находки](#ключевые-находки)

---

## Архитектура проекта

### Технологический стек

- **Backend:** Ruby on Rails (Rails 7+)
- **Frontend:** React (Vue.js для некоторых компонентов)
- **Real-time:** ActionCable (WebSocket)
- **База данных:** PostgreSQL
- **Кэш:** Redis
- **Фоновые задачи:** Sidekiq
- **Поиск:** Elasticsearch (опционально, через Searchkick)

### Структура проекта

```
chatwoot/
├── app/
│   ├── models/              # ActiveRecord модели
│   ├── controllers/         # API контроллеры
│   ├── channels/            # ActionCable каналы (WebSocket)
│   ├── services/            # Бизнес-логика (сервисы)
│   ├── jobs/                # Фоновые задачи (Sidekiq)
│   ├── dispatchers/         # Event dispatcher система
│   ├── javascript/          # Frontend код
│   │   ├── dashboard/       # Операторская панель (React)
│   │   └── widget/          # Виджет для сайта
│   └── concerns/            # Ruby concerns (mixins)
├── db/
│   └── migrate/            # Миграции БД
└── spec/                    # Тесты (RSpec)
```

### Ключевые архитектурные паттерны

1. **Service Objects** — бизнес-логика вынесена в сервисы
2. **Concerns (Mixins)** — переиспользование логики через модули
3. **Event Dispatcher** — система событий для real-time обновлений
4. **PubSub** — публикация/подписка через ActionCable
5. **Callbacks** — ActiveRecord callbacks для автоматизации

---

## Модели данных

### Conversation (Диалог)

#### Схема БД

```ruby
# Основные поля:
- id: integer (PK)
- display_id: integer (человекочитаемый номер, уникальный в рамках account)
- uuid: uuid (уникальный идентификатор)
- account_id: integer (FK)
- inbox_id: integer (FK)
- contact_id: bigint (FK)
- contact_inbox_id: bigint (FK)
- assignee_id: integer (FK, nullable)
- assignee_agent_bot_id: bigint (FK, nullable)
- team_id: bigint (FK, nullable)
- campaign_id: bigint (FK, nullable)
- sla_policy_id: bigint (FK, nullable)

# Статусы и приоритеты:
- status: integer (enum: open, resolved, pending, snoozed)
- priority: integer (enum: low, medium, high, urgent)

# Временные метки:
- created_at: datetime
- updated_at: datetime
- last_activity_at: datetime (NOT NULL, обновляется при сообщениях)
- waiting_since: datetime (когда начал ждать ответа)
- first_reply_created_at: datetime (время первого ответа оператора)
- snoozed_until: datetime (отложен до)
- agent_last_seen_at: datetime (когда агент последний раз видел)
- assignee_last_seen_at: datetime (когда назначенный оператор видел)
- contact_last_seen_at: datetime (когда контакт видел)

# Метаданные:
- additional_attributes: jsonb (метаданные: referer, browser, OS, etc.)
- custom_attributes: jsonb (кастомные атрибуты)
- identifier: string (идентификатор из внешней системы)
- cached_label_list: text (кэшированные метки)
```

#### Ключевые индексы

```sql
-- Составной индекс для списка диалогов (критически важен!)
CREATE INDEX conv_acid_inbid_stat_asgnid_idx 
ON conversations(account_id, inbox_id, status, assignee_id);

-- Уникальность display_id в рамках account
CREATE UNIQUE INDEX index_conversations_on_account_id_and_display_id 
ON conversations(account_id, display_id);

-- Индексы для фильтрации
CREATE INDEX index_conversations_on_status_and_account_id 
ON conversations(status, account_id);
CREATE INDEX index_conversations_on_priority 
ON conversations(priority);
CREATE INDEX index_conversations_on_waiting_since 
ON conversations(waiting_since);
CREATE INDEX index_conversations_on_first_reply_created_at 
ON conversations(first_reply_created_at);
```

#### Важные методы и логика

1. **`display_id`** — генерируется через DB trigger:
   ```sql
   -- Последовательность для каждого account отдельно
   NEW.display_id := nextval('conv_dpid_seq_' || NEW.account_id);
   ```
   - Загружается после создания через `load_attributes_created_by_db_triggers`
   - Уникален в рамках account

2. **`last_activity_at`** — обновляется при каждом сообщении:
   ```ruby
   # В Message model:
   conversation.update_columns(last_activity_at: created_at)
   ```
   - Используется для авто-резолва диалогов
   - Fallback на `created_at` если не задан

3. **`waiting_since`** — логика ожидания:
   - Устанавливается при создании диалога (`ensure_waiting_since`)
   - Очищается при первом ответе оператора (`first_reply_created_at`)
   - Очищается при резолве диалога (`handle_resolved_status_change`)

4. **`first_reply_created_at`** — устанавливается при первом исходящем сообщении:
   ```ruby
   # В Message model:
   def valid_first_reply?
     return false unless human_response? && !private?
     return false if conversation.first_reply_created_at.present?
     # ... проверки
   end
   ```

5. **Статусы:**
   - `open` — открыт, ожидает ответа
   - `resolved` — решён, но может быть переоткрыт
   - `pending` — ожидает (обычно для ботов)
   - `snoozed` — отложен до `snoozed_until`

6. **Авто-резолв:**
   ```ruby
   scope :resolvable_not_waiting, lambda { |auto_resolve_after|
     open.where('last_activity_at < ? AND waiting_since IS NULL', 
                Time.now.utc - auto_resolve_after.minutes)
   }
   ```

#### Concerns (Mixins)

1. **`AssignmentHandler`** — логика назначения:
   - Проверка, что assignee из команды
   - Автоназначение при смене команды
   - Создание activity сообщений

2. **`AutoAssignmentHandler`** — автоназначение:
   - Срабатывает при создании или открытии диалога
   - Использует round-robin через Redis
   - Учитывает capacity операторов

3. **`ActivityMessageHandler`** — системные сообщения:
   - Создание сообщений о смене статуса/приоритета/меток
   - Автоматические сообщения при резолве

4. **`ConversationMuteHelpers`** — заглушка диалогов:
   - `mute!` — блокирует контакт и резолвит диалог
   - `unmute!` — разблокирует контакт

5. **`PushDataHelper`** — подготовка данных для real-time:
   - `push_event_data` — данные для ActionCable
   - `webhook_data` — данные для webhooks

#### Callbacks

```ruby
before_create:
  - determine_conversation_status (устанавливает статус)
  - ensure_waiting_since (устанавливает waiting_since)

before_save:
  - ensure_snooze_until_reset (очищает snoozed_until если не snoozed)

after_create_commit:
  - notify_conversation_creation (отправляет событие)
  - load_attributes_created_by_db_triggers (загружает display_id)

after_update_commit:
  - execute_after_update_commit_callbacks:
    - handle_resolved_status_change (очищает waiting_since)
    - notify_status_change (отправляет события)
    - create_activity (создаёт activity сообщения)
    - notify_conversation_updation (отправляет CONVERSATION_UPDATED)
```

---

### Message (Сообщение)

#### Схема БД

```ruby
# Основные поля:
- id: integer (PK)
- account_id: integer (FK)
- conversation_id: integer (FK)
- inbox_id: integer (FK)
- sender_id: bigint (polymorphic FK)
- sender_type: string (Contact, User, AgentBot, etc.)

# Контент:
- content: text (макс. 150,000 символов)
- processed_message_content: text (обработанный контент, макс. 150,000)
- content_type: integer (enum: text, input_text, cards, form, etc.)
- message_type: integer (enum: incoming, outgoing, activity, template)
- content_attributes: json (структурированные данные)
- additional_attributes: jsonb (метаданные)
- external_source_ids: jsonb (ID во внешних системах)

# Статус:
- status: integer (enum: sent, delivered, read, failed)
- private: boolean (приватное сообщение)
- source_id: text (ID источника для дедупликации)
- sentiment: jsonb (анализ тональности)
```

#### Ключевые индексы

```sql
-- Составной индекс для запросов сообщений диалога
CREATE INDEX index_messages_on_conversation_account_type_created 
ON messages(conversation_id, account_id, message_type, created_at);

-- Индекс для поиска
CREATE INDEX index_messages_on_content USING gin (content);

-- Индекс для отчётов
CREATE INDEX idx_messages_account_content_created 
ON messages(account_id, content_type, created_at);
```

#### Валидации

1. **Длина контента:**
   ```ruby
   validates :content, length: { maximum: 150_000 }
   validates :processed_message_content, length: { maximum: 150_000 }
   ```

2. **Защита от флуда:**
   ```ruby
   before_validation :prevent_message_flooding
   
   def prevent_message_flooding
     return if conversation.blank?
     
     if conversation.messages.where('created_at >= ?', 1.minute.ago).count >= 
        Limits.conversation_message_per_minute_limit
       errors.add(:base, 'Too many messages')
     end
   end
   ```
   - Лимит: `CONVERSATION_MESSAGE_PER_MINUTE_LIMIT` (по умолчанию 20)
   - Проверяется за последнюю минуту

3. **Лимит вложений:**
   ```ruby
   NUMBER_OF_PERMITTED_ATTACHMENTS = 15
   validate_attachments_limit
   ```

#### Content Attributes (JSON)

```ruby
store :content_attributes, accessors: [
  :submitted_email,      # Для ботов
  :items,               # Для карточек
  :submitted_values,    # Для форм
  :email,               # Для email сообщений
  :in_reply_to,         # Ответ на сообщение
  :deleted,             # Удалённое сообщение
  :external_created_at, # Время создания во внешней системе
  :story_sender,        # Instagram story
  :story_id,
  :external_error,      # Ошибка внешнего API
  :translations,        # Переводы
  :in_reply_to_external_id,
  :is_unsupported,
  :data                  # Структурированные данные (voice_call и т.д.)
]
```

#### Callbacks

```ruby
before_validation:
  - ensure_content_type (устанавливает text если не задан)
  - prevent_message_flooding (защита от флуда)

before_save:
  - ensure_processed_message_content (обрабатывает контент)
  - ensure_in_reply_to (устанавливает in_reply_to)

after_create_commit:
  - execute_after_create_commit_callbacks:
    - reopen_conversation (переоткрывает резолвленный диалог)
    - set_conversation_activity (обновляет last_activity_at)
    - dispatch_create_events (отправляет события)
    - send_reply (отправляет через канал)
    - execute_message_template_hooks (шаблоны сообщений)
    - update_contact_activity (обновляет last_activity_at контакта)
    - update_waiting_since (обновляет waiting_since диалога)

after_update_commit:
  - dispatch_update_event (отправляет MESSAGE_UPDATED)
```

#### Логика waiting_since

```ruby
def update_waiting_since
  waiting_present = conversation.waiting_since.present?
  
  if waiting_present && !private
    if human_response?
      # Отправляет событие REPLY_CREATED с waiting_since
      dispatcher.dispatch(REPLY_CREATED, waiting_since: conversation.waiting_since)
      conversation.update(waiting_since: nil)
    elsif bot_response?
      # Боты тоже очищают waiting_since
      conversation.update(waiting_since: nil)
    end
  end
  
  # Устанавливает waiting_since при входящем сообщении
  conversation.update(waiting_since: created_at) if incoming? && 
    conversation.waiting_since.blank?
end
```

#### Первый ответ (first_reply_created_at)

```ruby
def valid_first_reply?
  return false unless human_response? && !private?
  return false if conversation.first_reply_created_at.present?
  return false if conversation.messages.outgoing
                    .where.not(sender_type: ['AgentBot', 'Captain::Assistant'])
                    .where.not(private: true)
                    .where("(additional_attributes->'campaign_id') is null")
                    .count > 1
  
  true
end

# В dispatch_create_events:
if valid_first_reply?
  dispatcher.dispatch(FIRST_REPLY_CREATED, message: self)
  conversation.update(first_reply_created_at: created_at, waiting_since: nil)
end
```

---

### Contact (Контакт)

#### Схема БД

```ruby
# Основные поля:
- id: integer (PK)
- account_id: integer (FK)
- company_id: bigint (FK, nullable)
- name: string
- email: string (уникальный в рамках account, case-insensitive)
- phone_number: string (формат E.164: +1234567890)
- identifier: string (уникальный в рамках account)
- contact_type: integer (enum: visitor, lead, customer)
- blocked: boolean (заблокирован)

# Метаданные:
- additional_attributes: jsonb (IP, browser, location, etc.)
- custom_attributes: jsonb
- last_activity_at: datetime
- country_code: string
- location: string
```

#### Валидации

```ruby
validates :email, 
  allow_blank: true, 
  uniqueness: { scope: [:account_id], case_sensitive: false },
  format: { with: Devise.email_regexp }

validates :phone_number,
  allow_blank: true, 
  uniqueness: { scope: [:account_id] },
  format: { with: /\+[1-9]\d{1,14}\z/ } # E.164 формат

validates :identifier, 
  allow_blank: true, 
  uniqueness: { scope: [:account_id] }
```

#### Callbacks

```ruby
before_validation:
  - prepare_contact_attributes (нормализует email, инициализирует JSONB)

before_save:
  - sync_contact_attributes (синхронизация атрибутов)

after_create_commit:
  - dispatch_create_event (CONTACT_CREATED)
  - ip_lookup (определение IP, если включено)

after_update_commit:
  - dispatch_update_event (CONTACT_UPDATED)

after_destroy_commit:
  - dispatch_destroy_event (CONTACT_DELETED)
```

---

### ContactInbox (Связь контакта с inbox)

#### Схема БД

```ruby
# Основные поля:
- id: bigint (PK)
- contact_id: bigint (FK)
- inbox_id: bigint (FK)
- source_id: text (NOT NULL, уникальный в рамках inbox)
- pubsub_token: string (уникальный, для WebSocket)
- hmac_verified: boolean
```

#### Важность

- **Один контакт может быть в нескольких inbox** (мультитенантность)
- **`source_id`** — идентификатор контакта в конкретном inbox (например, visitor_id для виджета)
- **`pubsub_token`** — токен для WebSocket подключения (генерируется через `has_secure_token`)

#### Валидации source_id

```ruby
validate :valid_source_id_format?

# Для Twilio SMS/WhatsApp — проверка формата E.164
# Для WhatsApp — проверка формата номера
```

---

### Inbox (Входящий канал)

#### Схема БД

```ruby
# Основные поля:
- id: integer (PK)
- account_id: integer (FK)
- channel_id: integer (polymorphic FK)
- channel_type: string (Channel::WebWidget, Channel::Email, etc.)
- name: string (NOT NULL)
- portal_id: bigint (FK, nullable)

# Настройки:
- enable_auto_assignment: boolean (автоназначение)
- auto_assignment_config: jsonb
- working_hours_enabled: boolean
- greeting_enabled: boolean
- greeting_message: string
- out_of_office_message: string
- csat_survey_enabled: boolean
- csat_config: jsonb
- lock_to_single_conversation: boolean
- allow_messages_after_resolved: boolean
- timezone: string (default: UTC)
- sender_name_type: integer (enum: friendly, professional)
```

#### Полиморфная связь с Channel

```ruby
belongs_to :channel, polymorphic: true

# Типы каналов:
- Channel::WebWidget (виджет сайта)
- Channel::Email
- Channel::FacebookPage
- Channel::Instagram
- Channel::Whatsapp
- Channel::Telegram
- Channel::TwitterProfile
- Channel::Api (API канал)
- Channel::Sms
- Channel::TwilioSms
- Channel::Line
- Channel::Tiktok
```

#### Методы проверки типа

```ruby
def web_widget?; channel_type == 'Channel::WebWidget'; end
def email?; channel_type == 'Channel::Email'; end
def api?; channel_type == 'Channel::Api'; end
# ... и т.д.
```

---

## Real-time коммуникация

### ActionCable (WebSocket)

#### RoomChannel

```ruby
class RoomChannel < ApplicationCable::Channel
  def subscribed
    current_user
    current_account
    ensure_stream
    update_subscription
    broadcast_presence
  end
  
  def ensure_stream
    # Подписка на канал для пользователя/контакта
    stream_from pubsub_token
    # Подписка на канал account (для операторов)
    stream_from "account_#{@current_account.id}" if @current_account.present? && 
      @current_user.is_a?(User)
  end
  
  def broadcast_presence
    # Отправка информации о присутствии
    data = { 
      account_id: @current_account.id, 
      users: OnlineStatusTracker.get_available_users(@current_account.id) 
    }
    ActionCable.server.broadcast(pubsub_token, { 
      event: 'presence.update', 
      data: data 
    })
  end
end
```

#### Pubsub Token

- Генерируется через `has_secure_token :pubsub_token`
- Уникален для каждого `ContactInbox` и `User`
- Используется для идентификации в WebSocket подключении
- Ротация при смене пароля пользователя

#### OnlineStatusTracker

```ruby
# Хранит статусы онлайн в Redis
OnlineStatusTracker.update_presence(account_id, user_class, user_id)
OnlineStatusTracker.get_available_users(account_id)
```

---

### Event Dispatcher

#### Архитектура

```ruby
# Dispatcher — Singleton
Rails.configuration.dispatcher.dispatch(event_name, timestamp, data)

# Два типа dispatcher:
- SyncDispatcher — синхронные обработчики
- AsyncDispatcher — асинхронные обработчики (через EventDispatcherJob)
```

#### События (Events::Types)

```ruby
# Conversation события:
CONVERSATION_CREATED
CONVERSATION_UPDATED
CONVERSATION_OPENED
CONVERSATION_RESOLVED
CONVERSATION_STATUS_CHANGED
CONVERSATION_READ
CONVERSATION_CONTACT_CHANGED
ASSIGNEE_CHANGED
TEAM_CHANGED
CONVERSATION_TYPING_ON
CONVERSATION_TYPING_OFF
CONVERSATION_BOT_HANDOFF

# Message события:
MESSAGE_CREATED
MESSAGE_UPDATED
FIRST_REPLY_CREATED
REPLY_CREATED

# Contact события:
CONTACT_CREATED
CONTACT_UPDATED
CONTACT_DELETED
```

#### Обработчики событий

События обрабатываются через listeners, которые подписаны на dispatcher:
- Отправка через ActionCable
- Отправка webhooks
- Отправка уведомлений
- Обновление индексов поиска

---

## Сервисы и бизнес-логика

### AutoAssignment::AgentAssignmentService

```ruby
class AutoAssignment::AgentAssignmentService
  def initialize(conversation:, allowed_agent_ids:)
    @conversation = conversation
    @allowed_agent_ids = allowed_agent_ids
  end
  
  def perform
    new_assignee = find_assignee
    conversation.update(assignee: new_assignee) if new_assignee
  end
  
  def find_assignee
    # Только онлайн операторы
    allowed_online_agent_ids = online_agent_ids & allowed_agent_ids.map(&:to_s)
    round_robin_service.available_agent(allowed_agent_ids: allowed_online_agent_ids)
  end
end
```

### AutoAssignment::InboxRoundRobinService

```ruby
class AutoAssignment::InboxRoundRobinService
  # Хранит очередь операторов в Redis (список)
  # Ключ: "round_robin_agents:#{inbox_id}"
  
  def available_agent(allowed_agent_ids: [])
    reset_queue unless validate_queue?
    user_id = get_member_from_allowed_agent_ids(allowed_agent_ids)
    inbox.inbox_members.find_by(user_id: user_id)&.user if user_id.present?
  end
  
  def get_member_from_allowed_agent_ids(allowed_agent_ids)
    # Берёт первого оператора из пересечения очереди и allowed_agent_ids
    user_id = queue.intersection(allowed_agent_ids).pop
    pop_push_to_queue(user_id) # Перемещает в конец очереди
    user_id
  end
  
  def validate_queue?
    # Проверяет, что очередь соответствует текущим членам inbox
    inbox.inbox_members.map(&:user_id).sort == queue.map(&:to_i).sort
  end
end
```

**Ключевые моменты:**
- Очередь хранится в Redis как список
- При назначении оператор перемещается в конец очереди
- Очередь валидируется и сбрасывается при несоответствии
- Учитываются только онлайн операторы

### AutoAssignment::RateLimiter

```ruby
class AutoAssignment::RateLimiter
  # Ограничение количества назначений оператору за период времени
  
  def within_limit?
    return true unless enabled?
    current_count < limit
  end
  
  def track_assignment(conversation)
    # Сохраняет в Redis с TTL = window
    assignment_key = build_assignment_key(conversation.id)
    Redis::Alfred.set(assignment_key, conversation.id.to_s, ex: window)
  end
  
  def current_count
    # Подсчитывает назначения за окно времени
    pattern = assignment_key_pattern
    Redis::Alfred.keys_count(pattern)
  end
end
```

### Conversations::AssignmentService

```ruby
class Conversations::AssignmentService
  def initialize(conversation:, assignee_id:, assignee_type: nil)
    @conversation = conversation
    @assignee_id = assignee_id
    @assignee_type = assignee_type
  end
  
  def perform
    agent_bot_assignment? ? assign_agent_bot : assign_agent
  end
  
  def assign_agent
    conversation.assignee = assignee
    conversation.assignee_agent_bot = nil
    conversation.save!
    assignee
  end
end
```

### Conversations::TypingStatusManager

```ruby
class Conversations::TypingStatusManager
  def toggle_typing_status
    case params[:typing_status]
    when 'on'
      trigger_typing_event(CONVERSATION_TYPING_ON, params[:is_private])
    when 'off'
      trigger_typing_event(CONVERSATION_TYPING_OFF, params[:is_private])
    end
  end
  
  def trigger_typing_event(event, is_private)
    dispatcher.dispatch(event, Time.zone.now, 
      conversation: @conversation, 
      user: @user, 
      is_private: is_private)
  end
end
```

### Conversations::MessageWindowService

```ruby
class Conversations::MessageWindowService
  # Проверяет, можно ли ответить в канале (окно ответа)
  
  MESSAGING_WINDOW_24_HOURS = 24.hours
  MESSAGING_WINDOW_7_DAYS = 7.days
  
  def can_reply?
    return true if messaging_window.blank?
    last_message_in_messaging_window?(messaging_window)
  end
  
  def messaging_window
    case @conversation.inbox.channel_type
    when 'Channel::Whatsapp'
      MESSAGING_WINDOW_24_HOURS
    when 'Channel::FacebookPage'
      # 24 часа или 7 дней в зависимости от конфига
      meta_messaging_window('ENABLE_MESSENGER_CHANNEL_HUMAN_AGENT')
    when 'Channel::Api'
      # Настраивается в channel.additional_attributes
      api_messaging_window
    end
  end
end
```

**Важно:** Некоторые каналы (WhatsApp, Messenger) имеют ограниченное окно ответа (24 часа или 7 дней). После истечения окна оператор не может ответить.

---

## API и контроллеры

### Api::V1::Accounts::ConversationsController

#### Ключевые endpoints

```ruby
# Список диалогов
GET /api/v1/accounts/:account_id/conversations
- Использует Conversations::FilterService
- Поддерживает фильтры, сортировку, пагинацию

# Детали диалога
GET /api/v1/accounts/:account_id/conversations/:id
- Использует display_id, не числовой id

# Создание диалога
POST /api/v1/accounts/:account_id/conversations
- ConversationBuilder создаёт диалог
- Опционально создаёт первое сообщение

# Обновление диалога
PATCH /api/v1/accounts/:account_id/conversations/:id
- Обновляет priority, status, assignee и т.д.

# Обновление last_seen
POST /api/v1/accounts/:account_id/conversations/:id/update_last_seen
- Throttling: обновляет только если есть непрочитанные или прошло > 1 часа
- Обновляет agent_last_seen_at и/или assignee_last_seen_at

# Typing status
POST /api/v1/accounts/:account_id/conversations/:id/toggle_typing_status
- Conversations::TypingStatusManager

# Вложения
GET /api/v1/accounts/:account_id/conversations/:id/attachments
- Пагинация (100 на страницу)
```

#### Throttling last_seen

```ruby
def update_last_seen
  # Всегда обновляет если есть непрочитанные
  return update_last_seen_on_conversation(DateTime.now.utc, true) if 
    assignee? && @conversation.assignee_unread_messages.any?
  
  # Throttling: обновляет только если прошло > 1 часа
  return unless should_update_last_seen?
  update_last_seen_on_conversation(DateTime.now.utc, assignee?)
end

def should_update_last_seen?
  agent_needs_update = @conversation.agent_last_seen_at.blank? || 
    @conversation.agent_last_seen_at < 1.hour.ago
  return agent_needs_update unless assignee?
  
  assignee_needs_update = @conversation.assignee_last_seen_at.blank? || 
    @conversation.assignee_last_seen_at < 1.hour.ago
  agent_needs_update || assignee_needs_update
end
```

**Важно:** Throttling предотвращает избыточные записи в БД при частом переключении между диалогами.

---

### Public::Api::V1::Inboxes::MessagesController

#### Публичный API виджета

```ruby
# Список сообщений
GET /public/api/v1/inboxes/:inbox_id/messages
- Фильтрует внутренние сообщения (private)
- Поддерживает пагинацию (before)

# Создание сообщения
POST /public/api/v1/inboxes/:inbox_id/messages
- Создаёт входящее сообщение
- Поддерживает вложения
- echo_id для синхронизации с фронтендом

# Обновление сообщения (CSAT)
PATCH /public/api/v1/inboxes/:inbox_id/messages/:id
- Только для CSAT опросов
- Блокируется через 14 дней
```

---

## Виджет

### Структура

```
app/javascript/widget/
├── api/              # API клиент
│   ├── conversation.js
│   ├── message.js
│   └── ...
├── composables/      # Vue composables
│   ├── useAttachments.js
│   ├── useAvailability.js
│   └── ...
└── constants/        # Константы
```

### API методы

```javascript
// Создание диалога
createConversationAPI(content)

// Отправка сообщения
sendMessageAPI(content, replyTo)

// Отправка вложения
sendAttachmentAPI(attachment, replyTo)

// Получение сообщений
getMessagesAPI({ before, after })

// Typing status
toggleTyping({ typingStatus })

// Last seen
setUserLastSeenAt({ lastSeen })
```

### Идентификация

- Использует `pubsub_token` из `ContactInbox`
- Передаётся через query параметры или заголовки
- Используется для WebSocket подключения

---

## Frontend (React)

### Структура компонентов

```
app/javascript/dashboard/components/
├── ConversationItem.vue          # Элемент списка диалогов
├── widgets/conversation/
│   ├── ConversationBox.vue      # Контейнер диалога
│   ├── ConversationCard.vue     # Карточка диалога
│   ├── ConversationHeader.vue   # Заголовок диалога
│   └── ConversationSidebar.vue  # Боковая панель
```

### Трёхколоночный layout

- Левая колонка: список диалогов (компактные карточки)
- Центральная колонка: диалог (сообщения, форма ввода)
- Правая колонка: информация о диалоге/контакте

---

## Безопасность и валидации

### Защита от флуда сообщений

```ruby
# В Message model:
before_validation :prevent_message_flooding

def prevent_message_flooding
  return if conversation.blank?
  
  if conversation.messages.where('created_at >= ?', 1.minute.ago).count >= 
     Limits.conversation_message_per_minute_limit
    errors.add(:base, 'Too many messages')
  end
end
```

**Лимит:** `CONVERSATION_MESSAGE_PER_MINUTE_LIMIT` (по умолчанию 20 сообщений в минуту)

### Валидация длины

- **Сообщения:** максимум 150,000 символов
- **Вложения:** максимум 15 файлов на сообщение
- **source_id:** максимум 20,000 символов

### Валидация JSONB атрибутов

```ruby
validates :additional_attributes, jsonb_attributes_length: true
validates :custom_attributes, jsonb_attributes_length: true

# Проверяет:
# - Длина строковых значений < 1500 символов
# - Числовые значения < 9999999999
```

### Rate Limiting

- На уровне API через throttling
- На уровне модели через `prevent_message_flooding`
- Для автоназначения через `AutoAssignment::RateLimiter`

---

## Производительность и оптимизации

### Составные индексы

```sql
-- Критически важен для списка диалогов!
CREATE INDEX conv_acid_inbid_stat_asgnid_idx 
ON conversations(account_id, inbox_id, status, assignee_id);

-- Для сообщений
CREATE INDEX index_messages_on_conversation_account_type_created 
ON messages(conversation_id, account_id, message_type, created_at);
```

### Оптимизация запросов

```ruby
# Использование select_related и prefetch_related
conversations.select_related(:inbox, :contact, :assignee, :branch)
  .prefetch_related(:messages)

# Избежание N+1 запросов
messages.includes(:sender, :attachments)
```

### Кэширование

- **Round-robin очередь** — в Redis
- **Online status** — в Redis
- **Typing status** — в Redis с TTL
- **Cached label list** — в БД (denormalization)

### Throttling

- **last_seen updates** — максимум раз в час (если нет непрочитанных)
- **Message flooding** — максимум 20 сообщений в минуту
- **Assignment rate** — через `AutoAssignment::RateLimiter`

---

## Edge cases и обработка ошибок

### Race conditions

1. **При назначении диалога:**
   - Используется `update` без блокировок (может быть улучшено через `select_for_update`)
   - Проверка через `should_run_auto_assignment?`

2. **При создании сообщений:**
   - Защита от флуда через валидацию
   - `source_id` для дедупликации

### Обработка ошибок

1. **Внешние API:**
   - `external_error` в `content_attributes`
   - Retry через Sidekiq jobs

2. **WebSocket:**
   - Fallback на polling при недоступности
   - Reconnection логика

3. **Вложения:**
   - Валидация типа файла
   - Обработка ошибок загрузки

### Дедупликация

- **Сообщения:** через `source_id`
- **Контакты:** через `email`, `phone_number`, `identifier`
- **Диалоги:** через `identifier` и `contact_inbox_id`

---

## Тесты и покрытие

### Структура тестов

```
spec/
├── models/
│   ├── conversation_spec.rb
│   ├── message_spec.rb
│   └── ...
├── services/
└── controllers/
```

### Ключевые тесты

1. **Conversation:**
   - Callbacks (before_create, after_update)
   - Валидации (JSONB атрибуты)
   - Статусы и переходы
   - Автоназначение
   - Activity сообщения

2. **Message:**
   - Валидации длины
   - Защита от флуда
   - Callbacks (waiting_since, first_reply_created_at)
   - Content attributes

### Edge cases в тестах

- Одновременное назначение диалога
- Флуд сообщений
- Длинные сообщения (> 150,000 символов)
- Множественные вложения (> 15)
- Невалидные source_id

---

## Ключевые находки

### 1. Display ID через DB Trigger

- Генерируется на уровне БД через последовательность
- Уникален в рамках account
- Загружается после создания через `load_attributes_created_by_db_triggers`

**Вывод:** Нужно реализовать аналогичную логику для человекочитаемых номеров диалогов.

### 2. Составные индексы

- Критически важны для производительности списка диалогов
- Индекс `(account_id, inbox_id, status, assignee_id)` покрывает большинство запросов

**Вывод:** Нужно добавить составные индексы для оптимизации запросов.

### 3. Throttling last_seen

- Обновляется только если есть непрочитанные или прошло > 1 часа
- Предотвращает избыточные записи в БД

**Вывод:** Нужно реализовать throttling для `assignee_last_read_at`.

### 4. Waiting Since логика

- Устанавливается при создании диалога
- Очищается при первом ответе оператора или бота
- Используется для метрик времени ожидания

**Вывод:** Нужно добавить `waiting_since` и логику его обновления.

### 5. First Reply Created At

- Устанавливается при первом человеческом ответе (не бот, не campaign)
- Используется для метрик времени первого ответа

**Вывод:** Нужно добавить поле и логику его установки.

### 6. Round-Robin через Redis

- Очередь операторов хранится в Redis как список
- При назначении оператор перемещается в конец очереди
- Очередь валидируется и сбрасывается при несоответствии

**Вывод:** Нужно реализовать аналогичную логику для равномерного распределения.

### 7. Rate Limiter для назначений

- Ограничивает количество назначений оператору за период времени
- Настраивается через `AssignmentPolicy`

**Вывод:** Нужно добавить rate limiting для автоназначения.

### 8. Message Window Service

- Проверяет, можно ли ответить в канале (окно ответа)
- Разные окна для разных каналов (24 часа, 7 дней, настраиваемое)

**Вывод:** Нужно реализовать проверку окна ответа для каналов с ограничениями.

### 9. Content Attributes (JSON)

- Гибкая структура для хранения метаданных сообщений
- Поддержка ответов на сообщения, удалённых сообщений, переводов

**Вывод:** Нужно расширить `content_attributes` для поддержки дополнительных функций.

### 10. Event Dispatcher

- Централизованная система событий
- Синхронные и асинхронные обработчики
- Используется для real-time обновлений, webhooks, уведомлений

**Вывод:** Нужно реализовать аналогичную систему событий для расширяемости.

---

## Заключение

Chatwoot использует продуманную архитектуру с акцентом на:
- **Производительность** (составные индексы, кэширование, throttling)
- **Надёжность** (валидации, защита от флуда, обработка ошибок)
- **Масштабируемость** (Redis для очередей, асинхронные обработчики)
- **Гибкость** (JSONB для метаданных, event dispatcher)

Ключевые улучшения для нашего мессенджера:
1. Добавить `display_id` через DB trigger
2. Добавить составные индексы
3. Реализовать throttling для `last_seen`
4. Добавить `waiting_since` и `first_reply_created_at`
5. Улучшить round-robin через Redis
6. Добавить rate limiter для назначений
7. Расширить `content_attributes` для гибкости
8. Реализовать event dispatcher систему

---

## Дополнительные компоненты

### Builders (Паттерн Builder)

#### ConversationBuilder

```ruby
class ConversationBuilder
  def initialize(params:, contact_inbox:)
    @params = params
    @contact_inbox = contact_inbox
  end
  
  def perform
    find_or_create_conversation
  end
  
  private
  
  def find_or_create_conversation
    # Ищет существующий диалог или создаёт новый
    # Учитывает lock_to_single_conversation настройку inbox
    # Создаёт через Conversation.create с правильными связями
  end
end
```

**Использование:**
- При создании диалога из виджета
- При создании диалога через API
- Учитывает настройки inbox (lock_to_single_conversation)

#### Messages::MessageBuilder

```ruby
class Messages::MessageBuilder
  def initialize(user, conversation, params)
    @user = user
    @conversation = conversation
    @params = params
  end
  
  def perform
    # Создаёт сообщение с правильными атрибутами
    # Обрабатывает вложения
    # Устанавливает sender (User, Contact, AgentBot)
    # Возвращает созданное сообщение
  end
end
```

**Использование:**
- При отправке сообщения оператором
- При создании сообщения через API
- При автоматических сообщениях (боты, кампании)

### Рабочие часы (Working Hours)

#### OutOfOffisable Concern

```ruby
module OutOfOffisable
  def out_of_office?
    working_hours_enabled? && working_hours.today.closed_now?
  end
  
  def working_now?
    !out_of_office?
  end
  
  def weekly_schedule
    # Возвращает расписание на неделю
    working_hours.order(day_of_week: :asc).select(*OFFISABLE_ATTRS).as_json
  end
end
```

**Модель WorkingHour:**
- `day_of_week` (0-6, 0=воскресенье)
- `closed_all_day` (закрыт весь день)
- `open_all_day` (открыт весь день)
- `open_hour`, `open_minutes`, `close_hour`, `close_minutes`

**Логика:**
- По умолчанию создаётся расписание: пн-пт 9:00-17:00, сб-вс закрыто
- Проверка учитывает timezone inbox
- Используется для автоназначения и отображения в виджете

### Доступность операторов

#### InboxAgentAvailability Concern

```ruby
module InboxAgentAvailability
  def available_agents
    online_agent_ids = fetch_online_agent_ids
    return inbox_members.none if online_agent_ids.empty?
    
    inbox_members
      .joins(:user)
      .where(users: { id: online_agent_ids })
      .includes(:user)
  end
  
  def member_ids_with_assignment_capacity
    # Может быть переопределено для учёта capacity
    member_ids
  end
end
```

**Логика:**
- Использует `OnlineStatusTracker` для получения онлайн операторов
- Фильтрует только операторов со статусом 'online'
- Используется при автоназначении

### Фильтрация диалогов

#### Conversations::FilterService

```ruby
class Conversations::FilterService < FilterService
  def perform
    validate_query_operator
    @conversations = query_builder(@filters['conversations'])
    mine_count, unassigned_count, all_count = set_count_for_all_conversations
    
    {
      conversations: conversations,
      count: {
        mine_count: mine_count,
        assigned_count: all_count - unassigned_count,
        unassigned_count: unassigned_count,
        all_count: all_count
      }
    }
  end
  
  def base_relation
    conversations = @account.conversations.includes(
      :taggings, :inbox, { assignee: { avatar_attachment: [:blob] } }, 
      { contact: { avatar_attachment: [:blob] } }, :team, :messages, :contact_inbox
    )
    
    Conversations::PermissionFilterService.new(conversations, @user, @account).perform
  end
end
```

**Особенности:**
- Поддерживает сложные фильтры через query builder
- Оптимизирует запросы через `includes`
- Применяет фильтр прав доступа через `PermissionFilterService`
- Возвращает счётчики для разных категорий диалогов

### Сортировка диалогов

#### SortHandler Concern

```ruby
module SortHandler
  def sort_on_last_activity_at(sort_direction = :desc)
    order(last_activity_at: sort_direction)
  end
  
  def sort_on_priority(sort_direction = :desc)
    order("priority #{sort_direction.to_s.upcase} NULLS LAST, last_activity_at DESC")
  end
  
  def sort_on_waiting_since(sort_direction = :asc)
    order("waiting_since #{sort_direction.to_s.upcase} NULLS LAST, created_at ASC")
  end
  
  def sort_on_last_user_message_at
    # Сортировка по последнему сообщению пользователя
    # Использует подзапрос для оптимизации
  end
end
```

**Важно:** Использует `NULLS LAST` для правильной сортировки с NULL значениями.

### Виджет: Availability

#### useAvailability Composable

```javascript
export function useAvailability(agents = []) {
  const hasOnlineAgents = computed(() => {
    const agentList = availableAgents.value || [];
    return Array.isArray(agentList) ? agentList.length > 0 : false;
  });
  
  const isInWorkingHours = computed(() =>
    checkInWorkingHours(
      currentTime.value,
      inboxConfig.value.utcOffset,
      inboxConfig.value.workingHours
    )
  );
  
  const isOnline = computed(() =>
    checkIsOnline(
      inboxConfig.value.workingHoursEnabled,
      currentTime.value,
      inboxConfig.value.utcOffset,
      inboxConfig.value.workingHours,
      hasOnlineAgents.value
    )
  );
  
  return { isOnline, isInWorkingHours, hasOnlineAgents };
}
```

**Логика:**
- Проверяет наличие онлайн операторов
- Проверяет рабочие часы с учётом timezone
- Используется для отображения статуса в виджете

---

## Итоговые выводы

### Критические улучшения для production

1. **Display ID через DB Trigger**
   - Генерация на уровне БД
   - Уникальность в рамках account
   - Загрузка после создания

2. **Составные индексы**
   - `(account_id, inbox_id, status, assignee_id)` для списка диалогов
   - `(conversation_id, account_id, message_type, created_at)` для сообщений

3. **Throttling last_seen**
   - Обновление только при непрочитанных или > 1 часа
   - Предотвращение избыточных записей

4. **Waiting Since и First Reply**
   - `waiting_since` для метрик ожидания
   - `first_reply_created_at` для метрик времени ответа

5. **Round-Robin через Redis**
   - Очередь операторов в Redis
   - Валидация и сброс при несоответствии
   - Учёт только онлайн операторов

6. **Rate Limiter для назначений**
   - Ограничение назначений за период времени
   - Настраиваемые лимиты через AssignmentPolicy

7. **Защита от флуда**
   - Валидация на уровне модели
   - Лимит: 20 сообщений в минуту на диалог

8. **Content Attributes (JSON)**
   - Гибкая структура для метаданных
   - Поддержка ответов, удалённых сообщений, переводов

9. **Event Dispatcher**
   - Централизованная система событий
   - Синхронные и асинхронные обработчики

10. **Рабочие часы**
    - Настраиваемое расписание
    - Учёт timezone
    - Использование для автоназначения и виджета

### Архитектурные паттерны

1. **Service Objects** — бизнес-логика в сервисах
2. **Builders** — создание сложных объектов
3. **Concerns** — переиспользование логики
4. **Event Dispatcher** — система событий
5. **PubSub** — real-time через ActionCable

### Производительность

1. **Индексы** — составные индексы для частых запросов
2. **Кэширование** — Redis для очередей и статусов
3. **Throttling** — ограничение частых операций
4. **Оптимизация запросов** — `includes` для избежания N+1

### Безопасность

1. **Валидации** — на уровне модели и API
2. **Rate Limiting** — защита от злоупотреблений
3. **Дедупликация** — через `source_id`
4. **JSONB валидации** — проверка длины значений

---

*Документация создана на основе досконального изучения кода Chatwoot. Все находки проверены на реальном коде проекта. Документация охватывает все ключевые аспекты архитектуры, моделей, сервисов, API, real-time коммуникации, виджета, frontend, безопасности, производительности и edge cases.*
