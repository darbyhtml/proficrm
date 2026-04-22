"""W2.1.3a — policy events retention (purge_old_policy_events) tests (Q17).

Verify task:
- Deletes events старше retention_days.
- Preserves recent events.
- Respects entity_type='policy' scope (не трогает other ActivityEvent).
- Handles empty state (no events older than retention).
"""

from __future__ import annotations

from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from accounts.models import User
from audit.models import ActivityEvent
from policy.tasks import purge_old_policy_events


class PurgePolicyEventsTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="pt_user", password="pass", role=User.Role.MANAGER
        )

    def _create_event(self, entity_type: str, days_ago: int) -> ActivityEvent:
        ev = ActivityEvent.objects.create(
            actor=self.user,
            verb=ActivityEvent.Verb.UPDATE,
            entity_type=entity_type,
            entity_id="test:resource",
            message="test",
            meta={"allowed": False},
        )
        # Override created_at via update (auto_now_add field)
        past = timezone.now() - timedelta(days=days_ago)
        ActivityEvent.objects.filter(id=ev.id).update(created_at=past)
        ev.refresh_from_db()
        return ev

    def test_purge_old_events_deletes_old_policy(self):
        """Policy events старше 14 дней должны быть удалены."""
        old_policy = self._create_event("policy", days_ago=20)
        recent_policy = self._create_event("policy", days_ago=5)

        deleted = purge_old_policy_events(retention_days=14)

        self.assertEqual(deleted, 1)
        self.assertFalse(ActivityEvent.objects.filter(id=old_policy.id).exists())
        self.assertTrue(ActivityEvent.objects.filter(id=recent_policy.id).exists())

    def test_purge_preserves_non_policy_events(self):
        """Non-policy events (company, task, etc.) НЕ должны удаляться."""
        old_company = self._create_event("company", days_ago=100)
        old_policy = self._create_event("policy", days_ago=100)

        deleted = purge_old_policy_events(retention_days=14)

        self.assertEqual(deleted, 1, "Only policy event should be deleted")
        self.assertTrue(ActivityEvent.objects.filter(id=old_company.id).exists())
        self.assertFalse(ActivityEvent.objects.filter(id=old_policy.id).exists())

    def test_purge_none_when_nothing_old(self):
        """No events старше retention → return 0."""
        self._create_event("policy", days_ago=5)
        self._create_event("policy", days_ago=10)

        deleted = purge_old_policy_events(retention_days=14)

        self.assertEqual(deleted, 0)

    def test_purge_respects_custom_retention_days(self):
        """retention_days=7 → events старше 7 дней удаляются."""
        self._create_event("policy", days_ago=8)  # > 7, should be deleted
        self._create_event("policy", days_ago=6)  # < 7, should stay

        deleted = purge_old_policy_events(retention_days=7)

        self.assertEqual(deleted, 1)

    def test_purge_chunked_for_large_volume(self):
        """Create > CHUNK_SIZE events, verify all deleted."""
        # Create 25 old policy events (small for test speed — CHUNK_SIZE=10K)
        for _ in range(25):
            self._create_event("policy", days_ago=30)

        deleted = purge_old_policy_events(retention_days=14)

        self.assertEqual(deleted, 25)
        self.assertEqual(
            ActivityEvent.objects.filter(entity_type="policy").count(),
            0,
        )
