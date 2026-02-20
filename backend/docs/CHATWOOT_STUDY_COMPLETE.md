# Изучение Chatwoot завершено

**Дата:** 2026-02-20  
**Статус:** ✅ Завершено

## Что было изучено

### 1. Модели данных (100%)
- ✅ Conversation (диалог) — полная схема, индексы, callbacks, concerns
- ✅ Message (сообщение) — валидации, защита от флуда, content_attributes
- ✅ Contact (контакт) — идентификация, валидации, метаданные
- ✅ ContactInbox (связь контакта с inbox) — pubsub_token, source_id
- ✅ Inbox (входящий канал) — полиморфная связь с Channel, настройки
- ✅ Attachment (вложение) — типы файлов, метаданные

### 2. Real-time коммуникация (100%)
- ✅ ActionCable (WebSocket) — RoomChannel, pubsub_token
- ✅ Event Dispatcher — синхронные и асинхронные обработчики
- ✅ OnlineStatusTracker — статусы онлайн в Redis
- ✅ Typing Status — индикатор "печатает"

### 3. Сервисы и бизнес-логика (100%)
- ✅ AutoAssignment::AgentAssignmentService — автоназначение
- ✅ AutoAssignment::InboxRoundRobinService — round-robin через Redis
- ✅ AutoAssignment::RateLimiter — ограничение назначений
- ✅ Conversations::AssignmentService — ручное назначение
- ✅ Conversations::TypingStatusManager — управление typing
- ✅ Conversations::MessageWindowService — окно ответа для каналов
- ✅ Conversations::FilterService — фильтрация диалогов
- ✅ ConversationBuilder — создание диалогов
- ✅ Messages::MessageBuilder — создание сообщений

### 4. API и контроллеры (100%)
- ✅ Api::V1::Accounts::ConversationsController — операторский API
- ✅ Public::Api::V1::Inboxes::MessagesController — публичный API виджета
- ✅ Throttling last_seen — оптимизация обновлений
- ✅ Endpoints для typing, attachments, transcript

### 5. Виджет (100%)
- ✅ Структура виджета (Vue.js)
- ✅ API методы (conversation, message, typing)
- ✅ Composables (useAvailability, useAttachments)
- ✅ Идентификация через pubsub_token

### 6. Frontend (React) (100%)
- ✅ Структура компонентов
- ✅ Трёхколоночный layout
- ✅ ConversationItem, ConversationBox, ConversationCard

### 7. Безопасность и валидации (100%)
- ✅ Защита от флуда сообщений (20/мин)
- ✅ Валидация длины (150,000 символов)
- ✅ Лимит вложений (15 файлов)
- ✅ JSONB валидации
- ✅ Rate limiting

### 8. Производительность (100%)
- ✅ Составные индексы
- ✅ Оптимизация запросов (includes, prefetch)
- ✅ Кэширование в Redis
- ✅ Throttling операций

### 9. Edge cases (100%)
- ✅ Race conditions при назначении
- ✅ Дедупликация через source_id
- ✅ Обработка ошибок внешних API
- ✅ WebSocket fallback на polling

### 10. Тесты (100%)
- ✅ Структура тестов (RSpec)
- ✅ Тесты моделей (conversation, message)
- ✅ Edge cases в тестах

### 11. Дополнительные компоненты (100%)
- ✅ Рабочие часы (OutOfOffisable)
- ✅ Доступность операторов (InboxAgentAvailability)
- ✅ Сортировка (SortHandler)
- ✅ Builders (ConversationBuilder, MessageBuilder)

## Документация

Созданы следующие документы:

1. **CHATWOOT_FULL_ANALYSIS.md** — полный анализ архитектуры и реализации (1000+ строк)
2. **CHATWOOT_COMPARISON.md** — сравнение с нашим мессенджером
3. **CHATWOOT_STUDY_PLAN.md** — план изучения
4. **CHATWOOT_REFERENCE.md** — краткая инструкция по использованию

## Ключевые находки

### Критические улучшения для production:

1. ✅ Display ID через DB Trigger
2. ✅ Составные индексы для производительности
3. ✅ Throttling last_seen обновлений
4. ✅ Waiting Since и First Reply Created At
5. ✅ Round-Robin через Redis
6. ✅ Rate Limiter для назначений
7. ✅ Защита от флуда сообщений
8. ✅ Content Attributes (JSON) для гибкости
9. ✅ Event Dispatcher система
10. ✅ Рабочие часы с учётом timezone

### Архитектурные паттерны:

1. ✅ Service Objects
2. ✅ Builders
3. ✅ Concerns (Mixins)
4. ✅ Event Dispatcher
5. ✅ PubSub через ActionCable

### Производительность:

1. ✅ Составные индексы
2. ✅ Кэширование в Redis
3. ✅ Throttling частых операций
4. ✅ Оптимизация запросов (includes)

### Безопасность:

1. ✅ Валидации на уровне модели
2. ✅ Rate Limiting
3. ✅ Дедупликация
4. ✅ JSONB валидации

## Статистика изучения

- **Файлов Ruby изучено:** 50+
- **Моделей изучено:** 6 основных
- **Сервисов изучено:** 10+
- **Контроллеров изучено:** 5+
- **Concerns изучено:** 15+
- **Тестов изучено:** 10+
- **Строк документации:** 1000+

## Готовность к следующему этапу

✅ **Документация готова, готов переходить к доскональному сравнению реализации chatwoot и messenger в моей CRM**

Все ключевые аспекты архитектуры Chatwoot изучены и задокументированы. Можно переходить к сравнению с нашей реализацией и составлению плана доведения live-chat до production-ready состояния.

---

*Изучение завершено: 2026-02-20*
