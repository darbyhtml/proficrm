# Архитектурные решения

## ADR-004 [2026-04-20] Uptime Kuma self-hosted вместо UptimeRobot (Wave 0.4 Track D)

**Контекст.** W0.4 DoD требует uptime-мониторинг 3 сервисов (CRM prod, CRM staging, GlitchTip) с alerts в Telegram при падении. План v1.2 (00_MASTER_PLAN.md §6) упоминает UptimeRobot free-tier. При попытке настроить проверилось:

1. **UptimeRobot недоступен в РФ** без VPN — IP-block на уровне провайдеров. Проверено из staging-VPS (5.181.254.172).
2. **Telegram integration у UptimeRobot стал платным** (Pro plan $54/mo с 2024). Free-tier шлёт только email.

Оба факта противоречат принципу Wave 0 §2.1 «только бесплатные self-hosted инструменты».

**Альтернативы.**
1. **UptimeRobot free (email only)** — не даёт Telegram уведомлений, email теряется в общем inbox, задержка alerts до часов.
2. **Platform.sh / Better Stack** — платные.
3. **Healthchecks.io self-hosted** — push-based (сервисы сами пингуют), не подходит для внешнего uptime.
4. **Uptime Kuma** (self-hosted) — open-source, ~80 MB RAM, UI-driven setup, native Telegram/Email/Slack/Webhook integrations. Зрелый (10k+ stars).
5. **Простой bash-скрипт в cron** — 30 строк, без UI. Для одного мониторинга ок, но масштабирование на 10+ сервисов неудобно.
6. **Prometheus Blackbox Exporter + Alertmanager** — мощный, но требует Prometheus-стек (W10.5).

**Решение.** **Uptime Kuma** через отдельный compose-проект `proficrm-uptime`, hard-limit 128 MB RAM.

Причины:
- Open source, MIT, active (релизы каждые 2-4 недели).
- Telegram native (через bot token + chat_id).
- UI позволяет быстро добавлять/менять monitors без YAML-redeploy.
- Volume-based persistence — конфиги и история переживают restart.
- Healthcheck-probes стандартные HTTP(s)/TCP/PING/Docker-health.

**Последствия.**
- ✅ Kuma развёрнут в `/opt/proficrm-observability/` (docker-compose.uptime.yml).
  Memory overhead: +91 MB (at runtime). Итого observability-стека: **736 MB**.
- ✅ Runbook `docs/runbooks/uptime-monitoring.md` описывает setup, 3 обязательных monitor'а, troubleshooting.
- ⚠️ Telegram bot token **не найден в проекте** (see Q7 open-questions.md + docs/audit/telegram-bot-inventory.md). Alerts пока только в email (если подключим SMTP) или UI. Нужен ответ пользователя.
- ⚠️ DNS `uptime.groupprofi.ru` **не заведён** (Q8 open-questions.md). Доступ через SSH tunnel на порту 3001.
- ℹ️ Бэкап конфигов Kuma — раз в месяц вручную через UI Export до W10 (MinIO automation).
- ℹ️ Если swap вырастет > 1.5 GB после Kuma + GlitchTip — эскалация в Q2 (либо погасить Chatwoot, либо отдельный VPS).

**Откат.** Полный откат — `docker compose -f docker-compose.uptime.yml down --volumes`. Освобождает 128 MB RAM. История мониторинга теряется (при повторной установке — с нуля).

**Связанные документы.**
- `docker-compose.uptime.yml`
- `docs/runbooks/uptime-monitoring.md`
- `docs/audit/telegram-bot-inventory.md`
- `docs/open-questions.md` Q7, Q8

---

## ADR-003 [2026-04-20] GlitchTip self-hosted вместо Sentry paid (Wave 0.4)

**Контекст.** Для observability в рамках плана доводки проекта до прод-готовности нужен error tracker с:
- user-контекстом (id/role/branch), чтобы понимать кого касается баг
- cross-reference с application logs через `request_id`
- feature flags context для триажа A/B («ошибка только при `UI_V3B_DEFAULT=True`?»)
- email/telegram alerts на new unhandled issue
- retention минимум 14 дней
- защитой от утечки PII

Текущий Sentry free-tier (5k events/мес) — недостаточен для production CRM на 50 юзеров: средний поток error-events в таком проекте 200-500/сутки, лимит выбирается за 10-25 дней. После превышения — events молча дропаются.

**Альтернативы.**
1. **Sentry Team plan** — $26/мес × 12 = **$312/год**. Отвергнуто принципом «только бесплатные инструменты» Wave 0 (00_MASTER_PLAN.md §2.1).
2. **Sentry self-hosted** — требует 8+ GB RAM, Kafka+ClickHouse+PostgreSQL+Redis+web+workers. Перебор для наших задач, дорого по инфраструктуре.
3. **GlitchTip self-hosted** — fork Sentry, сохраняющий SDK-совместимость, но без Kafka/ClickHouse. Стек: web+worker+postgres+redis. Минимум ~500 MB RAM. Open source, AGPL. Актуальная версия 6.1 (апрель 2026).
4. **Rollbar / Bugsnag / Raygun** — все платные, у всех free-tier ещё меньше Sentry.
5. **Собственное решение на Django logging + email** — нет агрегации/dedup/фильтрации, alerts будут спамом.

**Решение.** **GlitchTip 6.1 self-hosted** + Sentry-SDK в Django (тот же протокол, меняется только DSN URL).

Развёрнутые причины выбора:
- **SDK-совместимость**: `pip install sentry-sdk` → `sentry_sdk.init(dsn=GLITCHTIP_DSN, ...)`. Ноль изменений в коде при будущем переходе обратно на Sentry если появится необходимость.
- **Тот же VPS что и CRM**: 576 MB hard-limits, redis шарится (DB 10/11), суммарная overhead приемлемая. Альтернатива — отдельный $3-5/мес VPS, отложена в Q2 open-questions.md до подтверждения нагрузки.
- **Retention 30 дней** по умолчанию — больше чем free-tier Sentry (30 дней в платном).
- **Self-managed → полный контроль данных** (152-ФЗ-friendly — error events с PII остаются на российском VPS).
- **TLS через Let's Encrypt** — стандартный auto-renew.

**Последствия.**
- ✅ Observability работает на `https://glitchtip.groupprofi.ru/` с TLS.
- ✅ Backup: pg_dump ежедневно (03:00 UTC), retention 30 дней в `/var/backups/glitchtip/` (в W10 перенесём в MinIO bucket).
- ✅ 5 тегов на каждый issue через `core.sentry_context.SentryContextMiddleware`: `user_id`, `role`, `branch`, `request_id`, `feature_flags`.
- ✅ Celery tasks тоже получают request_id + Sentry scope через signals в `core.celery_signals`.
- ✅ `/live/` + `/ready/` endpoints для K8s-style probes и UptimeRobot.
- ✅ Runbooks: `docs/runbooks/glitchtip-setup.md` + `glitchtip-restore.md`.
- ⚠️ **RAM впритык**: VPS уже с 1 GB swap, мониторим. Эскалация в Q2 open-questions.md если swap > 1.5 GB.
- ⚠️ **Три ручных шага после деплоя**: login, create organization, create project — не автоматизируется через Django management command (ограничения GlitchTip). Документировано в setup-runbook.
- ⚠️ **Первичный superuser** создан через non-interactive `createsuperuser`, пароль сохранён в `/etc/proficrm/env.d/glitchtip.conf` (mode 600).
- ℹ️ **UptimeRobot** не автоматизирован — настраивается вручную через UI (3 монитора), документировано в setup-runbook.
- ℹ️ **GlitchTip ≠ Sentry 1:1**: performance monitoring (APM traces) менее детализирован; SDK send-rate разные. Для error-tracking — эквивалент.

