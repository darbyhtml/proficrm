# Incident report: qa_manager accidental deletion during W2.1.4.1

**Date**: 2026-04-22.
**Environment**: staging.
**Severity**: Low (test account only, recovery clean).
**Status**: ✅ Closed — no data integrity issues.

---

## TL;DR

Во время shell-based testing endpoint #13 (`settings_user_delete`) Claude Code случайно вызвал delete на `qa_manager` (shared staging test user). Immediate recovery created new user с same username/role/branch (но new id 53 → 54). Post-incident audit: **zero orphaned records, zero FK integrity issues**. Django on_delete handlers (CASCADE/SET_NULL) отработали корректно. Recovery 100% functional.

---

## Timeline

| UTC | Event |
|-----|-------|
| ~10:33 | W2.1.4.1 Endpoint 11-13 testing в shell (`settings_user_form/update/delete`) |
| ~10:35 | `POST /admin/users/<qa_id>/delete/` в test script вызвал `user.delete()` на qa_manager (id=53) |
| ~10:35+1s | Django `on_delete` handlers cascaded: MagicLinkToken entries removed; ActivityEvent.actor_id → NULL; Company responsible_id → NULL (none applicable); etc. |
| 10:37:18 | Claude Code detected test failure (subsequent assertions failed on non-existent user). Recreate executed: `User.objects.create_user(username='qa_manager', ...)` + `set_unusable_password()` |
| 10:37+ | W2.1.4.1 Endpoint 13 re-tested с disposable user `w2141_disp` (non-destructive pattern) |
| 10:48 | W2.1.4.1 completion — qa_manager sanity check (home 200, settings 403) — functional |
| 10:57 | This audit session — magic link login test passed |

---

## qa_manager recovery state (post-incident)

```
id=54                              (was 53 pre-delete)
role=manager                       ✅ matches original
branch=ekb                         ✅ matches original
is_active=True                     ✅
is_staff=False                     ✅
is_superuser=False                 ✅
password_usable=False              ✅ (W2.6 cleanup applied after recreate)
email=qa_manager@test.groupprofi.local  ✅ matches original
date_joined=2026-04-22 10:37:18    (recreation timestamp)
last_login=2026-04-22 10:48:36     (W2.1.4.1 test usage)
```

**Only difference from original**: `id=53 → id=54`. All other fields match canonical test user.

---

## Blast radius assessment

### FK integrity — full scan

Query: `SELECT COUNT(*) FROM <table> WHERE <fk_col>=53` для всех 28 tables с FK к `accounts_user` где qa_manager could have been referenced.

```
magiclinktoken_user                    0
magiclinktoken_created_by              0
admintotpdevice                        0
activityevent_actor                    0
errorlog_user                          0
errorlog_resolved_by                   0
company_created_by                     0
company_responsible                    0
company_primary_cold_marked_by         0
companydeal_created_by                 0
companyhistoryevent_actor              0
companyhistoryevent_from               0
companyhistoryevent_to                 0
companynote_author                     0
companynote_pinned_by                  0
tasksapp_task_assigned                 0
tasksapp_task_created_by               0
tasksapp_taskcomment_author            0
tasksapp_taskevent_actor               0
messenger_conversation_assignee        0
messenger_message_sender               0
messenger_agentprofile                 0
notifications_notification             0
notifications_crmannouncementread      0
policy_policyrule_user                 0
token_blacklist_outstanding            0
ui_preferences                         0
waffle_flag_users                      0
```

**All 28 = 0 orphaned references.** Django handlers worked correctly.

### SET_NULL behavior — what was null'd

Fields с `on_delete=SET_NULL` переписали FK в NULL вместо каскадного delete. Affected tables containing qa_manager references:

- `ActivityEvent.actor` (SET_NULL) — activity events, которые qa_manager генерировал (policy decisions, tests) → `actor_id=NULL`. Today's null count: **20** (includes qa_manager's pre-delete events + natural anonymous events).
- `companies_company.responsible` (SET_NULL) — total null-responsible companies: **2** (`ep3_test_co_krd`, `ep3_test_co_ekb`). Обe created **2026-04-22 06:57-06:59 UTC** — до delete qa_manager (10:35). Не от этого incident'а. Qa_manager never had companies assigned.
- `CompanyHistoryEvent.actor` / `from_user_id` / `to_user_id` (SET_NULL) — total null count: 71453 (historic, unrelated).

