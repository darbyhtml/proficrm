"""Тесты агрегированного API GET /api/conversations/{id}/context/.

Plan 4 Task 3 — правая панель live-chat: клиент, компания, история, аудит.
"""

from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from accounts.models import User, Branch
from companies.models import Company
from messenger.models import (
    Conversation,
    Contact,
    Inbox,
    ConversationTransfer,
)


@override_settings(MESSENGER_ENABLED=True)
class ConversationContextApiTests(TestCase):
    def setUp(self):
        self.branch = Branch.objects.create(name="ЕКБ", code="ekb")
        self.user = User.objects.create_user(
            username="ctxu", password="x", role="manager", branch=self.branch
        )
        self.inbox = Inbox.objects.create(
            name="S", branch=self.branch, widget_token="tok_ctxapi", settings={}
        )
        self.company = Company.objects.create(name="Тест-Ко")
        self.contact = Contact.objects.create(
            external_id="ctxapi_c",
            name="Клиент",
            email="k@test-ko.example",
            phone="+79990000000",
        )
        self.conv = Conversation.objects.create(
            inbox=self.inbox,
            contact=self.contact,
            branch=self.branch,
            assignee=self.user,
            company=self.company,
        )
        self.prev = Conversation.objects.create(
            inbox=self.inbox,
            contact=self.contact,
            branch=self.branch,
            status=Conversation.Status.RESOLVED,
        )
        self.api = APIClient()
        self.api.force_authenticate(self.user)

    def test_context_returns_client_block(self):
        resp = self.api.get(f"/api/conversations/{self.conv.id}/context/")
        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertIn("client", resp.data)
        self.assertEqual(resp.data["client"]["name"], "Клиент")
        self.assertEqual(resp.data["client"]["email"], "k@test-ko.example")

    def test_context_returns_company_block(self):
        resp = self.api.get(f"/api/conversations/{self.conv.id}/context/")
        self.assertIsNotNone(resp.data["company"])
        self.assertEqual(str(resp.data["company"]["id"]), str(self.company.id))
        self.assertEqual(resp.data["company"]["name"], "Тест-Ко")

    def test_context_returns_previous_conversations(self):
        resp = self.api.get(f"/api/conversations/{self.conv.id}/context/")
        previous = resp.data["previous_conversations"]
        self.assertIsInstance(previous, list)
        ids = [p["id"] for p in previous]
        self.assertIn(self.prev.id, ids)
        self.assertNotIn(self.conv.id, ids)

    def test_context_returns_audit_log_with_transfer(self):
        ConversationTransfer.objects.create(
            conversation=self.conv,
            from_user=self.user,
            to_user=self.user,
            from_branch=self.branch,
            to_branch=self.branch,
            reason="тестовая причина передачи",
        )
        resp = self.api.get(f"/api/conversations/{self.conv.id}/context/")
        self.assertIn("audit_log", resp.data)
        self.assertGreaterEqual(len(resp.data["audit_log"]), 1)
        kinds = [e["kind"] for e in resp.data["audit_log"]]
        self.assertIn("transfer", kinds)

    def test_context_no_company_returns_null(self):
        self.conv.company = None
        self.conv.save(update_fields=["company"])
        resp = self.api.get(f"/api/conversations/{self.conv.id}/context/")
        self.assertIsNone(resp.data["company"])
