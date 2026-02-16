# Messenger (встроенный чат/виджет) — обзор и локальная разработка

Этот модуль реализует внутренний «мессенджер» CRM (операторская панель диалогов и веб-виджет).

## Feature-флаг

- Включение/выключение через переменную окружения `MESSENGER_ENABLED` (по умолчанию выключен):
  - `MESSENGER_ENABLED=1` — функциональность включена;
  - `MESSENGER_ENABLED=0` — API/UI/виджет отвечают `404` / `{"detail": "Messenger disabled"}`, но маршруты остаются зарегистрированными.
- Проверка флага централизована в `messenger/utils.py` (`ensure_messenger_enabled_api`, `ensure_messenger_enabled_view`).

## База данных и миграции (важно)

Весь проект CRM рассчитан на работу с PostgreSQL. Некоторые старые миграции (`accounts`, `companies`)
используют PostgreSQL-специфичный SQL и **не поддерживаются под SQLite**.

**Рекомендации для разработки и тестов messenger:**  

- Использовать только PostgreSQL через Docker Compose:
  - `docker compose up -d db redis web` (или соответствующую команду из корневого README проекта);
  - выполнять миграции и тесты внутри контейнера `web`.
- Не запускать `python manage.py migrate`/`pytest` на SQLite — часть миграций может падать с ошибками
  вида `sqlite3.OperationalError: near "INDEX": syntax error`, что не отражает состояние боевой БД.

Все миграции приложения `messenger` совместимы с PostgreSQL и интегрируются в общий пайплайн миграций проекта.

## Inbox.branch и неизменяемость филиалов

- При создании `Inbox` филиал (`branch`) задаётся один раз и **не может быть изменён** позже.
- Это зафиксировано в `messenger/models.py` (метод `Inbox.clean`):
  - попытка изменить `branch` у существующего `Inbox` приводит к `ValidationError`.
- Причина: изменение филиала у Inbox может "перетаскивать" связанные диалоги (`Conversation`) в другой филиал
  и ломать модель безопасности видимости по `branch`.

Аналогично, у `Conversation`:

- `branch` всегда автоматически выставляется из `inbox.branch` и не редактируется вручную;
- `inbox` неизменяем после создания диалога.

Это гарантирует, что филиал диалога фиксируется навсегда, а фильтрация по `branch` в селекторах остаётся корректной.

## Widget session token (безопасность публичного API)

Для публичных эндпоинтов виджета (`/api/widget/bootstrap/`, `/api/widget/send/`, `/api/widget/poll/`)
используется отдельный `widget_session_token`:

- `/api/widget/bootstrap/`:
  - принимает `widget_token` (из `Inbox.widget_token`) и данные посетителя;
  - создаёт/находит `Contact` и `Conversation`;
  - генерирует `widget_session_token` через `create_widget_session(...)` (`messenger/utils.py`) и возвращает его в ответе.
- `/api/widget/send/` и `/api/widget/poll/`:
  - требуют одновременную передачу `widget_token` и `widget_session_token`;
  - валидируют токен через `get_widget_session(...)` и используют сохранённый контекст (inbox_id, conversation_id, contact_id).

Дополнительно публичный API виджета должен реализовывать:

- отдельные throttles (rate limit по IP и по inbox/widget_token);
- honeypot-поле в форме отправки;
- ограничение длины текста сообщения (валидация на уровне serializer/view).

Реализация эндпоинтов виджета и throttle-классов выполняется на отдельном этапе (см. план внедрения messenger).

## Cache/Redis для widget_session_token

Widget session-токены (`widget_session_token`) хранятся через стандартный Django cache (`django.core.cache`).

- В `crm/settings.py` настроено:
  - при `DEBUG=0` и наличии `REDIS_URL` используется `django_redis.cache.RedisCache` (подходит для staging/production);
  - иначе — `LocMemCache` (только для локальной разработки).
- Для production/staging **обязательно** задать `REDIS_URL` и включить Redis-кеш (через docker-compose), чтобы:
  - widget-сессии были общими для всех процессов/контейнеров;
  - не было рассинхронизации и неожиданного истечения сессий при перезапуске воркеров.

Использование `LocMemCache` допустимо только в локальной разработке.

