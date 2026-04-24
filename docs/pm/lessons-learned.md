# Lessons learned — GroupProfi CRM

_Накопленный опыт из реальных sessions (апрель 2026+). Читается при bootstrap PM-сессии. Каждый урок — из инцидента или discovery, не теория._

---

## Lesson 1: Deploy stdin bug

**Date:** 2026-04 (обнаружено пост-хок audit'ом).

**Что случилось:**

`docker compose run --rm web migrate` внутри bash heredoc `<<SCRIPT ... SCRIPT` consume'ил parent stdin → script exit'ил early **до** запуска migrate. Deploy workflow `deploy-staging.yml` показывал "успех", но gunicorn перезапускался на **стариом коде**.

**Impact:** 23h gunicorn на staging без 2FA routes (W2.1 changes). Undetected пока не сделали post-hoc audit через `docker inspect`.

**Fix:**

```bash
set -euxo pipefail

# Добавлены:
docker compose run --rm -T web python manage.py migrate </dev/null

# И marker в конце:
echo "=== DEPLOY FULLY COMPLETED ==="
```

**Правило:** Каждый deploy workflow **обязан** emit `DEPLOY FULLY COMPLETED` marker в последней строке. Executor в rapport **обязан** grep этот marker:

```bash
gh run view $DEPLOY_RUN --log | grep "DEPLOY FULLY COMPLETED"
```

Если не найден — deploy не завершён, независимо от exit code.

**Reference:** `docs/audit/staging-auto-deploy-investigation.md`.

---

## Lesson 2: Pre-deploy usage audit мандатный

**Date:** 2026-04-22 (W2.7).

**Что случилось:**

W2.6 commit (`ab89c287`) добавил block non-admin JWT login на `/api/token/`. Planning assumed что прод имеет 0 пользователей на этом endpoint (все managers используют session login).

**Discovery (W2.7 audit):**

Prod `ErrorLog` + `jwt_login_success` events показали: **98 `jwt_login_success` events за 30 days** от user id=13 (nkv — Непеаниди Ксения, manager, branch Курск). External IP 83.239.67.30 (её mobile carrier). Device: Xiaomi 23129RN51X.

Без audit — W2.6 deploy на прод сломал бы её Android app без warning. Emergency rollback или hotfix был бы нужен.

**Что сделали вместо:**

- Documented в `docs/audit/w2-7-android-user-identified.md`.
- Added hotlist item "nkv Android migration" — pre-W9 blocker.
- Migration plan: ~30 min coordination, новый QR scan (Android app уже поддерживает QR flow v0.5).

**Правило:**

Перед любым breaking change на auth / API / behavior surface:

1. **Query prod DB / logs** на endpoint.
2. **Identify users** с active usage в последние 30 days.
3. **Communicate** с affected users через Дмитрия / support контакт.
4. **Migration plan** с rollback.
5. **Only then** — staging change → main → (eventually) prod.

**References:**

- `docs/audit/w2-7-jwt-usage.md` — initial audit (stop condition triggered).
- `docs/audit/w2-7-android-user-identified.md` — user identification + revised recommendation.
- `docs/audit/hotlist.md` — "nkv Android migration" item.

---

## Lesson 3: qa_manager deletion incident (W2.1.4.1)

**Date:** 2026-04.

**Что случилось:**

Во время testing `settings_user_delete` view Claude Code (Executor) acceptally deleted `qa_manager` (id=53). Это был shared staging QA user — 2FA config, fixtures, тесты всей команды привязаны к нему.

**Recovery:** User recreated как id=54, но id shift сломал несколько fixture-based тестов. ~2h восстановления.

**Root cause:**

- Destructive test targeted shared staging user (не disposable).
- Нет "double check" на username перед delete.
- Confirmation dialog был не строгим.

**Fix (Pattern 4 в playbook):**

`backend/core/test_utils.py` добавил:

```python
def make_disposable_user(role='MANAGER', prefix='', **kwargs):
    """Create temporary user с unique timestamp-based username.
    
    Usage:
        user = make_disposable_user(role='MANAGER', prefix='w3_')
        try:
            # destructive test
        finally:
            user.delete()
    """
    ts = int(time.time() * 1000)
    username = f'{prefix}disposable_{ts}'
    # ... create user
```

**Правило:**

1. **Никогда** не target shared staging users (`sdm`, `qa_manager`, `perf_check`, `admin_tour_*`) для destructive tests.
2. **Create disposable** user с prefix wave.
3. **Test + delete** в try/finally.
4. **Verify 0 orphans** после session.
5. Если нужен admin — `browser_tour_<ts>` с TOTP, cleanup после.

**Reference:** `docs/audit/w2-1-4-1-incident-qa-manager-delete.md`.

---

## Lesson 4: "Dormant features" — UX audit revelation

**Date:** 2026-04-22 / 23 (UX-1 ... UX-4 sessions).

**Что случилось:**

4 подряд UX sessions estimated 2-6h каждая на implementation "новых" features. Actual — 1 LOC до 540 LOC, потому что infrastructure уже existed, integration missing.

| Session | Original estimate | Actual | Что существовало |
|---------|-------------------|--------|------------------|
| UX-1 Timeline | 3-4h service + UI | 60 LOC template + 152 LOC JS | `build_company_timeline()` уже existed |
| UX-3 Bulk actions | 2-3h UI + API | **1 LOC** | Full UI+JS+backend existed, missed context var |
| UX-4 Quick-add | 2h modals | ~200 LOC | V2Modal + partial existed |
| UX-2 Global search | 4-6h new service | 540 LOC | CompanySearchIndex mature, needed cross-entity wire |

**Insight:** Проект имеет ~18 месяцев dev history, несколько фаз refactoring, но coordination в chain implementation → integration → UI отвалилась на этапе "последняя миля". Fully-built features sitting в коде, users не видят, nobody noticed в production.

**Root cause гипотеза:**

- Waves/sprints закрывались без end-to-end user verification.
- "Backend done → moving on to next" культура.
- Нет E2E test на "feature видна в UI".

**Правило (Pattern 1 в playbook):**

**Перед любой implementation session:**

1. **Assume infrastructure existed.** Даже если документация пуста / hotlist не упоминает.
2. **Grep wide** по keyword (not только exact feature name): `grep -rn "timeline\|history\|audit" backend/companies/`.
3. **Read** `models.py` (поля?), `services/` (business logic?), `views/` (endpoints?), `templates/*/partials/` (UI?), `static/*/js/` (frontend?).
4. **If existed** → wire it, не rebuild. Documented в rapport.
5. **If partially existed** → complete gaps, не parallel implementation.

**Estimate compression typical:** 3-4x меньше when infra existed.

**References:**

- `docs/ux/ux-audit-2026-04-23.md` (audit document).
- UX-1 ... UX-4 commits in recent `git log`.

---

## Lesson 5: nkv discovery — verify before panic

**Date:** 2026-04-22.

**Что случилось:**

W2.7 audit обнаружил nkv (Непеаниди Ксения) — active Android user с 98 JWT logins / 30 days.

**Initial panic** (PM + Дмитрий):

> "Android app сломан, требует rewrite от IT-друга, W9 deploy delayed на недели, critical user impact."

**Актуальная discovery** (после 30 min research):

- Android app **здоров** (v0.5, QR flow implemented уже).
- nkv использует password path потому что **старая установка** (2026-01-12 device registered).
- Migration = **30-минутная coordination** (new QR scan), не Android dev work.
- IT-друг не нужен — это usage coordination, не code change.

**Что бы случилось без discovery:**

- Неоправданная задержка W9 prod deploy на недели.
- IT-друг alerted для non-existent bug.
- Stress для Дмитрия.

**Правило:**

Discovery **предотвращает** wrong panic-driven decisions. Когда обнаружил impact:

1. **Pause.** Не сразу write alarming rapport.
2. **Verify facts:** device state, app version, last activity, actual breaking behavior.
3. **Reproduce в staging** если возможно.
4. **Quantify impact:** N users, M operations, severity X.
5. **Then** write rapport с facts, не assumptions.

**Anti-pattern:** "я обнаружил что X существует → assume worst case → alarm Дмитрия". Вместо — "я обнаружил X → verified Y и Z → impact is W, options are A/B/C".

**Reference:** `docs/audit/w2-7-android-user-identified.md` — "Revised recommendation" section.

---

## Lesson 6: Compact-driven context loss

**Date:** ongoing (критично для long PM sessions).

**Что происходит:**

Claude Code автоматически сжимает conversation когда достигается context limit (~150K-200K токенов). Первые ~80% сообщений заменяются summary. PM теряет in-context memory недавних decisions и state.

**Impact без mitigation:**

- PM забывает current task goal → recommends уже закрытое.
- PM switch на английский потому что system prompt patterns faded.
- PM rubber-stamps Executor потому что review standards faded.
- PM предлагает prod deploy потому что Path E faded.
- Дмитрий видит regression в quality discussions.

**Symptoms post-compact drift:**

- Английский в ответах без причины.
- «Let me re-analyze...» — re-doing закрытую работу.
- Forgot recent rapport от Executor'а.
- Questions которые были answered 10 минут назад.
- Suggestions violating Path E.

**Mitigation — 4 layers:**

### Layer 1: Persistent state file

`docs/pm/current-context.md` — живой PM state.

**Update triggers:**

- Перед каждой передачей Дмитрию.
- Каждые 30-60 минут работы.
- После получения Executor rapport.
- После любого decision.

**Format:** переписать полностью, не incremental. Timestamp в header. Git commit после update.

### Layer 2: Post-compact ritual

См. `CLAUDE.md` §"Post-compact / session-start ritual".

Обязательно прочитать 8 items **до** substantive response:

1. CLAUDE.md
2. docs/pm/current-context.md ← primary state restoration
3. docs/pm/playbook.md
4. docs/pm/lessons-learned.md
5. docs/current-sprint.md
6. docs/audit/hotlist.md
7. `git log --oneline -20`
8. Last 10-20 messages conversation.

### Layer 3: Git commits as checkpoints

Каждый commit — immutable checkpoint состояния. PM может reconstruct timeline через `git log`.

**Rule:** PM рекомендует Дмитрию коммитить docs changes regularly (не накапливать stash). Commit atomic: один logical change = один commit, with descriptive message.

### Layer 4: Дмитрий как external memory

Дмитрий может notice drift у PM. В этом случае:

- Дмитрий говорит: «Ты выбился из контекста, почитай state files.»
- PM: stop, read ritual, acknowledge, continue.

**Response template когда PM замечает drift:**

> «Заметил признаки drift после compact — [specific symptom]. Прочитал `docs/pm/current-context.md` + другие state files. Контекст восстановлен. Продолжаю с [specific context].»

Commit note в `docs/pm/current-context.md` «Red flags» section.

**Rule:** better recovery чем silent drift. Acknowledge openly.

**Anti-pattern:** PM читает memory «nkv migration needed» → предполагает что это open → recommends session. Но migration могла пройти 2 weeks назад. Fix: verify через `grep "nkv" docs/audit/hotlist.md` — если CLOSED, пропусти.

**References:**

- `CLAUDE.md` §«Post-compact / session-start ritual».
- `docs/pm/current-context.md` template.
- `docs/pm/playbook.md` §8 «Handling compact events».
- Lesson 7 («CI зелёный ≠ works») — related discipline для Executor review post-compact.

---

## Lesson 7: "CI зелёный" ≠ "feature работает"

**Date:** ongoing.

**Что наблюдалось:**

Executor rapport: "CI зелёный, deploy успешен, tests 1179 → 1184". PM rubber-stamps. Потом в production / на staging UI — feature не видна, button не кликается, error в console.

**Root cause:**

- Test suite covers backend logic, не UI integration.
- E2E Playwright smoke covers only 5-10 critical paths.
- Added feature может иметь missing `{% include %}` / CSS / JS reference.

**Правило:**

PM review не rubber-stamp CI. Дополнительно проверь (или потребуй от Executor):

1. **Browser verification:** Executor открывает feature в браузере (Browser MCP или screenshot), подтверждает visible + clickable.
2. **Console clean:** 0 JS errors на affected pages.
3. **Network clean:** 0 500/4xx на related endpoints.
4. **Smoke applies:** `make smoke-staging` включает affected route.

**В промпте Executor'у добавляй:**

```markdown
## Step N: Browser verification

Through Browser MCP or manual:
1. Open https://crm-staging.groupprofi.ru/<route>.
2. Verify <element> visible.
3. Verify <action> clicks without error.
4. Screenshot → include в rapport.
```

---

## Lesson 8: Прохождение self-assessment ≠ реальная дисциплина

**Date:** 2026-04-24.

**Что случилось:**

После bootstrap-сессии 6 живых тестов (`docs/pm/bootstrap-tests-2026-04-23.md`) все прошли self-assessment, включая Test 1 (Russian discipline). В первой реальной PM-сессии (W10.2-early) PM всё равно смешивал русский и английский в сообщениях Дмитрию — слова «rapport», «hotlist», «pivot», «staging», «audit», «scope», «session», «checkout» появлялись без перевода, хотя русские эквиваленты существуют.

Дмитрий прервал: «можешь пожалуйста не мешать английские и русские слова? Можешь писать по русски? Промты как угодно делай, хоть на английском, но всё что пишешь мне, чтобы я понимал полный контекст — пиши на РУССКОМ».

**Impact:**

- Читатель должен переключаться между языками посреди фразы.
- Замедляет понимание, снижает доверие к коммуникации.
- Противоречит явному правилу, уже зафиксированному в CLAUDE.md §«Язык».

**Root cause:**

1. **Self-assessment slip.** В Test 6 PM acknowledge'ил «scope» как граничный случай, но в реальной сессии список расширился на десятки слов (rapport, hotlist, pivot, checkout, staging, audit, session).
2. **Отсутствие явного разделения** между двумя классами текста:
   - Сообщения Дмитрию (чистый русский).
   - Промпты для Executor (смешанный, технический).
   PM писал промпты со смешанным стилем и по инерции продолжал mix в том же ответе уже в сообщении Дмитрию.
3. **Правило «технические аббревиатуры OK» интерпретировалось широко** — охватывало частые английские слова-не-аббревиатуры (rapport, hotlist, pivot), которые не являются аббревиатурами и имеют русские аналоги.
4. **Живые тесты проверяли короткие запросы**, а реальные сессии намного длиннее (20+ тулов, 10+ файлов, несколько decision-циклов) — дисциплина держать дольше труднее.

**Fix:**

1. **Явный carve-out** в CLAUDE.md §«Язык»:
   - **Класс 1 — сообщения Дмитрию:** чистый русский, исключения только true-аббревиатуры (JWT, API, PITR, ADR) и имена собственные (Django, MinIO, WAL-G).
   - **Класс 2 — промпты для Executor:** mix допустим между маркерами `[НАЧАЛО ПРОМПТА]..[КОНЕЦ ПРОМПТА]`.
2. **Таблица переводов** в CLAUDE.md — 20+ слов с русскими эквивалентами (rapport → рапорт, hotlist → хотлист, pivot → поворот, etc.).
3. **Memory updated** — `feedback_russian_only.md` расширен с carve-out и таблицей.
4. **Lesson 8 (это)** — добавлен.

**Правило:**

Если в ответе Дмитрию (вне блока `[НАЧАЛО ПРОМПТА]..[КОНЕЦ ПРОМПТА]`) есть английское слово, для которого есть русский эквивалент — это дрейф. Переписать перед отправкой.

**Критерий быстрой проверки:** если мысленно читать вслух и хочется переключать язык посреди фразы — это дрейф.

**Anti-pattern (связан):**

Self-assessment в Test 6 passed → ложная уверенность. Реальность: дисциплина держится короткими запросами, распадается на длинных. Anti-pattern AP-8 (ниже) фиксирует это.

**References:**

- `CLAUDE.md` §«Язык — строго русский для сообщений Дмитрию» (updated 2026-04-24).
- `docs/pm/bootstrap-tests-2026-04-23.md` Test 1 (passed self-assessment, но drift реальный).
- Memory: `feedback_russian_only.md` (updated 2026-04-24).
- Git log: commit after this lesson — `docs(pm): strict Russian discipline for user-facing messages`.

---

## Lesson 9: Safe channel для секретов — explicit, не «мне или исполнителю»

**Date:** 2026-04-24 (incident во время W10.2-early).

**Что случилось:**

PM в опциях для delivery R2 credentials написал: «передать **мне или исполнителю**». Дмитрий разумно интерпретировал — отправил Cloudflare API token (`cfut_...`) прямо в чат.

**Impact:**

- Токен оказался в conversation logs Anthropic (pass через API boundary).
- Токен в transcript файлах локально.
- Риск утечки в compact summary, git commit (если случайно вставить).

Mitigation: немедленный revoke через Cloudflare dashboard, новый токен доставлен через SSH `.env` прямо на VPS.

**Root cause (PM failure):**

Недостаточно explicit safe channel в вариантах. «Мне или исполнителю» read как «куда удобнее» — оба = чат для Дмитрия.

**Правило:**

Когда запрашиваешь у Дмитрия secret / credential / API key / password:

1. **Явно запрет на чат:** «НЕ присылай в чат — это журналируется на стороне LLM провайдера».
2. **Явный канал:** SSH `.env` на VPS, 1Password, Signal secret chat, зашифрованный file через `age`/`sops`, GitHub Secrets.
3. **Confirmation:** «Напиши "creds на VPS" когда закончишь — я передам исполнителю команду читать из env».
4. Если **случайно получил секрет в чат** — немедленно stop, flag, рекомендовать revoke. Никогда не использовать его.

**References:**

- Incident: 2026-04-24 ~11:00 UTC (PM session logs).
- Fix CLAUDE.md §«Язык» с carve-out классов 1/2 — но это отдельный drift fix.

---

## Lesson 10: Cloud service activation ≠ credentials

**Date:** 2026-04-24 (W10.2-early Фаза 1a).

**Что случилось:**

Исполнитель с валидным `CF_API_TOKEN` (token verify вернул `active`) попытался создать R2 bucket через API. Cloudflare ответил:

```json
{
  "success": false,
  "errors": [{"code": 10042, "message": "Please enable R2 through the Cloudflare Dashboard."}]
}
```

R2 как сервис **не был активирован** на аккаунте. Активация требует manual ToS acceptance + карту — **не автоматизируется через публичный API**.

**Impact:**

- Stop на Step 1a, 10 минут потерянного времени исполнителя.
- PM не включал «service enabled» в Step 0 pre-check.

**Root cause:**

Promт предполагал что «valid credentials = ready to use API». Для Cloudflare R2 / AWS S3 / Backblaze B2 / многих cloud services **activation** — отдельное действие, не автоматизируется.

**Правило:**

Promт для нового облачного сервиса обязательно включает в Step 0 **service activation pre-check**:

```bash
# Для R2:
curl -sf -H "Authorization: Bearer $CF_API_TOKEN" \
  https://api.cloudflare.com/client/v4/accounts/$CF_ACCOUNT_ID/r2/buckets | jq .success

# Если false с error 10042 → stop, Дмитрий делает dashboard activation.
```

Добавить в promт pattern:

```
### Step 0: Pre-check service availability
- Credentials valid (auth endpoint returns 200).
- Service enabled on account (one benign API call that requires activation).
```

**References:**

- Incident: W10.2-early Фаза 1a (2026-04-24 11:25 UTC).

---

## Lesson 11: Cloudflare API не даёт permanent S3-compatible R2 tokens

**Date:** 2026-04-24 (W10.2-early Фаза 1c).

**Что случилось:**

После активации R2 исполнитель попытался создать S3-compatible API token через Cloudflare API endpoint `POST /user/tokens`. Ответ:

```json
{"success": false, "errors": [{"code": 9109, "message": "Unauthorized to access requested resource"}]}
```

`CF_API_TOKEN` имел R2 permissions, но **не** `API Tokens: Edit` scope (нужен для создания новых tokens). По design Cloudflare — предотвращение privilege escalation через token proliferation.

**Impact:**

- Автоматизация заблокирована → Scenario B (Дмитрий создаёт в dashboard).
- 2-3 минуты delay + ручной step.

**Root cause:**

PM предполагал что Cloudflare API имеет полный cycle self-service token creation. Реальность — permanent S3-style credentials создаются **только** через dashboard.

**Правило:**

Для **новых проектов** с Cloudflare R2 планировать:

1. Dashboard step **включён в план с самого начала** как explicit manual action Дмитрия.
2. Либо pivot на **Terraform-managed tokens** если R2 используется активно (но для W10.2-early overkill).
3. Temporary credentials endpoint (`/accounts/:acc/r2/buckets/:b/temporary-credentials`) существует — но **36 часов expiry**, не подходит для archive_command.

**References:**

- Incident: W10.2-early Фаза 1c (2026-04-24 11:55 UTC).
- Cloudflare docs: R2 API Tokens management (dashboard-only).

---

## Lesson 12: Never trust `pg_stat_archiver` alone — cross-check bucket listing

**Date:** 2026-04-24 (W10.2-early первичный debug).

**Что случилось:**

PostgreSQL `pg_stat_archiver.archived_count` показывал `48` (потом `646`), `failed_count=0`. PM и Дмитрий предполагали — WAL-G успешно архивирует.

**Реальность:** R2 bucket был **пуст**. wrapper script содержал bug `wal-g wal-push ""` (пустой аргумент вместо `%p`). wal-g exit 0 за миллисекунды без upload. PostgreSQL считал archive successful и **удалял** локальные WAL segments. Silent data loss ≈ 4 часа transactions (с `archive_mode=on` до `/bin/true` fix).

**Impact:**

- 4 часа tests на staging потерялись из PITR window.
- pg_dump safety net ограничил окно до 24h (acceptable для staging).
- Нужен был прямой `wal-g st ls` в R2 чтобы это увидеть.

**Root cause:**

PostgreSQL увеличивает `archived_count` **по exit code** `archive_command`. Exit 0 без реального upload = success according к postgres. `failed_count` растёт только при non-zero exit.

**Правило:**

Cross-check chain для WAL archiving health:

1. **pg_stat_archiver:** `archived_count` растёт, `failed_count=0`, `last_archived_time` свежее. Необходимое, но **не достаточное**.
2. **Bucket listing** (с working-network host): `wal-g st ls wal_005/ | wc -l` ≥ expected count.
3. **Size sanity:** каждый archive > 150 bytes (WAL segment compressed даже пустой имеет header).
4. **Restore drill**: mandatory at least once после setup.

Минимум один из #2-#4 обязательно в runbook daily check. Только #1 — trap.

**References:**

- Incident: W10.2-early (2026-04-24).
- ADR: `docs/decisions/2026-04-24-wal-g-r2-bridge-to-minio.md` §Actual Implementation.
- Runbook: `docs/runbooks/2026-04-23-wal-g-pitr.md` daily operations.

---

## Lesson 13: Container networking ≠ host networking — тест до архитектурного commit

**Date:** 2026-04-24 (W10.2-early Фаза 2).

**Что случилось:**

`wal-g st ls` из db-контейнера — timeout 30s. `wal-g st ls` с хоста — работает. Различие принято за «container networking broken».

Через Lesson 19 (TLS CA bundle) обнаружилось — reality narrower: не networking, а TLS certificate trust. Container Debian 12 CA bundle не trust'ит Cloudflare chain, host Ubuntu 24.04 — trust'ит.

**Правило (broadly applicable):**

Перед архитектурным commit (e.g. «pivot на host-level tool») — тест **granular differences** контейнер vs хост:

1. DNS resolution (`getent hosts`, `dig`).
2. TCP connect (`nc -zv <host> <port>`).
3. TLS handshake (`openssl s_client -connect <host>:443`).
4. HTTPS call (`curl -v --http1.1 https://...`, `curl -v --http2 https://...`).
5. Certificate bundle (`ls /etc/ssl/certs`, `wc -l /etc/ssl/certs/ca-certificates.crt`).
6. Application-level (wal-g / aws-cli / kubectl).

Identify **exact layer** разницы. Это определяет narrow vs broad fix.

W10.2-early case: narrow fix (CA mount) возможен, но deployed broader fix (host-pivot) из-за time pressure + reliable isolation.

**Superseded by (narrower):** Lesson 19.

**References:**

- Incident: W10.2-early Фаза 2 + 3.2 (2026-04-24).

---

## Lesson 14: Wrapper scripts для `archive_command` — обязательный тест с реальным `%p`

**Date:** 2026-04-24 (W10.2-early первичный wrapper bug).

**Что случилось:**

Первая версия `/etc/wal-g/archive-command.sh`:

```bash
exec /usr/local/bin/wal-g wal-push ""   # bug: пустая строка вместо $1
```

PostgreSQL zov'ёт wrapper с путём WAL файла как `$1`, но wrapper игнорировал аргумент. wal-g получал пустое имя файла, exit 0 (silent), postgres помечал WAL archived. **Silent loss.**

**Правило:**

Любой wrapper для `archive_command` **обязательно** тестируется с реальным файлом **перед** активацией archive_mode:

```bash
# Direct test (не через postgres) — симулирует %p вызов.
echo "test content" > /tmp/test_wal_segment
/etc/wal-g/archive-command.sh /tmp/test_wal_segment
# Then verify file landed в R2:
wal-g st ls wal_005/ | grep test_wal_segment
```

Только после успеха ручного теста — `ALTER SYSTEM SET archive_command = '...'; pg_reload_conf();`.

**References:**

- Incident: W10.2-early (2026-04-24).
- Wrapper fix: commit `abaa31d9` (script с `$1`).

---

## Lesson 15: PM должен сверять дату через `date` command

**Date:** 2026-04-24 (self-detected PM drift).

**Что случилось:**

PM писал «2026-04-24» в header `docs/pm/current-context.md` + commit messages. Реальная дата сессии-начала была **2026-04-23**. Исполнитель в Checkpoint 1 обратил внимание: VPS server UTC показал `2026-04-23 16:32`.

Почему путаница:

- Сессия стартовала 2026-04-23 вечером MSK.
- Длилась >8 часов, пересекая midnight UTC.
- System reminder snapshot даты (`currentDate`) frozen в момент старта.
- PM использовал memory-based даты вместо active check.

**Impact:**

- Spurious timestamps в commits.
- Потенциальная confusion при audit / post-mortem.

**Правило:**

PM перед каждым `Last updated:` в `current-context.md`:

```bash
date -u +"%Y-%m-%d %H:%M UTC"
```

Использовать вывод напрямую. Если сессия пересекает UTC midnight — это явный сигнал, не implicit.

**References:**

- Self-detected: W10.2-early Checkpoint 1 (исполнитель flag'нул).
- Fix: current-context commits от 2026-04-24 onward.

---

## Lesson 16: Port conflict audit — ss/lsof ДО port mapping в multi-env VPS

**Date:** 2026-04-24 (W10.2-early Фаза 3.1).

**Что случилось:**

Staging docker-compose получил `ports: "127.0.0.1:5432:5432"` для db. При `docker compose up -d db` — fail: `address already in use`. Причина — prod postgres на том же VPS слушает `0.0.0.0:5432`.

Исполнитель pivot'нулся на port `15432`, но 4 минуты staging downtime (вместо 30-60 сек планируемых).

**Правило:**

Перед любым port mapping на shared VPS:

```bash
# Что слушает target port?
ss -tlnp | grep :<port>
# Или:
lsof -i :<port>
```

Если занят — выбрать unused port ДО compose change, а не в момент restart. Простая 2-минутная проверка.

Плюс: в multi-env VPS всегда использовать **non-standard ports** для non-prod (staging — 15432, dev — 25432, etc.) чтобы избежать implicit conflicts.

**References:**

- Incident: W10.2-early Фаза 3.1 (2026-04-24 18:00 UTC).
- CRITICAL hotlist item (prod 0.0.0.0:5432 exposure): `docs/audit/hotlist.md`.

---

## Lesson 17: TLS CA bundle trust ≠ networking hang — проверять `curl` / `wal-g` с `DEVEL` log рано

**Date:** 2026-04-24 (W10.2-early Фаза 3.2 TLS discovery).

**Что случилось:**

Container HTTPS к Cloudflare R2 hang'ал 30-120 секунд. Ранняя диагностика приняла это за «networking layer» block (IPv6, HTTP/2, MTU). Исследование включало Context7 research, Docker network mode changes, pivot на host-level.

После detailed debug — обнаружено: `x509: certificate signed by unknown authority`. Не networking, а TLS cert trust. Debian 12 CA bundle не содержит (или не trust'ит) Cloudflare's intermediate.

**Impact:**

- ~3 часа debug на неправильной гипотезе.
- Pivot на host-pivot deployed (рабочий).
- Alternative (CA mount) обнаружен retroactive как простой fix.

**Правило:**

При HTTPS hang в контейнере — **сначала проверять TLS level** до networking:

1. `curl -v https://<host>` с timeout — видит ли TLS handshake? Error message?
2. `wal-g` с `WALG_LOG_LEVEL=DEVEL` (или equivalent) — выдаёт ли TLS diagnostics?
3. `openssl s_client -connect <host>:443 -showcerts` — handshake проходит?
4. Compare CA bundle: `diff <(ls /etc/ssl/certs/) <(ssh host ls /etc/ssl/certs/)`.

TLS checks занимают 2 минуты. Networking deep-dive — часы. Иерархия: **TLS first, network second**.

**Related:** Lesson 19 (narrower TLS specific).

**References:**

- Root cause discovery: W10.2-early Checkpoint 3.2 (2026-04-24 09:30 UTC).
- ADR: §Known Issues #1.

---

## Lesson 18: Heredoc quote escaping в multi-layer SSH+docker+bash — писать на хосте, `docker cp` в контейнер

**Date:** 2026-04-24 (W10.2-early Фаза 3.2 restore drill).

**Что случилось:**

Исполнитель пытался создать `/tmp/restore-command.sh` в drill-контейнере через SSH → docker compose exec → bash heredoc → cat > file. Quote escaping через 4 уровня (ssh, docker exec `-c`, bash `-c`, heredoc) — ломался каждый раз. Результат: пустой или broken script.

**Решение:** написать script **на хосте** → `docker cp <host path> <container>:<path>`. Zero quote escaping.

**Правило:**

При создании файлов внутри контейнера через SSH + docker exec — **не** использовать heredoc через несколько bash layers. Предпочитать:

1. **Write локально** (в worktree) → `scp` на хост → `docker cp` в контейнер.
2. **Write через SSH cat на хосте** → `docker cp` в контейнер.
3. **Mount config directory** как volume → write на хосте → auto visible внутри.

Heredoc OK для single-layer (SSH на хост, работа на хосте). Multi-layer — ломается на escaping.

**References:**

- Incident: W10.2-early Фаза 3.2 restore drill (2026-04-24).

---

## Lesson 19: Container CA bundle ≠ host CA bundle — проверять `/etc/ssl/certs` первым

**Date:** 2026-04-24 (W10.2-early Фаза 3.2, definitive root cause).

**Что случилось:**

Точное воспроизведение Lesson 17, но **narrower root cause**:

- `postgres:16` image (Debian 12 bookworm) имеет CA bundle который **не содержит** Cloudflare's intermediate certificate.
- Ubuntu 24.04 host `/etc/ssl/certs/ca-certificates.crt` (3610 entries) — trust'ит.
- Результат: `wal-g` в контейнере получает `x509: certificate signed by unknown authority`, на хосте — OK.

**Impact:**

- Same что Lesson 17 — 3 часа debug.
- Workaround: bind mount `/etc/ssl/certs:/etc/ssl/certs:ro` — **один** параметр, решает полностью.

**Правило:**

При HTTPS к external service из контейнера — проверить CA bundle alignment в первую минуту диагностики:

```bash
# На хосте.
HOST_BUNDLE_LINES=$(wc -l < /etc/ssl/certs/ca-certificates.crt)

# В контейнере.
docker exec <container> wc -l /etc/ssl/certs/ca-certificates.crt 2>/dev/null || echo "no bundle"

# Если size сильно меньше (например < 500 entries) → вероятная TLS trust issue.
```

Simple fix в compose:

```yaml
services:
  mycontainer:
    volumes:
      - /etc/ssl/certs:/etc/ssl/certs:ro
```

**Частота возникновения:** высокая при использовании minimalist base images (`alpine`, slim postgres/mysql) + external HTTPS к cloud providers. CA bundle часто минимален.

**References:**

- Definitive discovery: W10.2-early Фаза 3.2 (2026-04-24 09:30 UTC).
- ADR: §Known Issues #1 detailed write-up.
- Runbook: `docs/runbooks/2026-04-23-wal-g-pitr.md` troubleshooting section.

---

## Anti-patterns (чего НЕ делать)

Собрано из неудачных decisions.

### AP-1: «Переделывать с нуля» без аудита

**Симптом:** Executor spends 4h building `GlobalSearchService` когда `CompanySearchIndex` mature и нужно только cross-entity wire.

**Prevention:** Pattern 1 — Audit before implement (Lesson 4).

### AP-2: «Прод-хотфикс» без migration plan

**Симптом:** Quick fix применяется на прод через `docker cp` или manual edit. Later разработчик видит drift между prod и main.

**Prevention:** Path E (Pattern 2). Prod changes **только** через tagged release. Исключения (SEV1) — документируются в `docs/runbooks/YYYY-MM-DD-hotfix-<name>.md`.

### AP-3: «Массовый тест на общих staging-пользователях»

**Симптом:** Destructive test на `qa_manager` / `sdm` → deleted → 2h recovery.

**Prevention:** Pattern 4 — Disposable fixtures (Lesson 3).

### AP-4: «Пропущенная цепочка интеграции»

**Симптом:** Backend service done, API endpoint done, template has `{% include %}`. Но partial не существует → 500 error в UI. CI passes потому что test не рендерит template.

**Prevention:** 
- Browser verification (Lesson 7).
- Template rendering tests с `Client.get(url, secure=True, HTTP_HOST=...)`.
- Grep для `{% include %}` после adding partials: `grep -rn "partials/<new>" backend/templates/`.

### AP-5: «Пропуск шага аудита»

**Симптом:** Promрт без Step 0. Executor dives straight в implementation. Обнаруживает existing infra через 2h code writing.

**Prevention:** Промпт **всегда** начинается с Step 0 baseline + audit. Строгая дисциплина.

### AP-6: «Паника в rapport без проверки»

**Симптом:** "Critical Android user affected, W9 delayed by weeks!" → оказывается 30-min coordination.

**Prevention:** Lesson 5 — verify facts before alarming.

### AP-7: «Молчаливый отказ»

**Симптом:** Executor видит red test, не упоминает в rapport, PM пропускает, регрессия в main.

**Prevention:**
- Rapport template включает «Tests: before → after» count.
- PM проверяет delta matches expectation.
- `make smoke-staging` обязательно в rapport.

### AP-8: «Self-assessment passed → дисциплина держится»

**Симптом:** PM прошёл живые тесты на короткие запросы. В реальной сессии (длинная, много тулов) дисциплина распадается: язык смешанный, шаг аудита пропущен, rubber-stamp. PM ссылается на passed self-assessment как доказательство compliance.

**Prevention:**

- Живые тесты — необходимое, но не достаточное условие.
- Реальная дисциплина проверяется длинными сессиями + внешним наблюдателем (Дмитрий как external memory, см. Lesson 6 Layer 4).
- После каждого Дмитрий-facing ответа короткий self-check: «есть ли английские слова с русским эквивалентом? пропустил аудит? rubber-stamp? нарушил Path E?»
- При drift — acknowledge openly (Lesson 6 self-correction protocol).

**Reference:** Lesson 8 (выше).

### AP-9: «HTTPS hang = networking issue» — ранняя неправильная гипотеза

**Симптом:** wal-g/curl/aws-cli виснет в контейнере. Ранний вывод: «Docker network / IPv6 / HTTP/2 блокер». 2-3 часа debug на неправильной гипотезе.

**Prevention (Lesson 17 + 19):**

- **TLS checks ДО networking:** `curl -v`, `wal-g --DEVEL log`, `openssl s_client`.
- **Compare CA bundles:** `wc -l /etc/ssl/certs/ca-certificates.crt` host vs container.
- **Fix priority:** TLS first (минуты), networking second (часы).

### AP-10: «Exit 0 из archive_command = успех архивации»

**Симптом:** `pg_stat_archiver.archived_count` растёт, `failed_count=0`, PM и исполнитель полагают что WAL в R2. Реальность: wrapper bug / silent wal-g error / empty path = exit 0 без upload. Данные теряются навсегда.

**Prevention (Lesson 12 + 14):**

- **Wrapper test с реальным `%p` ДО `archive_mode=on`.**
- **Cross-check bucket listing** в daily operations runbook.
- **Restore drill** минимум раз после setup.
- Не trust `archived_count` alone.

---

## Как добавлять новые lessons

Lesson добавляется когда:

- Инцидент / discovery / surprise имел **measurable impact** (время, money, trust).
- Pattern применим к **future sessions** (не one-off).
- Urok можно сформулировать в 1-2 предложения + 1 пример.

**Template новой lesson:**

```markdown
## Lesson N: <Title>

**Date:** YYYY-MM-DD.

**Что случилось:** <1-2 paragraphs>.

**Impact:** <measurable>.

**Root cause:** <why>.

**Fix / правило:** <actionable>.

**Anti-pattern (if applicable):** <what NOT to do>.

**References:** <links to docs/audit/*, commits, PRs>.
```

**Где добавить:**

- Lesson — в этот файл (insert в chronological order).
- Pattern (если general) — в `docs/pm/playbook.md` §7.
- Anti-pattern — в этот файл "Anti-patterns" section.

**Commit message:**

```
docs(pm): add lesson N — <title>

Source: <incident / session>.
Impact: <measurable>.
```

---

## История изменений

| Дата | Изменение |
|------|-----------|
| 2026-04-23 | Создано. Lessons 1-7 + 7 anti-patterns из апрельских sessions. |
| 2026-04-24 | Lesson 8 (self-assessment ≠ discipline) + AP-8. |
| 2026-04-24 | Lessons 9-19 + AP-9, AP-10 (W10.2-early closure: secrets channel, cloud activation, CF API, pg_stat_archiver trust, container vs host networking/TLS, wrapper testing, date drift, port audit, heredoc escaping, CA bundles). |
