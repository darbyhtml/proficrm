# Решённые проблемы

## [2026-04-20] Conversation.status — `waiting_offline` в choices, но не в CheckConstraint

**Симптом**: off-hours widget-запросы на staging падали с `IntegrityError: new row for relation "messenger_conversation" violates check constraint "conversation_valid_status"`. Тесты `messenger.tests.test_widget_offhours` падали 2 штуки.

**Корень**: модель `Conversation` имела:
- `choices = ["open", "pending", "waiting_offline", "resolved", "closed"]`
- `CheckConstraint(Q(status__in=["open", "pending", "resolved", "closed"]))` — **без `waiting_offline`**.

Кто-то добавил новое значение в Enum, но забыл обновить constraint. Типичная миграционная ошибка.

**Фикс**:
- `backend/messenger/models.py`: добавлено `waiting_offline` в список.
- Миграция `messenger/0027_remove_conversation_conversation_valid_status_and_more.py` — DROP constraint + CREATE с 5 статусами.

**Важно для Релиза 1**: эта миграция **обязательна к деплою** перед включением `MESSENGER_ENABLED=1` на проде, иначе off-hours чаты будут падать с 500.

---

## [2026-04-20] PostgreSQL: FOR UPDATE cannot be applied to the nullable side of an outer join

**Симптом**: `tasksapp.tasks.generate_recurring_tasks` (Celery beat задача, ежедневно 06:00) падала с `django.db.utils.NotSupportedError: FOR UPDATE cannot be applied to the nullable side of an outer join`. 7 тестов красные. На проде — рекуррентные задачи **не генерировались молча** (функция в cron возвращает None → worker не жалуется).

**Корень**:
```python
Task.objects.select_for_update()
    .select_related("created_by", "assigned_to", "company", "type")
    .get(pk=template_id)
```
Все 4 FK в `select_related` — `on_delete=SET_NULL`, значит nullable. Django эмитит `LEFT OUTER JOIN`. PostgreSQL не разрешает `FOR UPDATE` на nullable-side join.

**Фикс**: `.select_for_update(of=("self",))` — `of` говорит «лочим только Task row, не joined таблицы». Известная Django-фича с 2.0+. `select_related` продолжает работать, просто без row-level lock на FK-таблицах (они и не нужны — блокируем только сам шаблон задачи).

**Как это проглядели**: `scripts/test.sh` использует `DJANGO_SETTINGS_MODULE=crm.settings_test` где `CELERY_TASK_ALWAYS_EAGER=True`, и тесты `GenerateRecurringTasksTest` показывали эту ошибку. Но: (1) тесты были в общем списке падающих «20 без разбора», (2) сама функция в проде — background beat, никто не видит её NoneReturn.

---

## [2026-04-20] `generate_recurring_tasks` возвращает None вместо dict

**Симптом**: `result = generate_recurring_tasks(); result["created"]` → `TypeError: 'NoneType' object is not subscriptable`.

**Корень**: функция обёрнута в redis-lock try/finally, но **нет `return` для результата inner-функции**:
```python
try:
    _generate_recurring_tasks_inner()   # возвращает {"templates":N,"created":N}, но результат теряется
finally:
    cache.delete(LOCK_KEY)
# неявный return None
```

**Фикс**: `return _generate_recurring_tasks_inner()`.

**Почему на проде «всё работает»**: Celery-beat запускает task, получает None или dict — ему всё равно. Задача «прошла успешно». Реальное отсутствие генерации экземпляров никто не замечал пока ручные тесты не запустили.

---

## [2026-04-20] settings_test.py не переопределял ALLOWED_HOSTS

**Симптом**: все view-тесты (использующие `django.test.Client`) возвращали status 400 с пустым body → `json.loads(resp.content)` падал `JSONDecodeError: Expecting value: line 1 column 1`. Без изменений кода 18 тестов были «сломаны годами».

**Корень**: Django `Client` по умолчанию шлёт `HOST=testserver`. Prod-settings имеет узкий whitelist `ALLOWED_HOSTS = [h for h in os.getenv('DJANGO_ALLOWED_HOSTS', ...).split(',')]` — там нет `testserver`. SecurityMiddleware возвращал 400 (Bad Request) до того, как view даже начинал работать.

В `settings_test.py` было `from .settings import *` — **унаследовал** узкий whitelist, не переопределил.

