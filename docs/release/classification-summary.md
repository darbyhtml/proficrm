# Classification of 446 commits: prod (`f015efb1`) → main (`90663bd1`)

**Диапазон**: `git log --no-merges f015efb1..90663bd1` = 446 коммитов.
**Даты**: 2026-02-19 (первый после prod HEAD) → 2026-04-21 (main HEAD).
**Источник истины**: `docs/release/classification-reviewed.csv` (per-commit).

---

## Summary

| Category | Count | % | Disposition |
|----------|-------|---|-------------|
| 🟢 Prod-safe ops | **67** | 15% | **Deploy next release** (infra, CI, config, hardening) |
| 🔵 Prod-safe refactor | **13** | 3% | Deploy per-component batch (unused imports, migration schema fixes) |
| 🟡 Feature-flagged | **71** | 16% | **Deploy safely, activate later** (messenger/widget за MESSENGER_ENABLED, waffle flags) |
| ⚫ Trivial | **66** | 15% | Include with next batch (docs, format, whitespace, .gitignore) |
| 🟠 UX-gated | **229** | 51% | **HOLD** — requires rollout plan + manager training |
| 🔴 Hold | **0** | 0% | (после повторного review 2 false-positive переклассифицированы в 🟡) |
| **TOTAL** | **446** | 100% | |

---

## Категории — определения и критерии

### 🟢 Prod-safe ops (67 commits)

Инфраструктура, tooling, config, CI, security hardening. Не меняет видимое поведение для менеджеров.

**Deploy**: сразу, без feature flags.

Типичные паттерны:
- `Fix(Docker): ...` — containers, healthchecks, Dockerfile changes
- `Harden(Security): Phase N ...` — rate limiting, SSL, CSP, password policy
- `Chore(Wave0.2X): ruff/mypy/pre-commit/black` — linter config, baselines
- `Fix(CI): ...`, `Harden(Deploy): ...`
- Nginx config, settings.py hardening, requirements bumps
- Management commands (opt-in)
- `Revert(X): ...` — undo commits

