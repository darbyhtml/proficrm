# W2.1.4 — Settings endpoints inventory

**Date**: 2026-04-22. **Scope**: Group A codification planning — migrate inline `require_admin()` checks to `@policy_required` decorators. **Read-only audit**, no code changes.

---

## TL;DR

- **64 settings view-functions** across 5 files.
- **0 endpoints** currently use `@policy_required` (complete greenfield codification).
- **60 trivial** admin-only (bulk codify candidates).
- **4 complex** (2 self-governing policy UI + 2 role-mixed).
- **~55 new resources** нужно зарегистрировать в `policy/resources.py`.
- **4 sub-sessions** предложено (~16 endpoints each, ~1.5-2h per session).

---

## Per-file breakdown

### `backend/ui/views/settings_core.py` (33 endpoints)

Lines 1713. Everything admin-only via `require_admin()` inline — за исключением `settings_access` / `settings_access_role` которые используют expanded check `user.is_superuser or user.role == User.Role.ADMIN` (functionally equivalent + harder to codify безопасно).

#### 4a. Policy self-management (sensitive bootstrap)

| # | Line | Endpoint | HTTP | Current protection | Resource candidate | Complexity |
|---|------|----------|------|--------------------|--------------------|------------|
| 1 | 143 | `settings_access` | GET/POST | inline `is_superuser or role==ADMIN` | `ui:settings:access` (page, sensitive) | **COMPLEX** — bootstrap safety |
| 2 | 346 | `settings_access_role` | GET/POST | inline `is_superuser or role==ADMIN` | `ui:settings:access:role` (page, sensitive) | **COMPLEX** — bootstrap safety |

**Bootstrap safety note**: Эти views manage policy itself. Если admin делает `PolicyRule.DENY` на `ui:settings:access` — lockout. Mitigation options:
- (a) Hard-coded superuser bypass в decorator (inspect `request.user.is_superuser` first, skip policy).
- (b) Keep inline check, НЕ codify.
- (c) Register как sensitive + document safety в W2.1.4.4 session.

Recommendation: **(a) is_superuser bypass** patterns — simplest, keeps codification consistency.

#### 4b. Dashboard + announcements (2)

| # | Line | Endpoint | HTTP | Resource candidate | Complexity |
|---|------|----------|------|--------------------|------------|
| 3 | 46 | `settings_dashboard` | GET | `ui:settings:dashboard` (page) | Trivial |
| 4 | 67 | `settings_announcements` | GET/POST | `ui:settings:announcements` (page) | Trivial |

#### 4c. Branches (3)

| # | Line | Endpoint | HTTP | Resource candidate | Complexity |
|---|------|----------|------|--------------------|------------|
| 5 | 535 | `settings_branches` | GET | `ui:settings:branches` (page) | Trivial |
| 6 | 544 | `settings_branch_create` | GET/POST | `ui:settings:branches:create` (action, sensitive) | Trivial |
| 7 | 560 | `settings_branch_edit` | GET/POST | `ui:settings:branches:edit` (action, sensitive) | Trivial |

#### 4d. Users (8)

| # | Line | Endpoint | HTTP | Resource candidate | Complexity |
|---|------|----------|------|--------------------|------------|
| 8 | 579 | `settings_users` | GET/POST | `ui:settings:users` (page) | **Nuanced** — содержит POST view_as toggle, уже покрыто `ui:settings:view_as:update` |
| 9 | 818 | `settings_user_create` | GET/POST | `ui:settings:users:create` (action, sensitive) | Trivial |
| 10 | 858 | `settings_user_edit` | GET/POST | `ui:settings:users:edit` (action, sensitive) | Trivial |
| 11 | 956 | `settings_user_magic_link_generate` | GET/POST | `ui:settings:users:magic_link:generate` (action, sensitive) | **Sensitive** — security token issuance |
| 12 | 1046 | `settings_user_logout` | POST | `ui:settings:users:force_logout` (action, sensitive) | Trivial |
| 13 | 1100 | `settings_user_form_ajax` | GET | `ui:settings:users:form` (action) | Trivial |
| 14 | 1143 | `settings_user_update_ajax` | POST | `ui:settings:users:update` (action, sensitive) | Trivial |
| 15 | 1198 | `settings_user_delete` | POST | `ui:settings:users:delete` (action, sensitive) | Trivial |

#### 4e. Dictionaries (13)

