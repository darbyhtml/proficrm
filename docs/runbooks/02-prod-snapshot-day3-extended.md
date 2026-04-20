---
tags: [runbook, прод, аудит, day3-extended, итог]
created: 2026-04-20
author: onboarding-audit
mode: read-only
---

# Snapshot прод — Day 3 Extended (итоговый)

Четвёртая итерация аудита. Закрывает 5 ранее открытых направлений: security deep-dive, frontend bundles, Android, slow queries, nginx access log. Это **финальный отчёт** по ознакомлению с проектом.

## TL;DR — 8 новых находок

| # | Находка | Severity | Решение |
|---|---------|----------|---------|
| 1 | **Security A-класс** — 12 заголовков, TLSv1.3, CSP nonce, HSTS 1 год, cookies secure, JWT с blacklist | ✅ nothing to fix | нет |
| 2 | **Android — middle-stage готов** (355 файлов Kotlin, versionName 0.5, Firebase Messaging, полная документация, pre-prod чеклист) | 🟢 1-2 мес полировки | отдельный трек |
| 3 | **3 стажёра-аккаунта на проде** (`test_krd`, `stagtmn`, `stagtmn2`) — используются стажёрами, оставить | 🟢 | документировать |
| 4 | **90% трафика = long polling** (`/mail/progress/poll`, `/notifications/poll`) → 1.5 млн запросов/сутки | 🟡 | Релиз 2: конвертация на SSE |
| 5 | **502 ошибки 0.18%** (~900/день) — OOM web-контейнера | 🟡 | Релиз 0 (memory limit → 1.5GB) |
| 6 | **pg_stat_statements не установлен** — нет профилирования slow queries | 🟡 | Релиз 0 или 1 (1 строка в postgresql.conf) |
| 7 | **Redis fragmentation 0.56** — памяти мало / swap pressure | 🟡 | Релиз 0 (`MEMORY PURGE` или рестарт) |
| 8 | **Typesense volume orphan (164 MB)** — подтверждено удалять | 🟢 | Релиз 1 cleanup |

## 1. Security — A-класс

Прошёл 12-point проверку. Результаты:

**HTTP response headers**:
- `Strict-Transport-Security`: max-age=31536000 (1 год) + includeSubDomains ✅
- `X-Frame-Options`: DENY ✅
- `X-Content-Type-Options`: nosniff ✅
- `Referrer-Policy`: strict-origin-when-cross-origin ✅
- `Permissions-Policy`: запрет geolocation/microphone/camera/payment/usb ✅
- `Cross-Origin-Opener-Policy`: same-origin ✅
- `Content-Security-Policy`: nonce-based (strict) ✅

**Django settings**:
- `DEBUG=False` ✅
- `SECURE_SSL_REDIRECT=True` ✅
- `SESSION_COOKIE_SECURE=True` ✅
- `CSRF_COOKIE_SECURE=True` ✅
- `SECURE_HSTS_SECONDS=31536000` ✅

**TLS**:
- Клиент-серверный handshake: TLSv1.3 + AES-256-GCM-SHA384 ✅
- TLSv1/1.1 в `ssl_protocols` (remove в Релизе 0) — минор

**CORS** (widget API):
- Unknown origins → `vary: origin` без `Access-Control-Allow-Origin` = deny ✅
- Правильный whitelist через Inbox.allowed_domains + `CORS_ALLOWED_ORIGINS`

**JWT (SIMPLE_JWT)**:
- ACCESS_TOKEN_LIFETIME: 1h ✅
- REFRESH_TOKEN_LIFETIME: 7 days ✅
- ALGORITHM: HS256 (приемлемо для одного приложения; RS256 мог бы быть лучше)
- BLACKLIST_AFTER_ROTATION: True ✅ (refresh нельзя повторно использовать после rotation)

**Password policy** (4 валидатора):
- UserAttributeSimilarityValidator
- MinimumLengthValidator
- CommonPasswordValidator
- NumericPasswordValidator

Стандартный Django-набор. **Без complexity-валидатора** (верхний/нижний регистр, цифры, спецсимволы). Для CRM с 50 внутренними пользователями — приемлемо.

**Rate limiting**: `accounts.middleware.RateLimitMiddleware` в стеке ✅. На уровне приложения закрыто.

**Незначительные слабости** (не критичны):
- CSP `style-src 'unsafe-inline'` — для legacy-шаблонов. Убрать в Релизе 2 при редизайне.
- **Tailwind через CDN** (`https://cdn.tailwindcss.com`) — CDN-зависимость. Есть локальный source (`frontend/src/main.css` + `tailwind.config.js`), но не интегрирован в build. Переделать в Релизе 2.
- `HS256` JWT — симметричный. На masштабе одного приложения приемлемо.

