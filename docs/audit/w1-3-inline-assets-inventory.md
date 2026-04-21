# W1.3 — Inline Assets Inventory

**Snapshot**: 2026-04-21 (после завершения W1.2).
**Scope**: Scenario C — extract inline JS/CSS, НЕ split HTML templates (deferred до W9 full UX redesign).

---

## Summary

| Metric | Count | Notes |
|--------|-------|-------|
| Total templates | 109 | backend/templates/ |
| Templates with inline content | 53 | любые inline scripts / styles / handlers |
| Bare `<script>` (no `src=`, no `nonce=`) | **9** | 🔴 CSP blocker — нужно минимум добавить nonce |
| `<script nonce=...>` blocks | **81** | 🟢 CSP-ready (nonce есть, но enforce не включен) |
| `<script src=...>` refs | 7 | уже external, ok |
| Inline `<style>` blocks | **32** | 🔴 нужно extract |
| Inline event handlers (onclick/onchange/onsubmit) | **76** | 🔴 CSP blocker — нужен addEventListener |
| Total inline JS in nonce scripts (LOC) | **12 427** | large — медиана 92, max 921 |
| Total inline CSS in `<style>` blocks (LOC) | **4 131** | — |

### Event handlers breakdown
- `onclick=`: **43**
- `onsubmit=`: **22**
- `onchange=`: **11**

---

## Existing CSP infrastructure

**УЖЕ настроено** (F6 prior work, см. `backend/crm/middleware.py` line 28-31):
- `SecurityHeadersMiddleware.process_request` генерирует `request.csp_nonce` per-request.
- `csp_nonce` доступен в шаблонах через context processor (`backend/crm/context_processors.py`).
- Большинство inline scripts (81) уже используют `nonce="{{ csp_nonce }}"` → они **ready для CSP strict**.
- НО: CSP header не устанавливается в nonce-mode (middleware комментирует: "пока не встраивается nonce, т.к. шаблоны содержат inline onclick=/style=").

**Переходный барьер** = 76 inline event handlers + 32 inline styles. Пока они есть — nonce scripts не работают в strict mode.

---

## Top templates by inline content

| Template | Scripts (nonce) | Bare | Styles | Handlers | Total |
|----------|----------------|------|--------|----------|-------|
| `ui/company_detail.html` | 33 | 0 | 1 | 10 | 44 |
| `ui/mail/campaign_detail.html` | 5 | 0 | 3 | 14 | 22 |
| `ui/base.html` | 13 | 1 | 2 | 1 | 17 |
| `ui/settings/messenger_inbox_form.html` | 0 | 1 | 1 | 5 | 7 |
| `ui/mail/campaigns.html` | 3 | 0 | 1 | 3 | 7 |
| `ui/settings/user_form.html` | 1 | 0 | 0 | 5 | 6 |
| `ui/settings/error_log.html` | 1 | 0 | 0 | 5 | 6 |
| `ui/settings/users.html` | 1 | 0 | 0 | 4 | 5 |
| `ui/preferences.html` | 2 | 0 | 1 | 2 | 5 |
| `ui/messenger_conversations_unified.html` | 4 | 0 | 1 | 0 | 5 |

---

## Top styles by LOC (biggest wins)

| File | Style block LOC | % of total |
|------|-----------------|------------|
| `ui/base.html` | 863 | 20.9% |
| `ui/company_detail_v3/b.html` | 569 | 13.8% |
| `ui/messenger_conversations_unified.html` | 558 | 13.5% |
| `ui/_v2/v2_styles.html` | 380 | 9.2% |
| `ui/_v2/v3_styles.html` | 306 | 7.4% |
| `ui/company_detail_v3/c.html` | 186 | 4.5% |
| `ui/company_detail_v3/a.html` | 179 | 4.3% |
| `ui/company_detail.html` | 157 | 3.8% |
| `ui/_inline_edit.html` | 120 | 2.9% |
| `ui/mail/admin.html` | 101 | 2.4% |

