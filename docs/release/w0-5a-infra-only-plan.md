# W0.5a-infra-only Deploy Plan — DEFERRED until W9

**Status**: 🛑 **DEFERRED 2026-04-21**
**Decision**: Path E (defer all prod deploy until W9 UX volna)
**Reason**: Current редизайн не final-approved менеджерами; W9 will do complete redesign review.
**Next prod deploy target**: After W9 completion (estimated 3-5 months, 2026-07 to 2026-09).

Full ADR: `docs/decisions/2026-04-21-defer-prod-deploy-to-w9.md`.

---

## Why deferred (summary)

After Session D1 diagnostic revealed UI_V3B_DEFAULT flag **не wired** в views + legacy templates **deleted в-place**, user rejected all 4 original paths (A/B/C/D):
- **Path A/D** (accept UX change + training): user — редизайн ещё не final.
- **Path B** (2-недели legacy-compat dev): would be discarded after W9 redesign.
- **Path C** (selective revert): too risky — templates modified in-place.

**Path E** added by user: freeze prod until W9 completes full UX review. Single "release milestone" at end of W9.

---

## Original plan content preserved below for W9 reference

---

**Session D1** (2026-04-21): diagnostic session только, no prod touch, no deploy.

---

## Goal (original)

Deploy main на prod с ALL feature flags OFF. Менеджеры видят старый UI. Observability, tooling, middleware active.

## Revised understanding (после Session D1)

Original plan assumed `UI_V3B_DEFAULT=OFF → legacy UI`. Diagnostic revealed:

- `UI_V3B_DEFAULT` flag **определён** в `core/feature_flags.py`, но **не wired** в actual views.
- Legacy templates `company_list.html`, `dashboard.html` **deleted** в main (replaced _v2).
- `base.html` + `company_detail.html` **modified in-place** (не parallel legacy).
- Deploy main на prod = **automatic UX activation** regardless of flags.

Full analysis: `docs/audit/legacy-templates-check-2026-04-21.md`.

---

## Status checklist (Session D1)

- [x] Q12 SSH key — **user rotated 2026-04-21 11:10 UTC**. Auto-deploy works (SSH auth OK).
- [x] CI pipeline: все 8 jobs success на commit `0348ded7`, `480f56b5`.
- [x] Deploy-staging workflow: fixed `git pull` → `reset --hard` (post-filter-repo divergent-branch issue). Commit `480f56b5`.
- [x] Staging auto-sync: verify after `480f56b5` push (see final section).
- [x] Legacy templates check: **BROKEN** (R-A/R-D/R-C options documented).
- [ ] Live-chat «Скоро» nav button: **SKIPPED** per user gate «only if Step 2 verdict = OK».
- [x] Prod pre-flight state: captured (Section «Prod state snapshot»).
- [ ] Migration dry-run: pending user decision on path.

---

## Prod state snapshot (2026-04-21 Session D1)

### Git
- HEAD: `f015efb1` (2026-03-20, `Fix(Contacts): email-валидация`).
- main ahead: ~448+ commits (`f015efb1..origin/main` = `f015efb1..480f56b5`).

### Infrastructure
| Resource | Value | Status |
|----------|-------|--------|
| Disk `/` | 26 GB free (66% used) | ✅ |
| RAM | 3232 MB available (used 4845/8078) | ✅ |
| Swap | 1713/2047 MB = 83% | ⚠️ high but not critical |
| `proficrm-web-1` | Up 28 hours | ✅ |
| `proficrm-db-1` | Up 28 hours (healthy) | ✅ |
| `proficrm-redis-1` | Up 5 weeks (healthy) | ✅ |
| `proficrm-celery-1` | Up 28 hours **(unhealthy)** | ⚠️ hotlist #9 — fixes с deploy |
| `proficrm-celery-beat-1` | Up 28 hours | ✅ |
| `proficrm-websocket` | **ОТСУТСТВУЕТ** | будет создан при deploy |
| Prod HTTP | `/health/` = 200 | ✅ |

