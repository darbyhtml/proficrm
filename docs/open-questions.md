# Open Questions — открытые вопросы для пользователя

_Правило (из 00_MASTER_PLAN.md §2): Claude Code не упирается в неопределённость молча —
заносит сюда запись с явным допущением `ASSUMPTION:` и продолжает. Пользователь
отвечает в Q-ответ, Claude следующей сессии убирает item._

---

## 🟡 Active — W0.5a pre-flight (2026-04-21)

### Q13 [2026-04-21] amoCRM test phones — user verification needed

**Контекст**: Public-readiness scan (Phase 5) выявил 12 возможно-real phone numbers
в `backend/amocrm/tests.py` — phones похожи на реальные Moscow/Tyumen/СПб area
codes, а не obvious dummy patterns `+79991234567`.

**Что попытано в cleanup session** (Phase B):
1. Автоматическая замена всех 12 phones через regex `\+7\d{10}` → `+70000000NNN` dummies.
2. Run `amocrm.tests` → 1 тест failed (`test_field_309609_extracts_skynet_phones`).
3. Root cause: raw input strings в тестах содержат форматы `"8 (919) 305-55-10"`,
   `"+7 919 337-77-55"` которые не matched regex → `normalize_phone()` produces
   оригинальные `+79193055510` / `+79193377755`, не matching обновлённые dummy
   assertions. Proper replacement требует format-aware substitution всех возможных
   форматов (8 NNN, +7 NNN, brackets, spaces, dashes).

**Revert applied**: phone changes в `backend/amocrm/tests.py` reverted до original
state. Файл staging контейнера restored через `docker compose up -d --force-recreate web`.

**Список suspicious phones** (unique, из Phase 5 scan):
- `+74956322197`, `+74951234567` (Moscow — 495)
- `+73453522095` (Tyumen — 345)
- `+73452540415` (Tyumen — 345)
- `+73847333392` (Kemerovo — 384)
- `+78165654958` (Vyborg / SPb region — 816)
- `+79123844985` (MTS — 912)
- `+79191111111`, `+79192222222` (mobile — могут быть dummy)
- `+79193055510`, `+79193377755` (mobile — могут быть real)
- `+79829481568` (mobile — может быть real)

Plus 8NNNNNNNNNN format copies of same numbers в raw test data strings.

**User action required**:

1. **Verify from memory / amoCRM dump**: какие из этих 12 phone values были
   copied from real amoCRM data (vs. deliberately crafted test patterns)?
2. **Decision options**:
   - **А. Leave as-is** — accept эти phones могут быть real. Context: они
     only в tests.py которого public repo читатель может увидеть. Marginal
     PII exposure.
   - **B. Fix properly** — separate session: format-aware replacement (regex
     для `8\s?\(?\d{3}\)?\s?\d{3}[-\s]?\d{2}[-\s]?\d{2}` etc) + update both
     input strings и assertions + full `amocrm.tests` pass. Est 1-2 hours.
   - **C. Delete amoCRM app entirely** — amoCRM marked for removal в W1
     refactor per user memory. Если уже не used — delete директорию
     (backend/amocrm/), убрать из INSTALLED_APPS. Removes 50 tests + все
     phone references в один commit.

**ASSUMPTION** (pending user decision): option A (accept) — Phase B revert
в cleanup session + proceed к Phase D (filter-repo для 2 docs files).
amoCRM phones остаются в репо even after public toggle.

---

### Q12 [2026-04-21] Staging auto-deploy broken — root cause?

**Факт.** `deploy-staging.yml` workflow **не триггерится** на последние 7 коммитов
main (с 2026-04-21 09:51 MSK по 10:39 MSK). Последний успешный deploy —
`18e2ed9a` в 08:08:26 MSK (FETCH_HEAD timestamp подтверждает).

Staging git работает на 18e2ed9a, docker containers — на образах из моего
SEV2 manual rebuild. External /live/ = 200, но git tree drift от main.

**Детали расследования**: `docs/audit/staging-auto-deploy-investigation.md`.

**Гипотезы**:
- (HIGH) CI workflow падал на 7 коммитов → `workflow_run.conclusion != 'success'`
  → deploy skip. В range есть backend changes (`7e834829` добавил new endpoint
  + settings), которые могут ломать тесты.
- (MED) GitHub Actions quota exceeded / billing issue.
- (LOW) `STAGING_SSH_PRIVATE_KEY` secret expired — но тогда deploy runs
  появлялись бы в UI с failure, а их не видно.
