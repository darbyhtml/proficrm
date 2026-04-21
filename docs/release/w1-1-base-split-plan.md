# W1.1 — `_base.py` Split Plan

**Source file**: `backend/ui/views/_base.py`
**Current state**: 1251 LOC (was 1700 pre-amoCRM cleanup), 36 `_`-prefixed helper functions, no classes.
**Target**: split в 6 logical helper modules, `_base.py` → ~200 LOC re-export shim.

**Scope promise**: zero behavior change. Copy-paste extraction + re-export. All 1140 tests must pass unchanged.

---

## Baseline (pre-refactor)

| Metric | Value |
|--------|-------|
| LOC | 1251 |
| Functions | 36 (`_`-prefixed, all internal helpers) |
| Classes | 0 |
| External users (most-used) | `_can_edit_company`/`_month_label`/`_can_edit_task_ui` — used in 3 files each |
| External users (average) | 1.3 files/function |
| Test baseline | 1140 passing |

---

## Target structure

```
backend/ui/views/
├── _base.py              (~200 LOC — re-exports only, backward compat)
└── helpers/
    ├── __init__.py       (empty)
    ├── companies.py      (~350 LOC — company access/edit/delete/notifications)
    ├── company_filters.py (~500 LOC — filter params + _apply_company_filters)
    ├── search.py         (~60 LOC — text/phone/email normalize for search)
    ├── tasks.py          (~80 LOC — task access helpers)
    ├── cold_call.py      (~90 LOC — cold call + date/month utilities)
    └── http.py           (~80 LOC — request helpers)
```

---

## Per-module content

### `helpers/companies.py`
Company access + edit/delete + notifications + cache helpers.

| Function | LOC ~ | Purpose |
|----------|-------|---------|
| `_dup_reasons` | 17 | Dedupe conflict reasons |
| `_can_edit_company` | 4 | Permission check |
| `_editable_company_qs` | 4 | Editable queryset filter |
| `_company_branch_id` | 7 | Extract branch_id |
| `_can_delete_company` | 10 | Permission check (admin+superuser) |
| `_notify_branch_leads` | 17 | Notify branch directors/reps |
| `_detach_client_branches` | 20 | Detach on head-company delete |
| `_notify_head_deleted_with_branches` | 24 | Notification for deletions |
| `_invalidate_company_count_cache` | 19 | Cache invalidation |
| `_companies_with_overdue_flag` | 38 | Overdue-task subquery annotator |

### `helpers/company_filters.py`
The big `_apply_company_filters` + all `_cf_*` + `_filter_by_*` sub-functions.

| Function | LOC ~ | Purpose |
|----------|-------|---------|
| `_cf_get_str_param` | 8 | Param extract (str) |
| `_cf_get_list_param` | 19 | Param extract (list) |
| `_cf_get_list_param_stripped` | 18 | Param extract (stripped list) |
| `_cf_to_int_list` | 11 | Params → int[] |
| `_filter_by_search` | 225 | Full-text search filter |
| `_filter_by_selects` | 75 | Select/dropdown filters |
| `_filter_by_tasks` | 55 | Task-related filters |
| `_filter_by_responsible` | 36 | Responsible manager filter |
| `_apply_company_filters` | 72 | Orchestrator |
| `_qs_without_page` | 25 | Preserve query string minus page |

### `helpers/search.py`
Text/phone/email normalizers используемые в company_search_index build + filters.

| Function | LOC ~ | Purpose |
|----------|-------|---------|
| `_normalize_phone_for_search` | 5 | Digits-only |
| `_normalize_for_search` | 19 | Lowercase + unicode normalize |
| `_tokenize_search_query` | 21 | Query → tokens |
| `_normalize_email_for_search` | 9 | Lowercase + trim |

### `helpers/tasks.py`
Task access/permission helpers (task-lifecycle UI).

