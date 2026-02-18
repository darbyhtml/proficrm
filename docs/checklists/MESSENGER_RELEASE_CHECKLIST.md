## Messenger (live‑chat) — Release Checklist

Этот чек‑лист описывает, что нужно проверить перед выкатыванием live‑chat (messenger) на production.

### 1. Конфигурация окружения

- **База данных**
  - [ ] Production использует PostgreSQL (как и основная инсталляция CRM).
  - [ ] Все миграции применяются без ошибок:
    - `cd backend && python manage.py migrate`.

- **Фича‑флаг и настройки messenger**
  - [ ] `MESSENGER_ENABLED=1`.
  - [ ] `MESSENGER_DEFAULT_BRANCH_ID` задан и соответствует существующему филиалу (для глобальных Inbox).
  - [ ] При необходимости включена GeoIP‑поддержка: `MESSENGER_GEOIP_ENABLED=1` (или осознанно отключена).

- **Throttling и abuse‑защита Widget API**
  - [ ] Значения `MESSENGER_WIDGET_*` выставлены под ожидаемую нагрузку:
    - `MESSENGER_WIDGET_BOOTSTRAP_RATE_PER_IP`
    - `MESSENGER_WIDGET_BOOTSTRAP_RATE_PER_TOKEN`
    - `MESSENGER_WIDGET_SEND_RATE_PER_SESSION`
    - `MESSENGER_WIDGET_SEND_RATE_PER_IP`
    - `MESSENGER_WIDGET_POLL_RATE_PER_SESSION`
    - `MESSENGER_WIDGET_POLL_MIN_INTERVAL_SECONDS`
  - [ ] Redis‑cache для продакшена настроен и доступен (для throttling, captcha, typing‑индикаторов).

- **Privacy / политика обработки данных**
  - [ ] Заданы дефолтные:
    - `MESSENGER_PRIVACY_URL`
    - `MESSENGER_PRIVACY_TEXT`
  - [ ] При необходимости переопределены на уровне конкретного Inbox через `inbox.settings["privacy"]`.

### 2. Inbox и безопасности доменов

- **Inbox‑ы**
  - [ ] Для каждого сайта/канала создан отдельный Inbox с уникальным `widget_token`.
  - [ ] Для глобальных Inbox настроена маршрутизация (Routing Rules) и/или fallback branch.

- **Allowlist доменов**
  - [ ] В каждом Inbox задан `settings["security"]["allowed_domains"]`:
    - только production‑домены сайтов;
    - без `localhost` и тестовых доменов.
  - [ ] Проверено, что запросы с чужих доменов (`Origin` / `Referer`) получают 403 и логируются.

### 3. Встраивание виджета (staging / production)

- **Скрипт виджета на сайте**
  - [ ] На каждом сайте добавлен:

```html
<script
  src="https://YOUR_CRM_DOMAIN/static/messenger/widget.js"
  data-widget-token="PASTE_INBOX_WIDGET_TOKEN_HERE">
</script>
```

  - [ ] Домен сайта присутствует в `allowed_domains` соответствующего Inbox.

- **Публичный JS‑API**
  - [ ] При необходимости используется `window.ProfiMessenger`:
    - `open()/close()/toggle()` для управления окном;
    - `showLauncher()/hideLauncher()` для управления иконкой‑лончером;
    - интеграция с кастомными кнопками “Написать в чат”.

### 4. Функциональный smoke‑тест

- **Со стороны посетителя**
  - [ ] Виджет появляется на сайте, открывается/закрывается, не ломает верстку.
  - [ ] Новое сообщение из виджета:
    - попадает в операторскую панель (unified messenger);
    - корректно отображается, включая вложения (если разрешены).
  - [ ] Закрытие/открытие страницы сохраняет историю переписки (сессия восстанавливается).
  - [ ] Offline‑сообщение отображается при отсутствии операторов или вне рабочих часов.
  - [ ] Privacy‑строка и ссылка корректны (ведут на актуальную политику).

- **Со стороны оператора**
  - [ ] Оператор видит новый диалог в списке, может:
    - принять/назначить диалог (“Назначить меня”, выбор assignee),
    - поменять статус (`open/pending/resolved/closed`),
    - изменить приоритет.
  - [ ] Отправка сообщения оператором:
    - немедленно видна в виджете;
    - корректно помечается read/delivered.
  - [ ] Внутренние заметки (режим “Заметка”) видны только сотрудникам и визуально отличаются от сообщений клиенту.

### 5. Тесты и CI

- **Unit / integration**
  - [ ] Тесты `messenger.tests.*` проходят на staging‑окружении (PostgreSQL, Redis).
  - [ ] Тесты безопасности Widget API (`test_widget_security_features.py`, `test_api_security.py`) зелёные.

- **E2E / smoke (рекомендуется)**
  - [ ] Есть как минимум один Playwright‑сценарий:
    - логин в CRM;
    - открытие unified messenger;
    - принятие диалога и отправка сообщения;
    - проверка, что виджет на тестовой странице это сообщение получил.

- **CI‑pipeline**
  - [ ] В CI настроен job, который для веток/PR, затрагивающих `backend/messenger/`, запускает:
    - `python manage.py test messenger`;
    - JS‑линтер для `backend/messenger/static/messenger/*.js`.

### 6. Нагрузочное тестирование (по возможности)

- **Сценарий**
  - [ ] Locust (или аналог) имитирует:
    - N одновременно активных посетителей (bootstrap + send + poll/stream);
    - M операторов, работающих в unified messenger.

- **Критерии**
  - [ ] p95 latency для Widget API в пределах договорённого SLA.
  - [ ] Ошибок 5xx (особенно на `/api/widget/*`) не более оговорённого порога.
  - [ ] Память и CPU растут линейно и не утекают после окончания теста.

### 7. Definition of Done для релиза messenger

- [ ] Все пункты выше отмечены как выполненные или осознанно исключены (с указанием причины).
- [ ] Есть понятный runbook/инструкция для поддержки (что проверять при инцидентах с чатами).
- [ ] На production включен messenger минимум на одном боевом Inbox, проведён пробный прогон с реальными пользователями (или пилотной группой).