**Откат.** Если GlitchTip не справится (memory peak > limit, data loss из-за OOM):
1. Удалить `SENTRY_DSN` из `.env` CRM → errors пишутся в ErrorLog Django-модель как раньше.
2. `docker compose ... down --volumes` → освобождение 576 MB RAM.
3. Рассмотреть вариант: отдельный мини-VPS 1-2 GB под observability (цена ~200₽/мес Netangels).

**Коммиты.** `09e1f94e` (code part), деплой-коммит (этот).

**Связанные документы.**
- `docs/runbooks/glitchtip-setup.md`
- `docs/runbooks/glitchtip-restore.md`
- `docs/open-questions.md` Q2 (RAM бюджет)
- `backend/core/sentry_context.py`
- `backend/core/celery_signals.py`
- `backend/crm/health.py`
- `docker-compose.observability.yml`
- `docs/plan/01_wave_0_audit.md` §0.4

---

## ADR-002 [2026-04-20] Feature flags на django-waffle (Wave 0.3)

**Контекст.** В последующих волнах запланированы минимум 4 поэтапные выкатки, которые нельзя сделать единым деплоем:
- W9: переключение UI карточки компании classic → v3/b (нужен percentage rollout и быстрый откат).
- W2.4: мягкая → mandatory миграция TOTP 2FA для админов (2 недели soft период, потом включение).
- W2: shadow-dashboard «denied requests» за 2 недели до перехода Policy Engine в ENFORCE.
- W6: включение bounce-обработчика от smtp.bz (возможны false-positives, нужен kill-switch).

Без единого механизма feature flags каждая выкатка = отдельная ночь деплоя с риском отката всего PR.

**Альтернативы.**
1. **Hardcoded if-блоки + env vars** (`if os.getenv("UI_V3B_ENABLED") == "1"`) — нет percentage rollout, нельзя включить для одного юзера, нет единого управления.
2. **django-flags** (Cognizant) — похож на waffle по функционалу, но меньше community (5k stars vs 1k), старше не обновлялся 2+ года, хуже поддержка Django 6.
3. **django-waffle** (Mozilla) — зрелый, 1900+ stars, активный (5.0 в 2024), поддерживает Flag/Switch/Sample, percentage, per-user, per-group, per-everyone.
4. **Собственное решение на Redis** — не окупается: waffle уже даёт всё нужное, включая Django admin.
5. **Unleash / LaunchDarkly / Flagsmith** — SaaS, платные, зависимость от внешнего сервиса. Исключено принципом «только self-hosted / free» проекта (Wave 0 §2.1).

**Решение.** **django-waffle 5.0.0** с обёрткой `core.feature_flags`.

Обёртка даёт 4 преимущества поверх чистого waffle:
1. **Канонические константы** (`UI_V3B_DEFAULT`, …) — защита от опечаток в именах флагов, возможность rename через IDE.
2. **Env kill-switch** — `FEATURE_FLAG_KILL_<NAME>=1` перекрывает admin/DB. Сценарий: админ-токен потерян, БД недоступна, но флаг ломает прод — правим .env и up -d (< 30 сек).
3. **Branch-based override (заготовка)** — аргумент `branch` в `is_enabled()` на будущее, когда понадобится «включить только для Екатеринбурга».
4. **Единообразный API для всех 4 интерфейсов** (Python/templates/DRF/JS) — не нужно помнить какой import.

**Последствия.**
- ✅ 4 начальных флага созданы через data-миграцию `core.0001_initial_feature_flags` (идемпотентно, `update_or_create`).
- ✅ 28 тестов, coverage 92% для `core/feature_flags.py` (DoD ≥ 90% выполнен).
- ✅ `/api/v1/feature-flags/` endpoint для фронта — фронтенду не нужно знать про waffle.
- ✅ Runbook (`docs/runbooks/feature-flags.md`) + архитектурный контракт (`docs/architecture/feature-flags.md`) — вся операционка в одном месте.
- ⚠️ В `INSTALLED_APPS` добавлены `waffle` + `core` (core раньше был utility-модулем без регистрации). Side-effect: теперь Django ищет миграции в `core/migrations/` при каждом `migrate`.
- ⚠️ Waffle кеширует флаги в Redis на 5-10 сек — включение через admin требует подождать. В тестах нужно использовать `waffle.testutils.override_flag`, а не `Flag.objects.update()` (обходит post_save → cache stale).
- ℹ️ **Не стали делать waffle** для: `POLICY_ENGINE_ENFORCE` (env var, 10-сек reload через systemd), `MEDIA_READ_FROM` (settings-based, один процесс — одно значение), `ANDROID_PHONEBRIDGE_V2` (W7 далеко, спекулятивный флаг). См. `docs/architecture/feature-flags.md` §«Почему НЕ приняты».

**Для включения в будущем.** Через Django admin `/admin/waffle/flag/`, с percentage rollout 10% → 50% → 100% и мониторингом error rate в GlitchTip (Wave 0.4). Флаги живут ≤ 30 дней — после стабилизации код чистится.

**Коммиты.** `96286510` (infra), `d30b0ce0` (tests), `6ab4d132` (override_flag fix).

**Связанные документы.**
- `docs/runbooks/feature-flags.md`
- `docs/architecture/feature-flags.md`
- `backend/core/feature_flags.py`
- `docs/plan/01_wave_0_audit.md` §0.3

---

## [2026-04-20] Companies services package — рефакторинг god-view company_detail.py

**Контекст.** `backend/ui/views/company_detail.py` разросся до 2883 LOC с 30+ view-функциями, каждая из которых сама выполняет валидацию, normalization, duplicate-checks, audit logging. Валидация телефонов дублирована 3 раза, валидация email — 2 раза, workflow удаления — 2 раза. Любое изменение правил (например, разрешить новый регион для `normalize_phone`) требует синхронного правления 3 мест, и легко пропустить. God-файл + god-функции.

В `backend/companies/services.py` уже была "business services" логика (`CompanyService`, `ColdCallService`), но плоский файл не давал места для новых сервисов без риска коллизий имён.

**Альтернативы.**
1. Переписать всё в DRF сериализаторы — **отвергнуто**: слишком большой blast radius, текущие view завязаны на `messages.success`/`redirect` (не JSON).
2. Вынести в миксины на CBV — **отвергнуто**: большинство view — FBV, конвертация бьёт slash-review diff'ы.
3. **Чистый extract в `companies/services/` пакет** — принято, т.к. позволяет извлекать *pure* функции (без HttpRequest), покрывать их юнит-тестами без Client, и постепенно утоньшать view-слой без переписывания HTTP-контракта.

