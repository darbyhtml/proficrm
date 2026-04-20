# Волна 0. Фундамент и аудит

**Цель волны:** Зафиксировать реальное состояние кодовой базы, закрыть tooling-пробелы, настроить baseline для объективных измерений прогресса. Это единственная волна, где ничего не строится — только измеряется и размечается. Без неё все последующие волны будут стрелять вслепую.

**Параллелизация:** нет. Идёт строго последовательно.

**Длительность:** 3–5 рабочих дней.

**Требования:** Доступ к staging и prod (только read-only для аудита). PostgreSQL-анализ через `docker compose exec db psql` (PostgreSQL MCP не подключён — работаем через CLI).

---

## Этап 0.0. Bootstrap минимального тулинга (20–30 минут)

### Контекст
Этап 0.1 требует инструментов (`radon`, `coverage`, `django-extensions`, `cloc`/`tokei`), которые настраиваются полно в Этапе 0.2. Чтобы не получить курицу-и-яйцо, делаем минимальный bootstrap.

### Цель
Поставить минимум, необходимый для запуска 0.1.

### Что делать
1. **System packages** (на dev-машине / в Docker dev-container):
   ```bash
   # Debian/Ubuntu
   sudo apt install -y tokei graphviz libgraphviz-dev
   # tokei = быстрая замена cloc (один бинарь, стабильно работает в Docker)
   ```
   Если `tokei` нет в репо — скачать бинарь с https://github.com/XAMPPRocky/tokei/releases.

2. **Python packages** (dev-requirements, не в prod):
   ```bash
   # Добавить в backend/requirements/dev.txt
   radon==6.0.1
   coverage==7.6.10
   django-extensions==3.2.3
   pygraphviz==1.14
   pip-audit==2.7.3
   pip install -r backend/requirements/dev.txt --break-system-packages  # или в venv
   ```

3. **Зафиксировать INSTALLED_APPS**:
   ```python
   # backend/crm/settings.py — только для DEBUG=True
   if DEBUG:
       INSTALLED_APPS += ["django_extensions"]
   ```

4. **Проверить готовность**:
   ```bash
   tokei backend/ --exclude 'migrations' --exclude '*.min.js'
   radon cc backend/ -a -s | head -20
   python manage.py graph_models --help | head -5
   coverage --version
   ```
   Все 4 команды должны работать без ошибок.

5. **Коммит**: `chore: bootstrap dev tooling for W0.1 audit` — один изолированный коммит с dev-requirements и settings change.

### Инструменты
- `Bash`
- `Read`, `Edit`, `Write`

### Definition of Done
- [ ] `tokei`, `radon`, `coverage`, `django-extensions`, `pygraphviz`, `pip-audit` установлены
- [ ] `python manage.py graph_models` отрабатывает без ошибок (даже если ERD ещё не нужен)
- [ ] `backend/requirements/dev.txt` обновлён
- [ ] Коммит отправлен, CI зелёный

### Артефакты
- `backend/requirements/dev.txt` (обновлён)
- `backend/crm/settings.py` (DEBUG-only INSTALLED_APPS)

### Валидация
```bash
tokei backend/ | tail -3
radon cc backend/core/models.py  # любой модуль
python manage.py graph_models --list-models | head -5
```

### Откат
`git revert` коммита. Ничего в проде не трогалось.

### Обновить в документации
- `docs/dev/getting-started.md` (пока stub, в W15 доработать) — раздел «Dev dependencies».

---

## Этап 0.1. Развёрнутый аудит кодовой базы

### Контекст
Проект — Django 6 CRM с ~66 моделями, 89 шаблонами, 1179 тестами. Есть known-issues: `ui/views/_base.py` на 1700 LOC, `ui/views/company_detail.py` на 2698 LOC, смешивание HTML и JSON в views, legacy-app `amocrm/`, phonebridge недописан. Нужно зафиксировать всё объективно.

### Цель
Составить объективную «As-Is» карту проекта + **coverage baseline** (стартовая точка для последующего gating).

