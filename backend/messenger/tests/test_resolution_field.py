"""Plan 3 Task 1 — поля Conversation.resolution / escalation_level / last_escalated_at."""
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from accounts.models import Branch, User
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


@override_settings(MESSENGER_ENABLED=True)
class ResolutionApiTests(TestCase):
    def setUp(self):
        self.branch = Branch.objects.create(name="ЕКБ", code="ekb")
        self.user = User.objects.create_user(
            username="resolapi_m", password="x", role="manager", branch=self.branch
        )
        self.inbox = Inbox.objects.create(name="S", branch=self.branch, widget_token="tok_resolapi", settings={})
        self.contact = Contact.objects.create(external_id="resolapi_c", name="C", email="c@e.com")
        self.conv = Conversation.objects.create(
            inbox=self.inbox, contact=self.contact, branch=self.branch, assignee=self.user,
        )
        self.api = APIClient()
        self.api.force_authenticate(self.user)

    def test_serializer_exposes_resolution_and_escalation_level(self):
        resp = self.api.get(f"/api/conversations/{self.conv.id}/")
        self.assertEqual(resp.status_code, 200, resp.data if hasattr(resp, 'data') else resp.content)
        self.assertIn("resolution", resp.data)
        self.assertIn("escalation_level", resp.data)
        self.assertIn("last_escalated_at", resp.data)

    def test_patch_status_resolved_with_resolution_payload(self):
        resp = self.api.patch(
            f"/api/conversations/{self.conv.id}/",
            {"status": "resolved", "resolution": {"outcome": "success", "comment": "ok"}},
            format="json",
        )
        self.assertEqual(resp.status_code, 200, resp.data)
        self.conv.refresh_from_db()
        self.assertEqual(self.conv.status, Conversation.Status.RESOLVED)
        self.assertEqual(self.conv.resolution["outcome"], "success")
        self.assertEqual(self.conv.resolution["comment"], "ok")
