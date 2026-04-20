# Инструкция для Claude Code

## При старте каждой сессии прочитать:

1. **CLAUDE.md** (этот файл)
2. **docs/current-sprint.md** — что сейчас в работе, где остановились, что следующее

Это обязательно. Не начинать работу, не прочитав оба файла.

Дополнительно, по ситуации:
- Задача связана с архитектурой или новым модулем → прочитать `docs/architecture.md`
- Задача похожа на ранее решённую проблему → проверить `docs/problems-solved.md`
- Нужно понять почему выбран конкретный подход → прочитать `docs/decisions.md`

## Аудит и граф знаний

**Два источника правды о состоянии проекта:**

1. **`docs/audit/README.md`** — Wave 0.1 audit snapshot (2026-04-20). **Свежее и детальнее графа.** Содержит: 70 моделей + 236 views + 18 Celery tasks + 112 templates + 150 API endpoints, метрики (LOC/CC/MI/coverage), **top-20 tech-debt**. Смотри сюда **первым** при планировании рефакторинга.
2. **`docs/audit/hotlist.md`** — top-7 файлов/артефактов «трогать первыми» в W1-W3. Краткий prioritized индекс для следующих сессий.
3. **`docs/plan/00_MASTER_PLAN.md`** — план 15 волн + `docs/plan/01_wave_0_audit.md` ... `16_wave_15_docs.md`.

## Граф знаний проекта (graphify)

В папке `graphify-out/` лежит построенный граф знаний всего проекта: 5281 узел, 20558 рёбер, 227 сообществ. Это основной навигационный инструмент для ориентации в коде.

**Файлы:**
- `graphify-out/graph.json` — сам граф (узлы + рёбра + сообщества, 11 МБ)
- `graphify-out/GRAPH_REPORT.md` — сводка: god-узлы, сюрпризы, сообщества, gaps
- `graphify-out/manifest.json` — хэши файлов для `--update` (инкрементального обновления)

**Когда использовать граф ДО чтения файлов вручную:**

| Ситуация | Команда |
|----------|---------|
| Надо понять как связаны два модуля/концепта | `/graphify path "ConceptA" "ConceptB"` |
| Надо узнать что такое X и на что влияет | `/graphify explain "X"` |
| Широкий вопрос об архитектуре или потоке | `/graphify query "..."` (BFS) |
| Проследить конкретную цепочку зависимостей | `/graphify query "..." --dfs` |
| Обзор зоны кода, где ещё не был | Прочитать нужное `### Community N` из `GRAPH_REPORT.md` |

**God-узлы (центральные абстракции, трогать с осторожностью):**
`User` (747 рёбер), `Company` (613), `Contact` (426), `Branch` (385), `ContactPhone` (341), `Task` (298), `CompanyPhone` (293), `CompanyNote` (279), `Conversation` (277), `ContactEmail` (223).

**Правило:** перед тем как задавать широкий вопрос «а где у нас X?» или читать 10 файлов подряд через Read — сначала запрос к графу. Граф быстрее и показывает связи, которые не видны линейным чтением.

**Обновление графа:**
- После крупного рефакторинга или добавления нового модуля: `/graphify . --update` (только изменившиеся файлы)
- Полный перестроит: `/graphify .` (дорого, только если сильно дрейфанула структура)

**НЕ коммитить** `graphify-out/` в git — это локальный инструмент. Добавить в `.gitignore` если ещё не добавлен.

## Маршрутизация скиллов (автоматический запуск)

Когда пользователь формулирует задачу, подпадающую под триггер из таблицы ниже — **вызвать соответствующий скилл через Skill tool ДО основной работы**, не спрашивая разрешения. Приоритет у верхних строк (критичные), ниже — ситуативные.

### 🔴 Критично — запускать автоматически

