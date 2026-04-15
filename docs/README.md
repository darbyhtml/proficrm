# Документация проекта CRM

Единая точка входа по всей документации. Обновляется вручную при добавлении/удалении файлов.

---

## Основное (читается Claude Code при старте)

- **[../CLAUDE.md](../CLAUDE.md)** — главный файл с правилами проекта, стеком, конвенциями.
- **[current-sprint.md](current-sprint.md)** — что сейчас в работе, стоп-точки, история спринтов.
- **[architecture.md](architecture.md)** — полная архитектура (стек, модули, БД, паттерны, Celery, Docker).
- **[decisions.md](decisions.md)** — лог архитектурных решений (ADR).
- **[problems-solved.md](problems-solved.md)** — решённые нетривиальные проблемы с причинами и файлами.
- **[roadmap.md](roadmap.md)** — планы, приоритеты, идеи.

---

## Obsidian wiki

**[wiki/](wiki/)** — 21 файл, 5 разделов (Архитектура / Модули / Инфраструктура / Статус / Журнал). Открывать через Obsidian, vault в корне проекта (`.obsidian/` в `.gitignore`).

---

## Деплой и окружения

- **[deploy/DEPLOY_INSTRUCTIONS.md](deploy/DEPLOY_INSTRUCTIONS.md)** — применение изменений на серверах.
- **[deploy/DEPLOY_WORKFLOW.md](deploy/DEPLOY_WORKFLOW.md)** — порядок работы staging → production.
- **[deploy/DEPLOY_SEARCH.md](deploy/DEPLOY_SEARCH.md)** — деплой поиска.
- **[deploy/PROD_FILES_PATHS.md](deploy/PROD_FILES_PATHS.md)** — пути файлов на проде.

---

## Поиск компаний

- **[search/INTELLIGENT_SEARCH_SPEC.md](search/INTELLIGENT_SEARCH_SPEC.md)** — спецификация поиска (текущий backend — PostgreSQL FTS + pg_trgm).
- **[search/SEARCH_BEST_PRACTICES.md](search/SEARCH_BEST_PRACTICES.md)** — практики и команды обслуживания индекса.

---

## UI и вёрстка

- **[ui/ICONS.md](ui/ICONS.md)** — Heroicons Outline, правила подбора.
- **[ui/Z_INDEX.md](ui/Z_INDEX.md)** — шкала z-index.

---

## Чеклисты и тестирование

- **[checklists/TESTING_CHECKLIST.md](checklists/TESTING_CHECKLIST.md)** — чеклист тестирования.
- **[PMI_livechat.md](PMI_livechat.md)** — отчёт комплексного тестирования live-chat (67 тестов, 96% pass).
- **Тесты в Docker:** `docker compose exec web python manage.py test` или `scripts/run_tests_docker.sh [модуль]`.

---

## Live-chat: планы и спецификации

- **[superpowers/specs/](superpowers/specs/)** — design-спецификации (live-chat UX Completion).
- **[superpowers/plans/](superpowers/plans/)** — планы реализации (Plan 1-4 live-chat, закрыты 2026-04-13).

---

## Расследования

- **[investigations/](investigations/)** — детальные отчёты по расследованию багов (формат `INV-YYYY-MM-DD-NNN-title.md`).

---

## Сводки

- **[summaries/FIXES_SUMMARY.md](summaries/FIXES_SUMMARY.md)** — резюме изменений (rate limiting, telemetry, call log).
- **[summaries/MODERN_FEATURES.md](summaries/MODERN_FEATURES.md)** — современные возможности карточки компании.

---

## Подпроекты

- **Android (CRMProfiDialer):** [../android/CRMProfiDialer/docs/README.md](../android/CRMProfiDialer/docs/README.md).

---

## Локальная база знаний (НЕ в git)

`knowledge-base/` в корне проекта — аудиты, бенчмарки, справочники, не попадают в репозиторий (в `.gitignore`). Структура: `raw/`, `research/`, `bench/`, `synthesis/`, `ref/`. Главный документ — `knowledge-base/synthesis/state-of-project.md`.
