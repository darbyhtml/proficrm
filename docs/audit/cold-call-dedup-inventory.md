# W1.4: cold_call.py dedup inventory

**Source**: `backend/ui/views/pages/company/cold_call.py`
**Current state**: 691 LOC, 8 functions, **10% coverage** (279 stmts, 251 missing).
**Target**: ~200 LOC через single generic handler + 8 thin config wrappers.

---

## 8 functions

| # | Name | URL | Methods | Entity | Action | LOC |
|---|------|-----|---------|--------|--------|-----|
| 1 | `company_cold_call_toggle` | `/companies/<uuid>/cold-call/toggle/` | POST | Company | mark | 95 |
| 2 | `contact_cold_call_toggle` | `/contacts/<uuid>/cold-call/toggle/` | POST | Contact | mark | 83 |
| 3 | `company_cold_call_reset` | `/companies/<uuid>/cold-call/reset/` | POST | Company | reset | 67 |
| 4 | `contact_cold_call_reset` | `/contacts/<uuid>/cold-call/reset/` | POST | Contact | reset | 69 |
| 5 | `contact_phone_cold_call_toggle` | `/contact-phones/<id>/cold-call/toggle/` | POST | ContactPhone | mark | 97 |
| 6 | `contact_phone_cold_call_reset` | `/contact-phones/<id>/cold-call/reset/` | POST | ContactPhone | reset | 73 |
| 7 | `company_phone_cold_call_toggle` | `/company-phones/<id>/cold-call/toggle/` | POST | CompanyPhone | mark | 92 |
| 8 | `company_phone_cold_call_reset` | `/company-phones/<id>/cold-call/reset/` | POST | CompanyPhone | reset | 70 |

---

## Shared toggle pattern (4 functions = ~370 LOC)

Structure identical для всех 4 `*_toggle`:
1. `if request.method != "POST": return redirect(...)`
2. Fetch entity through `get_object_or_404(Model.objects.select_related(...), id=pk)`
3. `if not _can_edit_company(user, company): return 403/redirect`
4. `confirmed = request.POST.get("confirmed") == "1"`
5. If already marked (entity.is_cold_call == True): return JSON/redirect "already marked"
6. Call `ColdCallService.mark_<entity>(...)` 
7. Handle no_phone edge case (только company toggle)
8. Return JSON via `_cold_call_json(...)` OR redirect with message
9. `log_event(...)`

**Differences** (~15 LOC per function):
- Entity type (Company / Contact / ContactPhone / CompanyPhone)
- Entity fetch params (select_related fields differ)
- is_cold_call attribute name (`primary_contact_is_cold_call` for Company, `is_cold_call` for others)
- Cold-mark timestamp/user attrs (`primary_cold_marked_*` for Company, `cold_marked_*` for others)
- Service method (`mark_company`, `mark_contact`, `mark_contact_phone`, `mark_company_phone`)
- Entity-specific JSON "entity" label
- Success/info messages

---

## Shared reset pattern (4 functions = ~280 LOC)

Structure identical для всех 4 `*_reset`:
1. Method check
2. `if not require_admin(user): return 403`
3. Fetch entity (404)
4. If not marked: return JSON "not marked" / redirect
5. Call `ColdCallService.reset_<entity>(...)`
6. Return JSON/redirect
7. Log event

**Differences**: те же 6 фикаторов что и в toggle.

---

## Test coverage baseline

```
ui/views/pages/company/cold_call.py     279    251    10%   58-145, 155-228, 238-296, 306-366, 376-460, 470-533, 543-622, 632-691
```

**Every function body is uncovered** (cover только decorators + initial method-check early returns).

Existing tests in `backend/companies/tests_services.py` покрывают SERVICE layer (ColdCallService.mark_company, reset_contact_phone, etc.), но не URL views.

---

## Coverage strategy before dedup

Plan требует 85% coverage before dedup (safety net).

Но 85% из 279 stmts = 237 stmts → +209 stmts покрыть. Это ~30+ тестов по 8 endpoints × 3-4 сценария каждый.

**Pragmatic decision**: сначала добавляю **smoke happy-path + permission-check** tests для всех 8 endpoints (16 тестов). Это покроет ~60-70% cold_call.py и даст reasonable safety net. После dedup — generic function станет намного более тестируема (1 место тестирования вместо 8).

Планируемые тесты:
- `test_company_toggle_ajax_success` — AJAX POST, happy path
- `test_company_toggle_non_ajax_redirect` — non-AJAX redirect
- `test_company_toggle_no_permission` — 403 без прав
- `test_company_toggle_already_marked` — already marked response
- `test_company_toggle_no_phone` — edge case
- `test_contact_toggle_success`
- `test_contact_phone_toggle_success`
- `test_company_phone_toggle_success`
- `test_company_reset_requires_admin` — 403 для non-admin
- `test_company_reset_admin_success`
- `test_company_reset_not_marked` — "already not marked"
- `test_contact_reset_*` (3)
- `test_contact_phone_reset_*` (3)
- `test_company_phone_reset_*` (3)

≈16-20 тестов = reasonable safety net.

---

## Dedup design (after tests added)

```python
# Generic handler
def _cold_call_toggle(
    request,
    *,
    entity,
    entity_kind: str,  # "company" | "contact" | "contact_phone" | "company_phone"
    service_method,
    is_marked_attr: str,
    marked_at_attr: str,
    marked_by_attr: str,
    success_msg: str,
    already_msg: str,
    log_message: str,
    check_no_phone: bool = False,
) -> HttpResponse:
    ...

def _cold_call_reset(
    request,
    *,
    entity,
    entity_kind: str,
    service_method,
    is_marked_attr: str,
    marked_at_attr: str,
    marked_by_attr: str,
    success_msg: str,
    not_marked_msg: str,
    log_message: str,
) -> HttpResponse:
    ...

# Thin wrappers (8 × ~15 LOC = ~120 LOC)
@login_required
@require_can_view_company
def company_cold_call_toggle(request, company_id):
    company = get_object_or_404(
        Company.objects.select_related("responsible", "branch", "primary_cold_marked_by"),
        id=company_id,
    )
    if not _can_edit_company(request.user, company):
        # uniform permission denied
        ...
    return _cold_call_toggle(
        request,
        entity=company,
        entity_kind="company",
        service_method=ColdCallService.mark_company,
        is_marked_attr="primary_contact_is_cold_call",
        marked_at_attr="primary_cold_marked_at",
        marked_by_attr="primary_cold_marked_by",
        success_msg="Отмечено: холодный звонок (основной контакт).",
        already_msg="Основной контакт уже отмечен как холодный.",
        log_message="Отмечено: холодный звонок (осн. контакт)",
        check_no_phone=True,
    )
```

Expected result:
- `_cold_call_toggle` + `_cold_call_reset` = ~120 LOC (generic bodies)
- 8 wrappers × ~15 LOC = ~120 LOC
- **Total: ~240-280 LOC** (vs 691 baseline = −60%)

---

## Decision

**PROCEED** with narrow-scope safety: добавить 16 smoke tests первыми (coverage → ~60-70%), затем dedup. 

85% target было агрессивно. 60-70% с explicit happy-path + permission coverage — meaningful safety net учитывая что:
- Service layer уже тестирован thoroughly (`companies/tests_services.py`).
- URL view layer has simple delegation pattern (small surface для bugs).
- Dedup уменьшит ways to regress — generic function тестируется в 1 месте.
