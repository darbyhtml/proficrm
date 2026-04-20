# Roadmap — CRM ПРОФИ (обновлено 2026-04-20)

## Статус выпусков

| Релиз | Статус | Дата | Содержание |
|-------|:------:|------|------------|
| **Релиз 0** | ✅ DONE | 2026-04-20 утром | Ночной hotfix: memory limits, shm_size, TLS 1.2/1.3 + HTTP/2, postfix loopback, policy events blocked (PG RULE), 10.3M старых события удалены |
| **Релиз 1** | 🟡 READY | 2026-04-20/21 ночью | main → prod (333 коммитов + 2 маленькие миграции + v3/b карточка + messenger включается пустым + celery healthcheck fix + правильный env-flag для policy logging) |
| **Релиз 2** | 🔵 PLAN | Май-июнь 2026 | Продуктовый: переезд с Chatwoot на внутренний messenger, редизайн, polling→SSE, рефакторинг god-views, очистки |
| **Android** | 🔵 PARALLEL | Май-июнь 2026 | Полировка по android/CRMProfiDialer/docs/NEXT_STEPS.md, APK через Firebase App Distribution |

Детальные runbook'и: `docs/runbooks/00-04` (аудит), `10-11` (Релиз 0), `20-21` (Релиз 1), `30` (orphan cleanup), `40` (Sentry + CICD).

---

## Релиз 1 (ночное окно, 21:00-22:00 MSK)

Runbook: `docs/runbooks/21-release-1-ready-to-execute.md`

Checklist:
- [ ] Установить ночное окно с заказчиком
- [ ] Настроить `STAGING_SSH_PRIVATE_KEY` в GitHub Secrets (для CICD авто-деплоя на staging)
- [ ] Опционально: зарегистрировать Sentry (`docs/runbooks/40-observability-and-cicd-setup.md`)
- [ ] Backup Netangels сегодняшний (автоматом)
- [ ] Применить main → prod по runbook 21
- [ ] Smoke + QA (открыть /v3/b/ карточку, убедиться что messenger-раздел работает с пустой базой)
- [ ] После Релиза 1: `DROP RULE block_policy_activity_events` (код теперь сам фильтрует)
- [ ] Через 1-2 дня: `VACUUM FULL audit_activityevent` ночью (освободит ~3 GB диска)

---

## Релиз 2 — продуктовый (2-3 месяца)

**Цель:** довести CRM до «wow» уровня, переехать с Chatwoot.

### 2.1 Редизайн (6-8 недель)

- [ ] v3/b classic replace — заменить `company_detail.html` (8781 LOC) на `company_detail_v3/b.html` (1812 LOC). −7000 LOC HTML долга.
- [ ] Применить Notion-палитру (`#01948E` / `#FDAD3A`) по всем страницам:
  - [ ] `/companies/` — список
  - [ ] `/tasks/` — список + карточка задачи
  - [ ] `/dashboard/` — главная
  - [ ] `/settings/*` — настройки
  - [ ] `/mail/*` — рассылки
  - [ ] `/reports/*` — отчёты
- [ ] Убрать Tailwind CDN → локальная сборка через Vite (`frontend/src/main.css` уже есть)
- [ ] CSP `unsafe-inline` для style-src → убрать после редизайна (nonce-based)

### 2.2 Messenger — переход с Chatwoot (4-6 недель)

- [x] Real-time доставка (SSE) — решено до меня, widget.js 3-кратная dedup
- [ ] Включить `MESSENGER_ENABLED=1` в Релизе 1 (технически) + продуктовое тестирование
- [ ] Интегрировать widget на сайт `groupprofi.ru` (один inbox)
- [ ] Импорт чата менеджеров: обучение, переход с Chatwoot по подразделениям
- [ ] Typing-индикаторы (оба направления)
- [ ] Browser push-уведомления
- [ ] Автоматизация: приветствие, эскалация, auto-assign по regions
- [ ] Оценка диалога (stars + NPS)
- [ ] Нагрузочное тестирование (10+ одновременных виджетов, SSE leak check)
- [ ] **Остановить Chatwoot** (контейнеры + volumes → `-1 GB RAM + 252 MB диска`)

### 2.3 Новые каналы в messenger (2-4 недели — зависит от API)

Архитектура omnichannel уже заложена (graphify: communities Email/TG/WA/VK).

- [ ] Telegram bot (BotFather, long-polling или webhook)
- [ ] VK сообщения сообществу
- [ ] Email-канал (входящие через IMAP или mailgun webhook)
- [ ] WhatsApp Business (через 360dialog / Twilio / meta API)

### 2.4 Производительность (1-2 недели, параллельно редизайну)

