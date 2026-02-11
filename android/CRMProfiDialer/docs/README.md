# CRMProfiDialer — документация Android‑приложения

Этот раздел описывает Android‑приложение CRMProfiDialer: архитектуру, интеграцию с CRM, основные экраны, конфигурацию, диагностику и историю изменений.

Приложение — это single‑activity Android‑клиент, который:
- получает команды на звонки из CRM через HTTPS (long‑poll),
- инициирует звонки через системную звонилку,
- отслеживает результат по CallLog,
- отправляет статусы и телеметрию обратно в CRM.

---

## С чего начать

- **Обзор архитектуры**: `ARCHITECTURE.md` — слои (UI/Domain/Data), ключевые компоненты и фоновые сервисы.
- **Интеграция с CRM**: `API_INTEGRATION.md` — все эндпойнты, потоки данных и типовые сценарии.
- **Функциональность и экраны**: `FEATURES.md` — что видит пользователь и как это связано с CRM.
- **Конфигурация и режимы**: `CONFIGURATION.md` — режимы Telemetry, флаги, BASE_URL и особенности сборки.
- **Карта кода**: `CODEMAP.md` — где какой файл/класс и с чего начинать чтение.
- **Сквозные потоки**: `FLOWS.md` — пошагово «что куда и как» по основным сценариям.
- **Диагностика и поддержка**: `guides/DIAGNOSTICS_GUIDE.md` — как снимать диагностику и читать отчёт.
- **Проверка перед продом**: `plans/PRE_PROD_CHECKLIST.md` — чеклист, что всё реально работает.

---

## Архитектура и интеграция

| Документ | Описание |
|----------|----------|
| [ARCHITECTURE.md](ARCHITECTURE.md) | Архитектура приложения, слои, ключевые сервисы и потоки данных внутри клиента |
| [API_INTEGRATION.md](API_INTEGRATION.md) | Интеграция с CRM: URL, эндпойнты, запросы/ответы, сценарии (auth, pull, update, heartbeat, telemetry) |
| [FEATURES.md](FEATURES.md) | Описание экранов и пользовательских сценариев (логин, главная, телефон, история, настройки, диагностика) |
| [CONFIGURATION.md](CONFIGURATION.md) | Настройки, режимы работы, feature‑флаги, рекомендации по конфигурированию под прод |
| [CODEMAP.md](CODEMAP.md) | Карта проекта: что где в коде и «куда смотреть» по задачам |
| [FLOWS.md](FLOWS.md) | Сквозные потоки логики: команда → звонок → результат → CRM, оффлайн/очередь, диагностика |

---

## Руководства, чеклисты и планы

| Документ | Описание |
|----------|----------|
| [guides/DIAGNOSTICS_GUIDE.md](guides/DIAGNOSTICS_GUIDE.md) | Руководство по диагностической панели и отчётам: как открыть, что означают поля, как делиться логами |
| [plans/PRE_PROD_CHECKLIST.md](plans/PRE_PROD_CHECKLIST.md) | Pre‑prod чеклист перед выкатом в прод (ручной прогон по ключевым сценариям) |
| [plans/TORTURE_TEST_PLAN.md](plans/TORTURE_TEST_PLAN.md) | План torture‑тестирования (30+ сценариев для LOCAL_ONLY/FULL режимов) |

---

## История изменений (changelogs)

Для истории развития приложения используются несколько файлов. Основной — единый changelog, остальные — тематические/поштучные отчёты, которые можно рассматривать как архив.

| Документ | Описание |
|----------|----------|
| [changelogs/CHANGELOG.md](changelogs/CHANGELOG.md) | **Основной журнал изменений** (основные архитектурные, производительные и security‑изменения) |
| [changelogs/CHANGELOG_IMPROVEMENTS.md](changelogs/CHANGELOG_IMPROVEMENTS.md) | Улучшения надёжности и UI (background‑работа, токены, NO_ACTION и др.) |
| [changelogs/FINAL_CHANGELOG.md](changelogs/FINAL_CHANGELOG.md) | Финальный changelog torture‑тестов и edge‑кейсов (готовность к продакшену) |
| [changelogs/LOCAL_FIRST_CHANGELOG.md](changelogs/LOCAL_FIRST_CHANGELOG.md) | Изменения, связанные с local‑first подходом |
| [changelogs/LONG_POLL_CHANGELOG.md](changelogs/LONG_POLL_CHANGELOG.md) | Эволюция long‑poll, burst/backoff и метрик доставки команд |
| [changelogs/TORTURE_TEST_CHANGELOG.md](changelogs/TORTURE_TEST_CHANGELOG.md) | История изменений вокруг torture‑тестов и диагностики |

Если нужно понять «что сделали в целом» — смотрите `CHANGELOG.md`. Остальные файлы пригодятся для расследования конкретной темы (long‑poll, local‑first, torture‑тесты).

---

## Отчёты и сводки по доработкам

Эти документы удобны для онбординга и понимания эволюции продукта: какие проблемы решались и какими сериями задач.

| Документ | Описание |
|----------|----------|
| [summaries/FINAL_IMPROVEMENTS_SUMMARY.md](summaries/FINAL_IMPROVEMENTS_SUMMARY.md) | Итоговая сводка улучшений перед продом (edge‑кейсы, диагностика, torture‑tests) |
| [summaries/RELIABILITY_POLISH.md](summaries/RELIABILITY_POLISH.md) | Улучшения надёжности доставки команд, backoff, OEM‑специфика и подготовка к FCM |
| [summaries/UI_UX_REVOLUTION_REPORT.md](summaries/UI_UX_REVOLUTION_REPORT.md) | Отчёт по ревизии UI/UX: навигация, доступность, производительность UI |

---

## Дорожная карта / следующие шаги

| Документ | Описание |
|----------|----------|
| [NEXT_STEPS.md](NEXT_STEPS.md) | Приоритизированный список следующих шагов по развитию приложения (тесты, логирование, Compose и т.п.) |

Рекомендуемый порядок чтения для нового разработчика:
1. `ARCHITECTURE.md`
2. `API_INTEGRATION.md`
3. `FEATURES.md`
4. `CONFIGURATION.md`
5. `guides/DIAGNOSTICS_GUIDE.md` и `plans/PRE_PROD_CHECKLIST.md`
6. По необходимости — `summaries/*.md` и `changelogs/*.md`