### Что делать
1. **ПЕРВЫМ ДЕЛОМ — зафиксировать coverage baseline** (до всех subagents):
   ```bash
   cd backend
   coverage run --source='.' -m pytest --ignore=tests/e2e
   coverage report --skip-empty > ../docs/audit/coverage-baseline.txt
   coverage xml -o ../docs/audit/coverage-baseline.xml
   coverage html -d ../docs/audit/coverage-baseline-html/
   ```
   В `coverage-baseline.txt` — общий %, топ-20 модулей с низким покрытием, топ-10 с высоким (нужно для понимания формы).

   **Это число — стартовая точка для gating.** Зафиксировать в `pyproject.toml`:
   ```toml
   [tool.coverage.report]
   fail_under = 40  # поднять до baseline - 2% для безопасности
   ```
   Если baseline = 43% — `fail_under = 40`. Если baseline = 52% — `fail_under = 50`. Округляем вниз до ближайших 5%.

2. Запусти 5 параллельных sub-agents через `Agent` tool:
   - **Agent 1**: Инвентаризация моделей. Для каждой модели в `backend/*/models.py` собрать: имя, поля, FK/M2M, indices, constraints, связанные сервисы, покрытие тестами. Сохранить в `docs/audit/models-inventory.md`.
   - **Agent 2**: Инвентаризация views. Собрать по всем `views/*.py`: LOC, список endpoint'ов, return type (HTML / JSON / stream), наличие `@policy_required`, наличие `@login_required`, сложность (McCabe cyclomatic). Сохранить в `docs/audit/views-inventory.md`.
   - **Agent 3**: Инвентаризация Celery-задач. Список задач из `@app.task`, расписание из `beat_schedule`, queue, rate limits, retry policy, идемпотентность. Сохранить в `docs/audit/celery-inventory.md`.
   - **Agent 4**: Инвентаризация frontend. Все 89 шаблонов: какой экран, какой layout, какие JS-острова подключены, использует v2-токены или v3. Все файлы в `backend/static/` — размер бандла, минификация, дубликаты. Сохранить в `docs/audit/frontend-inventory.md`.
   - **Agent 5**: Инвентаризация API. Все DRF viewset'ы, serializers, permissions, pagination, throttling. Соответствие `drf-spectacular` схеме. Сохранить в `docs/audit/api-inventory.md`.

3. После завершения агентов — построй сводную карту `docs/audit/README.md` с top-20 tech-debt items по приоритету (вес = влияние × частота изменения × риск).

4. Собери остальные метрики через shell:
   ```bash
   tokei backend/ --exclude migrations --exclude '*.min.js' > docs/audit/loc-tokei.txt
   radon cc backend/ -s -a > docs/audit/complexity-cc.txt
   radon mi backend/ -s > docs/audit/maintainability-mi.txt
   pytest --co -q | wc -l > docs/audit/test-count.txt
   ```
   Свод — в `docs/audit/metrics-baseline.md`.

5. Прогон `django-extensions` `graph_models` чтобы получить свежий ERD:
   ```bash
   python manage.py graph_models --pygraphviz -a -g -o docs/audit/erd.png
   ```

6. **Policy @policy_required audit** (важно для W2 preconditions):
   ```bash
   grep -rn '@policy_required\|@require_policy\|check_policy' backend/ > docs/audit/policy-coverage.txt
   grep -rn 'def post\|def put\|def patch\|def delete' backend/ --include='views.py' --include='views/*.py' \
     | grep -v '@policy_required' > docs/audit/policy-gaps.txt
   ```
   Это даст явный список mutating endpoints без `@policy_required` — вход в W2.

### Инструменты
- `Agent` tool × 5 параллельных sub-agents
- `Bash` — `tokei`, `radon`, `coverage`, `docker compose exec db psql` (для pg_stat_user_tables, pg_indexes)
- `Read`, `Grep`, `Glob` — базовое

