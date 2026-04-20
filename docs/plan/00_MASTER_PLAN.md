# GroupProfi CRM — Мастер-план доведения до продакшн-состояния

> Документ для Dmitry. Стратегический план + навигация по 15 волнам промптов для Claude Code.
> Версия плана: 1.0 (апрель 2026). Язык проекта: Russian-first.

---

## 1. Цели и не-цели

**Цели**
- Довести CRM до уровня, когда любой новый менеджер включается за день без «шаманских» инструкций.
- Каждый модуль (клиенты, сделки, задачи, чат, рассылки, звонки, аналитика) — работает надёжно, предсказуемо, без «дыр» в правах.
- Закрыть критичные блокеры безопасности (Policy Engine OBSERVE_ONLY → ENFORCE, 2FA для админов, 152-ФЗ opt-in, audit log полный).
- Установить наблюдаемость (Sentry + Prometheus + Grafana + Loki + pg_stat_statements).
- Подготовить фундамент для Android-приложения click-to-call (без iOS, без записи звонков).
- Закрыть `E2E Playwright` по всем user journey × ролям. Менеджеры не должны тестировать вручную «каждую модалку».

**Не-цели (V2 и позже)**
- SaaS multi-tenancy (остаёмся single-tenant для GroupProfi).
- Омниканальность live-chat кроме сайт-виджета (Telegram/WhatsApp/VK/Instagram — V2).
- AI-фичи в чате/рассылках (подсказки ответов, саммари).
- ClickHouse, Kafka, Kubernetes.
- Drag-n-drop редактор писем (делаем «богатый» HTML-редактор с переменными и превью).
- 1С-интеграция (отдельный стрим с 1С-программистом, не сейчас).
- iOS-приложение.
- Запись звонков.
- Google Calendar.

---

## 2. Принципы исполнения

1. **Только бесплатные инструменты.** Платим только за хостинг (VPS). Всё остальное — open-source или free-tier. Никаких подписок на Sentry, Grafana Cloud, Datadog, Browserbase, Linear, Figma Pro и т.п. Конкретные замены:
   - Sentry → **GlitchTip** (self-hosted, API-совместим с Sentry SDK)
   - Grafana Cloud → **Grafana + Prometheus + Loki** self-hosted
   - S3 managed → **MinIO** self-hosted на том же или соседнем VPS
   - Browserbase → локальный Playwright в CI
   - Sentry Team plan — **НЕ используем**
2. **Без экономии токенов Claude.** Claude Code на каждом этапе имеет право спавнить subagents, запускать Playwright, читать документацию через Context7. Цель — качество, не дешевизна. Токены — это время разработчика, которое стоит дороже.
3. **Документация — first-class артефакт.** После каждой волны обновляются: `docs/architecture.md`, `docs/decisions.md`, `docs/problems-solved.md`, `docs/current-sprint.md`, `docs/roadmap.md`. При необходимости создаются новые файлы в `docs/runbooks/`, `docs/adr/`, `docs/specs/`.
4. **Каждая волна завершается green CI.** `ruff check`, `mypy` (где включён), `pytest` (1179+ тестов), `coverage report --fail-under=<текущий порог>`, Playwright smoke. Нельзя переходить к следующей волне на красном CI.
5. **Coverage gating — постепенный.** Стартуем с `--cov-fail-under=40` (после baseline в W0.1). После каждой волны поднимаем порог на +5%: W0→45, W1→50, W2→55, ..., W13→80, W14→85. Цель «80% к концу» — это не блокер каждой волны, а линейная траектория. Правила в `pyproject.toml` + CI. Критичные сервисы (policy, campaigns, phonebridge, messaging) — ≥90% к W14.
6. **Feature flags везде, где трогается UX.** Новый функционал выкатывается за флагом, после приёмки флаг удаляется (вместе с legacy-кодом). Используем `django-waffle` (см. Wave 0).
7. **Policy Engine — приоритет 0.** Переход OBSERVE_ONLY → ENFORCE делаем не в конце, а в Wave 2. Всё последующее проверяется в режиме ENFORCE.
8. **Rollback в каждом этапе.** Миграции БД — только с реверсивной парой. Деплой — через атомарный swap + ключ `--no-input` для auto-migrate.
9. **Тесты перед кодом, где это дисциплинирует.** Для критичных сервисов (policy, передача клиентов, рассылки) — TDD. Для UI — E2E сценарии заранее согласованы.
10. **Никакой «тишины».** Если Claude Code упирается в неопределённость — он создаёт запись в `docs/open-questions.md` и продолжает с явным допущением, помеченным `ASSUMPTION:`.

