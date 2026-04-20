---
tags: [runbook, прод, аудит, day3-deep, tech-debt]
created: 2026-04-20
mode: read-only
---

# Day 3 Deep Dive — 5 новых находок про tech debt и реальный паттерн использования

## TL;DR

| # | Находка | Severity | Решение |
|---|---------|----------|---------|
| 1 | **`ui/views/` — 8 god-файлов** (суммарно 15 207 строк, `company_detail.py` = 2883 строки) | 🔴 **MAJOR tech debt** | Рефакторинг в service layer в Релизе 2 |
| 2 | **`company_detail.html` — 8781 строка HTML** | 🔴 | Замена на v3/b (уже есть — 1812 строк) + разбиение на partials |
| 3 | **Nginx access log БЕЗ `$request_time`** | 🟡 | 1-строчный фикс в `nginx.conf`, Релиз 0 |
| 4 | **Паттерн использования — 90% трафика = polling** (50% mail-progress, 40% notifications) | 🟡 | Релиз 2: SSE конвертация, **10× снижение нагрузки** |
| 5 | **Policy coverage ≈ 39% декораторами** (90 `@policy_required` на 231 views) | 🟢 | Запустить `audit_policy_coverage.py` → дописать недостающие |

Бонус: media крошечный (9 MB), Celery beat разумный, policy engine правильно спроектирован.

---

## 1. God-файлы в `ui/views/` — MAJOR tech debt

Общий объём: **15 207 строк** Python в 16 файлах.

| Файл | LOC | KB | Что там |
|------|----:|---:|---------|
| **`company_detail.py`** | **2 883** | **140** | view-handlers + бизнес-логика + serialization + DB queries |
| **`tasks.py`** | **2 215** | 109 | task CRUD + recurrence + assignee logic |
| `settings_core.py` | 1 581 | 70 | настройки профиля + branches + roles |
| `settings_integrations.py` | 1 487 | 71 | AmoCRM + webhooks + API tokens |
| `company_list.py` | 1 487 | 74 | список + фильтры + экспорт |
| `dashboard.py` | 1 282 | 56 | главная с метриками |
| `_base.py` | 1 158 | 49 | общая логика views |
| `settings_messenger.py` | 1 069 | 46 | messenger inbox setup |
| `reports.py` | 595 | 28 | cold-calls reports |

**Best practice Django**: views **100-300 LOC**. Если больше — выделять в service layer / CBV.

### Пример tech debt: `company_detail.py`

2883 строки в одном файле означают:
- При изменении одного поля модели — разбираться в 140 KB
- Сложность code review — огромная
- N+1 query risk — высокий (сложно увидеть все `.objects.*` в одном месте)
- Тестирование — сложное, mock-инг сотен переменных
- Merge conflicts — неизбежны при параллельной работе

### Рефакторинг-рецепт (для Релиза 2)

1. Создать `companies/services/company_detail_service.py` — чистые функции без Django request
2. Создать `companies/serializers/company_detail.py` — DRF-сериалайзеры (или attrs/pydantic)
3. В `ui/views/company_detail.py` оставить только: parse request → call service → render response
4. Ожидаемый размер после рефакторинга: **~400 LOC**
5. Покрытие тестами → возможно без `Client`, прямо через service

**Оценка работ**: 3-5 дней на один god-файл. Всего 8 файлов × 3-5 дней = **1-2 месяца** рефакторинга. Можно делать **параллельно** редизайну, т.к. внутренний API не меняется.

## 2. Шаблоны — `company_detail.html` монстр 8781 строка

Топ HTML-шаблонов:
| Шаблон | LOC |
|--------|----:|
| **`company_detail.html`** (classic) | **8 781** |
| `base.html` | 3 740 |
| `company_detail_v3/b.html` (v3/b) | 1 812 |
| `preferences.html` | 1 058 |
| `company_detail_v3/_inline_edit.html` | 1 059 |
| `task_list_v2.html` | 1 009 |

**8 781 строк Django-шаблона** — **в 10 раз больше нормы**. Шаблоны должны разбиваться на **partials** (`{% include %}`) по 100-200 строк.

### Хорошие новости

- **v3/b = 1812 строк** — в **4.8 раза меньше** classic. Значит редизайн v3/b уже решает проблему.
- `_inline_edit.html` — 1059 строк (partial для inline-редактирования) — тоже кандидат на разбиение.

В Релизе 1 v3/b появится на проде как preview. В Релизе 2 **classic заменяется на v3/b** → -7000 строк HTML долга, одним махом. Это то, что я видел в TodoWrite памяти — «этап 6 замены classic на v3/b».

## 3. Nginx log без `$request_time` — слепой performance