### `.env` vars state
| Var | Status |
|-----|--------|
| `SENTRY_DSN` | ✅ set (66 chars, new post-rotation) |
| `SENTRY_ENVIRONMENT=production` | ✅ |
| `DJANGO_SECRET_KEY` | ✅ set |
| `DJANGO_ALLOWED_HOSTS=crm.gr...` | ✅ |
| `DJANGO_DEBUG=0` | ✅ |
| `REDIS_URL=redis://redis:6379/0` | ✅ |
| `MAILER_FERNET_KEY` | ✅ set |
| `TG_BOT_TOKEN` | ✅ set |
| `TG_CHAT_ID=1363929250` | ✅ |
| `VAPID_CLAIMS_EMAIL=mailto...` | ✅ |
| `DATABASE_URL` | ⚠️ MISSING (но Django uses POSTGRES_* individually — не блокер) |
| `ADMIN_EMAIL` | ⚠️ MISSING (settings.py has `admin@example.com` default after cleanup) |
| `STAFF_DEBUG_ENDPOINTS_ENABLED` | ⚠️ MISSING (OK, defaults False — prod безопасен) |
| `MESSENGER_ENABLED` | ⚠️ MISSING (OK, defaults False — messenger UI hidden) |
| `VAPID_PUBLIC_KEY` / `VAPID_PRIVATE_KEY` | ⚠️ MISSING (push notifications won't work — graceful degradation) |
| `DKIM_PRIVATE_KEY` / `DKIM_SELECTOR` | ⚠️ MISSING (email DKIM disabled — graceful degradation) |

### Backups
- `/var/backups/postgres/`: **нет** (directory не exists).
- `/opt/proficrm/backups/`: только `crm_20260315_111600.sql.gz` (271 MB, **5 недель назад** — slишком старо для rollback).
- `/root/backups/`: свежие from public-readiness cleanup (W0.4 secret rotations).

**Pre-deploy action required**: fresh pg_dump + media tar + .env backup.

---

## Plan fork: User chooses path

### Path A: Accept UX change as part of deploy (RECOMMENDED, simplest)

**Premise**: Nothing можно отменить UX change без W0.5b flag-wiring work. Better to embrace it.

**Pre-deploy** (~2-3 часа user work):
1. 1-2 часа demo sessions с менеджерами на staging — explain new UI paths.
2. Screenshot before/after comparison docs для reference.
3. FAQ на внутренней wiki: "Где теперь кнопка X?".

**Deploy window**: night low-load (e.g. 2-4 AM MSK).

**Procedure** (next session with `DEPLOY_PROD_TAG=release-v1.0-w0-infra` + `CONFIRM_PROD=yes`):
1. Fresh snapshot: `pg_dump` + `tar /opt/proficrm/media/` + `cp .env`.
2. Create tag `release-v1.0-w0-infra` on current main.
3. Prod: `git fetch --tags && git checkout release-v1.0-w0-infra`.
4. `docker compose build web celery celery-beat websocket`.
5. Migrations: `docker compose run --rm web python manage.py migrate --noinput`. (Expect ~50 pending, may take 5-15 min on 9.5M ActivityEvent table.)
6. `docker compose up -d --force-recreate web celery celery-beat websocket`.
7. `docker restart proficrm-nginx` (if host-level exists).
8. Smoke: `make smoke-prod` (requires tests/smoke/prod_post_deploy.sh).
9. First-hour monitoring: GlitchTip issue rate, Uptime Kuma, Telegram.
10. 24-hour monitoring.

**Expected downtime**: 10-20 min (migrations dominated).

**Rollback**:
- Immediate (< 5 min after issue): `git checkout release-v0.0-prod-current` + `docker compose up -d --force-recreate web celery celery-beat`.
- DB restore (if migrations destructive): from snapshot step 1.

### Path B: Wire up `UI_V3B_DEFAULT` flag properly (W0.5b — expensive)

**Effort**: 1-2 weeks.

1. Restore legacy `company_list.html` + `dashboard.html` из prod HEAD в parallel.
2. Create `_v3b` variants для modified templates (`company_detail.html`, `base.html`).
3. Views: `if flag_is_active("UI_V3B_DEFAULT"): render v3b else legacy`.
4. Tests на обоих modes.
5. Deploy main → flag OFF → legacy UI.
6. Gradual activation per-user later.

Не scope этой сессии. Plan отдельно, отдельный Q-issue.

### Path C: Selective template revert (3-6 hours, risky)

Revert ТОЛЬКО user-visible templates к prod HEAD на release branch:
```bash
git checkout -b release/w0-5a-ui-revert origin/main
git checkout be569ad4 -- backend/templates/ui/base.html backend/templates/ui/company_detail.html backend/templates/ui/company_list.html backend/templates/ui/dashboard.html
```

Risk: fresh views reference new template names / new context vars → TemplateSyntaxError. Manual adjustment требуется. Not recommended.

### Path D: Rollback-capable accept (compromise — light work)

Same как Path A, but heavier monitoring + immediate rollback готовность:
- Path A steps 1-10.
- Plus: PagerDuty-like watch 24h.
- Plus: managers briefed что «24h observation window» + contact channel для issues.
- Plus: если any manager reports confusion — rollback w/o hesitation + manual training + re-deploy.

---

## Recommendation

**Path A** (с hints от Path D для safety net). Embrace UX change + prepare managers.

**Path B** correct long-term, но expensive — defer в separate W0.5b wave if user really хочет preserve legacy UI (e.g. for regulatory compliance / contract terms).

---

## User decision required

**Choose**: A (accept + manager pre-training) / B (flag wiring — next wave) / C (selective revert — risky) / D (accept + heavy monitoring).

After user choice — next session запускает выбранный path.

---

## What's in main right now (after Session D1)

Commits on main post-public toggle (reverse chronological):
- `480f56b5` fix(ci/deploy-staging): reset --hard instead of pull.
- `0348ded7` chore: verify auto-deploy pipeline after SSH key rotation.
- `37438fe7` docs(q12): SSH key broken — new blocker found after CI green. **(Q12 addendum 2 — now RESOLVED)**.
- `2c6224ac` fix(ci): lower coverage fail_under 50 → 45 temporarily + Q14/Q15.
- `3ead21cd` fix(tests+docs): MESSENGER_ENABLED=True в ContactedBackActionTests + lesson #5.
- `998fff8e` fix(ci): unblock pipeline (pygraphviz + black + bandit + ruff debt).
- `ce55fff4` docs(process): lesson #4 + Q12 addendum (billing identified).
- `3272b482` chore: trigger CI after public visibility toggle.
- `74a18dc3` audit(public-readiness): deep scan of secrets, PII, sensitive data.
- ... (earlier W0.4 / SEV2 / classification commits).

All infrastructure changes ready. Only UX gate issue blocks proper deploy.

---

## Next session triggers

| User prompt pattern | Session scope |
|---------------------|---------------|
| «Deploy W0.5a Path A — night window tonight» + `DEPLOY_PROD_TAG=release-v1.0-w0-infra` + `CONFIRM_PROD=yes` | Fresh snapshot + tag + prod deploy + 24h monitoring |
| «Start W0.5b flag wiring» | 1-2 week work, separate milestone |
| «Try Path C ui-revert» | 3-6 hour investigation + selective revert branch |

**Current session end state**: staging sync awaits `480f56b5` deploy. Monitor running.
