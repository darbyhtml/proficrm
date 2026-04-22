# 2FA URL 404 re-diagnosis — Deploy step 4-6 silent skip

**Session date**: 2026-04-22.
**User report**: `/accounts/2fa/setup/` returns 404 in browser despite CI+deploy "success".

---

## ROOT CAUSE

**`deploy-staging.yml` script silently exits after step 3 (Migrate).** Steps **4-6 (force-recreate, nginx restart, smoke) never executed** для последних 2 deploys (`b8a235d4` и `48c334c7`). GitHub Actions reports "success" because script exits with code 0, но фактически container НЕ recreated.

Consequences:
- `git pull` (step 1) works — files on disk updated.
- `docker build` (step 2) works — new image created.
- `migrate` (step 3) works — no migrations to apply.
- **Container НЕ recreated** — old gunicorn workers running pre-deploy Python код.
- Django urlconf в memory NOT includes `2fa/setup/` + `2fa/verify/` routes.
- `/accounts/2fa/setup/` → Django 404 (URL pattern not matched в cached urlconf).

---

## Evidence per diagnostic step

### Step 1: Django internal test (fresh Python shell)

```
Django internal (no HOST):     400  ← DisallowedHost, expected
Django internal (HOST+secure): 200  ← /accounts/2fa/setup/ works in fresh Python
```

`reverse('totp_setup')` = `/accounts/2fa/setup/` in fresh shell ✅

### Step 2: External curl (через gunicorn)

```
status: 404
size: 143198  ← Django 404 page (full template rendered)
```

Body contains `<title>404 — Страница не найдена</title>` — Django 404, not nginx.

### Step 3: URL introspection (fresh shell)

```
reverse totp_setup: /accounts/2fa/setup/
resolve match: <function totp_setup> url_name: totp_setup
route: accounts/2fa/setup/ → name=totp_setup
route: accounts/2fa/verify/ → name=totp_verify
```

URL routes correctly registered в urlconf **when freshly imported**.

### Step 4: Container age + process state

```
crm_staging_web    Up 23 hours (healthy)    Created 2026-04-21 09:57:01 UTC
```

**Container НЕ перезапускался со вчера**. Deploys (08:04 + 08:22 UTC сегодня) **did not recreate container**.

Gunicorn processes:
- PID 24, 25, 27 (master + original workers, container boot time).
- PID 10981, 8950 (recycled workers after `--max-requests 1000`).

Files on disk dated 2026-04-22 08:04 UTC (updated by git reset in step 1 of deploy). Workers running in-memory code loaded 23h назад, before 2FA files existed.

### Step 5: Deploy script log analysis — **smoking gun**

Log for latest deploy (`48c334c7`, run id 24768267732):

```
08:21:57 === [1/6] git fetch + reset to origin/main ===
08:21:59 Новый HEAD: 48c334c7... (было: b8a235d4...)
08:21:59 === [2/6] Build образов ===
08:22:02 celery Built, celery-beat Built, web Built, websocket Built
08:22:02 === [3/6] Migrate ===
08:22:06 Operations to perform: Apply all migrations...
08:22:06 No migrations to apply.
08:22:07 Complete job  ← bash exited here!
```

**Steps 4, 5, 6 never echoed**. Bash heredoc terminated ~1 second after `manage.py migrate` returned.

Same pattern для предыдущего deploy (`b8a235d4`):
```
08:04:23 === [1/6] git fetch ... ===
08:04:25 === [2/6] Build ... ===
08:07:04 === [3/6] Migrate ===
08:07:08 Complete job  ← same silent exit
```

### Step 6: Nginx logs (just for completeness)

```
08:26:43 "GET /accounts/2fa/setup/ HTTP/1.0" 404 143198
```

Nginx route корректно передаёт запрос к web upstream. 404 comes from Django, not nginx.

### Step 7: Web container logs

```
11:26:43 MSK "GET /accounts/2fa/setup/ HTTP/1.0" 404 143198
```