**Решение.**
- `backend/companies/services.py` → `backend/companies/services/company_core.py` + `__init__.py` c re-exports для обратной совместимости (18+ внешних импортов продолжают работать).
- **Phase 1**: `companies/services/timeline.py` — `build_company_timeline(*, company)` консолидирует 7-источниковую ленту (notes/events/tasks/deals/calls/mailings/delreqs), раньше дублированную между `company_detail` view и `company_timeline_items` view.
- **Phase 2**: `companies/services/company_phones.py` — `validate_phone_strict`, `validate_phone_main`, `check_phone_duplicate`, `validate_phone_comment`. `companies/services/company_emails.py` — `validate_email_value(allow_empty=)`, `check_email_duplicate(exclude_email_id=, check_main=)`. Защитный lowercase внутри `check_email_duplicate`.
- **Phase 3**: `companies/services/company_delete.py` — `execute_company_deletion(*, company, actor, reason, source, extra_meta) -> dict`. Делает всё: cleanup CompanySearchIndex → detach children → notify → delete Task → log_event → `company.delete()` в `transaction.atomic`. Ошибка индекса → `CompanyDeletionError` (кастомный exc).

**Последствия.**
- `company_detail.py`: 2883 → 2698 LOC (−185, ≈−6.4%).
- Тестовое покрытие: +36 юнит-тестов для новых сервисов (phone/email/delete), **1179/1179 тестов passed**.
- Трёхкратное дублирование валидации телефона устранено — правки теперь в одном месте.
- Риск регрессий **низкий**: все функции purely-extracted, HTTP-контракт view не менялся, E2E smoke-тесты (POST невалидного телефона/email) показали те же 400/200 ответы.
- Phase 4-5 (company_phones CRUD целиком в service + overview context-builder) отложены — значительно меньший ROI, требуют изменения HTTP-контракта.

**Коммиты.** `2048f4ef` (phase 0), `126b7930` (phase 1), `05b34036` + `3a6779c8` + `07e8000b` (phase 2), `785d314a` (phase 3).

**Связанные документы.**
- `docs/runbooks/50-frontend-audit-2026-04-20.md` — сводка всех находок 5 агентов.
- `backend/companies/services/__init__.py` — docstring с описанием роадмапа фаз.
- `backend/companies/tests_phone_email_services.py`, `backend/companies/tests_delete_service.py` — юнит-тесты.

---

## [2026-04-20] Выключение policy decision logging по умолчанию + PG RULE как хотфикс

**Контекст.** За полгода работы policy engine (`backend/policy/engine.py:_log_decision()`) накопил **9 514 890 записей** в `audit_activityevent` с `entity_type='policy'` — это **95% всей таблицы** (4 GB из 5.5 GB БД). Причина: функция пишет ActivityEvent на **каждый HTTP-запрос** через `@policy_required`, а polling endpoints (`/mail/progress/poll/`, `/notifications/poll/`) дают ~1.5 млн запросов/сутки. Логика `audit_activityevent` задумана как **бизнес-журнал** (кто что сделал с компаниями), а policy-логи — **technical telemetry**, они размывают реальную историю и раздувают БД.

Свидетельство: в `ui/views/settings_core.py:1432` уже есть `exclude(entity_type='policy')` — разработчик знал о проблеме, но починил только отображение, не корень.

**Альтернативы.**
1. Оставить как есть, очистить разово — **отвергнуто**: через полгода снова 4 GB.
2. Отдельная модель `PolicyDecisionLog` с TTL 24h — **отвергнуто для Релиза 0**: требует миграции, отложено в Релиз 2.
3. **Env-flag `POLICY_DECISION_LOGGING_ENABLED` + PG RULE как мгновенный хотфикс** (принято).

**Решение.**
- В `backend/crm/settings.py` добавлен `POLICY_DECISION_LOGGING_ENABLED = os.getenv("POLICY_DECISION_LOGGING_ENABLED", "0") == "1"` (по умолчанию ВЫКЛ).
- В `backend/policy/engine.py:_log_decision()` первой строкой проверка `if not settings.POLICY_DECISION_LOGGING_ENABLED: return`.
- **Хотфикс Релиза 0 (2026-04-20)** до прихода кода на прод: PostgreSQL RULE
  ```sql
  CREATE RULE block_policy_activity_events AS ON INSERT TO audit_activityevent
    WHERE NEW.entity_type='policy' DO INSTEAD NOTHING;
  ```
  Молча отбрасывает INSERT'ы с `entity_type='policy'` без изменения кода, без рестарта. Обратимо: `DROP RULE`.

**Последствия.**
- Прирост новых policy events: **0** сразу после `CREATE RULE` (проверено live).
- 10.3M старых записей удалено batch DELETE (103 итерации по 100K, ~12 минут).
- `audit_activityevent` физический размер не уменьшился (dead space), нужен `VACUUM FULL` ночью.
- После Релиза 1 (код с env-flag доедет на прод) — `DROP RULE` можно удалить, функция сама не будет писать благодаря флагу.

**Для включения в будущем** (целенаправленный аудит policy-решений на короткий период):
```bash
# .env.prod
POLICY_DECISION_LOGGING_ENABLED=1
# + DROP RULE block_policy_activity_events ON audit_activityevent;
# + docker compose up -d web
```

**Связанное.** `docs/runbooks/04-god-nodes-n1-analysis.md` (анализ проблемы), `docs/runbooks/11-release-0-actual-2026-04-20.md` (фактический отчёт).

---

## [2026-04-20] Celery healthcheck: убрать `-d destination`

**Контекст.** `proficrm-celery-1` показывал `unhealthy` **40 209 consecutive failures** (~4 недели). Ручной запуск `celery -A crm inspect ping -d celery@$HOSTNAME --timeout 5` через `sh -c` работает (возвращает `pong`), но Docker healthcheck фейлит.

**Анализ.** Конфиг формата `["CMD", "celery", "-A", "crm", "inspect", "ping", "-d", "celery@$HOSTNAME", "--timeout", "5"]` — это **прямой exec без shell**. Docker передаёт команде буквальный аргумент `celery@$HOSTNAME` (с долларом), а не `celery@b0d92e4160a5` — `$HOSTNAME` интерполируется только shell'ом. Ping уходит к несуществующему воркеру, always FAIL.

**Альтернативы.**
1. `["CMD-SHELL", "celery -A crm inspect ping -d celery@$HOSTNAME --timeout 5"]` — работает, но тянет `sh` в healthcheck.
2. **Убрать `-d destination` вовсе** (принято) — один воркер в prod-setup, `inspect ping` без `-d` опрашивает все ноды (N=1).

**Решение.** В `docker-compose.prod.yml` (main-ветка):
```yaml
healthcheck:
  test: ["CMD", "celery", "-A", "crm", "inspect", "ping", "--timeout", "10"]
  # timeout 10s вместо 5 — broker ответ иногда >5s при нагрузке
```

**Последствия.**
- Фикс приедет в Релизе 1 (main → prod deploy).
- До Релиза 1 `unhealthy` остаётся (не критично — Celery работает, задачи идут, просто мониторинг врёт 4 недели).
- Если добавим второй celery-worker — надо возвращать `-d` с `CMD-SHELL`, либо использовать broker probe.

**Связанное.** `docs/runbooks/11-release-0-actual-2026-04-20.md`.

---

## [2026-04-19] Hotfix: Company delete = CASCADE related tasks

**Контекст.** На проде за 60 дней 148 удалений компаний. У модели `Task.company` — `on_delete=SET_NULL`. При удалении компании задачи НЕ удалялись — оставались с `company_id=NULL`, в списке `/tasks/` в колонке «Компания» показывался «—», в карточку зайти нельзя. Пользователи путались («откуда задачи без компании»).

**Альтернативы.**
1. Изменить схему FK на `on_delete=CASCADE` — миграция БД, рисково на проде с 9.5M ActivityEvent.
2. **Явное удаление задач ПЕРЕД `company.delete()`** в view-логике (принято) — без миграции, контролируемо.
3. Оставить SET_NULL и в UI показывать «компания удалена» вместо «—» — не решает проблему «зайти нельзя».