- (LOW) Concurrency group cancelled все deploys.

**Что нужно от пользователя** (в зависимости от доступных инструментов):

1. **Preferred**: `gh auth login` на local machine Claude Code (user должен залогиниться
   в GitHub CLI, после этого любая следующая сессия сможет `gh run list --workflow=ci.yml`).
2. **Alternative**: открыть https://github.com/darbyhtml/proficrm/actions, посмотреть:
   - Status last 10 CI runs (success / failure / cancelled?).
   - Deploy Staging runs — есть ли вообще runs для 7 skipped commits?
   - Текст failure если CI red.
3. **If CI failed**: сформировать промпт «fix CI failure <details>», Claude Code в
   следующей сессии разберёт.
4. **If secret expired / missing**: ротация в GitHub Settings → Secrets and variables
   → Actions → `STAGING_SSH_PRIVATE_KEY`. Public counterpart добавить в
   `/root/.ssh/authorized_keys` на staging.

**ASSUMPTION если нет ответа** (pending):
- Auto-deploy broken — но НЕ блокер для W0.5a-safe planning (которое уже scaffold'им).
- Staging manual управление через `make restart-staging-*` targets работает.
- Prod deploy через gated promotion (runbook, не через auto-deploy) не затронут.
- Новая версия `deploy-staging.yml` с auto-rollback будет протестирована при
  восстановлении pipeline.

**Q12 RESOLVED 2026-04-21** (после получения GH_TOKEN и анализа API):

**Root cause**: **GitHub Actions billing / spending limit exceeded**.

Точный annotation текст с первого failed check-run (bandit, commit e96dbad4):
> The job was not started because recent account payments have failed or your
> spending limit needs to be increased. Please check the 'Billing & plans'
> section in your settings

**Характеристики failure**:
- Первый failed CI run: 2026-04-20 17:14:43 UTC (commit `d30b0ce0`).
- Последний failed CI run: 2026-04-21 08:17:50 UTC (commit `e96dbad4`).
- Run duration: **2-5 секунд** (jobs отвергаются до начала выполнения).
- Все jobs в each run — failure (lint/bandit/format-check/secret-scan/mypy/
  migration-linter/deps-audit). Test job всегда `skipped` из-за deps.
- deploy-staging workflow **корректно skip**s — condition
  `github.event.workflow_run.conclusion == 'success'` срабатывает (conclusion
  = failure → false → skip). Workflow config correct.

**НЕ root cause** (исключено):
- ❌ Secrets / SSH key — не дошло до выполнения SSH step.
- ❌ Workflow syntax / config — парсится, jobs создаются.
- ❌ Failing tests / flaky — jobs не успевают run'нуть тесты.
- ❌ Code bug в W0.4 commits — все runs с 2026-04-20 17:14 одинаково fail'ятся.

**User action required** (не могу сам):
1. Зайти https://github.com/settings/billing/spending_limit (или organization
   billing если repo в org).
2. Проверить:
   - Есть ли unpaid invoice (recent payment failures)?
   - Spending limit установлен на низкое значение?
   - Free tier minutes исчерпан для private repo?
3. Решение: payment method обновить ИЛИ spending limit увеличить ИЛИ 
   сделать repo public (free unlimited Actions).
4. После fix — trigger dummy commit → CI должен пройти → deploy-staging
   автоматически вытянет staging на новейший main.

**Full timeline**: `docs/audit/gh-actions-timeline-2026-04-21.txt`.

**НЕ блокирует W0.5a-safe**: release branch создаётся и deploy'ится вручную
(см. Track V). CI нужен только для автоматизации. После billing fix + первого
successful CI run — auto-deploy pipeline заработает с новой версией
`deploy-staging.yml` (которая имеет nginx restart + smoke + auto-rollback).

---

### Q11 [2026-04-21] W0.5a — pre-flight status

**Контекст.** Pre-W0.5a cleanup session (2026-04-21) включает sanity check
prod-состояния перед sync wave. Проверка read-only, без `CONFIRM_PROD=yes`.

**Prod state (snapshot 2026-04-21 ~10:30 UTC):**

