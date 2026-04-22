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

## Session artifacts (so far)

- This doc: inventory + plan.
- Zero code changes (will be added в 11+ subsequent commits).
- Baseline preserved: 1316 tests OK, smoke 6/6.
