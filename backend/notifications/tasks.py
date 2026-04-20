"""
Celery-задачи для очистки и генерации уведомлений.
"""

from __future__ import annotations

import logging
from datetime import timedelta

from celery import shared_task
from django.conf import settings
from django.utils import timezone

logger = logging.getLogger(__name__)

# Срок хранения по умолчанию — 90 дней (3 месяца)
_DEFAULT_NOTIFICATION_RETENTION_DAYS = 90


@shared_task(name="notifications.tasks.purge_old_notifications", ignore_result=True)
def purge_old_notifications() -> None:
    """
    Удаляет прочитанные уведомления старше NOTIFICATION_RETENTION_DAYS дней.
    Непрочитанные уведомления не удаляются.
    Запускается через Celery Beat еженедельно.
    """
    from notifications.models import Notification

    days = getattr(settings, "NOTIFICATION_RETENTION_DAYS", _DEFAULT_NOTIFICATION_RETENTION_DAYS)
    cutoff = timezone.now() - timezone.timedelta(days=days)
    deleted, _ = Notification.objects.filter(created_at__lt=cutoff, is_read=True).delete()
    logger.info(
        "purge_old_notifications: удалено %d записей (старше %d дней, is_read=True)", deleted, days
    )


@shared_task(name="notifications.tasks.generate_contract_reminders", ignore_result=True)
def generate_contract_reminders() -> int:
    """
    Создаёт напоминания и Notification о приближении окончания договора
    для ответственных пользователей на порогах warning_days / danger_days / 30 дней.

    Ранее эта логика работала из context_processor на каждый GET-запрос
    (context_processors.notifications_panel) — что приводило к записи в БД
    из read-пути. Теперь генерация вынесена в фоновой celery-beat job,
    запускаемый раз в сутки.

    Возвращает количество созданных CompanyContractReminder.
    """
    from companies.models import Company
    from notifications.models import CompanyContractReminder, Notification
    from notifications.service import notify

    today_date = timezone.localdate(timezone.now())
    contract_qs = (
        Company.objects.filter(contract_until__isnull=False, responsible__isnull=False)
        .select_related("contract_type", "responsible")
        .only("id", "name", "contract_until", "contract_type", "responsible")
    )

    created = 0
    for c in contract_qs.iterator(chunk_size=500):
        user = c.responsible
        if not user or not c.contract_type or not c.contract_until:
            continue
        warning_days = c.contract_type.warning_days
        danger_days = c.contract_type.danger_days
        days_left = (c.contract_until - today_date).days

        thresholds = [warning_days, danger_days]
        if warning_days < 30:
            thresholds.insert(0, 30)

        for days_before in thresholds:
            if days_before is None or days_before > days_left:
                continue
            target = c.contract_until - timedelta(days=days_before)
            if target != today_date:
                continue

            exists = CompanyContractReminder.objects.filter(
                user=user,
                company_id=c.id,
                contract_until=c.contract_until,
                days_before=days_before,
            ).exists()
            if exists:
                continue
            CompanyContractReminder.objects.create(
                user=user,
                company_id=c.id,
                contract_until=c.contract_until,
                days_before=days_before,
            )
            created += 1

            if days_before == 30:
                title = "До окончания договора остался месяц"
            elif days_left == 1:
                title = "До окончания договора остался 1 день"
            elif days_left in (2, 3, 4):
                title = f"До окончания договора осталось {days_left} дня"
            else:
                title = f"До окончания договора осталось {days_left} дней"
            body = f"{c.name} · до {c.contract_until.strftime('%d.%m.%Y')}"
            try:
                notify(
                    user=user,
                    kind=Notification.Kind.COMPANY,
                    title=title,
                    body=body,
                    url=f"/companies/{c.id}/",
                )
            except Exception:
                logger.exception(
                    "generate_contract_reminders: notify failed user=%s company=%s",
                    user.pk,
                    c.id,
                )

    logger.info("generate_contract_reminders: создано %d напоминаний", created)
    return created