### Definition of Done
- [ ] Создана директория `docs/audit/` со всеми артефактами (см. ниже)
- [ ] **Coverage baseline зафиксирован** в `coverage-baseline.txt` + порог `fail_under` в `pyproject.toml`
- [ ] README содержит top-20 tech-debt items, каждый со score, обоснованием, предлагаемой волной для исправления
- [ ] Метрики зафиксированы с датой снапшота: LOC, число тестов, coverage %, среднее CC, MI, число N+1 (через `django-debug-toolbar` on staging)
- [ ] ERD-диаграмма читаема (не «слипшийся ком»), использует группировку по app
- [ ] `policy-gaps.txt` — список mutating endpoints без `@policy_required` готов для W2

### Артефакты
- `docs/audit/README.md`
- `docs/audit/models-inventory.md`
- `docs/audit/views-inventory.md`
- `docs/audit/celery-inventory.md`
- `docs/audit/frontend-inventory.md`
- `docs/audit/api-inventory.md`
- `docs/audit/metrics-baseline.md`
- `docs/audit/erd.png`
- `docs/audit/coverage-baseline.txt` + `coverage-baseline.xml` + `coverage-baseline-html/`
- `docs/audit/policy-coverage.txt` + `policy-gaps.txt`
- `docs/audit/loc-tokei.txt`, `complexity-cc.txt`, `maintainability-mi.txt`, `test-count.txt`
- `pyproject.toml` (обновлён: `fail_under = <baseline - 2%>`)

### Валидация
```bash
ls docs/audit/ | wc -l  # должно быть 8
test -s docs/audit/README.md  # непустой
grep -c "^- " docs/audit/README.md  # >= 20 top items
```

### Откат
Аудит — read-only, никаких изменений в коде. Rollback = удалить `docs/audit/`.

### Обновить в документации
- `docs/current-sprint.md`: зафиксировать «Wave 0 завершена, baseline установлен»
- `CLAUDE.md`: добавить ссылку на `docs/audit/README.md` как источник правды для tech-debt

---

## Этап 0.2. Настройка tooling baseline

### Контекст
Сейчас: ruff и gitleaks в CI, pip-audit есть. Нет: black (форматтер), mypy (статическая типизация), coverage threshold в CI, bandit (security scan), pre-commit hooks. Tooling — фундамент предсказуемости.

### Цель
Настроить полный набор статических проверок и pre-commit, чтобы ни одна последующая волна не добавляла новый код с нарушениями.

### Что делать
1. **ruff**: выверить конфиг `ruff.toml`. Цель — `ruff check .` без ошибок. Правила: E, F, I, N, UP, B, S (security), DJ (Django), RUF. Добавить `--fix` в pre-commit.

2. **black**: заменить ручное форматирование. Конфиг в `pyproject.toml`, line-length=100, target-version=py313. Прогнать на всём backend/, закоммитить «format: initial black pass».

3. **mypy**: добавить `mypy.ini`. Сначала не strict, только `check_untyped_defs=true`. Strict — точечно для новых модулей через `[mypy-backend.services.*]` → `strict=True`. Установить `django-stubs`, `djangorestframework-stubs`, `types-redis`, `celery-types`.

4. **bandit**: `bandit -r backend/ -ll --skip B101,B601`. Добавить в CI.

5. **coverage threshold**: `fail_under` уже установлен в 0.1 на основе baseline (округлено вниз до 5%, напр. 40 или 45). Здесь задача — закрепить в `pyproject.toml` **траекторию** повышения: комментарий в конфиге «+5% после каждой волны: W0→baseline, W1→+5, W2→+10, ..., W14→85». В CI порог читается из конфига — при повышении просто меняем число.

6. **pre-commit**: `.pre-commit-config.yaml` с:
   - ruff-check + ruff-format
   - black (только для `.py` вне `migrations/`)
   - mypy (только для изменённых файлов)
   - trailing-whitespace, end-of-file-fixer, check-yaml
   - detect-secrets (с baseline)
   - django-migration-checker (проверка наличия migration для изменённой модели)

7. **CI changes**: `.github/workflows/ci.yml` — добавить jobs:
   - `black --check`
   - `mypy backend/`
   - `bandit -r backend/`
   - `coverage run -m pytest && coverage report --fail-under=60`
   - `semgrep --config=auto backend/` (optional, если не тормозит)