| Параметр | Значение |
|----------|---------|
| Prod HEAD | `f015efb1` (2026-03-20 — Fix(Contacts): email-валидация) |
| main ahead of prod | **~330+ commits** (`origin/main` cached на prod = 28bbe975 от 2026-04-17, dev HEAD сейчас `ec8b85bb`) |
| Migrations pending | **48** новых миграций при sync prod→main (см. `git log --name-only f015efb1..HEAD` на backend/*/migrations) |
| Disk (/dev/vda1) | 67% used, 26 GB free ✅ |
| Memory | 4544/8078 MB used, 3533 MB available ✅ |
| **Swap** | **2037/2047 MB used = 97%** ⚠️ — VPS под нагрузкой активно свопит |
| Prod web-1 | Up 24 hours (no healthcheck configured) ✅ |
| Prod db-1 | Up 24 hours (healthy) ✅ |
| Prod redis-1 | Up 5 weeks (healthy) ✅ |
| Prod celery-1 | **Up 24 hours (unhealthy)** ⚠️ — hotlist #9 active, healthcheck broken (`celery inspect ping` таймаут), но celery сам работает |
| Prod celery-beat-1 | Up 24 hours (no healthcheck) |
| Prod websocket | **ОТСУТСТВУЕТ** — prod не имеет daphne/channels контейнера |
| Prod HTTP | Home 302, `/health/` 200 ✅ |
| Last prod backup | `/tmp/release-0-backups/prod_pre_release0_20260420_065858.sql.gz` (2026-04-20) |
| `/opt/proficrm/backups/` | **Устарел** — только `crm_20260315_111600.sql.gz` (март) ⚠️ |

**Оценка downtime для W0.5a sync:**

| Шаг | Оценка |
|-----|--------|
| `git pull origin main` | 1-2s |
| `docker compose build web celery celery-beat websocket` | 2-4 min (первый раз, новые зависимости) |
| `docker compose run --rm web python manage.py migrate --noinput` | **5-15 min** (48 миграций, одна из них на ActivityEvent ~9.5M rows) |
| `docker compose up -d --force-recreate web celery celery-beat websocket` | 30-60s |
| `docker restart proficrm-nginx` (если есть host nginx) | 5s |
| Post-deploy smoke | 30s |
| **Итого** | **~10-25 минут downtime** (worst case, если миграции быстрые — 5-7 мин) |

**Blockers / risks перед W0.5a:**

1. **Pre-deploy backup MANDATORY.** Последний backup в стабильной локации — 
   `/tmp/release-0-backups/` (рискованно — `/tmp` чистится). Перед W0.5a 
   нужно свежий `pg_dump` в `/opt/proficrm/backups/` или `/root/backups/`, 
   чтобы rollback был возможен.

2. **Swap pressure 97%.** Добавление websocket контейнера + 48 migrations + 
   force-recreate могут спровоцировать OOM. Рекомендация — перед W0.5a 
   рестарт VPS или hard-kill Chatwoot (~1 GB) чтобы освободить RAM.

3. **Prod celery healthcheck broken** (hotlist #9). После sync — healthcheck-fix 
   коммита `242fcf2a` подтянется, проверить `docker inspect proficrm-celery-1` 
   через 2 минуты после up.

4. **Websocket container new on prod.** Впервые появится в compose — 
   проверить что `docker-compose.yml` на prod содержит `websocket` сервис 
   или он пулится из репо.

**Recommended sequence for W0.5a:**

1. Freeze prod (Telegram heads-up пользователю: «prod будет down ~15 мин»).
2. Snapshot: `pg_dump + tar /opt/proficrm/media + cp .env` в `/root/backups/w05a-pre-sync-<timestamp>/`.
3. `git fetch origin + git checkout <release-tag>` (НЕ pull main — только по tag).
4. `docker compose build web celery celery-beat websocket` (все, не только web).
5. `docker compose run --rm web python manage.py migrate --noinput` (засечь время).
6. `docker compose up -d --force-recreate web celery celery-beat websocket`.
7. `docker restart proficrm-nginx` (если есть host nginx).
8. `sleep 60 && bash tests/smoke/prod_post_deploy.sh` (должен быть green).
9. Verify celery healthy (`docker inspect proficrm-celery-1 --format '{{.State.Health.Status}}'`).
10. Telegram UP announce + GlitchTip event verification (первая prod error → issue появится).

**ASSUMPTION если нет ответа пользователя** (для меня ориентир, не решение):
- W0.5a НЕ запускать сегодня — нужен явный промпт с `DEPLOY_PROD_TAG=release-v1.0-w0-complete` + `CONFIRM_PROD=yes`.
- Сейчас всё done в этой сессии — CI improvements + Makefile + incident 3/3 closed.

---

## 🟡 Active — W0.4 closeout regression (2026-04-21)

### Q9 [2026-04-21] Dual uptime monitoring — strategy

**Контекст.** Обнаружено два независимых monitoring потока в один Telegram
канал (chat_id <USER_CHAT_ID> / `@proficrmdarbyoff_bot`):

(a) **`/opt/proficrm/scripts/health_alert.sh`** — cron `*/5 * * * *` от sdm.
   Мониторит `http://127.0.0.1:8001/health/` на prod VPS локально. Алерты
   формата `🔴 CRM ПРОФИ — УПАЛ`. Existed since March 2026.

(b) **Uptime Kuma** — 3 monitors через external HTTPS (crm.groupprofi.ru/health/,
   crm-staging.groupprofi.ru/live/, glitchtip.groupprofi.ru/_health/). Алерты
   формата `[Monitor Name] [🔴 Down]`. Added 2026-04-21.

Overlap: **только на prod CRM** — оба проверяют Django app. Staging и GlitchTip
мониторит только Kuma.

**Варианты.**
- **A. Оставить оба** — redundancy + разные failure modes (local vs external).
  Risk: double alerts в Telegram при down/up prod CRM.
- **B. Выключить health_alert.sh (cron disable)** — только Kuma. Проще, но
  теряем history + independence от external network.
- **C. Split scope (recommended)** — оставить health_alert.sh как internal
  probe (без изменений), удалить из Kuma monitor #1 CRM Production (оставить
  только Staging + GlitchTip). Prod alert только от health_alert.sh.