| Если задача / ситуация | Автоматически запустить |
|------------------------|-------------------------|
| «Как устроен X?», «Где используется Y?», «Как связаны A и B?», обзор кода | `/graphify` (query / path / explain) |
| Баг, падающий тест, неожиданное поведение | скилл `systematic-debugging` |
| Новая фича / багфикс / рефакторинг | скилл `tdd-workflow` или `test-driven-development` (тесты первыми) |
| Крупная задача (>2 шагов, новый модуль, архитектурное изменение) | скилл `writing-plans` → затем `executing-plans` |
| Нужно проверить 2+ независимых области (аудит, сравнение модулей, параллельные проверки) | скилл `dispatching-parallel-agents` |
| Код касается auth / user input / secrets / API endpoints / Fernet / JWT / CSRF / CORS | скилл `security-review` |
| Перед «готово / зафиксируем / коммитим» | скилл `verification-before-completion` |
| E2E на staging (`crm-staging.groupprofi.ru`), live-chat, widget, dashboard UI-flow | скилл `playwright-skill` или `webapp-testing` |
| Крупная ветка, чтобы не ломать `main` (редизайн, миграции, рискованный рефакторинг) | скилл `using-git-worktrees` |

### 🟡 Очень полезно — запускать при явном совпадении

| Если задача / ситуация | Автоматически запустить |
|------------------------|-------------------------|
| «Посмотри код», «Отревью», «Проверь что написал» | команда `/code-review` или скилл `code-review` |
| «Готово к пушу», «Можно мёрджить», «Закрываем фазу» | скилл `finishing-a-development-branch` |
| «Архитектурное решение», «Почему так выбрали», «ADR» | скилл `adr` |
| Поиск символа / паттерна в большом модуле (`ui/views/_base.py`, `messenger/api.py`) | скилл `smart-explore` (AST) — быстрее чем Grep |
| UI-задача v2 (Notion-стиль, Tailwind-токены, компоненты) | скилл `frontend-design` |
| Аудит UI-страницы (dashboard, tasks, companies, settings) | скилл `ux-audit` |
| «Поищи баги», багхант, проверка на регрессии | команда `/health-bugs` |
| «Проверь безопасность», security-аудит, OWASP | команда `/health-security` |
| «Обнови зависимости», CVE, deprecated пакеты | команда `/health-deps` |
| «Убери мёртвый код», dead code detection | команда `/health-cleanup` |
| «Найди дубли», консолидация одинаковых функций | команда `/health-reuse` |
| Разбор ошибок из `ErrorLog` / админки | команда `/process-logs` или скилл `parse-error-logs` |
| Извлечь повторяющийся паттерн из проблемы для будущего | скилл `continuous-learning` |
| Генерация коммит-сообщения по `Fix(Module):` / `Feat(Module):` / `Harden(Module):` / `UI(Module):` | скилл `format-commit-message` или `git-commit-helper` |
| Прогон typecheck / tests / lint перед коммитом | скилл `run-quality-gate` |
| Приоритизация списка находок / багов | скилл `calculate-priority-score` |

### 🟢 Ситуативно — запускать когда пользователь явно просит похожее

| Если задача / ситуация | Автоматически запустить |
|------------------------|-------------------------|
| «Придумай», «Как лучше сделать», перед новым модулем | скилл `brainstorming` |
| «Добавь хук», «автоматически делай X» | скилл `update-config` (хуки в settings.json) |
| «Сделай recap проекта», «где мы остановились» | команда `/project-recap` |
| «Объясни архитектуру визуально», схема потока | `visual-explainer` или `/generate-web-diagram` |
| «Сравни до/после», diff архитектуры | `/diff-review` или `/plan-review` |
| «Проверь документацию против кода» | `/fact-check` |
| «Обнови карты кода / docs» | `/update-codemaps` или `/update-docs` |
| Сессия прерывается мид-задачи | скилл `pause-work` (записать стоп-точку в `current-sprint.md`) |

### ❌ НЕ запускать (мимо стека проекта)

