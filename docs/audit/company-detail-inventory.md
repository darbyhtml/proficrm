# company_detail.py — Inventory (pre-W1.2)

**Snapshot**: 2026-04-21, HEAD `56a11f3a`.

- **Total LOC**: 3 022 (не 2 698 как в Wave 0.1 audit — post-audit добавления F4 R3 v3b 18-19.04 увеличили файл на ~324 LOC)
- **Classes**: 0
- **Functions**: **42** (+1 commented — итого 43 defs)
- **URL routes served**: **40** (см. §URL mapping)
- **External consumers**: **2**
  1. `backend/ui/views/__init__.py` — re-exports 41 функций из 42 (только `company_tasks_history` пропущен, но это баг __init__ — функция используется по URL)
  2. `backend/ui/views/company_detail_v3.py` — импортирует `_can_edit_company` (1 запись, можно поправить напрямую на `_base`)

---

## Function table (в порядке появления)

| # | Line | Function | LOC | Domain |
|---|---|---|---|---|
| 1 | 84 | `company_detail` | 271 | **detail** (main card) |
| 2 | 355 | `company_tasks_history` | 30 | **detail** (tasks mini-page) |
| 3 | 385 | `company_delete_request_create` | 77 | **deletion** |
| 4 | 462 | `company_delete_request_cancel` | 59 | **deletion** |
| 5 | 521 | `company_delete_request_approve` | 67 | **deletion** |
| 6 | 588 | `company_delete_direct` | 39 | **deletion** |
| 7 | 627 | `company_contract_update` | 51 | **edit** (contract slice) |
| 8 | 678 | `company_cold_call_toggle` | 97 | **cold_call** |
| 9 | 775 | `contact_cold_call_toggle` | 83 | **cold_call** |
| 10 | 858 | `company_cold_call_reset` | 68 | **cold_call** |
| 11 | 926 | `contact_cold_call_reset` | 70 | **cold_call** |
| 12 | 996 | `contact_phone_cold_call_toggle` | 97 | **cold_call** |
| 13 | 1093 | `contact_phone_cold_call_reset` | 73 | **cold_call** |
| 14 | 1166 | `company_phone_cold_call_toggle` | 92 | **cold_call** |
| 15 | 1258 | `company_phone_cold_call_reset` | 70 | **cold_call** |
| 16 | 1328 | `company_main_phone_update` | 66 | **phones** |
| 17 | 1394 | `company_phone_value_update` | 65 | **phones** |
| 18 | 1459 | `company_phone_delete` | 35 | **phones** |
| 19 | 1494 | `company_phone_create` | 89 | **phones** |
| 20 | 1583 | `company_main_email_update` | 52 | **emails** |
| 21 | 1635 | `company_email_value_update` | 52 | **emails** |
| 22 | 1687 | `company_main_phone_comment_update` | 43 | **phones** (comment) |
| 23 | 1730 | `company_phone_comment_update` | 44 | **phones** (comment) |
| 24 | 1774 | `contact_phone_comment_update` | 54 | **phones** (contact comment) |
| 25 | 1828 | `company_note_pin_toggle` | 57 | **notes** |
| 26 | 1885 | `company_note_attachment_open` | 30 | **notes** (attachment) |
| 27 | 1915 | `company_note_attachment_by_id_open` | 30 | **notes** (attachment) |
| 28 | 1945 | `company_note_attachment_by_id_download` | 30 | **notes** (attachment) |
| 29 | 1975 | `company_note_attachment_download` | 32 | **notes** (attachment) |
| 30 | 2007 | `company_edit` | 150 | **edit** |
| 31 | 2157 | `company_transfer` | 36 | **edit** |
| 32 | 2193 | `company_update` | 36 | **edit** |
| 33 | 2229 | `company_inline_update` | 99 | **edit** |
| 34 | 2328 | `contact_create` | 75 | **contacts** |
| 35 | 2403 | `contact_edit` | 79 | **contacts** |
| 36 | 2482 | `contact_delete` | 39 | **contacts** |
| 37 | 2521 | `company_note_add` | 45 | **notes** |
| 38 | 2566 | `company_note_edit` | 147 | **notes** |
| 39 | 2713 | `company_note_delete` | 55 | **notes** |
| 40 | 2768 | `company_deal_add` | 60 | **deals** |
| 41 | 2828 | `company_deal_delete` | 34 | **deals** |
| 42 | 2862 | `phone_call_create` | 124 | **calls** (phonebridge logging) |
| 43 | 2986 | `company_timeline_items` | 37 | **history** (AJAX items) |

---

## Domain grouping (revised — 10 modules)

| # | Module | Functions | Total LOC | Notes |
|---|--------|-----------|-----------|-------|
| 1 | `detail.py` | `company_detail`, `company_tasks_history`, `company_timeline_items` | ~338 | Main card + mini-pages |
| 2 | `edit.py` | `company_edit`, `company_update`, `company_inline_update`, `company_transfer`, `company_contract_update` | ~372 | Form editing |
| 3 | `deletion.py` | 4 delete funcs | ~242 | Full delete workflow |
| 4 | `contacts.py` | `contact_create`, `contact_edit`, `contact_delete` | ~193 | Contact CRUD |
| 5 | `notes.py` | 8 note funcs (CRUD + attachments + pin) | ~426 | Notes + attachments **(может чуть превысить 400)** |
| 6 | `deals.py` | `company_deal_add`, `company_deal_delete` | ~94 | Deal CRUD |
| 7 | `cold_call.py` | 8 cold-call toggles/resets | ~650 | **Самый большой — возможно разделить** |
| 8 | `phones.py` | 4 phone CRUD + 3 comment funcs | ~396 | Phone CRUD + comments |
| 9 | `emails.py` | `company_main_email_update`, `company_email_value_update` | ~104 | Email updates |
| 10 | `calls.py` | `phone_call_create` | ~124 | PhoneBridge call logging |

