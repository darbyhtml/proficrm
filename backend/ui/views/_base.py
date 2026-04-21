from __future__ import annotations

# phonebridge models — lazy import в функциях, где используются (company_detail, settings_integrations)
import json
import logging
import mimetypes
import os
import re
import uuid
from datetime import date as _date
from datetime import datetime, timedelta
from datetime import time as datetime_time
from decimal import Decimal
from uuid import UUID

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.core.validators import validate_email
from django.db import IntegrityError, models, transaction
from django.db.models import (
    Avg,
    Count,
    Exists,
    F,
    Max,
    OuterRef,
    Prefetch,
    Q,
)
from django.http import (
    FileResponse,
    Http404,
    HttpRequest,
    HttpResponse,
    HttpResponseNotFound,
    JsonResponse,
    StreamingHttpResponse,
)
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from accounts.models import Branch, MagicLinkToken, User
from audit.models import ActivityEvent
from audit.service import log_event
from companies.decorators import require_can_view_company, require_can_view_note_company
from companies.models import (
    Company,
    CompanyDeal,
    CompanyDeletionRequest,
    CompanyEmail,
    CompanyHistoryEvent,
    CompanyNote,
    CompanyNoteAttachment,
    CompanyPhone,
    CompanySearchIndex,
    CompanySphere,
    CompanyStatus,
    Contact,
    ContactEmail,
    ContactPhone,
    ContractType,
    Region,
)
from companies.permissions import (
    can_edit_company as can_edit_company_perm,
)
from companies.permissions import (
    can_transfer_companies,
    can_transfer_company,
    get_transfer_targets,
    get_users_for_lists,
)
from companies.permissions import (
    editable_company_qs as editable_company_qs_perm,
)
from companies.policy import can_view_company as can_view_company_policy
from companies.policy import visible_companies_qs
from companies.services import resolve_target_companies
from notifications.models import Notification
from notifications.service import notify
from tasksapp.models import Task, TaskComment, TaskEvent, TaskType
from tasksapp.policy import can_manage_task_status, visible_tasks_qs

logger = logging.getLogger(__name__)

from django.core.exceptions import PermissionDenied

from accounts.permissions import get_effective_user, get_view_as_user, require_admin
from policy.decorators import policy_required
from policy.engine import decide as policy_decide
from ui.cleaners import clean_int_id
from ui.models import UiGlobalConfig, UiUserPreference
from ui.templatetags.ui_extras import format_phone

from ..forms import (
    BranchForm,
    CompanyContractForm,
    CompanyCreateForm,
    CompanyEditForm,
    CompanyInlineEditForm,
    CompanyListColumnsForm,
    CompanyNoteForm,
    CompanyQuickEditForm,
    CompanySphereForm,
    CompanyStatusForm,
    ContactEmailFormSet,
    ContactForm,
    ContactPhoneFormSet,
    ContractTypeForm,
    ImportCompaniesForm,
    ImportTasksIcsForm,
    TaskEditForm,
    TaskForm,
    TaskTypeForm,
    UserCreateForm,
    UserEditForm,
)

# Константы для фильтров
RESPONSIBLE_FILTER_NONE = "none"  # Значение для фильтрации компаний без ответственного
STRONG_CONFIRM_THRESHOLD = 200  # Порог, после которого для bulk переноса включается усиленное подтверждение (логируется как strong_confirm_required)

