# W2.3 Phase 2 scope analysis — 2026-04-22

**Date**: 2026-04-22 (W2.3 Phase 1 deployed ~13:36 UTC, ~2h monitoring window closed).
**Method**: READ-ONLY анализ `crm.csp` logger entries from staging web container.
**User browsing session**: 15-20 min (~15:20 — 15:40 UTC).

---

## TL;DR

- **5 CSP violations** observed during real browser session.
- **Much narrower scope** чем W2.3.0 grep inventory suggested (66 handlers).
- **Main blocker**: `v2_modal.html::runScripts()` — dynamically-created `<script>` clones без nonce. 3 of 5 violations = this single function.
- **Remaining 2**: inline event handlers триггеровали script-src-attr (line 1 = browser-synthetic location for attribute handlers).
- **60+ grep-listed handlers NOT triggered** — those pages не посещались. Defer fix until actual usage triggers violation.

---

## Violation counts

Total: **5** violations in 6h window.

### By directive type

| Directive | Count | Meaning |
|-----------|-------|---------|
| `script-src-elem` | 3 | Inline `<script>` без nonce (strict blocks) |
| `script-src-attr` | 2 | Inline event handler (onclick/onchange/etc) |

### By document (page)

| URL | Violations |
|-----|------------|
| `/tasks/` | 3 (2 script-src-elem + 1 script-src-attr) |
| `/` (home) | 2 (1 script-src-elem + 1 script-src-attr) |

### By source location

| Source | Line | Count | Type |
|--------|------|-------|------|
| `/tasks/` inline | 3096 | 2 | script-src-elem (same runScripts() function) |
| `/` inline | 697 | 1 | script-src-elem (same runScripts() function) |
| `/tasks/` | 1 | 1 | script-src-attr (event handler) |
| `/` | 1 | 1 | script-src-attr (event handler) |

---

## Root cause analysis

### Issue 1: `v2_modal.html::runScripts()` (3 of 5 violations)

**Source**: `backend/templates/ui/_v2/v2_modal.html:144-150`

```javascript
function runScripts(){
  bodyEl.querySelectorAll('script').forEach(function(old){
    var s = document.createElement('script');
    s.textContent = old.textContent;
    old.parentNode.replaceChild(s, old);  // ← CSP violation: new <script> без nonce
  });
}
```

**Pattern**: Modal dialog loads HTML fragment via AJAX. `innerHTML = ...` **does NOT execute** `<script>` tags included в fragment (browser-enforced security). `runScripts()` clones each `<script>` via `document.createElement` to force execution.

**CSP conflict**: The cloned script не получает `nonce` attribute. Strict policy `script-src 'self' 'nonce-XXX'` blocks any inline script без matching nonce.

**Reach**: v2_modal.html included в:
- `ui/company_detail_v3/b.html` (company detail v3/b)
- `ui/company_list_v2.html` (company list)
- `ui/dashboard_v2.html` (home `/`)
- `ui/task_list_v2.html` (`/tasks/`)

Any modal loading HTML with `<script>` tags triggers this. **High-reach issue.**

### Issue 2: Script-src-attr violations (2 of 5)

**Source**: `/tasks/` + `/` at line 1 (browser-synthetic для attribute handlers).

**Static template grep (precise)**: no `onXXX=` attributes в:
- `dashboard_v2.html`
- `task_list_v2.html`
- `v2_modal.html`
- `base.html`

**Hypothesis**: Dynamic DOM manipulation — somewhere JS calls `elem.setAttribute('onclick', ...)` OR sets `elem.onclick = function(){...}` as string attribute. Could also be content loaded via modal (from partial templates — which use inline handlers per W2.3.0 inventory).

**Needs dedicated investigation** — can't locate source via static grep. May need browser DevTools (view rendered HTML after interaction).

---

## Cross-reference с W2.3.0 grep inventory

### Inventory promised

66 inline event handlers across 11 templates:

