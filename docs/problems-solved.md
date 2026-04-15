# Решённые проблемы

## [2026-04-07] Массовое переназначение компаний блокировалось при наличии «запрещённых»

**Симптом:** Директор филиала выбирает несколько компаний уволенных сотрудников → нажимает «Переназначить» → ошибка «Некоторые компании нельзя передать», предлагается обновить страницу. Одиночная передача работает.

**Причина:** В `company_bulk_transfer()` проверка `if transfer_check["forbidden"]:` блокировала **всю** операцию, если хотя бы одна из выбранных компаний не прошла `can_transfer_company()`. Причины отказа для директора филиала: компания без ответственного, ответственный из другого филиала, ответственный с ролью GROUP_MANAGER/ADMIN.

**Решение:** Разрешённые компании переназначаются, запрещённые пропускаются. Toast и аудит-лог информируют о количестве пропущенных.

**Файлы:** `backend/ui/views/company_list.py`, `backend/templates/ui/company_list.html`

---

## [2026-04-07] Staging-токены в .playwright-mcp/ не исключены из git

**Симптом:** Security review обнаружил, что `.playwright-mcp/` содержит логи Playwright Browser MCP со staging widget token и session token в URL. Директория не была в `.gitignore` — при `git add .` токены попали бы в репозиторий.

**Причина:** Playwright MCP записывает console-логи с полными URL, включая query-параметры (`widget_token=...`, `session_token=...`). Также `test-screenshots/` и PNG в корне содержали скриншоты staging UI с PII.

**Решение:** Добавлены в `.gitignore`: `.playwright-mcp/`, `test-screenshots/`, PNG-скриншоты. Рекомендована ротация staging widget token.

**Файлы:** `.gitignore`

---

## [2026-04-06] SSE стримы блокировали весь сервер

**Симптом:** Сообщения в мессенджере приходили с огромной задержкой. Real-time не работал — нужно было обновлять страницу вручную. И в виджете, и в оператор-панели.

**Причина:** Gunicorn работал с 2 sync workers. Каждый SSE-стрим (widget 25с + operator per-conversation 30с + notifications 55с) блокировал воркер на всё время соединения. С 3 стримами = 0 свободных воркеров для обработки API-запросов (send, poll, mark-read).

**Решение:** Переход на `--worker-class gthread --workers 4 --threads 8` (32 параллельных соединения). Первая попытка с gevent провалилась — psycopg3 несовместим (monkey-patching ломает `Queue[T]`).

**Файлы:** `docker-compose.staging.yml`, `Dockerfile.staging`, `backend/requirements.txt`

---

## [2026-04-06] Typing-индикатор инвертирован у оператора

**Симптом:** Оператор видел "контакт печатает" когда контакт НЕ печатал, и наоборот.

**Причина:** В `api.py:737` — `contact_typing = typing_status.get("contact_typing") is False` вместо `is True`.

**Решение:** Замена `is False` на `is True`.

**Файлы:** `backend/messenger/api.py`

---

## [2026-04-06] Оператор-стрим дублировал все сообщения при reconnect

**Симптом:** Каждые 25 секунд (reconnect SSE) все сообщения диалога появлялись заново.

**Причина:** `last_message_id = 0` при старте стрима. Должен начинаться с последнего существующего сообщения.

**Решение:** Инициализация `last_message_id` из `conversation.messages.order_by("-id").first()`.

**Файлы:** `backend/messenger/api.py`

---

## [2026-04-06] Widget не получал уведомление о прочтении оператором

**Симптом:** В виджете не показывалось что оператор прочитал сообщения.

**Причина:** В `widget_api.py:1215` — `changed = False` сбрасывал флаг, установленный блоком проверки `read_up_to` выше. Переменная `changed` инициализировалась перед проверкой read, но потом перезатиралась.

**Решение:** Перенос `changed = False` перед блоком read-check.

**Файлы:** `backend/messenger/widget_api.py`

---

## [2026-04-06] Celery task offline email: AttributeError

**Симптом:** `send_offline_email_notification` падал с `AttributeError: 'GlobalMailAccount' object has no attribute 'reply_to'`.

**Причина:** `MailAccount` имеет поле `reply_to`, но `GlobalMailAccount` — нет. `build_message()` обращался к `account.reply_to` без проверки.

**Решение:** `getattr(account, "reply_to", "")` в `smtp_sender.py` + явный `reply_to=""` в task.

**Файлы:** `backend/mailer/smtp_sender.py`, `backend/messenger/tasks.py`

---

## [2026-04-06] SSE: сообщения не отображаются в виджете — РЕШЕНО