---

## 3. Стек после доводки (подтверждённый)

| Слой | Технология | Комментарий |
|------|-----------|------------|
| Backend | Django 6.0.4, Python 3.13 | не меняем |
| DB | PostgreSQL 16 | + pg_stat_statements, WAL-G для PITR |
| Cache / Pub-sub | Redis 7-alpine | 4 логические БД: cache, celery-broker, celery-result, channels |
| Async | Celery 5.5.2 + beat | + celery-exporter для Prometheus |
| WebSocket | Django Channels 4.2 + Daphne | не меняем |
| Frontend | Django Templates + Tailwind 3.4 + vanilla JS | НЕ переписываем в SPA. Но: унифицируем токены v3, убираем v2-дубли, TypeScript для нового JS. |
| UI | Heroicons Outline + собственная дизайн-система | формализуем в `docs/ui/DESIGN_SYSTEM.md` |
| Auth | Django session + DRF SimpleJWT + MagicLinkToken | + **TOTP 2FA (django-otp)** для ADMIN и опционально для всех |
| Storage | **MinIO self-hosted** (S3-совместимое) | **миграция с локального media/ → MinIO**. Альтернатива: keep local + rsync backup на второй VPS (если MinIO не тянем). |
| Mail | smtp.bz (текущий платный) + кастомный SMTP pool (Fernet-крипт) | + suppression list + bounce handling через webhook (если smtp.bz даёт) или IMAP (fallback) |
| Mobile | Kotlin + Jetpack Compose | Android-only, отдельный репозиторий. OpenAPI spec живёт в этом репо как `docs/api/openapi.yaml`, Android тянет git submodule. |
| Push | Web Push (pywebpush, self-hosted) + FCM (firebase-admin, free tier) | |
| Testing | pytest + pytest-django + coverage + Playwright (локально в CI) | coverage gating: +5% за волну, 85% к концу |
| Observability | **GlitchTip** (self-hosted Sentry-совместимый) + Prometheus + Grafana + Loki + UptimeRobot (free) | всё self-hosted на том же VPS или соседнем |
| CI/CD | GitHub Actions (free tier 2000 min/mo достаточно для private repo) | + required checks, merge queue |
| Linting | ruff + black + mypy (strict для новых модулей) + gitleaks + pip-audit + bandit + semgrep (free) | |

---

## 4. Карта волн

| № | Волна | Кол-во промптов | Зависимости | Можно параллелить? |
|---|-------|-----------------|-------------|---------------------|
| 0 | Фундамент и аудит | 6 | — | нет |
| 1 | Архитектурная рефакторизация | 8 | W0 | внутри: да (3 потока) |
| 2 | Security, Auth, Policy ENFORCE | 7 | W1 | частично |
| 3 | Core CRM hardening | 8 | W2 | да |
| 4 | Tasks, Notifications, Reminders | 5 | W3 | частично |
| 5 | Live-chat полировка | 7 | W2 | да (не пересекается с W6) |
| 6 | Email-рассылка | 7 | W2 | да (не пересекается с W5) |
| 7 | Телефония + Android | 9 | W2 | Android ≠ Django, параллельно |
| 8 | Аналитика | 6 | W3, W5, W6, W7 | — |
| 9 | UX/UI унификация | 8 | W1 | частично с W3-W7 |
| 10 | Инфраструктура и DevOps | 7 | — (можно с начала) | да |
| 11 | API split (public/internal) | 4 | W2 | — |
| 12 | Интеграции (Яндекс.Метрика, IMAP) | 4 | W6 | — |
| 13 | Performance & Optimization | 5 | W1-W8 | — |
| 14 | Final QA (Playwright, a11y, load, security) | 10 | все | внутри: да |
| 15 | Документация и передача | 4 | все | — |

**Итого: ~105 промптов.**

