# Deploy workflow stdin audit (2026-04-22)

Post-diagnosis of 2FA 404 — audit всех `docker compose run/exec` commands в CI/CD workflows на предмет bash heredoc stdin consumption bug.

---

## `.github/workflows/deploy-staging.yml`

| Line | Command | Context | Has -T? | Has </dev/null? | Fix needed? |
|------|---------|---------|---------|-----------------|-------------|
| 68-69 | `docker compose ... build web celery celery-beat websocket` | rollback() function, heredoc | N/A (build no stdin) | N/A | NO |
| 70-71 | `docker compose ... up -d --force-recreate ...` | rollback() function, heredoc | N/A (up no stdin) | N/A | NO |
| 88-89 | `docker compose ... build web celery celery-beat websocket` | step 2 main flow, heredoc | N/A | N/A | NO |
| **95-96** | **`docker compose ... run --rm web python manage.py migrate`** | **step 3 main flow, heredoc** | **NO** | **NO** | **YES** |
| 103-104 | `docker compose ... up -d --force-recreate ...` | step 4 main flow, heredoc | N/A (up no stdin) | N/A | NO |

### Findings

- **Single vulnerable command**: line 95-96 (migrate).
- Reason: `docker compose run` allocates PTY by default, consumes parent's stdin (bash heredoc `<< 'REMOTE'`).
- After `run` returns, bash reads EOF на next line → script terminates.
- Steps 4-6 (force-recreate, nginx restart, smoke) silently skipped.

### Why `build` и `up -d` не affected

- `build` и `up -d` do not allocate PTY by default.
- They read no interactive stdin — bash heredoc stdin preserved для последующих commands.
- Only `run` (и `exec` w/o `-T`) attach parent stdin к container.

---

## Other workflows

- `.github/workflows/ci.yml` — no `docker compose run/exec` (CI runs tests directly через python, not в containers).
- Makefile — no heredoc+docker+stdin pattern.
- `scripts/run_tests_docker.sh`, `scripts/test.sh` — manual use, not called from SSH heredoc.

**Only `deploy-staging.yml:95-96` needs fix.**

---

## Fix applied (commit следующим)

```diff
- docker compose -f docker-compose.staging.yml -p proficrm-staging \
-   run --rm web python manage.py migrate --noinput \
+ docker compose -f docker-compose.staging.yml -p proficrm-staging \
+   run --rm -T web python manage.py migrate --noinput </dev/null \
    || rollback "migrate failed"
```

Both safeguards applied:
1. **`-T`** — disable pseudo-TTY allocation. Primary fix.
2. **`</dev/null`** — explicit no-stdin redirect. Belt-and-suspenders.

Plus additional hardening:
- **`set -euxo pipefail`** at heredoc start — strict error handling + command tracing.
- **Explicit completion marker** at end: `=== DEPLOY FULLY COMPLETED ===`. If future deploy log doesn't contain marker → alert immediately, не rely на GitHub Actions "success" badge (which was misleading this time).

---

## Impact summary

**Before fix**:
- Deploy runs steps 1-3 (pull, build, migrate).
- Container NOT recreated — gunicorn running with stale Python imports.
- GitHub Actions reports "success" despite partial execution.
- Any code change landing in staging не takes effect until manual container restart.

**After fix**:
- All 6 steps run deterministically.
- Strict mode exits loudly on any step failure.
- Completion marker confirms full execution.
- Safe re-run pattern: idempotent (git reset --hard safe, build/up idempotent, migrate --noinput safe).

---

## Verification plan

1. Apply fix + commit.
2. Push dummy-trigger commit to force deploy workflow re-run.
3. Verify deploy log contains all markers:
   - `=== [1/6] git fetch ... ===`
   - `=== [2/6] Build ... ===`
   - `=== [3/6] Migrate ===`
   - `=== [4/6] Recreate ... ===`
   - `=== [5/6] nginx DNS cache flush ===`
   - `=== [6/6] Post-deploy smoke ... ===`
   - `=== DEPLOY FULLY COMPLETED ===`
4. Verify container uptime reset (< 1 min after deploy) for web/celery/beat/websocket.
5. Smoke + test baseline preserved.
