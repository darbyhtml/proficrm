# W2.7 — /api/token/ usage audit

**Date**: 2026-04-22.
**Scope**: understand consumer patterns на `/api/token/` **before** blocking
admin password JWT login в W2.7.
**Result**: **STOP — real Android consumer detected, migration plan required**.

---

## TL;DR

- Staging `/api/token/` traffic: **0 real consumers**. Only curl tests from
  internal/host IPs. Safe to modify.
- Host-level nginx logs (shared между prod `crm.groupprofi.ru` + staging
  `crm-staging.groupprofi.ru` — no per-vhost log separation): **real
  okhttp/Android client (IP 83.239.67.30)** hitting `/api/token/` + refresh
  hourly. 14 successful password logins + 7 successful refreshes за 2 дня
  (2026-04-21 — 2026-04-22).
- Evidence path: traffic goes to **prod** — staging Django had W2.6 JWT
  filter blocking non-admin с 2026-04-22 09:02 UTC, но 83.239.67.30 okhttp
  still получает 200s после that time → must be hitting prod (which frozen
  at `be569ad4`, pre-W2.6).
- **Not safe to proceed**: regular Android user will break at W9 prod
  deploy (when W2.6 lands) если non-admin, OR at W2.7 prod deploy если admin.
  Consumer identification needed для migration plan.

---

## Staging ActivityEvent audit (14 days)

| entity_id prefix | Count |
|------------------|-------|
| login_failed | 11 |
| password_login_success | 7 (admin web login, W2.2 compliant) |
| jwt_login_success | **6 (before W2.6 deploy, user=nkv/manager)** |
| magic_link_success | 3 |
| access_key_login_success | 3 |
| user_logout | 1 |
| magic_link_failed | 1 |
| jwt_non_admin_blocked | 1 (W2.6 test) |

**Staging JWT events**:
- User `nkv` (role=manager) — 6 successful JWT logins **before** 2026-04-10 (pre-W2.6).
- 1 `jwt_non_admin_blocked` event 2026-04-22 09:53 — my W2.6 verification curl.
- **No admin JWT logins на staging в последние 14 days**.

**Conclusion**: staging Django не получала admin JWT password auth.

---

## Host-level nginx access.log analysis

Nginx config shares `/var/log/nginx/access.log` между `crm.groupprofi.ru` +
`crm-staging.groupprofi.ru` — нет per-vhost access_log directive. Default
combined format, no `$host` included.

### Raw evidence (last 2 log files, 2026-04-21 — 2026-04-22)

**Domain of origin — deduced via correlation**:

| IP | User-Agent | Pattern | Domain | Evidence |
|----|-----------|---------|--------|----------|
| `172.22.0.1` | curl/8.5.0 | 2026-04-22 09:52:40 | staging | Internal docker network IP |
| `87.248.238.61` | curl/8.18.0 | 2026-04-22 09:53-09:56 | staging | Office IP (my W2.6 tests) |
| `5.181.254.172` | curl/8.5.0 | 2026-04-22 09:52:40 | staging | Staging VPS's own IP |
| **`83.239.67.30`** | **okhttp/4.12.0** | **~hourly всю 2026-04-21+22** | **prod** | **See correlation below** |

### Correlation: 83.239.67.30 traffic = prod

1. **HTTP version**: okhttp traffic uses HTTP/2.0 (TLS-terminated at host nginx).
   Staging internal uses HTTP/1.1 (`172.22.0.1` internal, W2.6 test curls).
2. **Timing vs staging W2.6 deploy**: W2.6 JWT filter deployed к staging
   в commit `ab89c287` 2026-04-22 ~09:00 UTC. После этого staging blocks
   non-admin JWT с 403. Но `83.239.67.30` okhttp продолжает получать 200s
   на `/api/token/` (10:45, 11:54) ПОСЛЕ staging deploy.
3. **Pattern**: 14 successful `/api/token/` 200s + 7 successful
   `/api/token/refresh/` 200s + 5 refresh 401s (re-obtain after expiry).
   Classic 1-hour JWT access + weekly refresh rotation flow.
