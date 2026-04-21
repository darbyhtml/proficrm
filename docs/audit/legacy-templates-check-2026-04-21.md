# Legacy templates check — 2026-04-21

**Цель**: определить работает ли план «deploy main to prod + UI_V3B_DEFAULT=OFF → пользователи видят старый UI».

**Verdict**: ❌ **BROKEN** — legacy templates modified in-place, не параллельно. Flag `UI_V3B_DEFAULT` defined но **не wired** в views. Deploy main на prod = automatic activation of UX changes.

---

## Evidence

### 1. Legacy templates files check

| File | prod HEAD (`be569ad4`) | main HEAD |
|------|------------------------|-----------|
| `backend/templates/ui/base.html` | ✅ exists (2768 lines) | ✅ exists (**3781 lines**, +37%) |
| `backend/templates/ui/company_detail.html` | ✅ exists (8146 lines) | ✅ exists (**8781 lines**, +8%) |
| `backend/templates/ui/company_list.html` | ✅ exists | ❌ **DELETED** — replaced by `company_list_v2.html` |
| `backend/templates/ui/dashboard.html` | ✅ exists | ❌ **DELETED** — replaced by `dashboard_v2.html` |
| `backend/templates/ui/company_detail_v3/*` | N/A | ✅ new F4 R3 preview |
| `backend/templates/ui/_v2/*` | N/A | ✅ new v2 partials |
| `backend/templates/ui/analytics_v2/*` | N/A | ✅ new v2 partials |

**Ключевое**: `company_list.html` и `dashboard.html` **удалены** в main. Replaced _v2 versions в-place. No parallel legacy.

### 2. Base + detail templates modified in-place

`base.html` prod→main: +1013 lines (+37%).
`company_detail.html` prod→main: +635 lines (+8%).

Diff sample (prod → main):
- `"История передвижений"` → `"История взаимодействий"` (terminology change)
- `{% if history_events %}` → `{% if timeline_items %}` (variable change)
- `"Филиал"` → `"Подразделение"` (terminology change across multiple lines)

These are **in-place modifications**, not parallel legacy. Old wording/structure невозможно рендерить если deploy main.

### 3. Flag `UI_V3B_DEFAULT` — defined но не wired

**Defined**: `backend/core/feature_flags.py:73` — `UI_V3B_DEFAULT = "UI_V3B_DEFAULT"`.

**Usage in views** (grep `flag_is_active|waffle` в `backend/ui/views/`): **0 matches**.

**Usage в docstring example only**:
```python
# feature_flags.py:24-25 — это docstring с примером API usage:
if is_enabled("UI_V3B_DEFAULT", user=request.user):
    return render(request, "company_detail_v3b.html", ctx)
```

Это **template доку**, не implementation. Actual `company_detail` view (`backend/ui/views/company_detail.py:304`) рендерит `ui/company_detail.html` **без проверки флага**:
```python
return render(
    request,
    "ui/company_detail.html",
    {...}
)
```

`company_detail_v3.py` — отдельный view at `/companies/<uuid>/v3/<a|b|c>/` preview URL, не связан с main route.

### 4. Views always render latest templates

`backend/ui/views/company_detail.py` — рендерит `ui/company_detail.html` (уже updated до новой версии в main).
`backend/ui/views/dashboard.py` и другие — рендерят `_v2` versions (файлы новые, legacy deleted).

**Нет conditional rendering** по flag'у в actual main view functions.

---

## Implications for W0.5a-infra-only plan

### User's original plan (как сформулировано)
> «Deploy main на prod с ALL feature flags OFF. Менеджеры видят старый UI.»

**Это невозможно** при current state of main:
- Legacy templates `company_list.html` / `dashboard.html` **удалены** — TemplateDoesNotExist если пытаться render.
- Modified templates (`base.html`, `company_detail.html`) **не имеют conditional branch** для old vs new рендера.
- Flag `UI_V3B_DEFAULT` **ничего не контролирует** в runtime.

