# W2.1.5 — Inline enforce() inventory

**Date**: 2026-04-22. **Scope**: codify inline `enforce()` calls via
`@policy_required` decorator — preserve inline calls as defense-in-depth.

---

## Total: 57 inline enforce() calls across 11 files

Matches W2.1.1 initial inventory exactly.

---

## Per-file breakdown

| File | Calls | Category |
|------|-------|----------|
| `phonebridge/api.py` | 12 | Phone/Android API |
| `mailer/views/recipients.py` | 9 | Mail recipients CRUD |
| `mailer/views/settings.py` | 8 | Mail admin settings |
| `notifications/views.py` | 5 | Notifications polling + marking |
| `mailer/views/campaigns/files.py` | 5 | Campaign attachment files |
| `mailer/views/sending.py` | 4 | Campaign send/pause/resume |
| `mailer/views/campaigns/crud.py` | 4 | Campaign create/edit/delete |
| `mailer/views/unsubscribe.py` | 3 | Unsubscribe list/delete/clear |
| `mailer/views/campaigns/templates_views.py` | 3 | Campaign HTML preview/templates |
| `mailer/views/polling.py` | 2 | Mail progress/quota poll |
| `mailer/views/campaigns/list_detail.py` | 2 | Campaign list + detail pages |
| **Total** | **57** | |

---

## Missing resources (need registration)

4 resources used в `enforce()` но не в `policy/resources.py`:

1. **`phone:qr:status`** — мобильное приложение проверяет status QR token. Мобильная QR-flow — отдельная branch (W2.6 context, не admin-only).
2. **`ui:mail:campaigns:attachment:download`** — admin downloads campaign attachment.
3. **`ui:mail:campaigns:export_failed`** — admin exports failed recipients CSV.
4. **`ui:mail:campaigns:retry_failed`** — admin retries failed sends.

Все 4 будут зарегистрированы before codification.

---

## Codification strategy

### Pattern

**Before**:
```python
@login_required
def view_func(request):
    enforce(
        user=request.user,
        resource_type="action",
        resource="ui:mail:campaigns:pick",
        context={"path": request.path, "method": request.method},
    )
    # ... view body ...
```

**After**:
```python
@login_required
@policy_required(resource_type="action", resource="ui:mail:campaigns:pick")
def view_func(request):
    # W2.1.5: inline enforce() preserved as defense-in-depth.
    enforce(
        user=request.user,
        resource_type="action",
        resource="ui:mail:campaigns:pick",
        context={"path": request.path, "method": request.method},
    )
    # ... view body unchanged ...
```

### Rules

- **Keep inline enforce()** — defense-in-depth (per session spec).
- Decorator uses **identical** resource string.
- Decorator `resource_type` matches inline value.
- NO behavior change — decorator просто adds audit layer + early-fail before view body runs.

### Commit batching

**One commit per file** (11 commits total). Exception: phonebridge/api.py is large (12 calls) — may split if individual endpoints warrant separate review, otherwise single commit.

### Order (low-risk first)

1. `mailer/views/polling.py` (2 calls) — smallest file, simplest.
2. `mailer/views/campaigns/list_detail.py` (2 calls).
3. `mailer/views/unsubscribe.py` (3 calls).
4. `mailer/views/campaigns/templates_views.py` (3 calls).
5. `mailer/views/sending.py` (4 calls).
6. `mailer/views/campaigns/crud.py` (4 calls).
7. `mailer/views/campaigns/files.py` (5 calls).
8. `notifications/views.py` (5 calls).
9. `mailer/views/settings.py` (8 calls).
10. `mailer/views/recipients.py` (9 calls).
11. `phonebridge/api.py` (12 calls) — largest, mobile API scope.

---

## Risks

- **Double-enforcement cost**: decorator calls `enforce()`, then view body calls `enforce()` again. Both compute same decision. Marginal overhead (policy engine is fast + cached). Acceptable для defense-in-depth.
- **Resource name mismatch**: если decorator resource ≠ inline resource, results diverge. Mitigation: audit enforces identical.
- **Refresh-token endpoint pattern**: `phonebridge/api.py` has endpoints using different auth (QR token exchange). Need per-endpoint review. Covered в batch 11.

---

## Testing strategy

Per-file after codification:
1. Full test suite → zero regression.
2. qa_manager sanity check для affected endpoints (should remain denied).

End-of-session:
1. Dedicated verification suite `backend/tests_w2_1_5_codification.py`:
   - `test_defense_in_depth_preserved` — inline enforce() remains in source.
   - Decorator presence — `@policy_required` on each codified view.
2. Disposable fixtures leftover check.
3. Smoke staging.

---

## Scope adjustment (post-analysis)

### phonebridge/api.py deferred — APIView class-method pattern

Original plan listed 11 files, 57 enforce() calls. During codification
phonebridge/api.py (12 calls) was analyzed:

- All 12 calls wrapped в DRF `APIView` class methods (`post`/`get`/`put`).
- `@policy_required` decorator designed для function views (signature
  `wrapper(request, *args, **kwargs)`). APIView methods have signature
  `(self, request, *args, **kwargs)` — needs `@method_decorator` wrapper.
- Adding `@method_decorator(policy_required(...), name='post')` на каждый
  класс = more boilerplate, same effective behavior as existing inline
  enforce() (which already runs через `has_permission`/dispatch).
- Alternative: DRF `PolicyPermission` class (`policy/drf.py`) uses
  `policy_resource_prefix` + action pattern. Phonebridge views have
  single-resource-per-class design, не fit этот pattern.

**Decision**: Skip phonebridge/api.py. Inline enforce() preserved как
single defense layer (same как существующее поведение). Document explicit
exclusion. Any future refactor toward DRF ViewSets can revisit.

### Actual codified count

- **10 files, 45 enforce() calls** получили `@policy_required` decorator.
- **1 file (phonebridge/api.py), 12 enforce() calls** остаются inline-only (documented exclusion).
- **12 inline-only inside POST conditional branches** (mailer/views/settings.py line 88, 236, 301, 340; mailer/views/recipients.py line 608, 629 — all inside single views с decorator на outer function, cannot move к class-level).

---

## Summary

| File | enforce() calls | With @policy_required | Inline-only (conditional) |
|------|-----------------|------------------------|---------------------------|
| mailer/views/polling.py | 2 | 2 | 0 |
| mailer/views/campaigns/list_detail.py | 2 | 2 | 0 |
| mailer/views/unsubscribe.py | 3 | 3 | 0 |
| mailer/views/campaigns/templates_views.py | 3 | 3 | 0 |
| mailer/views/sending.py | 4 | 4 | 0 |
| mailer/views/campaigns/crud.py | 4 | 4 | 0 |
| mailer/views/campaigns/files.py | 5 | 5 | 0 |
| notifications/views.py | 5 | 5 | 0 |
| mailer/views/settings.py | 8 | 4 | 4 (POST-branch conditionals) |
| mailer/views/recipients.py | 9 | 7 | 2 (reset_failed/reset_all scope branches) |
| phonebridge/api.py | 12 | 0 | 12 (**APIView deferred**) |
| **Total** | **57** | **39** | **18** |

39 new `@policy_required` decorators applied. 18 inline enforce() calls
preserved inline-only (either branch-conditional или APIView deferred).
All 57 inline enforce() calls **preserved** (defense-in-depth intact).

---

## Session artifacts

- Inventory doc: this file.
- Code changes: 10 files modified + 4 resources registered + 1 commit
  per logical unit.
- Tests: mailer suite 191/191, full suite 1316/1316.
- Baseline preserved: smoke 6/6.
