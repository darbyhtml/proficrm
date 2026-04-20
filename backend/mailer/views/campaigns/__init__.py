"""
Пакет views/campaigns — CRUD, детали, файлы и шаблоны кампаний.
Импортируйте из этого пакета напрямую; подмодули можно редактировать независимо.
"""

from mailer.views.campaigns.crud import (
    campaign_clone,
    campaign_create,
    campaign_delete,
    campaign_edit,
)
from mailer.views.campaigns.files import (
    campaign_attachment_delete,
    campaign_attachment_download,
    campaign_export_failed,
    campaign_html_preview,
    campaign_retry_failed,
)
from mailer.views.campaigns.list_detail import campaign_detail, campaigns
from mailer.views.campaigns.templates_views import (
    campaign_create_from_template,
    campaign_save_as_template,
    campaign_template_delete,
    campaign_templates,
)

__all__ = [
    "campaign_attachment_delete",
    "campaign_attachment_download",
    "campaign_clone",
    "campaign_create",
    "campaign_create_from_template",
    "campaign_delete",
    "campaign_detail",
    "campaign_edit",
    "campaign_export_failed",
    "campaign_html_preview",
    "campaign_retry_failed",
    "campaign_save_as_template",
    "campaign_template_delete",
    "campaign_templates",
    "campaigns",
]