| # | Line | Endpoint | HTTP | Resource candidate | Complexity |
|---|------|----------|------|--------------------|------------|
| 16 | 1248 | `settings_dicts` | GET | `ui:settings:dicts` (page) | Trivial |
| 17 | 1267 | `settings_company_status_create` | POST | `ui:settings:dicts:company_status:create` (action) | Trivial |
| 18 | 1285 | `settings_company_sphere_create` | POST | `ui:settings:dicts:company_sphere:create` (action) | Trivial |
| 19 | 1303 | `settings_contract_type_create` | POST | `ui:settings:dicts:contract_type:create` (action) | Trivial |
| 20 | 1321 | `settings_task_type_create` | POST | `ui:settings:dicts:task_type:create` (action) | Trivial |
| 21 | 1341 | `settings_company_status_edit` | GET/POST | `ui:settings:dicts:company_status:edit` (action) | Trivial |
| 22 | 1369 | `settings_company_status_delete` | POST | `ui:settings:dicts:company_status:delete` (action, sensitive) | Trivial |
| 23 | 1384 | `settings_company_sphere_edit` | GET/POST | `ui:settings:dicts:company_sphere:edit` (action) | Trivial |
| 24 | 1412 | `settings_company_sphere_delete` | POST | `ui:settings:dicts:company_sphere:delete` (action, sensitive) | Trivial |
| 25 | 1451 | `settings_contract_type_edit` | GET/POST | `ui:settings:dicts:contract_type:edit` (action) | Trivial |
| 26 | 1481 | `settings_contract_type_delete` | POST | `ui:settings:dicts:contract_type:delete` (action, sensitive) | Trivial |
| 27 | 1496 | `settings_task_type_edit` | GET/POST | `ui:settings:dicts:task_type:edit` (action) | Trivial |
| 28 | 1536 | `settings_task_type_delete` | POST | `ui:settings:dicts:task_type:delete` (action, sensitive) | Trivial |

#### 4f. Activity + error log (5)

| # | Line | Endpoint | HTTP | Resource candidate | Complexity |
|---|------|----------|------|--------------------|------------|
| 29 | 1555 | `settings_activity` | GET | `ui:settings:activity` (page, sensitive) | Trivial |
| 30 | 1577 | `settings_error_log` | GET | `ui:settings:error_log` (page, sensitive) | Trivial |
| 31 | 1645 | `settings_error_log_resolve` | POST | `ui:settings:error_log:resolve` (action) | Trivial |
| 32 | 1669 | `settings_error_log_unresolve` | POST | `ui:settings:error_log:unresolve` (action) | Trivial |
| 33 | 1688 | `settings_error_log_details` | GET | `ui:settings:error_log:details` (action) | Trivial |

### `backend/ui/views/settings_mail.py` (5 endpoints)

Lines 343. All admin-only. Existing `ui:mail:*` resources cover page/action concepts already, но для settings-specific admin control need dedicated keys.

| # | Line | Endpoint | HTTP | Resource candidate | Complexity |
|---|------|----------|------|--------------------|------------|
| 34 | 63 | `settings_mail_setup` | GET | `ui:mail:smtp_settings` (existing, action, sensitive) | Trivial |
| 35 | 81 | `settings_mail_save_password` | POST | `ui:mail:settings:update` (existing, action, sensitive) | Trivial |
| 36 | 143 | `settings_mail_test_send` | POST | `ui:settings:mail:test_send` (NEW, action, sensitive) | Trivial |
| 37 | 238 | `settings_mail_save_config` | POST | `ui:mail:settings:update` (existing, reuse) | Trivial |
| 38 | 307 | `settings_mail_toggle_enabled` | POST | `ui:settings:mail:toggle_enabled` (NEW, action, sensitive) | Trivial |

### `backend/ui/views/settings_messenger.py` (14 endpoints)

Lines 1156. All admin-only (14/14 require_admin). Fresh resource tree needed.

