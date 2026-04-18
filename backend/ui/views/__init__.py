from __future__ import annotations
# Auto-generated package init — re-exports all public view names.

from ui.views._base import RESPONSIBLE_FILTER_NONE, STRONG_CONFIRM_THRESHOLD  # noqa: F401
from ui.views.dashboard import (
    view_as_update, view_as_reset,
    dashboard, dashboard_poll,
    analytics, analytics_user,
    help_page,
    preferences, preferences_ui, preferences_company_detail_view_mode,
    preferences_v2_scale,
    preferences_mail, preferences_profile, preferences_password,
    preferences_absence_create, preferences_absence_delete,
    preferences_mail_signature, preferences_avatar_upload,
    preferences_avatar_delete, preferences_table_settings,
)  # noqa: F401
from ui.views.reports import (
    cold_calls_report_day, cold_calls_report_month,
    cold_calls_report_last_7_days,
)  # noqa: F401
from ui.views.company_list import (
    company_list, company_list_ajax,
    company_bulk_transfer_preview, company_bulk_transfer,
    company_export, company_create,
    company_autocomplete, company_duplicates,
)  # noqa: F401
from ui.views.company_detail import (
    company_detail, company_tasks_history, company_timeline_items,
    company_delete_request_create, company_delete_request_cancel,
    company_delete_request_approve, company_delete_direct,
    company_contract_update,
    company_cold_call_toggle, company_cold_call_reset,
    contact_cold_call_toggle, contact_cold_call_reset,
    contact_phone_cold_call_toggle, contact_phone_cold_call_reset,
    company_phone_cold_call_toggle, company_phone_cold_call_reset,
    company_main_phone_update, company_phone_value_update,
    company_phone_create, company_main_email_update,
    company_email_value_update,
    company_main_phone_comment_update, company_phone_comment_update,
    contact_phone_comment_update,
    company_note_pin_toggle,
    company_note_attachment_open, company_note_attachment_by_id_open,
    company_note_attachment_by_id_download, company_note_attachment_download,
    company_edit, company_transfer, company_update, company_inline_update,
    contact_create, contact_edit, contact_delete,
    company_note_add, company_note_edit, company_note_delete,
    company_deal_add, company_deal_delete,
    phone_call_create,
)  # noqa: F401
from ui.views.tasks import (
    task_list, task_create, task_create_v2_partial,
    task_view_v2_partial, task_edit_v2_partial,
    task_delete, task_bulk_reassign,
    task_bulk_reschedule, task_bulk_reschedule_preview,
    task_bulk_reschedule_undo,
    task_set_status, task_add_comment,
    task_view, task_edit,
    _apply_task_filters_for_bulk_ui,  # accessed by tasksapp/tests.py
    _create_note_from_task,  # accessed by tasksapp/management/commands/cleanup_old_tasks.py
)  # noqa: F401
from ui.views.settings_core import (
    settings_dashboard, settings_announcements,
    settings_access, settings_access_role,
    settings_branches, settings_branch_create, settings_branch_edit,
    settings_users, settings_user_create, settings_user_edit,
    settings_user_magic_link_generate, settings_user_logout,
    settings_user_form_ajax, settings_user_update_ajax, settings_user_delete,
    settings_dicts,
    settings_company_status_create, settings_company_status_edit,
    settings_company_status_delete,
    settings_company_sphere_create, settings_company_sphere_edit,
    settings_company_sphere_delete,
    settings_contract_type_create, settings_contract_type_edit,
    settings_contract_type_delete,
    settings_task_type_create, settings_task_type_edit,
    settings_task_type_delete,
    settings_activity,
    settings_error_log, settings_error_log_resolve,
    settings_error_log_unresolve, settings_error_log_details,
)  # noqa: F401
from ui.views.settings_integrations import (
    settings_import, settings_import_tasks,
    settings_amocrm, settings_amocrm_callback,
    settings_amocrm_disconnect, settings_amocrm_migrate,
    settings_amocrm_migrate_progress, settings_amocrm_contacts_dry_run,
    settings_amocrm_debug_contacts,
    settings_company_columns, settings_security,
    settings_mobile_overview, settings_mobile_devices,
    settings_mobile_device_detail,
    settings_calls_stats, settings_calls_manager_detail,
)  # noqa: F401
# F6 R1+R2: SMTP onboarding / Fernet re-save UI + расширенная конфигурация
from ui.views.settings_mail import (
    settings_mail_setup,
    settings_mail_save_password,
    settings_mail_test_send,
    settings_mail_save_config,
    settings_mail_toggle_enabled,
)  # noqa: F401

# F7 R1: ролевые KPI-дашборды v2
from ui.views.analytics_v2 import analytics_v2_home  # noqa: F401

# F4 R3: preview 3 вариантов редизайна карточки компании
from ui.views.company_detail_v3 import company_detail_v3_preview  # noqa: F401

# F9 UI: управление APK-билдами CRMProfiDialer
from ui.views.settings_mobile_apps import (
    settings_mobile_apps,
    settings_mobile_apps_upload,
    settings_mobile_apps_toggle,
)  # noqa: F401
from ui.views.mobile import (
    mobile_app_page, mobile_app_download, mobile_app_qr_image,
)  # noqa: F401
from ui.views.settings_messenger import (
    settings_messenger_overview, settings_messenger_source_choose,
    settings_messenger_inbox_edit, settings_messenger_inbox_ready,
    settings_messenger_routing_list, settings_messenger_routing_edit,
    settings_messenger_routing_delete,
    settings_messenger_health, settings_messenger_analytics,
    settings_messenger_canned_list, settings_messenger_canned_edit,
    settings_messenger_canned_delete,
    settings_messenger_campaigns, settings_messenger_automation,
)  # noqa: F401
from ui.views.messenger_panel import (
    messenger_conversations_unified, messenger_agent_status,
)  # noqa: F401
