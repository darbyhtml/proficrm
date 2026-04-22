# Staging Test Accounts

Справочник test-аккаунтов на staging. **Пароли хранятся в `/etc/proficrm/env.d/*.conf` (mode 600, owner root)** — не в git, не в docs.

---

## Superuser: `sdm`

- **Username**: `sdm`
- **Role**: superuser + ADMIN (bypass всех policy checks через `is_superuser=True` shortcut в `policy.engine.decide()`)
- **Password**: хранится в Claude memory system (feedback_staging_sdm_credentials.md)
- **Use case**: admin UI, manual debugging, settings management.

**Limitation для policy validation**: superuser → `matched_effect=superuser_allow` → не проверяет role-based rules. **Невозможно validate actual policy rules через sdm.**

---

## QA Manager: `qa_manager` ⭐ NEW 2026-04-22 (W2.1.3a)

- **Username**: `qa_manager`
- **User ID**: 53
- **Email**: `qa_manager@test.groupprofi.local`
- **Role**: `MANAGER` (`User.Role.MANAGER`)
- **Branch**: `ekb` (Екатеринбург)
- **Flags**: `is_active=True`, `is_staff=False`, `is_superuser=False` ⚠️ важно для proper role enforcement
- **Password**: `/etc/proficrm/env.d/staging-qa-user.conf` (mode 600, owner root)
- **Created**: 2026-04-22 в рамках W2.1.3a (Q17 setup).

### Purpose

**Validate `@policy_required` decorators работают для non-superuser roles.**

Staging ранее имел только superuser Dmitry, что делало policy rules validation невозможным (superuser bypass shortcut). Этот account позволяет:
- Hit `/admin/`, `/settings/*` — ожидать `403` denial.
- Hit `/companies/`, `/tasks/`, `/companies/<id>/edit/` — должно работать для manager (с branch scope).
- Verify что `decide()` engine actually применяет role rules, а не просто allows all.

### Usage patterns

#### Django shell (inside container)

```python
from django.test import Client
import os
c = Client(HTTP_HOST="crm-staging.groupprofi.ru")
c.login(username="qa_manager", password=os.environ.get("STAGING_QA_MANAGER_PASSWORD"))
r = c.get("/admin/", secure=True)
# Expected: 302 (admin_login redirect — not staff)
```

Run from server:
```bash
ssh root@5.181.254.172 "source /etc/proficrm/env.d/staging-qa-user.conf && \
  cd /opt/proficrm-staging && \
  docker compose -f docker-compose.staging.yml -p proficrm-staging exec -T \
    -e QA_PASS=\"\$STAGING_QA_MANAGER_PASSWORD\" web python manage.py shell -c '...'"
```

#### Playwright E2E

```python
import os
USER = os.getenv("STAGING_QA_MANAGER_USERNAME", "qa_manager")
PASS = os.getenv("STAGING_QA_MANAGER_PASSWORD")  # REQUIRED, fail if missing
assert PASS, "STAGING_QA_MANAGER_PASSWORD env var required for qa_manager tests"
```

Set в CI: GitHub Secret `STAGING_QA_MANAGER_PASSWORD` (same value as server env file).

### Login verification (2026-04-22)

- `Client.login("qa_manager", ...)` → `True` ✅
- `GET /` (dashboard) → `200` ✅
- `GET /admin/` → `302` (admin login redirect — correct, не staff) ✅
- `GET /settings/` → `200` (это user preferences, не admin settings) ✅
- `GET /companies/` → `200` (manager visibility scope applies) ✅

### Observation для W2.1.3b

Manager account позволит deep-validation следующих endpoints:
- Are `/companies/<other-branch-id>/edit/` denied? (expect 403)
- Does `contact_create` require company edit permission? (expect enforced)
- Are admin-only settings paths truly blocked? (expect 403)

---

## Future accounts (as needed, W2 middle)

По необходимости создавать:
- `qa_branch_director_ekb` — BRANCH_DIRECTOR role, validate branch scope powers.
- `qa_sales_head_ekb` — SALES_HEAD role, similar.
- `qa_group_manager` — GROUP_MANAGER, validate cross-branch access.
- `qa_tenderist` — TENDERIST role (restricted, validate blocking).
- `qa_tyumen_manager`, `qa_krasnodar_manager` — cross-branch manager tests (expect deny для other-branch resources).

Не создаём pre-emptively — только когда test случай появится.

---

## Security notes

- **qa_manager credentials — staging only**. НЕ creating на prod.
- **Rotate password** при compromise suspicion (через `u.set_password()` + update env.d file).
- **Audit trail**: user creation logged в `accounts_user` table; subsequent logins → session table.
- **Revocation**: `u.is_active = False` (soft), или `u.delete()` (hard). Delete preferred since test account.

---

## Pre-W9 prep note

Для W9 prod deploy этот account **НЕ переносим на prod**. Prod validation использует real users per W9.10 plan. QA staging account остаётся staging-only.