| # | Line | Endpoint | HTTP | Resource candidate | Complexity |
|---|------|----------|------|--------------------|------------|
| 39 | 31 | `settings_messenger_overview` | GET | `ui:settings:messenger:overview` (page) | Trivial |
| 40 | 107 | `settings_messenger_source_choose` | GET/POST | `ui:settings:messenger:inbox:source_choose` (action) | Trivial |
| 41 | 123 | `settings_messenger_inbox_ready` | GET | `ui:settings:messenger:inbox:ready` (action) | Trivial |
| 42 | 149 | `settings_messenger_health` | GET | `ui:settings:messenger:health` (page) | Trivial |
| 43 | 204 | `settings_messenger_analytics` | GET | `ui:settings:messenger:analytics` (page) | Trivial |
| 44 | 355 | `settings_messenger_inbox_edit` | GET/POST | `ui:settings:messenger:inbox:edit` (action, sensitive) | Trivial |
| 45 | 829 | `settings_messenger_routing_list` | GET | `ui:settings:messenger:routing:list` (page) | Trivial |
| 46 | 855 | `settings_messenger_routing_edit` | GET/POST | `ui:settings:messenger:routing:edit` (action) | Trivial |
| 47 | 946 | `settings_messenger_routing_delete` | POST | `ui:settings:messenger:routing:delete` (action, sensitive) | Trivial |
| 48 | 967 | `settings_messenger_canned_list` | GET | `ui:settings:messenger:canned:list` (page) | Trivial |
| 49 | 991 | `settings_messenger_canned_edit` | GET/POST | `ui:settings:messenger:canned:edit` (action) | Trivial |
| 50 | 1053 | `settings_messenger_canned_delete` | POST | `ui:settings:messenger:canned:delete` (action) | Trivial |
| 51 | 1077 | `settings_messenger_campaigns` | GET/POST | `ui:settings:messenger:campaigns` (page) | Trivial |
| 52 | 1123 | `settings_messenger_automation` | GET/POST | `ui:settings:messenger:automation` (page) | Trivial |

### `backend/ui/views/settings_mobile_apps.py` (3 endpoints)

Lines 148. All admin-only. Existing `ui:mobile_app:*` resources cover APK download/QR для end-users, но settings CRUD needs dedicated keys.

| # | Line | Endpoint | HTTP | Resource candidate | Complexity |
|---|------|----------|------|--------------------|------------|
| 53 | 27 | `settings_mobile_apps` | GET | `ui:settings:mobile_apps` (page) | Trivial |
| 54 | 47 | `settings_mobile_apps_upload` | POST | `ui:settings:mobile_apps:upload` (action, sensitive) | Trivial |
| 55 | 120 | `settings_mobile_apps_toggle` | POST | `ui:settings:mobile_apps:toggle` (action, sensitive) | Trivial |

### `backend/ui/views/settings_integrations.py` (9 endpoints)

Lines 832. **7 admin-only + 2 role-mixed** (calls_stats, calls_manager_detail допускают MANAGER/SALES_HEAD/BRANCH_DIRECTOR/ADMIN).

| # | Line | Endpoint | HTTP | Current protection | Resource candidate | Complexity |
|---|------|----------|------|--------------------|--------------------|------------|
| 56 | 46 | `settings_import` | GET/POST | `require_admin` | `ui:settings:data:import_companies` (action, sensitive) | Trivial |
| 57 | 103 | `settings_import_tasks` | GET/POST | `require_admin` | `ui:settings:data:import_tasks` (action, sensitive) | Trivial |
| 58 | 171 | `settings_company_columns` | GET/POST | `require_admin` | `ui:settings:company_columns` (page) | Trivial |
| 59 | 191 | `settings_security` | GET | `require_admin` | `ui:settings:security` (page, sensitive) | Trivial |
| 60 | 234 | `settings_mobile_devices` | GET | `require_admin` | `ui:settings:mobile_devices:list` (page) | Trivial |
| 61 | 291 | `settings_mobile_overview` | GET | `require_admin` | `ui:settings:mobile_devices:overview` (page) | Trivial |
| 62 | 398 | `settings_mobile_device_detail` | GET | `require_admin` | `ui:settings:mobile_devices:detail` (page) | Trivial |
| 63 | 431 | `settings_calls_stats` | GET | **role-list** (MANAGER/SALES_HEAD/BRANCH_DIRECTOR/ADMIN) | `ui:settings:calls:stats` (page) | **COMPLEX** — не admin-only |
| 64 | 732 | `settings_calls_manager_detail` | GET | **role-list** (same as 63) | `ui:settings:calls:manager_detail` (page) | **COMPLEX** — не admin-only |

---

## Category rollup

