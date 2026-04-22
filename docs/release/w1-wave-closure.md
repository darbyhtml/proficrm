# W1 Refactor Wave — Closure Summary

**Duration**: 2026-04-21 (W1.1 start) → 2026-04-22 (W1.4 close).
**Scope**: Pure refactoring, zero behavior change.
**Mode**: Staging-only per Path E (no prod deploys до W9).
**Total commits**: ~40+ across 4 mini-sessions.

---

## Mini-sessions overview

### W1.1 — `_base.py` split ✅ (2026-04-21)

**Target**: `backend/ui/views/_base.py` — 1 251 LOC god helper file.

**Result**:
- `_base.py`: 1 251 → **371 LOC** (−78%)
- Extracted **6 helper modules** in `backend/ui/views/helpers/` (total 1 002 LOC):
  - `search.py` (65 LOC) — 4 normalizers
  - `tasks.py` (87) — 3 permissions
  - `http.py` (72) — 4 request helpers
  - `cold_call.py` (74) — 5 cold-call + month utilities
  - `companies.py` (178) — 10 company access/edit/notifications
  - `company_filters.py` (512) — 10 filter functions (incl. FTS)
- Backward compat via re-exports — все existing imports `from ui.views._base import X` работают.
- **5 commits**, CI 8/8 green.

Doc: `docs/release/w1-1-base-split-plan.md`.

---

### W1.2 — `company_detail.py` split ✅ (2026-04-21)

**Target**: `backend/ui/views/company_detail.py` — 3 022 LOC god view.

**Result**:
- `company_detail.py`: 3 022 → **deleted** (option A clean, без shim).
- Extracted **10 thematic modules** in `backend/ui/views/pages/company/` (total ~3 336 LOC):
  - `detail.py` (393 LOC) — main card + timeline + tasks_history
  - `edit.py` (420) — edit/update/inline/transfer/contract
  - `deletion.py` (280) — 4-stage delete workflow
  - `contacts.py` (228), `notes.py` (474), `deals.py` (128)
  - `cold_call.py` (**691 initially**; dedup → 608 in W1.4)
  - `phones.py` (436), `emails.py` (136), `calls.py` (150)
- **40 URL routes** preserved (через `views/__init__.py` re-exports).
- Only 2 external consumers (`views/__init__.py` + `company_detail_v3.py`) — оба updated.
- **13 commits**, CI 8/8 green.

Doc: `docs/release/w1-2-company-detail-split-plan.md`.

---

### W1.3 — Inline JS/CSS extraction ✅ (2026-04-21)

**Scope**: Scenario C — extract inline assets, defer HTML body split to W9.

**Diagnostic** (109 templates scanned):
- 9 bare `<script>` blocks (no nonce) — CSP blockers.
- 81 `<script nonce>` blocks — ~12 427 LOC pure JS, CSP-ready.
- 32 inline `<style>` blocks — 4 131 LOC CSS.
- 76 inline event handlers (onclick/onsubmit/onchange) — CSP blockers.

**Result**:
- 9 bare scripts → 0 (added `{{ csp_nonce }}`).
- **Extracted 5 top styles** (2 684 LOC, 65% of inline CSS):
  - `base_global.css` (864), `company_detail_v3_b.css` (571), `messenger_conversations.css` (560), `_v2.css` (382), `_v3.css` (308).
- Converted **10 event handlers в company_detail.html** → `addEventListener` в `company_detail_handlers.js` (53 LOC).
- **6 new static files** в `backend/static/ui/`, total 2 738 LOC.
- Updated `test_dashboard.py::test_task_status_badges_displayed` — obsolete assertion проверял inline CSS.
- **9 commits**, CI 8/8 green.

Deferred to W2: 66 handlers (campaign_detail 14 + settings 19 + other), 27 smaller styles, full nonce script extraction.

Doc: `docs/release/w1-3-execution-plan.md`, `docs/audit/w1-3-inline-assets-inventory.md`.

---

### W1.4 — Wrap-up + cold_call dedup + coverage 53% ✅ (2026-04-22)

**Scope C-light**: cold_call.py dedup + coverage boost + docs finalization.

**Cold_call.py dedup**:
- Safety net first: **24 URL-layer tests** added (`backend/ui/tests_cold_call_views.py`).
- cold_call.py coverage: 10% → **59%** pre-dedup (pragmatic safety, target 85% was too ambitious).
- Dedup: 691 LOC / 8 standalone fns → 608 LOC / `_CCConfig` dataclass + 2 generic impls + 8 thin wrappers.
- Statements: 279 → 225 (−19%).
- Coverage after dedup: **78%** (те же тесты покрывают больше per LOC после консолидации).
- Zero external API change: все 8 URL routes работают идентично, 24 tests pass unchanged.

