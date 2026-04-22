# Group D — Truly Unprotected Mutating Endpoints Audit

**Session**: W2.1.2a (audit-only, read-only).
**Baseline**: 1 164 tests passing, staging enforce mode, 330 active rules.
**Conducted**: 2026-04-22.

---

## TL;DR

Plan expected ~5-7 Group D endpoints. Accurate re-classification shows **4 endpoints**. Deep audit reveals **all 4 are false positives** of my classifier:
- 3 имеют explicit inline permission checks (IDOR-fix, role-based visibility, PermissionDenied).
- 1 — user-self OneToOne update (no cross-user mutation possible).

**No HIGH или MEDIUM severity findings**. All 4 = **LOW** (codify in engine позже для consistency).

Recommend: skip dedicated W2.1.4 session, roll these 4 в W2.1.3 (Group B) batch.

---

## Endpoint #1: `analytics_v2_home`

**File**: `backend/ui/views/analytics_v2.py`, line 26.
**URL**: `/analytics/v2/` (path: `analytics/v2/`, name: `analytics_v2_home`).
**HTTP methods**: GET only (не POST/PUT/DELETE).
**Current decorators**: `@login_required`.

### View body (paraphrased)
Router view: смотрит `user.role` и рендерит соответствующий dashboard template (`manager.html`, `sales_head.html`, `branch_director.html`, `group_manager.html`, `tenderist.html`, или `stub.html` для unknown role). Каждый dashboard подгружается via dedicated service function (`get_manager_dashboard(user)`, etc.).

### What it mutates
- **Nothing**. Pure read. `render(request, template, ctx)`.

### Who currently can call it
- Any authenticated user (any role).
- Each user sees dashboard dla **их own role только** (router inside view).
- Superusers + ADMIN + GROUP_MANAGER route к group_manager dashboard.

### Risk assessment
- **If any user calls it**: sees их own role dashboard. No cross-user data access possible — `get_X_dashboard(user)` scoped to user.
- **Privilege escalation**: невозможно (role determines template + service, не user-provided).
- **Info leak**: dashboard services (per W1.1 helpers) use `visible_companies_qs(user)` / `visible_tasks_qs(user)` — ограничены user scope.
- **Severity**: **LOW** (read-only, self-scoped).

### Proposed policy rule
- **Resource**: `ui:analytics:v2` (already registered? Проверю registry).
- **Allowed roles**: all authenticated roles (MANAGER, SALES_HEAD, BRANCH_DIRECTOR, GROUP_MANAGER, ADMIN, TENDERIST).
- **Effect**: add `@policy_required(resource_type="page", resource="ui:analytics:v2")` для engine routing.
- **No rule change in DB needed** — just codify existing allow-all pattern.

### Decorator proposal (W2.1.3 или W2.1.2c)
```python
@login_required
@policy_required(resource_type="page", resource="ui:analytics:v2")
def analytics_v2_home(request: HttpRequest) -> HttpResponse:
    ...
```

Note: need to add `ui:analytics:v2` в `backend/policy/resources.py` registry first.

### Business logic question for user
**None** — behavior clear, codify existing.

---

## Endpoint #2: `messenger_agent_status`

**File**: `backend/ui/views/messenger_panel.py`, line 221.
**URL**: `/messenger/me/status/` (name: `messenger_agent_status`).
**HTTP methods**: POST (GET → redirect).
**Current decorators**: `@login_required`.

### View body (paraphrased)
User updates **own AgentProfile.status** (enum: online/away/busy/offline). Uses `get_effective_user(request)` для "view-as" support (admin impersonation feature). `AgentProfile` — `OneToOneField(User)`, get-or-create on first status change.

### What it mutates
- **Model**: `AgentProfile` (row per user, 1:1).
- **Fields**: `status`, `updated_at`.
- **Side effects**: none (just save).

### Who currently can call it
- Any authenticated user (any role, including MANAGER).
- `get_effective_user()` == `request.user` for normal users. For admins in view-as mode — target user.
- `AgentProfile.objects.get_or_create(user=user)` strictly limited to the **user** из request — impossible to update someone else's profile.