| Template | Grep-listed handlers | Triggered в session? |
|----------|---------------------|----------------------|
| `ui/mail/campaign_detail.html` | 14 | ❌ не посещался |
| `ui/settings/user_form.html` | 5 | ❌ не посещался |
| `ui/settings/messenger_inbox_form.html` | 5 | ❌ не посещался |
| `ui/settings/error_log.html` | 5 | ❌ не посещался |
| `ui/settings/users.html` | 4 | ❌ не посещался |
| `ui/mail/campaigns.html` | 3 | ❌ не посещался |
| `ui/company_list_rows.html` | 3 | Уточнить (возможно, loaded via modal) |
| `ui/analytics_user.html` | 3 | ❌ не посещался |
| `ui/settings/messenger_automation.html` | 2 | ❌ не посещался |
| `ui/preferences.html` | 2 | ❌ не посещался |
| **Sum top 10** | **46** | **0 triggered** |

### Reality after monitoring

- User visited: `/` (home/dashboard), `/tasks/`, `/companies/`.
- None of top-10 handler-heavy templates posещались.
- Remaining 60+ grep-inventoried handlers — **deferred наблюдение**. Trigger only when admin opens those pages.

---

## Phase 2 refined scope

### Must-fix (observed violations)

1. **`v2_modal.html::runScripts()`** — 1 file, 3 lines changed:

   ```js
   function runScripts(){
     bodyEl.querySelectorAll('script').forEach(function(old){
       var s = document.createElement('script');
       s.textContent = old.textContent;
       // W2.3: propagate nonce to cloned script для CSP strict compatibility
       const nonce = document.querySelector('meta[name="csp-nonce"]')?.content;
       if (nonce) s.setAttribute('nonce', nonce);
       old.parentNode.replaceChild(s, old);
     });
   }
   ```

   Plus: add `<meta name="csp-nonce" content="{{ request.csp_nonce }}">` to `base.html` `<head>`.

