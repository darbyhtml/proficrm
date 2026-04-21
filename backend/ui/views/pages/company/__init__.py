"""Company card view modules (W1.2 refactor).

Split из монолитного `backend/ui/views/company_detail.py` (3 022 LOC) на
10 тематических модулей:

- `detail.py` — main card + timeline + tasks history
- `edit.py` — edit/update/inline-update/transfer/contract
- `deletion.py` — delete workflow (request + approval + direct)
- `contacts.py` — contact CRUD
- `notes.py` — notes CRUD + attachments + pin
- `deals.py` — deal CRUD
- `cold_call.py` — cold-call toggles/resets (8 endpoints)
- `phones.py` — phone CRUD + comments
- `emails.py` — email updates
- `calls.py` — PhoneBridge call logging

Dependency direction: `pages/company/*` → `ui.views._base` → `ui.views.helpers.*`.
Modules не импортируют друг у друга.

Backward compat: `backend/ui/views/__init__.py` re-exports все функции —
существующие URL routing через `views.FUNCTION_NAME` работает без изменений.
"""