**Detail**: `docs/audit/existing-monitoring-inventory.md`.

**Пока ответа нет — ASSUMPTION:** вариант A (оставить оба, терпеть double на
изменении состояния prod). Не критично — алерты state-based (только при up↔down).

---

### Q10 [2026-04-21] Staging test user для real-traffic verification

**Контекст.** Wave 0.4 regression выявил: shell-based smoke test (ручной
`_enrich_scope`) не эквивалентен real HTTP через Django MIDDLEWARE. Правильный
test — через `django.test.Client.force_login(user)`. Для верификации нужен
staging test user с realistic role/branch setup.

Сейчас использовал суперюзера id=1 (admin/sdm). Но хотелось бы отдельного
**test user с role=manager, branch=ekb** — ближе к real юзерам.

**Что нужно от пользователя** (опционально, для будущих sessions):
- Создать staging user `qa@groupprofi.ru` с role=MANAGER, branch=ekb
- Password в `/etc/proficrm/env.d/staging-qa-user.conf` (mode 600)

**Пока нет — ASSUMPTION:** используем admin (id=1). Работает, но не realistic.

---

## 🟡 Active — W0.4 Track D

### Q7 [2026-04-20] Telegram bot для uptime alerts — создать новый?

**Контекст.** Wave 0.4 Track D: Uptime Kuma развёрнут как self-hosted замена
UptimeRobot (который недоступен в РФ без VPN и Telegram-integration стал
платным). Uptime Kuma умеет слать alerts в Telegram через bot API.

**Проблема.** Telegram bot в проекте не найден (проверено:
`docs/audit/telegram-bot-inventory.md`). Ни в `.env`, ни в коде, ни в
systemd. Alert'ы пока уходят только в UI Kuma (который доступен через
SSH tunnel или после DNS uptime.groupprofi.ru — см. Q8 ниже).

**Что нужно от пользователя.**
1. Создать нового бота через `@BotFather` (2 минуты, инструкция в
   `docs/audit/telegram-bot-inventory.md` §«Что сделать пользователю»).
2. Сохранить token + chat_id в `/etc/proficrm/env.d/telegram-alerts.conf`
   (mode 600).
3. Ответить в этот Q — я добавлю notification channel в Kuma.

**Пока ответа нет — ASSUMPTION:**
- Uptime Kuma работает **без** Telegram alerts.
- При падении сервиса — узнаём через UI Kuma или email (если SMTP подключим
  отдельно).
- Real-time notifications отложены.

---

### Q8 [2026-04-20] DNS A-запись uptime.groupprofi.ru?

**Контекст.** Uptime Kuma UI сейчас доступен только через SSH tunnel:
```bash
ssh -L 3001:localhost:3001 root@5.181.254.172
# → http://localhost:3001
```

Для web-доступа с любого устройства без tunnel — нужна A-запись в DNS,
аналогично `glitchtip.groupprofi.ru` (ответ Q1 в закрытых).

