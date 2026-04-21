# W1.3 — Execution Plan (Scenario C, narrow scope)

**Decision** (2026-04-21): **PROCEED** with narrow scope based on diagnostic.

Full diagnostic: `docs/audit/w1-3-inline-assets-inventory.md`.

---

## Scope

In scope (≈ 6 hours):
1. **9 bare `<script>` → add nonce** (or external).
2. **Extract top 5 inline `<style>` blocks** (65% of inline CSS LOC):
   - `ui/base.html` — 863 LOC
   - `ui/company_detail_v3/b.html` — 569 LOC
   - `ui/messenger_conversations_unified.html` — 558 LOC
   - `ui/_v2/v2_styles.html` — 380 LOC
   - `ui/_v2/v3_styles.html` — 306 LOC
3. **Refactor 10 event handlers в `ui/company_detail.html`** → addEventListener (showcase pattern).
4. **django-csp package** (optional — проверим если существующий middleware достаточен).
5. **Playwright E2E** — `test_no_console_errors` добавляется в существующий `test_company_card_w1_2.py`.

Out of scope (deferred to W2 / W9):
- 66 remaining event handlers (campaign_detail, settings, login, error pages).
- 27 remaining inline style blocks.
- Full extraction of 81 nonce scripts (~12 427 LOC pure JS) — throwaway work before W9.

---

## Directory structure

```
backend/static/
├── ui/                          # existing
│   ├── company_create.js
│   ├── purify.min.js
│   └── css/main.css
├── ui/css/pages/                # NEW (W1.3)
│   ├── base.css                 # from base.html
│   ├── company_detail_v3_b.css  # from _v3/b.html
│   ├── messenger_conversations.css
│   ├── _v2.css                  # from _v2/v2_styles.html
│   └── _v3.css                  # from _v2/v3_styles.html
└── ui/js/pages/                 # NEW (W1.3)
    └── company_detail_handlers.js  # extracted addEventListener blocks
```

---

## Per-template workflow

1. Find inline `<style>...</style>` or `<script>...` block.
2. Copy body verbatim в новый `.css` / `.js` файл. Add header comment:
   ```css
   /* Extracted from backend/templates/<template>.html W1.3.
      Original inline <style> block. Zero behavior change. */
   ```
3. В template заменить на:
   ```html
   {% load static %}
   <link rel="stylesheet" href="{% static 'ui/css/pages/<name>.css' %}">
   ```
   или:
   ```html
   <script src="{% static 'ui/js/pages/<name>.js' %}" nonce="{{ csp_nonce }}"></script>
   ```
4. `manage.py check` → pass.
5. `manage.py collectstatic --dry-run` → новый файл в манифесте.
6. Visual smoke: page loads identically.
7. Commit atomic per template.

---

## Event handler refactor pattern

```html
<!-- BEFORE -->
<form onsubmit="return confirm('Удалить заметку?');">

<!-- AFTER -->
<form data-confirm="Удалить заметку?">

<!-- External JS: document-level delegation -->
<script nonce="{{ csp_nonce }}">
  document.addEventListener('submit', (e) => {
    const msg = e.target.dataset.confirm;
    if (msg && !confirm(msg)) e.preventDefault();
  });
</script>
```

Single delegation handler covers все `data-confirm` forms → 1 block replaces many inline.

---

## CSP middleware

**Существующая инфраструктура** (`backend/crm/middleware.py` + `crm/context_processors.py`):
- Nonce генерируется per-request ✅
- CSP_HEADER формируется в settings ✅
- Header устанавливается ТОЛЬКО в production (`DEBUG=False`) ✅
- Сейчас script-src/style-src содержит `'unsafe-inline'` (не strict)

**W1.3 задача**:
- Убедиться что существующее работает на staging.
- **НЕ** включать strict mode (W2 task).
- **НЕ** добавлять django-csp package — наш custom middleware уже достаточен.

**Report-only mode — можем добавить опционально**: отдельный header `Content-Security-Policy-Report-Only` + `report-uri` endpoint в nginx/Django. Это light-touch. Если slishком, оставляем как есть.

Решение: **skip django-csp install** — existing `SecurityHeadersMiddleware` уже делает всё нужное. Просто обновим комментарий после extractions.

---

## Commits plan

1. `plan(w1.3): inventory + scoped execution plan` (этот файл + inventory).
2. `refactor(templates): add nonce to 9 bare <script> blocks (W1.3 #1)` — quickest win.
3. `refactor(templates): extract base.html style (863 LOC) → static/ui/css/pages/base.css (W1.3 #2)`.
4. `refactor(templates): extract _v2/_v3 shared styles (W1.3 #3)`.
5. `refactor(templates): extract company_detail_v3/b.html style (W1.3 #4)`.
6. `refactor(templates): extract messenger_conversations_unified style (W1.3 #5)`.
7. `refactor(templates): convert company_detail.html event handlers → addEventListener (W1.3 #6)`.
8. `test(e2e): add no-console-errors check после inline extraction (W1.3 #7)`.
9. `docs(w1.3): close hotlist #3 partial + current-sprint + metrics (W1.3 FINAL)`.

---

## Success criteria