- Офис-документы: `docx`, `pptx`, `xlsx`, `pdf` — не применимо.
- Писательство: `academic-writing`, `newsroom-style`, `story-pitch`, `newsletter-publishing`, `writing-guru`.
- Арт / графика: `algorithmic-art`, `canvas-design`, `slack-gif-creator`, `illustration-prompt`, `brand-guidelines` (Anthropic).
- Презентации: `generate-slides`, `revealjs`, `frontend-slides`, `theme-factory`.
- Другие стеки: `clickhouse-io` (Postgres), `claude-api` (не Anthropic SDK-проект), `mcp-builder`, `pmf`, `eval-harness`.
- **GSD-команды (`/gsd:*`)** — параллельная система планирования через `.planning/`. В проекте свой workflow через `docs/` + Obsidian. Смешивать запрещено.

### Правила применения

1. **Не спрашивать разрешения** на запуск скилла, если задача однозначно подпадает под триггер.
2. **Сообщать кратко**, какой скилл запускаешь и почему («Запускаю `systematic-debugging` — это падающий тест»).
3. **При конфликте приоритетов** — выбирать критичный. Например, «починить auth-баг» = `systematic-debugging` + `security-review` (оба, последовательно).
4. **Если задача не подпадает ни под один триггер** — работать без скилла, инструментами напрямую.
5. **При пограничном случае** — один вопрос пользователю, не более.

## Язык

Всегда отвечать **только на русском языке**. Без исключений. Даже если пользователь написал по-английски. Даже в compact summary. Комментарии в коде — на русском (docstrings, inline), если это не противоречит существующим конвенциям модуля. Технические термины (JWT, API, CRUD, SSE, CORS) оставлять как есть.

## Автообновление документации

Обновлять документацию **самостоятельно**, без напоминаний:

| Триггер | Файл |
|---------|------|
| Завершён логический блок работы | `docs/current-sprint.md` |
| Решена нетривиальная проблема | `docs/problems-solved.md` |
| Принято архитектурное решение | `docs/decisions.md` |
| Добавлен модуль / изменена структура / новая зависимость | `docs/architecture.md` |
| Изменился стек, структура или правила | `CLAUDE.md` |
| Появились новые идеи или планы | `docs/roadmap.md` |
| Сессия прервалась на середине | `docs/current-sprint.md` (записать стоп-точку) |

## Текущий статус проекта

**CRM ПРОФИ** — полнофункциональная CRM-система для управления компаниями, задачами, рассылками, с live-chat мессенджером.

| Параметр | Значение |
|----------|---------|
| Стек | Python 3.13, Django 6.0.1, PostgreSQL 16, Redis 7, Celery 5.4 |
| Фронтенд | Django Templates + Tailwind CSS 3.4.17 + vanilla JS |
| Контейнеризация | Docker Compose (7 сервисов) |
| Ветка | `main` (единственная) |
| Прод | `crm.groupprofi.ru` — деплой ТОЛЬКО вручную пользователем |
| Staging | `crm-staging.groupprofi.ru` — деплой через Claude Code |
| Моделей БД | **70** (10 Django-приложений — актуализировано Wave 0.1) |
| API эндпоинтов | **~150** REST (DRF) + Widget API (публичный) |
| HTML шаблонов | **112** (актуализировано Wave 0.1) |
| Views-функций/классов | **236** (+20 DRF @action-методов) |
| Тестов | **1179** test-runs / **1 240** test-функций |
| Coverage | **51 %** (baseline 2026-04-20, gate `fail_under=50`) |
| Актуальный аудит | **`docs/audit/README.md`** (snapshot 2026-04-20, Wave 0.1) |

## Архитектура — где что лежит

