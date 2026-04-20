# Open Questions — открытые вопросы для пользователя

_Правило (из 00_MASTER_PLAN.md §2): Claude Code не упирается в неопределённость молча —
заносит сюда запись с явным допущением `ASSUMPTION:` и продолжает. Пользователь
отвечает в Q-ответ, Claude следующей сессии убирает item._

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
