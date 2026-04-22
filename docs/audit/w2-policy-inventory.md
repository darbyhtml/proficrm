# W2 Policy engine inventory

**Snapshot**: 2026-04-22 (W2.1.1 diagnostic session, read-only).

---

## Infrastructure files

| File | Role | LOC |
|------|------|-----|
| `backend/policy/engine.py` | Core engine: `decide()`, `enforce()`, `_log_decision()`, `baseline_allowed_for_role()` | 476 |
| `backend/policy/decorators.py` | `@policy_required` декоратор | 109 |
| `backend/policy/drf.py` | DRF `PolicyPermission` class | 40 |
| `backend/policy/resources.py` | Registry `RESOURCES: tuple[PolicyResource, ...]` | 233 |
| `backend/policy/models.py` | `PolicyConfig`, `PolicyRule` | 92 |
| `backend/policy/admin.py` | Django admin для UI управления | 41 |
| `backend/companies/policy.py` | `can_view_company_id`, `visible_companies_qs` helpers | 79 |
| `backend/tasksapp/policy.py` | `can_view_task_id`, `visible_tasks_qs` helpers | 103 |
| `backend/policy/tests.py` | 410 LOC unit tests |
| `backend/policy/tests_enforce_views.py` | 122 LOC integration tests |

**Total**: ~1 713 LOC policy infrastructure.

---

## Resource registry

**102 registered resources** в `policy/resources.py`:
- `page` type: 20 (ui:dashboard, ui:companies:list, ui:settings, etc.)
- `action` type: 82 (ui:companies:create, ui:tasks:delete, ui:companies:cold_call:toggle, etc.)

Scope: Web UI pages, UI actions, DRF API endpoints (api:companies:*, api:tasks:*).

---

## Policy config state (staging)

Из `PolicyConfig.load()` на staging:

| Setting | Value |
|---------|-------|
| `PolicyConfig.mode` | **`enforce`** ⚠️ |
| `PolicyRule` total | 330 |
| `PolicyRule` enabled | 330 |
| `POLICY_DECISION_LOGGING_ENABLED` (settings) | **False** (disabled per Release 0 для performance — иначе 150K+ records/day) |

### Rule distribution

- **subject_type**: все 330 rules привязаны к `role` (0 rules per-user override).
- **roles** (66 rules каждая):
  - `manager`, `branch_director`, `sales_head`, `group_manager`, `admin`
- **effect**: 273 `allow` / 57 `deny`
- **resource_type**: 85 page / 245 action

### Coverage gaps

- Registered в `RESOURCES` но БЕЗ DB rules: **36 resources** (все API: `api:companies:*`, `api:tasks:*`, `api:contacts:*`, `api:company_notes:*`).
  В enforce mode эти идут через `_baseline_allowed()` default (baseline per role).
- Stale DB rules (в DB, но не в registry): **0** ✅.

---

## Decorator usage

### `@policy_required` in web views

**86 view functions** декорированы `@policy_required` across 20 files:

Top by count:
| File | @policy_required count |
|------|-----------------------|
| `ui/views/dashboard.py` | 19 |
| `ui/views/tasks.py` | 11 |
| `ui/views/company_list.py` | 8 |
| `ui/views/pages/company/notes.py` | 8 |
| `ui/views/pages/company/phones.py` | 7 |
| `ui/views/pages/company/cold_call.py` | 6 |
| `ui/views/pages/company/edit.py` | 5 |
| `ui/views/pages/company/deletion.py` | 4 |
| `ui/views/pages/company/contacts.py` | 3 |
| `ui/views/reports.py` | 3 |

### `PolicyPermission` in DRF ViewSets

**3 viewsets** используют `policy_resource_prefix`:
- `backend/companies/api.py` — 3 viewsets (companies, contacts, company_notes)
- `backend/messenger/api.py` — 2 viewsets
- `backend/tasksapp/api.py` — 2 viewsets

Total: 7 DRF viewsets integrated с policy engine.

### Inline `enforce()` calls

**57 locations** across 11 files — в основном `mailer/views/*` (campaigns, files, recipients, sending, polling, templates):
```
backend/mailer/views/campaigns/crud.py        — 4 calls
backend/mailer/views/campaigns/files.py       — 5 calls
backend/mailer/views/campaigns/list_detail.py — multiple
backend/mailer/views/campaigns/templates_views.py
backend/mailer/views/recipients.py
backend/mailer/views/sending.py
backend/mailer/views/settings.py
backend/mailer/views/unsubscribe.py
backend/notifications/views.py
backend/phonebridge/api.py
```

**W2 migration opportunity**: inline `enforce()` → `@policy_required` decorator для consistency.

---

## Unprotected endpoints audit

### Total: **74 unprotected** из 160 top-level view functions (46%)

Breakdown:

#### Group A — `settings_*` views с inline `require_admin()` (64 штуки)

| File | Unprotected | With `require_admin()` inline |
|------|-------------|-------------------------------|
| `settings_core.py` | 33 | 32 (97%) |
| `settings_messenger.py` | 14 | 14 (100%) |
| `settings_integrations.py` | 9 | 7 (78%) |
| `settings_mail.py` | 5 | 5 (100%) |
| `settings_mobile_apps.py` | 3 | 3 (100%) |

**Status**: защищены, но _не через policy engine_. W2 migration task:
- Replace inline `require_admin()` → `@policy_required(resource_type="page", resource="ui:settings:...")`
- Это codification existing behavior, не new enforcement.

#### Group B — Views с альтернативными decorators (~7)

Через `@require_can_view_company` / `@require_can_view_note_company` (защищены, но не через policy engine):
- `pages/company/cold_call.py::company_cold_call_toggle`, `company_cold_call_reset` (имеют `@require_can_view_company`)
- `pages/company/detail.py::company_timeline_items`
- `pages/company/notes.py::attachment_open/download × 4`

#### Group C — User-self views (~14)

Dashboard / preferences / personal settings:
- `view_as_update`, `view_as_reset` (admin function)
- `preferences_ui`, `preferences_mail`, `preferences_profile`, `preferences_password`, etc.
- `dashboard_poll`, `analytics_user`

Большинство — user-self (no cross-user security risk). `view_as_*` — admin feature, нужна protection.

#### Group D — Truly unprotected (HIGH PRIORITY) (~3-5)

Requires deeper audit:
- `analytics_v2.py::analytics_v2_home` (unclear who should see)
- `messenger_panel.py::messenger_agent_status` (internal AJAX?)
- `tasks.py::task_add_comment`, `task_view`, `task_create_v2_partial`, `task_view_v2_partial`, `task_edit_v2_partial`

Few tasks AJAX partials без explicit check — нужен review.

---

## Summary

- Policy infrastructure: **mature** (engine + decorators + DRF + registry + admin UI).
- Staging mode: **enforce** (already transitioned, per plan expected "shadow → enforce").
- Rule coverage: 66/102 resources have DB rules; 36 API resources rely on defaults.
- Decorator coverage: 86/160 views (54%) use `@policy_required`.
- DRF: 7 viewsets integrated.
- **0 PermissionDenied** в ErrorLog за 14 дней (solo staging user, low traffic — expected).

**W2 real gap**: не shadow→enforce transition (already done), а:
1. Migrate 64 settings_* `require_admin()` → `@policy_required` (codify in engine).
2. Migrate 57 mailer inline `enforce()` → decorator (consistency).
3. Audit 7 Group B views (safe alternative decorators — maybe OK).
4. Audit 5-7 Group D "truly unprotected" (may or may not be gaps).
5. Add DB rules для 36 API resources (or explicit baseline confirmation).