```
backend/
├── crm/           — ядро: settings.py, urls.py, middleware, wsgi/asgi
├── accounts/      — User, Branch, MagicLinkToken, RateLimitMiddleware
├── companies/     — Company, Contact, Deal, Note, SearchIndex (ядро CRM, 16 моделей)
├── messenger/     — Conversation, Message, Inbox, Widget API, SSE, WebSocket (16 моделей)
├── mailer/        — Campaign, MailAccount, GlobalMailAccount, SMTP, Fernet (11 моделей)
├── tasksapp/      — Task, TaskType, RRULE повторения (4 модели)
├── ui/            — views для фронтенда, дашборд, настройки (~13K LOC)
├── notifications/ — Notification, Announcement, ContractReminder (4 модели)
├── phonebridge/   — PhoneDevice, CallRequest, QR-спаривание (6 моделей)
├── audit/         — ActivityEvent, ErrorLog (retention 180/90 дней)
├── policy/        — PolicyConfig, PolicyRule
├── amocrm/        — интеграция AmoCRM
├── core/          — утилиты
├── templates/     — 112 HTML шаблонов (см. docs/audit/frontend-inventory.md)
└── static/        — CSS (Tailwind), JS (widget, operator-panel, purify)

frontend/          — Tailwind source (src/main.css)
nginx/             — staging.conf, production.conf, snippets, errors
docker/            — entrypoint.sh
scripts/           — deploy, backup, health check, тесты
docs/              — вся документация
```

## Критические правила проекта

### Деплой

> **ЗАПРЕЩЕНО** трогать `/opt/proficrm/` (прод) через Claude Code. Всегда.

Workflow: локально → `git push` → staging `git pull` → `docker build` → `up -d` → QA → пользователь деплоит на прод вручную.

### Docker

- `docker compose up -d` (не `restart`) при изменении `.env` — `restart` не перечитывает env_file
- После пересоздания web-контейнера нужен `restart nginx` (DNS имя меняется)
- Gunicorn: `--worker-class gthread --workers 4 --threads 8` (не gevent — конфликт с psycopg3)

### Мессенджер (Widget)

При добавлении нового источника виджета — чеклист:
1. Inbox `allowed_domains` — добавить домен
2. `CORS_ALLOWED_ORIGINS` в `.env` — добавить origin
3. `docker compose up -d web` (не restart!)
4. Host nginx — добавить IP в whitelist (если нужно)

CORS разделён: nginx обрабатывает OPTIONS preflight, Django добавляет CORS через `_add_widget_cors_headers()`. Не трогать `django-cors-headers` для Widget API.

### Безопасность

- SMTP-пароли: Fernet шифрование (`MAILER_FERNET_KEY`)
- Rate limiting: middleware для `/login/`, `/api/token/*`, `/api/phone/*`
- CSP: nonce per-request в production
- Serializers: explicit fields (не `__all__`)
- Widget API: rate limiting по IP и session
- localhost в CORS запрещён при DEBUG=False (ImproperlyConfigured)

### Конвенции кода

- Ветка: только `main`
- Коммиты: `Fix(Module):`, `Feat(Module):`, `Harden(Module):`, `UI(Module):`, `Chore(Module):`, `Docs(Module):`
- Django 6.0.4, Python 3.13 — используем современные фичи
- DRF для API, django-filter для фильтрации
- Celery 5.5.2 для всех фоновых задач
- Redis 7 для кэша, брокера, channel layer, typing-индикаторов
- channels 4.2.0 + daphne — WebSocket / SSE для messenger

### CI / CD

- **CI** (`.github/workflows/ci.yml`): на каждый push/PR в main — ruff + gitleaks + Django test suite (**1179 тестов**, 100% pass) + pip-audit. Coverage gate `fail_under=50` в `pyproject.toml` (baseline 51%, траектория +5%/волна).
- **Auto-deploy staging** (`.github/workflows/deploy-staging.yml`): после успешного CI на main — GitHub Actions через SSH → git pull + build + migrate + up -d. Требует secret `STAGING_SSH_PRIVATE_KEY`.
- **Production deploy**: **только вручную** по runbook `docs/runbooks/21-release-1-ready-to-execute.md`. Workflow для прода намеренно НЕ создаётся.
- Тесты запускаются с `DJANGO_SETTINGS_MODULE=crm.settings_test` (ALLOWED_HOSTS=["*"], EAGER celery, in-memory email и т.д.).

### Observability

