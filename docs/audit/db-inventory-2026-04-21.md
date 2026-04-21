# Database Inventory — Staging Snapshot 2026-04-21

**Environment**: staging (`crm_staging` db on proficrm-staging postgres container).
**Size**: **5222 MB** total.
**Tables**: **89** user tables in `public` schema.

---

## Top-30 tables by size

| # | Table | Size | Rows |
|---|-------|------|------|
| 1 | `audit_activityevent` | **3923 MB** | **9,555,401** |
| 2 | `companies_companysearchindex` | 638 MB | 45,709 |
| 3 | `companies_company` | 203 MB | 45,709 |
| 4 | `companies_companynote` | 151 MB | 243,614 |
| 5 | `companies_contact` | 119 MB | 99,156 |
| 6 | `companies_companyhistoryevent` | 66 MB | 169,541 |
| 7 | `companies_contactphone` | 31 MB | 113,553 |
| 8 | `companies_contactemail` | 16 MB | 51,048 |
| 9 | `companies_companyphone` | 16 MB | 45,273 |
| 10 | `notifications_notification` | 14 MB | 39,185 |
| 11 | `tasksapp_task` | 9.6 MB | 18,185 |
| 12 | `companies_company_spheres` | 6.3 MB | 40,458 |
| 13 | `audit_errorlog` | 5.7 MB | 2,685 |
| 14 | `companies_companyemail` | 5.1 MB | 13,456 |
| 15 | `phonebridge_phonetelemetry` | 1.0 MB | 4,372 |
| ... | (rest < 1 MB) | | |

**Observations**:
- `audit_activityevent` = **75% of all DB data** (3.9 GB of 5.2 GB). Matches **hotlist #6 P0 risk** — purge_old_activity_events disabled в beat due to OOM risk. Retention cleanup needed.
- `companies_companysearchindex` heavy FTS — 14× больше чем `companies_company` source. Может optimize rebuild strategy.
- **Companies** имеют **99% данных из amoCRM** (45242 / 45709).
- **Contacts** — **98% из amoCRM** (97557 / 99156).

---

## amoCRM tables (Operation 3 targets)

| Table | Size | Rows | Status |
|-------|------|------|--------|
| `ui_amoapiconfig` | 48 KB | 1 | **DROP SAFE** — no FK references |

**Зная** что:
- `backend/amocrm/` app никогда не имел своих Django моделей (only code logic — `client.py` + `migrate.py`). `AmoApiConfig` model жил в `ui` app.
- `django_migrations` has **0 entries for app='amocrm'** — подтверждает absence of own tables.
- **0 FK constraints** к/от `ui_amoapiconfig` — drop безопасен.

**Drop plan**: `ui/migrations/0014_delete_amoapiconfig` already committed (в Op 1). Apply на staging via `python manage.py migrate ui`. Alternative: direct `DROP TABLE ui_amoapiconfig;`.

---

## Preserved historical fields (NOT touched)

| Table | Field | Non-null rows | Total rows | % |
|-------|-------|---------------|------------|---|
| `companies_company` | `amocrm_company_id` | 45,242 | 45,709 | **99%** |
| `companies_contact` | `amocrm_contact_id` | 97,557 | 99,156 | **98%** |

**Decision**: preserve — these are **source-tracking identifiers** для records imported from amoCRM years ago. Fields позволяют match back к original amoCRM entities если нужно (future support tickets, historical audit).

Also preserved:
- `companies_companynote.source` enum includes `"amocrm"` value — preserved в choices для old records.
- `backend/companies/region_utils.py` — region aliases dictionary для data imported from amoCRM (regions в `addresses` fields).

---

## Most-written tables (churn analysis)

| Rank | Table | Writes (ins+upd+del) | Pattern |
|------|-------|----------------------|---------|
| 1 | `audit_activityevent` | **9,558,736** | 100% inserts — event log |
| 2 | `companies_companynote` | 289,417 | 85% inserts, 15% updates |
| 3 | `companies_companysearchindex` | 182,837 | 25% inserts, 75% updates — FTS rebuilds |
| 4 | `companies_companyhistoryevent` | 169,541 | 100% inserts — history log |
| 5 | `companies_contactphone` | 113,553 | 100% inserts |
| 6 | `companies_contact` | 99,156 | 100% inserts |
| 7 | `companies_contactemail` | 51,048 | 100% inserts |
| 8 | `companies_company` | 45,711 | 99.99% inserts, 2 updates |
| 9 | `companies_companyphone` | 45,273 | 100% inserts |
| 10 | `companies_company_spheres` | 40,458 | 100% inserts |

**Hot core confirmed**: `Companies + Contacts + ActivityEvent` = основной workload CRM.

---

## Dusty tables (0-10 rows — informational for future)

Messenger module (12 tables, ALL 0 rows — behind `MESSENGER_ENABLED=0`):
- `messenger_conversation`, `messenger_message`, `messenger_contact`, `messenger_inbox`, `messenger_campaign`, `messenger_automationrule`, `messenger_routingrule`, `messenger_canned*`, `messenger_pushsubscription`, `messenger_reportingevent`, `messenger_conversationtransfer`, `messenger_macro`, `messenger_agentprofile`, `messenger_conversationlabel`, `messenger_conversation_labels`, `messenger_channel`, `messenger_contactinbox`, `messenger_messageattachment`, `messenger_routingrule_regions`, ...

Mailer module (4 tables, 0 rows):
- `mailer_campaignqueue`, `mailer_campaignrecipient`, `mailer_sendlog`, `mailer_unsubscribe`.

Django / auth housekeeping (0 rows — expected):
- `accounts_user_groups`, `accounts_user_user_permissions`, `accounts_userabsence`, `auth_group`, `auth_group_permissions`, `django_admin_log`, `companies_companydeal`.

**Decision**: **НЕ удалять**. Tables exist по Django model definitions — any drop breaks migrations. Informational list only.

---

## FK integrity check на `ui_amoapiconfig`

```sql
SELECT conrelid::regclass AS from_table, confrelid::regclass AS to_table
FROM pg_constraint
WHERE contype='f'
  AND (conrelid::regclass::text='ui_amoapiconfig' OR confrelid::regclass::text='ui_amoapiconfig');

-- RESULT: 0 rows — no FK relationships.
```

**Verdict**: **DROP SAFE**. Operation 3 может proceed без CASCADE risks.

---

## Follow-up decisions (deferred, informational only)

### P0 tech debt (known hotlist items)

- **`audit_activityevent` retention purge** (hotlist #6 P0): 9.5M rows, `purge_old_activity_events` beat task disabled due to OOM risk. Fix = chunked DELETE (100K batches) в W3.

### Candidate cleanup in future waves

| Table group | Rows | When to reconsider |
|-------------|------|---------------------|
| Messenger tables (12, 0 rows) | 0 | Post-W5 (live-chat activation decides if keep). |
| Mailer tables (4, 0 rows) | 0 | W6 mailer hardening decides. |
| `companies_companydeal` (0 rows) | 0 | W3 core CRM — deals feature used? |

### Large-table optimizations

- `companies_companysearchindex` (638 MB, 14× company table) — FTS rebuild strategy review in W3.
- `companies_companynote` 151 MB — retention policy needed. 243K notes, no delete activity.
- `companies_companyhistoryevent` 66 MB — event log, retention candidate.

---

## Operation 3 executed — see `operation-3-result.md` section below.

(Populated после Op 3 execute.)
