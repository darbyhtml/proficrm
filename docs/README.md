# Документация проекта CRM

## Поиск компаний

- **[INTELLIGENT_SEARCH_SPEC.md](INTELLIGENT_SEARCH_SPEC.md)** — требования к интеллектуальному поиску и сравнение внешних движков (Meilisearch, Typesense, Elasticsearch). Сейчас используется только PostgreSQL; документ полезен как справочный.
- **[SEARCH_ENGINE_ROADMAP.md](SEARCH_ENGINE_ROADMAP.md)** — исторический план внедрения внешнего движка. Актуальный backend поиска — PostgreSQL FTS.

В коде: `backend/companies/SEARCH_BEST_PRACTICES.md` — привязка практик к текущей реализации (PostgreSQL FTS + pg_trgm, EXACT-first, команды обслуживания индекса).