### Что происходит ФАКТИЧЕСКИ при deploy main на prod
- Все views рендерят новые templates automatically.
- Менеджеры увидят новый UI при first login after deploy.
- Нет way отключить это без code changes.

---

## Options для user decision

### R-A: Accept UX change as part of deploy (simplest)
Deploy main → users get new UI. Проводить manager training **до** deploy, не после.

Preparation:
- 1-2 часа demo sessions с менеджерами на staging pre-deploy.
- Screenshot before/after для reference.
- FAQ document для новых UI paths.

Risk: user confusion в первые часы после deploy.
Effort: 1-2 часа training pre-deploy.

### R-B: Wire up `UI_V3B_DEFAULT` flag properly (proper but expensive)
Restore legacy templates parallel + add conditional render в views:

1. Restore `company_list.html` + `dashboard.html` из `be569ad4` (cp через git show).
2. Create `_v3b` variants для modified templates:
   - `company_detail.html` → split into `company_detail_legacy.html` + `company_detail_v3b.html`
   - `base.html` → same split
3. Views: add `if flag_is_active("UI_V3B_DEFAULT"): render v3b else render legacy`.
4. Test оба modes на staging.
5. Deploy main → flag OFF → legacy UI.
6. Later: activate flag per-user, gradual rollout.

Effort estimate: **1-2 weeks** — not trivial. Requires careful rebuild of template hierarchy.

### R-C: Selective template revert (hybrid)
Revert **ТОЛЬКО** user-visible templates (`base.html`, `company_detail.html`, dashboard templates, company_list templates) к prod HEAD на **отдельной branch**. Keep all backend changes, messenger, security, observability.

Approach:
```bash
git checkout -b release/w0-5a-infra-ui-revert origin/main
git checkout be569ad4 -- backend/templates/ui/base.html backend/templates/ui/company_detail.html
# Recreate deleted legacy templates from prod HEAD:
git checkout be569ad4 -- backend/templates/ui/company_list.html  # если deleted in main
git checkout be569ad4 -- backend/templates/ui/dashboard.html     # если deleted in main
# Ensure views point to legacy templates (may need patching views too):
# Check backend/ui/views/dashboard.py — does it reference dashboard_v2 or dashboard.html?
```

Risk: fresh main views могут reference new template names или new context vars that legacy templates don't have → TemplateSyntaxError. Нужна manual adjustment.

Effort: 3-6 часов — plus full testing on staging.

### R-D: Rollback-capable accept (compromise)
Deploy main as-is (accepting UX change), BUT:
- Create rollback tag `release-v0.0-prod-pre-w05a` with `be569ad4` (already = `release-v0.0-prod-current`).
- Pre-deploy snapshot (DB + media + .env).
- Monitor первые 24 hours heavily (GlitchTip + Uptime Kuma + Telegram).
- Rollback immediate if any regression from managers.
- Plan 1-2h training sessions ПОСЛЕ deploy (reverse order of R-A).

Effort: ~30 min pre-deploy + ongoing monitoring.

---

## Recommendation

**R-A or R-D**. Both accept реальный state. Difference — когда делать training (before vs after).

R-B (proper flag wiring) is the correct long-term solution, но это W0.5b (следующая итерация), не текущая W0.5a.
R-C — too risky (template/view compatibility issues).

---

## Artifacts

- This file (`docs/audit/legacy-templates-check-2026-04-21.md`) — verdict.
- `classification-reviewed.csv` — per-commit UX classification.
- `docs/release/w0-5a-infra-only-plan.md` — will document chosen path + verdict.

---

## Status

**2026-04-21**: Diagnostic complete. User decision pending between R-A/R-D (or R-B as next wave).

Session D1 does NOT proceed to Step 3 (Live-chat button) because user explicitly
gated: «Only if Step 2 verdict = OK. Если broken — stop, нужна отдельная decision session.»
