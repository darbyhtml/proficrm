# Feature Flags — архитектурный контракт

_Wave 0.3 (2026-04-20). Backend: django-waffle 5.0. Обёртка: `backend/core/feature_flags.py`._

## Активные флаги

Таблица — **единственный источник правды** о существующих флагах проекта.
При добавлении нового флага её обновить обязательно (см. `docs/runbooks/feature-flags.md`).

| Флаг | Создан | Консумится в волне | Default | Consumer (путь) | Политика rollout |
|------|--------|--------------------|---------|-----------------|------------------|
| `UI_V3B_DEFAULT` | W0.3 (2026-04-20) | W9 — UX унификация | `False` | Templates `ui/company_detail*.html`, views в `ui/views/company_detail.py` | 10% → 50% → 100% за 7 дней, после стабилизации удалить classic-код |
| `TWO_FACTOR_MANDATORY_FOR_ADMINS` | W0.3 (2026-04-20) | W2.4 — TOTP migration | `False` | `accounts/` login middleware + `accounts/views.py` | 2 недели soft (баннер) → `Everyone=Yes` mandatory на логин; recovery codes обязательны |
| `POLICY_DECISION_LOG_DASHBOARD` | W0.3 (2026-04-20) | W2 — Policy ENFORCE | `False` | `ui/views/settings_audit.py` (новый dashboard в W2) + Grafana panel | Включить за 2 недели до W2 ENFORCE, собирать данные, выключить после ENFORCE |
| `EMAIL_BOUNCE_HANDLING` | W0.3 (2026-04-20) | W6 — email polish | `False` | `mailer/views/webhooks.py` (новый endpoint) + `mailer/tasks.py` (IMAP-poller fallback) | Включать после реального теста с smtp.bz webhook; сразу Everyone=Yes если тесты зелёные |

**Статус на 2026-04-20**: все 4 флага созданы в БД (миграция
`core.0001_initial_feature_flags`), но выключены (`everyone=False`). Включение —
начиная с Wave 2 (POLICY_DECISION_LOG_DASHBOARD первым).

## Почему именно эти 4

### ✅ Принят: `UI_V3B_DEFAULT`

Обоснование: в W9 будет постепенная миграция карточки компании с classic UI
на v3/b. Переключать через `if is_enabled(UI_V3B_DEFAULT)` в views — безопаснее
чем через deploy двух бранчей. Процентное включение позволит одновременно
вести старый и новый UI на 50/50 для валидации.

### ✅ Принят: `TWO_FACTOR_MANDATORY_FOR_ADMINS`

Обоснование: W2.4 — внедрение TOTP 2FA для ADMIN/BRANCH_DIRECTOR. План:
2 недели «soft» период (баннер «рекомендуется»), затем mandatory на логине.
Flag включает этот переход без деплоя — просто в admin меняем `everyone=False`
на `everyone=True` в назначенный день (например, перед выходными с отменой).

### ✅ Принят: `POLICY_DECISION_LOG_DASHBOARD`

Обоснование: W2 — переход Policy Engine в ENFORCE. До этого 2 недели в shadow
mode (OBSERVE) — собираем данные. Нужен admin-dashboard «denied requests»
для принятия решения о готовности. Этот dashboard показывается только когда
флаг on — иначе это мусор в меню для обычных менеджеров.

### ✅ Принят: `EMAIL_BOUNCE_HANDLING`

Обоснование: W6.2 — либо webhook от smtp.bz, либо IMAP fallback (точно
узнаем в начале W6). Флаг нужен для:
1. Фаза разработки: endpoint/poller задеплоен, но **не активен** пока мы
   не проверим что smtp.bz реально шлёт webhook в нашем формате.
2. Kill-switch: если webhook начнёт генерить false-positives (невалидные
   bounce suppressions), выключаем за 30 секунд.

## Почему НЕ приняты другие кандидаты

### ❌ `POLICY_ENGINE_ENFORCE` — это env var, не флаг

**Требование из плана**: Wave 2 переход в ENFORCE должен иметь kill-switch
с reload < 10 секунд.

**Проблема с waffle**: waffle-cache имеет TTL 5-10 сек, но при реальной
нагрузке и синхронизации между workers — до 30 сек нестабильно.

**Решение**: `POLICY_ENGINE_ENFORCE=1` в systemd EnvironmentFile. `systemctl
daemon-reload && systemctl restart` — < 10 сек, детерминистично.

Но для dashboard-а (отдельная фича) взят waffle-флаг `POLICY_DECISION_LOG_DASHBOARD`.

### ❌ `ANDROID_PHONEBRIDGE_V2` — преждевременно

**Причина**: W7 запланирован после W6, а до W7 ещё ≥ 2 месяца. Флаг
на 2 месяца без использования — это «забытый» флаг, риск сюрприза (кто-то
включит случайно, никто не узнает).

**Решение**: создадим в начале W7 через новую миграцию `core.000X_add_android_v2`.

### ❌ `MEDIA_READ_FROM` — settings-based, не waffle

**Требование**: в W10.3 dual-write media (локально + S3), потом переключение
чтения.

**Проблема**: если переключение read-path сделать через waffle — при неудаче
запроса нет fallback на локал, чтение идёт "или S3, или локал" на уровне
каждого запроса → путаница в логах.

**Решение**: `MEDIA_READ_FROM=local|s3|dual` в settings.py (одно значение
на весь процесс). Переключение через deploy, не через runtime.

## Архитектура обёртки