Gunicorn worker сам returns 404 (not 302 login redirect). Meaning: URL pattern doesn't match → Django's view dispatcher doesn't reach `@login_required` check → returns URL-not-found 404.

---

## Why deploy script exits silently after step 3

**Hypothesis**: `docker compose run --rm web python manage.py migrate` **consumes stdin** from parent bash heredoc (`<< 'REMOTE'`). После `run --rm` returns, heredoc stdin буфер пустой → bash reads EOF → script terminates prematurely.

Evidence: 
- Works perfectly на local (where heredoc/stdin handled differently).
- On GH Actions SSH'ing to remote server — `docker compose run` inherits stdin from SSH session.
- `docker compose run -T` flag disables pseudo-TTY allocation, preserves stdin for parent.

Similar pattern reported in multiple `docker compose` + bash heredoc combos online. The fix is well-known:

```bash
docker compose run --rm -T web python manage.py migrate --noinput
                       ^-- explicit no-TTY
```

Or:
```bash
docker compose run --rm web python manage.py migrate --noinput </dev/null
                                                               ^-- explicit no-stdin
```

---

## Impact assessment

Deploys affected (both had non-trivial code changes skipped from recreate):
- **b8a235d4** (2026-04-22 08:04) — W2.2 2FA infrastructure commit. Contains `views_2fa.py`, `middleware_2fa.py`, urls.py updates. Files updated in `/opt/proficrm-staging/backend/`, но gunicorn не reloaded → 2FA endpoints unreachable externally.
- **48c334c7** (2026-04-22 08:22) — diagnostic docs только, behavioral impact nil.

Prior deploy (from yesterday 2026-04-21) actually recreated container (current 23h uptime starts there). So bug is **recent regression** или was always present but prior deploys happened to include migrate failure или similar trigger.

---

## Blocker effect

- **W2.2 2FA user setup blocked** — URL unreachable even though code correct.
- All **subsequent deploys affected** — container never recreated, Python import state permanently stale.
- Only rescue: manual `docker compose up -d --force-recreate web` OR fix deploy script + redeploy.

---

## Recommended fix (user decides)

### Option A — Immediate rescue (unblock 2FA setup)

Manual commands on staging (no code change):
```bash
ssh root@5.181.254.172
cd /opt/proficrm-staging
docker compose -f docker-compose.staging.yml -p proficrm-staging up -d --force-recreate web celery celery-beat websocket
docker restart crm_staging_nginx
sleep 30
bash tests/smoke/staging_post_deploy.sh
```

After this, 2FA endpoints become reachable externally.

### Option B — Proper fix (prevent recurrence)

Edit `.github/workflows/deploy-staging.yml` line ~97:
```diff
  docker compose -f docker-compose.staging.yml -p proficrm-staging \
-   run --rm web python manage.py migrate --noinput \
+   run --rm -T web python manage.py migrate --noinput \
    || rollback "migrate failed"
```

Add `-T` flag (disable pseudo-TTY). Commit + push. Next deploy will run all 6 steps.

Likely also worth adding `</dev/null` as defense-in-depth:
```
  run --rm -T web python manage.py migrate --noinput </dev/null
```

### Option C — Both

Apply A (unblock user) + B (fix recurrence). Recommended.

---

## Session artifacts

- Docs only: this file.
- Zero code changes.
- Zero prod touches.
- W2.2 2FA code NOT affected — всё корректно committed + pulled + files on disk. Only missing step: container restart.

---

## Meta: reporting "success" misleadingly

GitHub Actions workflow UI reports **"success"** для both bad deploys. This is misleading. Ideally workflow should fail if expected `=== [6/6] ===` output не emitted. Can add validation:

```bash
# End of script
if [ -z "$FINAL_MARKER_SEEN" ]; then
  exit 99
fi
```

Or simpler: `trap 'echo EXITING_AT_$LINENO' EXIT` в начале heredoc — видно точный line где exit случился.

Это W2/W3 debt item, не blocker.
