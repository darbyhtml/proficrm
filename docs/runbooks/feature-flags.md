# Runbook: Feature Flags

_Wave 0.3 (2026-04-20). Инфраструктура: django-waffle 5.0.0. Обёртка:
`backend/core/feature_flags.py`. Архитектурный контракт: `docs/architecture/feature-flags.md`._

---

## Что такое feature flag в нашем проекте

**Определение.** Именованный switch в БД (таблица `waffle_flag`), который
меняет поведение кода без деплоя. Нужен когда фича потенциально ломает прод —
её выкатывают за флагом, включают процентно, откатывают через admin за 30 сек.

**Типы по назначению:**
- **Kill-switch** — мгновенное выключение функционала после инцидента
- **Phased rollout** — включение на 10% → 50% → 100% пользователей
- **Migration toggle** — soft→mandatory за 2 недели (пример: TWO_FACTOR)
- **Shadow mode** — фича работает но не влияет на UX (пример: POLICY_DECISION_LOG_DASHBOARD)

**Чем НЕ является feature flag:**
- **Не конфиг-переменная**: если значение не меняется между deploy'ами — это `settings.*`, не флаг.
- **Не permission**: если проверка основана на роли пользователя — это Policy Engine, не флаг.
- **Не A/B эксперимент**: waffle умеет A/B, но для этого нужна отдельная analytics-инфраструктура.

---

## Как добавить новый флаг

### Шаг 1: Именование

Имя — `UPPER_SNAKE_CASE`, описательное. Плохо: `flag1`, `new_ui`. Хорошо:
`EMAIL_BOUNCE_HANDLING`, `UI_V3B_DEFAULT`.

Примечание: в W0.3 были рассмотрены `policy_engine_enforce` (env-var, не флаг)
и `android_phonebridge_v2` (слишком далеко, отложено до W7). Не плодим
спекулятивные флаги.

### Шаг 2: Добавить константу в `core/feature_flags.py`

```python
#: Wave N — короткое описание. Consumer: backend/foo/views.py.
MY_NEW_FLAG = "MY_NEW_FLAG"
```

И в `KNOWN_FLAGS`:
```python
KNOWN_FLAGS: tuple[str, ...] = (
    UI_V3B_DEFAULT,
    TWO_FACTOR_MANDATORY_FOR_ADMINS,
    POLICY_DECISION_LOG_DASHBOARD,
    EMAIL_BOUNCE_HANDLING,
    MY_NEW_FLAG,  # <<< добавить
)
```

### Шаг 3: Data-миграция

```bash
cd backend
python manage.py makemigrations core --name="add_my_new_flag" --empty
# Отредактировать результирующий файл:
```

```python
# backend/core/migrations/0002_add_my_new_flag.py
from django.db import migrations


def add_flag(apps, schema_editor):
    Flag = apps.get_model("waffle", "Flag")
    Flag.objects.update_or_create(
        name="MY_NEW_FLAG",
        defaults={
            "everyone": False,  # ВСЕГДА off при создании
            "note": "Wave N. Описание назначения. Consumer: путь/к/views.py.",
        },
    )


def remove_flag(apps, schema_editor):
    Flag = apps.get_model("waffle", "Flag")
    Flag.objects.filter(name="MY_NEW_FLAG").delete()


class Migration(migrations.Migration):
    dependencies = [("core", "0001_initial_feature_flags")]
    operations = [migrations.RunPython(add_flag, remove_flag)]
```

### Шаг 4: Использование в коде

**В view/service:**
```python
from core.feature_flags import is_enabled, MY_NEW_FLAG

if is_enabled(MY_NEW_FLAG, user=request.user):
    return do_new_thing()
return do_old_thing()
```

**В template:**
```django
{% load feature_flags %}

{% feature_flag "MY_NEW_FLAG" as ff %}
{% if ff %}<div>Новое</div>{% else %}<div>Старое</div>{% endif %}
```

**В DRF ViewSet:**
```python
from core.feature_flags import MY_NEW_FLAG
from core.permissions import FeatureFlagPermission

class MyViewSet(ViewSet):
    permission_classes = [IsAuthenticated, FeatureFlagPermission]
    feature_flag_required = MY_NEW_FLAG
```

### Шаг 5: Обновить документацию

Добавить в `docs/architecture/feature-flags.md` — таблицу активных флагов
с колонками: имя, когда создан, в какой волне консумится, consumer-пути.

### Шаг 6: Деплой

```bash
git add backend/core/feature_flags.py backend/core/migrations/000N_*
git commit -m "Feat(Core): new feature flag MY_NEW_FLAG (Wave N)"
git push origin main
# CI прогонит tests_feature_flags.py — там должна быть проверка
# MigrationSeedTests.test_all_four_initial_flags_exist (обновить на N+1).
```

---

## Как включать флаг (percentage rollout)

### Через Django admin

1. `https://crm.groupprofi.ru/admin/waffle/flag/` (только ADMIN)
2. Кликнуть на флаг → поля:
   - **Everyone**: `Unknown` (percent rollout), `Yes` (всем), `No` (никому)
   - **Percent**: число 0-100 — процент юзеров по hash(session_id)
   - **Users**: список явных включённых (для QA)
   - **Groups**: Django groups (для ролей — у нас не используется, заменено policy)
   - **Authenticated**: только залогиненным
   - **Staff**: только is_staff
   - **Superusers**: только is_superuser
   - **Testing**: включает `?dwft_FLAGNAME=1` в URL (мы отключили через `WAFFLE_OVERRIDE=False`)
