# ADR: Defer all prod deploys until W9

**Date**: 2026-04-21
**Status**: Accepted
**Author**: darbyhtml (user)
**Recorded by**: Claude Code (Session D1 closeout)

---

## Context

После завершения W0.0-W0.4 (tooling, audit, feature flags, observability) на staging, первый prod sync (W0.5a) был запланирован. Classification 446 коммитов между prod HEAD (`f015efb1`, 2026-03-20) и main HEAD (W0.4 closeout) выявила:

- **229 коммитов (51%) — UX-gated**: полностью visible переработки (F4 R3 v3/b карточка компании, live-chat redesign, dashboard Notion-стиль, widget public changes).
- **71 коммит (16%) — feature-flagged**: за `MESSENGER_ENABLED` flag OFF by default на prod.
- **146 коммитов (33%) — prod-safe** (ops, refactor, trivial, observability).

Session D1 diagnostic (2026-04-21) revealed критическую issue с flag plan:
- `UI_V3B_DEFAULT` flag был создан в W0.3 **только в `core.feature_flags` module**, но НЕ wired в actual views (grep `flag_is_active("UI_V3B_DEFAULT")` в `backend/ui/views/` → 0 matches).
- Legacy templates `company_list.html`, `dashboard.html` **удалены** в main (replaced in-place by `_v2` versions).
- `base.html` (+1013 lines) и `company_detail.html` (+635 lines) — modified in-place (not parallel legacy).
- **Deploy main на prod = automatic UX activation**, flag не controls anything.

Детали: `docs/audit/legacy-templates-check-2026-04-21.md`.

User explicitly stated (2026-04-21):
- Current редизайн **experimental**, не final-approved менеджерами.
- W9 (UX volna) scheduled for full redesign review — visuals может кардинально поменяться.
- Does not want dev effort on legacy-compat (Path B, 1-2 weeks) that will be discarded post-W9.
- Managers продолжают использовать prod в текущем состоянии (Mar 2026 `f015efb1`) until W9.

---

## Decision

**Defer all prod deploys until после W9 completion**.

W0.5a-infra-only plan (originally proposed) — cancelled.
Paths A/B/C/D (explored in Session D1) — all rejected.
Path E — new, chosen: freeze prod until W9 UX volna завершит full redesign review.

---

## Consequences

### Positive

- **No prod deploy pressure для W0.5–W8**. All волны работают staging-only без рисков для менеджеров.
- **W9 becomes single "release milestone"** — один training event для менеджеров вместо multiple.
- **Claude Code can experiment broadly на staging** без compromise prod stability.
- **Large refactors** (W1 company_detail extraction, W2 schema security ENFORCE, W3 core CRM, W4-W8) land безопасно на staging.
- **Waffle feature flags** получают validation time на staging (test cases для flag-wiring появятся естественно).
- **Monitor tooling matures** на staging без prod impact.

### Negative

- **Prod observability blind** — GlitchTip SDK не deployed на prod, ошибки видны только через `ErrorLog` модель + manager-reported Telegram alerts. Период ~3-5 месяцев.
- **Prod remains на старом коде** (`f015efb1`, 2026-03-20) весь W1-W8 cycle.
- **Celery healthcheck broken** на prod (hotlist #9) — cosmetic, не блокер.
- **Tech debt accumulates** для single massive deploy в W9 — возможно 600-800 commits worth changes.
- **W9 deploy risk амплифицируется** — больше коммитов = больше unknown interaction между features.
- **GlitchTip DSN ротирован** (post-public-readiness cleanup) но не используется prod sentry_sdk.init() — env var лежит безвредно.

### Mitigations

- **Uptime Kuma** (self-hosted, 3 monitors на `crm.groupprofi.ru/health/`, `crm-staging`, `glitchtip.groupprofi.ru`) — continues external monitoring prod.
- **`scripts/health_alert.sh`** cron `*/5 * * * *` на prod VPS — continues local health checks → Telegram (`@proficrmdarbyoff_bot`).
- **Django `ErrorLog`** модель + `ErrorLoggingMiddleware` continue capturing exceptions server-side (без Sentry).
- **Point fixes on prod ALLOWED** для:
  - Security CVEs (dependency bumps).
  - Critical prod bugs (manager-reported via Telegram).
  - Infrastructure bumps (postgres/redis security patches).
  Each requires `CONFIRM_PROD=yes` explicit marker в промпте. Not routine main sync.
- **W9 plan обязан include** detailed prod deploy runbook covering accumulated changes — отдельный stage W9.10 «Accumulated Prod Deploy».

---

## Alternative paths considered (and rejected)

### Path A — Accept UX change + manager pre-training

Rejected: user hasn't finalized редизайн. Training на non-final UI = wasted training effort.

### Path B — Wire `UI_V3B_DEFAULT` flag properly (preserve legacy templates parallel)

Rejected: 1-2 weeks dev effort. Work would be **discarded** when W9 рerdesigns UI again.

### Path C — Selective template revert (cherry-pick legacy из `be569ad4`)

Rejected: too risky. Templates modified in-place → fresh views reference new template names и new context variables. `TemplateSyntaxError` / `VariableDoesNotExist` likely on legacy templates с fresh views.

### Path D — Path A + heavy 24h monitoring

Rejected for same reason as A (редизайн не final).

---

## References

- `docs/release/classification-summary.md` — 446 commit classification.
- `docs/release/w0-5a-infra-only-plan.md` — original plan (now DEFERRED).
- `docs/release/classification-reviewed.csv` — per-commit source of truth.
- `docs/audit/legacy-templates-check-2026-04-21.md` — diagnostic revealing flag was dead code.
- `docs/audit/gh-actions-timeline-2026-04-21.txt` — CI/deploy pipeline timeline (billing, SSH rotation).
- `docs/plan/10_wave_9_ux_ui.md` — W9 plan, will incorporate W9.10 «Accumulated Prod Deploy» stage.
- `docs/audit/process-lessons.md` — lessons #4 (never commit live credentials) and #5 (token scope audit after visibility change).

---

## Revisit trigger

Reopen this ADR для discussion если:

- **W9 scope changes**: if UX review reveals редизайн works as-is, consider partial deploy earlier.
- **Critical security CVE**: requires CVE-only point fix, NOT full sync. Does not invalidate this ADR.
- **Manager explicit request**: managers see staging редизайн и request immediate rollout.
- **Prod outage**: requires emergency sync some subset — handled as incident, not planned deploy.

---

## Implementation

- ✅ W0.5a cancelled (no tag `release-v1.0-w0-infra` created).
- ✅ Prod remains на `release-v0.0-prod-current` tag (`be569ad4`, Mar 2026).
- ✅ CLAUDE.md §«Prod freeze until W9» section added.
- ✅ `docs/plan/10_wave_9_ux_ui.md` — W9.10 Accumulated Prod Deploy stage added.
- ✅ `docs/current-sprint.md` — W0.5a marked DEFERRED.
- ✅ `docs/open-questions.md` — Q11 closed as RESOLVED.