```
┌────────────────────────────────────────────────────┐
│  core/feature_flags.py::is_enabled(flag, user=...) │
└────────────────┬───────────────────────────────────┘
                 │
  ┌──────────────┼──────────────┐
  ▼              ▼              ▼
┌──────────┐ ┌─────────┐ ┌──────────┐
│ env kill │ │  waffle │ │  fallback│
│ switch   │ │   DB    │ │  default │
│ (step 1) │ │ (step 2)│ │ (step 3) │
└──────────┘ └─────────┘ └──────────┘

step 1: FEATURE_FLAG_KILL_<NAME>=1  → return False (перекрывает всё)
step 2: Flag.objects.get(name=...) + waffle.flag_is_active
step 3: WAFFLE_FLAG_DEFAULT=False  → return False
```

### Три интерфейса использования

1. **Python-код**: `is_enabled(FLAG_NAME, user=..., branch=..., request=...)`
2. **Django templates**: `{% load feature_flags %}` + `{% feature_flag "NAME" as var %}` + `{% feature_enabled "NAME" %}...{% endfeature_enabled %}`
3. **DRF**: `permission_classes = [FeatureFlagPermission]` + `feature_flag_required = FLAG_NAME`

### JS-клиент (для SPA-like rendering)

`GET /api/v1/feature-flags/` отдаёт JSON со всеми известными флагами:
```json
{
    "UI_V3B_DEFAULT": false,
    "TWO_FACTOR_MANDATORY_FOR_ADMINS": false,
    "POLICY_DECISION_LOG_DASHBOARD": false,
    "EMAIL_BOUNCE_HANDLING": false
}
```

Пример использования на фронте:
```javascript
const flags = await fetch('/api/v1/feature-flags/').then(r => r.json());
if (flags.UI_V3B_DEFAULT) {
    document.body.classList.add('v3b-layout');
}
```

## Использование вне HTTP request (Celery, management commands, startup)

Waffle спроектирован вокруг HTTP-request: `waffle.flag_is_active(request, name)`.
Но у нас есть кейсы без `request`:

- Celery task (`@shared_task`): проверяет флаг внутри фоновой задачи
- Django management command (`python manage.py …`): точечные скрипты
- Startup-сигналы (`@receiver(post_migrate)`): не рекомендуется, но бывает

### Решение в `core.feature_flags.is_enabled()`

Обёртка поддерживает вызовы без `request`:

```python
# Из Celery task
from core.feature_flags import is_enabled, EMAIL_BOUNCE_HANDLING

@shared_task
def process_email_webhook(payload):
    if not is_enabled(EMAIL_BOUNCE_HANDLING):  # request=None
        logger.info("bounce handler off via feature flag, skipping")
        return
    # ... обработка
```

### Что происходит внутри при `request=None`

1. Если передан `user`, но нет `request` — конструируется **shim-request**:
   ```python
   req = HttpRequest()
   req.user = user
   req.COOKIES = {}
   req.META = {}
   return req
   ```
   Минимальный объект, достаточный для `waffle.flag_is_active`. Работают
   критерии `users`, `groups`, `authenticated`, `staff`, `superusers`,
   `percent` (через hash session_id — но session пуст, поэтому percent
   всегда даёт один и тот же hash для одного юзера).

2. Если нет ни `user`, ни `request` — **булев fallback**:
   - `Flag.everyone == True` → `True`
   - `Flag.everyone == False` → `False`
   - `Flag.everyone == None` (unknown) → `False` (WAFFLE_FLAG_DEFAULT)

### Ограничения shim-request

Что **НЕ работает** в Celery/management-режиме:
- **Cookie-based overrides** (`?dwft_<flag>=1`) — cookies пустые
- **Sample-флаги** с random per-request — будут давать one-fixed hash
- **Sticky session rollout** — sticky id берётся из session, которой нет

Эти ограничения **не проблема**, если флаги в Celery используются только
как kill-switch (on/off для всех) — это наш типичный сценарий. Percent
rollout в Celery имеет смысл только если таска разделяет работу по юзеру
(e.g. email-рассылка), тогда hash per-user даёт стабильный вариант.

### Альтернатива: булев helper для чистого on/off

Если нужен только on/off без учёта user:

```python
from core.feature_flags import is_enabled

# Безопасно для Celery, startup, management — передаём просто имя:
if is_enabled(EMAIL_BOUNCE_HANDLING):
    ...
```

Это вызывает путь «без user/request» → булев fallback по `Flag.everyone`.
Никакого shim-request не создаётся (lightweight, 0 overhead).

### Когда **не использовать** feature flag вне HTTP

- В `settings.py` — используйте env var (`os.getenv(...)`).
- В `post_migrate` signal — Django ещё не готов, lookup может упасть.
- В hot-path (>1000 req/sec) — даже кешированный waffle делает Redis-get;
  проще env var.

---

## Связи с другими документами

- `docs/runbooks/feature-flags.md` — операционные процедуры (как добавить/
  включить/выключить/мониторить).
- `docs/decisions.md` ADR-002 — обоснование выбора django-waffle vs альтернатив.
- `docs/plan/01_wave_0_audit.md` §0.3 — требования волны.
- `backend/core/feature_flags.py` — канонические константы и wrapper.
- `backend/core/migrations/0001_initial_feature_flags.py` — data-seed.
- `backend/core/tests_feature_flags.py` — 28 тестов, 92% coverage.

## История изменений

| Дата | Волна | Изменение |
|------|-------|-----------|
| 2026-04-20 | W0.3 | Создан документ. 4 начальных флага (UI_V3B_DEFAULT, TWO_FACTOR_MANDATORY_FOR_ADMINS, POLICY_DECISION_LOG_DASHBOARD, EMAIL_BOUNCE_HANDLING). |