**Вердикт**: security на уровне **enterprise-ready SaaS**. Единственное — закрыть Chatwoot postgres/rails порты в Релизе 0.

## 2. Android — middle-stage зрелый проект

### Структура
- **`/android/CRMProfiDialer/`** — Gradle-проект
- **355 файлов** Kotlin/Java/XML
- **78 MB** кода
- `namespace=ru.groupprofi.crmprofi.dialer`
- `applicationId=ru.groupprofi.crmprofi.dialer`
- `compileSdk=35, minSdk=23, targetSdk=35`
- `versionCode=5, versionName="0.5"` — 5-я итерация
- **Kotlin + KSP** (modern)
- **Firebase Messaging** (push)
- **Keystore** для signing
- **BASE_URL** к проду: `https://crm.groupprofi.ru`

### Архитектура (из ARCHITECTURE.md)
- **Single-Activity Android клиент**
- Получает команды на звонки из CRM через HTTPS long-poll
- Инициирует звонки через системную звонилку
- Отслеживает результат по CallLog
- Отправляет статусы и телеметрию обратно в CRM
- Слои: UI / Domain / Data

### Документация (11 файлов)
```
docs/
├── API_INTEGRATION.md     — все endpoints с CRM
├── ARCHITECTURE.md        — слои, компоненты, сервисы
├── CODEMAP.md             — где какой класс
├── CONFIGURATION.md       — флаги, BASE_URL, режимы
├── FEATURES.md            — пользовательские экраны
├── FLOWS.md               — сквозные потоки
├── NEXT_STEPS.md          — чеклист дальнейших улучшений
├── README.md              — навигация
├── changelogs/            — 6 тематических changelog'ов
├── guides/DIAGNOSTICS_GUIDE.md  — диагностическая панель
├── plans/PRE_PROD_CHECKLIST.md  — pre-prod чеклист
├── plans/TORTURE_TEST_PLAN.md   — 30+ сценариев torture-тестирования
└── summaries/             — UI_UX_REVOLUTION, RELIABILITY_POLISH, FINAL_IMPROVEMENTS
```

### Что осталось сделать (NEXT_STEPS.md)

1. Android Vitals проверка через Play Console + baseline profiles
2. Унификация логирования: перевести `TokenManager`, `CrashLogStore`, `LogSender`, `QueueManager`, `CallListenerService` на `AppLogger`
3. Unit-тесты: `ApiClient`, `QueueManager`, `TokenManager`, `CrashLogStore`
4. Instrumented тесты: `CallListenerService`
5. Jetpack Compose (постепенно, начать с Onboarding / Diagnostics)
6. **Firebase Performance + Crashlytics** подключить
7. Retry с exp backoff для `flushTelemetry()` и отправки логов

### Оценка
**1-2 месяца полировки** до production-ready APK. Не «разработать», а «довести». Команда прошлого разработчика сделала **70-80% работы** с качественной документацией.

Параллельно Релизу 2 — идёт Android-трек. На выходе: APK через Firebase App Distribution (внутренняя раздача, не Play Store).

## 3. Frontend — минимальный, оптимизация не нужна

Весь JS проекта:
| Файл | Размер |
|------|-------:|
| `ui/company_create.js` | 32 KB |
| `ui/purify.min.js` | 24 KB (DOMPurify) |
| `ui/custom-datetime-picker.js` | 12 KB |
| `messenger/widget.js` | ~ |
| `messenger/operator-panel.js` | ~ |
| `messenger/widget-loader.js` | ~ |
| `messenger/favicon-badge.js` | ~ |
| `messenger/sw-push.js` | ~ (service worker) |

**Итого**: ~200-300 KB JS на весь проект. Оптимизация бандлов **не нужна**.

Реальная проблема — **нет единого build процесса**. Каждый JS файл самостоятельный. При Релизе 2 добавим Vite (если решим по ADR «Frontend modernization»).

**CSS**: `backend/static/ui/css/main.css` + Tailwind через CDN (runtime). Локальная сборка через `frontend/src/main.css` + `tailwind.config.js` возможна, но не подключена.

## 4. Polling vs SSE — архитектурный рычаг

Из nginx access-log (последние 10 000 запросов):
- `/mail/progress/poll/`: **5024** (50%)
- `/notifications/poll/`: **3857** (39%)
- `/api/dashboard/poll/`: 134 (1.3%)
- Остальное: ~1000 (10%)

**90% трафика = polling**. Экстраполяция: 1.5 млн запросов/сутки при 50 пользователях.

Каждый poll = SQL query + Redis hit + CPU. После перехода на SSE (Server-Sent Events) — **10× снижение нагрузки** + мгновенные уведомления.

В main уже есть SSE для messenger. Шаблон копируется на:
- `/notifications/poll/` → `/notifications/sse/`
- `/mail/progress/poll/` → `/mail/progress/sse/`