3. Сохранить. Waffle кеширует — эффект через 5-10 секунд.

### Стратегия rollout

Стандартный сценарий:
1. **Day 0**: Everyone = No, Percent = None → флаг off везде. Сбор metrics baseline.
2. **Day 1**: Users = [qa_user_1, qa_user_2] → внутреннее QA.
3. **Day 2-3**: Everyone = Unknown, Percent = 10 → 10% рандомных залогиненных.
   Мониторим Sentry/GlitchTip: рост error rate?
4. **Day 4-5**: Percent = 50 → половина юзеров.
5. **Day 6**: Everyone = Yes → все.
6. **Day 14** (после стабилизации): удалить код `if is_enabled(...):`, удалить флаг.

**Правило**: флаг должен жить **не больше 30 дней**. Либо включаем навсегда
и чистим код, либо откатываем и удаляем флаг. «Зависшие» флаги — источник
long-tail багов («А у меня почему-то старое меню показывается!»).

---

## Как выключать (kill-switch)

### Плановое — через admin

`Everyone = No`. Эффект через 5-10 сек.

### Срочное — через env var (без захода в admin)

```bash
ssh root@prod-server
cd /opt/proficrm
# 1. Добавить в .env:
echo 'FEATURE_FLAG_KILL_UI_V3B_DEFAULT=1' >> .env
# 2. Перечитать env (без restart — чтобы прогресс юзеров не потерять):
docker compose up -d web  # re-create с новым env
```

Эффект — **мгновенный** (первый же запрос после перезапуска читает env).
Наша обёртка `is_enabled()` проверяет env **первым**, до обращения к БД/кешу.

Используется когда admin недоступен (БД лежит, токен потерян) или нужна
реакция быстрее 10 сек.

### Полное удаление флага

```python
# backend/core/migrations/000M_remove_my_old_flag.py
def remove_flag(apps, schema_editor):
    Flag = apps.get_model("waffle", "Flag")
    Flag.objects.filter(name="MY_OLD_FLAG").delete()

class Migration(migrations.Migration):
    dependencies = [("core", "000M-1_previous")]
    operations = [migrations.RunPython(remove_flag, migrations.RunPython.noop)]
```

Плюс:
- Удалить `MY_OLD_FLAG` из `KNOWN_FLAGS` и констант в `feature_flags.py`.
- Удалить все `is_enabled("MY_OLD_FLAG")` проверки — выбрать одну ветку.
- Удалить тесты на флаг.

---

## Мониторинг

### Какие флаги активны (в runtime)

```bash
# На сервере:
docker compose exec web python manage.py shell -c "
from waffle.models import Flag
for f in Flag.objects.all():
    print(f'{f.name:40s} everyone={f.everyone} percent={f.percent}')
"
```

### API endpoint

```bash
# Для любого залогиненного юзера:
curl -b cookies.txt https://crm.groupprofi.ru/api/v1/feature-flags/
```

Ответ:
```json
{
    "UI_V3B_DEFAULT": false,
    "TWO_FACTOR_MANDATORY_FOR_ADMINS": false,
    "POLICY_DECISION_LOG_DASHBOARD": false,
    "EMAIL_BOUNCE_HANDLING": false
}
```

### GlitchTip/Sentry tags

С Wave 0.4 (observability) в каждой error-ноте будут tags:
`feature_flag.UI_V3B_DEFAULT: true/false` — чтобы отличать ошибки старого
UI от нового.

---

## Тестирование

### В unit-тестах

Используйте `waffle.testutils.override_flag` (не `Flag.objects.update`!).
Причина: `update()` обходит `post_save` signal → waffle-cache не
инвалидируется → тесты флакают.

```python
from waffle.testutils import override_flag

class MyTest(TestCase):
    @override_flag('UI_V3B_DEFAULT', active=True)
    def test_new_ui_rendered(self):
        ...

    # Или context manager:
    def test_both_paths(self):
        with override_flag('MY_FLAG', active=True):
            ...
        with override_flag('MY_FLAG', active=False):
            ...
```

### В Playwright E2E

```javascript
// tests/e2e/helpers.js
async function setFlag(page, flagName, active) {
    await page.request.post('/admin/waffle/flag/.../change/', {
        data: { everyone: active ? 'Yes' : 'No' }
    });
    // Ждём invalidation cache (5 сек).
    await page.waitForTimeout(5_500);
}
```

---

## Что делать если флаг застрял включённым

1. Проверить `FEATURE_FLAG_KILL_<NAME>` в env — может быть случайно выставлен.
2. Проверить `waffle_flag.everyone` в БД — может быть явно `True`.
3. Проверить waffle-cache: `redis-cli KEYS 'waffle:*' | head`.
4. Проверить что ваш код действительно использует `is_enabled()`, а не
   старый хардкод.

---

## Связанные документы

- **Архитектурный контракт**: `docs/architecture/feature-flags.md` — таблица
  всех флагов с описанием consumer'ов.
- **ADR-002**: `docs/decisions.md` — выбор django-waffle vs альтернатив.
- **План волн**: `docs/plan/01_wave_0_audit.md` §0.3.
- **Upstream**: https://waffle.readthedocs.io/en/stable/
