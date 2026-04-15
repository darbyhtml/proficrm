# Решённые проблемы

## [2026-04-15] Аудит 2026-04-14 оказался stale: 5 «P0» уже были исправлены

**Симптом:** При валидации P0 из `knowledge-base/synthesis/state-of-project.md` (аудит 2026-04-14) обнаружилось, что половина критичных находок ссылается на код, которого в текущем HEAD нет — либо поля/логика уже переписаны.

**Закрыто как false alarm (5 штук):**
- **P0-02** `company_scope_q` возвращает `Q()` — не баг, намеренное бизнес-правило (общая база клиентов для 3 подразделений ЕКБ/Тюмень/Краснодар, нужна для входящих обращений). Расширен docstring + ADR 2026-04-15.
- **P0-03** `WidgetConsumer.Contact.session_token` — в коде используется Redis-кеш `get_widget_session()`, поле модели не трогается.
- **P0-04** `OperatorConsumer.AgentProfile.last_seen_at` — используется `AgentProfile.Status.ONLINE/OFFLINE`, `last_seen_at` вообще не упоминается в консьюмерах.
- **P0-05** Widget Origin hijack — `MESSENGER_WIDGET_STRICT_ORIGIN=1` в проде, `enforce_widget_origin_allowed` блокирует 403 при пустом allowlist. Nginx CORS-эхо preflight безвредно: реальный запрос всё равно уходит в Django и блокируется там.
- **P0-06** `get_client_ip` без allowlist — делегирует в `accounts.security.get_client_ip` с PROXY_IPS проверкой.

**Причина stale-аудита:** между снимком 2026-04-14 и текущим HEAD было несколько hardening-пассов. Часть находок уже была закрыта, но audit не перестроился.

**Вывод:** оставшиеся P0 проверять **через чтение текущего кода**, а не слепо фиксить по списку. Audit — карта, не территория.

**Ссылки:** [decisions.md](decisions.md) ADR 2026-04-15.

---

## [2026-04-15] Outbound webhooks и Web Push теряли payload при рестарте gunicorn

**Симптом:** Уведомления в интегрированные системы (webhook клиента) и
Web Push в браузеры операторов периодически не доходили. Не было
понимания «почему» — в логах `WARNING: Webhook call failed` иногда,
но без retry-истории.

**Причина:** `messenger/integrations.py:_send_webhook_async` и
`messenger/push.py:send_push_to_user` отправляли из
`threading.Thread(target=_worker, daemon=True)`. При любом рестарте
gunicorn (deploy, OOM, gthread cycle) daemon-поток убивался
вместе с процессом до завершения `requests.post(...)` — payload
пропадал без следа.

**Решение:** Два новых Celery-таска с `autoretry_for=(Exception,)`,
`retry_backoff=True`, `retry_backoff_max=600`, `max_retries=5/3`,
`acks_late=True`:
- `messenger.send_outbound_webhook` — 4xx не ретраит (проблема
  конфигурации получателя), 5xx и network errors ретраит с
  экспоненциальной паузой. SSRF-проверка остаётся на стороне
  producer (`_is_safe_outbound_url` до `.delay()`), чтобы не
  отправить мусор в Celery-очередь.
- `messenger.send_push_notification` — делится по одному таску
  на каждый `PushSubscription.id`; 404/410 деактивируют подписку
  без ретрая (endpoint мёртв), остальные ошибки → retry.