**Фикс**: добавить `ALLOWED_HOSTS = ["*"]` в `settings_test.py` после импорта. Безопасно — это тестовая среда, никаких внешних подключений.

**Важно для CI**: CI-джоба `test:` в `.github/workflows/ci.yml` явно задаёт `DJANGO_ALLOWED_HOSTS: localhost,127.0.0.1` — там может быть свой пропуск. Нужно проверить что CI идёт через settings_test (это уже так, судя по `DJANGO_SETTINGS_MODULE: crm.settings_test`).

---

## [2026-04-20] Тесты ColdCallsReport ожидали JSON без `X-Requested-With`

**Симптом**: после фикса ALLOWED_HOSTS тесты `ColdCallsReportDayTest` и `ColdCallsReportMonthTest` всё равно падали. Get возвращал 200 + HTML, `json.loads` не мог распарсить.

**Корень**: view `cold_calls_report_day`:
```python
if request.headers.get("X-Requested-With") == "XMLHttpRequest":
    return JsonResponse({...})
return render(request, "ui/reports/cold_calls_day.html", {...})
```
AJAX-запросы → JSON, обычные → HTML. Тесты использовали обычный `client.get(url)` без AJAX-заголовка.

**Фикс**: в `setUp` обоих классов:
```python
self.client.defaults["HTTP_X_REQUESTED_WITH"] = "XMLHttpRequest"
```
Теперь все запросы client получают AJAX-заголовок автоматически.

**Урок**: content-negotiation на основе headers (JSON vs HTML) — хорошо для production, но ломает тесты, если тестов автор не учёл. Либо use `Accept: application/json`, либо тесты дают AJAX header.

---

## [2026-04-20] Docker `["CMD", ...]` healthcheck не интерполирует `$HOSTNAME`

**Симптом**: `proficrm-celery-1` показывал `unhealthy` **40 209 consecutive failures** подряд (~4 недели). При этом Celery работал — задачи принимались, выполнялись, `docker logs` чистый.

**Диагностика**:
- `docker inspect` показал healthcheck: `["CMD", "celery", "-A", "crm", "inspect", "ping", "-d", "celery@$HOSTNAME", "--timeout", "5"]`
- Ручной запуск через `docker exec ... sh -c "celery -A crm inspect ping -d celery@\$HOSTNAME --timeout 5"` → OK, `pong`, `1 node online`
- Но без `sh -c` — команде целевой аргумент передаётся **буквально** `celery@$HOSTNAME` (с долларом), без интерполяции.

**Корень**: формат `["CMD", args...]` в docker-compose — это **exec напрямую без shell**. `$HOSTNAME` интерполируется только shell'ом (sh/bash), поэтому при exec-формате переменная не раскрывается. Раньше (до 4 недель назад) команда, возможно, была без `-d`, или healthcheck переписали при каком-то обновлении и не проверили.

**Фикс**: убрать `-d destination` вовсе. Один Celery-воркер в prod-инсталляции — `inspect ping` без `-d` опрашивает все ноды (N=1 даёт тот же результат):
```yaml
test: ["CMD", "celery", "-A", "crm", "inspect", "ping", "--timeout", "10"]
# timeout 10s (было 5) — при нагрузке broker ответ иногда >5s
```

**Альтернатива** (отвергнута): `["CMD-SHELL", "celery ... -d celery@$HOSTNAME ..."]` — работает, но тянет sh в healthcheck. Для current-inst. (1 worker) не нужна.

**Урок**: `docker-compose.yml healthcheck test` — **exec, не shell**. Любая `$VAR` без `CMD-SHELL` уйдёт буквально. Проверять через `docker inspect <container> --format '{{json .State.Health.Log}}'` после изменений.

---

## [2026-04-20] Policy engine писал 150K ActivityEvent/сутки — 9.5M записей, 95% БД

**Симптом**: таблица `audit_activityevent` раздулась до **4 GB** (73% от 5.5 GB БД). Из 9.5M строк — **9.5M с `entity_type='policy'`** (95%). Миграции на audit-таблицах ожидались "тяжёлыми", `pg_dump` медленный, retention worker неэффективен.

