"""
Пакет views для mailer.
Экспортирует все view-функции из подмодулей для совместимости с urls.py.
"""

from mailer.views.campaigns import (
    campaign_attachment_delete,
    campaign_attachment_download,
    campaign_clone,
    campaign_create,
    campaign_create_from_template,
    campaign_delete,
    campaign_detail,
    campaign_edit,
    campaign_export_failed,
    campaign_html_preview,
    campaign_retry_failed,
    campaign_save_as_template,
    campaign_template_delete,
    campaign_templates,
    campaigns,
)
from mailer.views.polling import (
    campaign_progress_poll,
    mail_progress_poll,
)
from mailer.views.recipients import (
    campaign_add_email,
    campaign_clear,
    campaign_generate_recipients,
    campaign_pick,
    campaign_recipient_add,
    campaign_recipient_delete,
    campaign_recipients_bulk_delete,
    campaign_recipients_reset,
)
from mailer.views.sending import (
    campaign_pause,
    campaign_resume,
    campaign_start,
    campaign_test_send,
)
from mailer.views.settings import (
    mail_admin,
    mail_quota_poll,
    mail_settings,
    mail_signature,
)
from mailer.views.unsubscribe import (
    mail_unsubscribes_clear,
    mail_unsubscribes_delete,
    mail_unsubscribes_list,
    unsubscribe,
)

__all__ = [
    "campaign_add_email",
    "campaign_attachment_delete",
    "campaign_attachment_download",
    "campaign_clear",
    "campaign_clone",
    "campaign_create",
    "campaign_create_from_template",
    "campaign_delete",
    "campaign_detail",
    "campaign_edit",
    "campaign_export_failed",
    "campaign_generate_recipients",
    "campaign_html_preview",
    "campaign_pause",
    "campaign_pick",
    "campaign_progress_poll",
    "campaign_recipient_add",
    "campaign_recipient_delete",
    "campaign_recipients_bulk_delete",
    "campaign_recipients_reset",
    "campaign_resume",
    "campaign_retry_failed",
    "campaign_save_as_template",
    "campaign_start",
    "campaign_template_delete",
    "campaign_templates",
    "campaign_test_send",
    "campaigns",
    "mail_admin",
    "mail_progress_poll",
    "mail_quota_poll",
    "mail_settings",
    "mail_signature",
    "mail_unsubscribes_clear",
    "mail_unsubscribes_delete",
    "mail_unsubscribes_list",
    "unsubscribe",
]
