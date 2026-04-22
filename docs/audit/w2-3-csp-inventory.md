# W2.3 — CSP current state inventory

**Date**: 2026-04-22. **Scope**: READ-ONLY discovery, no code changes.
**Purpose**: understand current CSP config + rollout readiness before W2.3 execution.

---

## TL;DR

- **Mode**: ENFORCE (not report-only) — but с **permissive `'unsafe-inline'`** в script-src + style-src → current policy — **effectively toothless against inline XSS**.
- **Nonce infrastructure ready**: middleware generates per-request nonce, 91 templates уже have `nonce="{{ request.csp_nonce }}"` applied (W1.3 work), но nonce **НЕ embedded** в CSP header пока.
- **Remaining blockers**: 66 inline event handlers + 673 inline `style=""` attributes + 25 `<style>` blocks.
- **Custom DIY middleware** — no `django-csp` package dependency.
- **Only 1 external CDN**: `cdn.jsdelivr.net` (Chart.js).
- **Realistic W2.3 effort**: 2-3h if defer inline-style cleanup к W9, 4-6h для full strict CSP.

---

## Current configuration

### Middleware

`backend/crm/middleware.py::SecurityHeadersMiddleware`:

```python
def process_request(self, request):
    request.csp_nonce = secrets.token_urlsafe(16)

def process_response(self, request, response):
    if getattr(response, "_skip_csp", False):
        return response  # widget-test pages skip CSP
    if not settings.DEBUG and getattr(settings, "CSP_HEADER", None):
        response["Content-Security-Policy"] = settings.CSP_HEADER
    # ... Permissions-Policy, X-API-Version ...
```

**Active только при `DEBUG=0`** — staging has `DJANGO_DEBUG=0`, так что CSP активен.

**Middleware comment acknowledges**: nonce generated, но **не embedded** в header, because "часть шаблонов всё ещё содержит inline onclick/style". Embedding nonce автоматически disables `'unsafe-inline'` в browsers (strictest takes precedence), поэтому пока nonce НЕ в headers.

### CSP header build (settings.py:190-210)

```python
CSP_DEFAULT_SRC = os.getenv("CSP_DEFAULT_SRC", "'self'")
CSP_SCRIPT_SRC = os.getenv("CSP_SCRIPT_SRC", "'self' 'unsafe-inline'")
CSP_STYLE_SRC = os.getenv("CSP_STYLE_SRC", "'self' 'unsafe-inline'")
CSP_IMG_SRC = os.getenv("CSP_IMG_SRC", "'self' data: https: blob:")
CSP_FONT_SRC = os.getenv("CSP_FONT_SRC", "'self' data:")
CSP_CONNECT_SRC = os.getenv("CSP_CONNECT_SRC", "'self'")

CSP_HEADER = (
    f"default-src {CSP_DEFAULT_SRC}; "
    f"script-src {CSP_SCRIPT_SRC}; "
    f"style-src {CSP_STYLE_SRC}; "
    f"img-src {CSP_IMG_SRC}; "
    f"font-src {CSP_FONT_SRC}; "
    f"connect-src {CSP_CONNECT_SRC}; "
    "frame-ancestors 'none'; "
    "base-uri 'self'; "
    "form-action 'self';"
)
```

### Packages

`grep -iE 'csp|security' backend/requirements.txt` → empty. **Custom DIY middleware, no django-csp package.**

### Report URI

**Absent.** No `report-uri` / `report-to` directive в CSP header. No CSP violation endpoint в URL patterns. No existing violation telemetry.

### CSP-skipped endpoints

`_skip_csp` flag используется на 1 page — messenger widget-test (`backend/messenger/views.py:176`). Легитимно (simulates external site embedding widget).

---

## Current response headers (staging, 2026-04-22)

### `/login/` response:

```
Content-Security-Policy: default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self' data: https: blob:; font-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self';
X-Frame-Options: DENY
Permissions-Policy: geolocation=(), microphone=(), camera=(), payment=(), usb=()
Strict-Transport-Security: max-age=31536000
X-Content-Type-Options: nosniff
Referrer-Policy: strict-origin-when-cross-origin
```

### Analysis

✅ **Good**:
- `frame-ancestors 'none'` — clickjacking protection.
- `base-uri 'self'` — prevents `<base>` tag hijacking.
- `form-action 'self'` — prevents form exfiltration.
- `X-Frame-Options: DENY`, `X-Content-Type-Options: nosniff`, HSTS, Referrer-Policy — все present.
- `img-src 'self' data: https: blob:` — разумно permissive для profile images + user uploads.