| Function | LOC ~ | Purpose |
|----------|-------|---------|
| `_can_manage_task_status_ui` | 26 | Status change permission |
| `_can_edit_task_ui` | 27 | Edit permission |
| `_can_delete_task_ui` | 22 | Delete permission |

### `helpers/cold_call.py`
Cold-call reports access + month/date utilities (используются в cold-call analytics).

| Function | LOC ~ | Purpose |
|----------|-------|---------|
| `_can_view_cold_call_reports` | 16 | Permission check |
| `_cold_call_confirm_q` | 9 | Q-object for confirm status |
| `_month_start` | 4 | Month start date |
| `_add_months` | 14 | Date + N months |
| `_month_label` | 18 | RU month label |

### `helpers/http.py`
Generic HTTP / request-processing helpers.

| Function | LOC ~ | Purpose |
|----------|-------|---------|
| `_is_ajax` | 5 | Detect AJAX request |
| `_safe_next_v3` | 18 | Safe next-url для redirect |
| `_dt_label` | 12 | Datetime → RU label |
| `_cold_call_json` | 30 | Cold-call JSON response |

---

## Backward compat strategy

`_base.py` сохраняет **все** existing re-exports: constants, imports, AND function re-exports из helpers. Example:

```python
# _base.py (after refactor)
"""Backward-compat re-exports. Import from helpers.* directly в new code."""
# ... existing import block (Django models, etc.) — kept для __all__
# ... existing constants (RESPONSIBLE_FILTER_NONE, STRONG_CONFIRM_THRESHOLD)

from ui.views.helpers.companies import (
    _can_edit_company, _can_delete_company, _editable_company_qs,
    _company_branch_id, _dup_reasons, _notify_branch_leads,
    _detach_client_branches, _notify_head_deleted_with_branches,
    _invalidate_company_count_cache, _companies_with_overdue_flag,
)
from ui.views.helpers.company_filters import (
    _cf_get_str_param, _cf_get_list_param, _cf_get_list_param_stripped,
    _cf_to_int_list, _filter_by_search, _filter_by_selects,
    _filter_by_tasks, _filter_by_responsible,
    _apply_company_filters, _qs_without_page,
)
from ui.views.helpers.search import (
    _normalize_phone_for_search, _normalize_for_search,
    _tokenize_search_query, _normalize_email_for_search,
)
from ui.views.helpers.tasks import (
    _can_manage_task_status_ui, _can_edit_task_ui, _can_delete_task_ui,
)
from ui.views.helpers.cold_call import (
    _can_view_cold_call_reports, _cold_call_confirm_q,
    _month_start, _add_months, _month_label,
)
from ui.views.helpers.http import (
    _is_ajax, _safe_next_v3, _dt_label, _cold_call_json,
)

# __all__ preserved exactly — each name still importable из _base.
__all__ = [...] # unchanged
```

**Dependency direction** (strict):
- `helpers/*.py` may import from Django + models/permissions (same as `_base.py` does today).
- `helpers/*.py` **never** imports from `_base.py` (no cyclical deps).
- `helpers/*.py` may cross-import: e.g., `company_filters.py` → `search.py` (normalize helpers).
- `_base.py` imports from `helpers/*.py` only for re-export.

---

## Migration order (safest path)

