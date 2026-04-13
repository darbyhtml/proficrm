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