### Risk assessment
- **Cross-user mutation**: NOT POSSIBLE — OneToOne + explicit `user=user` в get_or_create.
- **Privilege escalation**: NOT POSSIBLE — status is just display indicator, не permission gate.
- **Status enum validation**: strict — invalid status → flash error, no save.
- **Severity**: **LOW** (user-self update, self-scoped).

### Proposed policy rule
- **Resource**: `ui:messenger:agent_status` (new, need registration).
- **Allowed roles**: all authenticated roles (это self-service).
- **Effect**: codification only — no behavior change.

### Decorator proposal
```python
@login_required
@policy_required(resource_type="action", resource="ui:messenger:agent_status")
def messenger_agent_status(request: HttpRequest) -> HttpResponse:
    ...
```

### Business logic question for user
**None** — self-service, obvious pattern.

### Note
This actually belongs в Group C (user-self), не D. My classifier didn't include "agent_status" в USER_SELF_MARKERS список.

---

## Endpoint #3: `task_add_comment`

**File**: `backend/ui/views/tasks.py`, line 1885.
**URL**: Через `backend/ui/urls.py` (check):
```
path("tasks/<uuid:task_id>/comments/add/", views.task_add_comment, name="task_add_comment")
```
**HTTP methods**: POST only (else → 405 JSON).
**Current decorators**: `@login_required`.

### View body (paraphrased)
AJAX endpoint для добавления comment к задаче. **Has explicit SECURITY comment** references F3 IDOR-fix (prior security audit 2026-04-17):
1. `visible_tasks_qs(user).filter(id=task_id).first()` — IDOR-safe visibility filter.
2. If task not visible → 404 (не 403, чтобы не leak existence).
3. Additional check: `_can_manage_task_status_ui(user, task) OR _can_edit_task_ui(user, task)` — если нет → 403.
4. `TaskService.add_comment(task, user, text)` (validated).

### What it mutates
- **Model**: `TaskComment` (create new row).
- **Fields**: `task`, `author`, `text`, `created_at`.
- **Side effects**: likely none (service method, needs verification но вне scope audit).

### Who currently can call it
- Authenticated user.
- Must pass `visible_tasks_qs(user)` filter (IDOR protection — user sees только tasks their own branch/assigned/created/related).
- Must pass `_can_edit_task_ui` OR `_can_manage_task_status_ui`:
  - ADMIN, GROUP_MANAGER → any.
  - MANAGER → if assigned/created/responsible for task.
  - BRANCH_DIRECTOR, SALES_HEAD → branch + assigned/created.

### Risk assessment
- **IDOR**: **mitigated** (visible_tasks_qs filter explicitly addresses F3 audit finding).
- **Cross-user mutation**: blocked by two-layer check (visibility + edit perm).
- **Privilege escalation**: none (just comment creation).
- **Severity**: **LOW** (strong inline protection, comment explicitly references security audit).

### Proposed policy rule
- **Resource**: `ui:tasks:comment:add` (new).
- **Effect**: codify existing logic в engine via `@policy_required` + keep inline check as defense-in-depth.
- **Default rule**: все roles allow, actual permission check остаётся inline (custom visibility logic).

### Decorator proposal
```python
@login_required
@policy_required(resource_type="action", resource="ui:tasks:comment:add")
def task_add_comment(request: HttpRequest, task_id) -> HttpResponse:
    # ... existing inline checks remain (defense-in-depth)
```

### Business logic question for user
**None** — existing security model solid (documented F3 IDOR-fix).

### Note
This actually belongs в Group B (alt protection mechanism — explicit visible_tasks_qs + _can_edit_task_ui), не D. My classifier не recognized эти helpers как valid protection markers.

---

## Endpoint #4: `task_view_v2_partial`

**File**: `backend/ui/views/tasks.py`, line 2375.
**URL**: Через urls.py:
```
path("tasks/<uuid:task_id>/v2-view/", views.task_view_v2_partial, name="task_view_v2_partial")
```
**HTTP methods**: GET only (partial для modal).
**Current decorators**: `@login_required`.

### View body (paraphrased)
GET-only partial rendering task detail в modal. Uses `_v2_load_task_for_user(user, task_id)` helper (lines 2338-2371) which implements **full role-based visibility logic** с explicit `raise PermissionDenied()` если user cannot see task.