Это **быстрая оптимизация** в Релизе 2. Оценка: 3-5 дней работы.

## 5. 502 ошибки — 0.18% (~900/день)

Из 10 000 запросов — 18 вернули 502 Bad Gateway. Экстраполяция: ~900 штук/день.

**Причина**: OOM web-контейнера (768 MB limit, 421 MB use в нормал, пики выше). Gunicorn killed → gateway 502 за время restart.

**Фикс**: Релиз 0 — поднять лимит до 1.5 GB. Ожидается: 502 → 0.

Топ 502-endpoints:
- `/api/conversations/unread-count/` (7)
- `/sw-push.js` (3)
- `/` (3)
- `/favicon.ico` (2)
- `/mail/campaigns/` (2 × 500)
- `/notifications/poll/` (1)
- `/mail/progress/poll/?since=...` (1)

## 6. pg_stat_statements не установлен

Расширение PostgreSQL для профилирования slow queries — **отсутствует на проде**.

**Без него** нет представления о:
- Какие запросы медленные
- Сколько раз вызываются
- Какой total time по каждому endpoint

**Фикс**:
1. В `docker-compose.prod.yml`:
   ```yaml
   db:
     command:
       - "postgres"
       - "-c"
       - "shared_preload_libraries=pg_stat_statements"
       - "-c"
       - "pg_stat_statements.max=10000"
       - "-c"
       - "pg_stat_statements.track=all"
   ```
2. Рестарт БД
3. `CREATE EXTENSION pg_stat_statements;`
4. Неделю собирать → получить картину

## 7. Redis fragmentation 0.56

`mem_fragmentation_ratio: 0.56` при `used_memory: 7.72 MB` — серьёзный перекос. Redis виртуально использует больше, чем резидентно в RAM. Скорее всего — результат `swap 93%` сервера.

**Фикс**: в Релиз 0 — `redis-cli MEMORY PURGE` или полный рестарт Redis (5 секунд downtime).

## 8. Typesense orphan volume (164 MB)

Прошлый разработчик экспериментировал с Typesense (поисковая movement), но отказался в пользу PostgreSQL FTS. Volume остался.

В коде — одно упоминание в `companies/tests_search.py`: список backend-вариантов.

**Действие**: после Релиза 1 — `docker volume rm proficrm_typesense_data proficrm_typesense_data_staging`. Освободит ~200 MB.

## Итоговая оценка проекта

| Аспект | Оценка | Комментарий |
|--------|--------|-------------|
| Код | 9/10 | 1143 теста, 98.25% pass, ruff, gitleaks, типизация, docstrings |
| Архитектура | 9/10 | правильное разделение apps, FTS, Celery, Redis, Docker |
| Security | 9/10 | A-класс hardening, 12 headers, JWT blacklist, rate limiting |
| Деплой | 6/10 | есть скрипты, но нет автодеплоя, 333-коммитный backlog |
| Observability | 3/10 | нет Sentry, нет pg_stat_statements, слабый healthcheck Celery |
| Документация | 8/10 | Obsidian wiki + runbooks + ADR + Android docs |
| Bus factor | 2/10 | один разработчик, нет backup-команды |
| **Integrated** | **7.5/10** | сильный middle-stage проект с операционными gap'ами |

## Roadmap — обновлённый

### Релиз 0 — ночной hotfix (1 вечер)
- `docs/runbooks/10-release-0-night-hotfix.md`
- Security + память + pg_stat_statements + Redis purge
- Downtime: 5-10 минут

### Релиз 1 — main→prod (1-2 недели подготовки)
- `docs/runbooks/20-release-1-main-to-prod.md`
- v3/b карточка компании, messenger включён (пустой)
- Downtime: 5-10 минут
- Риск: LOW (dress rehearsal пройден)

### Релиз 2 — продуктовый (2-3 месяца)
- Редизайн остальных страниц в той же палитре (#01948E / #FDAD3A)
- Переход с Chatwoot на внутренний messenger (менеджеры + виджеты на сайте)
- Polling → SSE конвертация (10× снижение нагрузки)
- Mailer полировка
- Очистка 343 orphan-контактов (`docs/runbooks/30-orphan-contacts-cleanup.md`)

### Android трек (1-2 месяца, параллельно Р2)
- NEXT_STEPS.md полировка
- Unit + instrumented тесты
- Firebase Performance + Crashlytics
- APK через Firebase App Distribution

### Итого до «wow»: 4-6 месяцев

По сравнению с Day 0-оценкой (9-14 месяцев) — **в 2 раза быстрее**, благодаря зрелости кода и документации.

## Аудитор

Senior onboarding 3-дневный аудит 2026-04-18…20, read-only.
Никаких изменений на проде не произведено.
Следующая активность: **Релиз 0 в 17:30 MSK** (подтверждение у заказчика).