8. **Makefile** на корне: `make lint`, `make test`, `make coverage`, `make ci` (запускает всё как в CI).

### Инструменты
- `mcp__context7__*` — актуальная документация ruff, mypy, django-stubs
- `Bash`, `Edit`

### Definition of Done
- [ ] `make lint` — зелёный
- [ ] `make ci` — зелёный
- [ ] `pre-commit install` работает, хуки срабатывают на тестовом коммите
- [ ] CI добавил 4 новых job, все зелёные
- [ ] `mypy backend/` показывает, сколько ошибок на baseline (зафиксировать в `docs/audit/mypy-baseline.md`)
- [ ] `coverage report` показывает ≥ 60%

### Артефакты
- `ruff.toml` / `pyproject.toml` (ruff section)
- `pyproject.toml` (black section)
- `mypy.ini`
- `.coveragerc`
- `.pre-commit-config.yaml`
- `.secrets.baseline`
- `.github/workflows/ci.yml` (updated)
- `Makefile`
- `docs/audit/mypy-baseline.md`

### Валидация
```bash
make ci
pre-commit run --all-files  # зелёный
```

### Откат
```bash
git revert <commit-sha>
pre-commit uninstall
```

### Обновить в документации
- `docs/architecture.md`: раздел «Tooling и качество кода»
- `docs/decisions.md`: ADR-001 «Tooling stack: ruff + black + mypy + bandit + coverage»

---

## Этап 0.3. Feature flags инфраструктура

### Контекст
В последующих волнах будет много поэтапных выкаток (UI v3/b, Policy ENFORCE, новые каналы уведомлений). Без feature flags каждая выкатка — это ночь деплоя + откат при проблемах. Нужен легковесный механизм флагов.

### Цель
Внедрить feature flags с поддержкой percentage rollout, per-user, per-role, per-branch, kill-switch.

### Что делать
1. Установить `django-waffle` (актуальная версия). Настроить миграции, admin.
2. Создать обёртку `backend/core/feature_flags.py`:
   ```python
   def is_enabled(flag: str, user=None, branch=None) -> bool: ...
   ```
   с fallback на env var для kill-switch.
3. В templates добавить tag `{% feature_flag "flag_name" %}...{% endfeature_flag %}`.
4. В DRF — permission class `FeatureFlagPermission`.
5. В JS — эндпоинт `/api/v1/feature-flags/` возвращает активные флаги для юзера (чтобы фронт мог условно рендерить).
6. Создать набор начальных флагов:
   - `ui_v3b_default` — для Wave 9
   - `policy_engine_enforce` — для Wave 2
   - `email_bounce_handling` — для Wave 6
   - `android_phonebridge_v2` — для Wave 7
7. Документация: `docs/runbooks/feature-flags.md` — как добавлять, как katit' percentage rollout, как kill-switch.

### Инструменты
- `mcp__context7__*` — документация django-waffle
- `mcp__postgres__*` — миграции

### Definition of Done
- [ ] django-waffle установлен, миграции применены
- [ ] Обёртка `core.feature_flags` покрыта тестами (≥ 90%)
- [ ] 4 начальных флага созданы в admin и выключены по умолчанию
- [ ] Template tag и DRF permission работают (тесты)
- [ ] JS-эндпоинт отдаёт JSON с активными флагами
- [ ] Runbook написан

### Артефакты
- `backend/core/feature_flags.py` + tests
- `backend/core/templatetags/feature_flags.py`
- `backend/core/permissions.py` (добавить `FeatureFlagPermission`)
- `backend/api/v1/views/feature_flags.py`
- `docs/runbooks/feature-flags.md`

### Валидация
```bash
pytest backend/core/tests/test_feature_flags.py -v
curl -H "Authorization: ..." http://localhost:8001/api/v1/feature-flags/
```

### Откат
```bash
python manage.py migrate waffle zero
pip uninstall django-waffle
```