⚠️ **Weak points**:
- `script-src 'self' 'unsafe-inline'` — **allows ALL inline scripts** including XSS injections.
- `style-src 'self' 'unsafe-inline'` — **allows ALL inline styles**.
- **`'unsafe-inline'` defeats CSP's primary XSS defense.** Current config is compliance theater.

---

## Violations — no telemetry

- No `report-uri` → violations not logged anywhere.
- Web container logs: no CSP-related entries (grepped `csp|violation|blocked`).
- GlitchTip integration (W0.4) *could* capture CSP reports если configured — not currently.

---

## Inline script audit (post-W1.3)

| Category | Count | Status |
|----------|-------|--------|
| `<script>` bare без nonce | **0** | ✅ W1.3 cleaned — no regressions |
| `<script nonce="{{ request.csp_nonce }}">` | **91** | ✅ Ready для strict CSP after nonce-embed |
| `<script src="/static/...">` (first-party) | 7 unique paths | ✅ `'self'` covers |
| `<script src="https://cdn.jsdelivr.net/...">` (CDN) | 1 (Chart.js 4.4.7) | ⚠️ Нужен allowlist entry |

### External scripts enumeration

```
https://cdn.jsdelivr.net/npm/chart.js@4.4.7/dist/chart.umd.min.js
  — analytics_v2/group_manager.html:114
{% static 'messenger/favicon-badge.js' %}
{% static 'messenger/operator-panel.js' %}
{% static 'ui/company_create.js' %}
{% static 'ui/js/pages/company_detail_handlers.js' %}
{% static 'ui/purify.min.js' %}
{{ base_url }}/static/messenger/widget-loader.js
{{ base_url }}/static/messenger/widget.js
```

---

## Inline event handlers — main strict-CSP blocker

**66 inline event handler attributes** (onclick/onchange/onsubmit/onload/onerror/oninput/onkeyup/etc).

Strict CSP **blocks all inline event handlers** regardless of nonce — they must be extracted to `data-*` attributes + delegated JS listeners.

### Top 10 files (cleanup targets)

| File | Handlers |
|------|----------|
| `ui/mail/campaign_detail.html` | **14** |
| `ui/settings/user_form.html` | 5 |
| `ui/settings/messenger_inbox_form.html` | 5 |
| `ui/settings/error_log.html` | 5 |
| `ui/settings/users.html` | 4 |
| `ui/mail/campaigns.html` | 3 |
| `ui/company_list_rows.html` | 3 |
| `ui/analytics_user.html` | 3 |
| `ui/settings/messenger_automation.html` | 2 |
| `ui/preferences.html` | 2 |
| **Sum top 10** | **46** (out of 66) |

Cleanup pattern (W1.3 established):
```html
<!-- Before -->
<button onclick="doThing()">Click</button>

<!-- After -->
<button data-action="do-thing">Click</button>
<script nonce="{{ request.csp_nonce }}">
  document.addEventListener('click', (e) => {
    if (e.target.matches('[data-action="do-thing"]')) doThing();
  });
</script>
```

Или extract handler logic в existing delegated JS file (по pattern `company_detail_handlers.js`).

### `data-action`-style handlers currently

Only 2 `data-action` / `data-click` / `data-on-` references в templates. W1.3 extracted 10 для company_detail.html. Pattern established but не systematically applied.

---

## Inline styles — soft blocker

### `style="..."` attributes: **673 total** в templates

| File | Inline style= count |
|------|---------------------|
| `ui/company_detail.html` | 84 |
| `ui/company_list_v2.html` | 57 |
| `ui/dashboard_v2.html` | 53 |
| `ui/task_list_v2.html` | 38 |
| `ui/settings/mobile_apps.html` | 33 |
| `ui/mail/campaign_detail.html` | 28 |
| `ui/settings/mail_setup.html` | 27 |
| `ui/analytics_v2/manager.html` | 24 |
| `ui/reports/cold_calls_month.html` | 23 |
| `ui/reports/cold_calls_day.html` | 23 |
| **Sum top 10** | **390** (out of 673) |

### `<style>` block elements: **25**

3 в `mail/campaign_detail.html`, 1-each в 22 other templates. Легче конвертировать в внешние CSS (per-page или shared).

### Strict-CSP impact