# Explicitly list all names (including private helpers) so that
# "from ui.views._base import *" exports them into sub-modules.
__all__ = [
    # constants
    "RESPONSIBLE_FILTER_NONE",
    "STRONG_CONFIRM_THRESHOLD",
    # logging (sub-modules override this with their own logger)
    "logger",
    # private helpers
    "_dup_reasons",
    "_can_edit_company",
    "_editable_company_qs",
    "_company_branch_id",
    "_can_delete_company",
    "_notify_branch_leads",
    "_detach_client_branches",
    "_notify_head_deleted_with_branches",
    "_invalidate_company_count_cache",
    "_companies_with_overdue_flag",
    "_normalize_phone_for_search",
    "_normalize_for_search",
    "_tokenize_search_query",
    "_normalize_email_for_search",
    "_is_ajax",
    "_safe_next_v3",
    "_dt_label",
    "_cold_call_json",
    # sub-functions of _apply_company_filters
    "_cf_get_str_param",
    "_cf_get_list_param",
    "_cf_get_list_param_stripped",
    "_cf_to_int_list",
    "_filter_by_search",
    "_filter_by_selects",
    "_filter_by_tasks",
    "_filter_by_responsible",
    "_apply_company_filters",
    "_qs_without_page",
    # all imported names that sub-modules need
    "datetime",
    "datetime_time",
    "timedelta",
    "UUID",
    "Decimal",
    "login_required",
    "messages",
    "Paginator",
    "Exists",
    "OuterRef",
    "Q",
    "F",
    "Count",
    "Max",
    "Prefetch",
    "Avg",
    "models",
    "transaction",
    "IntegrityError",
    "HttpRequest",
    "HttpResponse",
    "StreamingHttpResponse",
    "JsonResponse",
    "FileResponse",
    "Http404",
    "HttpResponseNotFound",
    "get_object_or_404",
    "redirect",
    "render",
    "timezone",
    "ValidationError",
    "validate_email",
    "Branch",
    "User",
    "MagicLinkToken",
    "ActivityEvent",
    "log_event",
    "ContractType",
    "Company",
    "CompanyDeal",
    "CompanyHistoryEvent",
    "CompanyNote",
    "CompanyNoteAttachment",
    "CompanySphere",
    "CompanyStatus",
    "Region",
    "Contact",
    "ContactEmail",
    "ContactPhone",
    "CompanyDeletionRequest",
    "CompanyEmail",
    "CompanyPhone",
    "CompanySearchIndex",
    "resolve_target_companies",
    "can_edit_company_perm",
    "editable_company_qs_perm",
    "can_transfer_company",
    "get_transfer_targets",
    "get_users_for_lists",
    "can_transfer_companies",
    "can_view_company_policy",
    "visible_companies_qs",
    "require_can_view_company",
    "require_can_view_note_company",
    "Task",
    "TaskComment",
    "TaskEvent",
    "TaskType",
    "visible_tasks_qs",
    "can_manage_task_status",
    "Notification",
    "notify",
    # phonebridge models убраны из __all__ — lazy import
    "json",
    "mimetypes",
    "os",
    "re",
    "uuid",
    "_date",
    "cache",
    "UiGlobalConfig",
    "UiUserPreference",
    "require_admin",
    "get_effective_user",
    "get_view_as_user",
    "policy_required",
    "policy_decide",
    "PermissionDenied",
    "format_phone",
    "clean_int_id",
    "CompanyCreateForm",
    "CompanyQuickEditForm",
    "CompanyContractForm",
    "CompanyEditForm",
    "CompanyInlineEditForm",
    "CompanyNoteForm",
    "ContactEmailFormSet",
    "ContactForm",
    "ContactPhoneFormSet",
    "TaskForm",
    "TaskEditForm",
    "BranchForm",
    "CompanySphereForm",
    "CompanyStatusForm",
    "ContractTypeForm",
    "TaskTypeForm",
    "UserCreateForm",
    "UserEditForm",
    "ImportCompaniesForm",
    "ImportTasksIcsForm",
    "CompanyListColumnsForm",
    # cross-module helpers (used in multiple sub-modules)
    "_can_view_cold_call_reports",
    "_cold_call_confirm_q",
    "_month_start",
    "_add_months",
    "_month_label",
    "_can_manage_task_status_ui",
    "_can_edit_task_ui",
    "_can_delete_task_ui",
]


# Company access/edit/delete/notifications/cache helpers
# extracted to helpers/companies.py (W1.1).
# Re-exports at end of file для backward compat.


# Search normalizers extracted to helpers/search.py (W1.1).
# Re-exports at end of file для backward compat.


# Request helpers extracted to helpers/http.py (W1.1).
# Re-exports at end of file для backward compat.

# Company filter helpers extracted to helpers/company_filters.py (W1.1).
# Re-exports at end of file для backward compat.



# ---------------------------------------------------------------------------
# Cross-module helpers: defined here so all sub-modules can access them via
# "from ui.views._base import *"
# ---------------------------------------------------------------------------


# Cold-call + month utilities extracted to helpers/cold_call.py (W1.1).
# Re-exports at end of file для backward compat.


# Task access helpers extracted to helpers/tasks.py (W1.1).
# Re-exports at end of file для backward compat.


# -----------------------------------------------------------------------------
# W1.1 (2026-04-21): Helper functions extracted to ui/views/helpers/*.
# Re-exports preserved для backward compatibility (existing imports работают).
# In new code, prefer direct imports из ui.views.helpers.<submodule>.
# -----------------------------------------------------------------------------

from ui.views.helpers.cold_call import (
    _add_months,
    _can_view_cold_call_reports,
    _cold_call_confirm_q,
    _month_label,
    _month_start,
)
from ui.views.helpers.companies import (
    _can_delete_company,
    _can_edit_company,
    _companies_with_overdue_flag,
    _company_branch_id,
    _detach_client_branches,
    _dup_reasons,
    _editable_company_qs,
    _invalidate_company_count_cache,
    _notify_branch_leads,
    _notify_head_deleted_with_branches,
)
from ui.views.helpers.company_filters import (
    _apply_company_filters,
    _cf_get_list_param,
    _cf_get_list_param_stripped,
    _cf_get_str_param,
    _cf_to_int_list,
    _filter_by_responsible,
    _filter_by_search,
    _filter_by_selects,
    _filter_by_tasks,
    _qs_without_page,
)
from ui.views.helpers.http import (
    _cold_call_json,
    _dt_label,
    _is_ajax,
    _safe_next_v3,
)
from ui.views.helpers.search import (
    _normalize_email_for_search,
    _normalize_for_search,
    _normalize_phone_for_search,
    _tokenize_search_query,
)
from ui.views.helpers.tasks import (
    _can_delete_task_ui,
    _can_edit_task_ui,
    _can_manage_task_status_ui,
)

