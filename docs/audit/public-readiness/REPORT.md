# Public Repo Readiness Report — proficrm

**Scan date**: 2026-04-21
**Repo**: `darbyhtml/proficrm`
**Commits scanned**: 2267 (full history from 2025-12-17 `59b3782c` to 2026-04-21 `275b2ad1`)
**Scan host**: staging VPS (Docker with gitleaks v8.21 + trufflehog latest)
**Raw scan outputs**: `/tmp/scan-results/` на staging (не committed — содержат raw secret values)

---

## Verdict

### 🟡 CLEAN WITH CAVEATS

**Не 🔴 NOT READY** — critical leaks локализованы в **2 recent documentation files**, легко fix'аются через history rewrite + ротация ключей GlitchTip. Не требует переписывания большой доли истории.

**Estimated cleanup effort**: 2-4 часа.

---

## Findings summary

| Scanner | Findings | High | Medium | Low/FP |
|---------|----------|------|--------|--------|
| gitleaks | 29 | **1** | 0 | 28 |
| trufflehog | 4 | 0 | 0 | 4 (all placeholder matches) |
| detect-secrets baseline | 26 | 0 | 0 | 26 (test fixture passwords) |
| Custom patterns | 6 | **2** | **4** | 0 |
| PII / phones | ~12 uniques | 0 | 1 (possible client phones) | 11 (test dummies) |
| Business terms | ~350 mentions | 0 | 1 (GroupProfi identifiers) | 0 |

---

## 🔴 HIGH severity — must fix before public

### 1. Live GlitchTip DSN (staging) в docs

| Field | Value |
|-------|-------|
| File | `docs/audit/glitchtip-dsn-mapping.md:7` |
| Added in commit | `a30689fc` (2026-04-20, `feat(w0.4): wire GlitchTip DSN staging + prod`) |
| Type | Sentry/GlitchTip DSN (project-level client key) |
| Masked | `https://<32-hex-chars>@glitchtip.groupprofi.ru/1` |
| Hash (SHA256:12) | see gitleaks-summary.md finding #1 |
| Still active? | **YES — active** (используется staging `/opt/proficrm-staging/.env` на deploy коммите) |

**Impact**: Anyone знающий DSN может отправлять fake error events в GlitchTip project #1 от имени CRM Staging. Не даёт read access, но:
- Может spam issues list.
- Может исчерпать event quota (free tier limit).
- Может создать false positives, hiding real errors.

**Action required**:
1. Open https://glitchtip.groupprofi.ru/groupprofi/settings/projects/crm-staging/keys/ → Revoke current key → Create new.
2. Update `SENTRY_DSN` in `/opt/proficrm-staging/.env` → restart staging containers.
3. Remove file from git history (see §Cleanup procedure).

### 2. Live GlitchTip DSN (prod) в docs

| Field | Value |
|-------|-------|
| File | `docs/audit/glitchtip-dsn-mapping.md:8` |
| Added in commit | `a30689fc` (2026-04-20) |
| Type | Sentry/GlitchTip DSN (project-level client key) |
| Masked | `https://<32-hex-chars>@glitchtip.groupprofi.ru/2` |
| Still active? | **YES** (в prod `.env` после W0.5a sync — или уже сейчас если pending key был wired) |

**Impact**: Same как #1, но для prod project — больше риск influence на production operations.

**Action required**: Same как #1, for `crm-prod` project.

### 3. Live GlitchTip SECRET_KEY prefix (first 40 chars of 67-char key)

| Field | Value |
|-------|-------|
| File | `docs/audit/glitchtip-500-diag.md:85` |
| Added in commit | `6eeb585d` (2026-04-20, `fix(w0.4): GlitchTip login 500 — Redis unreachable`) |
| Type | GlitchTip server SECRET_KEY (Django SECRET_KEY equivalent for GlitchTip instance) |
| Masked | `GLITCHTIP_SECRET_KEY=<40-chars-of-67-displayed-then-truncated-with-3dots>` |
| Still active? | **YES** — used в `/etc/proficrm/env.d/glitchtip.conf`, signs sessions+auth tokens |

**Impact**: 40 of 67 chars leaked. Bruteforcing remaining 27 chars still ~2^162 operations (infeasible). **BUT**:
- Prefix уникально identifies this key.
- Если будущий leak revaels remaining 27 chars — полная compromise admin sessions.
- Best practice: rotate anyway when identifying prefix leaked.

**Action required**:
1. SSH prod: `python3 -c 'import secrets; print(secrets.token_urlsafe(50))'` → new key.
2. Update `/etc/proficrm/env.d/glitchtip.conf` (mode 600).
3. Restart GlitchTip: `docker compose -f docker-compose.observability.yml restart glitchtip-web glitchtip-worker`.
4. Remove file from git history.

---

## 🟡 MEDIUM severity — consider before public

### 4. Telegram chat_id + bot username exposed в 7+ files

