"""
Сервис для работы с очередью кампаний.
Единая точка для отложения кампаний с фиксацией причин и времени возобновления.
"""
from __future__ import annotations

import logging
import datetime
from django.utils import timezone
from django.core.cache import cache
from mailer.models import CampaignQueue, Campaign
from mailer.constants import (
    DEFER_REASON_DAILY_LIMIT,
    DEFER_REASON_QUOTA,
    DEFER_REASON_OUTSIDE_HOURS,
    DEFER_REASON_RATE_HOUR,
    DEFER_REASON_TRANSIENT_ERROR,
)

logger = logging.getLogger(__name__)


def defer_queue(
    queue_entry: CampaignQueue,
    reason: str,
    until: timezone.datetime,
    notify: bool = True,
) -> None:
    """
    Откладывает кампанию в очереди с фиксацией причины и времени возобновления.
    
    Обязательно:
    - Выставляет status=PENDING
    - Сбрасывает started_at
    - Записывает defer_reason и deferred_until
    
    Args:
        queue_entry: Запись в CampaignQueue
        reason: Причина отложения (из constants.DEFER_REASON_*)
        until: Время, когда можно продолжить обработку
        notify: Отправлять ли уведомление пользователю (с дедупликацией)
    """
    if not queue_entry:
        logger.warning("defer_queue called with None queue_entry")
        return
    
    # Обновляем статус очереди
    queue_entry.status = CampaignQueue.Status.PENDING
    queue_entry.started_at = None
    queue_entry.deferred_until = until
    queue_entry.defer_reason = reason
    queue_entry.save(update_fields=["status", "started_at", "deferred_until", "defer_reason"])
    
    logger.info(
        f"Campaign {queue_entry.campaign_id} deferred: reason={reason}, until={until}",
        extra={
            "campaign_id": str(queue_entry.campaign_id),
            "queue_id": str(queue_entry.id),
            "defer_reason": reason,
            "deferred_until": until.isoformat() if until else None,
        }
    )
    
    # Уведомление с дедупликацией (чтобы не спамить)
    if notify:
        try:
            camp = queue_entry.campaign
            user = getattr(camp, "created_by", None)
            if not user:
                return
            
            # Дедупликация: одно уведомление на причину в час
            throttle_key = f"mailer:notify:defer:{camp.id}:{reason}:{until.date().isoformat()}"
            if not cache.add(throttle_key, "1", timeout=3600):
                return  # Уже уведомили в этом окне
            
            from notifications.service import notify as notify_user
            from notifications.models import Notification
            
            # Формируем понятное сообщение в зависимости от причины
            reason_texts = {
                DEFER_REASON_DAILY_LIMIT: "Дневной лимит достигнут",
                DEFER_REASON_QUOTA: "Квота smtp.bz исчерпана",
                DEFER_REASON_OUTSIDE_HOURS: "Вне рабочего времени",
                DEFER_REASON_RATE_HOUR: "Лимит в час достигнут",
                DEFER_REASON_TRANSIENT_ERROR: "Временная ошибка отправки",
            }
            title = reason_texts.get(reason, "Рассылка отложена")
            
            # Форматируем время возобновления
            msk_tz = timezone.get_current_timezone() if hasattr(timezone, "get_current_timezone") else None
            if msk_tz is None:
                from zoneinfo import ZoneInfo
                msk_tz = ZoneInfo("Europe/Moscow")
            
            until_msk = until.astimezone(msk_tz)
            time_str = until_msk.strftime("%H:%M")
            date_str = until_msk.strftime("%d.%m")
            
            body = f"{title}. Продолжим в {time_str} МСК ({date_str}). Кампания возобновится автоматически."
            
            notify_user(
                user=user,
                kind=Notification.Kind.SYSTEM,
                title=title,
                body=body,
                url=f"/mail/campaigns/{camp.id}/",
                dedupe_seconds=3600,  # Дополнительная дедупликация на уровне notify
            )
        except Exception as e:
            logger.error(f"Error sending defer notification: {e}", exc_info=True)