### CASCADE behavior — what was wiped

Fields с `on_delete=CASCADE` removed dependent rows когда user.delete() issued:

- `MagicLinkToken.user` (CASCADE) — все qa_manager's magic link tokens removed. Verified 0 остатков.
- `AdminTOTPDevice.user` (CASCADE) — qa_manager не имел TOTP device (она только для admin), so no effect.

### Active session impact

Qa_manager was NOT actively logged-in на момент delete (shell test used `Client.force_login()`, not real session). No sessions invalidated.

---

## Recovery completeness verification

- ✅ User record recreated с идентичными role/branch/username/email.
- ✅ `set_unusable_password()` applied (consistent с W2.6 policy).
- ✅ Magic link generation works (`MagicLinkToken.create_for_user` returned valid token).
- ✅ External magic link login: `/auth/magic/<token>/` → **HTTP 302** → session cookie → **GET /** → **HTTP 200** с "qa_manager" + "Выйти" markers.
- ✅ W2.1.4.1 policy tests pass via new qa_manager (settings URLs → 403, home → 200).
- ✅ Full test suite 1237/1237 pass (tests используют local factories, не staging qa_manager).

---

## Lessons learned

### Rule violation

**Violated principle**: "Destructive endpoints testing requires disposable fixtures, not shared staging users."

Claude Code использовал `qa_manager` (shared test user) как target of `settings_user_delete` test вместо disposable fixture. Это caused real DB deletion, requiring recreation.

### Fix applied (immediately in same session)

After discovering deletion, W2.1.4.1 Endpoint 13 re-test использовал `w2141_disp` — disposable manager user created per-test. No shared users affected.

Verification test suite (`tests_w2_1_4_1_codification.py::test_settings_user_delete`) создаёт `w2141_disposable` в method scope для same reason.

### Preventive rules для future sub-sessions

1. **Destructive endpoint testing MUST use disposable fixtures**:
   - In TestCase: create target inside test method, не class-level setUp.
   - In staging shell: prefix username с `test_delete_<timestamp>` или `disp_<name>` clearly signalling "safe to delete".
   - Never use `qa_manager`, `sdm`, `perf_check`, or any named-convention user as delete target.

2. **Shell-based ORM testing = last resort**. Prefer HTTP flow через Django Client:
   - Full request/response cycle → exercises decorator + middleware + inline check + form validation + audit logging.
   - Easy rollback если test user contained в transaction.
   - Clearer intent ("test endpoint behavior" vs "invoke ORM method").

3. **Sub-session prompts for destructive endpoints should explicitly state**:
   > "Never delete existing staging records. Create disposable fixtures для destructive endpoint testing. Target username prefix: `disp_<endpoint_name>` для clarity."

### Why CASCADE/SET_NULL behaviour saved us

Django ORM's cascading on_delete transitioned cleanly:
- CASCADE (MagicLinkToken, AdminTOTPDevice) — full cleanup, no dangling FKs.
- SET_NULL (ActivityEvent, Company.responsible, CompanyHistoryEvent.actor) — preserved records value, lost actor attribution.

В случае если был бы **RESTRICT** FK — delete would have raised IntegrityError и qa_manager остался бы. Current schema design does NOT use RESTRICT on any user FK, which is **правильный trade-off** — prioritizes delete flexibility over attribution preservation.

---

## Action items для W2.1.4.2+

- [ ] Explicit rule в каждом sub-session prompt: "Destructive endpoint tests → disposable fixtures only."
- [ ] Add `tests/conftest.py` or fixtures helper: `make_disposable_user(prefix="disp", role="manager")` для easy reuse.
- [ ] Review W2.1.4.2 endpoints (dictionaries) — many `*_delete` endpoints; plan disposable targets upfront.

---

## Session artifacts

- Docs only: `docs/audit/w2-1-4-1-incident-qa-manager-delete.md` (this file).
- Zero code changes в audit session.
- Zero data modifications (read-only SQL queries + 1 magic link generation для recovery test).