### Обновить в документации
- `docs/architecture.md`: раздел «Feature flags»
- `docs/decisions.md`: ADR-002 «Feature flags на django-waffle»

---

## Этап 0.4. Observability MVP (GlitchTip + structured logging)

### Контекст
Сейчас Sentry только free-tier с опциональным DSN — неприемлемо для доводки. Платный Sentry исключён (принцип «только бесплатное»). Логи идут в stdout, structlog не используется. Для безопасного рефакторинга нужен self-hosted error tracker + структурные логи с correlation_id.

### Цель
Поднять observability до уровня, при котором любой новый баг в проде виден в error tracker с полным контекстом (user, branch, request, trace). **Без платных подписок.**

### Что делать
1. **GlitchTip** (self-hosted, Sentry SDK совместимый):
   - Поднять через Docker Compose на том же сервере (или соседнем VPS, если RAM мало).
   - Стек: `glitchtip-web`, `glitchtip-worker`, `postgres` (отдельная БД), `redis` (может быть общий с CRM если настроить namespace). Итого 2 новых сервиса + 1 БД. RAM ~500MB.
   - Пример `docker-compose.observability.yml`:
     ```yaml
     services:
       glitchtip-web:
         image: glitchtip/glitchtip:v4
         environment:
           DATABASE_URL: postgres://glitchtip:...@glitchtip-db:5432/glitchtip
           SECRET_KEY: ...
           PORT: 8000
           EMAIL_URL: smtp://...  # для уведомлений об ошибках
         ports: ["8100:8000"]
       glitchtip-worker:
         image: glitchtip/glitchtip:v4
         command: ./bin/run-celery-with-beat.sh
         environment: { ... }
       glitchtip-db:
         image: postgres:16
         volumes: [glitchtip-db-data:/var/lib/postgresql/data]
     volumes:
       glitchtip-db-data:
     ```
   - Прописать nginx reverse-proxy: `glitchtip.groupprofi.ru` (или `sentry.groupprofi.ru`) с TLS.
   - SDK в Django: `sentry-sdk` с `dsn=GLITCHTIP_DSN` из env var. Протокол идентичен, меняется только URL.
   - Release tracking через CI (`sentry-cli releases new` с `--url https://glitchtip.groupprofi.ru/`).
   - Performance Monitoring: `traces_sample_rate=0.1` (GlitchTip поддерживает performance, но менее детально чем Sentry).
   - Установить tags: `user_id`, `role`, `branch`, `request_id`.
   - Alert rules: новые unhandled issues → email. GlitchTip умеет email, Slack, webhook.
   - **Retention**: по умолчанию GlitchTip хранит 30 дней — достаточно. Настроить если нужно больше.

2. **Structlog**:
   - Установить `structlog` + `django-structlog`.
   - `LOGGING` в settings: JSON output, поля `timestamp`, `level`, `logger`, `request_id`, `user_id`, `branch`, `event`.
   - Request middleware генерирует `request_id` (UUID) и привязывает ко всем логам запроса.
   - Celery task middleware — то же для задач.

3. **Логи на сервере**:
   - Systemd journal → per-service JSON.
   - `scripts/tail_logs.sh` для быстрого грепа.
   - В W10 логи будут агрегированы через Loki — здесь только JSON-формат.

4. **/health и /ready endpoints**:
   - `/health/` — liveness (всегда 200 если процесс живой)
   - `/ready/` — readiness (проверяет DB, Redis, MinIO когда будет)
   - Интегрировать в UptimeRobot free (50 мониторов бесплатно — достаточно)

### Инструменты
- `mcp__context7__*` — Sentry SDK docs (GlitchTip использует тот же SDK), structlog docs
- `Bash` для тестирования structured logging

