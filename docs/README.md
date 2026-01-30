# Документация проекта CRM

## Поиск компаний

- **[INTELLIGENT_SEARCH_SPEC.md](INTELLIGENT_SEARCH_SPEC.md)** — требования к интеллектуальному поиску, сравнение движков (Meilisearch, Typesense, Elasticsearch), рекомендации по полям и настройке.
- **[SEARCH_ENGINE_ROADMAP.md](SEARCH_ENGINE_ROADMAP.md)** — план внедрения и текущий статус: Typesense backend, команды переиндексации и стоп-слов, fallback на Postgres, порядок деплоя.

В коде: `backend/companies/SEARCH_BEST_PRACTICES.md` — привязка практик к реализации (Postgres и Typesense).
