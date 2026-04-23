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

## Anti-patterns (чего НЕ делать)

Собрано из неудачных decisions.

### AP-1: "Rebuild from scratch" без audit

**Симптом:** Executor spends 4h building `GlobalSearchService` когда `CompanySearchIndex` mature и нужно только cross-entity wire.

**Prevention:** Pattern 1 — Audit before implement (Lesson 4).

### AP-2: "Prod hotfix" без migration plan

**Симптом:** Quick fix применяется на прод через `docker cp` или manual edit. Later разработчик видит drift между prod и main.

**Prevention:** Path E (Pattern 2). Prod changes **только** через tagged release. Исключения (SEV1) — документируются в `docs/runbooks/YYYY-MM-DD-hotfix-<name>.md`.

### AP-3: "Mass test на shared staging"

**Симптом:** Destructive test на `qa_manager` / `sdm` → deleted → 2h recovery.

**Prevention:** Pattern 4 — Disposable fixtures (Lesson 3).

### AP-4: "Missed integration chain"

**Симптом:** Backend service done, API endpoint done, template has `{% include %}`. Но partial не существует → 500 error в UI. CI passes потому что test не рендерит template.

**Prevention:** 
- Browser verification (Lesson 7).
- Template rendering tests с `Client.get(url, secure=True, HTTP_HOST=...)`.
- Grep для `{% include %}` после adding partials: `grep -rn "partials/<new>" backend/templates/`.

### AP-5: "Skip audit step"

**Симптом:** Promрт без Step 0. Executor dives straight в implementation. Обнаруживает existing infra через 2h code writing.

**Prevention:** Промпт **всегда** начинается с Step 0 baseline + audit. Строгая дисциплина.

### AP-6: "Panic rapport без verification"

**Симптом:** "Critical Android user affected, W9 delayed by weeks!" → оказывается 30-min coordination.

**Prevention:** Lesson 5 — verify facts before alarming.

### AP-7: "Silent failure"

**Симптом:** Executor видит red test, не упоминает в rapport, PM пропускает, regression в main.

**Prevention:** 
- Rapport template включает "Tests: before → after" count.
- PM проверяет delta matches expectation.
- `make smoke-staging` **обязательно** в rapport.

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
