"""
Management command для ручной очистки устаревших записей журналов.

Использование:
    python manage.py prune_logs
    python manage.py prune_logs --activity-days 90 --errorlog-days 30 --notification-days 60
    python manage.py prune_logs --dry-run
"""
from __future__ import annotations

from django.core.management.base import BaseCommand
from django.conf import settings
from django.utils import timezone


class Command(BaseCommand):
    help = "Удаляет устаревшие записи ActivityEvent, ErrorLog и Notification"

    def add_arguments(self, parser):
        parser.add_argument(
            "--activity-days",
            type=int,
            default=None,
            help="Срок хранения ActivityEvent (дни). По умолчанию из ACTIVITY_EVENT_RETENTION_DAYS.",
        )
        parser.add_argument(
            "--errorlog-days",
            type=int,
            default=None,
            help="Срок хранения ErrorLog (дни). По умолчанию из ERRORLOG_RETENTION_DAYS.",
        )
        parser.add_argument(
            "--notification-days",
            type=int,
            default=None,
            help="Срок хранения Notification (дни). По умолчанию из NOTIFICATION_RETENTION_DAYS.",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Только показать количество записей к удалению, не удалять.",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]

        activity_days = options["activity_days"] or getattr(settings, "ACTIVITY_EVENT_RETENTION_DAYS", 180)
        errorlog_days = options["errorlog_days"] or getattr(settings, "ERRORLOG_RETENTION_DAYS", 90)
        notification_days = options["notification_days"] or getattr(settings, "NOTIFICATION_RETENTION_DAYS", 90)

        now = timezone.now()

        self._prune_activity_events(now, activity_days, dry_run)
        self._prune_error_logs(now, errorlog_days, dry_run)
        self._prune_notifications(now, notification_days, dry_run)

    def _prune_activity_events(self, now, days, dry_run):
        from audit.models import ActivityEvent

        cutoff = now - timezone.timedelta(days=days)
        qs = ActivityEvent.objects.filter(created_at__lt=cutoff)
        count = qs.count()
        if dry_run:
            self.stdout.write(f"ActivityEvent: {count} записей к удалению (старше {days} дней) [dry-run]")
        else:
            deleted, _ = qs.delete()
            self.stdout.write(self.style.SUCCESS(f"ActivityEvent: удалено {deleted} записей (старше {days} дней)"))

    def _prune_error_logs(self, now, days, dry_run):
        from audit.models import ErrorLog

        cutoff = now - timezone.timedelta(days=days)
        qs = ErrorLog.objects.filter(created_at__lt=cutoff, resolved=True)
        count = qs.count()
        if dry_run:
            self.stdout.write(f"ErrorLog: {count} записей к удалению (старше {days} дней, resolved=True) [dry-run]")
        else:
            deleted, _ = qs.delete()
            self.stdout.write(self.style.SUCCESS(f"ErrorLog: удалено {deleted} записей (старше {days} дней, resolved=True)"))

    def _prune_notifications(self, now, days, dry_run):
        from notifications.models import Notification

        cutoff = now - timezone.timedelta(days=days)
        qs = Notification.objects.filter(created_at__lt=cutoff, is_read=True)
        count = qs.count()
        if dry_run:
            self.stdout.write(f"Notification: {count} записей к удалению (старше {days} дней, is_read=True) [dry-run]")
        else:
            deleted, _ = qs.delete()
            self.stdout.write(self.style.SUCCESS(f"Notification: удалено {deleted} записей (старше {days} дней, is_read=True)"))
