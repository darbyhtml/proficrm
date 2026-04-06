---
tags: [архитектура, api]
---

# REST API

DRF 3.16.1 + drf-spectacular (OpenAPI). Документация: `/api/schema/`

## Эндпоинты по модулям

### Companies API (`companies/api.py`)
- `CompanyViewSet` — CRUD компаний + фильтрация
- `ContactViewSet` — управление контактами
- `CompanyNoteViewSet` — заметки

### Messenger API (`messenger/api.py` — 1,033 LOC)
- `ConversationViewSet` — диалоги (list/detail/create/assign/resolve)
  - `stream` — SSE стрим per-conversation (30с)
  - `notifications/stream` — глобальный SSE стрим (55с)
  - `typing` — статус печати
  - `mark-read` — отметить прочитанным
  - `merge-contacts` — объединение контактов (admin only)
- `CannedResponseViewSet` — шаблоны ответов
- `ConversationLabelViewSet` — метки
- `PushSubscriptionViewSet` — push-подписки
- `CampaignViewSet` — кампании
- `AutomationRuleViewSet` — автоматизация
- `ReportingViewSet` — метрики
- `MacroViewSet` — горячие клавиши

### Widget API (`messenger/widget_api.py`)
> Отдельный API для виджета (без Django auth, token-based)

| Эндпоинт | Метод | Назначение |
|----------|-------|-----------|
| `/api/widget/bootstrap/` | POST | Инициализация сессии |
| `/api/widget/poll/` | GET | Polling новых сообщений |
| `/api/widget/stream/` | GET | SSE стрим (25с) |
| `/api/widget/send/` | POST | Отправка сообщения |
| `/api/widget/mark-read/` | POST | Отметить прочитанным |
| `/api/widget/typing/` | POST | Статус печати |
| `/api/widget/campaigns/` | GET | Список кампаний |
| `/api/widget/rate/` | POST | Оценка диалога |
| `/api/widget/attachment/` | GET | Скачивание файла |
| `/api/widget/contact/update/` | POST | Обновление профиля |

### TasksApp API (`tasksapp/api.py`)
- `TaskViewSet` — CRUD задач, статусы
- `TaskTypeViewSet` — типы задач

### PhoneBridge API (`phonebridge/api.py` — 965 LOC)
- Регистрация устройств, QR-спаривание
- Pull вызовов, обновление статусов
- Heartbeat, телеметрия, логи

## Аутентификация

| Контекст | Метод |
|---------|-------|
| Web-интерфейс | Django session |
| API | JWT (simplejwt) |
| Widget | widget_token + widget_session_token |
| Magic Link | Одноразовый SHA256 токен |

## CORS

- `django-cors-headers` для основного API
- Widget API: собственный `_add_widget_cors_headers()` (не django-cors-headers)
- Nginx: OPTIONS preflight для widget
- Django: CORS заголовки на ответы

---

Связано: [[Стек технологий]] · [[Мессенджер]] · [[Nginx]]