**Total after headers/imports overhead**: ≈ 2 950-3 050 LOC (baseline 3 022 + overhead ~30-50 LOC на импорты/docstrings × 10 модулей).

### Risk hotspots
- **`cold_call.py` = 650 LOC** — превышает 400-таргет. Вариант: разделить на `cold_call_company.py` (toggle/reset по компании + company_phone) и `cold_call_contact.py` (toggle/reset по контакту + contact_phone). Решение: **оставить одним модулем**, но задокументировать что это orchestrator-сегмент (внутри функции практически идентичны, разделение будет seksual).
- **`notes.py` = 426 LOC** — на грани. OK.
- **`detail.py` = 338 LOC** — ok.

---

## URL routes (40 шт.)

Все используют `views.NAME` через `ui.views.__init__.py` re-exports:

```
/companies/<uuid>/                                       company_detail
/companies/<uuid>/edit/                                  company_edit
/companies/<uuid>/tasks-history/                         company_tasks_history
/companies/<uuid>/timeline/items/                        company_timeline_items
/companies/<uuid>/update/                                company_update
/companies/<uuid>/inline-update/                         company_inline_update
/companies/<uuid>/main-phone-update/                     company_main_phone_update
/companies/<uuid>/main-email-update/                     company_main_email_update
/companies/<uuid>/main-phone-comment-update/             company_main_phone_comment_update
/companies/<uuid>/contract-update/                       company_contract_update
/companies/<uuid>/cold-call/toggle/                      company_cold_call_toggle
/companies/<uuid>/cold-call/reset/                       company_cold_call_reset
/companies/<uuid>/transfer/                              company_transfer
/companies/<uuid>/delete-request/                        company_delete_request_create
/companies/<uuid>/delete-request/<id>/cancel/            company_delete_request_cancel
/companies/<uuid>/delete-request/<id>/approve/           company_delete_request_approve
/companies/<uuid>/delete/                                company_delete_direct
/companies/<uuid>/contacts/new/                          contact_create
/contacts/<uuid>/edit/                                   contact_edit
/contacts/<uuid>/delete/                                 contact_delete
/contacts/<uuid>/cold-call/toggle/                       contact_cold_call_toggle
/contacts/<uuid>/cold-call/reset/                        contact_cold_call_reset
/contact-phones/<id>/cold-call/toggle/                   contact_phone_cold_call_toggle
/contact-phones/<id>/cold-call/reset/                    contact_phone_cold_call_reset
/contact-phones/<id>/comment-update/                     contact_phone_comment_update
/company-phones/<id>/cold-call/toggle/                   company_phone_cold_call_toggle
/company-phones/<id>/cold-call/reset/                    company_phone_cold_call_reset
/company-phones/<id>/comment-update/                     company_phone_comment_update
/company-phones/<id>/value-update/                       company_phone_value_update
/company-phones/<id>/delete/                             company_phone_delete
/companies/<uuid>/phones/create/                         company_phone_create
/company-emails/<id>/value-update/                       company_email_value_update
/companies/<uuid>/notes/add/                             company_note_add
/companies/<uuid>/notes/<id>/edit/                       company_note_edit
/companies/<uuid>/notes/<id>/delete/                     company_note_delete
/companies/<uuid>/notes/<id>/pin-toggle/                 company_note_pin_toggle
/companies/<uuid>/notes/<id>/attachment/                 company_note_attachment_open
/companies/<uuid>/notes/<id>/attachment/download/        company_note_attachment_download
/companies/<uuid>/notes/<id>/attachment/<aid>/           company_note_attachment_by_id_open
/companies/<uuid>/notes/<id>/attachment/<aid>/download/  company_note_attachment_by_id_download
/companies/<uuid>/deals/add/                             company_deal_add
/companies/<uuid>/deals/<id>/delete/                     company_deal_delete
/phone/call/                                             phone_call_create
```

---

## External consumers (backward compat requirement)

| Consumer | Line | Import | Resolution |
|----------|------|--------|-----------|
| `backend/ui/views/__init__.py` | 8-52 | re-exports 41 функций | Обновить на импорт из новых модулей `pages/company/*` |
| `backend/ui/views/company_detail_v3.py` | 292 | `from ui.views.company_detail import _can_edit_company` | Заменить на `from ui.views._base import _can_edit_company` (уже re-export из helpers/companies.py) |

**Decision on shim**: после обновления двух consumers — `company_detail.py` можно **удалить** (Option A clean). Никто больше не импортирует напрямую.

---

## Shared imports (unified via `_base.py`)

Все функции уже используют унифицированный импорт `from ui.views._base import (...)` (W1 W1.1 paid off). Новые модули будут повторять этот паттерн.

Никаких in-file shared helpers (все вспомогательные функции уже в `helpers/*` после W1.1).