Рекомендованный порядок запуска (если идти строго последовательно): `0 → 10 → 1 → 2 → 3 → 4 → 9 → 5 → 6 → 7 → 11 → 12 → 8 → 13 → 14 → 15`.

Волна 10 (инфраструктура) запущена рано, чтобы наблюдаемость и бэкапы были в наличии на время всей работы. Волна 9 (UX) идёт после W4 и параллельно с W5-W7, чтобы новые экраны сразу строились по унифицированной дизайн-системе.

---

## 5. Параллельные потоки (для 2-3 Claude Code сессий в worktree)

Когда можно запускать 2-3 параллельные сессии:

**Поток A (core backend):** W0 → W1 → W2 → W3 → W8 → W13
**Поток B (messaging):** W5 ∥ W6 → W12
**Поток C (mobile/phone):** W7.Android (отдельный репозиторий) — независим от Django-потока
**Поток D (infra/ops):** W10 — с самого начала, в отдельном worktree

**Важно:** W2 (Policy ENFORCE) — синхронизационный барьер. Ни один из потоков не вливается в main, пока Policy Engine не переведён в ENFORCE и все правила не прописаны.

---

## 6. MCP серверы — фактическое состояние и замены

**Подтверждены в текущей сессии:**
- ✅ `mcp__context7__*` (документация Django/DRF/Celery/Postgres)
- ✅ `mcp__playwright__*` (E2E, визуальная регрессия)

**Отсутствуют в сессии** (либо не установлены, либо не активированы). Используем замены:

| Отсутствующий MCP | Замена | Комментарий |
|---|---|---|
| PostgreSQL MCP | `docker compose exec db psql` / `ssh prod-server 'psql'` | EXPLAIN ANALYZE и pg_stat_statements — через psql |
| GitHub MCP | `gh` CLI (установлен локально) | PR, issues, workflow runs |
| Filesystem MCP | нативные Read/Write/Bash | Claude Code и так имеет полный доступ |
| Obsidian MCP | нативный Write в `docs/` + автоматический sync Obsidian vault → `docs/` | опционально установить — полезно для атомарных обновлений документации |
| Docker MCP | `docker compose` CLI | не требуется отдельный MCP |
| Sentry MCP | → **GlitchTip** (см. W10.4) даёт API + web UI; для отладки достаточно web UI | — |

**До старта W0 — опционально установить:**
- Obsidian MCP — если активно ведёшь vault GroupProfi.

**Никакие MCP не являются блокерами** — проект выполним полностью на нативных инструментах Claude Code + `psql` / `gh` / `docker compose`.

---

## 7. Обязательные инструменты и скиллы

Каждый промпт при необходимости вызывает:

- `mcp__context7__*` — актуальная документация для Django 6, DRF 3.15+, Celery 5.5, Channels 4.2, Postgres 16
- `mcp__playwright__*` — E2E тесты, визуальная регрессия
- `Agent` tool (Task) — для параллельного аудита (до 5 sub-agents одновременно)
- `Read`, `Edit`, `Write`, `Bash`, `Grep`, `Glob` — базовые

**CLI замены отсутствующих MCP:**
- `psql` через `docker compose exec db psql` — вместо PostgreSQL MCP
- `gh` CLI — вместо GitHub MCP
- `docker compose` — вместо Docker MCP

**Skills** (если есть):
- `frontend-design` — для всех UI-экранов (Wave 9)
- `skill-creator` — чтобы создать свои кастомные skills для рекуррентных задач (например: `django-view-splitter`, `policy-rule-writer`)

---

## 8. Критерии «готовности» всего продукта (Global DoD)

Считаем, что продукт дошёл до продакшн-состояния SaaS-уровня, когда **одновременно выполнены**:

1. ✅ CI зелёный. `ruff`, `mypy` (для новых модулей), `pytest`, `bandit`, `pip-audit`, `gitleaks`, `playwright smoke`.
2. ✅ Backend coverage ≥ 80%, критические сервисы (policy, billing-like, messaging, campaigns, phonebridge) ≥ 90%.
3. ✅ Playwright покрывает 100% user-journey матрицы (8 ролей × 15 ресурсов × 6 действий = ~720 сценариев, сокращённо до ~150 ключевых).
4. ✅ Policy Engine в ENFORCE, ни одного `@policy_required` без правила.
5. ✅ 2FA TOTP для ADMIN и BRANCH_DIRECTOR обязателен; для остальных ролей опционален.
6. ✅ Audit log покрывает 100% mutating операций (создание/изменение/удаление сущностей, передача клиентов, вход в систему, изменение ролей, отправка рассылок, звонки).
7. ✅ GlitchTip unhandled-error rate < 0.1% на недельном окне прод-трафика.
8. ✅ Prometheus/Grafana дашборды: System Health, Django Performance, Celery Queues, Postgres, Business KPI. Алерты настроены.
9. ✅ Бэкапы: Postgres WAL-G с PITR, media на MinIO с versioning. Еженедельный restore-drill на staging.
10. ✅ Документация: все 15 runbooks готовы (см. Wave 15), onboarding нового менеджера < 30 минут.
11. ✅ Accessibility: WCAG 2.1 AA на ключевых экранах (Company list, Company detail, Deal, Task, Chat, Analytics).
12. ✅ Mobile-responsive: все страницы адаптированы от 360px до 1920px.
13. ✅ i18n: все строки вынесены в `.po`, gettext работает (пока только русский, но задел для английского).
14. ✅ Нагрузочное тестирование: 100 RPS на `/api/v1/companies/` без деградации > 200ms p95.
15. ✅ Security scan: OWASP ZAP baseline чистый, Bandit без high/critical, npm audit без high/critical.

---

## 9. Риск-регистр

| Риск | Вероятность | Удар | Митигация |
|------|-------------|------|-----------|
| Policy ENFORCE сломает прод (юзеры теряют доступ) | Высокая | Высокий | **Preconditions:** (1) 100% audit @policy_required → все mutating endpoints покрыты; (2) Grafana dashboard «denied requests» (из W10.4) доступен ДО перехода; (3) kill-switch `POLICY_ENGINE_ENFORCE=False` через **env var** (systemd env file, перезагрузка через `systemctl reload` без редеплоя), не через `settings.py`; (4) матрица ролей×ресурсов как JSON (`policy/fixtures/role_matrix.json`) + автотесты на ~150 сценариев; (5) Shadow-режим SHADOW_ENFORCE минимум 2 недели на проде с PolicyDecisionLog; (6) gradual rollout: 10%→25%→50%→100% по `user.pk % 100`. |
| Миграция `ui/views/*.py` сломает URL/шаблоны | Средняя | Средний | Детальный Playwright smoke перед/после каждого рефакторинга. Feature flag `UI_V3B_DEFAULT`. |
| S3-миграция media потеряет файлы | Низкая | Высокий | Dual-write 48h (локально + MinIO) → переключение чтения → удаление локальных через месяц. |
| TOTP 2FA заблокирует админа без recovery | Средняя | Высокий | Recovery codes (10 шт) при включении + email OTP fallback + CLI-команда `reset_2fa <user_id>` (только для `is_superuser=True`). **Мягкая миграция 2 недели**: первые 2 недели — баннер «предложено», после — mandatory on next login. |
| Ресурс Android-приложения не согласован с backend phonebridge API | Средняя | Средний | OpenAPI 3.1 spec — `docs/api/openapi.yaml` в этом репо. Android-репо тянет git submodule. Contract tests на CI обоих репо. |
| Playwright-тесты «флакают» | Высокая | Средний | Жёсткий лимит retry=1, фикстуры изолированы, data-reset между тестами через factory_boy + pytest-playwright. |
| Bounce handling ломается | Средняя | Низкий | Если smtp.bz даёт webhook → webhook (простое). Иначе IMAP в изолированном Celery-queue, circuit breaker, fallback на ручной suppression. |
| MinIO на одном VPS = single-point-of-failure | Средняя | Средний | Еженедельный rsync в отдельный bucket на соседнем VPS (если есть) или rclone в другое место. Versioning в MinIO включён. |
| Переход с SMTP-per-campaign на pool ухудшит deliverability | Средняя | Средний | Проверка SPF/DKIM/DMARC через mail-tester перед каждым pool-хостом. Постепенный warm-up. |
| Coverage gating блокирует развитие | Средняя | Низкий | Постепенный +5% за волну, не единовременное 80%. Если этап реально не тянет — приём повысить на следующей волне. |