**Что нужно от пользователя.**
1. Добавить A-запись `uptime.groupprofi.ru` → `5.181.254.172` в Netangels
   DNS (аналогично `glitchtip.groupprofi.ru` из W0.4 pre-flight).
2. Ответить в Q — я сконфигурирую nginx reverse-proxy + Let's Encrypt TLS
   + basic-auth (чтобы не был публично доступен).

**Пока ответа нет — ASSUMPTION:**
- Kuma доступен через SSH tunnel на порту 3001.
- Это ок для одного админа (пользователь), но неудобно для мониторинга
  «с телефона».

---

## 🔴 W0.4 Blockers (GlitchTip prerequisites, 2026-04-20)

### Q1. DNS: поддомен `glitchtip.groupprofi.ru` — кто управляет и сроки?

**Контекст.** W0.4 план требует `https://glitchtip.groupprofi.ru/` с Let's Encrypt TLS.
Сертификаты выдаются через `certbot --nginx` после создания A-записи.
На сервере сейчас есть сертификаты на `crm`, `crm-staging`, `chat` (single-domain),
wildcard `*.groupprofi.ru` **не установлен**.

**Что нужно от пользователя.**
1. Кто управляет DNS `groupprofi.ru` (регистратор/панель)?
2. Можно ли завести A-запись `glitchtip.groupprofi.ru → 5.181.254.172`?
3. Сроки добавления (минуты/часы/дни)?

**Пока ответа нет — ASSUMPTION для W0.4:**
- GlitchTip поднимается **на staging VPS (5.181.254.172)** на порту **8100**.
- Доступ временно через `ssh tunnel` (`ssh -L 8100:localhost:8100 root@...`) или через
  Cloudflare tunnel. Для prod-использования (алерты в Telegram) — endpoint-URL
  GlitchTip пишем как `http://5.181.254.172:8100/` и закрываем nginx-whitelist'ом
  по IP прод-сервера.
- `crm-staging.groupprofi.ru/glitchtip/` path-prefix вариант **НЕ используем** —
  GlitchTip не любит non-root mount'ы, сломается assets/OAuth.

**После получения DNS** (ориентир в W10.x) — заводим A-запись, `certbot`, переезжаем
на `https://glitchtip.groupprofi.ru/`, меняем `SENTRY_DSN` в `.env` prod и staging.

---

### Q2. RAM budget на VPS — безопасно ли добавлять GlitchTip (~600 MB)?

**Факты на 2026-04-20 18:00 MSK (staging VPS 5.181.254.172):**
- Total: 7.9 GB
- Used: 4.3 GB (CRM web × 4 + Celery × 4 + Chatwoot × 4 + Redis/Postgres)
- Free: **413 MB** (без buff/cache)
- Available: 3.6 GB (с buff/cache)
- **Swap used: 1.0 GB из 2.0 GB** ← уже свопит, это красный флаг
- Load avg: 0.27 (спокойно, но не под нагрузкой)

**Что нужно от пользователя.**
1. Ок ли взять ~600 MB для GlitchTip (web + worker + postgres + redis)?
2. Альтернатива: закрыть Chatwoot? Он в Release 2 помечен как «уходит», сейчас
   занимает ~1 GB (3 контейнера). Если гасим — освобождается ресурс для GlitchTip.
3. Альтернатива: взять отдельный **младший VPS 1-2 GB RAM** только под GlitchTip?
   ~200-300 ₽/месяц на Netangels/Selectel.

**Пока ответа нет — ASSUMPTION для W0.4:**
- GlitchTip docker-compose с **жёсткими memory limits**: web 256 MB, worker 192 MB,
  db 128 MB, redis shared с existing. Итого ~576 MB.
- Мониторим swap в течение недели. Если вырастет > 1.5 GB — либо гасим Chatwoot,
  либо переезжаем на отдельный VPS (возвращаемся в Q2.2).

---

### Q3. `proficrm-celery-1` unhealthy 11 часов — отдельный инцидент

**Факт.** При проверке prerequisites видно:
```
proficrm-celery-1  Up 11 hours (unhealthy)
```

**Контекст.** Celery healthcheck был починен в Release 0 (коммит `242fcf2a` —
убрано `-d $HOSTNAME`). Но `proficrm-celery-1` — это **prod** (а не `crm_staging_celery`).
Прод, возможно, не получил обновление healthcheck.

**Что нужно от пользователя.**
1. Проверить `docker inspect proficrm-celery-1 --format '{{.State.Health.Status}}'`
   чтобы увидеть конкретную причину unhealthy.
