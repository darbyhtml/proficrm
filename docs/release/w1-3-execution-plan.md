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

- [ ] 9 bare scripts — nonce added (grep `<script>` без nonce = 0).
- [ ] 5 top style blocks extracted (`<style>...</style>` remaining count < 27).
- [ ] 10 handlers в company_detail.html removed (grep ` on[a-z]+="` в этом файле = 0).
- [ ] No new `<style src=>` или `<script>` без nonce.
- [ ] Tests baseline 1140 passing.
- [ ] Playwright E2E — no page errors на carded companies.
- [ ] CI 8/8 green.
- [ ] Staging smoke green.
- [ ] Kuma monitor Up.
