"""Тесты off-hours flow виджета (F5, 2026-04-18).

Проверяем:
- POST /api/widget/offhours-request/ — валидация каналов и контакта
- Статус диалога после заявки: WAITING_OFFLINE
- Поля off_hours_channel/contact/note/requested_at заполняются
- Internal-Message добавляется в диалог с деталями заявки
- Action POST /api/conversations/{id}/contacted-back/ возвращает OPEN
"""

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db.models.signals import post_save
from django.test import TestCase
from rest_framework import status
from rest_framework.test import APIClient

from accounts.models import Branch
from messenger.models import (
    Contact,
    Conversation,
    Inbox,
    Message,
)
from messenger.signals import auto_assign_new_conversation
from messenger.utils import create_widget_session

User = get_user_model()


class WidgetOffHoursRequestTests(TestCase):
    def setUp(self):
        # Сигнал не нужен — тесты проверяют широкий state вручную.
        post_save.disconnect(auto_assign_new_conversation, sender=Conversation)
        self.addCleanup(
            post_save.connect, auto_assign_new_conversation, sender=Conversation
        )
        cache.clear()

        self.branch = Branch.objects.create(code="offhours_branch", name="Off-hours Branch")
        self.inbox = Inbox.objects.create(
            name="Off-hours Inbox",
            branch=self.branch,
            widget_token="offhours_token_abc",
            is_active=True,
        )
        self.contact = Contact.objects.create(
            external_id="offhours_visitor",
            name="Visitor",
            email="v@example.com",
        )
        self.conversation = Conversation.objects.create(
            inbox=self.inbox,
            contact=self.contact,
            branch=self.branch,
            status=Conversation.Status.OPEN,
        )
        self.session = create_widget_session(
            inbox_id=self.inbox.id,
            conversation_id=self.conversation.id,
            contact_id=str(self.contact.id),
            client_ip="127.0.0.1",
        )

        self._orig_messenger_enabled = getattr(settings, "MESSENGER_ENABLED", False)
        settings.MESSENGER_ENABLED = True

    def tearDown(self):
        settings.MESSENGER_ENABLED = self._orig_messenger_enabled
        cache.clear()

    def _payload(self, **overrides):
        base = {
            "widget_token": "offhours_token_abc",
            "widget_session_token": self.session.token,
            "preferred_channel": "call",
            "contact_value": "+7 999 123-45-67",
            "note": "Позвоните пожалуйста после 10 утра",
        }
        base.update(overrides)
        return base

    def test_offhours_request_sets_waiting_offline_and_fields(self):
        client = APIClient(REMOTE_ADDR="127.0.0.1")
        resp = client.post("/api/widget/offhours-request/", self._payload())
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data.get("status"), "ok")

        self.conversation.refresh_from_db()
        self.assertEqual(
            self.conversation.status, Conversation.Status.WAITING_OFFLINE
        )
        self.assertEqual(self.conversation.off_hours_channel, "call")
        self.assertEqual(self.conversation.off_hours_contact, "+7 999 123-45-67")
        self.assertIn("после 10 утра", self.conversation.off_hours_note)
        self.assertIsNotNone(self.conversation.off_hours_requested_at)

    def test_offhours_request_creates_internal_message(self):
        client = APIClient(REMOTE_ADDR="127.0.0.1")
        client.post("/api/widget/offhours-request/", self._payload(preferred_channel="email", contact_value="client@example.com"))
        msg = (
            Message.objects.filter(conversation=self.conversation)
            .order_by("-id")
            .first()
        )
        self.assertIsNotNone(msg)
        self.assertEqual(msg.direction, Message.Direction.INTERNAL)
        self.assertTrue(msg.is_private)
        self.assertIn("Email", msg.body)
        self.assertIn("client@example.com", msg.body)

    def test_offhours_request_rejects_invalid_channel(self):
        client = APIClient(REMOTE_ADDR="127.0.0.1")
        resp = client.post(
            "/api/widget/offhours-request/",
            self._payload(preferred_channel="telepathy"),
        )
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.conversation.refresh_from_db()
        self.assertEqual(self.conversation.status, Conversation.Status.OPEN)

    def test_offhours_request_requires_contact_value(self):
        client = APIClient(REMOTE_ADDR="127.0.0.1")
        resp = client.post(
            "/api/widget/offhours-request/",
            self._payload(contact_value=""),
        )
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_offhours_request_rejects_invalid_session(self):
        client = APIClient(REMOTE_ADDR="127.0.0.1")
        resp = client.post(
            "/api/widget/offhours-request/",
            self._payload(widget_session_token="invalid"),
        )
        self.assertEqual(resp.status_code, status.HTTP_401_UNAUTHORIZED)


class ContactedBackActionTests(TestCase):
    def setUp(self):
        post_save.disconnect(auto_assign_new_conversation, sender=Conversation)
        self.addCleanup(
            post_save.connect, auto_assign_new_conversation, sender=Conversation
        )
        self.branch = Branch.objects.create(code="cb_branch", name="CB Branch")
        self.inbox = Inbox.objects.create(
            name="CB Inbox", branch=self.branch, widget_token="cb_t", is_active=True
        )
        self.contact = Contact.objects.create(
            external_id="cb_visitor", name="V", email="v@e.com"
        )
        self.conv = Conversation.objects.create(
            inbox=self.inbox,
            contact=self.contact,
            branch=self.branch,
            status=Conversation.Status.WAITING_OFFLINE,
            off_hours_channel="call",
            off_hours_contact="+7 900 000 00 00",
        )
        self.manager = User.objects.create_user(
            username="cb_mgr",
            role=User.Role.MANAGER,
            branch=self.branch,
            messenger_online=True,
        )

    def test_manager_can_mark_contacted_back(self):
        client = APIClient()
        client.force_authenticate(user=self.manager)
        resp = client.post(f"/api/conversations/{self.conv.id}/contacted-back/")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.conv.refresh_from_db()
        self.assertEqual(self.conv.status, Conversation.Status.OPEN)
        self.assertIsNotNone(self.conv.contacted_back_at)
        self.assertEqual(self.conv.contacted_back_by, self.manager)
        # Пустой assignee был — менеджер сам взял в работу.
        self.assertEqual(self.conv.assignee, self.manager)

    def test_contacted_back_rejects_foreign_manager(self):
        other_branch = Branch.objects.create(code="other", name="Other")
        other_mgr = User.objects.create_user(
            username="other_mgr",
            role=User.Role.MANAGER,
            branch=other_branch,
            messenger_online=True,
        )
        client = APIClient()
        client.force_authenticate(user=other_mgr)
        resp = client.post(f"/api/conversations/{self.conv.id}/contacted-back/")
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    def test_contacted_back_rejects_wrong_status(self):
        self.conv.status = Conversation.Status.OPEN
        self.conv.save(update_fields=["status"])
        client = APIClient()
        client.force_authenticate(user=self.manager)
        resp = client.post(f"/api/conversations/{self.conv.id}/contacted-back/")
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
