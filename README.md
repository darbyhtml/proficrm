# CRM (ProfiCRM)

![CI](https://github.com/darbyhtml/proficrm/actions/workflows/ci.yml/badge.svg?branch=main)
![Deploy Staging](https://github.com/darbyhtml/proficrm/actions/workflows/deploy-staging.yml/badge.svg?branch=main)

Внутренняя CRM для GroupProfi: учёт компаний/контактов, задачи, рассылки, live-chat мессенджер, Android-клиент для звонков.

## Подпроекты

- **Backend** — `backend/` (Django 6, DRF, Celery, PostgreSQL 16, Redis 7, channels/daphne)
- **Frontend** — `frontend/` (Tailwind source) + `backend/templates/` + `backend/static/ui/`
- **Android (CRMProfiDialer)** — `android/CRMProfiDialer/` (Kotlin, SDK 35, Firebase)

## Документация

- **[docs/README.md](docs/README.md)** — главная навигация
- **[docs/roadmap.md](docs/roadmap.md)** — релизы и приоритеты
- **[docs/runbooks/](docs/runbooks/)** — операционные runbook'и (snapshots, деплои, cleanup)
- **[docs/decisions.md](docs/decisions.md)** — ADR (лог архитектурных решений)
- **[docs/problems-solved.md](docs/problems-solved.md)** — база решённых проблем
- **[docs/wiki/](docs/wiki/)** — Obsidian-vault с детальной документацией модулей
- **[android/CRMProfiDialer/docs/README.md](android/CRMProfiDialer/docs/README.md)** — документация Android-приложения

## Развёртывание

- **Staging**: `crm-staging.groupprofi.ru` — авто-деплой при push в `main` через GitHub Actions (включает post-deploy smoke + auto-rollback).
- **Production**: `crm.groupprofi.ru` — только ручной деплой по `docs/runbooks/21-release-1-ready-to-execute.md`.

## Операции

- `make smoke-staging` — обязательная проверка staging после любого deploy ([CLAUDE.md](CLAUDE.md) §MANDATORY).
- `make restart-staging-web` / `make restart-staging-all` — безопасные рестарты с nginx flush + smoke ([docs/runbooks/staging-operations.md](docs/runbooks/staging-operations.md)).