Ключевые (нужны на prod):
- `242fcf2a` Fix(Docker): Celery healthcheck — drop `-d $HOSTNAME` (**закрывает hotlist #9** — proficrm-celery-1 unhealthy 11+ часов)
- `bea256b0` Same celery healthcheck fix for staging
- `6eeb585d` Fix(w0.4): GlitchTip login 500 (Redis unreachable)

### 🔵 Prod-safe refactor (13 commits)

Внутренний рефакторинг. Behaviour идентичен, covered by tests.

**Deploy**: deploy-safe, но пакетно — каждый refactor batch по component.

Типичные:
- Remove unused imports (`51b7ca7b`, `d43afe8e`)
- Migration schema fixes (`dd23bea4`, `880d4456`)
- Model drift sync (`0c142bec`)

### 🟡 Feature-flagged (71 commits)

Новая функциональность за django-waffle flag или за MESSENGER_ENABLED=1 toggle.
На prod flag OFF → user ничего не видит → deploy безопасен.

**Deploy**: сразу, но **activate позже** через admin после decision.

Ключевые (нужны на prod):
- `397eb85e` **Feat(Observability): Sentry integration (free-tier friendly)** — SDK init + scope setup
- `96286510` **Feat(Core): feature flags infrastructure via django-waffle (Wave 0.3)** — fundament
- `09e1f94e` **Feat(Observability): W0.4 GlitchTip self-hosted + structured logging (part 1)**
- `a30689fc` feat(w0.4): wire GlitchTip DSN staging + prod, deploy Uptime Kuma
- `3cb648ea` Fix(w0.4): feature_flags tag always set

Messenger-related (за MESSENGER_ENABLED) — 40+ коммитов за 2026-04-03...04-04 (W F4 messenger hardening batch):
- Multiple `Harden(Messenger): ...` API validation, auth checks, serializer whitelists
- `Fix(Messenger): SSE ...` — SSE real-time improvements

### ⚫ Trivial (66 commits)

Docs, format, whitespace, .gitignore, README.

**Deploy**: include with next batch, не возвращаются.

Типичные:
- `Docs(Wiki/Audit/Release): ...`
- `docs/current-sprint.md` updates
- `ea72704d` Format(Wave0.2b): initial black pass — 277 files reformatted
- `.gitignore` tweaks
- `CLAUDE.md` updates

### 🟠 UX-gated (229 commits — 51%)

UI/UX видимые изменения БЕЗ feature flag. **HOLD**.

**Deploy**: требует rollout plan — либо обучение менеджеров, либо wrapping в новый feature flag.

Состав (high-level):
- **F4 R3 v3/b карточка компании** (Apr 18-19): 98 коммитов редизайна — popup menus, phone normalizer, input-like edit, region/tz/workday. Новый UI никогда не тестировался менеджерами на проде.
- **Live-chat редизайн** (Apr 2-10): новый operator panel, new messenger UI, SSE status indicators
- **Dashboard + settings редизайн** (Apr 15-16): Notion-стиль, новые layouts
- **Widget public** (Feb-Mar): версия виджета в embed коде у клиентов GroupProfi
- Templates + partials changes: 112 HTML templates затронуты

---

## Временная разбивка

| Month | ops | refactor | featured | trivial | ux-gated | Total |
|-------|-----|----------|----------|---------|----------|-------|
| 2026-02 | 3 | 0 | 5 | 0 | 32 | 40 |
| 2026-03 | 0 | 0 | 0 | 0 | 2 | 2 |
| 2026-04 | 64 | 13 | 66 | 66 | 195 | 404 |

Апрель — mass refactor month. 50% ops/refactor/featured/trivial в Apr 2026 = ~150 deploy-safe коммитов сконцентрированы в W0.1-W0.4 waves.

---

## Recommendation для первого selective deploy

### Release W0.5a-safe (deploy batch)

Включить: 🟢 + 🔵 + 🟡 + ⚫ = **217 commits**.

Это даёт prod:
- ✅ **GlitchTip на prod** (real error tracking) — `09e1f94e`, `397eb85e`, `a30689fc`
- ✅ **Feature flags infrastructure** (django-waffle) — `96286510`
- ✅ **Health endpoints** (`/live/`, `/ready/`, `/health/`) — из W0.2/W0.4
- ✅ **Celery healthcheck fix** — `242fcf2a` (закрывает hotlist #9)
- ✅ **Messenger hardening batch** — 40+ коммитов security/validation/audit (за MESSENGER_ENABLED=0 на prod)
- ✅ **Wave 0.2 code quality baseline** — ruff/mypy/pre-commit/black
- ✅ **Internal refactoring** (unused imports, migration fixes)
- ✅ **Security Phase 0-1** — Android TokenManager, rate limiting, CSP

### Hold (staging-only)

Исключить: 🟠 = **229 commits**.

Это сохраняет на staging:
- 🟠 F4 R3 v3/b карточка компании (не тестировалась менеджерами в production)
- 🟠 Live-chat redesign (operator UI changes + SSE indicators)
- 🟠 Dashboard Notion-стиль (новые layouts)
- 🟠 Widget public changes (CSS + behavior changes, может повлиять на клиентов GroupProfi)
- 🟠 Templates / partials изменения (потенциально user-visible)

**Активация 🟠 batch** — отдельные decisions после:
1. Manager training sessions.
2. Side-by-side UI comparison (before / after screens).
3. Gradual rollout plan (branch director → group managers → managers).
4. Waffle flag per feature group (например: `W1_COMPANY_CARD_V3B`, `MESSENGER_V2_OPERATOR_PANEL`).

---

## Next steps

1. **Cherry-pick skeleton**: `docs/release/w0-5a-safe-plan.md` — план создания `release/w0-5a-safe` branch с только deploy-safe commits.
2. **Staging pre-validation**: после cherry-pick → deploy на staging → smoke + regression tests.
3. **Prod deploy**: tag `release-v1.0-w0-safe` + gated promotion по CLAUDE.md R2/R3.
4. **Post-deploy verification**: GlitchTip error tracking live, healthchecks green, manager UI **unchanged** (no visible difference vs. prod pre-deploy).

---

## Источник

- `scripts/release/classify_commits.py` — heuristic classifier.
- `scripts/release/refine_classification.py` — manual review overrides (63 commits + 3 false-positive fixes).
- `docs/release/classification-raw.csv` — heuristic output (reproducible).
- `docs/release/classification-reviewed.csv` — финальное распределение (source of truth).
