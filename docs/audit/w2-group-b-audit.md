# Group B audit — endpoints с alt decorators (no @policy_required)

**Session**: W2.1.3b (read-only audit).
**Baseline**: 1 172 tests passing, staging enforce mode.
**Conducted**: 2026-04-22.

---

## TL;DR

Plan expected ~7 Group B endpoints per W2.1.1. Accurate re-classification reveals **3 endpoints** (W1.4 cold_call dedup reduced some; others reclassified to Group C / A in W2.1.2a audit).

All 3 are **Category 1** (legitimate alt protection, just not routed через policy engine). Safe to codify в bulk session (W2.1.3c / W2.1.4). This session — audit only.

---

## 3 endpoints

| # | File | View | Line | Current decorator(s) |
|---|------|------|------|----------------------|
| 1 | `pages/company/cold_call.py` | `company_cold_call_toggle` | 299 | `@login_required` + `@require_can_view_company` |
| 2 | `pages/company/cold_call.py` | `company_cold_call_reset` | 322 | `@login_required` + `@require_can_view_company` |
| 3 | `pages/company/detail.py` | `company_timeline_items` | 357 | `@login_required` + `@require_can_view_company` |

---

## Endpoint #1: `company_cold_call_toggle`

**URL**: `/companies/<uuid>/cold-call/toggle/`  
**HTTP**: POST only  
**Defense mechanism**: 
- `@require_can_view_company` decorator — checks visibility scope (branch + responsible + visible_companies_qs).
- Inline `_can_edit_company(user, company)` check внутри `_cc_toggle_impl` → returns 403 если denied.
- W1.4 dedup routes всё через generic `_cc_toggle_impl`.

**Asymmetry observation**: 
- `contact_cold_call_toggle` и `contact_phone_*` + `company_phone_cold_call_toggle` — **всё уже используют** `@policy_required(resource="ui:companies:cold_call:toggle")`.
- Только `company_*` versions не декорированы. **Inconsistency, не security gap**.

### Classification: **Category 1** (legit alt protection)

**Rationale**: protection works — same resource/permission model as contact/phone variants, просто декоратор не применён. Can use existing `ui:companies:cold_call:toggle` resource (уже registered, уже имеет DB rules для 5 roles).

### Recommendation

- **This session**: leave alone, document in audit.
- **W2.1.3c / W2.1.4 bulk codification**: add `@policy_required(resource_type="action", resource="ui:companies:cold_call:toggle")` — symmetrical с другими 3 toggles.
- **No business logic question** — pattern identical.

---

## Endpoint #2: `company_cold_call_reset`

**URL**: `/companies/<uuid>/cold-call/reset/`  
**HTTP**: POST only  
**Defense mechanism**: 
- `@require_can_view_company` decorator.
- Inline `_cc_admin_guard(request)` → 403 non-admin AJAX / messages.error + redirect для non-admin non-AJAX.
- Falls через generic `_cc_reset_impl`.

**Asymmetry**: same as #1. `contact_*_reset` / `*_phone_*_reset` имеют `@policy_required(resource="ui:companies:cold_call:reset")`, но `company_cold_call_reset` — только `@require_can_view_company`.

### Classification: **Category 1** (legit alt protection)

### Recommendation

- **Bulk codification**: add `@policy_required(resource_type="action", resource="ui:companies:cold_call:reset")`.
- Resource уже registered, rules exist.

---

## Endpoint #3: `company_timeline_items`

**URL**: `/companies/<uuid>/timeline/items/?offset=N&limit=M`  
**HTTP**: GET only (AJAX partial, «Show more» button на карточке компании).

**Defense mechanism**:
- `@login_required` + `@require_can_view_company` — company visibility scope enforced.
- Inside body: `get_object_or_404(Company, id=company_id)` — 404 если не exists (не leaks existence).
- `build_company_timeline(company=company)` — service call, uses company as parameter (user scope enforced by `@require_can_view_company` already).

### Classification: **Category 1** (legit alt protection)

**Rationale**: read-only partial endpoint. `@require_can_view_company` is same visibility check как у `company_detail` page. Timeline items derive from the company — if user can see company, can see its timeline items (same security boundary).

### Recommendation

- **Bulk codification**: add `@policy_required(resource_type="page", resource="ui:companies:detail")` — same resource as main company_detail (timeline shares same page scope).
- No new resource needed.
- No business logic question.

---

## Summary matrix

| # | Endpoint | Resource to use | Already registered? | Already has DB rules? |
|---|----------|-----------------|---------------------|------------------------|
| 1 | `company_cold_call_toggle` | `ui:companies:cold_call:toggle` | ✅ | ✅ (5 roles × rules) |
| 2 | `company_cold_call_reset` | `ui:companies:cold_call:reset` | ✅ | ✅ |
| 3 | `company_timeline_items` | `ui:companies:detail` | ✅ | ✅ |

All 3 can be codified **zero-risk** в future bulk session — resource/rule infrastructure already in place.

---

## Business logic questions for user

**None** — все 3 endpoints clear Category 1. Patterns consistent с existing codified variants.

---

## Why leave alone в этой session (W2.1.3b)?

Per plan:
> W2.1.3b: Group B audit ONLY. Codification в W2.1.3c/W2.1.4.

Reasoning:
- 3 endpoints × change is trivial (~1 line decorator addition each).
- But staging retention task still pending first run (tomorrow 03:15 MSK).
- Prefer: Group D codification first (4 endpoints, more complex), Group B codification в следующую session вместе с settings_* если user approves plan continuation.

---

## Session output

- Docs only (audit file).
- No code changes.
- No registrations needed.
- Next session can process all 3 Group B + re-validate Group D via `qa_manager`.
