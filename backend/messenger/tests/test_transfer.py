"""Тесты API передачи диалога между операторами/филиалами."""

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from accounts.models import Branch
from messenger.models import Contact, Conversation, ConversationTransfer, Inbox

User = get_user_model()


class TransferEndpointTests(TestCase):
    def setUp(self):
        self.ekb = Branch.objects.create(name="ЕКБ", code="ekb")
        self.krd = Branch.objects.create(name="КРД", code="krd")
        self.op_ekb = User.objects.create_user(
            "op_ekb", password="pw", role=User.Role.MANAGER, branch=self.ekb
        )
        self.op_krd = User.objects.create_user(
            "op_krd", password="pw", role=User.Role.MANAGER, branch=self.krd
        )
        self.inbox = Inbox.objects.create(name="Widget", branch=self.ekb)
        self.contact = Contact.objects.create(name="Client")
        self.conv = Conversation.objects.create(
            inbox=self.inbox,
            contact=self.contact,
            assignee=self.op_ekb,
        )
        # branch проставится автоматически из inbox при save
        self.client = APIClient()
        self.client.force_authenticate(self.op_ekb)

    def test_transfer_requires_reason(self):
        resp = self.client.post(
            f"/api/messenger/conversations/{self.conv.id}/transfer/",
            {"to_user_id": self.op_krd.id, "reason": "abc"},
            format="json",
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn("reason", resp.data)

    def test_transfer_creates_log_and_updates_assignee(self):
        resp = self.client.post(
            f"/api/messenger/conversations/{self.conv.id}/transfer/",
            {
                "to_user_id": self.op_krd.id,
                "reason": "Клиент из Краснодарского края по регламенту",
            },
            format="json",
        )
        self.assertEqual(resp.status_code, 200, resp.data)

        self.conv.refresh_from_db()
        self.assertEqual(self.conv.assignee, self.op_krd)
        self.assertEqual(self.conv.branch, self.krd)

        log = ConversationTransfer.objects.get(conversation=self.conv)
        self.assertEqual(log.from_user, self.op_ekb)
        self.assertEqual(log.to_user, self.op_krd)
        self.assertEqual(log.from_branch, self.ekb)
        self.assertEqual(log.to_branch, self.krd)
        self.assertTrue(log.cross_branch)

    def test_transfer_same_branch_not_marked_cross(self):
        op_ekb2 = User.objects.create_user(
            "op_ekb2", password="pw", role=User.Role.MANAGER, branch=self.ekb
        )
        resp = self.client.post(
            f"/api/messenger/conversations/{self.conv.id}/transfer/",
            {"to_user_id": op_ekb2.id, "reason": "Ухожу на обед, возьмёт коллега"},
            format="json",
        )
        self.assertEqual(resp.status_code, 200)
        log = ConversationTransfer.objects.get(conversation=self.conv)
        self.assertFalse(log.cross_branch)
