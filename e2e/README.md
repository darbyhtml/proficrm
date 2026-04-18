# E2E тесты CRM ПРОФИ (F10)

Playwright smoke-тесты для критических UI flow на **staging**.

## Правила

- **Прод НЕ тестируем** — staging-only (`crm-staging.groupprofi.ru`).
- Запуск вручную, не в CI (пока) — требует credentials.
- 1 worker, последовательно — Django session не параллелится.

## Установка

```bash
cd e2e
npm run install:deps   # npm install + playwright install chromium --with-deps
```

## Запуск

```bash
# Credentials из env (обязательно для большинства тестов)
export E2E_USERNAME=sdm
export E2E_PASSWORD='...'   # см. reference_staging_sdm_credentials в памяти

# Против staging (по умолчанию)
npm run test:staging

# UI-режим (headed)
npm run test:headed

# HTML-отчёт после прогона
npm run report
```

## Что покрыто

| Flow | Файл |
|------|------|
| Login → dashboard | smoke.spec.ts |
| Companies list 200 | smoke.spec.ts |
| Tasks list 200 | smoke.spec.ts |
| Analytics v2 (ролевой роутер) | smoke.spec.ts |
| Settings → вкладка «Отсутствие» (F5) | smoke.spec.ts |
| Help: FAQ рендерится (F8 R2) | smoke.spec.ts |
| Admin → Mail setup (F6 R2) | smoke.spec.ts |
| /health/ без auth | smoke.spec.ts |

## В планах (R2)

- Create company по ИНН + auto-fill
- Create task → mark done → проверка счётчика на дашборде
- Live-chat: виджет на демо-странице → оператор видит диалог
- Off-hours form (F5): отправка формы вне рабочих часов → WAITING_OFFLINE
- `.gitignore` для `node_modules`, `playwright-report`, `test-results`

## Почему не в CI

Staging доступен только из доверенных IP (ssh sdm). В GitHub Actions
потребуется tunnel или отдельное E2E-окружение. Пока — только manual
пускают разработчики перед мержом большого релиза.
