"""Plan 3 Task 3 — Celery task escalate_waiting_conversations."""

from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from accounts.models import Branch, User
from messenger.models import Contact, Conversation, Inbox
from messenger.tasks import escalate_waiting_conversations
from notifications.models import Notification


class EscalationTaskTests(TestCase):
    def setUp(self):
        self.branch = Branch.objects.create(name="ЕКБ", code="ekb")
        self.manager = User.objects.create_user(
            username="m", password="x", role="manager", branch=self.branch
        )
        self.rop = User.objects.create_user(
            username="rop", password="x", role="sales_head", branch=self.branch
        )
        self.inbox = Inbox.objects.create(
            name="Site",
            branch=self.branch,
            widget_token="tok_escalation_test",
            is_active=True,
            settings={},
        )
        self.contact = Contact.objects.create(
            external_id="v-esc-1",
            name="Client",
            email="c@example.com",
        )
        self.conv = Conversation.objects.create(
            inbox=self.inbox,
            contact=self.contact,
            branch=self.branch,
            status=Conversation.Status.OPEN,
            assignee=self.manager,
        )
        # Клиент написал 5 минут назад, оператор не отвечал.
        Conversation.objects.filter(pk=self.conv.pk).update(
            last_customer_msg_at=timezone.now() - timedelta(minutes=5),
            last_agent_msg_at=None,
        )
        self.conv.refresh_from_db()

    def _set_waiting(self, minutes: int):
        Conversation.objects.filter(pk=self.conv.pk).update(
            last_customer_msg_at=timezone.now() - timedelta(minutes=minutes),
            last_agent_msg_at=None,
        )

    def test_warn_level_creates_no_notification(self):
        escalate_waiting_conversations()
        self.assertEqual(Notification.objects.count(), 0)
        self.conv.refresh_from_db()
        self.assertEqual(self.conv.escalation_level, 1)

    def test_urgent_level_notifies_assignee(self):
        self._set_waiting(11)
        escalate_waiting_conversations()
        notifs = Notification.objects.filter(user=self.manager)
        self.assertEqual(notifs.count(), 1)
        self.assertIn("ждёт", notifs.first().title.lower())
        self.conv.refresh_from_db()
        self.assertEqual(self.conv.escalation_level, 2)

    def test_rop_alert_notifies_branch_sales_heads(self):
        self._set_waiting(21)
        escalate_waiting_conversations()
        notifs = Notification.objects.filter(user=self.rop)
        self.assertEqual(notifs.count(), 1)
        self.conv.refresh_from_db()
        self.assertEqual(self.conv.escalation_level, 3)

    def test_pool_return_unassigns_and_notifies_branch(self):
        User.objects.create_user(
            username="m2",
            password="x",
            role="manager",
            branch=self.branch,
            messenger_online=True,
        )
        self._set_waiting(41)
        escalate_waiting_conversations()
        self.conv.refresh_from_db()
        self.assertIsNone(self.conv.assignee)
        self.assertEqual(self.conv.escalation_level, 4)
        self.assertTrue(Notification.objects.filter(payload__conversation_id=self.conv.id).exists())

    def test_idempotent_same_level(self):
        self._set_waiting(11)
        escalate_waiting_conversations()
        escalate_waiting_conversations()
        self.assertEqual(Notification.objects.filter(user=self.manager).count(), 1)

    def test_resolved_conversation_skipped(self):
        Conversation.objects.filter(pk=self.conv.pk).update(
            status=Conversation.Status.RESOLVED,
            last_customer_msg_at=timezone.now() - timedelta(minutes=15),
        )
        escalate_waiting_conversations()
        self.assertEqual(Notification.objects.count(), 0)