4. **UserAgent**: `okhttp/4.12.0` — Android native HTTP client (OkHttp
   library). Consistent с mobile app.

### Staging Django не получала эту нагрузку

Staging `jwt_login_success` events в ActivityEvent — 6 total, все от user
`nkv` (manager), все **до** 2026-04-10. Nothing new после W2.6 deploy.
Значит okhttp traffic с 83.239.67.30 НЕ hit staging Django app.

**Conclusion**: все 14 success `/api/token/` calls идут на prod
(`crm.groupprofi.ru`). Active Android user depends on prod password JWT.

---

## Prod DB query — BLOCKED

Path E hook предотвращает прямой read /opt/proficrm/. Cannot query prod
`audit_activityevent` или `accounts_user` для identification. User должен
run queries manually:

```sql
-- Which users have hit /api/token/ recently?
SELECT actor_id, COUNT(*), MAX(created_at) AS last_at
FROM audit_activityevent
WHERE entity_type = 'security' AND entity_id LIKE 'jwt_login_success:%'
  AND created_at > NOW() - INTERVAL '30 days'
GROUP BY actor_id
ORDER BY last_at DESC;

-- Correlate with user details
SELECT id, username, role, is_superuser, email
FROM accounts_user
WHERE id IN (<actor_ids from above>);
```

---

## Assessment

### Is `/api/token/` used for real admin authentication?

**Unknown but likely NO** — основываясь на:

1. Staging history: 0 admin JWT login events в 14 days.
2. Prod `83.239.67.30` user: unknown role (Path E blocks query), но:
   - W2.6 already filters non-admin → this user could be admin OR non-admin pre-W2.6.
   - Ambiguity: could be admin used Android app, OR non-admin who will break at W2.6 prod deploy.

### Assuming prod Android user = admin (worst case for W2.7)

- Blocking admin JWT = breaks this user's Android login.
- User's workaround: migrate to QR flow (`/mobile-app/` → session → QR → JWT).
- Migration path requires: web login first, then device pairing.

### Assuming prod Android user = non-admin

- W2.6 prod deploy alone will break them.
- W2.7 doesn't add incremental risk.

### Critical unknown: is Android app "officially supported" для admin?

Session prompt W2.6 said: *"Android app exists but not in production use.
Uses QR code scan flow at /mobile-app/, not /api/token/ JWT password."*

But staging evidence (nkv/manager) + host logs (83.239.67.30/okhttp)
contradicts — password JWT flow used by Android. Either:
- Old Android app version exists outside official flow.
- Official flow has password fallback not mentioned.
- Single user testing unofficial build.

---

## Decision: STOP W2.7

### Reason

Regular real Android consumer на prod `/api/token/` password auth. W2.7
would break them **whenever prod gets the fix** (W9 bundled deploy).

### Required before W2.7 applied to staging

- [ ] Identify prod user(s) behind 83.239.67.30 (run SQL queries above).
- [ ] Confirm их role (admin vs non-admin).
- [ ] If admin: plan migration к QR flow OR waive W2.7 scope.
- [ ] If non-admin: W2.6 prod deploy (W9 bundle) already blocks — no extra
      action от W2.7.

### Path forward

W2.7 code change itself is trivial (extend W2.6 filter). **Не blocker
technically**. But prod-impact assessment required before merging.

**Option A (conservative)**: Defer W2.7 — wait for user to identify prod
consumer + negotiate migration.

**Option B (staging-only)**: Proceed с W2.7 на staging, keep isolated,
не deploy prod в W9 bundle. Prod keeps admin password JWT until
consumer identified.

**Option C (aggressive)**: Proceed fully. User accepts risk Android user
breakage at W9 prod deploy. Provides migration path ahead of time.

---

## Session artifacts

- Docs only: `docs/audit/w2-7-jwt-usage.md` (this file).
- Zero code changes в audit step.
- Staging baseline preserved (1298 tests OK).
- Smoke: 6/6 green (pre-audit).
