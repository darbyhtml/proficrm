"""
Пакет tasks модуля mailer.
Все публичные имена реэкспортированы для обратной совместимости
(любой код, делающий `from mailer.tasks import X`, продолжает работать).
"""

from mailer.tasks.helpers import (
    _get_campaign_attachment_bytes,
    _is_transient_send_error,
    _is_working_hours,
    _smtp_bz_enrich_error,
    _smtp_bz_extract_tag,
    _smtp_bz_parse_campaign_recipient_from_tag,
    get_effective_quota_available,
    reserve_rate_limit_token,
)
from mailer.tasks.reconcile import (
    reconcile_campaign_queue,
)
from mailer.tasks.send import (
    send_pending_emails,
    send_test_email,
)
from mailer.tasks.sync import (
    sync_smtp_bz_delivery_events,
    sync_smtp_bz_quota,
    sync_smtp_bz_unsubscribes,
)

__all__ = [
    # helpers
    "_is_transient_send_error",
    "_smtp_bz_enrich_error",
    "_smtp_bz_extract_tag",
    "_smtp_bz_parse_campaign_recipient_from_tag",
    "_get_campaign_attachment_bytes",
    "_is_working_hours",
    "reserve_rate_limit_token",
    "get_effective_quota_available",
    # send
    "send_pending_emails",
    "send_test_email",
    # sync
    "sync_smtp_bz_delivery_events",
    "sync_smtp_bz_quota",
    "sync_smtp_bz_unsubscribes",
    # reconcile
    "reconcile_campaign_queue",
]