2. Если это регрессия после Release 0 — нужен мини-hotfix (пересоздать контейнер
   с актуальным healthcheck из main).
3. Если это staged регрессия (коммит healthcheck-fix ещё не задеплоен на prod) —
   это нормально, войдёт в Release 1.

**Пока ответа нет — ASSUMPTION: не блокер для W0.4**, но документирую как side-finding.
Добавляю в `docs/audit/hotlist.md` item-кандидат для Release 1 verification checklist.

---

## 🟡 Wave 1 Pending (из code-review)

_(эти пункты накапливаются по ходу волн, следующие сессии их разбирают)_

### Q4. `TWO_FACTOR_MANDATORY_FOR_ADMINS` — «мягкая миграция 2 недели» или day-1?

Принято ASSUMPTION: мягкая 2 недели (см. ADR в будущем W2.4).
Подтверждение пользователем — перед стартом W2.4.

### Q5. S3 / MinIO (W10.1) — на том же VPS или соседнем?

Принято ASSUMPTION: MinIO self-hosted на том же VPS (exclude Yandex Object
Storage — принцип «только бесплатное / self-hosted»).
Подтверждение — перед W10.1.

### Q6. Bounce handling smtp.bz — webhook или IMAP?

Нужно проверить в админке smtp.bz наличие webhook-API.
Принято ASSUMPTION: ветка B (IMAP-fallback), если webhook недоступен.
Решение — в начале W6 (grep кода + админка smtp.bz).

---

## ✅ Закрытые

### Q10 [2026-04-21] Staging test user для real-traffic verification

**Ответ** (2026-04-21): `sdm / ooqu1bieNg` — staff superuser на staging.

Используется в `scripts/verify_sentry_real_traffic.py` (env `VERIFY_USERNAME=sdm`)
для real-HTTP verification middleware chain через Django `Client.force_login`.

Event `66e3bae6c125...` (issue #9 CRM-STAGING) подтвердил работу middleware
с этим пользователем: все 5 custom tags + 2 scope.user tags enriched корректно.

Password в `/etc/proficrm/env.d/staging-qa-user.conf` на сервере (будет
добавлен пользователем при необходимости — пока в ответе на Q9/Q10 указан
inline, это временно).

### Q9 [2026-04-21] Dual uptime monitoring strategy

**Ответ** (2026-04-21): **Option C — split-scope**.

Реализация:
- Kuma monitor #1 «CRM Production» **paused** через `api.pause_monitor(id=1)`.
- `scripts/health_alert.sh` остаётся единственным источником prod uptime alerts.
- Kuma мониторит staging + GlitchTip + новый self-check (uptime.groupprofi.ru HEAD).

Детали — `docs/audit/existing-monitoring-inventory.md` §«Q9 resolved».

### Q-policy [2026-04-20] Какой режим работы Claude Code с prod?

**Ответ**: Gated promotion. Blanket hook-block на `/opt/proficrm/` отменён в
пользу explicit-marker модели. Детали — `CLAUDE.md` §«Деплой — Gated Promotion
Model» R1-R5, operational runbook — `docs/runbooks/prod-deploy.md`.

Ключевое:
- Prod deploys only via git tag `release-v1.N-wX.Y-<name>`.
- Claude Code МОЖЕТ выполнить deploy-команду только при наличии в промпте
  маркеров `DEPLOY_PROD_TAG=<tag>` + `CONFIRM_PROD=yes`.
- Prod file edits (.env, systemd, nginx без observability) — только с
  `CONFIRM_PROD=yes`.
- `/opt/proficrm-observability/` — free access (GlitchTip — shared infra).

Initial tag: `release-v0.0-prod-current` на commit `be569ad` (prod state
до смены политики, 333 commits behind main).

### Q1 [2026-04-20] DNS glitchtip.groupprofi.ru

**Ответ**: A-запись добавлена в Netangels 2026-04-20, CAA letsencrypt.org
покрывает. Certbot TLS выдан, expires 2026-07-19, auto-renew OK.

### Q3 [2026-04-20] proficrm-celery-1 prod unhealthy

**Ответ**: не чинится в W0.4. Добавлен в `docs/audit/hotlist.md` item #9
как Release 1 verification checklist. Прод HEAD `be569ad` отстаёт от main
на 333 commits, healthcheck-fix (`242fcf2a`) не дошёл — применится при
первом prod-deploy по gated promotion.

— остальные вопросы активны —
