"""
Тесты retention policy: purge_old_activity_events, purge_old_error_logs,
purge_old_notifications, management command prune_logs.
"""

from __future__ import annotations

from io import StringIO

from django.test import TestCase, override_settings
from django.utils import timezone

from accounts.models import User
from audit.models import ActivityEvent, ErrorLog
from notifications.models import Notification


def _make_activity(days_ago: int) -> ActivityEvent:
    """Создать ActivityEvent с created_at days_ago дней назад."""
    obj = ActivityEvent.objects.create(
        verb=ActivityEvent.Verb.CREATE,
        entity_type="company",
        entity_id="1",
    )
    ActivityEvent.objects.filter(pk=obj.pk).update(
        created_at=timezone.now() - timezone.timedelta(days=days_ago)
    )
    return obj


def _make_errorlog(days_ago: int, resolved: bool = True) -> ErrorLog:
    obj = ErrorLog.objects.create(
        level=ErrorLog.Level.ERROR,
        message="test error",
        resolved=resolved,
    )
    ErrorLog.objects.filter(pk=obj.pk).update(
        created_at=timezone.now() - timezone.timedelta(days=days_ago)
    )
    return obj


def _make_notification(user, days_ago: int, is_read: bool = True) -> Notification:
    obj = Notification.objects.create(
        user=user,
        kind=Notification.Kind.INFO,
        title="test",
        is_read=is_read,
    )
    Notification.objects.filter(pk=obj.pk).update(
        created_at=timezone.now() - timezone.timedelta(days=days_ago)
    )
    return obj


class PurgeActivityEventsTest(TestCase):

    @override_settings(ACTIVITY_EVENT_RETENTION_DAYS=30)
    def test_deletes_old_records(self):
        old = _make_activity(days_ago=31)
        recent = _make_activity(days_ago=10)
        from audit.tasks import purge_old_activity_events

        purge_old_activity_events()
        self.assertFalse(ActivityEvent.objects.filter(pk=old.pk).exists())
        self.assertTrue(ActivityEvent.objects.filter(pk=recent.pk).exists())

    @override_settings(ACTIVITY_EVENT_RETENTION_DAYS=30)
    def test_keeps_recent_records(self):
        recent = _make_activity(days_ago=5)
        from audit.tasks import purge_old_activity_events

        purge_old_activity_events()
        self.assertTrue(ActivityEvent.objects.filter(pk=recent.pk).exists())

    @override_settings(ACTIVITY_EVENT_RETENTION_DAYS=30)
    def test_empty_table_no_error(self):
        ActivityEvent.objects.all().delete()
        from audit.tasks import purge_old_activity_events

        purge_old_activity_events()  # должно выполниться без ошибок


class PurgeErrorLogsTest(TestCase):

    @override_settings(ERRORLOG_RETENTION_DAYS=30)
    def test_deletes_old_resolved(self):
        old_resolved = _make_errorlog(days_ago=31, resolved=True)
        from audit.tasks import purge_old_error_logs

        purge_old_error_logs()
        self.assertFalse(ErrorLog.objects.filter(pk=old_resolved.pk).exists())

    @override_settings(ERRORLOG_RETENTION_DAYS=30)
    def test_keeps_old_unresolved(self):
        old_unresolved = _make_errorlog(days_ago=31, resolved=False)
        from audit.tasks import purge_old_error_logs

        purge_old_error_logs()
        self.assertTrue(ErrorLog.objects.filter(pk=old_unresolved.pk).exists())

    @override_settings(ERRORLOG_RETENTION_DAYS=30)
    def test_keeps_recent_resolved(self):
        recent = _make_errorlog(days_ago=10, resolved=True)
        from audit.tasks import purge_old_error_logs

        purge_old_error_logs()
        self.assertTrue(ErrorLog.objects.filter(pk=recent.pk).exists())


class PurgeNotificationsTest(TestCase):

    def setUp(self):
        self.user = User.objects.create_user(username="pruneuser", password="pass")

    @override_settings(NOTIFICATION_RETENTION_DAYS=30)
    def test_deletes_old_read(self):
        old_read = _make_notification(self.user, days_ago=31, is_read=True)
        from notifications.tasks import purge_old_notifications

        purge_old_notifications()
        self.assertFalse(Notification.objects.filter(pk=old_read.pk).exists())

    @override_settings(NOTIFICATION_RETENTION_DAYS=30)
    def test_keeps_old_unread(self):
        old_unread = _make_notification(self.user, days_ago=31, is_read=False)
        from notifications.tasks import purge_old_notifications

        purge_old_notifications()
        self.assertTrue(Notification.objects.filter(pk=old_unread.pk).exists())

    @override_settings(NOTIFICATION_RETENTION_DAYS=30)
    def test_keeps_recent_read(self):
        recent = _make_notification(self.user, days_ago=5, is_read=True)
        from notifications.tasks import purge_old_notifications

        purge_old_notifications()
        self.assertTrue(Notification.objects.filter(pk=recent.pk).exists())


class PruneLogsCommandTest(TestCase):

    def setUp(self):
        self.user = User.objects.create_user(username="cmduser", password="pass")

    def _run_command(self, **kwargs):
        from django.core.management import call_command

        out = StringIO()
        call_command("prune_logs", stdout=out, **kwargs)
        return out.getvalue()

    @override_settings(
        ACTIVITY_EVENT_RETENTION_DAYS=30,
        ERRORLOG_RETENTION_DAYS=30,
        NOTIFICATION_RETENTION_DAYS=30,
    )
    def test_dry_run_does_not_delete(self):
        _make_activity(days_ago=60)
        count_before = ActivityEvent.objects.count()
        output = self._run_command(dry_run=True)
        self.assertEqual(ActivityEvent.objects.count(), count_before)
        self.assertIn("dry-run", output)

    @override_settings(
        ACTIVITY_EVENT_RETENTION_DAYS=30,
        ERRORLOG_RETENTION_DAYS=30,
        NOTIFICATION_RETENTION_DAYS=30,
    )
    def test_deletes_all_old_types(self):
        _make_activity(days_ago=60)
        _make_errorlog(days_ago=60, resolved=True)
        _make_notification(self.user, days_ago=60, is_read=True)
        self._run_command()
        self.assertEqual(
            ActivityEvent.objects.filter(
                created_at__lt=timezone.now() - timezone.timedelta(days=30)
            ).count(),
            0,
        )

    @override_settings(
        ACTIVITY_EVENT_RETENTION_DAYS=30,
        ERRORLOG_RETENTION_DAYS=30,
        NOTIFICATION_RETENTION_DAYS=30,
    )
    def test_custom_days_override(self):
        recent = _make_activity(days_ago=10)
        old = _make_activity(days_ago=60)
        # Override: удалить всё старше 5 дней
        self._run_command(**{"activity_days": 5})
        self.assertFalse(ActivityEvent.objects.filter(pk=old.pk).exists())
        self.assertFalse(ActivityEvent.objects.filter(pk=recent.pk).exists())