Производитель (`_send_webhook_async`) сначала сериализует body
и считает HMAC-подпись, затем `.delay()` — если Celery отвалится,
ошибка поднимется наверх (визуально в логе producer'a), а не
проглотится в потоке.

**Файлы:** `backend/messenger/tasks.py`, `backend/messenger/integrations.py`,
`backend/messenger/push.py`. Коммит `e118a36`.

---

## [2026-04-15] Race condition при генерации повторяющихся задач

**Симптом:** Теоретически: два параллельных запуска
`generate_recurring_tasks` (ручной + celery-beat) могли создать
дубликаты экземпляров повторяющейся задачи — `exists()`-проверка
перед `create()` не защищает от одновременной вставки.

**Причина:** Защита была в три слоя (redis-lock с TTL 15 мин,
`select_for_update` на шаблоне, `exists()`-проверка), но все три
работают на уровне приложения. Нужен был DB-level constraint.

**Решение:** Partial UniqueConstraint в `tasksapp.Task`:
```python
UniqueConstraint(
    fields=["parent_recurring_task", "due_at"],
    condition=Q(parent_recurring_task__isnull=False),
    name="uniq_task_recurrence_occurrence",
)
```
PostgreSQL создаёт partial unique index
`WHERE (parent_recurring_task_id IS NOT NULL)` — не мешает
ручному созданию задач с `parent_recurring_task=NULL`, но
гарантирует уникальность сгенерированных экземпляров.

`_process_template` оборачивает `Task.objects.create()` в
`with transaction.atomic():` (savepoint) и ловит `IntegrityError`
— если второй воркер как-то обошёл redis-lock и `SELECT FOR UPDATE`,
он получит DB-конфликт и тихо пропустит вставку, не ломая внешнюю
транзакцию итерации по шаблонам.

**Файлы:** `backend/tasksapp/models.py`,
`backend/tasksapp/migrations/0013_task_uniq_recurrence_occurrence.py`,
`backend/tasksapp/tasks.py`. Коммит `880d445`.

---

## [2026-04-15] `/notifications/poll/` — burst polling от нескольких вкладок

**Симптом:** На страницах с открытыми 5-10 вкладками каждая вкладка
дёргала `/notifications/poll/` по своему `setInterval` — итого
десятки запросов в минуту на пользователя, каждый отрабатывал
`notifications_panel(request)` с cascade-запросами.

**Причина:** Нет кэша на уровне endpoint. Первый слой кэша
(`bell_data:{user_id}` на 30с) был в `notifications_panel`, но
он всё равно выполнял Redis-GET + Announcement-query для каждого
запроса.

**Решение:**
1. `/notifications/poll/` кэшируется per-user на 3 секунды
   в Redis (`notif_poll:{user_id}`). 3 секунды — верхний порог
   незаметности для клик-отклика, но схлопывает burst от N вкладок.
   Response маркируется `X-Cache: HIT|MISS`.
2. Инвалидация на `mark_read` и `mark_all_read` через
   `cache.delete_many([f"bell_data:{id}", f"notif_poll:{id}"])`.
3. Фронтенд поставлен на паузу через `visibilitychange`: когда
   вкладка уходит в фон — `clearInterval`, когда возвращается —
   `poll()` + новый `setInterval`. Два интервала
   (bell 30s + campaign 15s, был 4s).

**Файлы:** `backend/notifications/views.py`,
`backend/templates/ui/base.html`, `backend/templates/ui/dashboard.html`.
Коммиты `ecefbe0`, `0c30357`.

---

## [2026-04-15] Staging build → wrong docker-compose file

**Симптом:** `docker compose build web` на staging завершался с
`EXIT=0`, но пересобранный образ не содержал новый код. Timestamp
на образе оставался старым.

**Причина:** `docker-compose.yml` (без `-f`) в
`/opt/proficrm-staging/` содержит старый black-box config
(`image: python:3.13-slim`, не build). Реальный staging-стек
описан в `docker-compose.staging.yml`. Без `-f`
флага Compose строил несуществующий сервис из неправильного
файла, выдавая 0 exit code с пустым выводом.

**Решение:** Все docker-compose команды на staging — с
`-f docker-compose.staging.yml`. Верификация пересборки —
`docker run --rm --entrypoint sh <image> -c 'grep -c NEW_SYMBOL /app/backend/path'`.

**Файлы:** `/opt/proficrm-staging/docker-compose.staging.yml`
(staging-only, не в git — fix на уровне procedure).

---

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