**Решение.** В `company_delete_direct` и `company_delete_request_approve` перед `company.delete()` добавлено:
```python
_tasks_del_cnt = Task.objects.filter(company_id=company_pk).delete()[0]
```
и `tasks_deleted_count` в `ActivityEvent.meta` для аудита. UI-подтверждение: «Все задачи и заметки этой компании будут удалены, контакты — отвяжутся.»

**Последствия.**
- Ручной hotfix применён на прод через `docker cp` (2026-04-18), зеркалено в коммите `b7dcb21a` (2026-04-19).
- Staging E2E-тест: 3 тестовые задачи удалились вместе с компанией, `tasks_deleted_count: 3` в audit.
- 45 orphan-задач на проде (созданных пользователями без company_id) НЕ тронуты — это содержательная manual-работа, не осиротевшие.

**Связанное решение** (отложено): для проблемы «скрытый `task_filter=week` через localStorage» — на проде добавлен баннер «Активен дополнительный фильтр» в `company_list.html`. При следующем полном деплое main→prod заменится на новый `company_list_v2.html`, где фильтры уже отображаются как `fchip` с ×-кнопкой.

## [2026-04-18] Min font-size 14px — глобальная accessibility политика

**Контекст.** Пользовательская база CRM — менеджеры/РОП/директора 40+ возраста. Мелкий шрифт (11-13px) читать сложно. Пользователь прямо потребовал «самый минимальный шрифт во всём проекте — 14px, пожалуйста, исправь это».

**Альтернативы.**
1. Только увеличить дефолтный `font_scale` пользователя — не учитывает новых юзеров.
2. Точечно править компоненты где жалоба — косметика, через месяц та же проблема.
3. **Жёсткая политика: все основные тексты ≥ 14px везде** (принято).

**Решение.**
- **284 замены** `font-size:Npx (N<14)` → `14px` в 29 шаблонах (Python-скрипт).
- **CSS override** в `base.html` для Tailwind-классов:
  ```css
  .text-xs { font-size: 14px !important; line-height: 1.4 !important; }
  [class*="text-[9px]"], [class*="text-[10px]"], [class*="text-[11px]"],
  [class*="text-[12px]"], [class*="text-[13px]"] { font-size: 14px !important; }
  .btn-sm, .btn-xs { font-size: 14px !important; }
  .badge-xxxs, .badge-xxs, .badge-xs, .badge-sm, .badge-md { font-size: 14px !important; }
  ```

**Коммит:** `5ec5cf3f UI(Global): минимум 14px шрифт во всём проекте (политика user 2026-04-18)`.

**Последствия.**
- Проверено 6 ключевых страниц (v3/b, dashboard, companies list, tasks list, mail, analytics, settings, messenger) — вёрстка держится.
- Минор-регрессии: в узких колонках («Рабочий стол», «Действует до», «Компания самостоятельная») текст переносится на 2 строки. Не критично.
- При любой новой UI-правке: НЕ писать `font-size < 14px`. При конфликте с вёрсткой — расширять колонку или сокращать текст, **не** возвращать мелкий шрифт.

## [2026-04-18] F4 R3 v3/b — single-card редизайн карточки компании

**Контекст.** 3 существующие карточки (classic v1, modern v2, preview v3) — техдолг и путаница. User выбрал vеариант B из 3 preview (A Notion, B Dashboard/Linear, C Editorial). Требуется единая карточка, итерируемая по фидбеку.

**Решение.** Создана `/companies/<id>/v3/b/` preview (будет заменой classic на Этапе 6). Фичи:
1. **Popup-меню действий по клику** (classic amoCRM-паттерн): Позвонить · Скопировать · Изменить · Открыть в Яндекс.Картах · Отметить холодным · Удалить. См. отдельный ADR-паттерн в памяти `feedback_popup_menu_click_pattern.md`.
2. **Современная обработка телефонов**: JS `_normalizePhoneRu` + `_formatPhoneRu` (live-форматирование 8→+7, блок букв). Backend валидация `fullmatch(\+\d{10,15})` + null-byte защита.
3. **Input-like визуал редактирования**: border 1.5px primary + box-shadow glow 3px + placeholder через `:empty::before`.
4. **Восстановлено из classic**: `region` (combobox), `work_timezone` (13 RU-таймзон combobox), `workday_start/end`, `phone_comment` (для основного номера), `kpp` в hero рядом с ИНН, баннер `CompanyDeletionRequest`, бейдж договора «Срочно/Напомнить/активен/истёк».
5. **Новый endpoint**: `company_phone_delete` (в classic отсутствовал).

**Последствия.**
- Classic `/companies/<id>/` остаётся работать параллельно до Этапа 6.
- v1/v2 → deprecate после стабилизации v3/b на проде.
- 98 коммитов за 4 дня (16-19.04), 10 unit + Playwright E2E тестов.
- Аудит 7 агентами параллельно — закрыто 31 пропажа vs classic + 5 security fixes + 3 P1 responsive.

**Коммиты:** `33950339` (F4 R3 start) … `b7dcb21a` (final hotfix). Документация в `docs/wiki/02-Модули/Компании.md` (нужно дополнить).

## [2026-04-18] F5 R2: Round-Robin per-branch вместо per-inbox

**Контекст.** `MultiBranchRouter.route()` может менять `conversation.branch`
относительно `inbox.branch` (например, глобальный inbox=ekb, клиент из
Томской области → диалог уходит в tmn). При этом `InboxRoundRobinService(inbox)`
строит очередь по `inbox.branch_id`, а `services.auto_assign_conversation`
берёт кандидатов по `conversation.branch_id`. Пересечение `queue_ekb ∩
candidates_tmn` — пустое → RR возвращает `None` → диалог остаётся без
assignee, несмотря на наличие онлайн-менеджеров в tmn.

**Альтернативы.**
1. Передавать `conversation.branch` в `InboxRoundRobinService` как override —
   отвергнуто, семантически класс перестаёт быть «per-inbox» и деградирует
   в адаптер.
2. Хранить очередь в `conversation.branch` при создании — отвергнуто, это
   не решает кейс, когда inbox глобальный и branch определяется роутером.
3. **Выбрано:** `BranchRoundRobinService(branch)` с ключом
   `messenger:rr:branch:<branch_id>`. Очередь хранит ID менеджеров именно
   того филиала, куда роутер направил диалог.

**Последствия.**
- `InboxRoundRobinService` остаётся в коде (использовался ранее в UI/admin
  для ручных операций — не трогаем до полного аудита). Новая логика
  `auto_assign_conversation` и `reassign_conversation_auto` использует только
  `BranchRoundRobinService`.
- Старые Redis-ключи `messenger:rr:queue:<inbox_id>` становятся мёртвыми,
  TTL 7 дней их очистит естественно. Prod-cleanup не требуется.
- Коммит `<pending>`. Regression-тест
  `AutoAssignIntegrationTests.test_cross_branch_routing_uses_target_branch_rr`
  специально создаёт op_ekb и op_tmn, проверяет что при маршрутизации
  ekb→tmn назначается именно op_tmn.

## [2026-04-17] Big Release 2026 — зафиксированные решения после Q&A с user