**Диагностика**:
- `SELECT verb, entity_type, COUNT(*) FROM audit_activityevent WHERE created_at > NOW() - INTERVAL '30 days' GROUP BY verb, entity_type ORDER BY COUNT(*) DESC`
- Результат: `update | policy | 4 543 450` (1000× больше любого другого события).
- Пример записи: `{"mode":"enforce","allowed":true,"context":{"path":"/mail/progress/poll/","method":"GET"},"matched_effect":"allow","matched_rule_id":109}`

**Корень**: функция `backend/policy/engine.py:_log_decision()` пишет ActivityEvent **на каждый HTTP-запрос** через `@policy_required`. Polling endpoints (`/mail/progress/poll/`, `/notifications/poll/`) = ~1.5 млн запросов/сутки при 50 пользователях.

В `ui/views/settings_core.py:1432` уже есть `exclude(entity_type='policy')` — разработчик **знал** и починил только UI-отображение, корень не тронул.

**Фикс** (Релиз 0, 2026-04-20):
1. **PG RULE (мгновенный хотфикс на проде)**:
   ```sql
   CREATE RULE block_policy_activity_events AS ON INSERT TO audit_activityevent
     WHERE NEW.entity_type='policy' DO INSTEAD NOTHING;
   ```
2. **Batch DELETE 10.3M** старых записей (103 итерации × 100K, пауза 2 сек, ~12 минут в фоне без блокировки).
3. **Код в main-ветке** (поедет в Релиз 1):
   - `settings.py`: `POLICY_DECISION_LOGGING_ENABLED = os.getenv(..., "0") == "1"` (выкл по умолчанию)
   - `engine.py:_log_decision()`: `if not settings.POLICY_DECISION_LOGGING_ENABLED: return`

**Осталось**: VACUUM FULL `audit_activityevent` ночью (освободит ~3 GB на диске, дед-спейс после DELETE).

**Урок**: аудит-логи технических решений (policy, middleware trace) — **отдельный слой** (рабочие журналы / Sentry / структурный log), не `audit_activityevent` (который пользователь видит в истории). Фиксировать только отказы (deny) и состояние изменения правил, не каждое `allow`.

---

## [2026-04-18] Django multi-line `{# #}` комментарии не работают

**Симптом**: в `templates/ui/company_detail_v3/b.html` в sidebar «Договор» пользователю показывался сырой текст комментария (`{# Годовой договор: показ суммы... #}`).

**Корень**: Django поддерживает однострочный `{# ... #}`, НО НЕ поддерживает многострочные комментарии через эту конструкцию. При переносе строки Django перестаёт считать это комментарием, а пытается парсить как тег, либо оставляет как литерал.

**Фикс**: заменить на `{% comment %}...{% endcomment %}` — это единственный корректный способ многострочных комментариев в Django templates.

**Урок**: никогда не писать многострочный `{# ... #}`. Для пометок/напоминаний в шаблоне — либо в одну строку, либо `{% comment %}`.

## [2026-04-18] Phone validation: 30-значный мусор проходил как валидный номер

**Симптом**: POST `/companies/<id>/phones/create/` с `phone=123456789012345678901234567890` возвращал 200 OK, в БД сохранялась 30-значная строка вместо номера.

**Корень**: `companies.normalizers.normalize_phone()` для unknown-префикса возвращает `original[:50]`. Далее проверка `len(digits) >= 10` проходила (30 ≥ 10).

**Фикс**: добавлен `re.fullmatch(r"\+\d{10,15}", normalized)` после normalize_phone. Мусор теперь не проходит.

**Также**: null-byte (`\x00`) в phone/comment вызывал HTTP 500 (PostgreSQL отклоняет NUL bytes). Добавлена явная проверка → 400 Bad Request.

## [2026-04-18] localStorage скрытые фильтры на `/companies/` путают пользователей

**Симптом**: РОП Оксана жаловалась «фильтр по Красноярскому краю показывает 1 компанию, общим списком 6 насчитала — но в БД точно больше».

**Корень**: в `company_list_v2.html:595-620` есть функция «Запомнить фильтры» — при применении фильтра параметры сохраняются в `localStorage['v2_company_filters_v1']`, и при следующем заходе на `/companies/` БЕЗ URL-params автоматически подставляются в URL через `window.location.search`. Пользователь не понимает, что фильтры активны (`task_filter=week` не отображался как видимый чип в старом `company_list.html`).