Strict style-src (`'self' 'nonce-...'`) blocks inline `style="..."` attributes. Two options:
1. **Extract all 673 inline styles** → CSS classes в static/css/*. Large refactor, **overlaps с W9 UX redesign** (~half of files).
2. **Keep `'unsafe-inline'` in style-src** для phase 1, only harden script-src. Defer style-src strict до W9 completes.

**Recommendation**: Option 2 (pragmatic — script-src is где XSS exploit вектор, style-src в реальности low-risk).

---

## Third-party integrations

| Dependency | Use | CSP implication |
|------------|-----|-----------------|
| `cdn.jsdelivr.net` (Chart.js) | Analytics v2 group_manager dashboard | Add `https://cdn.jsdelivr.net` to script-src |
| `yandex.ru/maps/` | `window.open()` в inline_edit.html | No CSP impact (navigation, not script) |
| `webattach.mail.yandex.net` | String comparison in mail attachment processing | No CSP impact (URL matching, not script) |
| Messenger widget (`{{ base_url }}/static/messenger/widget.js`) | First-party на same host | Covered by `'self'` |

**No Google Analytics, no Chatwoot, no Intercom, no Yandex.Metrica scripts loaded.**

Also clean:
- **Zero `javascript:` protocol URLs** ✅
- **Zero `eval()` / `new Function()`** в templates или static/ (non-minified) ✅

---

## Proposed W2.3 rollout

### Phase 1 — Add nonce + external allowlist + parallel report-only (1h)

- `CSP_HEADER`: `script-src 'self' 'nonce-{request.csp_nonce}' https://cdn.jsdelivr.net`.
- Keep `style-src 'self' 'unsafe-inline'` для pragmatic phase 1 (styles deferred).
- Add **`Content-Security-Policy-Report-Only`** parallel header с proposed **strict** config (no unsafe-inline in script-src, nonce-embedded). Browsers log violations without breaking.
- Add `/csp-report/` endpoint в urls.py — receives JSON violation reports, logs в ActivityEvent или ErrorLog.

### Phase 2 — Fix 66 inline event handlers (3-5h)

Extract handlers в `data-action` + delegated JS listeners. Target order:
1. `campaign_detail.html` (14) — highest single file.
2. `settings/user_form.html` + `settings/messenger_inbox_form.html` + `settings/error_log.html` (5 each = 15).
3. Остальные 7 files с 2-4 handlers each (22).
4. Long-tail 24 handlers spread по многим templates.

Commit per file. Test per file: manually trigger each interaction + verify CSP no longer violates.

### Phase 3 — Switch к strict enforce (30 min)

После Phase 2 + 48h monitoring without violations:
- Swap `Content-Security-Policy-Report-Only` (strict) → `Content-Security-Policy` (strict, removes `'unsafe-inline'` from script-src).
- Remove old permissive `Content-Security-Policy` header.
- Smoke test all major flows.
- Keep report-only endpoint для ongoing monitoring.

### Inline style-src cleanup — **deferred к W9**

- 673 inline `style=""` attributes overlap значительно с W9 UX redesign scope.
- В W9: extract styles в CSS classes, adopt design tokens. Naturally removes inline styles.
- Post-W9: tighten style-src к nonce-based strict.

---

## Risks

- **Chart.js CDN dependency** — jsdelivr fails → analytics v2 page broken. Alternative: vendor Chart.js в `/static/` (removes CDN dependency, adds ~300KB static asset).
- **Extension-injected scripts** — user browsers with script-injecting extensions (Grammarly, LastPass, ad blockers) may trigger violations. Normal и acceptable (не break site).
- **Future developers** adding inline scripts — need documentation + pre-commit lint check (grep for `onclick=`).
- **W9 redesign schedule** — если W9 delayed, inline style cleanup delayed. W2.3 Phase 1-3 can ship without waiting for style cleanup.

---

## Estimated effort

| Phase | Effort | Status |
|-------|--------|--------|
| Phase 1: nonce + report-only + report endpoint | **1h** | ready to start |
| Phase 2: 66 inline handlers extraction | **3-5h** | can be split across 3-4 sub-sessions |
| Phase 3: strict switch + verify | **30 min** | after 48h of report-only clean |
| Inline style cleanup (style-src strict) | — | **deferred к W9** |
| **Total W2.3 (phases 1-3)** | **4-6h** | 2-3 sub-sessions |

---

## Rollout readiness checklist

- [x] Nonce infrastructure: **ready** (91 templates already use `request.csp_nonce`).
- [x] No bare `<script>` без nonce: **clean** ✅.
- [x] No `javascript:` URLs: **clean** ✅.
- [x] No `eval()` / `new Function()`: **clean** ✅.
- [ ] Inline event handlers extraction: **66 остаются**, Phase 2 needed.
- [ ] CSP report endpoint: **absent**, нужно создать.
- [ ] Report-only monitoring run: **не проводился**, Phase 1 запустит.
- [ ] Third-party allowlist documented: **1 CDN** (jsdelivr Chart.js).
- [ ] Inline style cleanup: **deferred к W9** (673 occurrences).

---

## Session artifacts

- Docs only: `docs/audit/w2-3-csp-inventory.md` (this file).
- Zero code changes.
- Zero prod touches.
- Baseline preserved: 1301 tests passing, staging smoke 6/6.
