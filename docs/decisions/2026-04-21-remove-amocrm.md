# ADR: Remove dead amoCRM integration module

**Date**: 2026-04-21
**Status**: Accepted
**Author**: darbyhtml (user)
**Recorded by**: Claude Code

---

## Context

amoCRM integration was added early в проект как one-time migration tool +
ongoing OAuth client для фоновой синхронизации compan и contacts. Subscription
expired months ago, API endpoint unreachable. Код остался как dead weight:

- **3579 LOC** `backend/amocrm/migrate.py` — migration orchestrator.
- **700+ LOC** `backend/amocrm/client.py` — OAuth client, API wrappers.
- **800+ LOC** `backend/ui/views/settings_integrations.py` — 7 admin views.
- **4 template files** `backend/templates/ui/settings/amocrm*.html`.
- **6 management commands** для one-shot data migration.
- **3 form classes** в `backend/ui/forms.py`.
- **AmoApiConfig** model (OAuth tokens storage) + admin + migration 0002/0005/0010/0013.

Test file `backend/amocrm/tests.py` contained 12 phone numbers potentially из real
amoCRM data (see Q13). Previous session Path E deferred cleanup.

User confirmed 2026-04-21: **amoCRM subscription dead months ago**, `AmoApiConfig.load()`
never called in runtime from real traffic, management commands никогда не запускаются
больше. Full cleanup unblocked.

---

## Decision

**Delete entire amoCRM integration module + UI + data migration code**.

Preserve **historical data references** (fields, enum values) так как они
hold source-tracking для records imported years ago.

---

## Scope

### Deleted (source code)

| Path | Description |
|------|-------------|
| `backend/amocrm/` | Entire Django app (client.py, migrate.py, tests.py, tests_client.py, __init__.py, management/) |
| `backend/amocrm/__init__.py` | App init |
| `backend/ui/tests/test_amocrm_migrate.py` | Tests файл для amocrm migrate |
| `backend/templates/ui/settings/amocrm.html` | Connect/disconnect UI |
| `backend/templates/ui/settings/amocrm_migrate.html` | Migration wizard |
| `backend/templates/ui/settings/amocrm_contacts_dry_run.html` | Dry-run preview |
| `backend/templates/ui/settings/amocrm_debug_contacts.html` | Debug view |
| `backend/ui/management/commands/reset_amocrm_import_lock.py` | Import lock reset |
| `backend/companies/management/commands/import_amo.py` | Legacy import |
| `backend/companies/management/commands/delete_amomail_notes.py` | One-shot cleanup |
| `backend/companies/management/commands/migrate_amo_phones_to_company_phones.py` | One-shot migrate |
| `backend/companies/management/commands/backfill_company_region_from_amo.py` | One-shot backfill |
| `backend/companies/management/commands/backfill_skynet_company_phones.py` | One-shot backfill |

### Modified (references cleaned)

| File | Change |
|------|--------|
| `backend/crm/settings.py` | Remove `"amocrm"` from INSTALLED_APPS |
| `backend/ui/urls.py` | Remove 7 `admin/amocrm/*` URL routes |
| `backend/ui/views/settings_integrations.py` | Remove 7 settings_amocrm* views + helpers (~800 LOC) + amoCRM imports |
| `backend/ui/views/__init__.py` | Remove 7 amocrm view re-exports |
| `backend/ui/views/_base.py` | Remove AmoApiConfig / AmoApiConfigForm / AmoMigrateFilterForm imports + __all__ entries |
| `backend/ui/models.py` | Remove AmoApiConfig class (lines 78-198) |
| `backend/ui/admin.py` | Remove AmoApiConfig import + AmoApiConfigAdmin registration |
| `backend/ui/forms.py` | Remove AmoApiConfigForm + AmoMigrateFilterForm classes |
| `backend/templates/ui/settings/dashboard_v2.html` | Remove AmoCRM tile (broken link) |
| `pyproject.toml` | Remove amocrm/migrate.py coverage omit + ruff per-file-ignore |
| `.github/workflows/ci.yml` | Remove amocrm/migrate.py coverage omit |

### Added

| File | Purpose |
|------|---------|
| `backend/ui/migrations/0014_delete_amoapiconfig.py` | Django migration to drop `ui_amoapiconfig` table on next migrate |
| `docs/decisions/2026-04-21-remove-amocrm.md` | This ADR |

### Preserved (historical data integrity)

- **`Company.amocrm_company_id`** (BigIntegerField) — source-tracking for companies imported from amoCRM. Historic records.
- **`Contact.amocrm_contact_id`** (BigIntegerField) — same, for contacts.
- **`CompanyNote.source` enum value `"amocrm"`** — preserves source tracking on notes imported years ago.
- Migration files `0001_initial.py`, `0002_amoapiconfig.py`, `0005_amoconfig_region_field.py`, `0010_amoapiconfig_encrypt_tokens.py`, `0013_amoapi_client_secret_enc.py` — historical migrations (never edit past migrations).
- Region parsing utilities `backend/companies/region_utils.py` — dict aliases for regions received from amoCRM (still used для companies imported earlier).
- Phone validation constants in `backend/companies/normalizers.py` — value-neutral utilities.
- References в ICS importer `backend/tasksapp/importer_ics.py::import_amocrm_ics` — named function, works with any .ics source.

### Database

**Staging**: tables dropped via Operation 3 of session (pg_dump + DROP TABLE amocrm_* CASCADE, + delete `django_migrations` rows for app='amocrm', + run `ui/migrations/0014_delete_amoapiconfig`). Backup preserved в `/var/backups/postgres/amocrm-cleanup/`.

**Prod**: tables remain until W9 accumulated deploy per Path E (`docs/decisions/2026-04-21-defer-prod-deploy-to-w9.md`). Same cleanup included в W9.10 deploy runbook.

---

## Consequences

### Positive

- **-5000+ LOC removed** из codebase.
- **Coverage cleanup**: amocrm/migrate.py (3579 stmts, 14% cov) no longer drags total down. Coverage omit reverted → real % recalculated.
- **Ruff cleanup**: 3 per-file-ignores removed (F821/F823/F601 в amocrm/migrate.py).
- **Test suite faster**: amocrm/tests.py removed — 50+ tests. Remaining 1177 tests full pass.
- **Q13 RESOLVED** fully: 12 possibly-real phone numbers в tests deleted.
- **Q14 partial** progress: fewer ruff ignores needed.

### Negative

- **Loss of reimport capability**: если в будущем user wants reimport from amoCRM, need re-add all code. Acceptable потому что amoCRM subscription ended permanently.
- **Historical notes source badge**: templates показывают `"amoCRM"` badge на items с `source='amocrm'`. Still works — enum value preserved. Just no new entries ever created with this source.

### Mitigation

If future integration needed: reimplement as separate app (new auth protocol likely). Old code archived in git history (commit preceding cleanup).

---

## References

- Session: W0.5 test infrastructure + Op 1-3 amoCRM cleanup (2026-04-21).
- Related ADR: `docs/decisions/2026-04-21-defer-prod-deploy-to-w9.md` (Path E).
- Q13 resolved: `docs/open-questions.md`.
- DB inventory: `docs/audit/db-inventory-2026-04-21.md`.