Сводные ответы на 29 вопросов + аудит прода (read-only) определили следующие архитектурные направления. Детали — в `knowledge-base/audits/_summary-2026-04-17.md` и `docs/roadmap-2026-spring.md`.

### Роли (6)

| UI | Code | Scope |
|---|---|---|
| Менеджер | MANAGER | свои задачи+компании |
| Тендерист | TENDERIST | read-only + заметки ко всем компаниям (всех подразделений), НЕ видит задач, НЕ редактирует |
| РОП | SALES_HEAD | своё подразделение, эскалации |
| Директор подразделения | BRANCH_DIRECTOR | своё подразделение + transfer |
| Управляющий группой компаний | GROUP_MANAGER | все подразделения |
| Администратор | ADMIN | полный доступ + настройки + импорты |

### Дизайн

- **Фреймворк дизайна:** Notion + HubSpot/Linear, через скилл `frontend-design`. Figma нет.
- **Mobile-версия:** не нужна. Все пользователи с ПК.
- **Dark mode:** не нужен.
- **Layout:** fullwidth таблицы + popover-фильтры сверху (как уже в Компаниях v2).
- **Внутренний чат (Slack-стиль):** не нужен.
- **Два режима карточки Компании (classic/modern):** удалить оба, сделать **один** современный вариант, итерируемый по фидбеку.

### Чат (бывший live-chat) — решено

1. **Переименование:** `Live-chat` → `Чат` (15 видимых строк UI).
2. **Inbox:** один глобальный. Autoroute по региону клиента через `BranchRegion` fixture (95 записей, уже в коде).
3. **Понедельная ротация для 4 общих регионов** (Москва, СПб, Новгород, Псков):
   - Week 1 (ISO) → ЕКБ
   - Week 2 → Краснодар
   - Week 3 → Тюмень
   - Week 4 → снова ЕКБ (цикл по 3 филиалам)
   - Реализация: новый `WeeklyRotationRouter` в `messenger/assignment_services/`
4. **Рабочие часы per-branch:** 08:00-17:00 пн-чт, пт до 16:00. ЕКБ/Тюмень (UTC+5), Краснодар (UTC+3).
5. **Вне рабочего времени — форма выбора связи:**
   - Виджет показывает: «Наши менеджеры сейчас недоступны. Как удобнее связаться?»
   - Варианты: звонок / мессенджер / email / другое + поле для контакта
   - Ответственный менеджер получает email с историей переписки + выбранным способом связи
   - Диалог в CRM получает статус `WAITING_OFFLINE`, менеджер утром видит и нажимает «Я связался»
6. **Auto-away для отпуска/отгула:**
   - Новая модель `UserAbsence` (period, type: vacation/sick/dayoff, note)
   - `User.is_currently_absent()` property
   - `auto_assign` исключает отсутствующих менеджеров
   - Менеджер сам может отметить через UI «я в отгуле до X»
7. **WebSocket (Daphne):** добавить в прод docker-compose. Сейчас только на staging. 30-сек SSE fallback — deprecated после F5.
8. **SaaS-grade:** унифицировать два auto_assign пути (оставить новый в `services.py`), синхронизировать `messenger_online` ↔ `AgentProfile.Status`.

### Почта (F6) — причина поломки найдена

**🔴 Root cause (подтверждено на проде 2026-04-17):**
```
GlobalMailAccount.get_password() → cryptography.fernet.InvalidToken
```

Значит: `MAILER_FERNET_KEY` в `.env` на проде **не соответствует** ключу, которым пароль был зашифрован изначально. Все письма → FAILED с нечитаемой ошибкой.

**Фикс-план:**
1. Ротация Fernet-ключа через `MAILER_FERNET_KEYS_OLD` (если старый ключ известен)
2. Либо: Админ заходит в настройки, вводит пароль заново — пересохраняется текущим ключом
3. UI-onboarding (чеклист настроек): ключ → SMTP → тест-письмо → генерация получателей
4. Индикатор работы Celery, счётчик квот smtp.bz, понятные статусы
5. Лимиты smtp.bz (сутки/час/месяц) отображать в UI

### Android CRMProfiDialer — решено

- **Distribution:** не Google Play, внутренний APK.
- **QR-flow:** клиент сканирует QR → страница загрузки APK → устанавливает → входит через другой QR (безопасность). Сейчас вход по QR уже есть (39 QR-токенов в проде). Добавить download-page.
- **MobileAppBuild:** в БД прода 0 записей — endpoint готов, но релизов нет. **В F9 добавить CI-сборку APK и заливку через admin.**
- **P0 fix:** `ACTION_DIAL` из фонового сервиса блокируется на Android 10+ (BAL). Переход на full-screen notification intent.
- **Min SDK:** оставить API 26 (Android 8.0), target 35.
- **FCM:** env-переменных на проде нет → **не настроен**. Нужно: создать Firebase project, получить serviceAccountKey, настроить `FCM_CREDENTIALS` в env.

### Аналитика (F7)

- **5 специализированных дашбордов** для 6 ролей (Менеджер / РОП / Директор подразделения / Управляющий / Администратор = аналог Управляющего + админские метрики / Тендерист — справочная).
- **Chart.js** для графиков (не замудрённый, с tooltips).
- **KPI targets:** через админку (Администратор устанавливает цели на месяц для своих подразделений).
- **«Успешный cold call»:** звонок + ручная отметка, ИЛИ звонок + создал задачу за 24ч. Дефолт: **любой из двух**.
- **Экспорты CSV/PDF, автоматические отчёты:** не нужны пока.

### Magic numbers 25000 / 70000 (годовые договоры)

**Решение:** вынести в `ContractType.amount_danger_threshold` и `amount_warn_threshold` (DecimalField, nullable, fallback на хардкод). Настраивается через админку.

### CI/CD + инфра

- **GitHub Actions:** auto-staging при push в main, ручной прод.
- **Blue/green deploy:** пока не нужен (масштаб 20-50 пользователей).
- **Prometheus/Grafana/Sentry:** делаю если считаю необходимым. Предлагаю: Sentry (errors) + существующий `/health/` endpoint достаточны пока.

### Bulk-transfer и экспорт

- **Bulk-transfer UI** только для Администратора (user подтвердил Q5).
- **Экспорт CSV** — только Администратор.

### Тендерист — финальная роль

- Видит компании **всех подразделений** (общая база).
- Пишет заметки к любым компаниям.
- НЕ видит раздел Чат (Q1).
- НЕ редактирует компании (только заметки).
- НЕ имеет задач, не получает push-уведомлений про распределение (Q1).

### Процесс работы

- **Темп:** автономный, 2-3 часа моей работы в день.
- **Сверка с user:** только в критических ситуациях.
- **Backlog мелких идей:** `docs/roadmap.md` секция + spawn-task чипы + `_summary-*.md` открытые вопросы.

### Open items (к будущим фазам, не блокируют)

