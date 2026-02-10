# Документация проекта CRM

Единая точка входа по всей документации (backend, deploy, поиск, UI, Android).

---

## Деплой и окружения

- **[DEPLOY_INSTRUCTIONS.md](deploy/DEPLOY_INSTRUCTIONS.md)** — инструкция по применению изменений на сервере (прод и стагинг).
- **[DEPLOY_WORKFLOW.md](deploy/DEPLOY_WORKFLOW.md)** — порядок работы: две папки, Staging → Production.
- **[DEPLOY_SEARCH.md](deploy/DEPLOY_SEARCH.md)** — деплой и настройка поиска.

---

## UI и вёрстка

- **[ICONS.md](ui/ICONS.md)** — какие наборы иконок используем (Heroicons Outline), где применены, как подбирать новые.
- **[Z_INDEX.md](ui/Z_INDEX.md)** — шкала z-index для модалок, панелей, хедера.

---

## Поиск компаний

- **[INTELLIGENT_SEARCH_SPEC.md](search/INTELLIGENT_SEARCH_SPEC.md)** — требования к интеллектуальному поиску и сравнение внешних движков (Meilisearch, Typesense, Elasticsearch). Сейчас используется PostgreSQL.
- **[SEARCH_ENGINE_ROADMAP.md](search/SEARCH_ENGINE_ROADMAP.md)** — исторический план внедрения внешнего движка. Актуальный backend — PostgreSQL FTS.
- **[SEARCH_BEST_PRACTICES.md](search/SEARCH_BEST_PRACTICES.md)** — привязка практик к текущей реализации (PostgreSQL FTS + pg_trgm, EXACT-first, команды обслуживания индекса).

---

## Backend и аудит

- **[INN_AUDIT.md](backend/INN_AUDIT.md)** — аудит ИНН, нормализация, exact-поиск.

---

## Чеклисты и тестирование

- **[TESTING_CHECKLIST.md](checklists/TESTING_CHECKLIST.md)** — чеклист тестирования с ожидаемыми логами.
- **Тесты в Docker:** из корня проекта. Контейнер уже запущен: `docker compose exec web python manage.py test` (или `... test ui.tests.test_view_as`). Контейнер не запущен: `./scripts/run_tests_docker.sh [модуль]` или на Windows `scripts\run_tests_docker.bat [модуль]`.

---

## Сводки и отчёты

- **[FIXES_SUMMARY.md](summaries/FIXES_SUMMARY.md)** — резюме изменений (rate limiting, telemetry, call log).
- **[MODERN_FEATURES.md](summaries/MODERN_FEATURES.md)** — современные возможности.
- **[PERFORMANCE_FIXES_SUMMARY.md](summaries/PERFORMANCE_FIXES_SUMMARY.md)** — сводка по производительности.
- **[PERFORMANCE_PATCH_VERIFICATION.md](summaries/PERFORMANCE_PATCH_VERIFICATION.md)** — проверка патчей производительности.

---

## Прочее

- **[PR_DESCRIPTION.md](PR_DESCRIPTION.md)** — шаблон/описание для pull request.

---

## Документация по подпроектам

- **Android (CRMProfiDialer):** [android/CRMProfiDialer/docs/README.md](../android/CRMProfiDialer/docs/README.md) — changelogs, гайды, планы, torture-тесты.
