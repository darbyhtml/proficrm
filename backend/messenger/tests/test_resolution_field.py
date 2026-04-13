"""Plan 3 Task 1 — поля Conversation.resolution / escalation_level / last_escalated_at."""
from django.test import TestCase

from accounts.models import Branch
from messenger.models import Contact, Conversation, Inbox
from policy.models import PolicyConfig


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


class EscalationThresholdsFromPolicyTests(TestCase):
    def test_defaults_when_policy_empty(self):
        cfg = PolicyConfig.load()
        cfg.livechat_escalation = {}
        cfg.save()
        thresholds = Conversation.escalation_thresholds()
        self.assertEqual(thresholds["warn_min"], 3)
        self.assertEqual(thresholds["pool_return_min"], 40)

    def test_policy_overrides_defaults(self):
        cfg = PolicyConfig.load()
        cfg.livechat_escalation = {"warn_min": 5, "pool_return_min": 60}
        cfg.save()
        thresholds = Conversation.escalation_thresholds()
        self.assertEqual(thresholds["warn_min"], 5)
        self.assertEqual(thresholds["pool_return_min"], 60)
        self.assertEqual(thresholds["urgent_min"], 10)  # дефолт
