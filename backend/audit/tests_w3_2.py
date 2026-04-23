"""W3.2 — Audit tasks chunking + composite indexes tests.

Hotlist #6 (chunked purge) + #7 (composite indexes on audit_activityevent).
Template ported from `backend/policy/tests_purge.py` (W2.1.3a).
"""

from __future__ import annotations

from django.db import connection
from django.test import TestCase, override_settings
from django.utils import timezone


class AuditPurgeChunkingTest(TestCase):
    """purge_old_activity_events обрабатывает records в chunks."""

    def _make_old_events(self, count: int, days_ago: int = 200) -> None:
        """Создать count events с created_at = now() - days_ago."""
        from audit.models import ActivityEvent

        # bulk_create без created_at (auto_now_add игнорируется только при update)
        events = [
            ActivityEvent(
                verb=ActivityEvent.Verb.CREATE,
                entity_type="test",
                entity_id=str(i),
            )
            for i in range(count)
        ]
        ActivityEvent.objects.bulk_create(events, batch_size=2000)
        # Переопределить created_at через update (auto_now_add обходит bulk_create)
        past = timezone.now() - timezone.timedelta(days=days_ago)
        ActivityEvent.objects.filter(entity_type="test").update(created_at=past)

    @override_settings(ACTIVITY_EVENT_RETENTION_DAYS=180)
    def test_chunked_deletion_handles_large_batches(self):
        """15K events → требует >= 2 batches (PURGE_CHUNK_SIZE=10K)."""
        from audit.models import ActivityEvent
        from audit.tasks import purge_old_activity_events

        self._make_old_events(15_000)
        self.assertEqual(ActivityEvent.objects.filter(entity_type="test").count(), 15_000)

        result = purge_old_activity_events()

        self.assertEqual(result, 15_000)
        self.assertEqual(ActivityEvent.objects.filter(entity_type="test").count(), 0)

    @override_settings(ACTIVITY_EVENT_RETENTION_DAYS=180)
    def test_empty_case_returns_zero(self):
        """Пустая таблица → 0 deleted, без ошибок."""
        from audit.models import ActivityEvent
        from audit.tasks import purge_old_activity_events

        ActivityEvent.objects.all().delete()
        result = purge_old_activity_events()
        self.assertEqual(result, 0)

    @override_settings(ACTIVITY_EVENT_RETENTION_DAYS=30)
    def test_keeps_recent_events(self):
        """Events внутри retention window остаются."""
        from audit.models import ActivityEvent
        from audit.tasks import purge_old_activity_events

        # Недавние events (10 дней назад — внутри retention=30)
        ActivityEvent.objects.create(
            verb=ActivityEvent.Verb.CREATE, entity_type="recent", entity_id="1"
        )
        recent_count = ActivityEvent.objects.filter(entity_type="recent").count()

        # Старые events (200 дней назад — за пределами retention)
        self._make_old_events(100, days_ago=200)

        result = purge_old_activity_events()

        self.assertEqual(result, 100)
        self.assertEqual(ActivityEvent.objects.filter(entity_type="recent").count(), recent_count)

    @override_settings(ACTIVITY_EVENT_RETENTION_DAYS=180)
    def test_returns_int(self):
        """Task returns int (ранее возвращала None)."""
        from audit.tasks import purge_old_activity_events

        result = purge_old_activity_events()
        self.assertIsInstance(result, int)


class AuditCompositeIndexesTest(TestCase):
    """Composite indexes из migration 0004 применены."""

    def test_entity_type_created_index_exists(self):
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT indexname FROM pg_indexes
                WHERE tablename = 'audit_activityevent'
                  AND indexname = 'audit_activityevent_entity_type_created_idx';
                """
            )
            row = cursor.fetchone()
        self.assertIsNotNone(
            row, "migration 0004 должен создать audit_activityevent_entity_type_created_idx"
        )

    def test_actor_created_index_exists(self):
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT indexname FROM pg_indexes
                WHERE tablename = 'audit_activityevent'
                  AND indexname = 'audit_activityevent_actor_created_idx';
                """
            )
            row = cursor.fetchone()
        self.assertIsNotNone(
            row, "migration 0004 должен создать audit_activityevent_actor_created_idx"
        )

    def test_composite_index_definition_desc_order(self):
        """Индексы должны быть ordered DESC по created_at (для LIMIT-оптимизации)."""
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT indexdef FROM pg_indexes
                WHERE tablename = 'audit_activityevent'
                  AND indexname = 'audit_activityevent_entity_type_created_idx';
                """
            )
            row = cursor.fetchone()
        self.assertIsNotNone(row)
        indexdef = row[0]
        self.assertIn("entity_type", indexdef)
        self.assertIn("created_at", indexdef)
        self.assertIn("DESC", indexdef.upper())
