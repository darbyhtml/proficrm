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

## Session artifacts

- Docs only: this file.
- Zero code changes.
- Baseline preserved: 1320 tests OK, smoke 6/6.
- CSP monitoring continues — new violations as user browses further.