---

## 10. Как использовать эти файлы

1. Открой `01_wave_0_audit.md`. Прочитай всю волну целиком — пойми что ожидается на выходе.
2. Запусти Claude Code в корне проекта `/opt/proficrm/` (или в worktree для параллели).
3. Скопируй **один промпт за раз** в Claude Code. Дай ему отработать.
4. После завершения промпта — прогони CI локально (`make ci` или эквивалент), проверь, что документация обновилась.
5. Коммить изолированно, каждый промпт = один PR (или один коммит при trunk-based разработке).
6. Переходи к следующему промпту.

**Правила параллелизма (важно):**

- ✅ **OK:** `Agent` tool спавнит до 5 subagents в одной сессии — они работают в одном контексте, конфликтов нет.
- ✅ **OK:** 2–3 параллельные Claude Code сессии в разных `git worktree add` — если они трогают **разные файлы / разные Django apps**.
- ❌ **НЕ OK:** две Claude Code сессии в одной рабочей копии — неизбежные конфликты файлов.
- ❌ **НЕ OK:** две сессии в разных worktree, но обе правят `ui/views/_base.py` или `settings.py` — merge hell.

Правило нарезки: каждая параллельная сессия должна иметь **явно выделенную область** (Django app, подкаталог, или одну конкретную волну, которая не пересекается с другими).

---

## 11. Регулярные проверки между волнами

После каждой волны запусти этот чек-лист (руками или отдельным промптом):

- [ ] `pytest` — все тесты зелёные
- [ ] `ruff check .` — без ошибок
- [ ] `mypy backend/` — без ошибок в новых модулях
- [ ] `python manage.py check --deploy` — без warnings (в проде)
- [ ] `python manage.py makemigrations --dry-run --check` — нет незакоммиченных миграций
- [ ] `bandit -r backend/ -ll` — без medium/high
- [ ] `pip-audit` — без known CVE
- [ ] `playwright test --grep @smoke` — зелёный smoke
- [ ] `docs/current-sprint.md` — обновлён
- [ ] `docs/decisions.md` — записаны принятые решения
- [ ] `docs/problems-solved.md` — записаны нетривиальные проблемы

---

## 12. Формат промптов

Каждый промпт в волнах имеет структуру:

```
# Этап N.M: <Название>

## Контекст
Что есть сейчас, что сделано в предыдущих этапах, почему это нужно.

## Цель
Одно предложение. Измеримо.

## Что делать
Пошаговый план. Конкретные файлы, классы, эндпоинты.

## Инструменты
MCP, skills, subagents, которые должен использовать Claude Code.

## Definition of Done
Чек-лист с измеримыми критериями. Это то, по чему ты будешь принимать работу.

## Артефакты
Какие файлы должны появиться/измениться.

## Валидация
Команды для локальной проверки.

## Откат
Если что-то пошло не так.

## Обновить в документации
Какие файлы в docs/ должны быть обновлены.
```

---

## 13. Контакты с продукт-стороной

На протяжении всех волн держи связь с финальным приёмщиком — менеджерами в Тюмени/ЕКБ/Краснодаре. План:

- **Раз в 2 недели** — демо новой функциональности группе из 3-5 менеджеров.
- **Раз в неделю** — выгрузка лога ошибок Sentry + отчёт «что изменилось».
- **После Wave 8 (Аналитика)** — обязательная сессия с SALES_HEAD всех трёх филиалов для валидации дашбордов.
- **После Wave 14 (QA)** — 2 недели реального использования в одном из филиалов (например, Екатеринбург) прежде чем катить в остальные.

---

## 14. Итого

Оценка трудозатрат для соло-разработчика + Claude Code: **4–6 месяцев** при 20-30 часах в неделю. С выделением 3 параллельных потоков — сокращается до **3–4 месяцев**.

Ключевой рычаг: W14 (финальный QA) сокращает мануальное тестирование с «недели ручного труда менеджеров» до «одного запуска Playwright». Не экономь на нём.

Поехали. Начинай с `01_wave_0_audit.md`.