### _v2_load_task_for_user logic
```
ADMIN, GROUP_MANAGER → any task
MANAGER → assigned OR created OR company.responsible == user
BRANCH_DIRECTOR, SALES_HEAD → branch-scoped (created, assigned, company.branch, assigned.branch)
Fallback: company.responsible == user
Else: raise PermissionDenied()
```

### What it mutates
- **Nothing**. Pure GET read + render.

### Risk assessment
- **Cross-user read**: blocked (`PermissionDenied` raised).
- **Role-based logic**: comprehensive, matches main `task_view` pattern.
- **Severity**: **LOW** (GET-only + full role check).

### Proposed policy rule
- **Resource**: `ui:tasks:detail` (already registered — used by `task_view` с `@policy_required`).
- **Effect**: add decorator to match `task_view` pattern.

### Decorator proposal
```python
@login_required
@policy_required(resource_type="page", resource="ui:tasks:detail")
def task_view_v2_partial(request: HttpRequest, task_id) -> HttpResponse:
    ...
```

### Business logic question for user
**None** — identical visibility rules as already-protected `task_view`.

### Note
This is Group B (alt protection — custom PermissionDenied raise), false positive of my Group D classifier.

---

## Summary

### Total Group D endpoints audited: **4**

### Severity breakdown
| Severity | Count | Endpoints |
|---|---|---|
| HIGH | 0 | — |
| MEDIUM | 0 | — |
| LOW | 4 | analytics_v2_home, messenger_agent_status, task_add_comment, task_view_v2_partial |

### Key finding

**None of 4 endpoints are real security gaps.** All have existing protection:
- #1: Read-only router, scoped queries via service.
- #2: User-self OneToOne update (no cross-user mutation possible).
- #3: F3 IDOR-fix explicit + _can_edit_task_ui check.
- #4: Custom `_v2_load_task_for_user` с полноценной role-based visibility.

My W2.1.1 classifier was too strict — recognized только `@policy_required`, `require_admin()`, `_can_edit_company()`, and specific fn_name list. Real codebase uses broader set of protection patterns.

### Business logic questions for user
**None** — no uncertainty в permission logic. All 4 endpoints behave как expected, just not routed через policy engine.

### Safe to add @policy_required automatically?
- **Yes for all 4** — codification only, no behavior change. 2 need new resources в registry:
  - `ui:analytics:v2` — new resource, allow all authenticated.
  - `ui:messenger:agent_status` — new resource, allow all authenticated (self-service).
- 2 use existing resources:
  - `ui:tasks:comment:add` — new resource, allow all (actual check remains inline).
  - `ui:tasks:detail` — existing, reuse.

---

## Revised W2.1.4 recommendation

Since all 4 are false positives, **skip dedicated W2.1.4 session**. Roll these 4 into:
- W2.1.3 (Group B audit) — add them в that batch.
- Or W2.1.2c last sub-session — batch codification.

Saves 1-2 hours. Adjust W2 total estimate to **~18-24 hours** (from 20-26h).

---

## Pre-W9 consideration

**Do these 4 endpoints exist на prod currently?**

Yes — W1 did not touch any of these views. Prod currently:
- Has all 4 endpoints с same inline protection.
- Running enforce mode unclear (need prod PolicyConfig check, но Path E = don't touch prod).

**Risk level for prod**: same as staging = LOW. No point-fix needed. Wait until W9 accumulated deploy — codification changes ship together с all W2 migrations.

---

## Classifier lessons learned

My W2.1.1 "Group D" classifier produced **4 false positives / 4 total** = 100% false positive rate for this subset. Patterns I missed:

1. `visible_tasks_qs(user)` — IDOR filter, valid protection.
2. `_v2_load_task_for_user` — local helper с `raise PermissionDenied`.
3. `OneToOneField(User) + get_or_create(user=user)` — structural isolation.
4. Read-only GET views с self-scoped service calls.

For future audits: **cannot rely on static regex**. Need semantic analysis (does code path enforce access control for requested resource?).

Not critical for W2 — re-classification of other groups likely has some false positives too, but their "safe" classification (Groups A/B/C имеют **explicit protection visible** regex-wise) more reliable.
