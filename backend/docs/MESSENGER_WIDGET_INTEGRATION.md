## Messenger Widget: интеграция на сайт

Этот виджет даёт live‑чат “как в Chatwoot” для сайта клиента и операторскую панель внутри ProfiCRM.

### 1. Быстрый старт

- **Шаг 1. Включить messenger и создать Inbox**
  - В админке ProfiCRM включить фичу messenger (или выставить `MESSENGER_ENABLED=1` в `backend/.env` / переменных окружения).
  - В настройках Messenger создать Inbox для сайта (поле `widget_token` будет сгенерировано автоматически).

- **Шаг 2. Добавить скрипт на сайт**

В `<head>` или перед `</body>` публичного сайта:

```html
<script
  src="https://YOUR_CRM_DOMAIN/static/messenger/widget.js"
  data-widget-token="PASTE_INBOX_WIDGET_TOKEN_HERE">
</script>
```

- `YOUR_CRM_DOMAIN` — домен CRM (например, `https://crm.example.ru`).
- `data-widget-token` — значение `widget_token` из настроек Inbox.

> Важно: домен сайта, куда встраивается виджет, должен быть разрешён в Inbox (см. раздел “Безопасность”).

### 2. Публичный JS‑API (`window.ProfiMessenger`)

После загрузки `widget.js` на странице будет доступен объект:

```js
window.ProfiMessenger
```

Доступные методы:

- **`open()`**  
  Открывает окно чата (popup). Если есть непрочитанные, сбрасывает счётчик badge и скроллит вниз.

- **`close()`**  
  Закрывает окно чата, **не** удаляя сессию и историю.

- **`toggle()`**  
  Переключает состояние: если чат открыт — закрывает, если закрыт — открывает.

- **`showLauncher()`**  
  Показывает кнопку‑лончер (круглая кнопка в углу).

- **`hideLauncher()`**  
  Скрывает кнопку‑лончер (можно использовать, если вы хотите управлять открытием чата только из своего UI).

- **`isOpen()` → `boolean`**  
  Возвращает `true`, если окно чата сейчас открыто.

Примеры:

```js
// Открыть чат по клику на свою кнопку
document.getElementById('supportButton').addEventListener('click', () => {
  window.ProfiMessenger && window.ProfiMessenger.open();
});

// Скрыть встроенный лончер и управлять только своей кнопкой
if (window.ProfiMessenger) {
  window.ProfiMessenger.hideLauncher();
}
```

### 3. Настройки Inbox, влияющие на виджет

В `Inbox.settings` (JSON‑поле в модели `Inbox`) можно задать дополнительные параметры.

Основные блоки:

- **Внешний вид**
  - `settings["title"]` — заголовок в шапке (по умолчанию “Чат с поддержкой”).
  - `settings["greeting"]` — подзаголовок / приветствие под заголовком.
  - `settings["color"]` — основной цвет (HEX, например `#01948E`).

- **Безопасность**
  - `settings["security"]["allowed_domains"]` — список доменов, с которых разрешён вызов Widget API.
    - Поддерживается:
      - точный домен: `"example.com"`;
      - поддомены: `"*.example.com"` (разрешит `app.example.com`, `www.example.com`, **но не** сам `example.com`);
      - домен с протоколом: `"https://crm.example.com"` (hostname будет извлечён автоматически).

- **Вложения**
  - `settings["attachments"]["enabled"]` (`true/false`) — разрешены ли вложения.
  - `settings["attachments"]["max_file_size_mb"]` — лимит размера одного файла (МБ).
  - `settings["attachments"]["allowed_content_types"]` — список разрешённых MIME‑типов (например, `["image/*", "application/pdf"]`).

- **Фичи**
  - `settings["features"]["sse"]` (`true/false`) — включить SSE‑стрим (`/api/widget/stream/`) вместо частого poll.

