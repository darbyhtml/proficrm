"""
Пакет views/campaigns — CRUD, детали, файлы и шаблоны кампаний.
Импортируйте из этого пакета напрямую; подмодули можно редактировать независимо.
"""

from mailer.views.campaigns.list_detail import campaigns, campaign_detail
from mailer.views.campaigns.crud import (
    campaign_create,
    campaign_edit,
    campaign_delete,
    campaign_clone,
)
from mailer.views.campaigns.files import (
    campaign_html_preview,
    campaign_attachment_download,
    campaign_attachment_delete,
    campaign_export_failed,
    campaign_retry_failed,
)
from mailer.views.campaigns.templates_views import (
    campaign_save_as_template,
    campaign_create_from_template,
    campaign_template_delete,
    campaign_templates,
)

__all__ = [
    "campaigns",
    "campaign_detail",
    "campaign_create",
    "campaign_edit",
    "campaign_delete",
    "campaign_clone",
    "campaign_html_preview",
    "campaign_attachment_download",
    "campaign_attachment_delete",
    "campaign_export_failed",
    "campaign_retry_failed",
    "campaign_save_as_template",
    "campaign_create_from_template",
    "campaign_template_delete",
    "campaign_templates",
]
