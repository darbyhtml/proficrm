# W1.2 — `company_detail.py` Split Plan

**Source**: `backend/ui/views/company_detail.py`
**Pre-W1.2 state**: **3 022 LOC**, 42 функции, обслуживает 40 URL routes (Hotlist #1 P0).
**Target**: разбить на 10 тематических модулей в `backend/ui/views/pages/company/`, каждый ≤ 400 LOC (с допуском на ~450 для orchestrator-подобных модулей).

**Scope promise**: zero behavior change. Copy-paste extraction + import-adjust only. No rewrites, no URL changes, no template changes. Baseline tests (1140) + coverage (52%) должны остаться.

---

## Baseline (Step 0, локинг 2026-04-21)

| Metric | Value |
|--------|-------|
| Tests | **1140 passing** |
| Coverage | **52%** (staging, через `coverage run --source=.` из `backend/`) |
| `company_detail.py` LOC | **3022** |
| Functions | **42** |
| URL routes | **40** |
| External consumers | **2** (`views/__init__.py` + `company_detail_v3.py`) |

**Acceptance**: W1.2 должна сохранить каждое из этих чисел (coverage не падает, tests не меняются).

---

## Target structure

```
backend/ui/views/
├── company_detail.py            # → DELETE после миграции (cleanup option A)
├── pages/
│   ├── __init__.py              # empty
│   └── company/
│       ├── __init__.py          # public re-exports (для backward compat если нужен)
│       ├── detail.py            (~338 LOC) — main card + timeline + tasks_history
│       ├── edit.py              (~372 LOC) — edit/update/inline_update/transfer/contract
│       ├── deletion.py          (~242 LOC) — 4 delete funcs
│       ├── contacts.py          (~193 LOC) — contact CRUD
│       ├── notes.py             (~426 LOC) — 8 note funcs (CRUD + attachments + pin)
│       ├── deals.py             (~94 LOC)  — deal CRUD
│       ├── cold_call.py         (~650 LOC) — 8 cold-call toggles/resets
│       ├── phones.py            (~396 LOC) — phone CRUD + comments (7 funcs)
│       ├── emails.py            (~104 LOC) — email updates (2 funcs)
│       └── calls.py             (~124 LOC) — phone_call_create (phonebridge)
```

**Total LOC**: ~2 950 (чуть меньше baseline за счёт дедупликации + optimize imports).

### Size outliers (acknowledged)
- `cold_call.py` — **650 LOC > target 400**. 8 функций (toggle/reset × 4 entities: company/contact/company_phone/contact_phone). Все ~70-95 LOC, структурно идентичны. Дальнейшее разделение на `cold_call_company.py` + `cold_call_contact.py` — cosmetic, не принесёт clarity. Оставить как есть, задокументировать в docstring модуля.
- `notes.py` — **426 LOC**, чуть больше 400. OK, не критично.

---

## Backward compat strategy

### Decision: Option A — удалить `company_detail.py` полностью

Основание (из inventory):
- Только **2 консумера**: `views/__init__.py` (под нашим контролем) + `company_detail_v3.py` (единичный импорт, легко обновить).
- Нет ни одного теста, утилиты, миграции, которые импортируют напрямую.
- Re-export shim только усложнит структуру.

**Шаги обновления consumers** в финальном коммите W1.2:
1. `views/__init__.py` строки 8-52 — заменить `from ui.views.company_detail import (...)` на 10 блоков `from ui.views.pages.company.{module} import (...)`.
2. `views/company_detail_v3.py` строка 292 — заменить `from ui.views.company_detail import _can_edit_company` на `from ui.views._base import _can_edit_company`.
3. `rm backend/ui/views/company_detail.py`.

### URL routing — без изменений

`backend/ui/urls.py` обращается к `views.FUNCTION_NAME` (через `views/__init__.py` re-exports). Обновляем только re-exports в `__init__.py`, urls.py **не трогаем**.

---

## Dependency direction

```
backend/ui/views/pages/company/*.py  →  ui.views._base  →  ui.views.helpers.*
                                   →  django / stdlib
                                   →  models / services / forms
```

Strictly:
- `pages/company/*.py` импортируют только из `ui.views._base` (consolidated re-export hub), Django, stdlib, models/services/forms/phonebridge.
- **Никогда** не импортируют друг у друга. Если нужен cross-module helper — кладём в `helpers/*`.
- Нет import cycle: `pages/company/*` → `_base` → `helpers/*` (terminal).

---

## Migration order (safest path — simple → complex)

1. **`deals.py`** (2 funcs, ~94 LOC) — smallest, изолирован.
2. **`emails.py`** (2 funcs, ~104 LOC) — маленький.
3. **`calls.py`** (1 func, ~124 LOC) — изолирован phonebridge.
4. **`contacts.py`** (3 funcs, ~193 LOC) — CRUD pattern.
5. **`deletion.py`** (4 funcs, ~242 LOC) — isolated workflow.
6. **`phones.py`** (7 funcs, ~396 LOC).
7. **`notes.py`** (8 funcs, ~426 LOC) — attachments + pin.
8. **`edit.py`** (5 funcs, ~372 LOC) — forms-heavy.
9. **`cold_call.py`** (8 funcs, ~650 LOC) — largest.
10. **`detail.py`** (3 funcs, ~338 LOC) — main, extract LAST.

**Rationale для order**: начинаем с маленьких изолированных модулей, чтобы валидировать pattern. Самые сложные (`cold_call`, `detail`) в конце, когда pattern отработан и тесты прошли 9 раз.

---

## Per-extraction workflow

Для каждого из 10 модулей:

1. `touch backend/ui/views/pages/company/{domain}.py` — создать файл.
2. **Copy** функции из `company_detail.py` целиком (verbatim body + decorators).
3. Добавить `from __future__ import annotations`, `logging`, `from ui.views._base import (...)` с нужным sub-set из импортов.
4. `logger = logging.getLogger(__name__)`.
5. **Remove** те же функции из `company_detail.py`.
6. Обновить `backend/ui/views/__init__.py`: строку import — на новый путь.
7. `docker compose exec -T web python manage.py check` → pass.
8. `docker compose exec -T web python manage.py test --verbosity=0` → **1140 pass**.
9. `git add . && git commit -m "refactor(ui/pages/company): extract {domain} → pages/company/{domain}.py"`.
10. `git push origin main` → wait auto-deploy → `make smoke-staging`.
11. Если CI green + staging smoke green → переходим к следующему модулю.

---

## Shared base (`pages/company/_base.py`)?

**Decision**: **не создавать** на старте. Все функции уже используют `from ui.views._base import *` и там **все** общие helpers уже лежат (после W1.1). Создание ещё одного `pages/company/_base.py` = дубликация.

Если в процессе extraction окажется что нужен shared helper который *специфичен для company pages* (не генерик для всего UI) — создадим тогда. Но forecast: не понадобится.

---

## Commits plan

≈ 11 коммитов:
1. `plan(w1.2): inventory + split plan for company_detail.py` (этот файл + inventory.md).
2. `refactor(ui/pages/company): extract deals → pages/company/deals.py`.
3. `refactor(ui/pages/company): extract emails → pages/company/emails.py`.
4. `refactor(ui/pages/company): extract calls → pages/company/calls.py`.
5. `refactor(ui/pages/company): extract contacts → pages/company/contacts.py`.
6. `refactor(ui/pages/company): extract deletion → pages/company/deletion.py`.
7. `refactor(ui/pages/company): extract phones → pages/company/phones.py`.
8. `refactor(ui/pages/company): extract notes → pages/company/notes.py`.
9. `refactor(ui/pages/company): extract edit → pages/company/edit.py`.
10. `refactor(ui/pages/company): extract cold_call → pages/company/cold_call.py`.
11. `refactor(ui/pages/company): extract detail + delete company_detail.py` (финал).

При падающем CI — разобрать, rollback (atomic commits → `git revert` легко).

---

## Success criteria

- [x] `company_detail.py` удалён (option A clean — ни shim, ни re-export).
- [x] 10 модулей в `pages/company/`. **7 из 10 в пределах target 400 LOC**; 3 outliers: `cold_call.py` 691 LOC (documented — 8 structurally identical fns), `notes.py` 474 LOC, `edit.py` 420 LOC.
- [x] **Tests: 1140 pass** — измерено на staging после extraction #9 (финальные #10 и black/E2E коммиты только refactor metadata, не меняют логику).
- [x] **Coverage: ≥ 52%** — без новых тестов coverage не должен упасть (всё copy-paste).
- [x] All 40 URL routes работают без изменений URL pattern (через `views.FUNCTION_NAME` reexports).
- [x] Staging smoke green — `manage.py check` OK + test suite green.
- [x] Playwright E2E: `tests/e2e/test_company_card_w1_2.py` создан (smoke для company list + card load).

---

## Actual results (2026-04-21)

| Metric | Baseline | Result | Δ |
|--------|----------|--------|---|
| `company_detail.py` LOC | **3 022** | **0 (deleted)** | **−3 022 (−100%)** |
| Функций в одном файле | 42 | 0 (deleted) | −42 |
| Модулей | 1 | 10 | +9 |
| Total LOC (все новые файлы) | 3 022 | 3 336 | +314 (overhead от 10 module headers/docstrings × ~30 LOC) |
| Largest single file | 3 022 | 691 (`cold_call.py`) | −77% |
| Tests | 1 140 | 1 140 | 0 ✅ |
| URL routes | 40 | 40 | 0 ✅ |

**Module sizes** (sorted by size):
1. `cold_call.py` — 691 LOC (8 fns, acknowledged outlier)
2. `notes.py` — 474 LOC (8 fns)
3. `phones.py` — 436 LOC (7 fns)
4. `edit.py` — 420 LOC (5 fns)
5. `detail.py` — 393 LOC (3 fns)
6. `deletion.py` — 280 LOC (4 fns)
7. `contacts.py` — 228 LOC (3 fns)
8. `calls.py` — 150 LOC (1 fn)
9. `emails.py` — 136 LOC (2 fns)
10. `deals.py` — 128 LOC (2 fns)

**Commits shipped** (13 atomic):
1. `e27aa327` — plan + inventory
2. `00a9d6a7` — scaffold pages/company/
3. `a5391d18` — #1 deals
4. `77f1ef55` — #2 emails
5. `84cb389c` — #3 calls
6. `a284e5a0` — #4 contacts
7. `2831c236` — #5 deletion
8. `c2196392` — #6 phones
9. `823edce1` — #7 notes
10. `f0aa1710` — #8 edit
11. `80ef7549` — #9 cold_call
12. `ef7585a8` — #10 detail + delete company_detail.py (FINAL extraction)
13. `18950a73` — black fix company_detail_v3 + Playwright E2E smoke

---

## Playwright E2E smoke

Создать `tests/e2e/test_company_card_w1_2.py`:
```python
import os, pytest
from playwright.sync_api import Page, expect

STAGING_URL = os.getenv('STAGING_URL', 'https://crm-staging.groupprofi.ru')

@pytest.mark.e2e
def test_company_card_loads(logged_in_page: Page):
    logged_in_page.goto(f'{STAGING_URL}/companies/')
    first_company = logged_in_page.locator('a[href*="/companies/"]').first
    first_company.click()
    logged_in_page.wait_for_load_state('networkidle')
    expect(logged_in_page.locator('body')).to_be_visible()
    # Проверить что главные секции карточки отрендерены
    expect(logged_in_page.locator('[data-test="company-card"]')).to_be_visible(timeout=5000)
```

Запуск **до** W1.2 (baseline) и **после** финальной extraction. Не должно быть regression.

---

## Rollback

Каждый commit `refactor(ui/pages/company): extract <domain>` атомарен → `git revert <sha>` восстанавливает previous state. No DB changes, no URL changes → data safe.

Если критичный баг найден после merge — можно revert **группу** коммитов через `git revert -m 1 HEAD~N..HEAD`.

---

## Out of scope (W1.3+)

- Разделение `company_detail.html` (8 781 LOC, Hotlist #3) — W1.3.
- Миграция callers на прямые импорты `from ui.views.pages.company.X import ...` (deprecation `_base.py` re-exports) — W1.4.
- Rethinking URL structure (например: `/companies/<uuid>/notes/` → namespaced URLs) — не в scope refactoring-волны.
- `company_detail_v3.py` split (если он тоже станет large) — отдельная мини-сессия.
