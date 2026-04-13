"""Plan 3 Task 1 — поля Conversation.resolution / escalation_level / last_escalated_at."""
from django.test import TestCase

from accounts.models import Branch
from messenger.models import Contact, Conversation, Inbox


class ConversationEscalationFieldsTests(TestCase):
    def setUp(self):
        self.branch = Branch.objects.create(name="ЕКБ", code="ekb")
        self.inbox = Inbox.objects.create(
            name="Site",
            branch=self.branch,
            widget_token="tok_resolution_test",
            is_active=True,
            settings={},
        )
        self.contact = Contact.objects.create(
            external_id="v-res-1",
            name="Client",
            email="c@example.com",
        )
        self.conv = Conversation.objects.create(
            inbox=self.inbox,
            contact=self.contact,
            branch=self.branch,
            status=Conversation.Status.OPEN,
        )

    def test_resolution_defaults_empty_dict(self):
        self.assertEqual(self.conv.resolution, {})

    def test_resolution_stores_outcome_and_comment(self):
        self.conv.resolution = {"outcome": "success", "comment": "ok"}
        self.conv.save()
        self.conv.refresh_from_db()
        self.assertEqual(self.conv.resolution["outcome"], "success")
        self.assertEqual(self.conv.resolution["comment"], "ok")

    def test_escalation_level_defaults_zero(self):
        self.assertEqual(self.conv.escalation_level, 0)

    def test_last_escalated_at_nullable(self):
        self.assertIsNone(self.conv.last_escalated_at)