Текущий log_format:
```
91.230.154.82 - - [20/Apr/2026:00:00:06 +0000] "GET /mail/progress/poll/ HTTP/2.0" 200 98 "https://..." "Mozilla/..."
```

**Нет**:
- `$request_time` — полное время обслуживания запроса
- `$upstream_response_time` — время ответа upstream (Django web)
- `$upstream_connect_time`, `$upstream_header_time` — детали

**Без этого** невозможно:
- Найти медленные endpoints
- Понять, где узкое место (nginx / Django / БД)
- Построить SLO/SLI для мониторинга

### Фикс (1 минута, Релиз 0)

В `/etc/nginx/nginx.conf` добавить в `http {}`:
```
log_format perflog '$remote_addr - $remote_user [$time_local] '
                   '"$request" $status $body_bytes_sent '
                   '"$http_referer" "$http_user_agent" '
                   'rt=$request_time uct=$upstream_connect_time '
                   'uht=$upstream_header_time urt=$upstream_response_time';

access_log /var/log/nginx/access.log perflog;
```

`nginx -t && nginx -s reload`. Downtime = 0. Через день будут данные для анализа.

## 4. Паттерн использования CRM: reactive, не proactive

Из последних **50 000 запросов к прод-nginx**:

| Endpoint | Запросов | Доля | Тип |
|----------|---------:|-----:|-----|
| `/mail/progress/poll/` | **24 301** | 50% | polling |
| `/notifications/poll/` | **19 075** | 40% | polling |
| `/api/dashboard/poll/` | 761 | 1.5% | polling |
| `/cable` (Chatwoot WS) | 499 | 1% | WebSocket |
| `/companies/<UUID>/` | 390 | 0.8% | **реальная работа** |
| `/api/v1/widget/*` (Chatwoot) | 1 221 | 2.4% | интеграция |
| `/tasks/<UUID>/edit/` | 179 | 0.4% | **реальная работа** |
| `/tasks/<UUID>/` | 135 | 0.3% | **реальная работа** |

**91.5% запросов — это polling**. Только **1.5% — реальная работа менеджеров** (открытие карточек и задач).

### Что это значит

- Каждый поллинг = **SQL query + Redis hit + Gunicorn thread**
- При 50 пользователях × 4 poll'ов/минуту (mail + notifications + dashboard + cable) = 200 rpm фонового «шума»
- Реальной пользовательской работы (клик → просмотр → правка) меньше **в 60 раз**

### Паттерн поведения менеджеров

**Reactive** (реагируют на уведомление):
1. Poll видит новое уведомление → менеджер переключается
2. Открывает карточку компании (1 клик)
3. Смотрит, редактирует, возвращается
4. Ждёт следующего уведомления

**Не proactive** (не разгребают свой backlog):
- `/companies/` (список) почти не открывают (отсутствует в топ-20)
- `/tasks/` (список) — только 20 запросов в 50 000
- `/reports/*` — 0-1 запросов

### Вывод

CRM используется как **inbox**, а не как **workflow tool**. Это значит:
- Дашборд должен быть **главной точкой входа** — показывать ВСЁ, что требует внимания, в одном месте
- Уведомления должны быть **надёжными** (SSE, не polling)
- Редизайн Релиза 2 должен усиливать именно этот паттерн — **"открыл CRM → сразу видно, что делать"**

### Конкретный выигрыш от Polling → SSE

- Нагрузка: -90% SQL queries, -90% Redis hits, -90% gunicorn threads
- Латентность уведомлений: 0-5 секунд → **мгновенно**
- Свободные ресурсы для реальной работы (быстрее поиск, быстрее dashboard)

**3-5 дней работы в Релизе 2** на конверсию. ROI очень высокий.

## 5. Policy engine — правильно спроектирован, покрытие ~39%

### Схема `policy_policyrule`

```
id                    (bigint PK)
enabled               (bool)
priority              (int)
subject_type          (role | user)       ← два источника
role                  (manager / tenderist / sales_head / branch_director / group_manager / admin)
resource_type         (page, ...)
resource              (ui:dashboard, ui:companies:list, ...)
effect                (allow | deny)
conditions            (JSONB — ABAC)
user_id               (FK user, nullable)
```

Это **RBAC + ABAC гибрид**:
- **RBAC** (role-based): `role=manager` получает `ui:dashboard allow`
- **ABAC** (attribute-based): через `conditions JSONB` — например, `{"branch": "tyumen"}`
- **Per-user overrides**: через `subject_type=user, user_id=...`
- **Priority**: порядок применения правил

**Это правильный дизайн**. Современный, масштабируемый, гибкий.