- **Fernet old-key неизвестен** (подтверждено 2026-04-17). Решение: при прод-деплое main Админ заходит в `/admin/` (появится новый mailer-интерфейс), удаляет старый SMTP-пароль, вводит заново — пересохранится текущим `MAILER_FERNET_KEY`. 5 минут работы.
- **FCM не настроен** — user подтвердил, Firebase-проекта нет. План на F9: создать проект в console.firebase.google.com, получить service account key, добавить `FCM_CREDENTIALS` в прод `.env`. Делаю инструкцию в F9.
- **messenger-модуль на проде НЕ УСТАНОВЛЕН** (подтверждено 2026-04-17, `ModuleNotFoundError`). Прод на `f015efb`, до мержа live-chat в main (2026-04-02). Появится автоматически при деплое main, добавить env при деплое: `MESSENGER_ENABLED=1`, `MESSENGER_DEFAULT_BRANCH_ID=<ekb.id>`, `MESSENGER_WIDGET_STRICT_ORIGIN=1`, `JWT_SECRET_KEY=<generate>`.
- **Прод сейчас (факты 2026-04-17):** 25 активных менеджеров (ЕКБ 9, Тюмень 9, Краснодар 7), 39 QR-токенов Android, 7 PhoneDevice, 0 MobileAppBuild (релизов APK нет), 9.5M audit_activityevent, 45k компаний, 18k задач. Чат, FCM, analytics-2026 — не установлены на текущем HEAD.


## [2026-04-16] Claude Code хуки: 4 узких операционных защиты, НЕ skill-auto-routing

**Контекст:** После обсуждения автоматизации работы Claude Code в проекте рассматривались два варианта: (А) жёсткое авто-срабатывание скиллов по ключевым словам задачи через хуки (например, auth → security-review); (Б) 4 точечных хука, закрывающих конкретные операционные ошибки, которые уже случались в проекте.

**Решение:** Вариант Б. Скилл-роутинг оставлен на уровне памяти (`MEMORY.md` + раздел «Маршрутизация скиллов» в `CLAUDE.md`) — Claude сверяется с таблицей триггеров на старте сессии.

**4 хука в `.claude/settings.json`:**

1. **PreToolUse/Bash → `block-prod.py`** — блокирует команды, затрагивающие `/opt/proficrm/` (прод). `/opt/proficrm-staging/` и `/opt/proficrm-backup/` разрешены. Закрывает самое строгое правило проекта.
2. **PreToolUse/Bash `if Bash(git commit*)` → `check-secrets.py`** — сканирует `git diff --cached` на FERNET_KEY, DJANGO_SECRET_KEY, password=, api_key=, PRIVATE KEY, AWS/GitHub токены. Повод — `.playwright-mcp/` с токенами в 2026-04-07.
3. **PostToolUse/Write|Edit → `ruff-fix.py`** — прогоняет `ruff check --fix` на изменённых `.py` в `backend/`. Fail-safe: если ruff не установлен, молча пропускает.
4. **PostToolUse/Write|Edit → `template-reminder.py`** — при правке `backend/templates/**.html` напоминает использовать `docker compose restart web` (не `up -d`) — gunicorn кэширует скомпилированные шаблоны (см. `problems-solved.md` 2026-04-16).

**Почему не skill-auto-routing:** (а) хуки не умеют вызывать скиллы — только инжектить текст в контекст модели; (б) паттерн-матчинг по словам даёт шум; (в) семантическое решение «какой скилл нужен» лучше остаётся за моделью по таблице в CLAUDE.md.

**Архитектура:** Хук-скрипты на Python (не bash/jq — на хосте нет jq, но Python 3.13+ гарантированно есть, т.к. это язык проекта). Каждый скрипт читает JSON со stdin, возвращает либо пусто (пропустить), либо JSON с `hookSpecificOutput.permissionDecision=deny` или `systemMessage`.

**`.gitignore`:** `.claude/settings.json` и `.claude/hooks/` — коммитятся (shared). `.claude/settings.local.json`, `.claude/agents/`, `.claude/skills/`, `.claude/commands/`, `.claude/vendor/`, `.claude/worktrees/` — игнорируются.

**Альтернативы:**
- Pre-commit hook (Git, не Claude Code) для проверки секретов — плюс: работает для всех, не только через Claude; минус: дополнительная инсталляция у команды. Хук Claude Code — zero-setup для любого, кто использует Claude.
- Расширить до 10+ хуков («всё автоматизировать») — отклонено: over-engineering, шум, сложность поддержки.

## [2026-04-16] Архитектурный рефакторинг: консолидация общих утилит в core/ и accounts/

**Контекст:** Анализ через graphify-граф выявил 8 структурных проблем: god-модуль `_base.py` (387 рёбер к phonebridge через транзитивный импорт), крипто-логика жила в `mailer/crypto.py` но использовалась в `ui/models.py` и `amocrm/client.py`, авторизация (`require_admin`, `get_effective_user`) жила в `crm/utils.py` вместо `accounts/`, инфраструктура (request_id, json_formatter, exceptions, test_runner) жила в `crm/` — ядре Django, которое должно содержать только settings/urls/wsgi. `_normalize_phone` дублировался через `ui.forms` вместо единственного источника `companies.normalizers`.

**Решение:**
1. Создан пакет `core/` — общие утилиты: crypto, timezone_utils, request_id, json_formatter, exceptions, test_runner
2. Авторизация перенесена в `accounts/permissions.py`
3. Все оригинальные файлы заменены re-export shim'ами (backward compatibility для миграций и неизвестных ссылок)
4. Все прямые импорты обновлены на новые пути (включая string references в settings.py)
5. phonebridge: top-level import в `_base.py` убран, каждый sub-view импортирует напрямую из `phonebridge.models`
6. `_normalize_phone`: 10 мест переведены с `ui.forms` на `companies.normalizers`
7. AmoApiConfig оставлен в `ui/models.py` — перенос Django-модели между приложениями слишком рискован (миграции, данные на проде), а `amocrm/` даже не является Django app

**Удалённый dead code:** `ui/work_schedule_utils.py` (дубль core), `_task_status_badge.html` (не включался), 3 debug management commands (`debug_contacts`, `debug_amo_events`, `test_migration_speed`).

**Создано:** `templates/500.html` — standalone error page (без extends base.html).

**Альтернативы:** Полный перенос AmoApiConfig в amocrm app (отклонено: потребовал бы создание Django app + SeparateDatabaseAndState миграция + ручное применение на проде).

## [2026-04-16] Dashboard poll: только {updated: true/false}, без сериализации данных

**Контекст:** `dashboard_poll` дублировал 170 строк логики из `_build_dashboard_context()` — полную сериализацию задач и договоров в JSON. Клиентский JS при `updated: true` делал `window.location.reload()`, не используя остальные данные ответа. SSE endpoint (`dashboard_sse`) был добавлен как альтернатива, но не подключён.

**Решение:** Poll возвращает только `{updated: bool, timestamp: int}`. EXISTS-проверка: 2 SQL-запроса вместо ~15. Удалён SSE endpoint (60 строк). Итого −230 строк. Клиентский JS не изменён — формат ответа обратно совместим.

**Альтернативы:** 1) Partial DOM-обновление через HTMX/morphdom — overhead для 30-секундного poll. 2) WebSocket push — уже есть для мессенджера, но dashboard не критичен для real-time. 3) Оставить как есть — лишние SQL + дублированная логика при каждом poll (каждые 30 сек от каждого пользователя).

## [2026-04-16] confirm() → двойной клик с timeout для «выполнить задачу»

**Контекст:** Нативный `window.confirm()` блокирует UI, не стилизуем, выглядит инородно в Notion-стиле. Toast+undo (как в Gmail) — сложнее и требует undo-механизма на бэкенде.

**Решение:** Первый клик подсвечивает кнопку (border + background primary-50), через 2.5с сбрасывается. Второй клик выполняет действие. Не блокирует UI, мобильно-дружественно, визуально встроено в стиль.

## [2026-04-15] URL-рефактор: `/settings/` — личные настройки, `/admin/` — админ-панель, `/django-admin/` — Django admin

