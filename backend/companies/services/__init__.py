"""
Сервисный пакет `companies.services`.

Исторически все бизнес-сервисы были в плоском файле `companies/services.py`.
2026-04-20 файл превращён в пакет (подготовка к рефакторингу god-view
`ui/views/company_detail.py` 2883 LOC → ~500 LOC, см. docs/runbooks/03
и refactoring-specialist plan).

Этот `__init__.py` реэкспортирует все публичные имена из `company_core`
для полной обратной совместимости — существующие 18+ импортов
`from companies.services import ...` продолжают работать без изменений.

Новые модули (будут добавлены в phase 1-5 рефакторинга):
- `timeline`       — сборка timeline для карточки компании (phase 1)
- `company_phones` — CRUD для CompanyPhone (phase 2)
- `company_emails` — CRUD для CompanyEmail (phase 2)
- `company_delete` — workflow удаления компании (phase 3)
- `company_overview` — context-builder для company_detail (phase 5)

См. `docs/decisions.md` (ADR 2026-04-20 «Companies services package»).
"""

from __future__ import annotations

# Обратная совместимость: все публичные имена из company_core.py
# Перечисляем явно, чтобы не терять видимость при tooling (pyflakes, mypy, IDE).
from companies.services.company_core import (
    # Константы (используются в тестах и UI)
    ANNUAL_CONTRACT_DANGER_AMOUNT,
    ANNUAL_CONTRACT_WARN_AMOUNT,
    DASHBOARD_CONTRACTS_LIMIT,
    ColdCallService,
    # Классы-сервисы
    CompanyService,
    _get_annual_contract_alert,
    # Функции верхнего уровня (используются в views/dashboard/tests)
    get_contract_alert,
    get_dashboard_contracts,
    get_org_companies,
    get_org_root,
    get_worktime_status,
    resolve_target_companies,
)

# Phase 3 extract (2026-04-20): единый workflow удаления компании.
# Консолидирует общую логику company_delete_direct и company_delete_request_approve.
from companies.services.company_delete import (
    CompanyDeletionError,
    execute_company_deletion,
)
from companies.services.company_emails import (
    check_email_duplicate,
    validate_email_value,
)

# Phase 2 extract (2026-04-20): валидация и уникальность phone/email.
# Устраняет тройное дублирование валидации телефона и двойное — email.
from companies.services.company_phones import (
    check_phone_duplicate,
    validate_phone_comment,
    validate_phone_main,
    validate_phone_strict,
)

# Phase 1 extract (2026-04-20): единая сборка timeline для карточки компании.
# Устраняет дублирование между company_detail и company_timeline_items views.
from companies.services.timeline import build_company_timeline

__all__ = [
    "CompanyService",
    "ColdCallService",
    "get_contract_alert",
    "_get_annual_contract_alert",
    "get_dashboard_contracts",
    "get_worktime_status",
    "get_org_root",
    "get_org_companies",
    "resolve_target_companies",
    "ANNUAL_CONTRACT_DANGER_AMOUNT",
    "ANNUAL_CONTRACT_WARN_AMOUNT",
    "DASHBOARD_CONTRACTS_LIMIT",
    "build_company_timeline",
    # Phase 2
    "validate_phone_strict",
    "validate_phone_main",
    "check_phone_duplicate",
    "validate_phone_comment",
    "validate_email_value",
    "check_email_duplicate",
    # Phase 3
    "execute_company_deletion",
    "CompanyDeletionError",
]