**С��мптом:** Оператор отправляет с��общение — оно не появляется в виджете. Нужно обновить страницу. При перезагрузке — сохранённые сообщения тоже не рендерились.

**Причина (корневая — тройная дедупликация):** Один и тот же паттерн ошибки повторялся в ТРЁХ местах `widget.js`. Код добавлял `msg.id` в `receivedMessageIds` Set **перед** вызовом `addMessageToUI()`, который проверял тот же Set — и сразу возвращал `return` (не рендерил).

Места бага:
1. SSE `update` handler (строка ~740) — SSE фильтр добавлял в Set перед рендером
2. `render()` savedMessages loop (строка ~1618) — восстановление из localStorage
3. `render()` initialMessages loop (строка ~1626) — начальные сообщения из bootstrap

**Ложный след (gthread буферизация):** Первоначально подозревали, что gthread Gunicorn буферизует StreamingHttpResponse. Тесты curl доказали обратное — gthread корректно стримит SSE инкрементально. Проблема была полностью на стороне JS.

**Дополнительная проблема (host nginx):** Двухуровневый nginx (host → Docker) — host nginx не имел `proxy_buffering off` для SSE эндпоинтов. Добавлены отдельные location-блоки для `/api/widget/stream/` и `/api/conversations/*/stream/` с `proxy_buffering off`.

**Решение:** Удалить `receivedMessageIds.add(msg.id)` из всех трёх мест. `addMessageToUI()` сам корректно обрабатывает дедупликацию: проверяет Set → добавляет → рендерит.

**Файлы:** `backend/messenger/static/messenger/widget.js`, `/etc/nginx/sites-available/crm-staging`

---

## [2026-04-05] CORS дубли — виджет не загружался на внешних сайтах

**Симптом:** Widget API возвращал два `Access-Control-Allow-Origin` заголовка. Браузер отвергал ответ.

**Причина:** И nginx, и Django добавляли CORS заголовки. `django-cors-headers` middleware обрабатывал OPTIONS до view-кода, поэтому `_add_widget_cors_headers()` не мог их контролировать.

**Решение:** Разделение: nginx обрабатывает OPTIONS preflight (возвращает 204 с CORS), Django добавляет CORS на ответы (POST/GET). django-cors-headers не используется для Widget API.

**Файлы:** `nginx/staging.conf`, `backend/messenger/widget_api.py`

---

## [2026-04-05] Widget CSS не загружался на внешних сайтах

**Симптом:** Виджет открывался, но без стилей — все элементы в дефолтных стилях браузера.

**Причина:** `widget.js` не подключал свой CSS при встраивании на внешний сайт (CSS подключался только в Django-шаблоне).

**Решение:** Метод `_ensureCSS()` в `widget.js` — автоматически создаёт `<link>` тег для `widget.css`, используя `CONFIG.API_BASE_URL` как базу.

**Файлы:** `backend/messenger/static/messenger/widget.js`

---

## [2026-04-05] WidgetSession TypeError при bootstrap

**Симптом:** `/api/widget/bootstrap/` возвращал 500. В логах: `TypeError` при создании `WidgetSession`.

**Причина:** `create_widget_session()` передавал `bound_ip` и `created_at`, но dataclass `WidgetSession` не имел этих полей.

**Решение:** Добавлены поля `bound_ip: str = ""` и `created_at: str = ""` в dataclass.

**Файлы:** `backend/messenger/utils.py`

---

## [2026-04-05] Inbox без branch — 503 при bootstrap

**Симптом:** Widget bootstrap возвращал 503 "no routing rule and no MESSENGER_DEFAULT_BRANCH_ID".

**Причина:** Inbox создан без `branch_id`. Поле `branch` нельзя изменить после создания (нет такой логики в модели).

**Решение:** Создание нового inbox с `branch_id=1`. Старый деактивирован.

**Файлы:** нет изменений кода — операционная проблема.

---

## [2026-04-05] docker compose restart не подхватывает новые env

**Симптом:** Изменили `.env.staging`, сделали `docker compose restart web` — переменные не обновились.

**Причина:** `restart` перезапускает контейнер без пересоздания. `env_file` читается только при `create`.

**Решение:** Всегда использовать `docker compose up -d web` (пересоздаёт контейнер).

**Файлы:** нет изменений кода — операционная проблема.

---

## [2026-04-05] merge-contacts 500 на невалидном UUID

**Симптом:** API merge-contacts возвращал 500 при передаче мусора в quality UUID.

**Причина:** Django `UUIDField.get_prep_value()` бросает `ValidationError`, не `ValueError`. Код ловил только `ValueError`.

**Решение:** Добавлен `except DjangoValidationError` в обработчик.

**Файлы:** `backend/messenger/api.py`