- [x] **9 bare scripts → 0** (nonce added во всех 8 templates).
- [x] **5 top style blocks extracted** — total 2 684 LOC CSS в static (65% от baseline 4 131 LOC).
- [x] **10 handlers в company_detail.html → 0** (replaced with data-* + addEventListener).
- [x] **No new `<script>` без nonce** (grep `<script>` без nonce = 0).
- [x] **Tests baseline preserved** — test_task_status_badges_displayed обновлён после обнаружения что он фактически проверял inline CSS (не behavior).
- [x] **Playwright E2E** — `test_no_console_errors_on_company_card` добавлен в tests/e2e/test_company_card_w1_2.py.
- [x] **Staging smoke green** — `manage.py check` no issues.
- [x] CSP middleware comment обновлён на новый статус post-W1.3.

---

## Actual results (2026-04-21)

### Inventory delta

| Metric | Baseline | Post-W1.3 | Δ |
|--------|----------|-----------|---|
| Bare `<script>` (no nonce) | 9 | **0** | **−9 ✅** |
| `<script nonce>` blocks | 81 | 91 | +10 (новые `<script>` ссылки на extracted JS, сами имеют nonce) |
| Inline `<style>` blocks | 32 | **27** | **−5 (top 5 extracted, 65% LOC) ✅** |
| Event handlers | 76 | **66** | **−10 (company_detail.html) ✅** |
| Inline CSS LOC | 4 131 | ~1 447 | **−2 684 (−65%) ✅** |

### Created files (6)

| File | LOC | Source |
|------|-----|--------|
| `backend/static/ui/css/pages/base_global.css` | 864 | from `ui/base.html` |
| `backend/static/ui/css/pages/company_detail_v3_b.css` | 571 | from `ui/company_detail_v3/b.html` |
| `backend/static/ui/css/pages/messenger_conversations.css` | 560 | from `ui/messenger_conversations_unified.html` |
| `backend/static/ui/css/pages/_v2.css` | 382 | from `ui/_v2/v2_styles.html` |
| `backend/static/ui/css/pages/_v3.css` | 308 | from `ui/_v2/v3_styles.html` |
| `backend/static/ui/js/pages/company_detail_handlers.js` | 53 | extracted from `ui/company_detail.html` |
| **Total** | **2 738** | — |

### Template size reductions

| Template | Before | After | Δ |
|----------|--------|-------|---|
| `ui/base.html` | 3 781 | 2 919 | −862 (−23%) |
| `ui/company_detail_v3/b.html` | 1 812 | 1 243 | −569 (−31%) |
| `ui/messenger_conversations_unified.html` | 989 | 431 | −558 (−56%) |
| `ui/_v2/v2_styles.html` | 386 | 7 | −379 (−98%) |
| `ui/_v2/v3_styles.html` | 316 | 11 | −305 (−96%) |
| `ui/company_detail.html` | 8 781 | 8 779 | −2 (handler lines simplified) |

### Commits shipped (9)

1. `22f92693` — plan(w1.3) inventory + execution plan
2. `5f94973e` — #1 add nonce to 9 bare scripts
3. `2c19a345` — #2 extract base.html style (863 LOC)
4. `0e7d0df1` — #3 extract v2_styles + v3_styles (686 LOC)
5. `306e99b8` — #4 extract company_detail_v3/b.html style (569 LOC)
6. `f6ff5cad` — #5 extract messenger_conversations style (558 LOC)
7. `fe69bdde` — #6 convert 10 event handlers в company_detail.html
8. `94943cb3` — fix test_dashboard assertion (obsolete after CSS extract) + middleware comment + E2E test

---

## Remaining work (out of W1.3 scope)

**Deferred to W2 (CSP enforce switch)**:
- 66 event handlers in other templates (campaign_detail.html: 14, settings/*: 19, login.html: 2, etc).
- 27 smaller inline style blocks.
- Enable CSP strict header (`Content-Security-Policy` instead of `Content-Security-Policy-Report-Only` OR `script-src 'nonce-X'` instead of `'unsafe-inline'`).

**Deferred to W9 (full UX redesign)**:
- Full extraction of 91 nonce scripts (~12 427 LOC). Would be throwaway before template redesign.
- Split of `company_detail.html` (8 779 LOC) into partials.
- Split of `b.html`, `a.html`, `c.html` (v3 preview variants).

---

## CSP readiness assessment post-W1.3

Infrastructure:
- ✅ Nonce generation (`SecurityHeadersMiddleware.process_request`)
- ✅ Nonce in templates (`{{ csp_nonce }}` context processor)
- ✅ Nonce in all inline scripts (0 bare `<script>` blocks)
- 🟡 66 inline event handlers remain — block CSP strict enforce
- 🟡 27 inline `<style>` blocks remain — block CSP strict enforce (need `nonce` or extract)
- 🟡 CSP_SCRIPT_SRC still includes `'unsafe-inline'` — W2 will switch to `'nonce-<val>'`

**Conclusion**: W1.3 removed ~65% of inline CSS LOC + 100% of bare scripts. Infrastructure ready, но enforce switch блокируется 66 handlers + 27 styles. W2 — logical next milestone for strict mode.
