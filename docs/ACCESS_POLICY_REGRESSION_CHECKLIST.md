# Регресс-чеклист: единые доступы (policy) и критичные страницы

## 0) Перед началом
- Применить миграции (включая `policy`).
- Убедиться, что в админке есть запись `PolicyConfig` (singleton) и режим **observe_only** по умолчанию.

## 1) Режимы policy
- **observe_only**:
  - Ничего не должно “сломаться” по доступам (поведение как раньше).
  - В `audit.ActivityEvent` должны появляться записи с `entity_type=policy` при обращениях к UI/API/phone API.
- **enforce**:
  - Запрещённые правилами ресурсы должны блокироваться (403/PermissionDenied на API/phone API).
  - В UI должен быть 403/редирект+сообщение для запрещённых страниц/действий.

### 1.1) UI “Доступы” (policy)
- `/settings/access/`:
  - Доступ только у `superuser` и роли `ADMIN`.
  - Кнопка **«Восстановить дефолтные правила страниц»** создаёт недостающие правила, не перезаписывая существующие.
  - Кнопка **«Восстановить дефолтные правила действий (UI)»** делает то же самое для действий.
  - Кнопка baseline для менеджера запрещает все `sensitive` ресурсы менеджеру.

## 2) Аутентификация
- `/login/` (session)
- `/auth/magic/<token>/` (magic-link)
- `/logout/`
- `/api/token/` + `/api/token/refresh/` (JWT)

## 3) Задачи (P0 — утечка через API)
- Web UI: `/tasks/`
  - Менеджер видит **только свои** задачи (и `mine=0` не расширяет).
  - Директор/РОП: задачи своего филиала + свои.
  - Админ/управляющий: все.
- DRF API:
  - `/api/tasks/` (list) **должен совпадать** с видимостью UI.
  - `/api/tasks/<id>/` (retrieve) не должен отдавать чужие задачи (должен 404 при ограничении queryset).

## 4) Компании
- `/companies/` список: фильтры/сортировки/экспорт/duplicates/bulk-transfer.
- `/companies/<uuid>/` карточка: inline update, заметки, контакты.
- Удаление:
  - delete-request create/cancel/approve
  - direct delete
- Холодный звонок:
  - toggle/reset (company/contact/phones)

## 5) Почта
- `/mail/campaigns/` + операции (create/edit/generate/send/pause/resume)
- `/mail/settings/` (SMTP настройки)
- `/mail/signature/` (подпись пользователя)
- Админские отписки:
  - `/mail/unsubscribes/list/`
  - `/mail/unsubscribes/delete/`
  - `/mail/unsubscribes/clear/`
- `/unsubscribe/<token>/` (GET/POST, CSRF exempt)

## 6) Аналитика/уведомления
- `/analytics/` + `/analytics/users/<id>/`
- `notifications/*` (poll/mark read/mark all)
- `/notifications/all/` и `/notifications/reminders/all/`

## 7) Phone/Android API
- `/api/phone/devices/register/` (POST)
- `/api/phone/devices/heartbeat/` (POST)
- `/api/phone/calls/pull/` (GET)
- `/api/phone/calls/update/` (POST)
- `/api/phone/telemetry/` (POST)
- `/api/phone/logs/` (POST)
- `/api/phone/qr/create/` (POST)
- `/api/phone/qr/exchange/` (POST)
- `/api/phone/logout/` (POST)
- `/api/phone/logout/all/` (POST)
- `/api/phone/user/info/` (GET)

## 8) UI-меню (base.html)
- Пункт **Аналитика** должен показываться/скрываться по policy ресурсу `ui:analytics`.
- Пункт **Админка** vs **Настройки** должен показываться по policy ресурсу `ui:settings`.