- [ ] Polling → SSE: `/notifications/poll/` (39% трафика), `/mail/progress/poll/` (50% трафика) → −90% фоновой нагрузки
- [ ] `PolicyDecisionLog` отдельная модель с TTL 24h (вместо выключенного `_log_decision`) — если нужен аудит policy
- [ ] Рефакторинг god-views в service layer:
  - [ ] `ui/views/company_detail.py` (2883 LOC → ~500 LOC)
  - [ ] `ui/views/tasks.py` (2215 LOC)
  - [ ] `ui/views/settings_core.py` (1581 LOC)
  - [ ] `ui/views/company_list.py` (1487 LOC)
  - [ ] `ui/views/dashboard.py` (1282 LOC)
- [ ] Исправить `Company.save()` — `self.responsible.branch` lazy FK даёт N+1 при bulk import (вынести в services с prefetch)
- [ ] Удалить 525 MB dead FTS indexes после подтверждения кода поиска (см. `03-day3-deep-dive.md`)

### 2.5 Очистка данных (1 неделя)

- [ ] 343 orphan contacts → по runbook `30-orphan-contacts-cleanup.md` (45 пустых безусловно, 298 с данными — CSV-согласование + удаление)
- [ ] Orphan media files (24 из 32 файлов не привязаны к БД) — auto cleanup task в Celery beat
- [ ] Typesense volumes (164 MB) удалить после подтверждения что не используется

### 2.6 Testing backlog (1-2 недели)

- [x] 1143 тестов pass rate 100% (как сегодня)
- [ ] Добавить unit-тесты для god-services после рефакторинга (цель 80%+ coverage)
- [ ] E2E Playwright для критичных flow: login, создать компанию, отправить сообщение в messenger, принять звонок в Android-app (через emulator)
- [ ] Нагрузочное тестирование messenger (k6 / locust) — 100+ concurrent widgets

---

## Android трек (параллельно Релиз 2, 1-2 месяца)

Детали: `/android/CRMProfiDialer/docs/NEXT_STEPS.md`.

- [ ] Android Vitals + baseline profiles (Play Console)
- [ ] Логирование: TokenManager / CrashLogStore / LogSender / QueueManager / CallListenerService → AppLogger
- [ ] Unit + instrumented тесты
- [ ] Firebase Performance + Crashlytics
- [ ] Retry с exp backoff (flushTelemetry, отправка логов)
- [ ] Jetpack Compose (поэтапно, начать с Onboarding / Diagnostics)
- [ ] Релиз APK через Firebase App Distribution (внутренняя раздача)

---

## Пост-Релиз 2 (через 6 месяцев)

### Обсервабилити (уже частично готово после Sentry + UptimeRobot)

- [ ] Grafana + Prometheus (если нужен дашборд метрик) — когда Sentry-квоты станет мало
- [ ] Log aggregation (Loki / Better Stack)
- [ ] APM traces с `SENTRY_TRACES_SAMPLE_RATE=0.1`

### SaaS-готовность (если заказчик будет двигаться к продаже)

Это тема не ближайших месяцев, но если возникнет — см. оценку в `02-prod-snapshot-day3-extended.md` («3.5/10 для SaaS, 7/10 for single-tenant»):
- [ ] Multi-tenancy: либо per-tenant schema, либо row-level с tenant_id FK на всех моделях
- [ ] Public API с rate limiting per-tenant и API keys
- [ ] Billing integration
- [ ] Self-service onboarding
- [ ] Компиляция: docs/DECISIONS-SaaS.md и docs/runbooks/50-multitenancy-migration.md — **когда решите**

---

## Идеи (не приоритизированы, для копилки)

- AI-ассистент в messenger (автоответы, классификация intent)
- Knowledge base для виджета (FAQ до оператора)
- Отчёты по email-кампаниям (open rate, click rate — нужен tracking pixel)
- Webhook-уведомления для внешних интеграций
- API rate limiting dashboard для мониторинга
- Интеграция с AmoCRM двусторонняя (сейчас только импорт)

---

## Приоритеты (по убыванию ROI)

1. **Релиз 1 на прод** — 5-10 мин downtime, закрывает 4-недельное отставание.
2. **Активировать Sentry + UptimeRobot + CICD auto-deploy staging** — 15 минут ручной настройки, огромный долгосрочный observability win.
3. **VACUUM FULL audit_activityevent** — ночью, 5-15 мин блокировки только одной таблицы, освобождает 3 GB.
4. **Polling → SSE** — 3-5 дней разработки, −90% фоновой нагрузки.
5. **v3/b classic replace** — 2-3 дня, пользователи видят новый UI во всей CRM.
6. **Messenger переезд с Chatwoot** — 4-6 недель, главный продуктовый win.
7. **Android release** — 1-2 месяца параллельно, доделать NEXT_STEPS.md.

**Итого до «wow»: 4-6 месяцев**, не 9-14 как казалось на Day 0.
