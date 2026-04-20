"""Тесты heartbeat endpoint и онлайн-статуса мессенджера."""

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.models import Branch
from messenger.tasks import check_offline_operators

User = get_user_model()


class HeartbeatEndpointTests(TestCase):
    def setUp(self):
        self.branch = Branch.objects.create(name="Br", code="br")
        self.user = User.objects.create_user(
            username="op1",
            password="pw",
            role=User.Role.MANAGER,
            branch=self.branch,
        )
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    def test_heartbeat_sets_online_and_last_seen(self):
        self.assertFalse(self.user.messenger_online)
        resp = self.client.post("/api/messenger/heartbeat/")
        self.assertEqual(resp.status_code, 200)
        self.user.refresh_from_db()
        self.assertTrue(self.user.messenger_online)
        self.assertIsNotNone(self.user.messenger_last_seen)
        delta = timezone.now() - self.user.messenger_last_seen
        self.assertLess(delta.total_seconds(), 5)

    def test_heartbeat_requires_auth(self):
        self.client.force_authenticate(None)
        resp = self.client.post("/api/messenger/heartbeat/")
        self.assertIn(resp.status_code, [401, 403])


class CheckOfflineOperatorsTests(TestCase):
    def setUp(self):
        self.branch = Branch.objects.create(name="Br", code="br")
        self.op_active = User.objects.create_user(
            "a",
            password="pw",
            role=User.Role.MANAGER,
            branch=self.branch,
            messenger_online=True,
            messenger_last_seen=timezone.now(),
        )
        self.op_stale = User.objects.create_user(
            "b",
            password="pw",
            role=User.Role.MANAGER,
            branch=self.branch,
            messenger_online=True,
            messenger_last_seen=timezone.now() - timedelta(seconds=120),
        )

    def test_stale_operator_marked_offline(self):
        check_offline_operators()
        self.op_active.refresh_from_db()
        self.op_stale.refresh_from_db()
        self.assertTrue(self.op_active.messenger_online)
        self.assertFalse(self.op_stale.messenger_online)
