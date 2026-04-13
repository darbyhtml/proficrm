"""Тесты временных меток last_customer_msg_at / last_agent_msg_at.

Нужны для property ui_status (следующая задача): определение
«Ждёт ответа» vs «В работе».
"""

from django.db.models.signals import post_save
from django.test import TestCase

from accounts.models import Branch
from messenger.models import Contact, Conversation, Inbox, Message
from messenger.signals import auto_assign_new_conversation


class MessageTimestampsTests(TestCase):
    def setUp(self):
        # Сигнал авто-назначения мешает юнит-тестам простых полей — отключаем.
        post_save.disconnect(auto_assign_new_conversation, sender=Conversation)
        self.addCleanup(
            post_save.connect, auto_assign_new_conversation, sender=Conversation
        )

        self.branch = Branch.objects.create(name="Br", code="br")
        self.inbox = Inbox.objects.create(name="Widget", branch=self.branch)
        self.contact = Contact.objects.create(name="C", email="c@example.com")
        self.conv = Conversation.objects.create(
            inbox=self.inbox, contact=self.contact
        )

    def test_incoming_sets_customer_ts(self):
        Message.objects.create(
            conversation=self.conv,
            direction=Message.Direction.IN,
            body="Привет",
        )
        self.conv.refresh_from_db()
        self.assertIsNotNone(self.conv.last_customer_msg_at)
        self.assertIsNone(self.conv.last_agent_msg_at)

    def test_outgoing_sets_agent_ts(self):
        Message.objects.create(
            conversation=self.conv,
            direction=Message.Direction.OUT,
            body="Здравствуйте",
        )
        self.conv.refresh_from_db()
        self.assertIsNotNone(self.conv.last_agent_msg_at)
        self.assertIsNone(self.conv.last_customer_msg_at)

    def test_internal_does_not_touch_customer_or_agent_ts(self):
        """Служебная заметка не должна переключать ui_status."""
        Message.objects.create(
            conversation=self.conv,
            direction=Message.Direction.INTERNAL,
            body="Служебка",
        )
        self.conv.refresh_from_db()
        self.assertIsNone(self.conv.last_customer_msg_at)
        self.assertIsNone(self.conv.last_agent_msg_at)


class UiStatusPropertyTests(TestCase):
    def setUp(self):
        post_save.disconnect(auto_assign_new_conversation, sender=Conversation)
        self.addCleanup(
            post_save.connect, auto_assign_new_conversation, sender=Conversation
        )
        self.branch = Branch.objects.create(name="Br", code="br")
        self.inbox = Inbox.objects.create(name="Widget", branch=self.branch)
        self.contact = Contact.objects.create(name="C", email="c@example.com")
        from django.contrib.auth import get_user_model
        self.User = get_user_model()
        self.op = self.User.objects.create_user(
            "op", password="pw", branch=self.branch, role=self.User.Role.MANAGER,
        )

    def _conv(self, **kw):
        return Conversation.objects.create(
            inbox=self.inbox, contact=self.contact, **kw
        )

    def test_status_new_when_open_and_unassigned(self):
        c = self._conv()
        self.assertEqual(c.ui_status, Conversation.UiStatus.NEW)

    def test_status_waiting_when_customer_last(self):
        from django.utils import timezone
        c = self._conv(assignee=self.op)
        Conversation.objects.filter(pk=c.pk).update(
            last_customer_msg_at=timezone.now(),
            last_agent_msg_at=None,
        )
        c.refresh_from_db()
        self.assertEqual(c.ui_status, Conversation.UiStatus.WAITING)

    def test_status_in_progress_when_agent_replied(self):
        from django.utils import timezone
        now = timezone.now()
        c = self._conv(assignee=self.op)
        Conversation.objects.filter(pk=c.pk).update(
            last_customer_msg_at=now,
            last_agent_msg_at=now,
        )
        c.refresh_from_db()
        self.assertEqual(c.ui_status, Conversation.UiStatus.IN_PROGRESS)

    def test_status_in_progress_when_no_customer_yet(self):
        """Оператор назначен, клиент ещё не писал — считаем 'В работе'."""
        c = self._conv(assignee=self.op)
        self.assertEqual(c.ui_status, Conversation.UiStatus.IN_PROGRESS)

    def test_status_closed_for_resolved_and_closed(self):
        c1 = self._conv(status=Conversation.Status.RESOLVED)
        c2 = self._conv(status=Conversation.Status.CLOSED)
        self.assertEqual(c1.ui_status, Conversation.UiStatus.CLOSED)
        self.assertEqual(c2.ui_status, Conversation.UiStatus.CLOSED)


class WaitingMinutesTests(TestCase):
    def setUp(self):
        post_save.disconnect(auto_assign_new_conversation, sender=Conversation)
        self.addCleanup(post_save.connect, auto_assign_new_conversation, sender=Conversation)
        self.branch = Branch.objects.create(name="Br", code="br")
        self.inbox = Inbox.objects.create(name="Widget", branch=self.branch)
        self.contact = Contact.objects.create(name="C", email="c@example.com")
        from django.contrib.auth import get_user_model
        self.User = get_user_model()
        self.op = self.User.objects.create_user(
            "op", password="pw", branch=self.branch, role=self.User.Role.MANAGER,
        )

    def test_zero_when_not_waiting(self):
        c = Conversation.objects.create(inbox=self.inbox, contact=self.contact, assignee=self.op)
        self.assertEqual(c.waiting_minutes, 0)

    def test_zero_when_new_unassigned(self):
        c = Conversation.objects.create(inbox=self.inbox, contact=self.contact)
        self.assertEqual(c.waiting_minutes, 0)

    def test_positive_when_customer_last(self):
        from django.utils import timezone
        from datetime import timedelta
        c = Conversation.objects.create(inbox=self.inbox, contact=self.contact, assignee=self.op)
        ten_ago = timezone.now() - timedelta(minutes=10)
        Conversation.objects.filter(pk=c.pk).update(last_customer_msg_at=ten_ago)
        c.refresh_from_db()
        self.assertGreaterEqual(c.waiting_minutes, 10)
        self.assertLess(c.waiting_minutes, 11)


class EscalationThresholdsTests(TestCase):
    def test_defaults_returned_when_no_policy(self):
        thresholds = Conversation.escalation_thresholds()
        self.assertEqual(thresholds["warn_min"], 3)
        self.assertEqual(thresholds["urgent_min"], 10)
        self.assertEqual(thresholds["rop_alert_min"], 20)
        self.assertEqual(thresholds["pool_return_min"], 40)
