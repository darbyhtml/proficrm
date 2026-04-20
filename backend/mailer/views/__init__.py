"""
Пакет views для mailer.
Экспортирует все view-функции из подмодулей для совместимости с urls.py.
"""

from mailer.views.campaigns import (
    campaigns,
    campaign_create,
    campaign_edit,
    campaign_detail,
    campaign_html_preview,
    campaign_attachment_download,
    campaign_attachment_delete,
    campaign_delete,
    campaign_clone,
    campaign_retry_failed,
    campaign_export_failed,
    campaign_save_as_template,
    campaign_create_from_template,
    campaign_template_delete,
    campaign_templates,
)
from mailer.views.settings import (
    mail_signature,
    mail_settings,
    mail_admin,
    mail_quota_poll,
)
from mailer.views.sending import (
    campaign_start,
    campaign_pause,
    campaign_resume,
    campaign_test_send,
)
from mailer.views.recipients import (
    campaign_pick,
    campaign_add_email,
    campaign_recipient_add,
    campaign_recipient_delete,
    campaign_recipients_bulk_delete,
    campaign_generate_recipients,
    campaign_recipients_reset,
    campaign_clear,
)
from mailer.views.polling import (
    mail_progress_poll,
    campaign_progress_poll,
)
from mailer.views.unsubscribe import (
    unsubscribe,
    mail_unsubscribes_list,
    mail_unsubscribes_delete,
    mail_unsubscribes_clear,
)

__all__ = [
    "campaigns",
    "campaign_create",
    "campaign_edit",
    "campaign_detail",
    "campaign_html_preview",
    "campaign_attachment_download",
    "campaign_attachment_delete",
    "campaign_delete",
    "campaign_clone",
    "campaign_retry_failed",
    "campaign_export_failed",
    "campaign_save_as_template",
    "campaign_create_from_template",
    "campaign_template_delete",
    "campaign_templates",
    "mail_signature",
    "mail_settings",
    "mail_admin",
    "mail_quota_poll",
    "campaign_start",
    "campaign_pause",
    "campaign_resume",
    "campaign_test_send",
    "campaign_pick",
    "campaign_add_email",
    "campaign_recipient_add",
    "campaign_recipient_delete",
    "campaign_recipients_bulk_delete",
    "campaign_generate_recipients",
    "campaign_recipients_reset",
    "campaign_clear",
    "mail_progress_poll",
    "campaign_progress_poll",
    "unsubscribe",
    "mail_unsubscribes_list",
    "mail_unsubscribes_delete",
    "mail_unsubscribes_clear",
]