**Фикс на прод (2026-04-18)**: добавлен жёлтый баннер «Активен дополнительный фильтр: Задачи — только на этой неделе» + ссылка «снять этот фильтр» через `docker cp` в `company_list.html`.

**Фикс в main (`company_list_v2.html`)**: task_filter + overdue уже показываются как `fchip` с ×-кнопкой (строки 162-170). При полном деплое main→prod проблема решается естественным образом через v2-шаблон.

## [2026-04-18] Task.company on_delete=SET_NULL создаёт osirоты после удаления компании

**Симптом**: РОП Оксана: «стали появляться компании без названия, в карточку нельзя зайти». На самом деле — в списке `/tasks/` колонка «Компания» показывает «—» (это **задачи** без company_id после SET_NULL), а не «компании без названия».

**Корень**: модель `Task.company` имеет `on_delete=SET_NULL`. При удалении компании у task `company_id → NULL`. В таблице задач показывается «—», нажать на «—» ничего не даёт (компания удалена).

**Фикс**: в `company_delete_direct` и `company_delete_request_approve` перед `company.delete()` явно `Task.objects.filter(company_id=company_pk).delete()[0]`. UI-подтверждение: «Все задачи и заметки этой компании будут удалены». Коммит `b7dcb21a`.

**Hotfix применён на прод** через `docker cp` 2026-04-18, staging E2E-тест пройден.

**Не затронуто**: 45 orphan-задач на проде (созданы пользователями без company_id с самого начала) — это нормальная manual-работа, не осиротевшие. Проверено через `ActivityEvent.meta.company_id`: 0 следов удалённых компаний, все 148 удалений за 60 дней имели `detached_count=0` (пользователи аккуратно удаляли задачи до компаний).

## [2026-04-18] F5 Weekly Rotation: два исторических кода Тюмени "tym" и "tmn"

**Симптом:** Новая логика `MultiBranchRouter._pick_common_pool_branch` (понедельная
ротация общих регионов) должна была ставить Тюмень на 3-й слот ротации (`W3 → tym`).
Но в проекте сосуществуют два кода Тюмени: в фикстуре `branch_regions_2025_2026.json`
— `"tym"`, в `seed_demo.py` и старых тестах `test_auto_assign.py` / `test_visibility.py`
— `"tmn"`. Использование `COMMON_POOL_ROTATION_ORDER = ("ekb", "krd", "tym")`
давало тонкое расхождение: на проде ротация шла по tym, в dev-seed — tmn уходил в
fallback-бакет и порядок зависел от id.

**Фикс** (`061432ae`): ротация перестроена на слоты-синонимы через
`COMMON_POOL_ROTATION_SLOTS = (("ekb",), ("krd",), ("tym", "tmn"))`. Код в каждом
слоте — синоним, маппится на один и тот же индекс ротации. Одинаковый порядок в
любом окружении. Тесты обновлены: убран `test_common_pool_picks_round_robin_branch`
(per-visit RR устарел), добавлены `test_common_pool_same_branch_within_same_week`
и `test_common_pool_weekly_rotation_cycles_branches` (W1-W4).

**Урок:** При введении списков-констант из «бизнес-правила» — всегда проверять
существующие БД-состояния (seed, фикстуры, старые тесты). Если кодов-синонимов
больше одного, использовать слоты, а не линейный порядок.

## [2026-04-18] Staging test env: SECURE_SSL_REDIRECT=True ломает widget-тесты

**Симптом:** Прогон `manage.py test messenger` на staging даёт 59 failures + 19 errors,
большинство — `AttributeError: 'HttpResponsePermanentRedirect' object has no attribute 'data'`
или `AssertionError: 301 != 403` в `test_widget_api.py`, `test_api_security.py`,
`test_widget_security_features.py`. После F5 weekly rotation commit — auto_assign
14/14 прошли, но широкие падения маскируют истинный статус регрессии.

**Причина:** В `settings_staging.py` установлено `SECURE_SSL_REDIRECT = True`.
Тесты делают `self.client.post(...)` без `secure=True` — middleware возвращает 301
редирект на https. Ответ не имеет `.data`, assertions на status code тоже падают.

