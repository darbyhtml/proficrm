from __future__ import annotations

from django.conf import settings
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, Client
from rest_framework import status
from rest_framework.test import APIClient

from accounts.models import Branch
from messenger.models import Inbox, Contact, Conversation, Message
from messenger.utils import create_widget_session


class WidgetSecurityFeatureTests(TestCase):
    def setUp(self):
        self.original_messenger_enabled = getattr(settings, "MESSENGER_ENABLED", False)
        settings.MESSENGER_ENABLED = True

        self.branch = Branch.objects.create(code="b1", name="B1")
        self.inbox = Inbox.objects.create(
            name="Inbox",
            branch=self.branch,
            widget_token="token_1",
            is_active=True,
            settings={},
        )
        self.contact = Contact.objects.create(external_id="v1", name="V")
        self.conversation = Conversation.objects.create(
            inbox=self.inbox,
            contact=self.contact,
            branch=self.branch,
            status=Conversation.Status.OPEN,
        )

    def tearDown(self):
        settings.MESSENGER_ENABLED = self.original_messenger_enabled

    def test_domain_allowlist_blocks_foreign_origin(self):
        self.inbox.settings = {"security": {"allowed_domains": ["allowed.com"]}}
        self.inbox.save(update_fields=["settings"])

        client = APIClient()
        r = client.post(
            "/api/widget/bootstrap/",
            {"widget_token": "token_1", "contact_external_id": "v2"},
            format="json",
            HTTP_ORIGIN="https://bad.com",
        )
        self.assertEqual(r.status_code, status.HTTP_403_FORBIDDEN)

    def test_domain_allowlist_allows_exact_origin_and_subdomain_patterns(self):
        """allowed_domains поддерживает точные домены и поддомены через *.example.com."""
        self.inbox.settings = {
            "security": {
                "allowed_domains": [
                    "allowed.com",
                    "*.subdomain.com",
                    "https://proto.example.org",
                ]
            }
        }
        self.inbox.save(update_fields=["settings"])

        client = APIClient()

        # Точный домен без схемы
        r1 = client.post(
            "/api/widget/bootstrap/",
            {"widget_token": "token_1", "contact_external_id": "v2"},
            format="json",
            HTTP_ORIGIN="https://allowed.com",
        )
        self.assertNotEqual(
            r1.status_code,
            status.HTTP_403_FORBIDDEN,
            "Точный домен из allowlist не должен блокироваться",
        )

        # Поддомен, разрешённый через *.subdomain.com
        r2 = client.post(
            "/api/widget/bootstrap/",
            {"widget_token": "token_1", "contact_external_id": "v3"},
            format="json",
            HTTP_ORIGIN="https://api.subdomain.com",
        )
        self.assertNotEqual(
            r2.status_code,
            status.HTTP_403_FORBIDDEN,
            "Поддомен, подпадающий под *.subdomain.com, не должен блокироваться",
        )

        # Origin, совпадающий с базовым доменом subdomain.com, не считается поддоменом
        r3 = client.post(
            "/api/widget/bootstrap/",
            {"widget_token": "token_1", "contact_external_id": "v4"},
            format="json",
            HTTP_ORIGIN="https://subdomain.com",
        )
        self.assertEqual(
            r3.status_code,
            status.HTTP_403_FORBIDDEN,
            "Базовый домен не должен проходить по маске *.subdomain.com",
        )

        # allowed_domains может содержать origin с протоколом — hostname всё равно должен совпасть
        r4 = client.post(
            "/api/widget/bootstrap/",
            {"widget_token": "token_1", "contact_external_id": "v5"},
            format="json",
            HTTP_ORIGIN="https://proto.example.org",
        )
        self.assertNotEqual(
            r4.status_code,
            status.HTTP_403_FORBIDDEN,
            "Домен, указанный с протоколом в allowed_domains, должен корректно сопоставляться",
        )

    def test_captcha_required_then_passed_allows_send(self):
        ip = "1.2.3.4"
        cache.set(f"messenger:captcha:ip:{ip}", 999, timeout=600)

        client = APIClient()
        boot = client.post(
            "/api/widget/bootstrap/",
            {"widget_token": "token_1", "contact_external_id": "v1"},
            format="json",
            REMOTE_ADDR=ip,
        )
        self.assertEqual(boot.status_code, status.HTTP_200_OK)
        self.assertTrue(boot.data.get("captcha_required"))
        token = boot.data.get("captcha_token")
        self.assertTrue(token)

        expected = cache.get(f"messenger:captcha:token:{token}")
        self.assertTrue(expected)

        session = boot.data["widget_session_token"]

        # send without captcha → 400
        r1 = client.post(
            "/api/widget/send/",
            {"widget_token": "token_1", "widget_session_token": session, "body": "hi"},
            format="json",
            REMOTE_ADDR=ip,
        )
        self.assertEqual(r1.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertTrue(r1.data.get("captcha_required"))

        # send with captcha → 201
        r2 = client.post(
            "/api/widget/send/",
            {
                "widget_token": "token_1",
                "widget_session_token": session,
                "body": "hi",
                "captcha_token": token,
                "captcha_answer": expected,
            },
            format="json",
            REMOTE_ADDR=ip,
        )
        self.assertEqual(r2.status_code, status.HTTP_201_CREATED)

        # subsequent send without captcha should pass for same session
        r3 = client.post(
            "/api/widget/send/",
            {"widget_token": "token_1", "widget_session_token": session, "body": "ok"},
            format="json",
            REMOTE_ADDR=ip,
        )
        self.assertEqual(r3.status_code, status.HTTP_201_CREATED)

    def test_stream_endpoint_returns_sse(self):
        client = APIClient()
        boot = client.post(
            "/api/widget/bootstrap/",
            {"widget_token": "token_1", "contact_external_id": "v1"},
            format="json",
        )
        session = boot.data["widget_session_token"]

        c = Client()
        resp = c.get(
            "/api/widget/stream/",
            {"widget_token": "token_1", "widget_session_token": session},
        )
        self.assertEqual(resp.status_code, 200)
        self.assertIn("text/event-stream", resp.get("Content-Type", ""))
        first = next(resp.streaming_content)
        if isinstance(first, bytes):
            first_text = first.decode("utf-8", errors="ignore")
        else:
            first_text = str(first)
        self.assertIn("event: ready", first_text)


class WidgetDeliveryAndAttachmentsTests(TestCase):
    def setUp(self):
        self.original_messenger_enabled = getattr(settings, "MESSENGER_ENABLED", False)
        settings.MESSENGER_ENABLED = True

        self.branch = Branch.objects.create(code="b2", name="B2")
        self.inbox = Inbox.objects.create(
            name="Inbox2",
            branch=self.branch,
            widget_token="token_2",
            is_active=True,
            settings={},
        )
        self.contact = Contact.objects.create(external_id="v-send", name="Visitor")
        self.conversation = Conversation.objects.create(
            inbox=self.inbox,
            contact=self.contact,
            branch=self.branch,
            status=Conversation.Status.OPEN,
        )

    def tearDown(self):
        settings.MESSENGER_ENABLED = self.original_messenger_enabled

    def test_widget_send_returns_attachments_payload(self):
        client = APIClient()
        boot = client.post(
            "/api/widget/bootstrap/",
            {"widget_token": self.inbox.widget_token, "contact_external_id": "v-send-1"},
            format="json",
        )
        self.assertEqual(boot.status_code, status.HTTP_200_OK)
        session = boot.data["widget_session_token"]

        file_obj = SimpleUploadedFile(
            "test.pdf",
            b"dummy content",
            content_type="application/pdf",
        )
        response = client.post(
            "/api/widget/send/",
            {
                "widget_token": self.inbox.widget_token,
                "widget_session_token": session,
                "body": "hi",
                "files": file_obj,
            },
            format="multipart",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        data = response.data
        self.assertIn("attachments", data)
        self.assertEqual(len(data["attachments"]), 1)
        att = data["attachments"][0]
        self.assertEqual(att["original_name"], "test.pdf")
        self.assertTrue(att["url"])

        msg = Message.objects.get(id=data["id"])
        self.assertEqual(msg.direction, Message.Direction.IN)
        self.assertEqual(msg.attachments.count(), 1)

    def test_outgoing_message_marked_delivered_on_poll(self):
        # Сообщение OUT без delivered_at
        msg = Message.objects.create(
            conversation=self.conversation,
            direction=Message.Direction.OUT,
            body="from operator",
        )
        self.assertIsNone(msg.delivered_at)

        session = create_widget_session(
            inbox_id=self.inbox.id,
            conversation_id=self.conversation.id,
            contact_id=str(self.contact.id),
        )

        client = APIClient()
        response = client.get(
            "/api/widget/poll/",
            {
                "widget_token": self.inbox.widget_token,
                "widget_session_token": session.token,
            },
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.data
        self.assertIsInstance(data.get("messages"), list)
        self.assertTrue(any(m.get("id") == msg.id for m in data["messages"]))

        msg.refresh_from_db()
        self.assertIsNotNone(msg.delivered_at)

