# Android user identified — Непеаниди Ксения

**Date**: 2026-04-22 (audit update to `w2-7-jwt-usage.md`).
**Scope**: confirm identity of prod `/api/token/` Android consumer.
**Method**: READ-ONLY prod DB queries via `docker exec proficrm-db-1 psql`.

---

## User identified

| Field | Value |
|-------|-------|
| User ID | **13** |
| Username | **nkv** |
| Full name | Непеаниди Ксения (per user visual ID в mobile app registry UI) |
| Email | `nkv@kurskpk.ru` |
| Role | **manager** (NOT admin) |
| is_superuser | **False** |
| is_active | True |
| date_joined | 2026-01-10 |
| last_login (Django auth) | 2026-02-09 (stale — JWT логины не обновляют last_login) |

---

## Auth mechanism: exclusively password JWT

### Recent events (last 7 days audit_activityevent):

18 `jwt_login_success:13` events. Pattern:
- ~hourly access_token acquisitions.
- IP always `172.19.0.1` (docker internal — nginx proxies to Django без real-IP forwarding).
- All via `/api/token/` password flow.

### QR flow usage: ZERO

```sql
SELECT COUNT(*) FROM phonebridge_mobileappqrtoken WHERE user_id = 13;
-- Result: 0 rows
```

**Непеаниди Ксения никогда не использовала QR flow.** Её Android client
авторизуется строго через password JWT.

### Mobile device registered

```
id: c406f667-947a-43fa-8edc-438ce13b3446
device_id: a19de43cf093d767
device_name: 23129RN51X (Xiaomi model — likely Redmi 13C or similar)
platform: android
registered: 2026-01-12 12:15 UTC
last_seen_at: 2026-04-22 13:10 UTC (16:10 MSK — active сейчас)
```

Device зарегистрировано еще в январе 2026. Consistent device_id across
sessions suggests это тот же физический телефон весь период.

---

## IP 83.239.67.30 correlation

Nginx access.log показывает окhttp/Android запросы от внешнего IP
`83.239.67.30`. Это точно Ксения — host nginx пробрасывает к Django через
internal Docker network, поэтому Django видит `172.19.0.1` (internal) не
real public IP. Evidence:

- okhttp/Android UA на nginx matches только nkv's device registration
  (she's the only manager with active Android device последние 4 months).
- Timing совпадает: nginx 2026-04-22 11:54:35 UTC match Django audit
  event same timestamp.
- 98 JWT logins в 30 days = one authenticated user, repeated sessions.

---

## W2.6 / W2.7 impact assessment (REVISED)

### Admin JWT password usage на prod

```sql
SELECT COUNT(*) FROM audit_activityevent e
JOIN accounts_user u ON u.id = e.actor_id
WHERE (u.is_superuser OR u.role = 'admin')
  AND e.entity_id LIKE 'jwt_login_success:%'
  AND e.created_at > NOW() - INTERVAL '30 days';
-- Result: 0
```

**ZERO admin users have used JWT password на prod last 30 days.**

### Only affected user on prod: nkv

```sql
SELECT u.username, u.role, COUNT(e.id) as jwt_logins_30d
FROM accounts_user u
JOIN audit_activityevent e ON e.actor_id = u.id
WHERE e.entity_id LIKE 'jwt_login_success:%'
  AND e.created_at > NOW() - INTERVAL '30 days'
GROUP BY u.id, u.username, u.role;
-- nkv | manager | 98
```

**Единственный prod user, использующий `/api/token/` — это Непеаниди Ксения.**

### Impact matrix

| Fix | Impact на nkv | Impact на admins | Verdict |
|-----|--------------|------------------|---------|
| **W2.6 (blocks non-admin JWT)** | ❌ **breaks nkv** при prod deploy | ⚪ none (no admin JWT usage) | nkv needs migration |
| **W2.7 (blocks admin JWT too)** | ⚪ none (already blocked by W2.6) | ⚪ none (no admin JWT usage) | Safe |

---

## Recommendation (REVISED)

### W2.7 — SAFE TO PROCEED

Original STOP condition invalid после user identification:
- No admin users use password JWT → W2.7 breaks nobody new.
- Only W2.6 has production impact (nkv breaks).
- W2.7 provides consistent auth surface без incremental risk.

### Migration plan for nkv (required before W9 prod deploy)

**Coordinated migration path** (needs staging test first):

1. Admin logs в web (crm.groupprofi.ru) + 2FA.
2. Admin opens `/mobile-app/` → generates QR for nkv.
3. nkv updates к latest Android app version (must support QR flow).
4. nkv scans QR в Android app.
5. App обменивает QR через `/api/phone/qr/exchange/` → gets JWT.
6. Post-migration: 0 password JWT usage за 7 days confirms success.

**Blocker**: если current Android app version у nkv не supports QR flow,
нужна app update distribution first. Check `app_version` field
(`phonebridge_phonedevice.app_version` = empty string in her record —
unclear which version she runs).

### Alternative: waive W2.7, keep W2.6

If migration takes long, можно ship только W2.6 to prod (already applied
к staging). W2.7 defer until nkv migrated.

### Alternative: dedicated non-admin JWT endpoint

Create `/api/token/exchange/` (магик link exchange) для non-admin users
who need JWT. Admin generates magic link → user exchanges в Android app →
gets JWT. Preserves functionality без password exposure.

---

## Session artifacts

- Docs only: `docs/audit/w2-7-android-user-identified.md` (this file).
- Query evidence: SQL queries via `docker exec proficrm-db-1` (no
  /opt/proficrm path touches).
- Zero prod modifications.
- Zero code changes.

## Open questions для user

1. **Migration timeline для nkv**: когда админ может coordinate с Ксенией
   для QR flow setup?
2. **Android app version compatibility**: current app version у nkv
   supports QR flow? (Need to check APK list в
   `/admin/mobile-apps/` + compare с installed version).
3. **W2.7 scope decision** теперь informed:
   - (A) Proceed W2.7 code change staging-only (safe, no prod impact).
   - (B) Defer W2.7 — wait until ready с W2.6 + migration bundle for W9.
4. **Dedicated non-admin JWT endpoint?** Feature request for later.