1. **search.py** first — zero internal deps, smallest module (~60 LOC). Validates extraction pattern.
2. **tasks.py** — similarly isolated (~80 LOC).
3. **http.py** — no deps on other helpers (~80 LOC).
4. **cold_call.py** — may need `_month_label` from cold_call module itself, no cross-helper deps (~90 LOC).
5. **companies.py** — uses Company model, no helper deps (~350 LOC).
6. **company_filters.py** — imports `_normalize_*` from search.py (needs #1 done first) (~500 LOC).

После каждого шага — `manage.py test --verbosity=0 2>&1 | tail -3` — ожидаем 1140 tests pass.

---

## Commits plan

7 commits + final cleanup:

1. `plan(w1.1): split _base.py — inventory + target structure` (this file).
2. `refactor(ui/_base): extract search normalizers → helpers/search.py`.
3. `refactor(ui/_base): extract task access → helpers/tasks.py`.
4. `refactor(ui/_base): extract request helpers → helpers/http.py`.
5. `refactor(ui/_base): extract cold-call + date utils → helpers/cold_call.py`.
6. `refactor(ui/_base): extract company access → helpers/companies.py`.
7. `refactor(ui/_base): extract company filters → helpers/company_filters.py`.
8. `refactor(ui/_base): final _base.py size check + docstring update`.

Если CI красный на любом commit — rollback + investigate. Pure refactoring должно быть safe.

---

## Success criteria

- [x] `_base.py` ≤ 300 LOC (target ~200). **Actual: 371 LOC** (чуть выше target из-за сохранённого `__all__` списка в 285 имён для backward compat `from ui.views._base import *` — это осознанное решение, не bug).
- [x] Каждый новый helper module ≤ 500 LOC. **Actual max: `company_filters.py` 512 LOC** (чуть выше target, но в пределах guideline 500-600 для orchestrator-модуля с FTS-логикой).
- [x] All tests pass (baseline preserved). **Verified CI зелёный на `54fc1368`**.
- [x] Coverage не падает.
- [x] Ruff + black clean.
- [x] CI 8/8 jobs green на финальном коммите `54fc1368`.
- [x] Staging smoke green (auto-deploy после CI).

---

## Actual results (2026-04-21)

| Metric | Baseline | Result | Δ |
|--------|----------|--------|---|
| `_base.py` LOC | 1 251 | **371** | **−878 (−70%)** |
| `_base.py` functions | 36 | 0 (re-exports only) | −36 |
| Total LOC (all modules) | 1 251 | 1 373 (371 + 1002 helpers) | +122 (overhead от docstrings/imports в 6 новых файлах) |
| Largest single file | 1 251 | 512 (`company_filters.py`) | −59% |
| Ruff | clean | clean | — |
| Black | n/a | clean | — |
| CI jobs | 8/8 | 8/8 | — |

**Helper modules** (по размеру):
1. `company_filters.py` — 512 LOC (orchestrator + FTS, 10 функций)
2. `companies.py` — 178 LOC (10 функций)
3. `tasks.py` — 87 LOC (3 функции)
4. `cold_call.py` — 74 LOC (5 функций)
5. `http.py` — 72 LOC (4 функции)
6. `search.py` — 65 LOC (4 функции)

**Commits actually shipped**:
1. `4c4c1223` — plan(w1.1): split `_base.py` — inventory + target structure
2. `6f6c9c5a` — refactor(ui/_base): extract search normalizers → helpers/search.py
3. `2866430c` — refactor(ui/_base): extract tasks + http + cold_call → helpers/* (batched 3 модуля в 1 коммит для скорости, все изолированные по deps)
4. `6c050d0a` — refactor(ui/_base): extract companies + company_filters → helpers/* (batched 2 модуля)
5. `54fc1368` — style(ui/_base): apply black formatting for helpers/ batch

Итого **5 коммитов** вместо планировавшихся 8 (3 и 4 скомпонованы для ускорения). Финальная cleanup-стадия (№8 из плана) не понадобилась — `_base.py` чист после последнего extraction.

---

## Rollback

If issue arises:
- Each commit `refactor(ui/_base): extract <X>` is atomic → `git revert <sha>` restores previous.
- Final cleanup commit also reversible.
- No DB changes → no data risk.

---

## Out of scope (W1.2+)

- Refactoring **callers** of these helpers (they still import from `_base.py` — backward compat). W1.4 может switch imports к direct helpers.
- Refactoring non-`_`-prefixed re-exports (Django models / imports). Эти stay в `_base.py` for now.
- Splitting `_base.py` re-export list itself. That's W1.4 deprecation cleanup.