### Definition of Done
- [ ] GlitchTip self-hosted работает, доступен по `https://glitchtip.groupprofi.ru/` с TLS
- [ ] Тестовая ошибка `raise Exception("glitchtip-smoke-test")` видна в GlitchTip с user/branch/request_id тегами
- [ ] В логах на staging все запросы имеют одинаковый `request_id` внутри одного request-цикла
- [ ] `/health/` и `/ready/` работают, UptimeRobot настроен (free tier)
- [ ] Alert rules созданы и протестированы (выкатить искусственный баг → получить email-уведомление)
- [ ] GlitchTip БД бэкапится отдельно (см. W10.2)

### Артефакты
- `backend/core/middleware/request_id.py`
- `backend/core/logging.py`
- `backend/api/v1/views/health.py`
- `settings/production.py` — обновлённая секция LOGGING и SENTRY_DSN (точка на GlitchTip)
- `docker-compose.observability.yml` — GlitchTip стек
- `scripts/tail_logs.sh`
- `docs/runbooks/glitchtip-setup.md`
- `docs/runbooks/glitchtip-restore.md`
- `docs/runbooks/logging.md`

### Валидация
```bash
# Trigger test error
curl http://staging.url/_debug/sentry-error/
# Check GlitchTip UI within 30s
```

### Откат
GlitchTip — удалить DSN из env, структурные логи можно оставить (не ломают работу). Docker compose стек остановить: `docker compose -f docker-compose.observability.yml down`.

### Обновить в документации
- `docs/architecture.md`: раздел «Observability» — GlitchTip, retention, backups
- `docs/decisions.md`: ADR-003 «GlitchTip self-hosted вместо Sentry paid»

---

## Этап 0.5. Test infrastructure upgrade

### Контекст
Сейчас 1179 тестов, pytest, Playwright отдельно. Нет: factory_boy (фикстуры), pytest-django settings для разных окружений, parallel тесты через `pytest-xdist`, mutation testing как контроль качества тестов. Мы будем писать много новых тестов в каждой волне — инфраструктура должна быть готова.

### Цель
Подготовить тестовую инфраструктуру, чтобы написание новых тестов было быстрым и надёжным.

### Что делать
1. **factory_boy**: установить, создать `backend/*/factories.py` для ключевых моделей (Company, Contact, User, CompanyDeal, Task, Campaign, Conversation, Message). Использовать `DjangoModelFactory` + `SubFactory` + `sequence`.

2. **pytest-xdist**: добавить в `requirements-dev.txt`, настроить `pytest.ini` с `-n auto` (опционально, из-за DB изоляции). Проверить, что тесты изолированы (transactional DB per worker).

3. **pytest-playwright**: заменить standalone Playwright на интеграцию через pytest. Фикстуры `page`, `authenticated_page`, `admin_page`, `manager_page`. Базовый URL из env.

4. **Snapshot testing**: `syrupy` для snapshot-тестов API-ответов.

5. **Coverage config**: исключить migrations, tests, admin, __init__.py. Группировка по app.

6. **Mutation testing** (опционально): `mutmut` на критичных модулях (policy, services). Запуск раз в неделю в CI (долгий).

7. **Test data**: `backend/conftest.py` с пул-фикстурами (users всех ролей, стандартные companies, seeded data).

8. **Playwright fixtures**: `tests/e2e/conftest.py` — логин под каждой ролью, cleanup после теста.

### Инструменты
- `mcp__context7__*` — factory_boy, pytest-django, pytest-playwright docs

### Definition of Done
- [ ] `pytest -n auto` работает без конфликтов (если решили параллелить)
- [ ] 8+ factory-классов для ключевых моделей
- [ ] Пример E2E теста на pytest-playwright зелёный
- [ ] conftest.py с user fixtures для всех ролей
- [ ] coverage.xml генерируется и имеет правильные exclude

### Артефакты
- `requirements-dev.txt` (обновлённый)
- `backend/companies/factories.py`
- `backend/accounts/factories.py`
- `backend/messenger/factories.py`
- `backend/mailer/factories.py`
- `backend/tasks/factories.py`
- `backend/phonebridge/factories.py`
- `backend/conftest.py`
- `tests/e2e/conftest.py`
- `tests/e2e/test_smoke.py` (zерый)
- `.coveragerc` (updated)