**Решение (отложено в F11):** Создать `crm/settings_test.py`, наследующий от
`settings_staging.py`, с переопределением `SECURE_SSL_REDIRECT = False`,
`SECURE_HSTS_SECONDS = 0`, `SESSION_COOKIE_SECURE = False`. Прогон через
`DJANGO_SETTINGS_MODULE=crm.settings_test python manage.py test`. Локально через
`scripts/test.sh` проблема отсутствует (settings_dev). Зафиксировано как отдельная
задача в F11 (CI/CD + security hardening).

**Урок:** Прогон регрессии на staging должен использовать изолированные test-settings,
а не реальный staging-config — security middleware rewrites ломают HTTP-клиент DRF.

## [2026-04-16] Migration: дубль _like индекса при AddField + AlterField(unique=True)

**Симптом:** На staging при `migrate` — `ProgrammingError: relation "phonebridge_mobileappqrtoken_token_hash_8a962ef6_like" already exists`. Миграция `phonebridge.0010_qr_token_hash` падала на шаге 3 (AlterField с `unique=True`).

**Причина:** Шаг 1 (AddField) содержал `db_index=True`, который создавал `_like` индекс для CharField. Шаг 3 (AlterField с `unique=True, db_index=True`) пытался создать тот же `_like` индекс повторно. Дополнительно, шаг 4 был явным AddIndex, который дублировал уже существующий unique index.

**Фикс** (`dd23bea`): убран `db_index=True` из шага 1 AddField (unique в шаге 3 сам создаёт индекс). Удалён шаг 4 (лишний AddIndex). Также удалены дубли в `Meta.indexes` модели — `unique=True` автоматически создаёт индекс, явные `models.Index` для `token` и `token_hash` были избыточны. Создана миграция `0011_remove_duplicate_qr_indexes` для очистки.

**Урок:** При миграции «добавить поле → data migration → сделать unique» — не ставить `db_index=True` на шаге AddField, если следующим шагом будет AlterField с `unique=True`. Unique constraint сам создаёт все нужные индексы.

## [2026-04-16] Отчёты cold_calls: JsonResponse вместо HTML, сломанный фильтр

**Симптом:** Кнопки «Отчет: день/месяц» на дашборде открывали страницу с сырым JSON вместо отформатированного отчёта. Ссылка «Все без задач» показывала все компании вместо компаний текущего пользователя (неправильный GET-параметр `no_active_tasks=1` вместо `task_filter=no_tasks`).

**Причина:** `cold_calls_report_day` и `cold_calls_report_month` возвращали `JsonResponse` напрямую — HTML-шаблоны не были подключены. Фильтр `no_active_tasks` не существовал в `_apply_company_filters`, правильный параметр — `task_filter=no_tasks`.

**Фикс:** Views переведены на `render()` с v2-шаблонами `cold_calls_day.html` и `cold_calls_month.html`. Добавлен счётчик выполненных задач (`tasks_done`). Ссылки с дашборда получили правильные параметры фильтрации + `responsible=user.id`.

## [2026-04-16] Dashboard: N+1 запросы, мёртвый SSE, дублированная логика poll

**Симптом:** Аудит дашборда выявил 3 взаимосвязанных проблемы: 1) до 48 лишних SQL-запросов из-за deferred field access (`.only()` без нужных полей при `select_related`), 2) мёртвый SSE endpoint `dashboard_sse` с бесконечным `while True: time.sleep(5)` — каждое подключение навсегда блокировало gunicorn worker, 3) `dashboard_poll` дублировал 170 строк логики из `_build_dashboard_context`, хотя клиент использовал только `{updated: true/false}` → `location.reload()`.

**Причина:** Исторически poll строил полный JSON-ответ с задачами и договорами, но клиентский JS никогда не использовал эти данные — просто перезагружал страницу. SSE endpoint был добавлен как альтернатива, но не подключён нигде в шаблонах.

**Фикс** (`c27f3fd`): 1) Добавлены `assigned_to`, `is_urgent`, `company__address`, `company__work_timezone` в `.only()` + `select_related("assigned_to")`. 2) Удалён `dashboard_sse` (view, URL, import, StreamingHttpResponse). 3) `dashboard_poll` сокращён до EXISTS-проверки (2 SQL вместо ~15).

## [2026-04-16] Gunicorn кэширует скомпилированные шаблоны в воркерах

**Симптом:** После `git pull` + `docker compose up -d web` на staging баннер «Preview редизайна» то появлялся, то исчезал при обновлении страницы.