### Индексы (9 штук) — все полезные
- `(enabled, resource_type, resource)` — главный lookup
- `(subject_type, user_id)` — user overrides
- `(subject_type, role)` — role lookup
- `priority` — sorting
- `effect` — фильтрация
- Плюс `_like` для pattern matching

**Дублей нет.** Не путается.

### Coverage декораторами

В коде:
- **90 случаев `@policy_required`** в 8 view-файлах (из 16)
- **231 view-функция/класс** в 15 view-файлах
- **Ratio: 39%**

Это **не значит, что 61% не защищены**. Часть защищается через:
- Базовые классы в `_base.py` (36 вхождений) — CBV с `LoginRequiredMixin` и т.п.
- Middleware-слой (`accounts.middleware.RateLimitMiddleware`)
- `require_admin` decorator
- Проверки внутри функций (runtime `if request.user.role != ...`)

**Действие для Релиза 1**:
1. Запустить `scripts/audit_policy_coverage.py` на staging (я не смог из-за путей) — получить детальный отчёт
2. Добавить `@policy_required` на views без защиты
3. Сгенерировать **матрицу «роль × endpoint × allow/deny»** → покажет дырки

Подозрение: самые уязвимые места — это **legacy-views в `company_detail.py`** (2883 строки, 43 `@policy_required`) — но как минимум декораторы там активно используются.

## 6. Celery beat schedule — разумный, без дублей

11 задач:

**Частые**:
- `send-pending-emails`: каждую минуту
- `sync-smtp-bz-quota`: 5 мин (лимит 15 000 писем/день, мониторинг)
- `sync-smtp-bz-unsubscribes`: 10 мин
- `sync-smtp-bz-delivery-events`: 10 мин
- `reconcile-mail-campaign-queue`: 5 мин
- `clean-old-call-requests`: 1 час

**Суточные**:
- `reindex-companies-daily` в **00:00** — полный rebuild FTS. При 45 709 компаниях может занять 5-15 минут. Следить в логах.
- `generate-recurring-tasks` в 06:00 — создание повторений. Это то, что ломается в 7 падающих тестах.

**Недельные** (воскресенье 03:00-03:30):
- `purge-old-activity-events` — retention 180 дней на 9.5M ActivityEvent. **Это тяжёлая** задача.
- `purge-old-error-logs`
- `purge-old-notifications`

### Вывод

Схема **хорошо продумана**: ночью — тяжёлое, днём — лёгкое polling. Никаких task overlap или зависимых друг от друга.

**Единственная тема**: нет задачи **cleanup orphan-attachments** (171 ошибка в январе про missing files). Добавить в Релиз 2:
```python
@shared_task
def cleanup_orphan_attachments():
    # Удаляет файлы из media/, которые не имеют ссылок в БД
    pass
```

## 7. Media — крошечный и простой

- **9.3 MB** всего на диске прода
- **32 файла**
- **8** из них — реальные `companynote.attachment` в БД
- **~24 потенциальных orphan** (возможно legacy)

**Выводы**:
- Attachments — **маленькая фича**, используется редко
- Нет смысла выносить в S3 / CDN
- Orphan cleanup — косметика
- `mailer_campaignattachment` модели **уже не существует** (прошлый разработчик её удалил) — значит январские ошибки были из-за этого

## Обновлённый roadmap tech-debt

### Квик-фиксы (Релиз 0, эта неделя)
- nginx `log_format perflog` с `$request_time` / `$upstream_response_time`
- `pg_stat_statements` установить
- Запустить `audit_policy_coverage.py` → список недостающих декораторов

### Релиз 1 (1-2 недели)
- Main → prod (v3/b preview появится)
- Включить messenger flag'ом
- Готово.

### Релиз 2 (2-3 месяца)
1. **Заменить classic `company_detail.html` на v3/b** → -7000 строк HTML
2. **Polling → SSE** на `/notifications/` и `/mail/progress/` → -90% нагрузки
3. **Рефакторинг god-views** в service layer (начать с `company_detail.py`) → 1-2 месяца параллельно редизайну
4. **Очистка**: 45 пустых контактов + 298 orphan + orphan media + dead FTS indexes
5. **Фикс 20 падающих тестов**
6. **Android Release** (параллельно, 1-2 мес полировки)

### Пост-Релиз 2 (6+ месяцев)
- ADR «Frontend modernization: Htmx + Alpine + Vite» (если решим)
- Sentry free + UptimeRobot + GitHub Actions deploy
- Подключение Firebase Performance в Android

## Аудитор

Senior onboarding Day 3 Deep Dive, 2026-04-20.
Read-only. Ни одного изменения на проде.