**Top 5 styles** = 2 676 LOC = **65% of total** (4 131).
**Top 10 styles** = ~3 420 LOC = **83% of total**.

---

## Feasibility classification

### 🟢 Easy (pure JS/CSS, no Django tags)
- **65 nonce scripts** pure (no `{{ }}` / `{% %}` inside) = ~9 975 LOC.
  Strategy: `<script nonce=...>` → `<script src="{% static 'js/pages/<name>.js' %}" nonce=...>`. Verbatim copy.
- **~25-30 style blocks** без Django-vars.
  Strategy: `<style>...</style>` → `<link rel="stylesheet" href="{% static 'css/pages/<name>.css' %}">`.

### 🟡 Medium (Django vars inside)
- **16 nonce scripts** с `{{ }}` / `{% %}` = ~2 452 LOC.
  Strategy: передать данные через `data-*` attributes на root element, JS читает из `dataset`.
- **~2-5 style blocks** (редко) с Django tags.

### 🔴 Hard — CSP blockers
- **76 event handlers** — нельзя просто extract, требуют addEventListener refactor.
- **9 bare scripts** без nonce — минимум надо добавить nonce (1 min each).

---

## Scope decision for W1.3 (focused Scenario C)

Full extraction of 81 nonce scripts (~12 427 LOC) = **throwaway work** перед W9 full UX redesign. Per user decision + Path E philosophy, делаем **narrow scope**:

### In scope ✅
1. **Add nonce to 9 bare scripts** (5 min) — minor but unblocks CSP enforce на тех страницах.
2. **Extract top 5 style blocks** (base.html, company_detail_v3/b.html, messenger, v2_styles, v3_styles) = 2 676 LOC = **65% of inline CSS removed**.
3. **Convert event handlers в company_detail.html** (10 handlers) — самый activный template, показательный refactor.
4. **django-csp package install + report-only mode** — готовит W2 к enforce switch.
5. **Playwright regression test** — запускается до и после каждой extraction.

### Out of scope (deferred) ❌
- **Остальные 66 event handlers** (404/500/login + campaign_detail 14 + settings forms 19 + analytics 3 + ...) — W2 task когда будем switch CSP на enforce.
- **Оставшиеся 27 inline style blocks** — 35% LOC, low ROI per effort. W2 cleanup batch.
- **Massive extraction of 81 nonce scripts** — уже CSP-compatible через nonce. Full extract был бы ~12 427 LOC movement без поведенческого winа. Defer до W9 UX redesign (template split там будет естественной частью).

### Why not defer всё к W9?
1. **9 bare scripts** — очевидный технический долг, 5-минут fix.
2. **76 event handlers** — CSP enforcement blocker, критичен для W2 security волны.
3. **4 131 LOC inline CSS** — slow page parse + cache невозможен. Extract top 5 = reduced HTML payload для всех страниц которые extend base.html (т.е. все).
4. **django-csp package** — готовит инфраструктуру. W2 switch become simpler.

---

## Effort estimate

| Task | Blocks | LOC | Est. time |
|------|--------|-----|-----------|
| Nonce 9 bare scripts | 9 | ~50 | 15 min |
| Extract top 5 styles | 5 | 2 676 | 2 hours |
| Convert handlers in company_detail.html | 10 | ~50 | 2 hours |
| Install django-csp + report-only config | — | — | 30 min |
| Playwright regression | 1 suite | — | 30 min |
| Docs + final verify | — | — | 30 min |
| **Total** | | | **~6 hours** |

Fits в 1 working session. 80/20: top 5 styles + 9 nonce fixes + 10 handlers = biggest CSP-readiness win per effort.

---

## Success metrics (post-W1.3)

- `<script>` bare blocks (no nonce): **9 → 0**.
- `<style>` block LOC: **4 131 → ~1 455** (top 5 extracted, 65% reduction).
- Event handlers: **76 → 66** (company_detail cleanup only).
- django-csp installed, report-only active.
- Tests baseline 1140 preserved.
- Playwright E2E zero new console.errors.

**Full CSP strict enforcement остаётся для W2**, когда все handlers расчищены.