| Category | Count | Notes |
|----------|-------|-------|
| User management + branches | 11 | users/user_* (8) + branches/branch_* (3) |
| Dictionaries | 13 | company_status/sphere, contract_type, task_type (CRUD × 3 each, + dicts page) |
| Audit logs | 5 | activity + error_log × 4 |
| Dashboard + announcements | 2 | General settings pages |
| Policy self-management | 2 | access + access_role — BOOTSTRAP-CRITICAL |
| Mail (SMTP) | 5 | Admin SMTP config |
| Messenger admin | 14 | Inbox/routing/canned/campaigns/automation |
| Mobile apps (APK) | 3 | APK upload/toggle/list |
| Data import | 2 | Companies + tasks CSV import |
| Security/monitoring | 4 | security, mobile_devices × 3 |
| Column preferences | 1 | company_columns |
| Call stats | 2 | calls_stats + manager_detail — ROLE-MIXED |
| **Total** | **64** | |

---

## Complexity rollup

| Complexity | Count | Approach |
|------------|-------|----------|
| Trivial (admin-only, standard register + decorate) | **58** | Bulk codify, one commit per file or per logical group |
| Nuanced (admin-only, но с внутренней логикой) | **2** | settings_users (view_as toggle), settings_user_magic_link_generate (audit + rate limit preservation) |
| Complex — bootstrap safety | **2** | settings_access, settings_access_role — superuser bypass в decorator требуется |
| Complex — role-mixed (не admin-only) | **2** | settings_calls_stats, settings_calls_manager_detail — резолвер smart default должен разрешать roles |
| **Total** | **64** | |

---

## Resource registration gap

### Existing settings-relevant resources в `policy/resources.py` (6 relevant)

```
ui:settings                  (page, sensitive)  — umbrella
ui:settings:view_as:update   (action, sensitive) — codified W2.1.3b
ui:settings:view_as:reset    (action, sensitive) — codified W2.1.3b
ui:mail:smtp_settings        (action, sensitive) — existing
ui:mail:settings:update      (action, sensitive) — existing
ui:mobile_app:download       (action, sensitive) — existing (user-facing, different from settings)
```

### Needed new resources (~55)

Breakdown by prefix (rollup estimates):
- `ui:settings:dashboard` (1 page)
- `ui:settings:announcements` (1 page)
- `ui:settings:branches[*]` (3: page + 2 actions)
- `ui:settings:users[*]` (8: page + 7 actions)
- `ui:settings:dicts[*]` (13: page + 12 actions)
- `ui:settings:activity` (1 page)
- `ui:settings:error_log[*]` (4: page + 3 actions)
- `ui:settings:access[*]` (2: page + role page)
- `ui:settings:mail[*]` (2 new: test_send + toggle_enabled; остальные reuse существующие `ui:mail:*`)
- `ui:settings:messenger[*]` (14 new)
- `ui:settings:mobile_apps[*]` (3 new: page + 2 actions)
- `ui:settings:data[*]` (2 new: import_companies + import_tasks)
- `ui:settings:company_columns` (1 page)
- `ui:settings:security` (1 page, sensitive)
- `ui:settings:mobile_devices[*]` (3: list + overview + detail)
- `ui:settings:calls[*]` (2: stats + manager_detail)

**Approximate total needed**: **~55 new PolicyResource entries**.

---

## Proposed sub-session batching

Each sub-session ~1.5-2h. Structure: (1) register resources batch, (2) codify endpoints one-by-one with inline check preserved, (3) tests, (4) commit per logical unit.

### W2.1.4.1 — User management + branches + general (13 endpoints, ~1.5h)

**Scope**: `settings_core.py` lines 46-1198 — everything related к user/branch CRUD + dashboard/announcements page.

**Pre-work** (~5 resources):
- `ui:settings:dashboard`, `ui:settings:announcements`
- `ui:settings:branches`, `ui:settings:branches:create`, `ui:settings:branches:edit`
- `ui:settings:users`, `ui:settings:users:{create,edit,magic_link:generate,force_logout,form,update,delete}`

**Endpoints** (13):
1. settings_dashboard
2. settings_announcements
3. settings_branches
4. settings_branch_create
5. settings_branch_edit
6. settings_users
7. settings_user_create
8. settings_user_edit
9. settings_user_magic_link_generate (sensitive — preserve rate-limit + audit)
10. settings_user_logout
11. settings_user_form_ajax
12. settings_user_update_ajax
13. settings_user_delete

**Test strategy**: create 1 manager + 1 admin; для каждого endpoint — manager=403, admin=200/302. Pattern tests_w2_group_d_codification.py.

### W2.1.4.2 — Dictionaries + audit logs (18 endpoints, ~2h)

**Scope**: `settings_core.py` lines 1248-1713 — dicts CRUD + activity + error_log.