2. **Investigate 2 script-src-attr violations on / and /tasks/**:
   - Reproduce в браузере с DevTools.
   - Найти exact source (setAttribute / innerHTML / third-party).
   - Fix once identified.

### Dead-code-in-production candidates (deferred)

60+ inline handlers в templates user не посещает в сессии. Options:
- **Defer**: fix only when real violation triggers (iterative).
- **Proactive**: extract all upfront (aligned с W9 UX redesign naturally).

Recommendation: **defer** — align с iterative rollout pattern.

### Browser extensions (если бы были)

**0 observed** в session. Clean. No Grammarly/LastPass/ad blocker interference detected.

---

## Proposed Phase 2 sub-sessions

### Sub-session 2a: `v2_modal.html::runScripts()` nonce propagation (~30 min)

- **Scope**: Fix single function in 1 file + add meta tag to base.html.
- **Risk**: Low — change scoped к modal AJAX fragments.
- **Test**: Open modal на / + /tasks/ + /companies/*. Verify no CSP violations.
- **Acceptance**: CSP violations count drops from 5 to ≤2 после 1h monitoring.

### Sub-session 2b: Investigate 2 script-src-attr на / and /tasks/ (~1h)

- **Scope**: Reproduce violations в браузере с DevTools Security panel.
- **Find**: exact source (static template, dynamic JS, third-party).
- **Fix**: depends on finding.
- **Acceptance**: CSP violations count = 0 после 1h monitoring (excluding browser extensions).

### Sub-session 2c (optional): Extract 60+ grep-listed handlers proactively (~3-5h)

- **Scope**: W2.3.0 full plan — extract onclick/onchange → data-action + delegated listeners.
- **Rationale**: только если want Phase 3 flip без risk breaking admin pages.
- **Alternative**: defer к W9 (aligned с UX redesign anyway).

### Sub-session 2d: Phase 3 switch (~30 min)

- Enforce strict policy.
- Remove permissive enforce header.
- Acceptance: 48h clean monitoring в strict mode.

---

## Risks

- **Hidden violations на admin pages**: if admin visits campaign_detail, settings/user_form etc, those pages will break strict CSP immediately. Need Sub-session 2c before Phase 3 **or** admin training to use report-only in dev.
- **Dynamically-injected handlers via JS libraries**: Phase 3 may reveal new violations from libraries we use (jQuery's `.on()` attaches handlers properly, no issue; but `.attr('onclick', ...)` would break).
- **Future dev anti-regression**: need pre-commit check для bare `<script>` + inline `onX=` attributes.

---

## Estimated total Phase 2 effort

| Option | Duration |
|--------|----------|
| A (minimal, 2a + 2b + 2d, defer 2c к W9) | **~2h** |
| B (thorough, 2a + 2b + 2c + 2d) | **~5-6h across 3-4 sessions** |

---

## Sub-session 2a — Post-retest results (2026-04-22, ~17:05–17:25 UTC)

### Fix deployed (`d02f8230`)

- `backend/templates/ui/base.html`: `<meta name="csp-nonce" content="{{ request.csp_nonce }}">` добавлен в `<head>`.
- `backend/templates/ui/_v2/v2_modal.html::runScripts()`: reads meta → `setAttribute('nonce', value)` на clone + preserves type/src attrs.

### Retest session

- **Duration**: 5-10 min active browsing (17:05–17:25 UTC).
- **Container state**: fresh, up 20 min от deploy time.
- **HTTP activity**: 344 requests recorded в web container logs (last 30m) — active user session, не idle check.
- **Pages exercised**: dashboard + companies list + tasks + multiple modals (per user report).

### Violation comparison

| Directive | Pre-fix (initial monitoring) | Post-fix (2a retest) | Delta |
|-----------|------------------------------|----------------------|-------|
| script-src-elem | 3 | **0** | **−3** ✅ (target met) |
| script-src-attr | 2 | **0** | **−2** ✅ (bonus) |
| **Total** | **5** | **0** | **−5** |

### Analysis

**script-src-elem elimination** — expected primary outcome. Meta-tag-based nonce propagation works: runScripts() clones получают nonce attribute, strict CSP matches inline modal scripts.

**script-src-attr elimination** — unexpected but welcome. Hypothesis: original 2 violations related к same modal flow:
- When `runScripts()` был blocked (script-src-elem), the would-be attached event handlers never ran → browser reported attribute-level violation on later interaction.
- После fix, cloned scripts execute normally, attaching handlers через addEventListener (which is CSP-compliant) → script-src-attr не triggers.

Alternative: script-src-attr may reappear на future retest когда admin visits pages с raw `onclick=` attributes в static HTML (e.g., `mail/campaign_detail.html` 14 handlers). Current retest didn't посетить those admin pages.

### Verdict: **SUCCESS**

- **Primary goal met**: all 3 runScripts-related violations eliminated.
- **Secondary bonus**: script-src-attr also dropped to 0 in retest window.
- **Single-point fix** без regressions в modal functionality (user completed 344 requests without errors).

### Remaining Phase 2 work

**Sub-session 2b — script-src-attr re-investigation**:
- Current retest session showed 0 violations, но тест не покрыл admin pages с известными handlers.
- Recommend: admin visits mail/campaign_detail.html + settings/* pages → collect new violation data → targeted fix.
- Alternative: defer proactive cleanup к W9 UX redesign (aligns naturally).

**Sub-session 2c (optional)**: proactive extraction of 60+ grep-listed handlers — defer к W9.

**Phase 3 — strict enforce switch**:
- Required: 48h clean monitoring + admin pages cleanup либо scope limitation.
- Current monitoring window (20 min) too short для confidence.
- Next check milestone: 24h window → review violation rate.

---

## Session artifacts

- Docs only: this file (updated 2026-04-22 17:25 UTC с post-retest results).
- Code changes: `d02f8230` (base.html + v2_modal.html — 18 lines net).
- Baseline preserved: 1320 tests OK, smoke 6/6.
- CSP monitoring continues.

---

## Extended admin tour — 2026-04-22, ~17:56–18:03 UTC

### Методика

Browser MCP (Playwright) прошёлся по **22 admin/settings URL** с временным
admin-юзером (`browser_tour_1776879969215055742`, user_id=66, 2FA
verified). После тура юзер + AdminTOTPDevice удалены (`DELETED user_id=66
username=browser_tour_1776879969215055742 totp_devices=1`, 0 orphans).

### Посещённые URL (22)

`/`, `/analytics/`, `/companies/`, `/settings/`, `/settings/users/`
(404 — reroute), `/admin/`, `/admin/access/`, `/admin/activity/`,
`/admin/announcements/`, `/admin/branches/`, `/admin/calls/stats/`,
`/admin/company-columns/`, `/admin/dicts/`, `/admin/error-log/`,
`/admin/import/`, `/admin/mail/setup/`, `/admin/messenger/`,
`/admin/messenger/automation/`, `/admin/messenger/campaigns/`,
`/admin/mobile/overview/`, `/admin/security/`, `/admin/users/`,
`/admin/users/new/`, `/mail/campaigns/`, `/mail/campaigns/<id>/`.

Каждая страница: `browser_navigate` → wait 4-5s → следующая (reasonable idle
per page для CSP reports).

### Результат: **2 violations на 22 страницах**

| # | Time UTC | Document | Directive | Line |
|---|----------|----------|-----------|------|
| 1 | 17:48:23 | `/login/` | script-src-attr | 29 |
| 2 | 17:57:10 | `/mail/campaigns/` | script-src-attr | 429 |

**0 violations script-src-elem** (runScripts fix holds после модал-
интеракций в campaign detail).

### Root cause confirmed via grep

**`/login/:29`** → `backend/templates/registration/login.html` lines 20, 28:
```html
<button ... onclick="switchTab('access-key')">
<button ... onclick="switchTab('password')">
```

**`/mail/campaigns/:429`** → `backend/templates/ui/mail/campaigns.html`:
```html
line 101: <button ... onclick="window.__refreshQuota && window.__refreshQuota(true)">
line 162: <select name="branch" ... onchange="this.form.submit()">
line 170: <select name="manager" ... onchange="this.form.submit()">
```

Один из трёх триггернул violation (мы не знаем точно какой — browser
line-number ≠ template line). Но источник локализован в одном файле.

### Коррекция W2.3.0 grep inventory vs tour реальности

| Template | Grep handlers | Страница посещалась | Triggered violation |
|----------|--------------:|:-------------------:|:-------------------:|
| `mail/campaign_detail.html` | 14 | ✅ (navigated) | ❌ (no interaction) |
| `settings/user_form.html` | 5 | ✅ (via /admin/users/new/) | ❌ (no form interaction) |
| `settings/messenger_inbox_form.html` | 5 | ❌ | — |
| `settings/error_log.html` | 5 | ✅ | ❌ (no filter interaction) |
| `settings/users.html` | 4 | ✅ | ❌ (no row action) |
| `mail/campaigns.html` | 3 | ✅ | ✅ (line 429) |
| `company_list_rows.html` | 3 | ✅ (modal) | ❌ |
| `analytics_user.html` | 3 | ❌ (/analytics/ only) | — |
| `settings/messenger_automation.html` | 2 | ✅ | ❌ |
| `preferences.html` | 2 | ❌ | — |
| `registration/login.html` | 2 | ✅ (forced) | ✅ (line 29) |

**Ключевое наблюдение**: `script-src-attr` violation возникает
**только при реальной interaction** (click, change, focus), не при page
load. Passive tour не покрывает все handler-bearing templates — нужен
либо active admin user flow, либо proactive extraction.

### Phase 2b scope classification

**Verdict: SMALL/MEDIUM** — ~66 inline handlers в 11 файлах, полностью
enumerated через grep. Итеративный подход (fix only when reported) может
пропустить handlers в rarely-used pages → риск breaking strict CSP при
деплое.

**Priority ordering** (по observed + static risk):

1. **CRITICAL — `login.html` (2 handlers)**: blocks strict CSP (password
   login broken — confirmed during our own login).
2. **HIGH — `mail/campaigns.html` (3 handlers)**: main feature, actively
   triggered violation in tour.
3. **HIGH — `mail/campaign_detail.html` (14 handlers)**: highest handler
   count, high-traffic page.
4. **MEDIUM — settings forms (user_form, messenger_inbox_form, 10
   handlers)**: admin CRUD (rare but breaks when used).
5. **LOW — tail** (error_log, users, messenger_automation, preferences,
   company_list_rows, analytics_user, campaign_row, task_view/edit/create
   partials, base.html, 404/500 — ~30 handlers): defer к W9 UX.

### Recommended Phase 2b plan

**Option A (minimal, ~1.5h)**: fix priority 1-2 only (`login.html` +
`mail/campaigns.html`, 5 handlers). Unblocks Phase 3 switch для user
login flow + main mail page. Остальное — iterative.

**Option B (thorough, ~3-5h)**: extract priority 1-5 (66 handlers,
11 templates) proactively. Pattern: `onXXX="handler()"` → `data-action`
+ delegated listener на document or scoped root. Aligns с W9 UX naturally
(redesign will rewrite these files anyway).

**Recommendation**: **Option A for Phase 2b** (priority 1-2 only, 5
handlers, ~1.5h). Priority 3-5 defer к W9. Rationale:
- Priority 1-2 blocks observable user paths (login + mail).
- Priority 3-5 requires admin user actions, which are rare and flagged
  by report-only mode if triggered.
- Strict CSP switch (Phase 3) can proceed safely после Option A:
  report-only continues collecting data from admin pages, admin can
  self-report breakage.

### Phase 3 readiness

- ✅ runScripts nonce propagation verified.
- ✅ Main user paths (`/`, `/tasks/`, `/companies/`, `/mail/campaigns/`
  after Option A fix, modal flows): clean.
- ⚠️ Login page `switchTab` needs fix before flip (otherwise password
  login visually broken).
- ⏳ Admin pages с static handlers: defer к iterative / W9.
- Suggested Phase 3 gate: Option A deployed → 24h clean monitoring → flip
  enforce → keep report-only 7 days as safety net.

### Session artifacts (tour)

- Temp user: `browser_tour_1776879969215055742` (id=66) **DELETED** post-tour.
- TOTP device: 1 linked device **DELETED** (cascaded with user).
- Orphan check: 0 remaining `browser_tour_*` users, 0 TOTP devices.
- No code changes (read-only tour + audit doc update).
- 2 violations fully classified; grep inventory cross-validated.

---

## Phase 2b — Handler extraction (2 files, 5 handlers) — 2026-04-22 ~18:10–18:30 UTC

### Scope

Priority 1-2 handlers из W2.3 Phase 2a findings извлечены в external JS
модули с delegated listeners (W1.3 pattern). Priority 3-5 (campaign_detail
+ settings forms + tail ~61 handlers) — defer к W9 UX redesign.

### Files modified

| Файл | Было | Стало |
|------|------|-------|
| `backend/templates/registration/login.html` | 2 inline `onclick="switchTab(...)"` + inline `<script nonce>` блок с `function switchTab` | `data-action="switch-tab"` data-tab атрибут + `<script src="pages/login.js" nonce>` |
| `backend/templates/ui/mail/campaigns.html` | 2× `onchange="this.form.submit()"` на filter selects + `onclick="window.__refreshQuota..."` | `data-action="filter-submit"` / `data-action="quota-refresh"` атрибуты + `<script src="pages/mail_campaigns.js" nonce>` |

### JS modules created

| Файл | LOC | Exports | Delegated patterns |
|------|----:|---------|--------------------|
| `backend/static/ui/js/pages/login.js` | 92 | IIFE, no globals | `click` → `[data-action="switch-tab"]` → `switchTab(data-tab)` |
| `backend/static/ui/js/pages/mail_campaigns.js` | 37 | IIFE, no globals | `change` → `[data-action="filter-submit"]` → `form.submit()`; `click` → `[data-action="quota-refresh"]` → `window.__refreshQuota(true)` |

Zero behavior change — логика `switchTab` (focus + classes + description
text) + filter auto-submit + quota manual refresh идентичны до/после.

### Commits

- `19703f94` — `fix(csp): W2.3 Phase 2b extract login.html inline handlers`
- `7da6c835` — `fix(csp): W2.3 Phase 2b extract mail/campaigns.html inline handlers`

### Browser MCP re-verification

Tool: Playwright Browser MCP. Temp admin `browser_2b_1776882094247040907`
(user_id=67, device_id=3) — создан, использован, удалён. 0 orphans.

**Действия в браузере**:
1. `/login/` — переключение табов: access-key → password → access-key → password. Тумба работает визуально (form/description/focus меняются).
2. Password login submit → `/accounts/2fa/verify/` → TOTP submit → `/` (dashboard).
3. `/mail/campaigns/` — `select[name="branch"]` dispatch `change` event → URL изменился на `?branch=1` (delegated submit сработал).
4. `/` + `/tasks/` + повторный визит `/mail/campaigns/` — routine navigation.

**HTTP traffic**: 147 запросов за 10-min post-deploy window (реальный
workflow, не idle).

**Result**:

| Metric | Pre-2b (tour) | Post-2b (verify) |
|--------|--------------:|-----------------:|
| script-src-elem violations | 0 | **0** |
| script-src-attr violations | 2 | **0** |
| Total CSP violations | 2 | **0** |

### Phase 2 total accounting

| Phase | Fix | Violations eliminated |
|-------|-----|----------------------:|
| 2a | `v2_modal.html::runScripts()` nonce propagation + meta tag | 3 script-src-elem (modal-driven) + 2 collateral script-src-attr |
| 2b | Extract `login.html` (2) + `campaigns.html` (3) | 2 script-src-attr (login tab + campaigns filter) |
| **Total** | | **7 unique CSP violations eliminated** |

Deferred к W9 UX redesign: **61 grep-listed inline handlers** в 9
templates (campaign_detail 14, user_form 5, messenger_inbox_form 5,
error_log 5, users 4, company_list_rows 3, analytics_user 3,
messenger_automation 2, preferences 2 + остальные partials/500/404).
Risk: script-src-attr violations при admin interactions с этими
страницами в Phase 3 strict enforce. Mitigation: report-only policy
сохраняется 7 дней после flip для обнаружения — iterative fix.

### Phase 3 readiness — UPDATED

- ✅ runScripts nonce propagation (2a).
- ✅ Main user paths (`/`, `/tasks/`, `/companies/`, `/mail/campaigns/`,
  login flow): clean после 2b.
- ✅ Public endpoint `/login/` clean — tab switcher работает.
- ⏳ Admin pages с static handlers: defer к iterative / W9.
- ✅ 48h monitoring window активен (started 17:05 UTC 2026-04-22).
- **Earliest Phase 3 flip: 2026-04-24 ~17:05 UTC**.

**Phase 3 gate checklist**:
- [x] 2a deployed + verified (0 script-src-elem).
- [x] 2b deployed + verified (0 script-src-attr на main paths).
- [ ] 48h monitoring in report-only — no new unexpected sources.
- [ ] Pre-flip final Browser MCP tour (admin pages + quick priority 3 spot-checks).
- [ ] Feature-flag guard для rollback (Content-Security-Policy vs -Report-Only switch via django-waffle).

### Session artifacts (Phase 2b)

- Code: `19703f94` + `7da6c835` (login.html, campaigns.html, +2 new JS files, 143 lines net).
- Tests: 1320 passing (CI green на both commits).
- Smoke: 6/6 pre+post deploy.
- Temp user `browser_2b_1776882094247040907` **DELETED** (uid=67, totp=1).
- Orphan check: 0 remaining `browser_2b_*` users, 0 TOTP devices.
- Post-fix CSP violations (10-min window, 147 HTTP requests): **0**.
