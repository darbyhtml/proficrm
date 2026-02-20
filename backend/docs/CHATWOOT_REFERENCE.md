# Chatwoot как референс для улучшения мессенджера

## Что сделано

1. ✅ Скачан код Chatwoot в папку `chatwoot-reference/` (в корне проекта)
2. ✅ Папка добавлена в `.gitignore` — не будет пушиться в репозиторий
3. ✅ Создан план изучения (`CHATWOOT_STUDY_PLAN.md`)
4. ✅ Создано сравнение с нашим мессенджером (`CHATWOOT_COMPARISON.md`)

## Структура Chatwoot

```
chatwoot-reference/
├── app/
│   ├── models/              # Модели (Conversation, Message, Contact, Inbox)
│   ├── controllers/         # API контроллеры
│   ├── channels/            # WebSocket (ActionCable) для real-time
│   ├── services/            # Бизнес-логика
│   └── javascript/          # Frontend (React)
│       ├── dashboard/       # Операторская панель
│       └── widget/          # Виджет для сайта
└── db/migrate/             # Миграции БД
```

## Как использовать

### 1. Изучение моделей

```bash
# Посмотреть модель Conversation
cat chatwoot-reference/app/models/conversation.rb | less

# Посмотреть модель Message
cat chatwoot-reference/app/models/message.rb | less

# Найти все валидации
grep -r "validates" chatwoot-reference/app/models/conversation.rb
```

### 2. Изучение API

```bash
# Посмотреть контроллер диалогов
cat chatwoot-reference/app/controllers/api/v1/accounts/conversations_controller.rb | less

# Посмотреть публичный API виджета
find chatwoot-reference/app/controllers/public/api/v1/widgets -name "*.rb"
```

### 3. Изучение Frontend

```bash
# Структура React компонентов
ls -la chatwoot-reference/app/javascript/dashboard/components/Conversation/

# Виджет
ls -la chatwoot-reference/app/javascript/widget/
```

### 4. Поиск конкретной функциональности

```bash
cd chatwoot-reference

# Найти все упоминания typing indicator
grep -r "typing" app/models app/controllers app/services

# Найти обработку race conditions
grep -r "select_for_update\|lock" app/services

# Найти валидации флуда
grep -r "flood\|rate.*limit" app/models app/controllers
```

## Ключевые находки

См. `CHATWOOT_COMPARISON.md` для детального сравнения.

### Критические улучшения:

1. **Защита от флуда** — валидация на уровне модели
2. **Race conditions** — использование `select_for_update()` при назначении
3. **Составные индексы** — для производительности запросов
4. **`display_id`** — человекочитаемый номер диалога
5. **`waiting_since`** и **`first_reply_created_at`** — для метрик

## План действий

См. `CHATWOOT_STUDY_PLAN.md` для детального плана изучения.

### Быстрый старт:

1. Прочитать `CHATWOOT_COMPARISON.md` — понять различия
2. Изучить модели Chatwoot — найти edge cases
3. Изучить валидации — понять защиту от ошибок
4. Применить находки в нашем коде

## Важно

- Chatwoot на Ruby on Rails, мы на Django — изучаем **концепции**, не копируем код
- Chatwoot использует React, мы используем vanilla JS — изучаем **логику**, не компоненты
- Фокус на **как решены проблемы**, а не на конкретной реализации

## Полезные ссылки

- [Chatwoot GitHub](https://github.com/chatwoot/chatwoot)
- [Chatwoot Documentation](https://www.chatwoot.com/help-center)
- [Chatwoot API Docs](https://www.chatwoot.com/developers/api)

---

*Используйте Chatwoot как референс для улучшения нашего мессенджера до production-ready состояния.*