Identifiers:
- `1363929250` (owner's personal Telegram chat ID)
- `@proficrmdarbyoff_bot` (bot username)

Files containing these references:
- `docs/audit/existing-monitoring-inventory.md`
- `docs/audit/telegram-bot-inventory.md`
- `docs/audit/incidents/2026-04-21-staging-502.md`
- `docs/audit/kuma-alert-test-2026-04-21.md`
- `docs/current-sprint.md`
- `docs/open-questions.md`
- `scripts/kuma-bootstrap.py` (comment only)

**Impact**: Owner contact profile identified. Telegram bot can be found and interacted with (but needs token to impersonate, which is NOT in repo).

**Action options**:
- **Keep as-is**: chat_id is a numeric opaque ID, bot username is already public (Telegram directory). Low risk.
- **Sanitize docs**: replace with `<owner-chat-id>` / `<alerts-bot>`. Cleaner for public view.
- **Rotate bot** (optional): create new bot, update `TG_BOT_TOKEN` on prod, revoke old. If old token anywhere leaked — it's not in this repo per scan — rotation is precaution.

### 5. `admin@profi-cpr.ru` hardcoded as VAPID default

| File | `backend/crm/settings.py:534` |
|------|-------------------------------|
| Line | `VAPID_CLAIMS_EMAIL = os.getenv("VAPID_CLAIMS_EMAIL", "mailto:admin@profi-cpr.ru")` |
| Issue | Business admin email exposed as default if env not set |

**Impact**: Email address for contact. Not a credential, but spam/phishing target. Note: domain `profi-cpr.ru` likely related to same owner (GroupProfi business).

**Action**: Replace default with placeholder `mailto:admin@example.com` в settings.py. Require env var to be set explicitly в prod/staging.

### 6. Server IPs exposed в docs (58 mentions)

- `5.181.254.172` — staging/prod VPS (shared)
- `80.87.102.67` — dev test server (na4u.ru)

Files: mostly `docs/runbooks/`, `docs/audit/`, `CLAUDE.md`.

**Impact**: Attack surface identification. Network-level prob targets known.

**Action options**:
- **Keep**: IPs are not secrets by themselves; VPS is behind firewall + IP whitelist + fail2ban + SSH key auth.
- **Sanitize**: `<staging-ip>`, `<prod-ip>`, `<dev-ip>` в docs. Security-best-practice hygiene.

### 7. Possible real customer phones в `backend/amocrm/tests.py`

Phone numbers in amoCRM migration tests:
- `+74956322197` (Moscow area, 9 assertions) — appears in `test_normalize_phone`, may be template.
- `+73453522095` (Tyumen area), `+79829481568`, `+79193377755`, `+79193055510`, `+79123844985` — amoCRM tests specifically.

**Impact**: Tests depend on realistic phone data. These could be **real amoCRM customer phones** copied into tests during development, OR deliberately crafted test patterns.

**Action required (user verification)**:
- Check whether these 5-6 specific numbers были ever real clients in amoCRM.
- If yes: replace with obvious test patterns like `+79991234567`. History rewrite optional depending on severity.
- If no: accept as-is.

---

## ⚫ LOW / acceptable (no action needed)

1. **Test fixture Fernet key** `_TEST_FERNET_KEY = <44-char-base64url>` (backend/mailer/tests.py:1055, backend/ui/tests/test_amocrm_migrate.py:20 — identical value in both, hash `20014bd7b3ec`) — dummy Fernet key for decrypt_str unit tests. Never used on prod. **Safe**.

2. **26 test passwords** in `.secrets.baseline` — detect-secrets pre-commit hook SHA256 hashes of suppressed test fixture passwords (e.g. `"testpassword"` in factory-boy UserFactory). Tests only. **Safe**.

3. **`docker-compose.test.yml`** — `POSTGRES_PASSWORD: testpassword`. CI-only service (not prod/staging compose). **Safe**.

4. **`.github/workflows/ci.yml`** — `DJANGO_SECRET_KEY: ci-secret` for CI test env. **Safe**.

5. **TruffleHog Postgres finding** — `postgres://glitchtip:...@glitchtip-db:5432/glitchtip` в `docs/plan/01_wave_0_audit.md:345` — uses `...` as placeholder, not real password. **FP**.

6. **GlitchTip placeholders** — `GLITCHTIP_SECRET_KEY=$SECRET_KEY` (shell var), `<REPLACE_WITH_SECRETS_TOKEN_URLSAFE_50>` (template). **FP**.

7. **Test emails** `@ex.com`, `@b.com`, `@acme.ru`, `s@e.com` — test fixtures. **Safe**.

8. **Test phones** `+79991234567` (9 uses), `+79991111111`, `+79993333333` — obvious dummy patterns. **Safe**.

9. **ИНН / ОГРН references** — only field name parsing, no actual real ИНН values leaked. **Safe**.

---

## Business data exposure (acceptable if owner OK)

| Term | Occurrences в HEAD | Notes |
|------|--------------------|-------|
| `GroupProfi` | 19 | Business name |
| `groupprofi.ru` | 234 | Business domain (monitoring/email) |
| `Тюмень` | 34 | Regional branch |
| `Екатеринбург` | 23 | Regional branch |
| `Краснодар` | 35 | Regional branch |
| `ЕКБ` | 54 | EKB branch shortcode |
| `Профи` | 13 | Business name (Russian) |
| `profi-cpr` | 1 | Old domain reference (in settings.py default) |

**Business context в public repo**: Name "proficrm" on GitHub will openly associate with GroupProfi company. Docs describe:
- Internal organizational structure (3 regional branches + 6 roles).
- Operational workflows (managers receive calls, escalations to branch directors).
- Business domains (group sales, cargo logistics).
- Technology stack в detail (production Postgres sizing, Celery task names, internal API structure).

**This is a business decision, not a security issue**. Options:
- **Accept exposure** — competitive intelligence not particularly unique.
- **Sanitize** — rewrite docs to use `<Company>`, `<Region-1/2/3>` placeholders. Significant work (~1-2 weeks).
- **Keep repo private** и использовать self-hosted runner / GitHub Enterprise для unlimited Actions.

---

## Cleanup procedure (если user выбирает 🟡 path → public)

### Phase A — Rotate live secrets (~30 min)

```bash
# 1. GlitchTip staging DSN
# Open https://glitchtip.groupprofi.ru/groupprofi/settings/projects/crm-staging/keys/
# → Revoke → Create new → copy DSN
ssh root@5.181.254.172 "vi /opt/proficrm-staging/.env"  # update SENTRY_DSN
ssh root@5.181.254.172 "cd /opt/proficrm-staging && docker compose up -d --force-recreate web celery"

# 2. GlitchTip prod DSN (same процедура для prod project)

# 3. GlitchTip SECRET_KEY
ssh root@5.181.254.172 "
  NEW_KEY=\$(python3 -c 'import secrets; print(secrets.token_urlsafe(50))')
  # Update /etc/proficrm/env.d/glitchtip.conf: GLITCHTIP_SECRET_KEY=\$NEW_KEY
  docker compose -f /opt/proficrm-observability/docker-compose.yml restart glitchtip-web glitchtip-worker
"
```

### Phase B — History rewrite (~30 min)

Install [git-filter-repo](https://github.com/newren/git-filter-repo):
```bash
pip install git-filter-repo
```

Remove 2 leaked files from **entire history**:
```bash
# Make backup clone first
git clone --mirror . /tmp/proficrm-backup.git

# Filter
git filter-repo \
  --path docs/audit/glitchtip-dsn-mapping.md \
  --path docs/audit/glitchtip-500-diag.md \
  --invert-paths --force

# Verify secrets gone — run scan again on filtered repo
docker run --rm -v "$(pwd):/repo" zricethezav/gitleaks:latest detect \
  --source=/repo --log-opts="--all" --no-banner
# Expected: 0 leaks в docs/audit/glitchtip-*
```

### Phase C — Force-push (destructive — coordinate!) (~5 min)

```bash
# Push rewritten history
git push --force --all
git push --force --tags

# WARNING: any collaborators (if any) будут need to re-clone.
# В текущем setup — только darbyhtml — единственный coauthor.
```

### Phase D — Optional Medium cleanup (~1-2 hours)

1. Replace `profi-cpr.ru` default in `backend/crm/settings.py` с `example.com`.
2. Sanitize `scripts/kuma-bootstrap.py` chat_id comment to `<owner-chat-id>`.
3. Optional: rotate TG bot token (user action via @BotFather).
4. Optional: batch-replace IP addresses в docs с placeholders.
5. User check phones in `backend/amocrm/tests.py` — replace if real.

### Phase E — Make public

GitHub Settings → General → Danger Zone → Change repository visibility → Public.

GitHub Actions now unlimited. CI automation восстановлено (also resolves Q12 billing issue — public repos don't have billing limits).

---

## Alternatives if user not comfortable with public

1. **Keep private, pay GitHub**: Increase spending limit OR fix payment method. $4/user/month for Team plan = unlimited private repo Actions minutes. ~$48/year.

2. **Self-hosted runner on staging VPS**: Install GitHub Actions self-hosted runner in Docker. Free, keeps repo private, CI resumes.

3. **Migrate to GitLab / Forgejo / Gitea**: Self-hosted git server. Free, unlimited CI. Migration effort ~1 day.

All three preserve current setup without cleanup work.

---

## Summary for user decision

| Option | Effort | Security | Cost |
|--------|--------|----------|------|
| **Public (recommended)** | 2-4 hours (Phase A-C) + optional (Phase D) | ✅ All live secrets rotated, HIGH findings removed | $0 — unlimited CI |
| Private + spending limit | 5 min user action | ✅ Secrets stay in private repo | ~$48/year GitHub Team |
| Private + self-hosted runner | 2-4 hours setup | ✅ Self-hosted | $0 |
| Private + migrate GitLab | 1 day | ✅ Change provider | $0 |

**Technical recommendation**: **🟡 Go public after cleanup** — cleanup work is bounded and repo becomes permanently useful as portfolio reference, plus GitHub Actions benefits.

**Business recommendation**: Only if owner comfortable с public visibility of business name + operational structure. Otherwise → private + self-hosted runner.
