# PM current context

_Живое состояние текущей PM-сессии. PM обновляет этот файл перед предсказуемым compact или каждые 30-60 минут активной работы. После compact — читается ПЕРВЫМ для восстановления контекста._

**Last updated:** 2026-04-24 12:10 UTC (PM).

---

## 🎯 Current session goal

W10.2-early 🟡 PARTIAL — Шаг 1a ✅ (бакет создан), Шаг 1c → Сценарий B (Cloudflare API не даёт создать permanent R2 S3-токены без прав `API Tokens: Edit` — разумный security design). Ждём от Дмитрия dashboard-creation R2 API Token + delivery на VPS.

## 📋 Active constraints

- Path E: **ACTIVE**.
- R2 bucket `proficrm-walg-staging` **создан** через API (2026-04-24 11:41 UTC).
- `CF_API_TOKEN` + `CF_ACCOUNT_ID` валидны, в `.env.staging`. После delivery R2 S3 creds CF_API_TOKEN можно revoke (его роль выполнена).
- Защитный слой pg_dump работает.
- Disk 23 ГБ свободно.

## 🔄 Last decision made

**Timestamp:** 2026-04-24 12:10 UTC.
**Decision:** Сценарий B — Дмитрий создаёт R2 API Token через дашборд, scope `Object Read & Write`, specific bucket, no TTL. После delivery на VPS — исполнитель resume с Шага 2 (WAL-G install), Шаг 1 fully closed.
**Reasoning:** публичный API Cloudflare требует `API Tokens: Edit` scope для создания новых tokens — нет у `CF_API_TOKEN`. Это design limitation (предотвращение privilege escalation), не bug. Dashboard action 2-3 минуты — стандартный workflow для первого R2 setup.
**Owner:** Дмитрий (dashboard + SSH на VPS).

## ⏭️ Next expected action

1. ✅ Обновить `docs/pm/current-context.md` (этот файл).
2. ✅ Коммит.
3. ⏭️ Передать Дмитрию compact-инструкцию создания R2 API Token + добавления 4 переменных в `.env`.
4. ⏭️ Ждать сообщение «R2 S3 creds на VPS».
5. ⏭️ Передать исполнителю короткое resume от Шага 2 (Шаг 1 fully done, bucket existed, credentials в env).
6. ⏭️ Ждать финальный рапорт W10.2-early через 4-5 часов.

## ❓ Pending questions to Дмитрий

- [ ] **Создать R2 API Token** в дашборде:
  - Cloudflare → R2 Object Storage → **Manage R2 API Tokens** → Create API Token.
  - Name: `proficrm-walg-staging-r2`.
  - Permissions: **Object Read & Write**.
  - Specify bucket: **Apply to specific buckets only** → `proficrm-walg-staging` (least-privilege).
  - TTL: blank (permanent).
  - Copy значения (показываются один раз): Access Key ID, Secret Access Key, S3 endpoint.
- [ ] **Добавить на VPS** в `/opt/proficrm-staging/.env` (не перезаписать существующий файл, только дописать):
  ```
  R2_ACCESS_KEY_ID=<Access Key ID>
  R2_SECRET_ACCESS_KEY=<Secret Access Key>
  R2_BUCKET_NAME=proficrm-walg-staging
  R2_ENDPOINT=https://<ACCOUNT_ID>.r2.cloudflarestorage.com
  ```
  `chmod 600 /opt/proficrm-staging/.env` (уже должно быть).
- [ ] Ответить PM «R2 S3 creds на VPS».

## 📊 Last Executor rapport summary

**Session:** W10.2-early Шаги 1a-1c (R2 setup через Cloudflare API).
**Received:** 2026-04-24 12:05 UTC.
**Status:** 🟡 PARTIAL — Шаг 1a ✅, Шаг 1c Сценарий B.
**Classification:** **win** — audit discipline, security discipline (0 утечек литералов), Context7 research thorough, сценарий B triggered правильно.

### Прогресс

- **Шаг 1a ✅:** бакет `proficrm-walg-staging` создан (listing подтверждает).
- **Шаг 1b ✅:** Context7 research findings:
  - Permanent R2 S3 creds нельзя создать прямым API endpoint — нужна 2-шаговая модель через `/user/tokens` с R2 permission groups.
  - Temporary credentials (36 ч) существуют, не подходят для archive_command.
  - Permission group bucket-scoped: `Workers R2 Storage Bucket Item Write` (id `2efd5506f9c8494dacb1fa10a3e7d5b6`).
- **Шаг 1c → Сценарий B:** `POST /user/tokens` возвращает error 9109 «Unauthorized» — CF_API_TOKEN не имеет scope `API Tokens: Edit` (design limitation Cloudflare, не bug).

### Следующий рапорт

После delivery R2 S3 creds — финальный рапорт end-to-end через 4-5 часов (Шаги 2-7: WAL-G install → archive_command → base backup → restore drill → runbook).

## 🚨 Red flags (if any)

Нет. Все stops corrrect, zero утечек секретов, bucket как persistent side-effect — нормален (используется на Шаге 2).

## 📝 Running notes

### Resume-сообщение для исполнителя (после «R2 S3 creds на VPS»)

> **Resume W10.2-early от Шага 2 (WAL-G install).** Шаг 1 fully closed:
> - Bucket `proficrm-walg-staging` created (2026-04-24 11:41 UTC).
> - R2 S3 credentials в `/opt/proficrm-staging/.env`: R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY, R2_BUCKET_NAME, R2_ENDPOINT.
> Продолжай по оригинальному промпту от Шага 2 (install WAL-G binary v3.0.3 → /etc/wal-g/walg.env → test `wal-g st ls` → Шаги 3-7). Security discipline та же. Stop conditions те же.

### Полная reuse промпта не требуется

Оригинальный промпт W10.2-early от ~11:00 UTC валиден. Коротких resume-сообщений достаточно (2 переданы: после R2 activation, далее после S3 creds delivery).

### Lesson candidates (добавить после closure)

- **Lesson 9** — PM failure указать explicit safe channel для секретов.
- **Lesson 10** — cloud service activation ≠ credentials (включать в Шаг 0).
- **Lesson 11** (new) — Cloudflare API не позволяет создавать permanent R2 S3-tokens без `API Tokens: Edit` scope. Для новых проектов сразу планировать dashboard step или использовать Terraform-managed tokens.

### Update triggers (reminder)

- Перед каждой передачей Дмитрию.
- Каждые 30-60 минут активной работы.
- После получения рапорта исполнителя.
- После принятия решения.
- Перед длительной операцией.
- При приближении к компактификации контекста.