**Причина:** `up -d` не перезапускает контейнер, если конфигурация Docker не менялась. Разные gunicorn workers держат разные версии скомпилированных шаблонов в памяти.

**Фикс:** При деплое шаблонов — `docker compose restart web`, а не `up -d web`.

## [2026-04-15] v2 task partial — 500 Internal Server Error при открытии задачи в модалке

**Симптом:** На staging после деплоя редизайна Фаза 2 клик по карточке
задачи на дашборде или в /tasks/ → модалка показывала «Не удалось
загрузить (500)». В логах Django — `TemplateSyntaxError: Invalid
filter: 'full_name'` при рендере `ui/_v2/task_view_partial.html`.

**Причина:** Фильтр `|full_name` живёт в templatetags-модуле
`accounts_extras.py` (`@register.filter(name="full_name")`). Шаблоны
`task_view_partial.html` и `task_create_partial.html` подгружали
только `{% load ui_extras %}`, а `accounts_extras` не был загружен —
поэтому Django не находил фильтр и падал в compile_nodelist.

**Фикс** (`821f568`): добавил `{% load accounts_extras %}` в оба
partial в той же первой строке.

**Второй баг, всплывший после фикса первого** (`14d63e5`):
`task_create_v2_partial` (GET) → `FieldError: Field Company.responsible
cannot be both deferred and traversed using select_related`. Причина:
`_editable_company_qs(user)` внутри делает `select_related("responsible")`,
а сверху накладывался `.only("id","name")` без поля responsible —
Django требует, чтобы `responsible` либо не был defer'ед, либо не
использовался в select_related. Убрал `.only()` в `_v2_task_create_get`
и в POST 422 branch. При limit [:500] оверхед приемлем.

**Урок:** Перед merge фич с новыми шаблонами/view — прогонять
smoke-тест через Django Client (`Client.get(url, secure=True,
HTTP_HOST=...)`). TemplateSyntaxError падает только в рантайме, pytest/
линтер их не ловит. Я надеялся на визуальный тест пользователя и
пропустил обе ошибки.

---

## [2026-04-15] Блок «Договоры» на дашборде пустой у seed-пользователя sdm

**Симптом:** После `python manage.py seed_demo_data --user sdm --clear`
блок «Договоры» на `/v2/dashboard/` оставался с плейсхолдером «Нет
предупреждений», хотя команда сообщала «Обновлено договоров: 5».

**Причина:** В `seed_demo_data.py` выбор компаний фильтровался по
`Company.objects.filter(responsible=user)[:30]`. Если у user нет
компаний в владении (частый случай на чистой staging БД), срабатывал
fallback на компании по branch / все. Затем контракты ставились на
эти случайные компании — но блок «Договоры» на дашборде фильтрует
строго по `responsible=user`, и набор оказывался пустым.

**Решение:** При проставлении `contract_until` на contract target
компаниях команда форсит `c.responsible = user` (если владелец не
совпадает) и сохраняет оба поля. Теперь блок гарантированно
показывает созданные договоры. Коммит `6616287`.

**Предотвращение:** seed-команды должны читать фильтры дашборда,
а не полагаться на «случайно подойдёт». Если блок фильтрует по X,
seed обязан X проставить.

## [2026-04-15] v2-hover-кнопки «В работу»/«Выполнить» появлялись резко

**Симптом:** На `/v2/dashboard/` при наведении на строку задачи снизу
резко появлялись две кнопки «В работу» и «Выполнено» (injected JS-ом
через `querySelectorAll('a.v2-item[href*="/tasks/"]')`). Визуально
раздражало, площадь клика непредсказуемая, не работало на
тач-устройствах.

**Решение:** Заменено на **круглый чекбокс слева** от строки
(всегда видимый, не появляется при hover). Клик по нему → confirm
«Отметить задачу как выполненной?» → POST `/tasks/<id>/status/` с
`status=done` → плавный fade-out строки → reload. Клик по остальной
части строки открывает модалку просмотра задачи (v2_modal).
Чекбокс добавлен и на `/tasks/`: цветной `.v2-item__dot` в строке
стал кликабельным (hover ring + transform scale), без изменения
grid-layout таблицы. Коммиты `6616287`, `c20d9a6`.

**Побочный эффект:** интерфейс стал более пригодным для возрастных
менеджеров — действие явное, подтверждение обязательное, область
клика большая и стабильная.

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