- **Sentry free tier** (через `SENTRY_DSN` env var) — error tracking для web + celery + redis (`backend/crm/settings.py` условно инициализирует). Без DSN — no-op.
- **UptimeRobot free** — опционально, 50 HTTP-мониторов.
- **pg_stat_statements** — пока не установлен (план Релиз 2).
- Runbook активации: `docs/runbooks/40-observability-and-cicd-setup.md`.

### MCP серверы

Конфигурация: `.mcp.json` в корне проекта.

| Сервер | Назначение |
|--------|-----------|
| **Playwright** | Browser MCP — E2E тестирование через Chromium |
| **Context7** | Актуальная документация библиотек в промпт. Использовать при работе с API Django, DRF, Celery, Redis и др. — чтобы не писать код по устаревшим API |

При работе с кодом, где нужно использовать API библиотек — **сначала запросить документацию через Context7**, потом писать код.

## Серверы

| Среда | SSH | Путь |
|-------|-----|------|
| Staging (root) | `ssh -i ~/.ssh/id_proficrm_deploy root@5.181.254.172` | `/opt/proficrm-staging/` |
| Staging (sdm) | `ssh -i ~/.ssh/id_proficrm_deploy sdm@5.181.254.172` | `/opt/proficrm-staging/` |
| na4u.ru (тест) | `ssh -i ~/.ssh/id_aethr c21434@80.87.102.67` | `~/vm-f841f9cb.na4u.ru/www/` |
| Прод | **ЗАПРЕЩЕНО** | `/opt/proficrm/` |

## Документация проекта

### Основные файлы (поддерживаются Claude Code)

| Файл | Назначение |
|------|-----------|
| `CLAUDE.md` | Этот файл — главный, читается при старте |
| `docs/current-sprint.md` | Что сейчас в работе, стоп-точки — читается при старте |
| `docs/architecture.md` | Полная архитектура, стек, модули, БД, паттерны |
| `docs/decisions.md` | Лог архитектурных решений (ADR) |
| `docs/problems-solved.md` | Решённые нетривиальные проблемы |
| `docs/roadmap.md` | Планы, идеи, приоритеты |

### Obsidian wiki (подробная документация по модулям)

`docs/wiki/` — 21 файл, открывается в Obsidian (vault в корне проекта). Разделы:
- `01-Архитектура/` — стек, структура, БД, API
- `02-Модули/` — компании, мессенджер, рассылки, задачи, телефония, аудит
- `03-Инфраструктура/` — Docker, nginx, деплой, Celery
- `04-Статус/` — прод, staging, известные проблемы
- `05-Журнал/` — changelog, архитектурные решения

### Существующая документация (справочная)

| Файл | Назначение |
|------|-----------|
| `docs/deploy/DEPLOY_WORKFLOW.md` | Инструкции деплоя staging/прод |
| `docs/deploy/DEPLOY_INSTRUCTIONS.md` | Применение изменений на серверах |
| `docs/deploy/PROD_FILES_PATHS.md` | Пути файлов на проде |
| `docs/checklists/TESTING_CHECKLIST.md` | Чеклист тестирования |
| `docs/PMI_livechat.md` | Отчёт тестирования live-chat (67 тестов, 96% pass) |
| `docs/search/INTELLIGENT_SEARCH_SPEC.md` | Спецификация поиска |
| `docs/search/SEARCH_BEST_PRACTICES.md` | Best practices PostgreSQL FTS |
| `docs/summaries/FIXES_SUMMARY.md` | Сводка фиксов (Android, PhoneBridge) |
| `docs/summaries/MODERN_FEATURES.md` | Фичи Modern-режима карточки компании |
| `docs/ui/ICONS.md` | Набор иконок (Heroicons Outline) |
| `docs/ui/Z_INDEX.md` | Шкала z-index |

### Внутренняя память Claude Code

Файлы в `~/.claude/projects/.../memory/` — внутренний механизм Claude Code для сохранения контекста между сессиями. Не часть проекта, не коммитятся. Содержат: правила поведения (русский язык, автообновление docs), информацию о серверах, статус проекта. Обновляются автоматически.
