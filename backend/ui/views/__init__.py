from __future__ import annotations

# Auto-generated package init — re-exports all public view names.
from ui.views._base import RESPONSIBLE_FILTER_NONE, STRONG_CONFIRM_THRESHOLD

# F7 R1: ролевые KPI-дашборды v2
from ui.views.analytics_v2 import analytics_v2_home
from ui.views.company_detail import (
    company_cold_call_reset,
    company_cold_call_toggle,
    company_detail,
    company_phone_cold_call_reset,
    company_phone_cold_call_toggle,
    company_tasks_history,
    company_timeline_items,
    contact_cold_call_reset,
    contact_cold_call_toggle,
    contact_phone_cold_call_reset,
    contact_phone_cold_call_toggle,
)

# F4 R3: preview 3 вариантов редизайна карточки компании + quick-endpoints
from ui.views.company_detail_v3 import (
    company_detail_v3_preview,
    contact_quick_create,
)
from ui.views.company_list import (
    company_autocomplete,
    company_bulk_transfer,
    company_bulk_transfer_preview,
    company_create,
    company_duplicates,
    company_export,
    company_list,
    company_list_ajax,
)
from ui.views.dashboard import (
    analytics,
    analytics_user,
    dashboard,
    dashboard_poll,
    help_page,
    preferences,
    preferences_absence_create,
    preferences_absence_delete,
    preferences_avatar_delete,
    preferences_avatar_upload,
    preferences_company_detail_view_mode,
    preferences_mail,
    preferences_mail_signature,
    preferences_password,
    preferences_profile,
    preferences_table_settings,
    preferences_ui,
    preferences_v2_scale,
    view_as_reset,
    view_as_update,
)
from ui.views.messenger_panel import (
    messenger_agent_status,
    messenger_conversations_unified,
)
from ui.views.mobile import (
    mobile_app_download,
    mobile_app_page,
    mobile_app_qr_image,
)

# W1.2: extracted to pages/company/*
from ui.views.pages.company.calls import (
    phone_call_create,
)
from ui.views.pages.company.contacts import (
    contact_create,
    contact_delete,
    contact_edit,
)
from ui.views.pages.company.deals import (
    company_deal_add,
    company_deal_delete,
)
from ui.views.pages.company.deletion import (
    company_delete_direct,
    company_delete_request_approve,
    company_delete_request_cancel,
    company_delete_request_create,
)
from ui.views.pages.company.edit import (
    company_contract_update,
    company_edit,
    company_inline_update,
    company_transfer,
    company_update,
)
from ui.views.pages.company.emails import (
    company_email_value_update,
    company_main_email_update,
)
from ui.views.pages.company.notes import (
    company_note_add,
    company_note_attachment_by_id_download,
    company_note_attachment_by_id_open,
    company_note_attachment_download,
    company_note_attachment_open,
    company_note_delete,
    company_note_edit,
    company_note_pin_toggle,
)
from ui.views.pages.company.phones import (
    company_main_phone_comment_update,
    company_main_phone_update,
    company_phone_comment_update,
    company_phone_create,
    company_phone_delete,
    company_phone_value_update,
    contact_phone_comment_update,
)
from ui.views.reports import (
    cold_calls_report_day,
    cold_calls_report_last_7_days,
    cold_calls_report_month,
)
from ui.views.settings_core import (
    settings_access,
    settings_access_role,
    settings_activity,
    settings_announcements,
    settings_branch_create,
    settings_branch_edit,
    settings_branches,
    settings_company_sphere_create,
    settings_company_sphere_delete,
    settings_company_sphere_edit,
    settings_company_status_create,
    settings_company_status_delete,
    settings_company_status_edit,
    settings_contract_type_create,
    settings_contract_type_delete,
    settings_contract_type_edit,
    settings_dashboard,
    settings_dicts,
    settings_error_log,
    settings_error_log_details,
    settings_error_log_resolve,
    settings_error_log_unresolve,
    settings_task_type_create,
    settings_task_type_delete,
    settings_task_type_edit,
    settings_user_create,
    settings_user_delete,
    settings_user_edit,
    settings_user_form_ajax,
    settings_user_logout,
    settings_user_magic_link_generate,
    settings_user_update_ajax,
    settings_users,
)
from ui.views.settings_integrations import (
    settings_calls_manager_detail,
    settings_calls_stats,
    settings_company_columns,
    settings_import,
    settings_import_tasks,
    settings_mobile_device_detail,
    settings_mobile_devices,
    settings_mobile_overview,
    settings_security,
)

# F6 R1+R2: SMTP onboarding / Fernet re-save UI + расширенная конфигурация
from ui.views.settings_mail import (
    settings_mail_save_config,
    settings_mail_save_password,
    settings_mail_setup,
    settings_mail_test_send,
    settings_mail_toggle_enabled,
)
from ui.views.settings_messenger import (
    settings_messenger_analytics,
    settings_messenger_automation,
    settings_messenger_campaigns,
    settings_messenger_canned_delete,
    settings_messenger_canned_edit,
    settings_messenger_canned_list,
    settings_messenger_health,
    settings_messenger_inbox_edit,
    settings_messenger_inbox_ready,
    settings_messenger_overview,
    settings_messenger_routing_delete,
    settings_messenger_routing_edit,
    settings_messenger_routing_list,
    settings_messenger_source_choose,
)

# F9 UI: управление APK-билдами CRMProfiDialer
from ui.views.settings_mobile_apps import (
    settings_mobile_apps,
    settings_mobile_apps_toggle,
    settings_mobile_apps_upload,
)
from ui.views.tasks import (
    _apply_task_filters_for_bulk_ui,  # accessed by tasksapp/tests.py
    _create_note_from_task,  # accessed by tasksapp/management/commands/cleanup_old_tasks.py
    task_add_comment,
    task_bulk_reassign,
    task_bulk_reschedule,
    task_bulk_reschedule_preview,
    task_bulk_reschedule_undo,
    task_create,
    task_create_v2_partial,
    task_delete,
    task_edit,
    task_edit_v2_partial,
    task_list,
    task_set_status,
    task_view,
    task_view_v2_partial,
)
