# Инструкция для Claude Code

## При старте каждой сессии прочитать:

1. **CLAUDE.md** (этот файл)
2. **docs/current-sprint.md** — что сейчас в работе, где остановились, что следующее

Это обязательно. Не начинать работу, не прочитав оба файла.

Дополнительно, по ситуации:
- Задача связана с архитектурой или новым модулем → прочитать `docs/architecture.md`
- Задача похожа на ранее решённую проблему → проверить `docs/problems-solved.md`
- Нужно понять почему выбран конкретный подход → прочитать `docs/decisions.md`

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
| Моделей БД | 66 (10 Django-приложений) |
| API эндпоинтов | 50+ REST (DRF) + Widget API (публичный) |

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
├── templates/     — 89 HTML шаблонов
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
- Коммиты: `Fix(Module):`, `Feat(Module):`, `Harden(Module):`, `UI(Module):`
- Django 6.0.1, Python 3.13 — используем современные фичи
- DRF для API, django-filter для фильтрации
- Celery для всех фоновых задач
- Redis для кэша, брокера, channel layer, typing-индикаторов

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