**Контекст:** Исторически в проекте URL `/preferences/` вёл на личные
настройки пользователя (профиль, интерфейс, почта, пароль), а
`/settings/` — на админскую панель приложения (пользователи,
подразделения, справочники, интеграции). При этом Django admin занимал
`/admin/`. Пользователи регулярно путались: «Настройки» в шапке у
менеджера и у администратора вели в разные места, и слово settings в
URL не означало настройки пользователя.

**Решение:** Переименовано:
- `/preferences/*` → `/settings/*` (личные настройки)
- старый `/settings/*` (админ-панель) → `/admin/*`
- Django admin `/admin/` → `/django-admin/`

Имена `name=` в `path()` не менялись (`preferences_ui`,
`settings_dashboard` и т.д.) — это внутренние идентификаторы для
`reverse()`. Так все `{% url %}` в шаблонах автоматически начинают
рендерить новые URL без правок шаблонов. Хардкод-пути (112 в шаблонах
+ 47 в Python) отдельно заменены механически.

**Причина:**
1. `/settings/` для обычного пользователя — это то, что он ожидает
   увидеть под словом «Настройки» в своём меню (личный профиль, не
   админка).
2. `/admin/` — термин, который прямо говорит «это админ-панель
   приложения», без двусмысленности.
3. Django admin — это техническая админка Django, а не админка CRM.
   Перенос на `/django-admin/` освобождает короткий путь для бизнес-
   админ-панели и делает явным, что это два разных интерфейса.

**Альтернативы:**
- Alias-подход (добавить новые пути рядом со старыми, не трогать
  старые) — отвергнут. Django разрешает URL сверху вниз: новый
  `/settings/preferences_view` не матчится, если выше уже есть старый
  `/settings/admin_view`. Пришлось бы переставлять порядок или
  суффиксить name, что ломает `reverse()`.
- Рефактор только имён (`name=`) без смены URL — отвергнут. Тогда
  задача «поменять URL» вообще не решается.

**Риск:** старые закладки пользователей (`/preferences/profile/`,
`/settings/users/`) больше не работают. Допустимо: staging, пользователи
редизайн тестируют заново. На прод выкатывать только после явного
согласия.

**Проверки:**
- `grep /preferences/ backend/` → 0 вхождений
- `backend/ui/urls.py`: 11 `settings/*` + 68 `admin/*`
- `backend/crm/urls.py`: `django-admin/`
- `python -c "import ast; ast.parse(...)"` → ok

---

## [2026-04-15] v2 partial-views — параллельные endpoints, а не рефакторинг v1

**Контекст:** Редизайн Фаза 2 требует открывать задачи в модалке
(view/edit/create) без перезагрузки страницы. Существующий
`task_create` (`ui/views/tasks.py:405`) — большой (~200 строк),
связан с RRULE, apply_to_org, messages framework, редиректами
и частичной AJAX-поддержкой.

**Решение:** Создаём **отдельные** thin views с суффиксом
`_v2_partial` (`task_create_v2_partial`, `task_view_v2_partial`,
`task_edit_v2_partial`) и отдельные URL `/tasks/v2/...`. v1
`task_create`/`task_view`/`task_edit` остаются без изменений.

**Причина:**
1. Рефакторинг v1 задел бы RRULE-ветку, apply_to_org и все
   существующие сценарии — высокий риск регрессии на живой системе.
2. v2 partial-views имеют **другой контракт**: POST → JSON или HTML
   с ошибками валидации (status 422), без redirect/messages.
3. Параллельные endpoints позволяют включать v2 постранично
   (dashboard → /tasks/ → карточка компании) без «всё или ничего».
4. После стабилизации v2 старые endpoints можно удалить одним
   коммитом, а промежуточный период у нас минимальный риск.

**JSON-контракт v2 partial POST:**
- Успех: `{ok:true, toast:"...", id:"...", close:true}` — v2_modal.js
  закроет модалку, покажет toast, задиспатчит `v2-modal:saved`.
- Валидация: `status=422` + HTML-фрагмент формы — v2_modal.js
  перерисует тело модалки, ошибки inline.
- Ошибка прав: `{ok:false, error:"..."}` + status 403/400 → toast.

**Глобальное событие `v2-modal:saved`:** страницы сами решают, что
делать (reload, diff, partial refresh). Пока используется
тривиальный `window.location.reload()` — позже можно заменить на
умный patch конкретных карточек.

**Альтернативы:**
- Рефакторить v1 `task_create` — отвергнуто, слишком высокий риск.
- HTMX/Alpine — отвергнуто, не хотим добавлять новую библиотеку.
- Отдельный app `tasks_v2/` — избыточно, достаточно отдельных
  функций в том же `ui/views/tasks.py`.

## [2026-04-15] Общая база клиентов для всех 3 подразделений

**Контекст:** Аудит 2026-04-14 (P0-02) пометил `accounts/scope.company_scope_q` как утечку данных — функция возвращает пустой `Q()`, т.е. любой пользователь видит все компании всех подразделений (ЕКБ, Тюмень, Краснодар).

**Решение:** Оставляем как есть — это намеренное бизнес-правило, не баг. База клиентов общая, видимость не ограничивается по подразделению.

**Причина:** При входящем обращении (звонок 8-800, письмо на общую почту, заявка с сайта) оператор должен:
1. Найти клиента в общей базе по телефону/email/ИНН.
2. Увидеть текущего владельца (менеджер + подразделение).
3. Смаршрутизировать/передать обращение в нужное подразделение.

Если бы база была разрезана по подразделениям, оператор ЕКБ не нашёл бы клиента из Тюмени при входящем и создал бы дубль карточки — это хуже, чем «утечка видимости».

**Разграничение доступа** реализовано не на видимости, а на **правах редактирования/забора владения** (кто может менять владельца, редактировать чужую карточку и т.д.) — через permissions/policy, а не через queryset-фильтры.

**Альтернативы:**
- Фильтр `Q(branch=user.branch)` для MANAGER — **отвергнуто**: ломает сценарий входящего из чужого региона.
- Два режима (read-all, write-own) через разные queryset'ы — избыточно, уже покрыто правами на уровне actions.

**Что сделано:** Расширен docstring `scope.company_scope_q` с явным указанием бизнес-правила и ссылкой на этот ADR, чтобы следующий аудит не поднимал тревогу повторно.

**P0-02 закрыт** в `knowledge-base/synthesis/state-of-project.md` как ложная тревога.

---

## [2026-04-06] gthread вместо gevent для Gunicorn

**Контекст:** SSE-стримы (widget 25с + operator 30с + notifications 55с) полностью блокировали 2 sync-воркера Gunicorn. Нужен concurrency для параллельной обработки SSE и обычных API-запросов.

**Решение:** `--worker-class gthread --workers 4 --threads 8` (32 параллельных соединения).

**Альтернативы:**
- gevent (greenlets, тысячи соединений) — **отвергнуто**: monkey-patching ломает psycopg3 (`Queue[T]` не subscriptable, TypeError при старте worker)
- Увеличить sync workers до 8-10 — неэффективно, каждый worker = отдельный процесс с полной копией Django
- eventlet — те же проблемы с monkey-patching что и gevent

**Причина:** gthread использует реальные POSIX-потоки, совместим со всем стеком включая psycopg3. 4×8=32 потока достаточно для staging (3 SSE + остальные API).

---

## [2026-04-05] CORS для виджета: разделение nginx/Django