### Валидация
```bash
pytest -x  # 1179 + новые smoke тесты
pytest -n 4  # параллельно
pytest tests/e2e --headed --slowmo=500  # визуальная проверка E2E
```

### Откат
```bash
git revert <commit-sha>
pip uninstall factory-boy pytest-xdist pytest-playwright syrupy
```

### Обновить в документации
- `docs/testing.md` (новый файл): как писать тесты, фикстуры, factory-паттерны
- `docs/decisions.md`: ADR-004 «Test stack: pytest + factory_boy + xdist + playwright»

---

## Этап 0.6. Baseline performance snapshot

### Контекст
Прежде чем рефакторить и оптимизировать, нужно замерить baseline. Без чисел — все оптимизации гадательные.

### Цель
Собрать performance snapshot продакшна и staging: p50/p95/p99 на ключевых эндпоинтах, топ-10 медленных запросов, топ-10 N+1 мест.

### Что делать
1. **pg_stat_statements**: включить расширение в Postgres (`CREATE EXTENSION pg_stat_statements` + `shared_preload_libraries`). Через PostgreSQL MCP выгрузить топ-20 запросов по total_exec_time, mean_exec_time, calls. Сохранить в `docs/audit/postgres-baseline.md`.

2. **Django Silk** (dev) или **django-debug-toolbar** на staging: прогнать ключевые user journeys (вход, список компаний, детальная карточка, сделки, чат, рассылки). Записать время, количество SQL-запросов, наличие N+1. Сохранить в `docs/audit/django-perf-baseline.md`.

3. **k6 нагрузочный тест baseline**:
   - `tests/load/baseline_companies_list.js`: 50 VU за 3 минуты.
   - `tests/load/baseline_chat_websocket.js`: 20 одновременных WS-соединений.
   - Результаты в `docs/audit/load-baseline.md`.

4. **Frontend perf**: Lighthouse CI на 5 ключевых страницах. Результаты в `docs/audit/lighthouse-baseline.md`.

5. **Bundle analysis**: размер `backend/static/ui/` — по файлам, что дублируется. Результат в `docs/audit/frontend-inventory.md` (дополнение).

### Инструменты
- `mcp__postgres__*` — для pg_stat_statements запросов
- `mcp__playwright__*` — для Lighthouse через Chromium DevTools Protocol

### Definition of Done
- [ ] Postgres baseline: топ-20 запросов с временем, планами, предположениями «где индекс»
- [ ] Django perf baseline: 10 user journey с SQL count и временем
- [ ] k6 baseline: p50/p95/p99 для двух сценариев
- [ ] Lighthouse baseline: 5 страниц с Performance/Accessibility/Best Practices/SEO scores
- [ ] Документация содержит цифры, не абстракции

### Артефакты
- `docs/audit/postgres-baseline.md`
- `docs/audit/django-perf-baseline.md`
- `docs/audit/load-baseline.md`
- `docs/audit/lighthouse-baseline.md`
- `tests/load/baseline_companies_list.js`
- `tests/load/baseline_chat_websocket.js`

### Валидация
Цифры сошлись с ожидаемыми порядками:
- p95 списка компаний < 2000ms (если больше — уже flag в Wave 13)
- N+1 в company_detail очевиден (это ожидаемо, чинится в Wave 1)

### Откат
pg_stat_statements оставить включённым (полезно постоянно). Остальное read-only.

### Обновить в документации
- `docs/audit/README.md`: добавить раздел «Performance baseline», ссылки на новые файлы
- `docs/roadmap.md`: конкретные perf-цели на Wave 13

---

## Checklist завершения волны 0

- [ ] Все 6 этапов пройдены, CI зелёный
- [ ] `docs/audit/` содержит 15+ файлов
- [ ] Tooling baseline установлен, pre-commit работает
- [ ] Sentry + structured logging работают
- [ ] Тестовая инфраструктура готова к интенсивному написанию новых тестов
- [ ] Performance baseline зафиксирован
- [ ] `docs/current-sprint.md` отражает завершение Wave 0

**Только после этого** — переход к Wave 1.