- **Offline / рабочие часы**
  - `settings["offline"]["enabled"]` — включить офлайн‑режим.
  - `settings["offline"]["message"]` — текст “Сейчас никого нет…” (отображается в баннере виджета, когда нет операторов или вне рабочих часов).
  - Рабочие часы задаются в `settings["working_hours"]` и используются в `widget_api` при bootstrap (см. `utils.is_within_working_hours`).

- **Privacy**
  - `settings["privacy"]["url"]` — ссылка на политику конфиденциальности.
  - `settings["privacy"]["text"]` — короткий текст уведомления (например, “Отправляя сообщение, вы соглашаетесь с обработкой персональных данных.”).

### 4. Env‑переменные, влияющие на виджет

В `backend/crm/settings.py` предусмотрены безопасные дефолты, все значения можно переопределить через `.env` / переменные окружения.

- **Feature‑флаг и общие настройки**
  - `MESSENGER_ENABLED=1` — включает messenger целиком.
  - `MESSENGER_DEFAULT_BRANCH_ID` — fallback‑филиал, если маршрутизация не смогла выбрать branch.

- **Throttling public Widget API**
  - `MESSENGER_WIDGET_BOOTSTRAP_RATE_PER_IP` — запросов в минуту на `/api/widget/bootstrap/` с одного IP.
  - `MESSENGER_WIDGET_BOOTSTRAP_RATE_PER_TOKEN` — запросов в минуту на `/api/widget/bootstrap/` по одному `widget_token`.
  - `MESSENGER_WIDGET_SEND_RATE_PER_SESSION` — запросов в минуту на `/api/widget/send/` на одну сессию.
  - `MESSENGER_WIDGET_SEND_RATE_PER_IP` — запросов в минуту на `/api/widget/send/` с одного IP.
  - `MESSENGER_WIDGET_POLL_RATE_PER_SESSION` — запросов в минуту на `/api/widget/poll/` на одну сессию.
  - `MESSENGER_WIDGET_POLL_MIN_INTERVAL_SECONDS` — минимальный интервал между `poll`‑запросами (секунды).

- **Privacy notice (дефолтные значения)**
  - `MESSENGER_PRIVACY_URL` — URL политики конфиденциальности по умолчанию.
  - `MESSENGER_PRIVACY_TEXT` — текст уведомления по умолчанию (отображается под формой ввода).

### 5. Безопасность и best practices

- **Origin allowlist**  
  Все публичные Widget‑эндпоинты (`bootstrap`, `send`, `poll`, `stream`, `attachment`, `typing`, `mark_read`, `rate`) проверяют домен по `Inbox.settings.security.allowed_domains`.  
  Если список пустой — виджет работает с любых доменов (подходит только для dev/staging).

- **Rate limiting и защита от спама**
  - Throttling на уровне DRF (`WidgetBootstrapThrottle`, `WidgetSendThrottle`, `WidgetPollThrottle`).
  - Anti‑spam внутри `widget_send`:
    - honeypot‑поле `hp` в JSON‑payload (если заполнено — запрос отклоняется),
    - ограничение количества ссылок в сообщении,
    - блокировка повторяющихся сообщений через cache.
  - Дополнительная защита через math‑captcha при подозрительной активности IP.

- **XSS/контент**
  - Все тексты сообщений и имена контактов экранируются на стороне виджета и операторской панели.
  - Вложения выдаются только через проверенный endpoint, привязанный к текущей widget‑сессии.

### 6. Типичный сценарий проверки на staging

1. Настроить Inbox с нужным `widget_token`, `allowed_domains`, offline‑сообщением и privacy‑полями.  
2. Вставить `<script ... widget.js>` на тестовый сайт, домен которого есть в `allowed_domains`.  
3. Убедиться, что:
   - при открытии сайта виджет корректно загружается и создаёт диалог в ProfiCRM;
   - сообщения из виджета видны в операторской панели, и наоборот;
   - offline‑баннер и privacy‑строка отображаются согласно настройкам;
   - публичные методы `window.ProfiMessenger` работают как заявлено. 