**Контекст:** Браузер получал дублирующиеся `Access-Control-Allow-Origin` заголовки — nginx и Django оба добавляли CORS. Chrome/Firefox отвергают ответ при дублях.

**Решение:** Разделение:
- Nginx: обработка OPTIONS preflight для `/api/widget/` (возвращает 204)
- Django: добавление CORS на ответы через `_add_widget_cors_headers()`
- django-cors-headers: только для основного API (`/api/`), НЕ для виджета

**Альтернативы:**
- Только django-cors-headers — **отвергнуто**: middleware перехватывает OPTIONS до view-кода, custom handler не работает для preflight
- Только nginx CORS — **отвергнуто**: nginx не знает какие origins разрешены для конкретного inbox

**Причина:** nginx быстро обрабатывает preflight (без обращения к Django), Django имеет контекст для валидации origin на ответах.

---

## [2026-04-02] SSE вместо WebSocket для real-time виджета

**Контекст:** Виджет встраивается на внешние сайты. Нужен real-time push сообщений от сервера к клиенту.

**Решение:** SSE (Server-Sent Events) с короткими соединениями (25-55 секунд) и автоматическим reconnect.

**Альтернативы:**
- WebSocket (Django Channels уже есть) — **отвергнуто для виджета**: сложнее CORS, EventSource API проще, SSE достаточно для server→client push
- Long polling — **отвергнуто**: больше запросов, больше latency

**Причина:** SSE работает через обычный HTTP (проще CORS), EventSource автоматически reconnect'ит, для one-directional push достаточно. WebSocket оставлен для оператор-панели (используется через Django Channels).

---

## [2026-03] PostgreSQL FTS вместо Elasticsearch/Typesense

**Контекст:** Нужен поиск компаний — нечёткий + полнотекстовый.

**Решение:** PostgreSQL FTS (tsvector/tsquery) + pg_trgm (similarity).

**Альтернативы:**
- Elasticsearch — тяжёлый, отдельный сервис, избыточен
- Typesense — был в docker-compose, но удалён (deprecated в settings.py)

**Причина:** Нет лишних зависимостей, достаточная производительность для текущего масштаба (~тысячи компаний). Переиндексация: 1 Celery-задача в день.

---

## [2026-03] Fernet для шифрования SMTP-паролей

**Контекст:** SMTP-пароли хранятся в БД, нужно шифрование.

**Решение:** Fernet symmetric encryption (библиотека `cryptography`).

**Альтернативы:**
- AES-GCM напрямую — больше кода, Fernet — wrapper над AES-CBC
- HashiCorp Vault — overkill для текущего масштаба

**Причина:** Fernet — простой, стандартный, обратимый (нужен для отправки). Ключ в env: `MAILER_FERNET_KEY`.

---

## [2026-03] Единая ветка main

**Контекст:** Ранее мессенджер разрабатывался в feature-ветке. После слияния (2026-04-02) — нужно ли продолжать feature-ветки?

**Решение:** Одна ветка `main` для всего.

**Альтернативы:**
- Git Flow (develop, feature/*, release/*) — избыточно для одного разработчика

**Причина:** Один разработчик + Claude Code. Staging защищает от ошибок на проде. Feature-ветки добавляют complexity без benefit.

---

## [2026-04-15] Роль `sales_head` в коде остаётся, «РОП» — только в UI

**Контекст:** При аудите ролей обнаружено, что в `User.Role` значение — `sales_head` («Sales Head»), а в речи и в UI бизнес называет эту роль «РОП». Три имени одной роли (`sales_head` / «Руководитель отдела продаж» / «РОП») — источник путаницы.

**Решение:** В коде оставляем `SALES_HEAD = "sales_head"`. В UI везде пишем «РОП». Переименование кода не делаем.

**Альтернативы:**
- Переименовать в коде `sales_head` → `rop`: требует миграции БД (смена значения TextChoices) + правки в 50+ местах + смена существующих записей в `accounts_user.role`. Риск ошибки при миграции, особенно на данных прода, которые ещё не видим.

**Причина:** Минимальный риск. Текстовая замена «Руководитель отдела продаж» → «РОП» в шаблонах безопасна. Для будущих разработчиков добавляем комментарий в `models.py` и этот ADR.

**Следствие:** При любом упоминании роли в новом коде/шаблонах используем «РОП». При чтении кода помнить, что `sales_head` == «РОП».

---

## [2026-04-15] Поле `User.data_scope` остаётся частично используемым (отложено)

**Контекст:** При аудите обнаружено, что `User.data_scope` (`GLOBAL` / `BRANCH` / `SELF`) применяется только в модуле `messenger` (в `visible_conversations_qs`). В `companies` и `tasksapp` это поле игнорируется — там фильтрация идёт напрямую по `role` и `branch`. Это непоследовательность.

**Решение:** В рамках редизайна 2026-04 поле **не трогаем**. Оставляем работающим в мессенджере, в остальных модулях не используем. Задокументировано в [docs/roles-access-matrix.md](./roles-access-matrix.md) раздел 4.5.

**Альтернативы:**
- **A.** Расширить `data_scope` на компании и задачи — добавляет гибкость (matrix `role × data_scope`), но увеличивает complexity и риск ошибок. Требует полного пересмотра фильтрации.
- **B.** Удалить поле полностью — требует рефакторинга мессенджера и миграции БД на не-пустой базе staging.
- **C.** ⭐ Оставить как есть, вернуться отдельной задачей после стабилизации редизайна.

**Причина:** В редизайне приоритет — UX/UI, не рефакторинг прав. Поле не мешает. Решение сознательное, не по забывчивости.

**Обязательство:** Вернуться к этому решению после завершения Шага 4 редизайна. Либо интегрировать `data_scope` в компании/задачи, либо удалить из мессенджера и заменить на проверки по `role+branch`. Зафиксировано в `docs/roadmap.md`.

---

## [2026-04-15] Роль «Тендерист» — новый read-only участник для тендерного отдела

**Контекст:** Появилась потребность добавить роль для сотрудников тендерного отдела. Им нужно читать всю базу компаний (контекст для оценки тендеров), писать заметки, ставить себе напоминания — но не вести клиентов и не участвовать в live-chat.

**Решение:** Добавить роль `TENDERIST = "tenderist"` в `User.Role`. Правила:
- Компании: только чтение, заметки — да, редактирование — нет, быть ответственным — нет
- Задачи: как менеджер (свои)
- Почта: как менеджер (не ограничиваем)
- Мессенджер: полностью скрыт раздел, исключён из round-robin
- Settings/Analytics: нет доступа

Детали в [docs/roles-access-matrix.md раздел 7.2](./roles-access-matrix.md#72-целевая-роль-тендерист-tenderist).

**Альтернативы:**
- Флаг `is_tenderist` на пользователе вместо новой роли — усложняет matrix (роль × флаг), плохо композируется с `role`
- Использовать `GROUP_MANAGER` с ограниченными policy rules — GROUP_MANAGER сейчас имеет максимальные права, переопределять их через PolicyRule в БД ненадёжно

**Причина:** Отдельная роль — чистая модель. Policy baseline + per-object checks дают безопасный результат. Видимость компаний у тендериста — через `visible_companies_qs` (и так общая).

**Следствие:** Все новые проверки ролей в коде должны учитывать, что `tenderist ⊂ read-only`. Helper-функции: `user.is_tenderist` property; `has_role` templatetag; при сомнениях — отказывать.
