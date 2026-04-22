# W2 Enforcement Plan — Policy hardening + Security foundations

**Drafted**: 2026-04-22 (W2.1.1 diagnostic outcome).
**Mode**: Staging-only per Path E до W9 accumulated deploy.
**Key finding**: policy engine **already в enforce mode** на staging. Scope pivot — from "shadow→enforce transition" к "codify existing + close remaining gaps".

---

## Discovered scope

### Unprotected (no `@policy_required`) — 74 / 160 views (46%)

| Group | Count | Defense mechanism | Risk | W2 action |
|-------|-------|-------------------|------|-----------|
| A | 64 | Inline `require_admin()` в `settings_*.py` | Low (protected, not routed) | Migrate to decorator |
| B | 7 | Alt decorator (`@require_can_view_company`, `@require_can_view_note_company`) | Low | Audit (likely OK) |
| C | 14 | User-self views (preferences/dashboard) | Very low | Audit, most acceptable |
| D | 5-7 | No visible check | Unknown | **HIGH PRIORITY audit** |

### Inline `enforce()` — 57 calls across 11 files

Primarily `mailer/views/*`, некоторые в `notifications/views.py`, `phonebridge/api.py`.
**Task**: migrate to `@policy_required` decorator для консистентности.

### DRF API — 36 resources without DB rules

Use `_baseline_allowed_for_role()` defaults. Verify что defaults correct per role matrix.

### 2FA coverage — 0% (from Wave 0.1 audit)

Admin accounts: 2 (Dmitry + IT colleague). Easy rollout per Path E.

### CSP strict enforcement — blocked by 66 remaining inline handlers (W1.3 deferred)

Plus 27 smaller `<style>` blocks.

---

## Conservative rollout plan