**Coverage 52% → 53%**:
- Achieved **automatically** через cold_call dedup (smaller stmts count + 24 new tests).
- `pyproject.toml fail_under`: 50 → **53**.
- W1 trajectory target met.

**Tests total**: 1 140 → **1 164** (1 140 baseline + 24 new).

**3 commits**:
- `563a937d` — test(cold_call): 24 safety tests
- `e266bdfd` — refactor(cold_call): dedup generic impl
- (this doc commit)

Doc: `docs/audit/cold-call-dedup-inventory.md`.

---

## W1 totals

| Metric | W1 start (W0 baseline) | W1 end | Δ |
|--------|------------------------|--------|---|
| `_base.py` LOC | 1 251 | 371 | −880 (−70%) |
| `company_detail.py` LOC | 3 022 | 0 (deleted) | −3 022 |
| `cold_call.py` LOC (post-W1.2) | 691 | 608 | −83 |
| Total tests | 1 140 | 1 164 | +24 |
| **Coverage** | **51–52%** | **53%** | **+1–2 pp ✅** |
| `pyproject.toml fail_under` | 50 | **53** | +3 |
| Biggest remaining god-file (Python) | `company_detail.py` | `company_filters.py` (512) | — |

### Created modules (24)

**Helpers** (`ui/views/helpers/`):
1. `search.py`, 2. `tasks.py`, 3. `http.py`, 4. `cold_call.py`, 5. `companies.py`, 6. `company_filters.py`.

**Pages** (`ui/views/pages/company/`):
7. `detail.py`, 8. `edit.py`, 9. `deletion.py`, 10. `contacts.py`, 11. `notes.py`, 12. `deals.py`, 13. `cold_call.py` (dedup'd), 14. `phones.py`, 15. `emails.py`, 16. `calls.py`.

**Static assets** (`backend/static/ui/`):
17. `css/pages/base_global.css`, 18. `company_detail_v3_b.css`, 19. `messenger_conversations.css`, 20. `_v2.css`, 21. `_v3.css`, 22. `js/pages/company_detail_handlers.js`.

**Tests**:
23. `tests_cold_call_views.py` (24 tests)
24. `tests/e2e/test_company_card_w1_2.py` (E2E smoke + no-console-errors)

### Security foundations (prep for W2)

- 9 bare `<script>` → 0 (100% nonce coverage на bare blocks).
- 10 inline event handlers converted → `addEventListener`.
- CSP middleware comment updated с post-W1.3 state.
- 66 remaining event handlers + 27 styles — W2 cleanup scope.
- CSP strict enforce switch — W2 logical next step.

### Quality gates

- ✅ **CI 8/8 green** на каждом commit.
- ✅ **Staging auto-deploy success** после каждого push.
- ✅ **Staging smoke** 6/6 зелёные.
- ✅ **Kuma monitor** Up throughout W1.
- ✅ **Ruff + black** clean.
- ✅ **1 164 tests passing** (1 140 baseline + 24 W1.4 new).

---

## Deviations from original plan

1. **cold_call.py coverage target 85% → actual 78%**: Initial plan asked 85% before dedup (safety net). Pragmatic call: 24 tests covering happy-path + permission enforcement = 59% pre-dedup → sufficient for safe dedup. Post-dedup: coverage jumped to 78% без additional tests (dedup consolidates tested paths).

2. **W1.4 Step 3 (coverage +1pp via new tests) skipped**: Coverage 53% достигнут автоматически через dedup (smaller stmts count). Нет необходимости писать tests for W0.4 modules.

3. **W1.2 `cold_call.py` в pages/company/ превысил target 400 LOC** (691). Документировано как "8 near-identical functions — dedup позже". W1.4 исправил: 608 LOC (ближе к target).

4. **Playwright E2E consolidation** — не делал per user scope C-light (skip).

---

## Ready for W2 — Security hardening

**Pre-W2 context** (per Path E + user confirmations):
- Staging users: **1** (Dmitry). No manager rollout coordination для staging experiments.
- Admin accounts: **2** (user + IT colleague). 2FA rollout = ~20 min.
- **Prod frozen** per Path E до W9. W2 policy enforce = staging only.

This simplifies W2 significantly: straight to enforce mode once staging verified, no staged rollout.

**W2 starting blocks** (from Hotlist + W1.3 deferred):
- 66 inline event handlers across templates (campaign_detail, settings, login).
- 27 smaller `<style>` blocks.
- CSP strict enforce switch (`'unsafe-inline'` → `'nonce-<val>'`).
- Items from Wave 0.1 audit: 83 mutating endpoints без `@policy_required` (P1).
- Password policy strict mode toggle (Wave 0.1 identified).