**Pre-work** (~14 resources):
- `ui:settings:dicts` + 12 CRUD resources по {company_status, company_sphere, contract_type, task_type} × {create, edit, delete}
- `ui:settings:activity`, `ui:settings:error_log`, `ui:settings:error_log:{resolve, unresolve, details}`

**Endpoints** (18):
1. settings_dicts
2-13. company_status/sphere, contract_type, task_type × CRUD (12)
14. settings_activity
15. settings_error_log
16. settings_error_log_resolve
17. settings_error_log_unresolve
18. settings_error_log_details

**Test strategy**: parametrized tests per dict entity. Admin-only gate.

### W2.1.4.3 — Messenger + mail (19 endpoints, ~2h)

**Scope**: `settings_messenger.py` full + `settings_mail.py` full.

**Pre-work** (~16 resources):
- 14 `ui:settings:messenger:*`
- 2 new `ui:settings:mail:{test_send, toggle_enabled}` (остальные reuse existing `ui:mail:*`)

**Endpoints** (19):
1-14. settings_messenger_* (14)
15-19. settings_mail_* (5)

**Test strategy**: mock messenger/SMTP deps. Covers largest single session (~1.5h codify + 30m tests).

### W2.1.4.4 — Integrations + mobile apps + complex (14 endpoints, ~2-2.5h)

**Scope**: `settings_integrations.py` (9) + `settings_mobile_apps.py` (3) + 2 complex policy UI (`settings_access`, `settings_access_role`).

**Pre-work** (~20 resources):
- 9 `ui:settings:*` для integrations
- 3 `ui:settings:mobile_apps:*`
- 2 `ui:settings:access[:role]` (sensitive, bootstrap-critical)

**Endpoints** (14):
1-3. settings_mobile_apps*
4-10. settings_{import, import_tasks, company_columns, security, mobile_devices, mobile_overview, mobile_device_detail}
11. settings_access — **нужен superuser bypass**
12. settings_access_role — **нужен superuser bypass**
13. settings_calls_stats — **role-mixed smart default**
14. settings_calls_manager_detail — **role-mixed smart default**

**Complexity premium** (+30m):
- Implement superuser bypass в `policy_required` decorator (или inline check preserved).
- Calls* endpoints: smart default `ALLOW` для MANAGER/SALES_HEAD/BRANCH_DIRECTOR/ADMIN, DENY для TENDERIST. Confirm smart default engine supports role-list в base state, OR use PolicyRule seeds.

**Test strategy**: specific tests для bootstrap safety (superuser never blocked даже если DENY rule создан) + role-mix tests (manager sees own data only).

---

## Pre-work before W2.1.4.1

- [ ] Confirm `@policy_required` imports + usage pattern from existing codified endpoints (e.g., `backend/ui/views/pages/company/cold_call.py`).
- [ ] Verify `require_admin` — **KEEP inline checks**? Per W2.1.3b pattern, decorator = explicit audit layer, inline = defense-in-depth. Confirm strategy continues (answer: YES per W2.1.3b decision).
- [ ] Decide naming convention: `ui:settings:<subsystem>[:<action>]`. Confirm consistency with existing `ui:mail:*` / `ui:companies:*` patterns.
- [ ] Plan for superuser bypass (needed in W2.1.4.4 для settings_access). Check if `policy_required` decorator already supports `is_superuser` skip — if not, need enhancement. Scope for W2.1.4.4 pre-work.

---

## Risk / open questions

1. **Smart default for settings_calls_stats**: does engine return "ALLOW for user.role in {MANAGER,SALES_HEAD,BRANCH_DIRECTOR,ADMIN}" automatically? If not → need explicit PolicyRule seeds in migration. Check `policy/engine.py`.
2. **Superuser bypass for settings_access**: if `policy_required` decorator checks `is_superuser` first и skips, cool. Otherwise, admin DENY-ing `ui:settings:access` by mistake = lockout для всех admin (only superuser sdm + perf_check escape). Verify и document.
3. **Bulk test file**: one `tests_w2_1_4.py` per sub-session OR one monolith? Recommend per sub-session (`tests_w2_1_4_1_users_branches.py`, etc.) для reviewability.
4. **Naming collision**: `ui:settings:mail` prefix vs existing `ui:mail:*` — verify clear separation (settings-side admin gates vs user-facing pages).

---

## Session artifacts

- Docs only: `docs/audit/w2-1-4-inventory.md` (this file).
- Zero code changes.
- Zero prod touches.
- Inventory baseline: 2026-04-22 post-W2.6 completion.