Mini-sessions ordered by **risk (low→high)** and **dependency** (e.g., 2FA doesn't depend on policy changes, can run parallel).

### W2.1.2 — Codify settings_* into policy engine (4-6 hours)

**Scope**: 64 settings views с `require_admin()` → `@policy_required(resource_type="page", resource="ui:settings:...")`.

**Sub-sessions** (per file, one commit each):
- W2.1.2a: `settings_core.py` (33 views)
- W2.1.2b: `settings_messenger.py` (14)
- W2.1.2c: `settings_integrations.py` (9)
- W2.1.2d: `settings_mail.py` + `settings_mobile_apps.py` (8)

**Per session**:
1. Identify precise resource key для каждой view.
2. Verify DB rules exist для admin role (or create migration).
3. Replace inline `require_admin()` → `@policy_required(...)`.
4. Run tests_enforce_views.py + smoke.
5. Commit.

**Safety net**: behavior identical (admin → allow, non-admin → deny). Just routed через policy engine instead of direct check.

---

### W2.1.3 — Audit Group B + Group C (2 hours)

Check each of 21 views с alternative decorators:
- Confirm actual security boundary matches policy engine expectations.
- If gap found — add explicit `@policy_required`.
- Most likely: OK as-is, document rationale.

**Outcome**: documented decision (add policy_required или justified skip) for каждой view.

---

### W2.1.4 — Audit Group D "truly unprotected" (1-2 hours)

Deep review каждой из ~7 views:
- `analytics_v2.py::analytics_v2_home`
- `messenger_panel.py::messenger_agent_status`
- `tasks.py::task_add_comment`, `task_view`, `task_create_v2_partial`, `task_view_v2_partial`, `task_edit_v2_partial`

**Review checklist per view**:
- What action/page does it serve?
- Who should be able to access?
- Is there implicit protection (e.g., AJAX partial called only from authorized parent view)?
- Add `@policy_required` if genuine gap.

---

### W2.1.5 — Migrate inline `enforce()` to decorator (3-4 hours)

57 calls across 11 files (primarily mailer/). 

**Sub-sessions** (per domain):
- W2.1.5a: `mailer/views/campaigns/*` (crud, files, list_detail, templates) — ~18 calls
- W2.1.5b: `mailer/views/recipients`, `sending`, `polling`, `settings` — ~19 calls
- W2.1.5c: `mailer/views/unsubscribe` + `notifications/views.py` + `phonebridge/api.py` — ~20 calls

**Pattern**:
```python
# Before
def some_view(request, ...):
    enforce(user=request.user, resource_type="action", resource="ui:mail:..." , context=...)
    # ... rest
    
# After
@login_required
@policy_required(resource_type="action", resource="ui:mail:...")
def some_view(request, ...):
    # ... rest
```

**Safety net**: same enforce() call, just decorator-wrapped. Zero behavior change.

---

### W2.2 — 2FA mandatory для admins (1 session, ~3 hours)

**Scope**: 2 admin accounts (Dmitry + IT colleague).

1. Install `django-two-factor-auth` or equivalent.
2. Enforce TOTP setup для `User.role == ADMIN` (or `is_superuser=True`).
3. Onboarding flow: redirect first login без TOTP → setup page.
4. Backup codes generation.
5. Login flow updates (admin URL paths).

**Prod implications**: W9 deploy включит 2FA для admins на prod. ~20 min dual admin setup.

---

### W2.3 — CSP strict enforce + remaining inline cleanup (2 sessions, 4-6 hours)

**W2.3a** — Clean remaining inline:
- 66 handlers → `addEventListener` (pattern from W1.3 #6).
  - campaign_detail.html (14), settings views (19 across 4 files), login.html (2), error pages (2), other (~29).
- 27 smaller `<style>` → external CSS.
- Full nonce coverage для `<script>` blocks without nonce, если any.

**W2.3b** — Switch CSP mode:
- `SecurityHeadersMiddleware` uncomment nonce injection в header.
- Replace `'unsafe-inline'` в `CSP_SCRIPT_SRC` / `CSP_STYLE_SRC` с `'nonce-{nonce}'`.
- Keep `CSP_HEADER` в production only (DEBUG=False).
- Playwright E2E checks для CSP violation errors in console.

**Rollback**: если CSP violations found → revert env var CSP_SCRIPT_SRC back to `'unsafe-inline'` temporarily.

---

### W2.4 — Integration tests + W2 closure (1 session, 2-3 hours)

1. Expand `policy/tests_enforce_views.py` до cover each role × top-20 resources (~100 test cases).
2. Targeted enable POLICY_DECISION_LOGGING for 1 day audit window на staging:
   - Let user Dmitry exercise flows.
   - Review decisions для obvious false patterns.
   - Turn off.
3. W2 rollup doc: `docs/release/w2-wave-closure.md`.
4. Update `docs/audit/hotlist.md`: items 2FA / CSP strict / unprotected endpoints → closed.
5. Pre-W3 context.

---

## Risk matrix

| Session | Behavior change risk | Rollback cost |
|---------|---------------------|---------------|
| W2.1.2 (codify settings) | None (same allow/deny, different path) | Trivial (`git revert`) |
| W2.1.3 (Group B/C audit) | None (docs only) | — |
| W2.1.4 (Group D audit) | Minimal (may add decorator, same semantic as alt check) | Trivial |
| W2.1.5 (inline→decorator) | None (same enforce call) | Trivial |
| W2.2 (2FA) | **Medium** — admins must re-auth | 10 min (disable flag, reset TOTP secrets) |
| W2.3a (inline cleanup) | Low (same behavior as W1.3) | Per-template revert |
| W2.3b (CSP strict) | **Medium** — browser console errors possible | Config-only revert |
| W2.4 (closure) | None | — |

**Most risky**: W2.2 (2FA) и W2.3b (CSP strict). Both have clear rollback paths.

---

## Prerequisites для W9 prod deploy

После W2 закрытия, prod будет получать (accumulated):
- Policy enforce mode (already staging-validated через W2.1).
- All migration/codification from W2.1.2-5.
- 2FA mandatory для admins (W2.2 tested on 2 staging admins = sufficient).
- CSP strict mode (W2.3 tested through Playwright + console monitoring).

**W9 deploy flow** (per MASTER_PLAN):
1. Deploy staging-verified code.
2. 48-72h monitoring window:
   - ErrorLog PermissionDenied rate.
   - Browser CSP violation reports (nginx error.log).
   - Admin 2FA setup completion.
3. User feedback channel (Telegram).
4. Rollback script ready.

---

## Session time estimates

| Session | Estimated | Cumulative |
|---------|-----------|------------|
| W2.1.1 diagnostic | ✅ done | 0h |
| W2.1.2 codify settings (4 sub-sessions) | 4-6h | 4-6h |
| W2.1.3 audit Group B/C | 2h | 6-8h |
| W2.1.4 audit Group D | 1-2h | 7-10h |
| W2.1.5 inline→decorator (3 sub-sessions) | 3-4h | 10-14h |
| W2.2 2FA | 3h | 13-17h |
| W2.3a inline cleanup | 3h | 16-20h |
| W2.3b CSP strict | 2-3h | 18-23h |
| W2.4 closure | 2-3h | 20-26h |

**Total W2**: ~20-26 hours = **5-7 mini-sessions**, 3-4 working days.

---

## Success criteria W2

- [ ] All 64 settings views migrated to `@policy_required`.
- [ ] All 57 inline `enforce()` calls migrated to decorator.
- [ ] Group D views: each either decorated или documented as intentionally open.
- [ ] DRF API: 36 resources — either DB rules added или default behavior confirmed.
- [ ] 2FA mandatory для admins (2 accounts set up).
- [ ] CSP strict mode active (nonce-based, no `'unsafe-inline'`).
- [ ] All remaining 66 inline handlers + 27 styles cleaned.
- [ ] `tests_enforce_views.py`: coverage expanded к ~100 test cases.
- [ ] Coverage 53% → 55% target (W2 per MASTER_PLAN).
- [ ] All 1164+ tests passing.
- [ ] Playwright E2E passing без new console errors.
- [ ] Staging smoke green throughout.
